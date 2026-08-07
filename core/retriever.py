"""Retrieval of the fragments relevant to a question.

Embeds the question with the same model used for indexing and searches the
vector store for the nearest chunks. The search is hybrid: the dense vector
carries meaning and a sparse BM25 vector carries literal terms, because a short
question gives the dense side too little to work with while the exact word is
right there in the text.
"""

import logging

from core.config import RetrievalConfig
from core.embeddings import EmbeddingClient
from core.lexical import BM25, tokenize
from core.models import RetrievedChunk
from core.store import VectorStore

logger = logging.getLogger(__name__)


class Retriever:
    """Joins the embedding client to the vector store."""

    def __init__(
        self,
        embedding_client: EmbeddingClient,
        store: VectorStore,
        config: RetrievalConfig,
    ) -> None:
        self.embedding_client = embedding_client
        self.store = store
        self.config = config
        self._lexical: BM25 | None = None
        self._lexical_loaded = False

    @property
    def lexical(self) -> BM25 | None:
        """BM25 statistics for the current index, loaded once and cached.

        Loaded lazily rather than in __init__ so constructing a Retriever does
        not require Qdrant to be reachable. None means the index predates hybrid
        search, and retrieval falls back to dense-only.
        """
        if not self._lexical_loaded:
            self._lexical_loaded = True
            try:
                data = self.store.load_lexical_model()
                self._lexical = BM25.from_dict(data) if data else None
            except Exception as exc:
                # Lexical search is an improvement, not a requirement: losing it
                # degrades ranking but must never break answering.
                logger.warning("Could not load the lexical model, using dense only: %s", exc)
                self._lexical = None

        return self._lexical

    def retrieve(self, question: str, top_k: int | None = None) -> list[RetrievedChunk]:
        """Return the fragments most similar to the question, best score first."""
        limit = top_k or self.config.top_k
        query_vector = self.embedding_client.embed_query(question)
        sparse_query = self.lexical.encode_query(question) if self.lexical else None

        # Over-fetch so that capping any single document still leaves enough
        # fragments to fill the limit.
        raw = self.store.search(query_vector, limit * 3, sparse_query)
        results = self._diversify(raw, limit)

        logger.debug(
            "Retrieved %d fragments (best score: %.3f)",
            len(results),
            results[0].score if results else 0.0,
        )
        return results

    def _diversify(self, results: list[RetrievedChunk], limit: int) -> list[RetrievedChunk]:
        """Cap how many fragments one document may contribute.

        Corpora are rarely balanced: in one measured set a 115-page PDF produced
        81% of the index and a short .md 1.4%, so the PDF crowded the ranking and
        the small document holding the direct answer never appeared. The cap keeps the
        ordering intact but reserves room for other sources.

        Fragments beyond the cap are not discarded — they refill the tail if
        there is nothing else to put there, so a question genuinely answered by
        one document alone still gets its full context.
        """
        if len(results) <= limit:
            return results

        per_document = max(1, round(limit * self.config.max_document_share))

        selected: list[RetrievedChunk] = []
        overflow: list[RetrievedChunk] = []
        counts: dict[str, int] = {}

        for result in results:
            source = result.chunk.source_file
            if counts.get(source, 0) < per_document:
                counts[source] = counts.get(source, 0) + 1
                selected.append(result)
            else:
                overflow.append(result)

        # Order is preserved on both lists, so the tail is still the best of
        # what was left over.
        return (selected + overflow)[:limit]

    def has_sufficient_context(self, results: list[RetrievedChunk]) -> bool:
        """Decide whether the retrieved context is enough to attempt an answer.

        This is the first barrier against hallucination: if not even the best
        fragment clears the minimum threshold, the question falls plainly
        outside the corpus and the engine answers "no information" without
        consulting the model.

        The threshold is deliberately low because the scores of legitimate and
        unrelated questions overlap (see config.yaml), so filtering tightly here
        would reject valid questions. Fine discrimination is left to the prompt,
        which weighs content rather than vector similarity alone.

        Results arrive sorted by descending score, so only the first matters.
        """
        if not results:
            return False
        return results[0].score >= self.config.min_score_threshold

    def is_low_confidence(self, results: list[RetrievedChunk], question: str | None = None) -> bool:
        """True if the retrieved fragments are of doubtful relevance.

        Two signals, because neither suffices alone:

        - **Dense similarity.** Works for descriptive questions, but a short one
          scores low regardless of validity: one measured at 0.307, below an
          unrelated question at 0.364.
        - **Lexical overlap.** How much of the question appears verbatim in a
          retrieved fragment. If a distinctive term is literally there, relevance
          is not in doubt whatever the cosine says. Measured on a test corpus the
          separation is clean: every legitimate question reached at least 0.5,
          every unrelated one exactly 0.0 — precisely because hybrid search now
          surfaces those fragments at all.

        Confidence therefore requires only one of the two to hold. The warning
        exists to be read, and one that fires on correct answers is ignored.
        """
        if not results:
            return True

        if results[0].score >= self.config.low_confidence_threshold:
            return False

        if question is None:
            return True

        return self._lexical_overlap(question, results) < self.config.min_lexical_overlap

    @staticmethod
    def _lexical_overlap(question: str, results: list[RetrievedChunk]) -> float:
        """Largest fraction of the question's terms appearing in one fragment.

        Per fragment rather than pooled across all of them: terms scattered over
        unrelated chunks are a coincidence, whereas finding them together in one
        passage is what makes it the answer.
        """
        question_terms = set(tokenize(question))
        if not question_terms:
            return 0.0

        return max(
            len(question_terms & set(tokenize(result.chunk.text))) / len(question_terms)
            for result in results
        )
