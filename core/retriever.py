"""Retrieval of the fragments relevant to a question.

Embeds the question with the same model used for indexing and searches the
vector store for the nearest chunks.
"""

import logging

from core.config import RetrievalConfig
from core.embeddings import EmbeddingClient
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

    def retrieve(self, question: str, top_k: int | None = None) -> list[RetrievedChunk]:
        """Return the fragments most similar to the question, best score first."""
        query_vector = self.embedding_client.embed(question)
        results = self.store.search(query_vector, top_k or self.config.top_k)

        logger.debug(
            "Retrieved %d fragments (best score: %.3f)",
            len(results),
            results[0].score if results else 0.0,
        )
        return results

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

    def is_low_confidence(self, results: list[RetrievedChunk]) -> bool:
        """True if the retrieved fragments are of doubtful relevance.

        Covers the band where the score ranges overlap: the question is still
        answered, but the user is advised to verify it against the sources.
        """
        if not results:
            return True
        return results[0].score < self.config.low_confidence_threshold
