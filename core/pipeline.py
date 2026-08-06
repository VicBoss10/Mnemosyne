"""Engine orchestration: ingestion and querying.

Pipeline is the only class a client of the engine (the CLI, the API, or whatever
phase 2 brings) needs to know about. Everything else is an internal piece.
"""

import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from core.chunker import chunk_documents
from core.config import Settings, get_settings
from core.embeddings import EmbeddingClient
from core.generator import INSUFFICIENT_CONTEXT_MESSAGE, Generator
from core.lexical import BM25, tokenize
from core.loader import load_documents
from core.models import Answer, Chunk, RetrievedChunk, Source
from core.retriever import Retriever
from core.store import VectorStore

logger = logging.getLogger(__name__)

#: Texts embedded per Ollama call during ingestion. Very large batches can
#: exhaust the model's memory; 32 is a safe middle ground.
EMBEDDING_BATCH_SIZE = 32

#: Length of the text excerpt attached to each cited source, so the user can
#: verify the answer without opening the file.
EXCERPT_LENGTH = 300


@dataclass
class StreamEvent:
    """An event emitted while streaming an answer.

    Transport (SSE, WebSocket) is the consuming layer's responsibility: the
    pipeline only describes what happened.
    """

    event: str
    data: dict = field(default_factory=dict)


@dataclass
class IngestionResult:
    """Summary of an ingestion run, for reporting back to the user."""

    documents: int
    chunks: int
    collection: str


