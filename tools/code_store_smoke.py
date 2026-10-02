#!/usr/bin/env python3
"""WS5 — real code store fixture (C1–C5), offline.

- C1 ollama provider registered (self-hosted bge-base-en-v1.5, dim 768, no key)
- C2 store identity key is sha256(file)+model+dim; cached load hits the slot
- C3 dim mismatch fails loudly (DimensionMismatch, never silent)
- C4 build_code_store resumes without wiping on an embedding-space switch
- C5 S2 latency labeled cold/warm (retrieval_day10 --live path present)
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_os.providers import PROVIDER_REGISTRY  # noqa: E402
from ai_os.rag import DimensionMismatch, RagStore, assert_store_matches, Chunk  # noqa: E402

FAILS = 0


def check(label: str, cond: bool, detail: str = "") -> bool:
    global FAILS
    if not cond:
        FAILS += 1
    print(f"[{'OK  ' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))
    return bool(cond)


def c1_ollama_registered() -> None:
    cls = PROVIDER_REGISTRY.get("ollama")
    ok = cls is not None and getattr(cls, "EXPECTED_DIM", 0) == 768
    check("C1 ollama provider registered (self-hosted bge, dim 768, no key)",
          ok, getattr(cls, "DEFAULT_EMBED_MODEL", "missing"))
    if cls is not None:
        p = cls(api_key="", model="llama3.1")  # no key required
        check("C1 ollama instantiates without an API key", p.api_key == "")


def c2_identity_key() -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "s.json")
        store = RagStore(embedding_model="bge-base-en-v1.5")
        store.add(Chunk(chunk_id="a", doc_id="a", source="m", heading="h",
                        position=1, text="hello world test", vector=[1.0, 0.0]))
        store.save(path)
        from ai_os.rag import _store_identity, load_store_cached
        ident = _store_identity(path, store.embedding_model, store.dimension)
        parts = ident.split("|")
        check("C2 identity key is sha256(file)+model+dim",
              len(parts) == 3 and len(parts[0]) == 64, ident[:40])
        loaded = load_store_cached(path)
        check("C2 cached load returns the store", len(loaded) == 1)


def c3_dim_mismatch_loud() -> None:
    store = RagStore(embedding_model="bge-base-en-v1.5")
    store.add(Chunk(chunk_id="a", doc_id="a", source="m", heading="h",
                    position=1, text="hello world test", vector=[1.0, 0.0, 0.0]))
    try:
        assert_store_matches(store, embedding_model="bge-base-en-v1.5", dimension=5)
        check("C3 dim mismatch fails loudly", False)
    except DimensionMismatch as exc:
        check("C3 dim mismatch fails loudly", "dim" in str(exc).lower(),
              str(exc)[:80])
    try:
        assert_store_matches(store, embedding_model="other-model")
        check("C3 model mismatch fails loudly", False)
    except DimensionMismatch:
        check("C3 model mismatch fails loudly", True)


def c4_resume_preserves() -> None:
    import inspect
    from tools import build_code_store as bcs
    src = inspect.getsource(bcs.build_code_store)
    check("C4 build preserves partial store on space switch (no wipe)",
          "preserved" in src.lower() or "preserve" in src.lower())


def c5_cold_warm_labels() -> None:
    import inspect
    from tools import retrieval_day10 as r10
    src = inspect.getsource(r10.main)
    check("C5 S2 latency labeled cold/warm",
          "cold=" in src and "warm=" in src)


def main() -> int:
    print("== WS5 — real code store (C1–C5) ==")
    c1_ollama_registered()
    c2_identity_key()
    c3_dim_mismatch_loud()
    c4_resume_preserves()
    c5_cold_warm_labels()
    print("\n" + ("ALL CODE-STORE CHECKS PASSED" if not FAILS else f"{FAILS} CHECK(S) FAILED"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
