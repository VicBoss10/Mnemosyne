"""Extracting text from binary document formats.

Every extractor returns **Markdown**, not plain text. That is deliberate: the
chunker splits on Markdown headers and builds its citations from them, so a
format that carries structure (DOCX heading styles, a PDF's page boundaries)
keeps that structure by translating it into headers instead of flattening it
into an undifferentiated blob.

All parsing is local — pypdf and python-docx read the file and nothing else.
"""

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

#: Word's built-in heading styles map onto Markdown levels. The English and
#: Spanish names are both checked because the style name follows the locale of
#: the Word install that created the document, not the one that reads it.
_HEADING_STYLE_PATTERN = re.compile(r"^(?:heading|título|titulo)\s+([1-6])$", re.IGNORECASE)

#: Collapses three or more newlines, which the extractors produce whenever an
#: empty paragraph or page sits between two blocks of content.
_EXCESS_BLANK_LINES = re.compile(r"\n{3,}")


class ExtractionError(Exception):
    """A document could not be read: corrupt, encrypted, or not the format its
    extension claims. Ingestion skips the file rather than aborting."""


def _normalize(blocks: list[str]) -> str:
    """Join blocks with blank lines between them, without runs of empty ones.

    The blank line matters beyond aesthetics: it is the paragraph separator the
    chunker uses to subdivide oversized sections.
    """
    text = "\n\n".join(block.strip() for block in blocks if block.strip())
    return _EXCESS_BLANK_LINES.sub("\n\n", text).strip()


def extract_pdf(path: Path) -> str:
    """Extract a PDF as Markdown, one '## Page N' header per page.

    A PDF has no reliable notion of headings — visually a title is just larger
    text, and font size does not survive extraction dependably. Pages, on the
    other hand, are unambiguous, and they are what a reader needs in order to
    verify a citation ("manual.pdf § Page 12"), so pages become the sections.

    Scanned PDFs with no text layer yield nothing; this raises rather than
    indexing an empty document, since OCR is out of scope.
    """
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependencia declarada
        raise ExtractionError("pypdf no está instalado: no se pueden leer PDFs") from exc

    try:
        reader = PdfReader(path)
        # An empty password unlocks PDFs that are merely permission-locked; a
        # genuinely password-protected one cannot be read.
        if reader.is_encrypted and not reader.decrypt(""):
            raise ExtractionError("el PDF está protegido con contraseña")
        pages = list(reader.pages)
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError(f"no se pudo abrir el PDF: {exc}") from exc

    blocks: list[str] = []
    for number, page in enumerate(pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            logger.warning("Página %d de %s ilegible: %s", number, path.name, exc)
            continue

        text = _clean_pdf_text(text)
        if text:
            blocks.append(f"## Página {number}\n\n{text}")

    if not blocks:
        raise ExtractionError(
            "el PDF no contiene texto extraíble (¿es un escaneo? se necesitaría OCR)"
        )

    return _normalize(blocks)


def _clean_pdf_text(text: str) -> str:
    """Undo the two artifacts PDF extraction reliably introduces.

    Text in a PDF is positioned glyph by glyph, so the extractor emits a line
    break wherever the layout had one — mid-sentence included. Left as is, a
    single paragraph arrives as a dozen short lines, and the chunker reads each
    blank-line gap as a paragraph boundary that isn't there.
    """
    # Words split across a line by hyphenation: "aerodiná-\nmica" → "aerodinámica".
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)

    lines = [line.strip() for line in text.split("\n")]

    merged: list[str] = []
    for line in lines:
        # An empty line is a real paragraph break; keep it as a separator.
        if not line:
            merged.append("")
            continue

        # A line continues the previous one when the previous did not end in
        # sentence-final punctuation and this one does not start a new block
        # (a bullet or a numbered item).
        continues = merged and merged[-1] and not re.search(r"[.:;!?]$", merged[-1])
        if continues and not re.match(r"^[-•*\d]", line):
            merged[-1] = f"{merged[-1]} {line}"
            continue

        merged.append(line)

    return _normalize(merged)


def extract_docx(path: Path) -> str:
    """Extract a .docx as Markdown, mapping Word heading styles to '#' levels.

    Word documents carry real structure: a paragraph styled 'Heading 2' is a
    section title, so it becomes '##' and the chunker gets the same hierarchy it
    would from a Markdown file. Tables are rendered as Markdown tables so their
    content stays searchable instead of being dropped.

    The legacy binary .doc format is not supported — it is a different format
    entirely, unreadable by python-docx.
    """
    try:
        import docx
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as exc:  # pragma: no cover - dependencia declarada
        raise ExtractionError("python-docx no está instalado: no se pueden leer .docx") from exc

    try:
        document = docx.Document(str(path))
    except Exception as exc:
        raise ExtractionError(f"no se pudo abrir el .docx: {exc}") from exc

    blocks: list[str] = []

    # Iterating the body's XML children keeps paragraphs and tables in the order
    # they appear; document.paragraphs and document.tables lose that interleaving.
    body = document.element.body
    for child in body.iterchildren():
        tag = child.tag.split("}")[-1]

        if tag == "p":
            block = _docx_paragraph(Paragraph(child, document))
            if block:
                blocks.append(block)
        elif tag == "tbl":
            block = _docx_table(Table(child, document))
            if block:
                blocks.append(block)

    if not blocks:
        raise ExtractionError("el .docx no contiene texto")

    return _normalize(blocks)


def _docx_paragraph(paragraph) -> str:
    """Render one Word paragraph, as a header if its style says so."""
    text = paragraph.text.strip()
    if not text:
        return ""

    style_name = (paragraph.style.name if paragraph.style is not None else "") or ""

    match = _HEADING_STYLE_PATTERN.match(style_name.strip())
    if match:
        return f"{'#' * int(match.group(1))} {text}"

    # 'Title' is Word's document-level heading, above Heading 1.
    if style_name.strip().lower() in {"title", "título", "titulo"}:
        return f"# {text}"

    if style_name.startswith("List"):
        return f"- {text}"

    return text


def _docx_table(table) -> str:
    """Render a Word table as a Markdown table.

    The first row is treated as the header, which is the overwhelmingly common
    convention and the only one recoverable without inspecting cell formatting.
    """
    rows = [[cell.text.strip().replace("\n", " ") for cell in row.cells] for row in table.rows]
    rows = [row for row in rows if any(row)]
    if not rows:
        return ""

    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]

    # A pipe inside a cell would break the table's column structure.
    def render(row: list[str]) -> str:
        return "| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |"

    lines = [render(rows[0]), "| " + " | ".join("---" for _ in range(width)) + " |"]
    lines.extend(render(row) for row in rows[1:])
    return "\n".join(lines)


def extract_text(path: Path) -> str:
    """Read a plain text or Markdown file.

    Raises:
        ExtractionError: if the file is not valid UTF-8.
    """
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ExtractionError("no es texto UTF-8 válido") from exc


#: Extension → extractor. Adding a format means adding an entry here and a
#: function above; the loader needs no changes.
EXTRACTORS = {
    ".md": extract_text,
    ".markdown": extract_text,
    ".txt": extract_text,
    ".pdf": extract_pdf,
    ".docx": extract_docx,
}
