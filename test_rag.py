"""Day 6 §5/§6 — deterministic RAG mechanics (hand vectors, no network).

Covers the pieces that Day 6 made *new*: the metadata-carrying store and
its persist/lock, retrieval ordering, the budget floor, and the refusal
path. Generation is a fake `Completion`; nothing here needs a model.

    $ python test_rag.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import ai_os.search as search

sys.path.insert(0, ".")
from ai_os.contract import Completion, Embedding, Usage  # noqa: E402
from ai_os.ledger import Ledger  # noqa: E402
from ai_os.rag import (  # noqa: E402
    Chunk, RagStore, DimensionMismatch, StoreVersionError,
    build_context, citation, k_from_budget, run_rag,
)

FAILS = 0


def check(label: str, cond: bool, detail: str = ""):
    global FAILS
    if not cond:
        FAILS += 1
        # gate_prod leg 3 is `pytest -q`: a failed check must fail the run
        # there, while as a script this keeps printing and reports at the end.
        if os.environ.get("PYTEST_CURRENT_TEST"):
            raise AssertionError(label + (f"  ({detail})" if detail else ""))
    print(f"[{'OK  ' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


class FakeGateway:
    """Scripted embed+chat so nothing leaves the process."""

    def __init__(self, vectors: dict[str, list[float]], answer: str = "answer text"):
        self.vectors = vectors
        self.answer = answer
        self.ledger = Ledger()
        self.last_attempts = 1
        self.last_latency_ms = 1.0
        self.last_provider = "fake"

    def embed(self, inputs: list[str]) -> list[Embedding]:
        out = []
        for t in inputs:
            key = next((k for k in self.vectors if k.lower() in t.lower()), "")
            v = self.vectors.get(key, [0.0, 0.0, 0.0])
            emb = Embedding(text=t, vector=list(v), model="text-embedding-3-small",
                            usage=Usage(input_tokens=len(t.split()), output_tokens=0,
                                        total_tokens=len(t.split())))
            out.append(emb)
            self.ledger.record(provider="fake", model=emb.model,
                               input_tokens=emb.usage.input_tokens, ok=True)
        return out

    def chat(self, messages, **kw) -> Completion:
        return Completion(text=self.answer, model="gpt-4o-mini",
                          finish_reason="stop",
                          usage=Usage(10, 10, 20))


def make_store(tmp: Path, vectors=None) -> RagStore:
    vectors = vectors or {
        "checkout": [1.0, 0.2, 0.0],
        "order": [0.8, 0.0, 0.2],
        "webhook": [0.0, 0.0, 1.0],
    }
    store = RagStore(embedding_model="text-embedding-3-small")
    chunks = [
        Chunk("c-checkout-0", "developer/checkout/overview", "https://docs.saleor.io/developer/checkout/overview.md",
              "Checkout Lifecycle", 0, "A checkout is created and then completed into an order.", vectors["checkout"]),
        Chunk("c-order-0", "developer/order/overview", "https://docs.saleor.io/developer/order/overview.md",
              "Order Overview", 0, "Orders flow through fulfillment and reservation.", vectors["order"]),
        Chunk("c-webhook-0", "developer/extending/webhooks", "https://docs.saleor.io/developer/extending/webhooks/overview.md",
              "Webhooks Overview", 0, "Webhooks notify external systems of events.", vectors["webhook"]),
    ]
    for c in chunks:
        store.add(c)
    store.save(tmp / "store.json")
    return store


def test_store():
    store = RagStore(embedding_model="text-embedding-3-small")
    store.add(Chunk("a0", "d", "s", "h", 0, "text", [3.0, 4.0]))
    check("normalize-on-write (unit length)",
          abs(search.magnitude(store.chunks[0].vector) - 1.0) < 1e-9,
          str(store.chunks[0].vector))
    check("dimension locked on first add", store.dimension == 2)
    try:
        store.add(Chunk("b0", "d", "s", "h", 1, "t", [1.0, 2.0, 3.0]))
        check("mismatched chunk dim rejected", False)
    except DimensionMismatch:
        check("mismatched chunk dim rejected", True)


def test_roundtrip(tmp):
    store = make_store(tmp)
    loaded = RagStore.load(tmp / "store.json")
    check("load preserves chunks", len(loaded) == len(store) == 3)
    check("load preserves metadata",
          loaded.chunks[0].doc_id == "developer/checkout/overview"
          and loaded.chunks[0].heading == "Checkout Lifecycle"
          and loaded.chunks[0].position == 0)
    check("load locks model+dim",
          loaded.embedding_model == "text-embedding-3-small" and loaded.dimension == 3)
    bad = {**{"version": 99, "chunks": []}}
    (tmp / "bad.json").write_text(str(bad).replace("'", '"'))
    try:
        RagStore.load(tmp / "bad.json")
        check("bad version rejected", False)
    except StoreVersionError:
        check("bad version rejected", True)


def test_retrieve():
    with tempfile.TemporaryDirectory() as d:
        store = make_store(Path(d))
        q = [1.0, 0.2, 0.0]
        results = store.retrieve(q, k=2)
        check("top-1 is the checkout chunk", results[0][1].doc_id == "developer/checkout/overview",
              results[0][1].doc_id)
        check("scores descending", results[0][0] >= results[1][0]
              and all(results[i][0] >= results[i + 1][0] for i in range(len(results) - 1)))
        check("k clamps to n", len(store.retrieve(q, k=100)) == 3)
        check("empty store → []", RagStore().retrieve([1.0, 0.0, 0.0]) == [])
        q4 = [1.0, 0.2, 0.0, 0.0]
        try:
            store.retrieve(q4, k=1)
            check("query dim mismatch rejected", False)
        except DimensionMismatch:
            check("query dim mismatch rejected", True)


def test_context_budget():
    with tempfile.TemporaryDirectory() as d:
        store = make_store(Path(d))
        results = store.retrieve([1.0, 0.2, 0.0], k=3)
        context, used, kept = build_context(results, budget_chars=len(results[0][1].text) + 1)
        check("context respects budget", used <= len(results[0][1].text) + 1
              and len(context) == used)
        check("only the first chunk fits (rank keeps priority)", len(kept) == 1)
        tiny = results[0][1].text[:10]
        context, used, kept = build_context(results, budget_chars=5)
        check("budget below one chunk → empty-safe", context == "" and kept == [])
        # k from budget: budget/avg-ish
        k = k_from_budget(len(results[0][1].text) + 1, store.avg_chunk_len(), len(store))
        check("k_from_budget is 1 for a single-chunk budget", k == 1, f"k={k}")
        check("k_from_budget clamps to n",
              k_from_budget(10 ** 9, store.avg_chunk_len(), len(store)) == len(store))
        check("k_from_budget(0) → 0", k_from_budget(0, 10, 3) == 0)


def test_run_rag():
    with tempfile.TemporaryDirectory() as d:
        store = make_store(Path(d))
        gw = FakeGateway({"checkout": [1.0, 0.2, 0.0]}, answer="A checkout is created and completed.")
        res = run_rag(gw, store, "how do I create a checkout?", budget_chars=1000)
        check("run_rag grounded answer + citations",
              res["answer"] == gw.answer and len(res["citations"]) == 3)
        check("citation carries metadata",
              res["citations"][0]["doc_id"] == "developer/checkout/overview"
              and isinstance(res["citations"][0]["score"], float))
        check("retrieval vs generation costs separated",
              res["retrieval"]["model"] == "text-embedding-3-small"
              and res["generation"]["model"] == "gpt-4o-mini"
              and res["retrieval"]["cost_usd"] >= 0 and res["generation"]["cost_usd"] >= 0)

        # out-of-corpus: nearest neighbors retrieved anyway (mechanics)
        gw2 = FakeGateway({"zzz": [0.0, 0.0, 0.0]}, answer="x")
        res2 = run_rag(gw2, store, "gibberish zzz")
        check("out-of-corpus still returns candidates", res2["retrieval"]["candidates"] > 0)

        # dimension mismatch at the query boundary is a hard error
        gw3 = FakeGateway({"checkout": [1.0, 0.2, 0.0, 0.0]}, answer="x")
        try:
            run_rag(gw3, store, "checkout")
            check("mismatched query dim → error", False)
        except DimensionMismatch:
            check("mismatched query dim → error", True)


def test_refusal():
    with tempfile.TemporaryDirectory() as d:
        empty = RagStore()
        gw = FakeGateway({"a": [1.0, 0.0, 0.0]}, answer="should not be called")
        res = run_rag(gw, empty, "anything", budget_chars=1000)
        check("empty corpus → honest refusal, no chat call",
              res["refused"] is True and res["answer"] == "not covered by the documentation"
              and res["generation"] is None and res["citations"] == [])
        # populated store but budget too small for one chunk → refusal
        store = make_store(Path(d))
        res = run_rag(gw, store, "checkout", budget_chars=1)
        check("budget smaller than one chunk → refusal by code",
              res["refused"] is True and res["generation"] is None)


def test_lost_in_middle_mechanics():
    # 8 chunks: retrieval rank + budget order preserved for the 2nd vs 7th
    store = RagStore()
    for i in range(8):
        store.add(Chunk(f"c{i}", f"doc{i}", "s", f"heading {i}", i,
                        f"chunk number {i} with checkout terms",
                        [1.0, 0.1 * i, 0.0, 0.0]))
    q = [1.0, 0.0, 0.0, 0.0]
    results = store.retrieve(q, k=8)
    check("8 chunks retrieved for 8-query", len(results) == 8)
    context, used, kept = build_context(results, budget_chars=10 ** 6)
    check("context preserves retrieval rank order",
          [c.chunk_id for _, c in kept] == [c.chunk_id for _, c in results])
    second = results[1][1]
    check("second-ranked chunk survives the budget", second.text in context)
    check("retrieve is deterministic", store.retrieve(q, k=8) == results)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as _tmp:
        test_store()
        test_roundtrip(Path(_tmp))
        test_retrieve()
        test_context_budget()
        test_run_rag()
        test_refusal()
        test_lost_in_middle_mechanics()
    print("\n" + ("ALL RAG CHECKS PASSED" if not FAILS else f"{FAILS} CHECK(S) FAILED"))
    sys.exit(1 if FAILS else 0)