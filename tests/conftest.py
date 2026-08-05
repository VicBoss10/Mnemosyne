"""Shared fixtures.

The core tests run without Qdrant or Ollama: external dependencies are replaced
by test doubles. That keeps the suite deterministic and runnable in CI, with no
reliance on what a model happens to answer.
"""

from pathlib import Path

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


def _write_minimal_pdf(path: Path, pages: list[str]) -> None:
    """Write a valid PDF with one text page per string.

    Built by hand rather than with a PDF-writing library: the project would gain
    a dependency used only by the test suite. The structure is the minimum the
    format requires — catalog, page tree, one content stream per page — and the
    cross-reference table is built from the real byte offsets so any conforming
    reader accepts it.
    """
    objects: list[bytes] = []

    def escape(text: str) -> bytes:
        # WinAnsi is the font's encoding; parentheses and backslashes delimit
        # PDF strings and have to be escaped.
        escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        return escaped.encode("cp1252", errors="replace")

    n_pages = len(pages)
    # Object numbering: 1 catalog, 2 page tree, 3 font, then per page a page
    # object and its content stream.
    page_ids = [4 + 2 * i for i in range(n_pages)]

    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    for i, text in enumerate(pages):
        content = b"BT /F1 12 Tf 72 720 Td (" + escape(text) + b") Tj ET"
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {page_ids[i] + 1} 0 R >>".encode()
        )
        objects.append(
            f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream"
        )

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_start = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode()
    out += f"startxref\n{xref_start}\n%%EOF\n".encode()

    path.write_bytes(bytes(out))


@pytest.fixture
def make_pdf():
    """Build a real PDF at a path, one page per string given."""
    return _write_minimal_pdf


@pytest.fixture
def make_docx():
    """Build a real .docx from (style_name, text) pairs; style None means body."""

    def build(path: Path, paragraphs: list[tuple[str | None, str]]) -> None:
        import docx

        document = docx.Document()
        for style, text in paragraphs:
            document.add_paragraph(text, style=style) if style else document.add_paragraph(text)
        document.save(str(path))

    return build


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
