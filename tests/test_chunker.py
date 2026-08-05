"""Tests del chunker.

Lo crítico acá es que la jerarquía de headers sobreviva al partido: de eso
dependen las citas, que son la razón de ser del sistema.
"""

from core.chunker import MIN_BODY_LENGTH, chunk_document, chunk_documents
from core.config import ChunkingConfig
from core.models import Document

CONFIG = ChunkingConfig(max_chunk_size=1200, overlap=150, min_chunk_size=50)


def body(text: str) -> str:
    """Cuerpo de sección garantizado por encima de MIN_BODY_LENGTH.

    Los tests que verifican jerarquía de headers no deberían fallar por un
    detalle de longitud, así que el relleno se agrega acá una sola vez.
    """
    padding = " Texto de relleno para superar el mínimo de cuerpo exigido."
    while len(text) < MIN_BODY_LENGTH:
        text += padding
    return text


def make_doc(content: str, name: str = "test.md") -> Document:
    return Document(source_file=name, content=content)


def test_hierarchical_headers_are_preserved():
    """Un header anidado arrastra la ruta completa de sus ancestros."""
    doc = make_doc(
        f"# Manual\n\n{body('Introducción al sistema.')}\n\n"
        f"## Arquitectura\n\n{body('Descripción de los servicios.')}\n\n"
        f"### Backend\n\n{body('Java 21 con Spring Boot.')}\n"
    )
    chunks = chunk_document(doc, CONFIG)

    paths = [c.header_path for c in chunks]
    assert ["Manual"] in paths
    assert ["Manual", "Arquitectura"] in paths
    assert ["Manual", "Arquitectura", "Backend"] in paths


def test_sibling_header_replaces_previous_sibling():
    """Un header hermano no se acumula sobre el anterior, lo reemplaza."""
    doc = make_doc(
        f"# Doc\n\n{body('Preámbulo del documento.')}\n\n"
        f"## Primera\n\n{body('Contenido de la primera sección.')}\n\n"
        f"## Segunda\n\n{body('Contenido de la segunda sección.')}\n"
    )
    chunks = chunk_document(doc, CONFIG)
    paths = [c.header_path for c in chunks]

    assert ["Doc", "Segunda"] in paths
    # 'Primera' no debe seguir colgando de la ruta de 'Segunda'.
    assert ["Doc", "Primera", "Segunda"] not in paths


def test_deep_nesting_then_shallow_header_pops_stack():
    """Volver a un nivel más alto descarta toda la rama profunda."""
    doc = make_doc(
        f"# A\n\n{body('Sección A.')}\n\n"
        f"## B\n\n{body('Sección B.')}\n\n"
        f"### C\n\n{body('Sección C.')}\n\n"
        f"## D\n\n{body('Sección D.')}\n"
    )
    chunks = chunk_document(doc, CONFIG)
    paths = [c.header_path for c in chunks]

    assert ["A", "D"] in paths
    assert ["A", "B", "C", "D"] not in paths


def test_document_without_headers_still_chunks():
    """Un .txt plano se indexa igual, solo que sin ruta de headers."""
    doc = make_doc(
        "Este es un documento plano sin ningún encabezado markdown. "
        "Igualmente debe poder indexarse porque el motor no puede asumir "
        "que todos los documentos son markdown bien estructurado.",
        name="plano.txt",
    )
    chunks = chunk_document(doc, CONFIG)

    assert len(chunks) == 1
    assert chunks[0].header_path == []
    assert chunks[0].source_file == "plano.txt"


def test_preamble_before_first_header_is_kept():
    """El texto anterior al primer header no se pierde."""
    doc = make_doc(
        "Texto introductorio que aparece antes de cualquier encabezado y que "
        "no debería descartarse porque suele tener contexto importante.\n\n"
        f"# Sección\n\n{body('Contenido de la sección.')}\n"
    )
    chunks = chunk_document(doc, CONFIG)

    assert any("introductorio" in c.text for c in chunks)


