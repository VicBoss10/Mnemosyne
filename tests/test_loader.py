"""Tests del loader."""

import pytest

from core.loader import load_documents


def test_loads_supported_extensions(tmp_path):
    (tmp_path / "uno.md").write_text("# Uno\n\nContenido.", encoding="utf-8")
    (tmp_path / "dos.txt").write_text("Contenido plano.", encoding="utf-8")
    (tmp_path / "tres.markdown").write_text("# Tres\n\nContenido.", encoding="utf-8")

    documents = load_documents(tmp_path)

    assert {d.source_file for d in documents} == {"uno.md", "dos.txt", "tres.markdown"}


def test_ignores_unsupported_extensions(tmp_path):
    """Un PDF o un binario no se leen como texto: se omiten en silencio."""
    (tmp_path / "doc.md").write_text("# Doc\n\nContenido.", encoding="utf-8")
    (tmp_path / "imagen.png").write_bytes(b"\x89PNG\r\n")
    (tmp_path / "manual.pdf").write_bytes(b"%PDF-1.4")

    documents = load_documents(tmp_path)

    assert [d.source_file for d in documents] == ["doc.md"]


def test_reads_nested_folders_with_relative_paths(tmp_path):
    """La ruta relativa es lo que se muestra como fuente, no la absoluta."""
    nested = tmp_path / "guias" / "avanzado"
    nested.mkdir(parents=True)
    (nested / "config.md").write_text("# Config\n\nContenido.", encoding="utf-8")

    documents = load_documents(tmp_path)

    assert documents[0].source_file == "guias/avanzado/config.md"


def test_skips_empty_files(tmp_path):
    (tmp_path / "vacio.md").write_text("   \n\n  ", encoding="utf-8")
    (tmp_path / "lleno.md").write_text("# Lleno\n\nContenido.", encoding="utf-8")

    documents = load_documents(tmp_path)

    assert [d.source_file for d in documents] == ["lleno.md"]


def test_skips_non_utf8_files(tmp_path):
    """Un archivo con otra codificación no debe romper toda la ingesta."""
    (tmp_path / "raro.txt").write_bytes(b"\xff\xfe\x00binario")
    (tmp_path / "bueno.md").write_text("# Bueno\n\nContenido.", encoding="utf-8")

    documents = load_documents(tmp_path)

    assert [d.source_file for d in documents] == ["bueno.md"]


def test_result_is_sorted_for_reproducibility(tmp_path):
    """El orden estable hace que dos ingestas den el mismo resultado."""
    for name in ["c.md", "a.md", "b.md"]:
        (tmp_path / name).write_text(f"# {name}\n\nContenido.", encoding="utf-8")

    documents = load_documents(tmp_path)

    assert [d.source_file for d in documents] == ["a.md", "b.md", "c.md"]


def test_missing_folder_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_documents(tmp_path / "no-existe")


def test_file_instead_of_folder_raises(tmp_path):
    archivo = tmp_path / "no-soy-carpeta.md"
    archivo.write_text("contenido", encoding="utf-8")

    with pytest.raises(NotADirectoryError):
        load_documents(archivo)
