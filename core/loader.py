"""Reading documents from disk.

Walks a folder and returns Documents. Deliberately simple: it decides *which*
files to read and delegates *how* to read each format to core.extractors, which
normalizes every format to Markdown. Structure is the chunker's concern.
"""

import logging
from pathlib import Path

from core.extractors import EXTRACTORS, ExtractionError
from core.models import Document

logger = logging.getLogger(__name__)

#: Formats that can be ingested. Driven by the extractor registry so a new
#: format is registered in one place only.
SUPPORTED_EXTENSIONS = frozenset(EXTRACTORS)


def load_documents(docs_path: Path) -> list[Document]:
    """Recursively read every supported document in a folder.

    Each Document's source_file is its path relative to docs_path rather than
    the absolute one, since that is what gets shown to the user as a citation.

    Raises:
        FileNotFoundError: if the folder does not exist.
        NotADirectoryError: if the path is not a folder.
    """
    if not docs_path.exists():
        raise FileNotFoundError(f"Documents folder does not exist: {docs_path}")
    if not docs_path.is_dir():
        raise NotADirectoryError(f"Documents path is not a folder: {docs_path}")

    documents: list[Document] = []
    for path in sorted(docs_path.rglob("*")):
        if not path.is_file():
            continue

        extractor = EXTRACTORS.get(path.suffix.lower())
        if extractor is None:
            continue

        # One unreadable file — a corrupt PDF, a scan with no text layer — must
        # not abort the ingestion of everything else in the folder.
        try:
            content = extractor(path)
        except ExtractionError as exc:
            logger.warning("Skipping %s: %s", path.name, exc)
            continue

        if not content.strip():
            logger.warning("Skipping %s: file is empty", path.name)
            continue

        # as_posix(): la cita debe verse igual sin importar en qué SO se ingirió.
        documents.append(
            Document(source_file=path.relative_to(docs_path).as_posix(), content=content)
        )

    logger.info("Loaded %d documents from %s", len(documents), docs_path)
    return documents
