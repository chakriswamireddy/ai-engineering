"""Day 6 §4 — embed a local corpus into a persisted `RagStore` (store.json).

```
$ python tools/mock_gateway.py &            # deterministic stub vectors
$ OPENAI_API_KEY=sk-test ANTHROPIC_API_KEY=x \
  OPENAI_BASE_URL=http://127.0.0.1:8700/v1 ANTHROPIC_BASE_URL=http://127.0.0.1:8701/v1 \
  python tools/build_rag_store.py
```

For the *real* corpus: point the gateway at real providers and an API key
(run `python -m ai_os.ingest` first to download sections into `corpus/`),
then re-run this — the store's `embedding_model`/`dimension` lock will make
the swap explicit at query time instead of silent.

Embedding runs through the Day 4 gateway — retries, ledger, cost — and the
zero-vector normalize-on-write fix from Day 5 is in `RagStore.add`.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_os.chunk import chunk_text  # noqa: E402
from ai_os.config import build_gateway_from_env  # noqa: E402
from ai_os.contract import RateLimitError  # noqa: E402
from ai_os.ingest import DOCS_BASE, documents_from_markdown  # noqa: E402
from ai_os.rag import Chunk, RagStore  # noqa: E402

BATCH_SLEEP_S = 40.0         # used when --batch-chars is set (free tier refills
                            # ~2k chars/min; pacing a ~1.4k-char request ~40s
                            # keeps a Gemini free key under its char window).
                            # Offline builds pass --batch N and never sleep.
MAX_BATCH_RETRIES = 60       # a long window reset needs room to wait + retry
RETRY_GRACE_S = 5.0          # extra sleep past the server's Retry-After
BATCH_CHARS = 1400           # default char cap per request when --batch-chars
                            # is passed (Gemini free tier: ~2k chars/min window)


def embed_corpus(gateway, path: Path, batch: int,
                 resume_from: str | None = None, save_every: int = 4,
                 pages: list[str] | None = None,
                 batch_chars: int = 0) -> RagStore:
    """Chunk every markdown page under `path`, then embed *each chunk*.

    Chunking happens **before** embedding so every chunk carries its own
    vector (Day 6 §2: a document is the average of several meanings; the
    retrieval unit is the paragraph-wide chunk, not the page). Metadata
    (source/heading/position) rides along as the citation layer.

    `pages` (when given) restricts the build to those relative paths — useful
    for curating a subset when the provider's free tier throttles by input
    size (Gemini: ~2k chars/min), or for targeted rebuilds; the *same* command
    without `pages` embeds everything, and resume makes either run resumable.

    Resumable: when `resume_from` points at an existing store whose
    `embedding_model` + `dimension` match the live embedder, already-embedded
    chunks are skipped and the store carries on (a throttled/failed run never
    costs the whole corpus again). A store built by a *different* embedder
    (e.g. the old dim-8 stub) is wiped so vectors never mix spaces.
    """
    files = sorted(p for p in path.rglob("*.md") if "__pycache__" not in str(p))
    if pages:
        rel = lambda f: f.relative_to(path).as_posix()
        files = [f for f in files if rel(f) in pages]
        missing = [p for p in pages if not (path / p).exists()]
        if missing:
            raise SystemExit(f"--pages not found under {path}: {missing}")
    if not files:
        raise SystemExit(f"no markdown files under {path}")

    pieces: list[tuple[str, str, str, str, int, str]] = []  # (chunk_id, doc_id, source, heading, position, text)
    for file in files:
        rel = file.relative_to(path.parent).as_posix().removeprefix("corpus/")
        source = f"{DOCS_BASE}/{rel}"
        for d in documents_from_markdown(file.read_text(encoding="utf-8"), source):
            for position, piece in enumerate(chunk_text(d.text)):
                pieces.append((f"{d.doc_id}#{position}", d.doc_id, d.source,
                               d.heading, position, piece))

    store: RagStore | None = None
    done: set[str] = set()
    if resume_from:
        try:
            store = RagStore.load(resume_from)
            done = {c.chunk_id for c in store.chunks}
            print(f"resume: {len(done)} chunks already in {resume_from}")
        except FileNotFoundError:
            print(f"resume: {resume_from} missing -> building fresh")
        except Exception as e:
            print(f"resume: {resume_from} unreadable ({e}) -> building fresh")

    avg_len = (sum(len(p[-1]) for p in pieces) / len(pieces)) if pieces else 0.0
    per = (max(1, min(batch, int(batch_chars // max(avg_len, 1.0))))
           if batch_chars else batch)
    n_slabs = (len(pieces) + per - 1) // per
    print(f"plan: {len(pieces)} chunks needing {n_slabs} requests "
          f"(~{per} chunks, ~{per * avg_len:.0f} chars each)", flush=True)
    n_chunks = 0
    n_batches = 0
    for start in range(0, len(pieces), per):
        slab = [p for p in pieces[start:start + per] if p[0] not in done]
        n_batches += 1
        if not slab:
            continue
        embeddings = embed_with_quota(
            gateway, [p[5] for p in slab],
            between_s=BATCH_SLEEP_S if batch_chars else 0.0)
        for (chunk_id, doc_id, source, heading, position, text), emb in zip(slab, embeddings):
            if store is None:
                store = RagStore()
            elif store.dimension and len(emb.vector) != store.dimension:
                print(f"embedder changed space (store dim {store.dimension} != "
                      f"live dim {len(emb.vector)}) -> wiping and rebuilding")
                store = RagStore()
                done = set()
            if not store.embedding_model:
                store.embedding_model = emb.model
            store.add(Chunk(chunk_id=chunk_id, doc_id=doc_id, source=source,
                            heading=heading, position=position, text=text,
                            vector=emb.vector))
            done.add(chunk_id)
            n_chunks += 1
        if resume_from and n_batches % save_every == 0:
            store.save(resume_from)
            print(f"  checkpoint {n_batches:>4} of {n_slabs}: "
                  f"{len(store)} chunks -> {resume_from}", flush=True)

    if store is None:
        store = RagStore()
    if resume_from:
        store.save(resume_from)
    print(f"embedded {n_chunks} chunks this run ({len(store)} total) from "
          f"{len(files)} files via {store.embedding_model}, dim {store.dimension}")
    return store


def embed_with_quota(gateway, texts: list[str], between_s: float = 0.0):
    """Embed one batch, tolerating free-tier per-minute throttling.

    Gemini's free tier throttles with 429+`Retry-After` *and* by dropping
    connections; the gateway's own 3-attempt/30s cap is too tight for a long
    quota window. So this honors `Retry-After`, waits out transient 5xx /
    connection resets, and only lets *fatal* errors (401/400/…) through —
    bounded, so a genuinely dead key still fails loudly.

    `between_s` is slept after any non-final attempt so batches pace
    themselves under the free tier's per-window char budget. Offline/stub
    builds pass 0 and never sleep.
    """
    import time

    from ai_os.contract import ProviderError
    from urllib import error as urlerror

    for attempt in range(1, MAX_BATCH_RETRIES + 1):
        try:
            return gateway.embed(texts)
        except RateLimitError as e:
            retry_after = float(getattr(e, "retry_after", 0) or 30.0)
            wait = min(max(retry_after, 70.0) + RETRY_GRACE_S, 150.0)
            if attempt == MAX_BATCH_RETRIES:
                raise
            print(f"  quota rate-limit (attempt {attempt}/{MAX_BATCH_RETRIES}): "
                  f"429 retry-after={retry_after:.0f}s -> cooling {wait:.0f}s",
                  flush=True)
            time.sleep(wait)
        except ProviderError as e:
            if not getattr(e, "retryable", False) or attempt == MAX_BATCH_RETRIES:
                raise
            print(f"  quota transient {type(e).__name__} (attempt "
                  f"{attempt}/{MAX_BATCH_RETRIES}) -> sleeping "
                  f"{min(attempt, 5) * 10}s", flush=True)
            time.sleep(min(attempt, 5) * 10)
        except urlerror.URLError as e:
            if attempt == MAX_BATCH_RETRIES:
                raise
            print(f"  quota connection reset (attempt {attempt}/{MAX_BATCH_RETRIES}) "
                  f"-> sleeping {min(attempt, 5) * 10}s", flush=True)
            time.sleep(min(attempt, 5) * 10)
        finally:
            if attempt < MAX_BATCH_RETRIES:
                time.sleep(between_s)


def build(path: Path, batch: int, out: str | None = None,
          pages: list[str] | None = None, batch_chars: int = 0) -> RagStore:
    gateway = build_gateway_from_env()
    if not any(getattr(p, "embed", None) for p in gateway.providers):
        raise SystemExit(
            "no provider in the chain supports embedding — add an OpenAI provider "
            "(OPENAI_API_KEY / OPENAI_BASE_URL) that can reach POST /v1/embeddings")
    return embed_corpus(gateway, path, batch, resume_from=out,
                        pages=pages, batch_chars=batch_chars)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", default=str(ROOT / "corpus"),
                    help="directory tree of markdown pages")
    ap.add_argument("--out", default=str(ROOT / "data" / "store.json"))
    ap.add_argument("--batch", type=int, default=100,
                    help="texts per embed request (Gemini caps a batch at 100)")
    ap.add_argument("--pages", nargs="*",
                    help="only embed these relative paths under --corpus "
                         "(curated subsets / targeted rebuilds)")
    ap.add_argument("--batch-chars", type=int, default=0,
                    help="cap each request\u2019s input chars when set and pace "
                         "batches by BATCH_SLEEP_S — for free tiers throttled "
                         "on input volume (Gemini: ~2k chars/min); leave 0 "
                         "for offline/stub builds")
    args = ap.parse_args()
    store = build(Path(args.corpus), args.batch, out=args.out, pages=args.pages,
                  batch_chars=args.batch_chars)
    store.save(args.out)
    print(f"wrote {args.out}: {len(store)} chunks, "
          f"{store.embedding_model} / dim {store.dimension}")


if __name__ == "__main__":
    main()