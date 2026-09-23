"""Day 10 §5 — embed a Saleor source tree into a persisted `RagStore`.

```
$ python tools/build_code_store.py --corpus corpus_code/saleor --out data/code_store.json
```

Mirrors Day 6's `build_rag_store` wholesale — per-chunk ledger, resumable
saves, dimension-lock on the persisted store — with exactly one input swap:
`ingest_code()` (AST symbol extraction) instead of `documents_from_markdown`.

Two things this builder makes real that were theory before:

- **Interface-vs-body embedding.** Each symbol embeds its *interface* (name +
  signature + docstring — what retrieval should match); the *body* rides as
  plain `text` for the context builder (the cited evidence). The vector stays
  thin; the match stays honest.
- **Symbols, not chunks.** The ledger line count is the symbol count (tens of
  thousands for Saleor) — build cost explodes, run cost does not. Resume is why
  that is affordable: a throttled multi-hour build never costs the corpus twice.

The second store coexists with `data/store.json` behind the same `RagStore`
shape and dimension lock; the doc store is never invalidated.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_os.config import build_gateway_from_env  # noqa: E402
from ai_os.ingest_code import ingest_code, interface_text  # noqa: E402
from ai_os.rag import Chunk, RagStore  # noqa: E402
from tools.build_rag_store import (  # noqa: E402
    BATCH_SLEEP_S,
    embed_with_quota,
)


def build_code_store(gateway, corpus: Path, batch: int,
                     resume_from: str | None = None, save_every: int = 8,
                     batch_chars: int = 0,
                     modules: list[str] | None = None) -> RagStore:
    """Extract symbols from `corpus`, then embed one interface text per symbol.

    `modules` (when given) keeps only symbols whose module path starts with one
    of the prefixes — the code analogue of `build_rag_store --pages`, for a
    bounded/curated build (e.g. the fixture-relevant apps) before committing to
    the full tree.
    """
    symbols = ingest_code(corpus)
    if modules:
        prefixes = tuple(modules)
        symbols = [s for s in symbols if s.module.startswith(prefixes)]
    if not symbols:
        raise SystemExit(f"no python symbols under {corpus}"
                         + (f" matching {modules}" if modules else ""))

    store: RagStore | None = None
    done: set[str] = set()
    if resume_from:
        try:
            store = RagStore.load(resume_from)
            done = {c.chunk_id for c in store.chunks}
            print(f"resume: {len(done)} symbols already in {resume_from}")
        except FileNotFoundError:
            print(f"resume: {resume_from} missing -> building fresh")
        except Exception as e:
            print(f"resume: {resume_from} unreadable ({e}) -> building fresh")

    avg_len = sum(len(interface_text(s)) for s in symbols) / len(symbols)
    per = (max(1, min(batch, int(batch_chars // max(avg_len, 1.0))))
           if batch_chars else batch)
    n_slabs = (len(symbols) + per - 1) // per
    print(f"plan: {len(symbols)} symbols needing {n_slabs} requests "
          f"(~{per} symbols, ~{per * avg_len:.0f} chars each)", flush=True)

    n_done = 0
    n_batches = 0
    for start in range(0, len(symbols), per):
        slab = [s for s in symbols[start:start + per] if s.doc_id not in done]
        n_batches += 1
        if not slab:
            continue
        embeddings = embed_with_quota(
            gateway, [interface_text(s) for s in slab],
            between_s=BATCH_SLEEP_S if batch_chars else 0.0)
        # A code store is one embedding space. If the gateway fell through to a
        # *different* embed provider/model mid-build (Cloudflare's free tier caps
        # -> OpenRouter nemotron, dim 768 -> 2048), stop and PRESERVE the partial
        # store. Mixing spaces is a wrong-answer bug; *wiping* thousands of
        # already-paid-for symbols to start over is how a bounded free-tier build
        # becomes uncompletable. Pin `LLM_PROVIDERS` to one embed provider and
        # resume — the ledger skips what is already embedded.
        if store is not None and store.dimension and embeddings:
            live_dim = len(embeddings[0].vector)
            if live_dim != store.dimension:
                if resume_from:
                    store.save(resume_from)
                raise SystemExit(
                    f"embedding space changed mid-build: store is dim "
                    f"{store.dimension} ({store.embedding_model}) but the gateway "
                    f"returned dim {live_dim} ({embeddings[0].model}). Pin "
                    f"LLM_PROVIDERS to a single embedding provider (e.g. "
                    f"LLM_PROVIDERS=cloudflare:) and resume; the partial store is "
                    + (f"preserved at {resume_from}." if resume_from else "in memory."))
        for sym, emb in zip(slab, embeddings):
            if store is None:
                store = RagStore()
            if not store.embedding_model:
                store.embedding_model = emb.model
            store.add(Chunk(
                chunk_id=sym.doc_id, doc_id=sym.doc_id, source=sym.module,
                heading=sym.symbol, position=sym.first_line, text=sym.body,
                vector=emb.vector, symbol=sym.symbol, kind=sym.kind,
                signature=sym.signature, docstring=sym.docstring,
                imports=sym.imports, dependencies=sym.dependencies))
            done.add(sym.doc_id)
            n_done += 1
        if resume_from and n_batches % save_every == 0:
            store.save(resume_from)
            print(f"  checkpoint {n_batches:>4} of {n_slabs}: "
                  f"{len(store)} symbols -> {resume_from}", flush=True)

    if store is None:
        store = RagStore()
    if resume_from:
        store.save(resume_from)
    print(f"embedded {n_done} symbols this run ({len(store)} total) from "
          f"{corpus} via {store.embedding_model}, dim {store.dimension}")
    return store


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", default=str(ROOT / "corpus_code" / "saleor"),
                    help="root of the Saleor source tree (a clone of "
                         "github.com/saleor/saleor)")
    ap.add_argument("--out", default=str(ROOT / "data" / "code_store.json"))
    ap.add_argument("--batch", type=int, default=100,
                    help="symbols per embed request")
    ap.add_argument("--batch-chars", type=int, default=0,
                    help="cap each request's input chars when set and pace "
                         "batches (free tiers throttled on input volume)")
    ap.add_argument("--modules", nargs="*", default=None,
                    help="only embed symbols whose module starts with one of "
                         "these prefixes (curated/bounded build)")
    args = ap.parse_args()

    gateway = build_gateway_from_env()
    if not any(getattr(p, "embed", None) for p in gateway.providers):
        raise SystemExit(
            "no provider in the chain supports embedding — add an OpenAI/"
            "Cloudflare provider (see .env)")
    store = build_code_store(gateway, Path(args.corpus), args.batch,
                             resume_from=args.out, batch_chars=args.batch_chars,
                             modules=args.modules)
    store.save(args.out)
    print(f"wrote {args.out}: {len(store)} symbols, "
          f"{store.embedding_model} / dim {store.dimension}")


if __name__ == "__main__":
    main()
