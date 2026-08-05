"""Embedding generation through Ollama.

An embedding is the numeric representation of a text's meaning: two texts about
the same thing produce nearby vectors, and that proximity is what the whole
search rests on.

The question and the documents must be embedded with the SAME model — vectors
from different models are not comparable.

Several embedding models are trained asymmetrically: a question and the passage
answering it do not look alike as text, so the model is taught to place them
together only when each is marked with its role. nomic-embed-text is one of
these, and using it unprefixed measurably degrades retrieval. Hence the
query_prefix / document_prefix settings, empty for models that need none.
"""

import logging

import httpx

from core.config import OllamaConfig

logger = logging.getLogger(__name__)


class EmbeddingError(RuntimeError):
    """Raised when Ollama fails to produce embeddings."""


class EmbeddingClient:
    """Client for Ollama's embeddings endpoint."""

    def __init__(self, config: OllamaConfig) -> None:
        self.config = config
        self._client = httpx.Client(base_url=config.url, timeout=config.timeout)

    def embed(self, text: str) -> list[float]:
        """Generate the embedding for a single text, with no role prefix.

        Kept for callers that embed raw text. Retrieval should use embed_query
        and embed_documents instead, so each side gets its role marker.
        """
        return self.embed_batch([text])[0]

    def embed_query(self, text: str) -> list[float]:
        """Embed a question, marked as a search query."""
        return self.embed_batch([f"{self.config.query_prefix}{text}"])[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed passages to be indexed, marked as searchable documents."""
        prefix = self.config.document_prefix
        return self.embed_batch([f"{prefix}{text}" for text in texts])

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for several texts in one call.

        Ollama's /api/embed accepts a list and responds in the same order.

        Raises:
            EmbeddingError: if Ollama is unreachable, rejects the request, or
                returns vectors whose dimension contradicts the configuration.
        """
        if not texts:
            return []

        try:
            response = self._client.post(
                "/api/embed",
                json={"model": self.config.embedding_model, "input": texts},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # By far the most common cause: the model was never pulled.
            raise EmbeddingError(
                f"Ollama rejected the embeddings request ({exc.response.status_code}). "
                f"Is the model '{self.config.embedding_model}' downloaded? "
                f"Try: ollama pull {self.config.embedding_model}"
            ) from exc
        except httpx.RequestError as exc:
            raise EmbeddingError(
                f"Could not reach Ollama at {self.config.url}. Is it running? Try: ollama serve"
            ) from exc

        embeddings = response.json().get("embeddings")
        if not embeddings:
            raise EmbeddingError("Ollama returned a response with no embeddings")

        # A dimension mismatch means the Qdrant collection was built for another
        # model, which would make the search return meaningless results. Failing
        # here with a clear message beats debugging bad retrieval later.
        actual_dim = len(embeddings[0])
        if actual_dim != self.config.embedding_dim:
            raise EmbeddingError(
                f"Model '{self.config.embedding_model}' produces {actual_dim}-dimensional "
                f"vectors, but config.yaml declares {self.config.embedding_dim}. "
                "Fix embedding_dim and re-ingest the documents."
            )

        return embeddings

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "EmbeddingClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
