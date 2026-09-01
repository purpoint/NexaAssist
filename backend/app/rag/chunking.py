"""Splitting documents into retrievable spans.

Paragraph-first, because a paragraph is usually the smallest unit that still
answers a question on its own. Oversized paragraphs are split on whitespace so
one runaway block cannot produce a chunk too large to embed usefully.
"""

DEFAULT_CHUNK_SIZE = 800
MIN_CHUNK_SIZE = 50


def chunk_text(text: str, *, chunk_size: int = DEFAULT_CHUNK_SIZE) -> list[str]:
    """Split ``text`` into ordered, non-empty chunks."""
    if chunk_size < MIN_CHUNK_SIZE:
        raise ValueError(f"chunk_size must be at least {MIN_CHUNK_SIZE}")

    chunks: list[str] = []
    for paragraph in (p.strip() for p in text.split("\n\n")):
        if not paragraph:
            continue
        if len(paragraph) <= chunk_size:
            chunks.append(paragraph)
        else:
            chunks.extend(_split_long(paragraph, chunk_size))

    # A document of pure whitespace still has to yield something storable, and
    # the caller has already been told the text is non-blank.
    if not chunks and text.strip():
        chunks.append(text.strip())
    return chunks


def _split_long(paragraph: str, chunk_size: int) -> list[str]:
    parts: list[str] = []
    current = ""
    for word in paragraph.split():
        candidate = f"{current} {word}".strip()
        if len(candidate) > chunk_size and current:
            parts.append(current)
            current = word
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts
