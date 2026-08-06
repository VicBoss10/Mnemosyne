"""Data models shared by every stage of the pipeline.

These types are the engine's common vocabulary: the loader produces Documents,
the chunker turns them into Chunks, the retriever returns RetrievedChunks and
the generator produces an Answer. Nothing here depends on Qdrant, Ollama or the
subject matter of the documents.
"""

from pydantic import BaseModel, Field


class Document(BaseModel):
    """A text file read from disk, not yet split."""

    #: Path relative to the documents folder, never absolute: it is shown to the
    #: user as the citation.
    source_file: str
    content: str


class Chunk(BaseModel):
    """A document fragment — the unit that gets embedded and searched."""

    text: str
    source_file: str
    #: Markdown header trail down to this fragment, broadest first, e.g.
    #: ["Manual", "3. Installation", "3.4 Requirements"]. Empty for documents
    #: without headers, such as a plain .txt.
    header_path: list[str] = Field(default_factory=list)
    #: Position within the source document, starting at 0.
    chunk_index: int
    #: Character offset in the original document, so the fragment can be located
    #: in the real file to verify a citation.
    char_start: int
    #: 1-based line where the fragment begins, counted over the extracted text.
    #: For a .md or .txt that is the line in the file itself; for a PDF or DOCX
    #: it is the line of the Markdown the extractor produced, which is why the
    #: page (below) is the locator that matters there.
    start_line: int = 1

    @property
    def citation(self) -> str:
        """Readable citation: 'file.md § Section > Subsection'."""
        if not self.header_path:
            return self.source_file
        return f"{self.source_file} § {' > '.join(self.header_path)}"


class RetrievedChunk(BaseModel):
    """A chunk returned by the search, with its similarity score."""

    chunk: Chunk
    #: Cosine similarity to the question, in [0, 1]. Higher is more relevant.
    score: float


class Source(BaseModel):
    """A cited source as returned to the user.

    Built from the retrieved chunk's metadata, never from the text the model
    generated, so a source cannot be fabricated.
    """

    source_file: str
    section: str
    score: float
    #: Excerpt of the text backing the answer, so it can be verified.
    excerpt: str
    #: Human-readable position inside the document — "página 2", "línea 140".
    #: Empty when the format offers nothing more precise than the section.
    locator: str = ""
    #: True when this fragment's wording actually appears in the generated
    #: answer. Retrieval hands the model ten fragments and it typically uses one
    #: or two; without this every question would claim ten cited sources.
    cited: bool = False


class Answer(BaseModel):
    """Final answer produced by the engine."""

    question: str
    answer: str
    sources: list[Source] = Field(default_factory=list)
    #: True when the engine found no sufficient context, letting the caller tell
    #: an honest "I don't know" apart from a real answer.
    insufficient_context: bool = False
    #: True when the retrieved fragments were of doubtful relevance. The answer
    #: is still produced, but the caller should advise verifying it against the
    #: cited sources.
    low_confidence: bool = False
