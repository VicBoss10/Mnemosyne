"""Mnemosyne HTTP API.

Exposes the engine over HTTP and serves the static web interface:

    GET  /              chat interface
    GET  /project       active project's title and sample questions
    POST /query         question and complete answer (JSON)
    GET  /query/stream  question with the answer streamed as SSE
    POST /ingest        re-index the documents
    GET  /health        system state

There is no authentication or rate limiting: the API assumes a trusted network,
which is what phase 2's gateway is meant to provide.
"""

import json
import logging
from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from api.schemas import (
    HealthResponse,
    IngestRequest,
    IngestResponse,
    ProjectInfoResponse,
    QueryRequest,
    QueryResponse,
)
from core.config import get_settings
from core.pipeline import Pipeline

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"

#: Built once at startup: it holds open connections to Ollama and Qdrant, so
#: that cost is not paid on every request.
pipeline: Pipeline | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pipeline
    pipeline = Pipeline(get_settings())
    logger.info("Engine initialized")
    yield
    if pipeline is not None:
        pipeline.close()
    logger.info("Engine closed")


app = FastAPI(
    title="Mnemosyne",
    description="Motor de preguntas y respuestas sobre documentos, 100% local.",
    version="0.1.0",
    lifespan=lifespan,
)


def get_pipeline() -> Pipeline:
    """Return the active pipeline, or fail if startup has not completed."""
    if pipeline is None:
        raise HTTPException(status_code=503, detail="The engine is not initialized")
    return pipeline


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """System state and number of indexed fragments."""
    engine = get_pipeline()
    status = engine.health()
    return HealthResponse(
        status="ok" if status["qdrant"] else "degraded",
        qdrant=bool(status["qdrant"]),
        collection=str(status["collection"]),
        indexed_chunks=int(status["indexed_chunks"]),
    )


@app.get("/project", response_model=ProjectInfoResponse)
def project_info() -> ProjectInfoResponse:
    """Title and sample questions of the active project.

    The web interface reads these on load, so the HTML need not know anything
    about the documents' subject matter.
    """
    project = get_settings().project
    return ProjectInfoResponse(
        name=project.name,
        title=project.title,
        sample_questions=project.sample_questions,
    )


@app.post("/query", response_model=QueryResponse)
def query(request: QueryRequest) -> QueryResponse:
    """Answer a question using only the indexed documents."""
    engine = get_pipeline()
    try:
        answer = engine.answer(request.question, request.top_k)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to answer")
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return QueryResponse.from_answer(answer)


def _sse_events(question: str, top_k: int | None) -> Iterator[str]:
    """Translate the pipeline's events into the Server-Sent Events format."""
    engine = get_pipeline()
    for event in engine.answer_stream(question, top_k):
        payload = json.dumps(event.data, ensure_ascii=False)
        yield f"event: {event.event}\ndata: {payload}\n\n"


@app.get("/query/stream")
def query_stream(
    question: str = Query(..., min_length=1, max_length=2000),
    top_k: int | None = Query(None, ge=1, le=20),
) -> StreamingResponse:
    """As /query, but returning the answer as it is generated.

    Uses SSE over GET rather than POST because that is what the browser's
    EventSource consumes without any client library.
    """
    return StreamingResponse(
        _sse_events(question, top_k),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Stops an intervening proxy from buffering the response and
            # defeating the point of streaming.
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/ingest", response_model=IngestResponse)
def ingest(request: IngestRequest) -> IngestResponse:
    """Re-index the documents, rebuilding the collection from scratch.

    Destructive and unauthenticated: it drops the existing collection and will
    index any path readable by the process. Safe while the API is bound to
    localhost, but it must sit behind authentication before being exposed.
    """
    engine = get_pipeline()
    path = Path(request.path) if request.path else None
    try:
        result = engine.ingest(path)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Ingestion failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return IngestResponse(
        documents=result.documents, chunks=result.chunks, collection=result.collection
    )


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """Serve the chat interface."""
    return FileResponse(STATIC_DIR / "index.html")


# Mounted last so the static route does not shadow the API routes.
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
