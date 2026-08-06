"""Tests de los endpoints de proyectos.

Cubren el contrato que consume el dashboard: qué se lista y en qué orden, qué
pasa al crear con datos inválidos, y que borrar no toque los documentos.

`Pipeline` se sustituye por un doble —igual que en test_workspace— para que la
suite siga corriendo sin Qdrant ni Ollama. Lo que se prueba aquí es la capa
HTTP: códigos de estado, forma de la respuesta y qué llega al registro.
"""

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from api import main


@dataclass
class FakeStore:
    collection_name: str
    dropped: bool = False

    def drop_collection(self) -> None:
        self.dropped = True


class FakePipeline:
    """Doble de Pipeline: no abre ninguna conexión."""

    def __init__(self, settings):
        self.settings = settings
        self.store = FakeStore(collection_name=settings.collection_name)
        self.closed = False

    def ingest(self, path=None):
        @dataclass
        class Result:
            documents: int = 2
            chunks: int = 7
            collection: str = ""

        return Result(collection=self.settings.collection_name)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def docs(tmp_path):
    folder = tmp_path / "documentos"
    folder.mkdir()
    (folder / "nota.md").write_text("# Nota\n\nContenido.", encoding="utf-8")
    return folder


@pytest.fixture
def client(tmp_path, settings, monkeypatch):
    """Cliente contra una API con su propio directorio de datos.

    El directorio se aísla por test: el registro es un archivo y dos tests
    compartiéndolo se pisarían.
    """
    monkeypatch.setattr("core.workspace.Pipeline", FakePipeline)
    monkeypatch.setenv("MNEMOSYNE_DATA_DIR", str(tmp_path / "datos"))
    monkeypatch.setattr(main, "get_settings", lambda: settings)

    with TestClient(main.app) as test_client:
        yield test_client


def test_listing_starts_empty(client):
    """Es el estado de la primera vez que se abre la app."""
    response = client.get("/projects")

    assert response.status_code == 200
    assert response.json() == []


def test_create_returns_the_registered_project(client, docs):
    response = client.post("/projects", json={"name": "Drones", "docs_path": str(docs)})

    assert response.status_code == 201
    body = response.json()
    assert body["slug"] == "drones"
    assert body["name"] == "Drones"
    assert body["folder_exists"] is True
    # Registrar no indexa: son pasos separados y el dashboard lo muestra así.
    assert body["indexed_at"] is None
    assert body["chunks"] == 0


def test_create_rejects_a_folder_that_does_not_exist(client, tmp_path):
    """El usuario puede teclear la ruta: el error tiene que ser suyo, no un 500."""
    response = client.post(
        "/projects", json={"name": "Fantasma", "docs_path": str(tmp_path / "no-existe")}
    )

    assert response.status_code == 400
    assert "no existe" in response.json()["detail"].lower()


def test_create_rejects_an_empty_name(client, docs):
    response = client.post("/projects", json={"name": "", "docs_path": str(docs)})

    assert response.status_code == 422


def test_two_projects_with_the_same_name_get_different_slugs(client, docs):
    """El slug nombra una colección de Qdrant: colisionar mezclaría dos corpus."""
    first = client.post("/projects", json={"name": "Drones", "docs_path": str(docs)}).json()
    second = client.post("/projects", json={"name": "Drones", "docs_path": str(docs)}).json()

    assert first["slug"] != second["slug"]


def test_listing_puts_the_most_recently_opened_first(client, docs):
    """Es el orden que espera quien abre la app."""
    client.post("/projects", json={"name": "Drones", "docs_path": str(docs)})
    client.post("/projects", json={"name": "Move", "docs_path": str(docs)})

    client.post("/projects/drones/open")

    assert [p["slug"] for p in client.get("/projects").json()] == ["drones", "move"]


def test_opening_a_project_makes_it_the_active_one(client, docs):
    """Sin `?project=` los endpoints van al activo, y abrir es lo que lo cambia."""
    client.post("/projects", json={"name": "Drones", "docs_path": str(docs)})
    client.post("/projects", json={"name": "Move", "docs_path": str(docs)})

    client.post("/projects/move/open")

    assert client.get("/project").json()["name"] == "move"


def test_opening_an_unknown_project_is_a_404(client):
    assert client.post("/projects/fantasma/open").status_code == 404


def test_delete_removes_it_from_the_listing(client, docs):
    client.post("/projects", json={"name": "Drones", "docs_path": str(docs)})

    response = client.delete("/projects/drones")

    assert response.status_code == 204
    assert client.get("/projects").json() == []


def test_delete_leaves_the_documents_alone(client, docs):
    """Son del usuario: borrar un proyecto borra su índice, nunca su carpeta."""
    client.post("/projects", json={"name": "Drones", "docs_path": str(docs)})

    client.delete("/projects/drones")

    assert (docs / "nota.md").is_file()


def test_delete_of_an_unknown_project_is_a_404(client):
    """Distinguirlo de un borrado correcto evita que la interfaz mienta."""
    assert client.delete("/projects/fantasma").status_code == 404


def test_a_project_whose_folder_went_away_is_flagged(client, docs):
    """El chat sigue —el índice está en Qdrant— pero no se puede reindexar."""
    client.post("/projects", json={"name": "Drones", "docs_path": str(docs)})
    docs.rename(docs.parent / "movida")

    assert client.get("/projects").json()[0]["folder_exists"] is False


def test_ingest_updates_what_the_card_shows(client, docs):
    """El dashboard lee esos números del registro, no de Qdrant."""
    client.post("/projects", json={"name": "Drones", "docs_path": str(docs)})

    client.post("/ingest?project=drones", json={})

    card = client.get("/projects").json()[0]
    assert (card["documents"], card["chunks"]) == (2, 7)
    assert card["indexed_at"] is not None


def test_dependencies_are_checked_without_any_project(client, monkeypatch):
    """Es lo que llama el asistente de primer arranque, y ahí no hay proyectos.

    Justo cuando más importa saber si falta Ollama es en una instalación
    recién hecha, así que este chequeo no puede depender de que haya un corpus.
    """
    monkeypatch.setattr(
        "core.pipeline.Pipeline.check_dependencies",
        staticmethod(lambda settings: {"ollama": False, "missing_models": ["bge-m3"]}),
    )

    response = client.get("/dependencies")

    assert response.status_code == 200
    assert response.json() == {"ollama": False, "missing_models": ["bge-m3"]}


def test_indexing_without_the_folder_says_so_in_spanish(client, docs, monkeypatch):
    """El mensaje se muestra tal cual en la tarjeta: el del motor va en inglés."""
    client.post("/projects", json={"name": "Drones", "docs_path": str(docs)})

    def gone(self, path=None):
        raise FileNotFoundError(f"Documents folder does not exist: {docs}")

    monkeypatch.setattr(FakePipeline, "ingest", gone)
    response = client.post("/ingest?project=drones", json={})

    assert response.status_code == 400
    assert "carpeta" in response.json()["detail"].lower()


def test_querying_without_any_project_explains_itself(client):
    """Antes de crear el primero: mejor un 409 con motivo que un 500."""
    response = client.post("/query", json={"question": "¿hola?"})

    assert response.status_code == 409
    assert "proyecto" in response.json()["detail"].lower()
