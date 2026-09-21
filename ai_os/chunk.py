"""Day 6 §2/§3 — the Chunker.

A whole document is a poor retrieval unit (one embedding = the average of
all its topics) and a bare sentence is worse (it loses the context that
disambiguates it). The unit is a paragraph-ish window: big enough to hold
its own context, small enough to be about one thing.

Design decisions (verified in test_chunk.py):

- **Overlap duplicates the seam.** A fact that straddles a chunk boundary
  belongs to *both* neighbors; a sliding window (`start = end - overlap`)
  re-serves the seam instead of losing it.
- **Tail-grace never cuts mid-word.** The cut walks forward to the next
  whitespace, but capped at `size + grace` so one unbroken token (a URL, a
  base64 blob, minified code) can't blow a chunk out to thousands of chars.
- **`overlap >= size` is rejected eagerly.** A window that steps backward
  loops forever — a real bug, not a style choice.
"""
from __future__ import annotations

DEFAULT_SIZE = 200
DEFAULT_OVERLAP = 40
DEFAULT_GRACE = 100


def chunk_text(text: str, size: int = DEFAULT_SIZE,
               overlap: int = DEFAULT_OVERLAP,
               grace: int = DEFAULT_GRACE) -> list[str]:
    """Split `text` into fixed-size windows with `overlap` chars of seam.

    Returns a list of substrings of the *original* `text` — no whitespace
    rewriting — so the overlap check `chunk0[-overlap:] in chunk1` tests the
    same surface that will actually be embedded.
    """
    if overlap >= size:
        raise ValueError("overlap must be < size")
    if grace < 0:
        raise ValueError("grace must be >= 0")
    if not text:
        return []
    if len(text) <= size:
        return [text]

    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = start + size
        limit = min(n, start + size + grace)
        # tail-grace: extend through the current word to its end, but never
        # past `limit` — a pathological token must not overshoot the window.
        while end < limit and not text[end - 1].isspace():
            end += 1
        chunks.append(text[start:end])
        if end >= n:
            break
        start = end - overlap
    return chunks