class Pipeline:
    """The complete RAG engine."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.embedding_client = EmbeddingClient(self.settings.ollama)
        self.store = VectorStore(
            config=self.settings.qdrant,
            collection_name=self.settings.collection_name,
            vector_size=self.settings.ollama.embedding_dim,
        )
        self.retriever = Retriever(
            embedding_client=self.embedding_client,
            store=self.store,
            config=self.settings.retrieval,
        )
        self.generator = Generator(self.settings.ollama)

    def ingest(self, docs_path: Path | None = None) -> IngestionResult:
        """Index a folder of documents, rebuilding the collection.

        Ingestion is idempotent: running it twice leaves the same state.

        Raises:
            ValueError: if the folder yields no indexable fragment.
        """
        path = docs_path or self.settings.resolved_docs_path

        documents = load_documents(path)
        chunks = chunk_documents(documents, self.settings.chunking)
        if not chunks:
            raise ValueError(f"No indexable fragment was produced from {path}")

        self.store.recreate_collection()

        # BM25 weights a term by how rare it is across the corpus, so the model
        # is fitted over every chunk before any batch is written.
        lexical_model = BM25.fit([c.text for c in chunks])
        self.store.save_lexical_model(lexical_model.to_dict())

        # Embedding and storing in batches keeps memory bounded and avoids
        # sending Ollama one enormous request.
        for start in range(0, len(chunks), EMBEDDING_BATCH_SIZE):
            batch = chunks[start : start + EMBEDDING_BATCH_SIZE]
            embeddings = self.embedding_client.embed_documents([c.text for c in batch])
            sparse = [lexical_model.encode_document(c.text) for c in batch]
            self.store.upsert_chunks(batch, embeddings, sparse)
            logger.info(
                "Indexed %d/%d fragments", min(start + len(batch), len(chunks)), len(chunks)
            )

        return IngestionResult(
            documents=len(documents),
            chunks=len(chunks),
            collection=self.settings.collection_name,
        )

    def answer(self, question: str, top_k: int | None = None) -> Answer:
        """Answer a question using only the indexed documents.

        Raises:
            ValueError: if the question is empty.
        """
        question = question.strip()
        if not question:
            raise ValueError("The question cannot be empty")

        results = self.retriever.retrieve(question, top_k)

        # Deterministic barrier: without sufficient context the model is never
        # invoked.
        if not self.retriever.has_sufficient_context(results):
            logger.info(
                "Insufficient context for '%s' (best score: %.3f, threshold: %.3f)",
                question,
                results[0].score if results else 0.0,
                self.settings.retrieval.min_score_threshold,
            )
            return Answer(
                question=question,
                answer=INSUFFICIENT_CONTEXT_MESSAGE,
                sources=[],
                insufficient_context=True,
            )

        generated = self.generator.generate(question, results)

        return Answer(
            question=question,
            answer=generated,
            sources=_build_sources(results, generated),
            insufficient_context=False,
            low_confidence=self.retriever.is_low_confidence(results, question),
        )

    def answer_stream(self, question: str, top_k: int | None = None) -> Iterator[StreamEvent]:
        """Answer as a stream of events, emitted as results become available.

        The event order exploits the fact that sources are known before the text
        is generated: they are emitted first, so the interface can display them
        immediately while the answer is still being written.

        Events:
            sources → the retrieved sources (empty when there was no context)
            token   → a fragment of the answer text
            cited   → which of those sources the finished answer drew on
            done    → the answer is complete
            error   → something failed during retrieval or generation

        Attribution needs the complete text, which does not exist when the
        sources are emitted, so it arrives afterwards as its own event rather
        than delaying the source list until the end.

        Raises:
            ValueError: if the question is empty.
        """
        question = question.strip()
        if not question:
            raise ValueError("The question cannot be empty")

        try:
            results = self.retriever.retrieve(question, top_k)
        except Exception as exc:
            logger.exception("Retrieval failed")
            yield StreamEvent("error", {"message": str(exc)})
            return

        # Same deterministic barrier as in answer().
        if not self.retriever.has_sufficient_context(results):
            yield StreamEvent(
                "sources",
                {"sources": [], "insufficient_context": True, "low_confidence": False},
            )
            yield StreamEvent("token", {"text": INSUFFICIENT_CONTEXT_MESSAGE})
            yield StreamEvent("done", {})
            return

        sources = _build_sources(results)
        yield StreamEvent(
            "sources",
            {
                "sources": [s.model_dump() for s in sources],
                "insufficient_context": False,
                "low_confidence": self.retriever.is_low_confidence(results, question),
            },
        )

        generated: list[str] = []
        try:
            for piece in self.generator.generate_stream(question, results):
                generated.append(piece)
                yield StreamEvent("token", {"text": piece})
        except Exception as exc:
            logger.exception("Generation failed")
            yield StreamEvent("error", {"message": str(exc)})
            return

        # Now that the text exists, say which fragments it was drawn from. The
        # indices refer to the source list already sent.
        _mark_cited(sources, results, "".join(generated))
        yield StreamEvent(
            "cited", {"cited": [i for i, source in enumerate(sources) if source.cited]}
        )

        yield StreamEvent("done", {})

    def health(self) -> dict[str, bool | int | str]:
        """Dependency status, for the /health endpoint."""
        return {
            "qdrant": self.store.health_check(),
            "collection": self.settings.collection_name,
            "indexed_chunks": self.store.count(),
        }

    def dependencies(self) -> dict[str, bool | list[str]]:
        """Whether Ollama responds and which configured models are missing.

        Separate from health() because this failure mode is different: Ollama
        can be up and still lack the models, in which case every question fails
        on a download the user never asked for. Reporting it up front lets a
        client offer to fetch them instead.
        """
        return self.check_dependencies(self.settings)

    @staticmethod
    def check_dependencies(settings: Settings) -> dict[str, bool | list[str]]:
        """Like `dependencies()`, without needing a pipeline.

        The models are configuration, not corpus, so this answer does not depend
        on there being a project at all — and on a fresh install there is none,
        which is precisely when the caller needs to know that Ollama is missing.
        """
        ollama = settings.ollama
        required = {ollama.embedding_model, ollama.generation_model}

        try:
            response = httpx.get(f"{ollama.url}/api/tags", timeout=5.0)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            # Unreachable: nothing can be said about the models, so they all
            # count as missing — which is what a caller has to resolve anyway.
            return {"ollama": False, "missing_models": sorted(required)}

        # Ollama reports untagged names with an implicit ":latest", so
        # "bge-m3" and "bge-m3:latest" denote the same model.
        def canonical(name: str) -> str:
            return name if ":" in name else f"{name}:latest"

        installed = {canonical(m.get("name", "")) for m in payload.get("models", [])}
        missing = sorted(name for name in required if canonical(name) not in installed)
        return {"ollama": True, "missing_models": missing}

    def close(self) -> None:
        self.embedding_client.close()
        self.generator.close()


def _build_sources(results: list[RetrievedChunk], answer: str | None = None) -> list[Source]:
    """Build the cited sources from the retrieved chunks' metadata.

    The [1], [2] references in the generated text are deliberately NOT parsed:
    sources come from what the search actually retrieved, so the model can
    neither invent them nor attribute a claim to a document never consulted.

    When the generated `answer` is supplied, each source is additionally marked
    as cited or not — see _mark_cited. Without it every fragment is reported
    unmarked, which is what the streaming path needs: it emits the sources
    before the text exists.
    """
    sources = []
    for retrieved in results:
        chunk = retrieved.chunk
        excerpt = chunk.text[:EXCERPT_LENGTH]
        if len(chunk.text) > EXCERPT_LENGTH:
            excerpt += "..."

        sources.append(
            Source(
                source_file=chunk.source_file,
                section=" > ".join(chunk.header_path) if chunk.header_path else "",
                score=round(retrieved.score, 4),
                excerpt=excerpt,
                locator=_locator(chunk),
            )
        )

    if answer is not None:
        _mark_cited(sources, results, answer)

    return sources


#: A "Página N" section produced by the PDF extractor. The page is the locator a
#: reader needs to verify a citation, and unlike a line number it survives
#: independently of how the text was extracted.
_PAGE_SECTION = re.compile(r"^p[áa]gina\s+(\d+)$", re.IGNORECASE)


def _locator(chunk: Chunk) -> str:
    """Human-readable position of a fragment inside its document.

    Prefers the page when the format has one: PDF pages become '## Página N'
    sections during extraction, so the page is already in the header path and
    points at something the reader can turn to. Otherwise the line where the
    fragment starts, which is exact for .md and .txt because their extracted
    text *is* the file.
    """
    for header in chunk.header_path:
        match = _PAGE_SECTION.match(header.strip())
        if match:
            return f"página {match.group(1)}"

    return f"línea {chunk.start_line}"


#: Share of a fragment's distinctive terms that must appear in the answer for it
#: to count as the fragment the answer was drawn from. Set by what the two
#: populations look like: a fragment the model actually used shares the names,
#: figures and terminology it restated, while an unused one overlaps only on
#: generic vocabulary. Low enough that a one-line answer drawn from a long
#: fragment still registers.
CITATION_OVERLAP = 0.12

#: Fragments marked cited at most, so a diffuse answer does not simply relabel
#: the whole retrieved set and reproduce the problem this exists to solve.
MAX_CITED_SOURCES = 4


def _mark_cited(sources: list[Source], results: list[RetrievedChunk], answer: str) -> None:
    """Mark which retrieved fragments the answer was actually drawn from.

    Retrieval hands the model ten fragments and a typical answer uses one or
    two, yet all ten were being reported as "fuentes citadas" — which made the
    citation meaningless precisely where it matters most.

    Attribution is measured, not asked for: the model is never trusted to name
    its own sources (it cannot do so reliably, and a fabricated citation is the
    exact failure this system exists to prevent). Instead each fragment is scored
    by how much of its distinctive vocabulary reappears in the answer. That is
    directional evidence rather than proof — an answer restating a passage shares
    its rare terms — and it is checked against the retrieved text, so nothing the
    model invents can create a citation.

    Nothing is discarded: unmarked fragments are still returned as consulted
    context. The mark only separates what backed the answer from what was merely
    searched.
    """
    if not answer.strip():
        return

    answer_terms = set(tokenize(answer))
    if not answer_terms:
        return

    scored: list[tuple[float, int]] = []
    for index, retrieved in enumerate(results):
        chunk_terms = set(tokenize(retrieved.chunk.text))
        if not chunk_terms:
            continue
        overlap = len(chunk_terms & answer_terms) / len(chunk_terms)
        if overlap >= CITATION_OVERLAP:
            scored.append((overlap, index))

    # Strongest evidence first, so the cap keeps the best-supported fragments.
    scored.sort(reverse=True)

    for _, index in scored[:MAX_CITED_SOURCES]:
        sources[index].cited = True

    # An answer that matched nothing is still grounded in something: rather than
    # cite nothing at all, fall back to the best-ranked fragment, which is what
    # the retrieval actually stood behind.
    if not scored and not _is_refusal(answer):
        sources[0].cited = True


def _is_refusal(answer: str) -> bool:
    """True when the model declined to answer, so there is nothing to cite."""
    return INSUFFICIENT_CONTEXT_MESSAGE.rstrip(".").lower() in answer.lower()
