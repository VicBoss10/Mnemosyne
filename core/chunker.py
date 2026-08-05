"""Partido de documentos en fragmentos indexables.

La estrategia respeta la estructura del markdown en vez de cortar cada N
caracteres a ciegas. Motivo: los headers son la unidad semántica natural del
documento y además son exactamente la información que necesitamos para citar
("PRD.md § 3.4 Correlation Analysis"). Un corte a ciegas destruye las dos cosas.

El algoritmo tiene dos niveles:
  1. Partir por headers markdown, arrastrando la jerarquía de cada sección.
  2. Si una sección es más grande que max_chunk_size, subdividirla por párrafos
     con solapamiento, conservando la misma ruta de headers.

Para documentos sin headers (un .txt plano) el paso 1 no encuentra nada y todo
el contenido cae al paso 2, que funciona igual pero con header_path vacía.
"""

import re

from core.config import ChunkingConfig
from core.models import Chunk, Document

# Header markdown ATX: entre 1 y 6 almohadillas, espacio, y el texto.
# Se exige inicio de línea para no confundir con almohadillas dentro del texto.
HEADER_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)

# Separador de párrafos: una o más líneas en blanco.
PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")


class _Section:
    """Sección de un documento delimitada por headers, uso interno."""

    def __init__(self, header_path: list[str], text: str, char_start: int) -> None:
        self.header_path = header_path
        self.text = text
        self.char_start = char_start


def _split_into_sections(content: str) -> list[_Section]:
    """Parte el documento por headers, arrastrando la jerarquía.

    La ruta de headers se mantiene como una pila: un header de nivel N descarta
    todos los headers de nivel >= N que había antes. Así '### 3.4' bajo '## 3.'
    bajo '# MOVE' produce la ruta completa de los tres.
    """
    matches = list(HEADER_PATTERN.finditer(content))

    # Sin headers: el documento entero es una sola sección sin jerarquía.
    if not matches:
        return [_Section([], content, 0)]

    sections: list[_Section] = []

    # Texto anterior al primer header (preámbulo). Se conserva si tiene algo.
    preamble = content[: matches[0].start()]
    if preamble.strip():
        sections.append(_Section([], preamble, 0))

    # Pila de (nivel, título) que representa la jerarquía vigente.
    stack: list[tuple[int, str]] = []

    for i, match in enumerate(matches):
        level = len(match.group(1))
        title = match.group(2).strip()

        # Un header de este nivel cierra todas las secciones de nivel igual o
        # más profundo que estaban abiertas.
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))

        # El cuerpo va desde el final de este header hasta el próximo header
        # (o el final del documento si es el último).
        body_start = match.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        body = content[body_start:body_end]

        header_path = [t for _, t in stack]

        # El título se incluye en el texto del chunk: es contenido semántico
        # relevante para la búsqueda, no solo metadata.
        section_text = f"{title}\n{body}"
        sections.append(_Section(header_path, section_text, match.start()))

    return sections


def _split_oversized(text: str, config: ChunkingConfig) -> list[tuple[str, int]]:
    """Subdivide un texto largo por párrafos, con solapamiento.

    Devuelve pares (fragmento, offset relativo al inicio del texto recibido).
    Los párrafos se agrupan hasta llenar max_chunk_size; el solapamiento
    arrastra el final del fragmento anterior para no cortar una idea al medio.
    """
    if len(text) <= config.max_chunk_size:
        return [(text, 0)]

    pieces: list[tuple[str, int]] = []
    paragraphs = PARAGRAPH_SPLIT.split(text)

    current = ""
    current_start = 0
    cursor = 0  # posición de lectura dentro de `text`

    for paragraph in paragraphs:
        # Ubicar el párrafo en el texto original para calcular offsets reales.
        found = text.find(paragraph, cursor)
        para_start = found if found != -1 else cursor
        cursor = para_start + len(paragraph)

        if not current:
            current = paragraph
            current_start = para_start
            continue

        # ¿Entra este párrafo en el fragmento actual?
        if len(current) + len(paragraph) + 2 <= config.max_chunk_size:
            current = f"{current}\n\n{paragraph}"
            continue

        # No entra: cerramos el fragmento actual y empezamos uno nuevo.
        pieces.append((current, current_start))

        # El nuevo fragmento arranca con la cola del anterior (solapamiento),
        # para que una idea partida al medio siga siendo recuperable.
        tail = current[-config.overlap :] if config.overlap > 0 else ""
        if tail:
            current = f"{tail}\n\n{paragraph}"
            current_start = para_start - len(tail)
        else:
            current = paragraph
            current_start = para_start

    if current:
        pieces.append((current, current_start))

    # Caso patológico: un solo párrafo gigante sin líneas en blanco (una tabla
    # enorme, un bloque de código). Ahí sí no queda otra que cortar por tamaño.
    result: list[tuple[str, int]] = []
    for piece, offset in pieces:
        if len(piece) <= config.max_chunk_size:
            result.append((piece, offset))
            continue
        step = max(1, config.max_chunk_size - config.overlap)
        for start in range(0, len(piece), step):
            result.append((piece[start : start + config.max_chunk_size], offset + start))

    return result


def chunk_document(document: Document, config: ChunkingConfig) -> list[Chunk]:
    """Convierte un Document en la lista de Chunks que se van a indexar."""
    chunks: list[Chunk] = []
    index = 0

    for section in _split_into_sections(document.content):
        for piece, relative_offset in _split_oversized(section.text, config):
            text = piece.strip()

            # Descartar fragmentos triviales: headers sin cuerpo, líneas sueltas.
            # Indexarlos solo agregaría ruido a la búsqueda.
            if len(text) < config.min_chunk_size:
                continue

            chunks.append(
                Chunk(
                    text=text,
                    source_file=document.source_file,
                    header_path=section.header_path,
                    chunk_index=index,
                    char_start=section.char_start + relative_offset,
                )
            )
            index += 1

    return chunks


def chunk_documents(documents: list[Document], config: ChunkingConfig) -> list[Chunk]:
    """Aplica chunk_document a una colección de documentos."""
    return [chunk for document in documents for chunk in chunk_document(document, config)]
