"""Mnemosyne HTTP API.

Exposes the engine over HTTP and serves the static web interface:

    GET    /                       chat interface
    GET    /projects               registered projects, most recently used first
    POST   /projects               register a project (does not index it)
    DELETE /projects/{slug}        drop its index and registry entry
    POST   /projects/{slug}/open   mark it as in use and make it active
    GET    /project                active project's title and sample questions
    POST   /query                  question and complete answer (JSON)
    GET    /query/stream           question with the answer streamed as SSE
    POST   /ingest                 re-index the documents
    GET    /health                 system state
    GET    /dependencies           inference runtime and model availability
    POST   /dependencies/pull      download the missing models, progress as SSE

Every endpoint that acts on a corpus takes an optional `?project=slug`.
Omitting it means the active one — the most recently opened.

There is no authentication or rate limiting: the API assumes a trusted network,
which is what phase 2's gateway is meant to provide.
"""

import json
import logging
import os
from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers
from starlette.responses import Response
from starlette.types import Scope

from api.schemas import (
    CreateProjectRequest,
    DependenciesResponse,
    HealthResponse,
    IngestRequest,
    IngestResponse,
    ProjectInfoResponse,
    ProjectSummary,
    QueryRequest,
    QueryResponse,
)
from core.config import get_settings
from core.pipeline import Pipeline
from core.workspace import Workspace

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"

#: Built once at startup. Holds one pipeline per project, each with its own open
#: connections to Ollama and Qdrant, so that cost is not paid on every request.
workspace: Workspace | None = None


def _data_dir() -> Path:
    """Where the project registry lives.

    The desktop app points this at the OS data directory; running from the
    repository it falls back to a local folder, so the CLI and a dev server
    share one registry.
    """
    configured = os.environ.get("MNEMOSYNE_DATA_DIR")
    return Path(configured) if configured else Path.cwd() / ".mnemosyne"


@asynccontextmanager
async def lifespan(app: FastAPI):
    global workspace
    workspace = Workspace(_data_dir(), base_settings=get_settings())
    logger.info("Engine initialized")
    yield
    if workspace is not None:
        workspace.close()
    logger.info("Engine closed")


app = FastAPI(
    title="Mnemosyne",
    description="Motor de preguntas y respuestas sobre documentos, 100% local.",
    version="0.1.0",
    lifespan=lifespan,
)


def get_workspace() -> Workspace:
    """Return the workspace, or fail if startup has not completed."""
    if workspace is None:
        raise HTTPException(status_code=503, detail="The engine is not initialized")
    return workspace


def get_pipeline(project: str | None = None) -> Pipeline:
    """Return the pipeline for a project, or for the active one.

    Every endpoint takes an optional project. Omitting it means "the one in use",
    which is what the single-project setup did before projects existed and what
    the CLI still expects.
    """
    space = get_workspace()
    slug = project or _active_slug(space)

    if slug is None:
        raise HTTPException(
            status_code=409,
            detail="No hay ningún proyecto. Creá uno antes de consultar.",
        )

    try:
        return space.pipeline_for(slug)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _active_slug(space: Workspace) -> str | None:
    """The project a request refers to when it does not name one.

    The most recently opened, which is the one on screen: the registry lists
    them in that order.
    """
    projects = space.registry.all()
    return projects[0].slug if projects else None


@app.get("/projects", response_model=list[ProjectSummary])
def list_projects() -> list[ProjectSummary]:
    """The registered projects, most recently used first.

    That order is what the dashboard shows: yesterday's work at the top,
    without having to look for it.
    """
    space = get_workspace()
    return [ProjectSummary.from_project(p) for p in space.registry.all()]


@app.post("/projects", response_model=ProjectSummary, status_code=201)
def create_project(request: CreateProjectRequest) -> ProjectSummary:
    """Register a project. Does not index it: that is a separate, visible step.

    Kept apart because indexing takes minutes on a real corpus, and a create
    call that silently blocks for them gives no way to show progress.
    """
    space = get_workspace()
    try:
        project = space.registry.create(request.name, Path(request.docs_path))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ProjectSummary.from_project(project)


@app.delete("/projects/{slug}", status_code=204)
def delete_project(slug: str) -> Response:
    """Remove a project: its index and its entry in the registry.

    The documents are left alone — they are the user's and live in their own
    folder, which is why this is safe to offer behind a single confirmation.
    """
    space = get_workspace()
    if space.registry.get(slug) is None:
        raise HTTPException(status_code=404, detail=f"No hay ningún proyecto '{slug}'")

    space.delete(slug)
    return Response(status_code=204)


@app.post("/projects/{slug}/open", response_model=ProjectSummary)
def open_project(slug: str) -> ProjectSummary:
    """Mark a project as in use and make it the active one.

    Entering a project from the dashboard is what reorders the list, so it is a
    deliberate call: merely reading a project's state does not count as using
    it. Opens its pipeline too, so the first question does not pay for it.
    """
    space = get_workspace()
    try:
        space.open(slug)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    project = space.registry.get(slug)
    assert project is not None  # open() ya falló si no existía
    return ProjectSummary.from_project(project)


@app.get("/health", response_model=HealthResponse)
def health(project: str | None = Query(None)) -> HealthResponse:
    """System state and number of indexed fragments."""
    engine = get_pipeline(project)
    status = engine.health()
    return HealthResponse(
        status="ok" if status["qdrant"] else "degraded",
        qdrant=bool(status["qdrant"]),
        collection=str(status["collection"]),
        indexed_chunks=int(status["indexed_chunks"]),
    )


