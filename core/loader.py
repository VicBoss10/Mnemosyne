"""Lectura de documentos desde el disco.

Recorre una carpeta y devuelve Documents. Deliberadamente simple: no interpreta
el contenido, solo lo lee. La estructura la entiende el chunker.
"""

import logging
from pathlib import Path

from core.models import Document

logger = logging.getLogger(__name__)

# Extensiones que sabemos leer como texto plano. Agregar PDF/DOCX implicaría
# una dependencia de parseo — se deja fuera del MVP a propósito.
SUPPORTED_EXTENSIONS = {".md", ".txt", ".markdown"}


def load_documents(docs_path: Path) -> list[Document]:
    """Lee recursivamente todos los documentos soportados de una carpeta.

    El source_file de cada Document es su ruta relativa a docs_path, no la
    absoluta: es lo que después se le muestra al usuario como cita.

    Raises:
        FileNotFoundError: si la carpeta no existe.
    """
    if not docs_path.exists():
        raise FileNotFoundError(f"La carpeta de documentos no existe: {docs_path}")
    if not docs_path.is_dir():
        raise NotADirectoryError(f"La ruta de documentos no es una carpeta: {docs_path}")

    documents: list[Document] = []
    for path in sorted(docs_path.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue

        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            logger.warning("Se omite %s: no es texto UTF-8 válido", path.name)
            continue

        # Un archivo en blanco no aporta nada y ensuciaría el índice.
        if not content.strip():
            logger.warning("Se omite %s: está vacío", path.name)
            continue

        documents.append(
            Document(source_file=str(path.relative_to(docs_path)), content=content)
        )

    logger.info("Se cargaron %d documentos desde %s", len(documents), docs_path)
    return documents
