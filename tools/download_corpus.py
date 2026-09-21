"""Day 6 §3 — download the real Saleor corpus into `corpus/` as markdown.

Mirrors the prioritized sections the day plan selected (Quickstart 4 +
Developer 215, plus Api Usage 14 ≈ 233 pages) from `docs.saleor.io/llms.txt`
indexes, preserving each page's URL-derived path so `RagStore` citations and
`build_rag_store.py` stay truthful (`corpus/developer/checkout.md` →
`https://docs.saleor.io/developer/checkout.md`).

The nav/breadcrumb cruft is stripped via `ai_os.ingest.clean_markdown`; the
page **is** the source format, so no HTML/PDF scraping is involved.

    $ python tools/download_corpus.py                 # quickstart+developer+usage
    $ python tools/download_corpus.py --sections /overview/llms.txt
    $ python tools/download_corpus.py --force          # re-fetch everything
"""
from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_os.ingest import (  # noqa: E402
    DOCS_BASE, IngestError, clean_markdown, fetch_text, list_pages,
)

DEFAULT_SECTIONS = ["/quickstart/llms.txt", "/api-usage/llms.txt",
                    "/developer/llms.txt"]


def page_path(page_url: str) -> Path:
    """`https://docs.saleor.io/developer/checkout.md` → `corpus/developer/checkout.md`."""
    rel = page_url.removeprefix(DOCS_BASE).lstrip("/")
    if not rel.endswith(".md"):
        rel += ".md"
    return ROOT / "corpus" / rel


def download_section(section_url: str, force: bool) -> tuple[int, list[str]]:
    url = section_url if section_url.startswith("http") else DOCS_BASE + section_url
    pages = list_pages(url)
    saved, skipped = 0, []
    for _title, page in pages:
        dest = page_path(page)
        if dest.exists() and not force:
            skipped.append(str(dest.relative_to(ROOT)))
            continue
        page_url = page if page.startswith("http") else DOCS_BASE + page
        try:
            raw = fetch_text(page_url)
        except IngestError as e:
            print(f"  ! {page_url}: {e}", file=sys.stderr)
            continue
        clean = clean_markdown(raw)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(clean, encoding="utf-8")
        saved += 1
        print(f"  ✓ {dest.relative_to(ROOT)}  ({len(clean):,} chars)")
    return saved, skipped


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sections", nargs="*", default=DEFAULT_SECTIONS,
                    help="llms.txt section paths to mirror (default: "
                         "quickstart + api-usage + developer)")
    ap.add_argument("--force", action="store_true",
                    help="re-download even if the file already exists")
    args = ap.parse_args()

    total_saved = total_pages = 0
    for sec in args.sections:
        print(f"== {sec}")
        saved, skipped = download_section(sec, args.force)
        total_saved += saved
        total_pages += saved + len(skipped)
        print(f"   {saved} saved, {len(skipped)} already present")
    print(f"\ndone: {total_saved} page(s) fetched this run, "
          f"{total_pages} page(s) now under corpus/")


if __name__ == "__main__":
    main()