"""Day 7 — deterministic tests for the RAG debug harness (`MECH` column).

Everything here runs without an API key or network: a vocab-vector stub in
process (same contract shape as the Day 4 mock), a hand-built store, and the
pure functions of `gate` / `trace` / `fixtures` / `oracle`. What is *not*
asserted is semantically judged — those cells are `PENDING` by design and
become Day 20/21's evaluation set, not this file's job.

    $ python test_day7.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TOOLS = ROOT / "tools"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(TOOLS))
import tools.rag_debug as _rd  # noqa: E402  (StubGateway, STUB_DOCS, vocab)

from ai_os.contract import Completion, Embedding, Usage  # noqa: E402
from ai_os.fixtures import (  # noqa: E402
    FIXTURES_PATH,
    check_fixture,
    load_questions,
    run_fixtures,
)
from ai_os.gate import score_gap, should_refuse  # noqa: E402
from ai_os.ledger import Ledger  # noqa: E402
from ai_os.oracle import chunks_for_doc_ids, oracle_test  # noqa: E402
from ai_os.rag import Chunk, RagStore, build_context, k_from_budget  # noqa: E402
from ai_os.trace import (  # noqa: E402
    VERDICT_CONTEXT,
    VERDICT_CONTEXT_QUALITY,
    VERDICT_CORPUS_GAP,
    VERDICT_GENERATION,
    VERDICT_MISSING_RETRIEVAL,
    VERDICT_OK,
    VERDICT_PENDING,
    classify_trace,
    duplicate_count,
    duplicate_ratio,
    expected_stats,
    retrieve_trace,
    run_trace,
)

FAILS = 0


def check(label: str, cond: bool, detail: str = ""):
    global FAILS
    if not cond:
        FAILS += 1
    print(f"[{'OK  ' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


class FakeGateway(_rd.StubGateway):
    """Stub gateway with a scriptable chat reply (for refusal/oracle tests)."""

    def __init__(self, chat: str = "not covered by the documentation",
                 vocab=None) -> None:
        super().__init__(vocab)
        self.chat_text = chat

    def chat(self, messages, **kw) -> Completion:
        return Completion(text=self.chat_text, model="gpt-4o-mini",
                          finish_reason="stop", usage=Usage(12, 24, 36))


def build_store(gw) -> RagStore:
    store = RagStore()
    for doc_id, heading, text in _rd.STUB_DOCS:
        vec = gw.embed([text])[0].vector
        if not store.embedding_model:
            store.embedding_model = gw.last_embeddings[0].model
        store.add(Chunk(chunk_id=f"{doc_id}#0", doc_id=doc_id,
                        source=f"https://docs.saleor.io/{doc_id.split('#')[0]}.md",
                        heading=heading, position=0, text=text, vector=vec))
    return store


# ---------------------------------------------------------------- gate.py

def gate_tests():
    print("\n== gate.py — should_refuse arithmetic (MECH)")
    check("empty retrieval refuses", should_refuse([], 0.5))
    check("top1 < tau refuses", should_refuse([(0.40, None)], 0.5))
    check("top1 >= tau passes", not should_refuse([(0.60, None)], 0.5))
    check("boundary == tau passes", not should_refuse([(0.5, None)], 0.5))
    try:
        should_refuse([(0.9, None)], -0.1)
        check("negative tau rejected", False)
    except ValueError:
        check("negative tau rejected", True)
    check("score_gap top1-top3", abs(score_gap([(0.9, None), (0.8, None), (0.7, None)]) - 0.2) < 1e-9)
    check("score_gap empty -> None", score_gap([]) is None)


def gate_routes() -> None:
    pass  # placeholder removed below


# -------------------------------------------------------------- trace.py

def dup_and_stats_tests():
    print("\n== trace.py — duplicate + rank mechanics (MECH)")
    check("duplicate_ratio two identical", abs(duplicate_ratio(["a", "a"]) - 0.5) < 1e-9)
    check("duplicate_ratio distinct", duplicate_ratio(["a", "b"]) == 0.0)
    check("duplicate_ratio empty/single", duplicate_ratio(["a"]) == 0.0)
    check("duplicate_count", duplicate_count(["a", "a", "b", "a"]) == 2)

    st = expected_stats(["d3", "d1"], ["d1", "d2", "d3"])
    check("expected_stats ranks", st["best_rank"] == [3, 1] and st["in_list"] == [True, True])
    check("expected_stats recall/MRR", st["recall_at_k"] == 1.0 and st["mrr"] == 1.0)
    st2 = expected_stats(["d5"], ["d1", "d2"])
    check("expected_stats absent", st2["in_list"] == [False] and st2["recall_at_k"] == 0.0
          and st2["mrr"] == 0.0)


def classify_tests():
    print("\n== trace.py — the day-7 decision tree (verdicts by experiment)")
    base = _trace(expected=[], in_ret=[], in_ctx=[])
    t = _trace(expected=["e1"], in_ret=[False], in_ctx=[False])
    check("expected ∉ top-K -> missing retrieval",
          classify_trace(t) == VERDICT_MISSING_RETRIEVAL)
    t = _trace(expected=["e1"], in_ret=[True], in_ctx=[False])
    check("expected ∉ context -> context construction",
          classify_trace(t) == VERDICT_CONTEXT)
    t = _trace(expected=["e1"], in_ret=[True], in_ctx=[True], refused=True)
    check("expected in prompt yet refused -> generation",
          classify_trace(t) == VERDICT_GENERATION)
    t = _trace(expected=[], refused=True)
    check("E refused -> corpus gap (correct behaviour)",
          classify_trace(t) == VERDICT_CORPUS_GAP)
    t = _trace(expected=[], refused=False, answer="invented", gate=False)
    check("E answered anyway -> generation",
          classify_trace(t) == VERDICT_GENERATION)
    t = _trace(expected=[], answer=None, refused=None, gate=True)
    check("E gate refuses with no chat -> corpus gap",
          classify_trace(t) == VERDICT_CORPUS_GAP)
    t = _trace(expected=["e1"], in_ret=[True], in_ctx=[True],
               answer="grounded", refused=False)
    check("no oracle -> pending", classify_trace(t) == VERDICT_PENDING)
    t = _trace(expected=["e1"], in_ret=[True], in_ctx=[True], answer="x")
    check("oracle wrong -> generation",
          classify_trace(t, {"ok": False}) == VERDICT_GENERATION)
    t = _trace(expected=["e1"], in_ret=[True], in_ctx=[True], answer="x",
               dupe=0.40)
    check("oracle right + dupes crowd -> context quality",
          classify_trace(t, {"ok": True}) == VERDICT_CONTEXT_QUALITY)
    t = _trace(expected=["e1"], in_ret=[True], in_ctx=[True], answer="x")
    check("oracle right, no dupes -> ok",
          classify_trace(t, {"ok": True}) == VERDICT_OK)


def _trace(**kw) -> dict:
    return {
        "expected": kw.get("expected", []),
        "expected_in_retrieved": kw.get("in_ret", []),
        "expected_in_context": kw.get("in_ctx", []),
        "refused": kw.get("refused", False),
        "answer": kw.get("answer"),
        "gate_refused": kw.get("gate", False),
        "kept_duplicate_ratio": kw.get("dupe", 0.0),
    }


def integration_tests():
    print("\n== trace.py — retrieve_trace / run_trace end-to-end (stub)")
    gw = FakeGateway(chat="the answer")
    store = build_store(gw)

    tr = retrieve_trace(gw, store, "How do I complete a checkout?",
                        expected=["developer/checkout/api-guide#completing-checkout"])
    check("A-query ranks the checkout doc top-1",
          tr["top1"] is not None and tr["top1"] > 0.5)
    check("expected in retrieved",
          tr["expected_in_retrieved"] == [True] and tr["expected_best_rank"] == [1])

    tr = run_trace(gw, store, "How do I complete a checkout?",
                   expected=["developer/checkout/api-guide#completing-checkout"],
                   with_generation=True)
    check("run_trace answers", bool(tr["answer"]))
    cited = {c["doc_id"] for c in tr["citations"]}
    retrieved = {e["doc_id"] for e in tr["retrieved"]}
    check("run_trace: cited ⊆ retrieved (exact)", cited == retrieved)
    check("run_trace: chart fields filled", tr["generation"]["model"] == "gpt-4o-mini")

    trr = run_trace(gw, store, "How do I complete a checkout?",
                    with_generation=False)
    check("retrieval-only: no answer, honest PENDING",
          trr["answer"] is None and trr["verdict"] == VERDICT_PENDING)

    # budget floor evicts the rank-2 evidence -> CONTEXT, not retrieval miss.
    # explicit k=3 (so the rank exists) + a small budget (so the floor drops
    # everything after the winner) is the exact shape of the day-6 rank-order
    # floor: "fits" says nothing about which chunk is the answer.
    store3 = RagStore()
    for i, text in enumerate(["checkout cart", "checkout complete order",
                              "webhook sync"]):
        v = gw.embed([text])[0].vector
        store3.add(Chunk(f"d{i}", f"d{i}", "s", f"h{i}", i, text * 60, v))
    small = run_trace(gw, store3, "checkout",
                      expected=["d1"], k=3, budget_chars=120,
                      with_generation=False)
    check("budget drops rank-2 -> context failure",
          small["expected_in_retrieved"] == [True]
          and small["expected_in_context"] == [False]
          and small["context_chars"] < small["context_budget"])
    check("verdict routes to context (not retrieval)",
          classify_trace(small) == VERDICT_CONTEXT)


def empty_context_refusal():
    print("\n== trace.py — empty context refuses *by code*")
    gw = FakeGateway(chat="I will answer anyway (bogus)")
    empty = RagStore()
    tr = run_trace(gw, empty, "anything", with_generation=True)
    check("empty store: refused, no generation",
          tr["refused"] is True and tr["generation"] is None
          and tr["answer"] == "not covered by the documentation")
    check("empty store: verdict = corpus gap",
          classify_trace(tr) == VERDICT_CORPUS_GAP)


# ------------------------------------------------------------- fixtures.py

def fixtures_tests():
    print("\n== fixtures.py — the model-free regression set (MECH)")
    data = load_questions()
    check("questions.yaml has 9 pre-registered questions", len(data["questions"]) == 9)
    check("A1 control registered", "A1" in data["by_id"])
    check("must_refuse / expected are exclusive",
          all(not (q["must_refuse"] and q["expected"]) for q in data["questions"]))
    check("families A–E covered",
          {q["family"][0] for q in data["questions"]} == {"A", "B", "C", "D", "E"})

    f = {"id": "T", "expected": ["a", "b"], "must_not": [], "must_refuse": False}
    ok, _ = check_fixture(f, [(0.9, "b"), (0.8, "a")])
    check("expected ⊆ retrieved -> pass", ok)
    ok, reason = check_fixture(f, [(0.9, "b"), (0.8, "c")])
    check("expected ∉ retrieved -> fail + names the missing", not ok and "a" in reason)
    f2 = {"id": "T", "expected": ["a"], "must_not": ["z"], "must_refuse": False}
    ok, reason = check_fixture(f2, [(0.9, "a"), (0.8, "z")])
    check("must_not present -> fail", not ok and "z" in reason)
    f3 = {"id": "E", "expected": [], "must_not": [], "must_refuse": True, "tau": 0.5}
    ok, _ = check_fixture(f3, [(0.9, "a")])
    check("must_refuse with strong top1 -> fails gate", not ok)
    ok, _ = check_fixture(f3, [(0.2, "a")])
    check("must_refuse with weak top1 -> gate refuses", ok)
    e2 = check_fixture({"id": "E2", "expected": [], "must_not": [],
                        "must_refuse": True, "tau": None}, [(0.4, "a")])
    check("must_refuse without calibrated tau -> loud fail",
          not e2[0] and "tau" in e2[1])

    print("-- fixtures against the stub corpus (house the run pattern) --")
    gw = FakeGateway()
    store = build_store(gw)
    rows = run_fixtures(gw, store, tau=0.05)
    check("run_fixtures returns 9 rows", len(rows) == 9)
    a1 = next(r for r in rows if r["id"] == "A1")
    check("A1 fixture green on stub corpus", a1["ok"])
    e1 = next(r for r in rows if r["id"] == "E1")
    check("E1 gate refuses on stub corpus (top1 < tau)",
          "must refuse" in e1["reason"] or (e1["ok"]))


# --------------------------------------------------------------- oracle.py

def oracle_tests():
    print("\n== oracle.py — the isolation test (inject perfect evidence)")
    gw = FakeGateway(chat="The checkout becomes an order via checkoutComplete.")
    store = build_store(gw)
    o = oracle_test(
        gw, store, "How does a checkout become an order?",
        ["developer/checkout/lifecycle#completing-checkout",
         "developer/order/overview#lifecycle"])
    check("oracle: answer produced", bool(o["answer"] and o["answer"].strip()))
    cited = {c["doc_id"] for c in o["citations"]}
    check("oracle: citations are exactly the injected docs",
          cited == {"developer/checkout/lifecycle#completing-checkout",
                    "developer/order/overview#lifecycle"})
    check("oracle: cited ⊆ expected", o["cited_subset_expected"])
    check("oracle: context carries the injected evidence",
          len(o["context"]) > 100)

    try:
        oracle_test(gw, store, "q", ["does/not/exist"])
        check("oracle: missing expected doc -> loud error", False)
    except ValueError:
        check("oracle: missing expected doc -> loud error", True)

    try:
        oracle_test(gw, store, "q",
                    ["developer/order/overview#lifecycle"], budget_chars=10)
        check("oracle: budget below one chunk -> loud error", False)
    except ValueError:
        check("oracle: budget below one chunk -> loud error", True)


# ------------------------------------------------------------------- main

def main() -> None:
    gate_tests()
    dup_and_stats_tests()
    classify_tests()
    integration_tests()
    empty_context_refusal()
    fixtures_tests()
    oracle_tests()

    print()
    if not FAILS:
        print("ALL DAY 7 (MECH) CHECKS PASSED")
        raise SystemExit(0)
    print(f"{FAILS} CHECK(S) FAILED")
    raise SystemExit(1)


if __name__ == "__main__":
    main()