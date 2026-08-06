"""Tests del registro de proyectos.

Lo que se comprueba aquí es lo que la interfaz da por hecho: que el listado
llega ordenado por uso, que dos proyectos con el mismo nombre no comparten
colección, y que borrar uno no toca los documentos de nadie.
"""

import json

import pytest

from core.projects import Project, ProjectRegistry, slugify


@pytest.fixture
def registry(tmp_path):
    return ProjectRegistry(tmp_path / "datos")


@pytest.fixture
def docs(tmp_path):
    folder = tmp_path / "documentos"
    folder.mkdir()
    (folder / "nota.md").write_text("# Nota\n\nContenido.", encoding="utf-8")
    return folder


def test_registry_starts_empty(registry):
    assert registry.all() == []


def test_create_registers_the_project(registry, docs):
    project = registry.create("Drones", docs)

    assert project.slug == "drones"
    assert project.name == "Drones"
    assert registry.get("drones") == project


def test_docs_path_is_stored_absolute(registry, docs, monkeypatch):
    """El registro no puede depender del directorio de trabajo de quien lo lea."""
    monkeypatch.chdir(docs.parent)

    project = registry.create("Relativo", "documentos")

    assert project.path.is_absolute()
    assert project.path == docs.resolve()


def test_create_rejects_a_missing_folder(registry, tmp_path):
    with pytest.raises(ValueError, match="no existe"):
        registry.create("Fantasma", tmp_path / "no-esta-aqui")


def test_same_name_yields_distinct_slugs(registry, docs):
    """El slug nombra la colección de Qdrant: dos iguales mezclarían corpus."""
    first = registry.create("Drones", docs)
    second = registry.create("Drones", docs)

    assert first.slug == "drones"
    assert second.slug == "drones-2"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Proyecto MOVE", "proyecto-move"),
        ("  Espacios  ", "espacios"),
        ("Acentos: ñandú", "acentos-and"),
        ("!!!", "proyecto"),
    ],
)
def test_slugify_produces_valid_collection_names(name, expected):
    assert slugify(name) == expected


def test_listing_is_ordered_by_last_opened(registry, docs):
    """Lo último usado va primero: es lo que el dashboard muestra arriba."""
    registry.create("Primero", docs)
    registry.create("Segundo", docs)
    registry.create("Tercero", docs)

    registry.mark_opened("primero")
    registry.mark_opened("tercero")

    assert [p.slug for p in registry.all()][:2] == ["tercero", "primero"]


def test_never_opened_projects_go_last(registry, docs):
    registry.create("Usado", docs)
    registry.create("Sin usar", docs)

    registry.mark_opened("usado")

    assert [p.slug for p in registry.all()] == ["usado", "sin-usar"]


def test_mark_indexed_records_the_result(registry, docs):
    registry.create("Drones", docs)

    project = registry.mark_indexed("drones", documents=12, chunks=340)

    assert (project.documents, project.chunks) == (12, 340)
    assert project.indexed_at is not None


def test_update_rejects_unknown_fields(registry, docs):
    registry.create("Drones", docs)

    with pytest.raises(ValueError, match="desconocido"):
        registry.update("drones", inventado=1)


def test_update_fails_for_a_missing_project(registry):
    with pytest.raises(KeyError):
        registry.update("no-existe", name="X")


def test_delete_leaves_the_documents_alone(registry, docs):
    """Los documentos son del usuario: el registro nunca los toca."""
    registry.create("Drones", docs)

    registry.delete("drones")

    assert registry.get("drones") is None
    assert (docs / "nota.md").is_file()


def test_exists_reports_a_folder_that_went_away(registry, docs):
    """Mover la carpeta no debe romper: hay que poder avisar antes de indexar."""
    project = registry.create("Drones", docs)
    assert project.exists

    docs.rename(docs.parent / "movida")

    assert not registry.get("drones").exists


def test_registry_survives_a_corrupted_file(registry, docs):
    """Un JSON ilegible no puede impedir que la app arranque."""
    registry.create("Drones", docs)
    registry.path.write_text("{esto no es json", encoding="utf-8")

    assert registry.all() == []


def test_saved_file_is_readable_json(registry, docs):
    """Se guarda en claro a propósito: permite recuperarlo a mano."""
    registry.create("Drones", docs)

    payload = json.loads(registry.path.read_text(encoding="utf-8"))

    assert [p["slug"] for p in payload["projects"]] == ["drones"]


def test_project_round_trips_through_disk(registry, docs):
    registry.create("Drones", docs)
    registry.mark_indexed("drones", documents=3, chunks=42)

    reloaded = ProjectRegistry(registry.data_dir).get("drones")

    assert reloaded == Project(
        slug="drones",
        name="Drones",
        docs_path=str(docs.resolve()),
        indexed_at=reloaded.indexed_at,
        documents=3,
        chunks=42,
        opened_at=None,
    )
