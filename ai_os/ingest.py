"""Day 6 §1/§3 — ingest: fetch + parse Saleor documentation.

Saleor's docs are *already LLM-ready*: `docs.saleor.io/llms.txt` is an index
of sections, each section has a `llms.txt` listing markdown pages, and the
page **is** the source (no HTML/PDF scraping). So parsing cost here is near
zero; the real decisions are selection and structure:

- keep headings (they become citation metadata), prose, and fenced code
  (Saleor code examples are half the retrieval target);
- drop the breadcrumb blockquote, `<a id>` anchors and stray build markers.
"""
from __future__ import annotations

import re
import urllib.request
from dataclasses import dataclass
from typing import Iterable

DOCS_BASE = "https://docs.saleor.io"
USER_AGENT = "ai-layer/0.3 (ai_os.ingest)"

_LINK_RE = re.compile(r"^\s*[-*]\s+\[([^\]]+)\]\(([^)]+\.(?:md|txt))\)(?::\s*\d+\s+pages)?\s*$",
                      re.M)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_ANCHOR_RE = re.compile(r"^<a\s+id=[\"']?([^\"'>\s]+)")
_CRUFT_RE = re.compile(r"^>\s", re.M)          # breadcrumb blockquote lines
_BREADCRUMB_RE = re.compile(r"^\s*(?:Documentation index:|Source:)\s")
_HTML_RE = re.compile(r"<!--.*?-->", re.S)


class IngestError(Exception):
    pass


@dataclass
class Document:
    """One heading-section of a page: the unit that gets chunked.

    `doc_id` is a stable citation handle like `developer/checkout/overview`
    or `quickstart/api#create-checkout` — the `source` URL plus a heading slug.
    """

    doc_id: str
    source: str
    heading: str
    text: str


def fetch_text(url: str, timeout: float = 15.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode()
    except Exception as e:
        raise IngestError(f"failed to fetch {url}: {e}") from e


def list_links(markdown: str) -> list[tuple[str, str]]:
    """Every `- [label](url)` link of an `llms.txt` index, in order."""
    return [(m.group(1), m.group(2)) for m in _LINK_RE.finditer(markdown)]


def list_sections(markdown: str | None = None) -> list[tuple[str, str]]:
    """Top-level `/llms.txt` → [(section title, `/.../llms.txt`)]."""
    raw = markdown if markdown is not None else fetch_text(DOCS_BASE + "/llms.txt")
    return list_links(raw)


def list_pages(section_url: str) -> list[tuple[str, str]]:
    """Section `llms.txt` → [(page title, `/path/to/page.md`)]."""
    url = section_url if section_url.startswith("http") else DOCS_BASE + section_url
    return list_links(fetch_text(url))


def clean_markdown(markdown: str) -> str:
    """Strip the cruft, keep headings + prose + fences."""
    text = _HTML_RE.sub("", markdown)
    text = _CRUFT_RE.sub("", text)            # `> Documentation index: ...`
    lines: list[str] = []
    for line in text.splitlines():
        if _ANCHOR_RE.match(line):
            continue                            # `<a id="...">` bookmarks
        if _BREADCRUMB_RE.match(line):
            continue                            # leftover index / source lines
        if line.strip() == "":
            if not lines or lines[-1] != "":
                lines.append("")
            continue
        if re.fullmatch(r"\d+", line.strip()):
            continue                            # stray ordered-list markers
        lines.append(line)
    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def _slug(anchor: str | None, heading: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")
    return (anchor or base) or "section"


def documents_from_markdown(markdown: str, source: str) -> list[Document]:
    """Split a cleaned page into `Document`s, one per heading section.

    Retrieval wants units that are *about one thing*; a heading section is
    the natural subject boundary. The document's heading (and the slug
    Saleor's own `<a id>` gives it) becomes the citation handle.
    """
    html = _HTML_RE.sub("", markdown)
    text = _CRUFT_RE.sub("", html)

    pending_anchor: str | None = None
    sections: list[tuple[str, str, list[str]]] = []   # (heading, anchor, body lines)
    cur: tuple[str, str, list[str]] | None = None
    saw_h1 = False

    for line in text.splitlines():
        m = _ANCHOR_RE.match(line.strip())
        if m:
            pending_anchor = m.group(1).strip()
            continue
        if _BREADCRUMB_RE.match(line):
            continue
        if line.strip() == "":
            continue
        if re.fullmatch(r"\d+", line.strip()):
            continue
        h = _HEADING_RE.match(line)
        if h:
            heading = re.sub(r"[*_`]+", "", h.group(2)).strip()
            if h.group(1) == "#":
                saw_h1 = True
            sec = (heading, pending_anchor, [])
            sections.append(sec)
            cur = sec
            pending_anchor = None
            continue
        if cur is None:
            # body before the first heading → attach to the page title
            h1 = text.splitlines()[0].lstrip("#").strip() if saw_h1 else source.split("/")[-1]
            sec = (h1, None, [])
            sections.append(sec)
            cur = sec
        cur[2].append(line)

    docs: list[Document] = []
    base = source.removeprefix("https://docs.saleor.io").removesuffix(".md").strip("/")
    for heading, anchor, body in sections:
        if not body:
            continue
        doc_id = f"{base}#{_slug(anchor, heading)}" if anchor or heading != base else base
        media = " ".join(body).strip()
        if not media:
            continue
        docs.append(Document(doc_id=doc_id, source=source, heading=heading,
                             text=media))
    return docs


def ingest(sections: Iterable[str], base: str = DOCS_BASE) -> list[Document]:
    """Fetch whole sections (default: quickstart + key developer guides).

    `sections` are `llms.txt` paths like `/quickstart/llms.txt`. Every page
    is fetched, cleaned, split into heading-section `Document`s.
    """
    docs: list[Document] = []
    for section in sections:
        url = section if section.startswith("http") else base + section
        for title, page in list_pages(url):
            page_url = page if page.startswith("http") else base + page
            try:
                raw = fetch_text(page_url)
            except IngestError:
                continue
            docs.extend(documents_from_markdown(raw, page_url))
    return docs


if __name__ == "__main__":
    import sys
    sections = sys.argv[1:] or ["/quickstart/llms.txt"]
    ds = ingest(sections)
    print(f"{len(ds)} documents from {len(sections)} section(s)")
    for d in ds[:10]:
        print(f"  {d.doc_id:<50} {len(d.text):>6} chars  {d.heading}")