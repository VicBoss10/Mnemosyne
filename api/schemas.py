"""Public request and response contracts of the API.

Defined separately from core.models on purpose: the engine's internal models can
change without breaking the API's public contract.
"""

from pydantic import BaseModel, Field

from core.models import Answer


class QueryRequest(BaseModel):
    question: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Natural-language question about the indexed documents.",
    )
    top_k: int | None = Field(
        None,
        ge=1,
        le=20,
        description="How many fragments to retrieve. Falls back to the configured value.",
    )


class SourceResponse(BaseModel):
    """A consulted source, with everything needed to verify the answer."""

    source_file: str
    section: str
    score: float
    excerpt: str
    #: Where inside the document the fragment sits — "página 2", "línea 140".
    locator: str = ""
    #: True when the answer was actually drawn from this fragment. The rest were
    #: retrieved and offered to the model as context, but are not what it used.
    cited: bool = False


class QueryResponse(BaseModel):
    question: str
    answer: str
    sources: list[SourceResponse]
    #: True when the engine found no sufficient context and therefore did not
    #: answer, letting the client tell an honest "I don't know" apart.
    insufficient_context: bool
    #: True when the fragments were of doubtful relevance: the answer was still
    #: produced, but it is worth verifying against the cited sources.
    low_confidence: bool

    @classmethod
    def from_answer(cls, answer: Answer) -> "QueryResponse":
        return cls(
            question=answer.question,
            answer=answer.answer,
            sources=[SourceResponse(**s.model_dump()) for s in answer.sources],
            insufficient_context=answer.insufficient_context,
            low_confidence=answer.low_confidence,
        )


class HealthResponse(BaseModel):
    status: str
    qdrant: bool
    collection: str
    indexed_chunks: int


class ProjectInfoResponse(BaseModel):
    """Project data the interface needs to present itself.

    Exists so the front end need not know what the documents are about: the
    title and sample questions come from the active project's configuration.
    """

    name: str
    title: str
    sample_questions: list[str]


class IngestRequest(BaseModel):
    path: str | None = Field(
        None,
        description="Folder to index. Falls back to the configured one.",
    )


class IngestResponse(BaseModel):
    documents: int
    chunks: int
    collection: str
