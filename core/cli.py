"""Mnemosyne command line interface.

    mnemosyne ingest              index the configured documents folder
    mnemosyne ask "question?"     query the indexed corpus
    mnemosyne serve               start the web interface and API
    mnemosyne status              show configuration and system state

User-facing strings are in Spanish, matching the language the engine answers in.
"""

import logging
import sys
from pathlib import Path

import typer

from core.config import get_settings
from core.pipeline import Pipeline

app = typer.Typer(
    help="Motor de preguntas y respuestas sobre documentos, 100% local.",
    no_args_is_help=True,
    add_completion=False,
)


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )


@app.command()
def ingest(
    path: Path | None = typer.Option(
        None, "--path", "-p", help="Carpeta de documentos (por defecto, la del config)."
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Log detallado."),
) -> None:
    """Indexa los documentos: los parte en fragmentos y los guarda en el vector store."""
    _setup_logging(verbose)
    settings = get_settings()
    target = path or settings.resolved_docs_path

    typer.echo(f"Indexando documentos de {target} ...")

    pipeline = Pipeline(settings)
    try:
        result = pipeline.ingest(path)
    except Exception as exc:
        typer.secho(f"\nError durante la ingesta: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    finally:
        pipeline.close()

    typer.secho(
        f"\nListo: {result.documents} documentos → {result.chunks} fragmentos "
        f"en la colección '{result.collection}'.",
        fg=typer.colors.GREEN,
    )


@app.command()
def ask(
    question: str = typer.Argument(..., help="La pregunta a responder."),
    top_k: int | None = typer.Option(None, "--top-k", "-k", help="Cuántos fragmentos recuperar."),
    show_sources: bool = typer.Option(
        True, "--sources/--no-sources", help="Mostrar las fuentes citadas."
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Log detallado."),
) -> None:
    """Responde una pregunta usando únicamente los documentos indexados."""
    _setup_logging(verbose)

    pipeline = Pipeline()
    try:
        result = pipeline.answer(question, top_k)
    except Exception as exc:
        typer.secho(f"\nError al responder: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    finally:
        pipeline.close()

    typer.echo()
    color = typer.colors.YELLOW if result.insufficient_context else typer.colors.WHITE
    typer.secho(result.answer, fg=color)

    if result.low_confidence:
        typer.echo()
        typer.secho(
            "⚠  Los fragmentos encontrados tienen relevancia dudosa para esta "
            "pregunta.\n   Verifica la respuesta contra las fuentes citadas abajo.",
            fg=typer.colors.YELLOW,
        )

    if show_sources and result.sources:
        typer.echo()
        typer.secho("Fuentes:", bold=True)
        for i, source in enumerate(result.sources, start=1):
            location = source.source_file
            if source.section:
                location += f" § {source.section}"
            typer.echo(f"  [{i}] {location}  (similitud: {source.score:.3f})")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Interfaz donde escuchar."),
    port: int = typer.Option(8100, "--port", "-p", help="Puerto."),
    reload: bool = typer.Option(False, "--reload", help="Recargar al cambiar el código."),
) -> None:
    """Levanta la interfaz web y la API."""
    import uvicorn

    typer.secho(f"\n  Interfaz web:  http://{host}:{port}", fg=typer.colors.GREEN)
    typer.echo(f"  Documentación: http://{host}:{port}/docs\n")
    uvicorn.run("api.main:app", host=host, port=port, reload=reload)


@app.command()
def status(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Log detallado."),
) -> None:
    """Muestra la configuración activa y el estado de las dependencias."""
    _setup_logging(verbose)
    settings = get_settings()

    typer.secho("Configuración", bold=True)
    typer.echo(f"  proyecto:            {settings.project.name}")
    typer.echo(f"  documentos:          {settings.resolved_docs_path}")
    typer.echo(f"  modelo embeddings:   {settings.ollama.embedding_model}")
    typer.echo(f"  modelo generación:   {settings.ollama.generation_model}")
    typer.echo(f"  umbral de similitud: {settings.retrieval.min_score_threshold}")

    pipeline = Pipeline(settings)
    try:
        health = pipeline.health()
    finally:
        pipeline.close()

    typer.echo()
    typer.secho("Estado", bold=True)
    qdrant_ok = bool(health["qdrant"])
    typer.secho(
        f"  qdrant:              {'conectado' if qdrant_ok else 'NO DISPONIBLE'}",
        fg=typer.colors.GREEN if qdrant_ok else typer.colors.RED,
    )
    typer.echo(f"  colección:           {health['collection']}")
    typer.echo(f"  fragmentos indexados: {health['indexed_chunks']}")

    # Non-zero exit so scripts and healthchecks can detect a broken deployment.
    if not qdrant_ok:
        sys.exit(1)


if __name__ == "__main__":
    app()
