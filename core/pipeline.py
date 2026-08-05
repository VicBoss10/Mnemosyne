"""Engine orchestration: ingestion and querying.

Pipeline is the only class a client of the engine (the CLI, the API, or whatever
phase 2 brings) needs to know about. Everything else is an internal piece.
"""

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from core.chunker import chunk_documents
from core.config import Settings, get_settings
from core.embeddings import EmbeddingClient
from core.generator import INSUFFICIENT_CONTEXT_MESSAGE, Generator
from core.lexical import BM25
from core.loader import load_documents
from core.models import Answer, RetrievedChunk, Source
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
            sources=_build_sources(results),
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
            done    → the answer is complete
            error   → something failed during retrieval or generation

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

        try:
            for piece in self.generator.generate_stream(question, results):
                yield StreamEvent("token", {"text": piece})
        except Exception as exc:
            logger.exception("Generation failed")
            yield StreamEvent("error", {"message": str(exc)})
            return

        yield StreamEvent("done", {})

    def health(self) -> dict[str, bool | int | str]:
        """Dependency status, for the /health endpoint."""
        return {
            "qdrant": self.store.health_check(),
            "collection": self.settings.collection_name,
            "indexed_chunks": self.store.count(),
        }

    def close(self) -> None:
        self.embedding_client.close()
        self.generator.close()


def _build_sources(results: list[RetrievedChunk]) -> list[Source]:
    """Build the cited sources from the retrieved chunks' metadata.

    The [1], [2] references in the generated text are deliberately NOT parsed:
    sources come from what the search actually retrieved, so the model can
    neither invent them nor attribute a claim to a document never consulted.
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
            )
        )
    return sources