def test_oversized_section_is_split_respecting_max_size():
    """Una sección larga se subdivide, y cada parte conserva la ruta."""
    paragraph = "Este es un párrafo con contenido técnico de relleno. " * 12
    section_body = "\n\n".join([paragraph] * 8)
    doc = make_doc(f"# Grande\n\n## Sub\n\n{section_body}\n")

    chunks = chunk_document(doc, CONFIG)

    assert len(chunks) > 1
    sub_chunks = [c for c in chunks if c.header_path == ["Grande", "Sub"]]
    assert len(sub_chunks) > 1, "la sección larga debió partirse en varios chunks"
    for chunk in chunks:
        assert len(chunk.text) <= CONFIG.max_chunk_size


def test_single_huge_paragraph_is_hard_split():
    """Un párrafo gigante sin líneas en blanco igual respeta el tamaño máximo."""
    doc = make_doc("# T\n\n" + ("palabra " * 900))
    chunks = chunk_document(doc, CONFIG)

    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk.text) <= CONFIG.max_chunk_size


def test_tiny_fragments_are_discarded():
    """Headers sin cuerpo no generan chunks de ruido."""
    doc = make_doc("# A\n\n## B\n\n### C\n")
    chunks = chunk_document(doc, CONFIG)

    assert chunks == []


def test_section_with_thin_body_is_discarded():
    """Una sección con cuerpo mínimo no se indexa aunque el título sea largo.

    Estos chunks son activamente dañinos: al repetir el nombre del proyecto en
    el título puntúan alto en cualquier búsqueda que lo mencione y desplazan a
    los fragmentos que sí contienen la respuesta.
    """
    doc = make_doc(
        "# Plataforma de Monitoreo Ambiental Distribuido\n\nVer abajo.\n\n"
        f"## Instalación\n\n{body('Ejecutar docker compose up -d.')}\n"
    )
    chunks = chunk_document(doc, CONFIG)

    paths = [c.header_path for c in chunks]
    assert len(paths) == 1
    assert paths[0][-1] == "Instalación"


def test_chunk_index_is_sequential_per_document():
    doc = make_doc(
        f"# Uno\n\n{body('Primera sección.')}\n\n"
        f"# Dos\n\n{body('Segunda sección.')}\n\n"
        f"# Tres\n\n{body('Tercera sección.')}\n"
    )
    chunks = chunk_document(doc, CONFIG)

    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_citation_format():
    """La cita legible combina archivo y ruta de secciones."""
    doc = make_doc(
        f"# PRD\n\n## Features\n\n{body('Funcionalidades del producto.')}\n",
        name="PRD.md",
    )
    chunks = chunk_document(doc, CONFIG)
    target = next(c for c in chunks if c.header_path == ["PRD", "Features"])

    assert target.citation == "PRD.md § PRD > Features"


def test_citation_without_headers_is_just_filename():
    doc = make_doc(body("Contenido plano sin encabezados."), "n.txt")
    chunks = chunk_document(doc, CONFIG)

    assert chunks[0].citation == "n.txt"


def test_chunk_documents_aggregates_and_keeps_origin():
    docs = [
        make_doc(f"# A\n\n{body('Primer documento.')}\n", "a.md"),
        make_doc(f"# B\n\n{body('Segundo documento.')}\n", "b.md"),
    ]
    chunks = chunk_documents(docs, CONFIG)

    assert {c.source_file for c in chunks} == {"a.md", "b.md"}


def test_char_start_points_into_original_document():
    """El offset permite ubicar el fragmento en el archivo real."""
    doc = make_doc(
        f"# Primera\n\n{body('Contenido inicial.')}\n\n"
        f"# Segunda\n\n{body('Contenido posterior.')}\n"
    )
    chunks = chunk_document(doc, CONFIG)
    second = next(c for c in chunks if c.header_path == ["Segunda"])

    assert second.char_start > 0
    assert doc.content[second.char_start :].startswith("# Segunda")
