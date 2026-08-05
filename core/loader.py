"""Reading documents from disk.

Walks a folder and returns Documents. Deliberately simple: it does not interpret
content, only reads it. Structure is the chunker's concern.
"""

import logging
from pathlib import Path

from core.models import Document

logger = logging.getLogger(__name__)

#: Extensions readable as plain text. PDF and DOCX would pull in a parsing
#: dependency and are intentionally out of scope for the MVP.
SUPPORTED_EXTENSIONS = {".md", ".txt", ".markdown"}


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
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue

        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            logger.warning("Skipping %s: not valid UTF-8 text", path.name)
            continue

        if not content.strip():
            logger.warning("Skipping %s: file is empty", path.name)
            continue

        documents.append(Document(source_file=str(path.relative_to(docs_path)), content=content))

    logger.info("Loaded %d documents from %s", len(documents), docs_path)
    return documents
