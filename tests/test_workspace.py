"""Tests del acceso a varios proyectos en el mismo proceso.

Lo que importa aquí es que cada proyecto consulte su propia colección y que los
pipelines se reutilicen: son caros de construir —abren conexiones a Ollama y a
Qdrant— y crear uno por consulta anularía el sentido de tenerlos en caché.

`Pipeline` se sustituye por un doble: comprobar el aislamiento real necesita
Qdrant y Ollama, y eso ya se verificó a mano; lo que se prueba acá es que el
workspace nombre bien cada colección y administre bien su caché.
"""

from dataclasses import dataclass

import pytest

from core.workspace import Workspace


@dataclass
class FakeStore:
    collection_name: str
    dropped: bool = False

    def drop_collection(self) -> None:
        self.dropped = True


class FakePipeline:
    """Doble de Pipeline: registra con qué configuración se construyó."""

    #: Todos los construidos, para poder contarlos.
    built: list["FakePipeline"] = []

    def __init__(self, settings):
        self.settings = settings
        self.store = FakeStore(collection_name=settings.collection_name)
        self.closed = False
        self.ingested = 0
        FakePipeline.built.append(self)

    def ingest(self, path=None):
        self.ingested += 1

        @dataclass
        class Result:
            documents: int = 3
            chunks: int = 42
            collection: str = ""

        return Result(collection=self.settings.collection_name)

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def fake_pipeline(monkeypatch):
    FakePipeline.built = []
    monkeypatch.setattr("core.workspace.Pipeline", FakePipeline)
    return FakePipeline


@pytest.fixture
def docs(tmp_path):
    folder = tmp_path / "documentos"
    folder.mkdir()
    (folder / "nota.md").write_text("# Nota\n\nContenido.", encoding="utf-8")
    return folder


@pytest.fixture
def workspace(tmp_path, settings):
    return Workspace(tmp_path / "datos", base_settings=settings)


def test_each_project_gets_its_own_collection(workspace, docs):
    """El aislamiento entre corpus depende de esto."""
    workspace.registry.create("Drones", docs)
    workspace.registry.create("Proyecto MOVE", docs)

    drones = workspace.pipeline_for("drones")
    move = workspace.pipeline_for("proyecto-move")

    assert drones.settings.collection_name == "mnemosyne_drones"
    assert move.settings.collection_name == "mnemosyne_proyecto-move"


def test_pipelines_are_reused(workspace, docs):
    """Construirlos abre conexiones: uno por consulta sería caro."""
    workspace.registry.create("Drones", docs)

    first = workspace.pipeline_for("drones")
    second = workspace.pipeline_for("drones")

    assert first is second
    assert len(FakePipeline.built) == 1


def test_base_settings_are_not_mutated(workspace, docs):
    """Se comparten entre proyectos: cambiarlas en sitio movería el corpus ajeno."""
    workspace.registry.create("Drones", docs)
    original = workspace.base_settings.project.name

    workspace.pipeline_for("drones")

    assert workspace.base_settings.project.name == original


def test_project_metadata_reaches_the_settings(workspace, docs):
    workspace.registry.create("Proyecto MOVE", docs)

    pipeline = workspace.pipeline_for("proyecto-move")

    assert pipeline.settings.project.title == "Proyecto MOVE"
    assert pipeline.settings.project.docs_path == str(docs.resolve())


def test_unknown_project_is_rejected(workspace):
    with pytest.raises(KeyError):
        workspace.pipeline_for("no-existe")


def test_open_records_the_use(workspace, docs):
    """Ese registro es lo que ordena el dashboard."""
    workspace.registry.create("Drones", docs)
    assert workspace.registry.get("drones").opened_at is None

    workspace.open("drones")

    assert workspace.registry.get("drones").opened_at is not None


def test_consulting_a_project_is_not_using_it(workspace, docs):
    """Solo las aperturas deliberadas reordenan el listado."""
    workspace.registry.create("Drones", docs)

    workspace.pipeline_for("drones")

    assert workspace.registry.get("drones").opened_at is None


def test_ingest_records_the_result(workspace, docs):
    workspace.registry.create("Drones", docs)

    documents, chunks = workspace.ingest("drones")

    project = workspace.registry.get("drones")
    assert (documents, chunks) == (3, 42)
    assert (project.documents, project.chunks) == (3, 42)
    assert project.indexed_at is not None


def test_ingest_fails_clearly_when_the_folder_went_away(workspace, docs):
    """Mover la carpeta es cosa corriente: el error tiene que decir qué pasó."""
    workspace.registry.create("Drones", docs)
    docs.rename(docs.parent / "movida")

    with pytest.raises(FileNotFoundError, match="ya no existe"):
        workspace.ingest("drones")


def test_delete_drops_the_index_and_closes_the_pipeline(workspace, docs):
    workspace.registry.create("Drones", docs)
    pipeline = workspace.pipeline_for("drones")

    workspace.delete("drones")

    assert pipeline.store.dropped
    assert pipeline.closed
    assert workspace.registry.get("drones") is None


def test_delete_drops_the_index_of_a_project_never_opened(workspace, docs):
    """Sin pipeline en caché la colección puede existir igual, de otra sesión."""
    workspace.registry.create("Drones", docs)

    workspace.delete("drones")

    assert FakePipeline.built[-1].store.dropped


def test_delete_leaves_the_documents_alone(workspace, docs):
    workspace.registry.create("Drones", docs)

    workspace.delete("drones")

    assert (docs / "nota.md").is_file()


def test_close_releases_every_pipeline(workspace, docs):
    workspace.registry.create("Drones", docs)
    workspace.registry.create("Move", docs)
    pipelines = [workspace.pipeline_for("drones"), workspace.pipeline_for("move")]

    workspace.close()

    assert all(p.closed for p in pipelines)
    # Pedirlo otra vez construye uno nuevo en vez de devolver el cerrado.
    assert workspace.pipeline_for("drones") not in pipelines
