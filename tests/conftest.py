"""Shared fixtures.

The core tests run without Qdrant or Ollama: external dependencies are replaced
by test doubles. That keeps the suite deterministic and runnable in CI, with no
reliance on what a model happens to answer.
"""

import pytest

from core.config import (
    ChunkingConfig,
    OllamaConfig,
    ProjectConfig,
    QdrantConfig,
    RetrievalConfig,
    Settings,
)
from core.models import Chunk, RetrievedChunk


@pytest.fixture
def settings() -> Settings:
    """Test configuration, independent of the repository's config.yaml."""
    return Settings(
        project=ProjectConfig(name="test", docs_path="tests/fixtures"),
        ollama=OllamaConfig(embedding_dim=4),
        qdrant=QdrantConfig(),
        chunking=ChunkingConfig(),
        retrieval=RetrievalConfig(min_score_threshold=0.5, low_confidence_threshold=0.65),
    )


def make_chunk(text: str = "Contenido de prueba", **kwargs) -> Chunk:
    """Chunk with sensible defaults."""
    defaults = {
        "text": text,
        "source_file": "doc.md",
        "header_path": ["Título", "Sección"],
        "chunk_index": 0,
        "char_start": 0,
    }
    return Chunk(**{**defaults, **kwargs})


def make_retrieved(score: float, text: str = "Contenido de prueba", **kwargs):
    """RetrievedChunk with a given score, for exercising the thresholds."""
    return RetrievedChunk(chunk=make_chunk(text, **kwargs), score=score)


class FakeEmbeddingClient:
    """Embedding client double: never calls Ollama."""

    def __init__(self, dim: int = 4) -> None:
        self.dim = dim
        self.embedded: list[str] = []

    def embed(self, text: str) -> list[float]:
        self.embedded.append(text)
        return [0.1] * self.dim

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.embedded.extend(texts)
        return [[0.1] * self.dim for _ in texts]

    def close(self) -> None:
        pass


class FakeStore:
    """Vector store double: returns preset results."""

    def __init__(self, results: list[RetrievedChunk] | None = None) -> None:
        self.results = results or []
        self.upserted: list[Chunk] = []
        self.recreated = False

    def recreate_collection(self) -> None:
        self.recreated = True

    def upsert_chunks(self, chunks, embeddings) -> None:
        self.upserted.extend(chunks)

    def search(self, query_vector, top_k):
        return self.results[:top_k]

    def count(self) -> int:
        return len(self.upserted)

    def health_check(self) -> bool:
        return True


class FakeGenerator:
    """Generator double: returns fixed text and records what it received."""

    def __init__(self, response: str = "Respuesta generada.") -> None:
        self.response = response
        self.calls: list[tuple[str, list[RetrievedChunk]]] = []

    def generate(self, question: str, chunks: list[RetrievedChunk]) -> str:
        self.calls.append((question, chunks))
        return self.response

    def generate_stream(self, question: str, chunks: list[RetrievedChunk]):
        self.calls.append((question, chunks))
        yield from self.response.split(" ")

    def close(self) -> None:
        pass
