"""Splitting documents into indexable fragments.

The strategy follows Markdown structure instead of cutting blindly every N
characters, because headers are both the document's natural semantic unit and
exactly the information needed to cite a source ("manual.md § 3.4 Requirements").
A blind cut destroys both.

The algorithm works in two levels:
  1. Split on Markdown headers, carrying each section's hierarchy.
  2. If a section exceeds max_chunk_size, subdivide it by paragraphs with
     overlap, keeping the same header path.

For documents without headers (a plain .txt) step 1 finds nothing and all the
content falls through to step 2, which works the same but with an empty
header_path.
"""

import re

from core.config import ChunkingConfig
from core.models import Chunk, Document

#: ATX Markdown header: one to six hashes, a space, then the text. Anchored to
#: the start of a line so hashes inside prose are not mistaken for headers.
HEADER_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)

#: Paragraph separator: one or more blank lines.
PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")

#: Minimum body length, excluding the title, for a section to be worth indexing.
#: Independent of min_chunk_size, which measures the assembled chunk: this one
#: measures content alone, to drop sections that contribute only their heading.
MIN_BODY_LENGTH = 80


class _Section:
    """A header-delimited region of a document. Internal use."""

    def __init__(self, header_path: list[str], text: str, char_start: int) -> None:
        self.header_path = header_path
        self.text = text
        self.char_start = char_start


def _split_into_sections(content: str) -> list[_Section]:
    """Split the document on headers, carrying the hierarchy.

    The header path is kept as a stack: a level-N header pops every header of
    level >= N before it. That way '### 3.4' under '## 3.' under '# Manual'
    yields the full path of all three.

    Sections whose body is nearly empty are dropped. This is not just hygiene:
    such fragments carry only their title — which usually repeats the project
    name — so they score highly on any query mentioning it and crowd out the
    fragments that actually hold the answer.
    """
    matches = list(HEADER_PATTERN.finditer(content))

    # No headers: the whole document is a single section with no hierarchy.
    if not matches:
        return [_Section([], content, 0)]

    sections: list[_Section] = []

    preamble = content[: matches[0].start()]
    if preamble.strip():
        sections.append(_Section([], preamble, 0))

    # Stack of (level, title) representing the hierarchy currently open.
    stack: list[tuple[int, str]] = []

    for i, match in enumerate(matches):
        level = len(match.group(1))
        title = match.group(2).strip()

        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))

        # The body runs from the end of this header to the next one, or to the
        # end of the document for the last header.
        body_start = match.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        body = content[body_start:body_end]

        header_path = [t for _, t in stack]

        if len(body.strip()) < MIN_BODY_LENGTH:
            continue

        # The title is part of the chunk text, not just metadata: it is semantic
        # content that the search should match against.
        section_text = f"{title}\n{body}"
        sections.append(_Section(header_path, section_text, match.start()))

    return sections


def _split_oversized(text: str, config: ChunkingConfig) -> list[tuple[str, int]]:
    """Subdivide a long text by paragraphs, with overlap.

    Returns (fragment, offset) pairs, where the offset is relative to the start
    of the given text. Paragraphs are grouped until max_chunk_size is reached;
    the overlap carries the tail of the previous fragment so an idea split
    across the boundary stays retrievable.
    """
    if len(text) <= config.max_chunk_size:
        return [(text, 0)]

    pieces: list[tuple[str, int]] = []
    paragraphs = PARAGRAPH_SPLIT.split(text)

    current = ""
    current_start = 0
    cursor = 0

    for paragraph in paragraphs:
        # Locate the paragraph in the original text to compute real offsets.
        found = text.find(paragraph, cursor)
        para_start = found if found != -1 else cursor
        cursor = para_start + len(paragraph)

        if not current:
            current = paragraph
            current_start = para_start
            continue

        if len(current) + len(paragraph) + 2 <= config.max_chunk_size:
            current = f"{current}\n\n{paragraph}"
            continue

        pieces.append((current, current_start))

        tail = current[-config.overlap :] if config.overlap > 0 else ""
        if tail:
            current = f"{tail}\n\n{paragraph}"
            current_start = para_start - len(tail)
        else:
            current = paragraph
            current_start = para_start

    if current:
        pieces.append((current, current_start))

    # A single paragraph longer than the limit: common in prose extracted from
    # PDFs, where a whole page can arrive as one block. It still has to be cut,
    # but on a sentence boundary rather than at an arbitrary character.
    result: list[tuple[str, int]] = []
    for piece, offset in pieces:
        if len(piece) <= config.max_chunk_size:
            result.append((piece, offset))
            continue
        result.extend((frag, offset + start) for frag, start in _split_by_sentence(piece, config))

    return result


#: End of sentence: terminal punctuation followed by whitespace. The lookbehind
#: excludes a single capital before the period, which is an initial ("R. Enríquez")
#: rather than a sentence ending.
SENTENCE_END = re.compile(r"(?<![A-ZÁÉÍÓÚÑ])[.!?]\s+")


def _split_by_sentence(text: str, config: ChunkingConfig) -> list[tuple[str, int]]:
    """Cut an oversized paragraph on sentence boundaries, with overlap.

    Cutting at a fixed character offset splits words in half, and a fragment
    starting mid-word embeds poorly — the truncated token carries no meaning and
    dilutes the vector. Sentences are the smallest unit that keeps a fragment
    readable and citable.
    """
    boundaries = [match.end() for match in SENTENCE_END.finditer(text)]
    # No sentence structure at all (a table, a long code block): fall back to
    # word boundaries, which at least never split a token.
    if not boundaries:
        boundaries = [match.end() for match in re.finditer(r"\s+", text)]
    boundaries.append(len(text))

    fragments: list[tuple[str, int]] = []
    start = 0

    while start < len(text):
        limit = start + config.max_chunk_size
        if limit >= len(text):
            fragments.append((text[start:], start))
            break

        # The furthest boundary that still fits; if none does, a single sentence
        # exceeds the limit and it gets cut at the last word before it.
        cut = max((b for b in boundaries if start < b <= limit), default=limit)
        fragments.append((text[start:cut], start))

        # Step back by the overlap, landing on the earliest boundary inside the
        # overlap window so the next fragment also starts at a sentence start.
        # It must stay strictly between the current start and the cut, or the
        # loop would either stall or skip text.
        target = cut - config.overlap
        candidates = [b for b in boundaries if start < b < cut and b >= target]
        start = min(candidates) if candidates else cut

    return fragments


def chunk_document(document: Document, config: ChunkingConfig) -> list[Chunk]:
    """Turn a Document into the list of Chunks to be indexed."""
    chunks: list[Chunk] = []
    index = 0

    for section in _split_into_sections(document.content):
        for piece, relative_offset in _split_oversized(section.text, config):
            text = piece.strip()

            # Trivial fragments (bodiless headers, stray lines) would only add
            # noise to the search.
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
    """Apply chunk_document across a collection of documents."""
    return [chunk for document in documents for chunk in chunk_document(document, config)]