@app.get("/project", response_model=ProjectInfoResponse)
def project_info(project: str | None = Query(None)) -> ProjectInfoResponse:
    """Title and sample questions of a project, or of the active one.

    The web interface reads these on load, so the HTML need not know anything
    about the documents' subject matter.
    """
    space = get_workspace()
    slug = project or _active_slug(space)

    # With no projects registered the interface still has to render: it shows
    # the configured defaults until the first one is created.
    if slug is None:
        configured = get_settings().project
        return ProjectInfoResponse(
            name=configured.name,
            title=configured.title,
            sample_questions=configured.sample_questions,
        )

    registered = space.registry.get(slug)
    if registered is None:
        raise HTTPException(status_code=404, detail=f"No hay ningún proyecto '{slug}'")

    settings = space.settings_for(registered)
    return ProjectInfoResponse(
        name=settings.project.name,
        title=settings.project.title,
        sample_questions=settings.project.sample_questions,
    )


@app.post("/query", response_model=QueryResponse)
def query(request: QueryRequest, project: str | None = Query(None)) -> QueryResponse:
    """Answer a question using only the indexed documents."""
    engine = get_pipeline(project)
    try:
        answer = engine.answer(request.question, request.top_k)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to answer")
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return QueryResponse.from_answer(answer)


def _sse_events(engine: Pipeline, question: str, top_k: int | None) -> Iterator[str]:
    """Translate the pipeline's events into the Server-Sent Events format."""
    for event in engine.answer_stream(question, top_k):
        payload = json.dumps(event.data, ensure_ascii=False)
        yield f"event: {event.event}\ndata: {payload}\n\n"


@app.get("/query/stream")
def query_stream(
    question: str = Query(..., min_length=1, max_length=2000),
    top_k: int | None = Query(None, ge=1, le=20),
    project: str | None = Query(None),
) -> StreamingResponse:
    """As /query, but returning the answer as it is generated.

    Uses SSE over GET rather than POST because that is what the browser's
    EventSource consumes without any client library.
    """
    # Resolved before the response starts: raising inside the generator would
    # surface as a broken stream instead of a proper status code.
    engine = get_pipeline(project)
    return StreamingResponse(
        _sse_events(engine, question, top_k),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Stops an intervening proxy from buffering the response and
            # defeating the point of streaming.
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/dependencies", response_model=DependenciesResponse)
def dependencies() -> DependenciesResponse:
    """Whether the inference runtime is up and has the configured models."""
    status = get_pipeline().dependencies()
    return DependenciesResponse(
        ollama=bool(status["ollama"]),
        missing_models=list(status["missing_models"]),  # type: ignore[arg-type]
    )


@app.post("/dependencies/pull")
def pull_models() -> StreamingResponse:
    """Download the configured models that are missing, streaming the progress.

    Several gigabytes over several minutes, so the progress is streamed rather
    than made to wait in silence. Ollama already reports it as NDJSON and the
    events are forwarded as they arrive, adding only which model they belong to.
    """
    engine = get_pipeline()
    missing = list(engine.dependencies()["missing_models"])  # type: ignore[arg-type]
    url = engine.settings.ollama.url

    def events() -> Iterator[str]:
        for model in missing:
            try:
                with httpx.stream(
                    "POST",
                    f"{url}/api/pull",
                    json={"model": model, "stream": True},
                    # No read timeout: verifying a multi-gigabyte blob emits
                    # nothing for a while, and cutting there would abort a
                    # download that is progressing fine.
                    timeout=httpx.Timeout(30.0, read=None),
                ) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if not line.strip():
                            continue
                        payload = json.loads(line)
                        payload["model"] = model
                        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            except (httpx.HTTPError, ValueError) as exc:
                error = {"model": model, "error": str(exc)}
                yield f"data: {json.dumps(error, ensure_ascii=False)}\n\n"
                return

        yield 'data: {"done": true}\n\n'

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/ingest", response_model=IngestResponse)
def ingest(request: IngestRequest, project: str | None = Query(None)) -> IngestResponse:
    """Re-index the documents, rebuilding the collection from scratch.

    Destructive and unauthenticated: it drops the existing collection and will
    index any path readable by the process. Safe while the API is bound to
    localhost, but it must sit behind authentication before being exposed.
    """
    space = get_workspace()
    slug = project or _active_slug(space)
    engine = get_pipeline(project)
    path = Path(request.path) if request.path else None

    try:
        result = engine.ingest(path)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Ingestion failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # The registry keeps what the dashboard shows on each card, so it has to
    # learn about an indexing run even when it was requested through the API.
    if slug is not None and space.registry.get(slug) is not None:
        space.registry.mark_indexed(slug, result.documents, result.chunks)

    return IngestResponse(
        documents=result.documents, chunks=result.chunks, collection=result.collection
    )


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """Serve the chat interface.

    Sent with caching disabled: the desktop app loads this page from a webview
    whose cache survives restarts, so without it an updated interface keeps
    showing the previous version until that cache is cleared by hand.
    """
    return FileResponse(
        STATIC_DIR / "index.html",
        headers={"Cache-Control": "no-store, must-revalidate"},
    )


class NoCacheStaticFiles(StaticFiles):
    """Static files served with caching disabled.

    Same reason as the index: the desktop webview keeps its cache across
    restarts. The interface's CSS and JS live in their own files, so they are
    what actually changes between versions — serving them from a stale cache
    would show an old interface over a new engine.
    """

    def is_not_modified(self, response_headers: Headers, request_headers: Headers) -> bool:
        # Sin esto el navegador revalida con su ETag y recibe un 304: la
        # respuesta llega sin cuerpo y el webview reutiliza la copia vieja.
        return False

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store, must-revalidate"
        return response


# Mounted last so the static route does not shadow the API routes.
if STATIC_DIR.exists():
    app.mount("/static", NoCacheStaticFiles(directory=STATIC_DIR), name="static")
