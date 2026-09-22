"""Day 8 — deterministic tests for the improved retriever (`MECH` half).

Day 8's claim, per the notes: hybrid scoring fixes the lexical gap, query
rewriting + metadata filtering fix ambiguity (Family C), multi-query
decomposition fixes multi-document questions (Family B), and the improved
retriever keeps the answerable fixtures green that the baseline fails. All of
that is *arithmetic* — a hand vector, a token set, an IDF sum — so nothing
here needs a model or a network:

    $ python test_day8.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from ai_os.contract import Completion, Embedding, Usage  # noqa: E402
from ai_os.fixtures import check_fixture, load_questions  # noqa: E402
from ai_os.gate import should_refuse  # noqa: E402
from ai_os.lexicon import Lexicon, tokenize  # noqa: E402
from ai_os.rag import Chunk, RagStore  # noqa: E402
from ai_os.retrieval import (  # noqa: E402
    Retriever,
    SECTION_PREFIXES,
    decompose,
    fuse_score,
    rewrite,
    rule_section,
)
import tools.rag_debug as _rd  # noqa: E402

FAILS = 0


def check(label: str, cond: bool, detail: str = ""):
    global FAILS
    if not cond:
        FAILS += 1
    print(f"[{'OK  ' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


def near(a: float, b: float, eps: float = 1e-6) -> bool:
    return abs(a - b) < eps


class ScriptedGateway:
    """No-network gateway: maps embedded strings to hand vectors by substring.

    Deterministic by construction — the "semantics" are exactly what the test
    scripts, so a test can force cosine to fail where lexical succeeds and
    vice versa.
    """

    def __init__(self, vectors: dict[str, list[float]], embed_dim: int = 2):
        self.vectors = vectors
        self.embed_dim = embed_dim
        self.ledger = None

    def embed(self, inputs: list[str]) -> list[Embedding]:
        out = []
        for t in inputs:
            vec = None
            for token, v in self.vectors.items():
                if token.lower() in t.lower():
                    vec = v
                    break
            if vec is None or len(vec) != self.embed_dim:
                vec = [0.0] * self.embed_dim
            out.append(Embedding(text=t, vector=list(vec),
                                 model="text-embedding-3-small",
                                 usage=Usage(len(t.split()), 0, len(t.split()))))
        return out

    def chat(self, messages, **kw) -> Completion:  # unused: Day 8 is retrieval-only
        return Completion(text="x", model="gpt-4o-mini", finish_reason="stop",
                          usage=Usage(1, 1, 2))


def make_store(chunks: list[tuple[str, str, str, list[float]]]) -> RagStore:
    """chunks: (doc_id, heading, text, vector)."""
    store = RagStore()
    for i, (doc_id, heading, text, vec) in enumerate(chunks):
        store.add(Chunk(chunk_id=f"{doc_id}#0", doc_id=doc_id,
                        source=f"https://docs.saleor.io/{doc_id.split('#')[0]}.md",
                        heading=heading, position=0, text=text, vector=vec))
    return store


def _norm(v):
    m = math.sqrt(sum(x * x for x in v))
    return [x / m for x in v] if m else v


# ---------------------------------------------------------------- lexicon

def lexicon_tests():
    print("\n== lexicon.py — tokenize + IDF scorer (MECH)")
    check("tokenize drops stops + punct",
          tokenize("How do I cancel an ORDER?") == ["cancel", "order"],
          str(tokenize("How do I cancel an ORDER?")))
    check("tokenize keeps digits", "v1" in tokenize("checkout v1 api"))
    check("stopwords never score", "do" not in tokenize("do i know"))

    # 5 docs: "checkout" in 4, "webhook" in 1 → webhook is the rarer token.
    store = make_store([
        (f"d{i}", "h", "checkout api", [1.0, float(i)]) for i in range(4)
    ] + [("d4", "h", "webhook sync", [1.0, 0.0])])
    lex = Lexicon(store.chunks)
    check("idf: a rare token wins over a common one",
          lex.idf["webhook"] > lex.idf["checkout"],
          f"checkout={lex.idf['checkout']:.3f} webhook={lex.idf['webhook']:.3f}")
    check("score: set-membership (repetition saturates)",
          lex.score(["checkout"])[0] == lex.score(["checkout", "checkout"])[0])
    check("score: unmatched query token contributes 0",
          sum(lex.score(["xylophone"])) == 0.0)
    norm = lex.normalized(["webhook"])
    check("normalized: max == 1.0", max(norm) == 1.0)
    check("normalized: band is [0, 1]",
          all(0.0 <= v <= 1.0 for v in lex.normalized(["checkout", "webhook"])))
    check("all-zero query → all zeros (refusal path)",
          all(v == 0.0 for v in lex.normalized(["xylophone"])))


# --------------------------------------------------------------- rules

def rewrite_tests():
    print("\n== retrieval.py — rewrite / decompose / rule_section (MECH)")
    r = rewrite("why is my cart stuck?")
    check("rewrite: symptom → mechanism words",
          "shipping method" in r and "checkout" in r, r)
    r2 = rewrite("how do I cancel an order?")
    check("rewrite: inserts corpus verb orderCancel", "orderCancel" in r2, r2)
    check("rewrite: no rule → pass-through",
          rewrite("what is a checkout in Saleor?") == "what is a checkout in Saleor?")
    check("rewrite: case-insensitive match",
          rewrite("CANCEL AN ORDER please")
          == "cancel an order with orderCancel after payment")

    parts = decompose("How does a checkout become an order, and what changes after?")
    check("decompose B1 into 2 intents",
          len(parts) == 2 and "changes after" in parts[1], str(parts))
    check("decompose caps at 3",
          len(decompose("a and b and c and d and e and f")) == 3)
    check("decompose single intent unchanged",
          decompose("what is a checkout?") == ["what is a checkout?"])

    check("rule_section: checkout query → checkout",
          rule_section("How do I complete a checkout?") == "checkout")
    check("rule_section: apps query → apps",
          rule_section("How do apps work in Saleor?") == "apps")
    check("rule_section: ambiguous (checkout+order) → None",
          rule_section("checkout to order") is None)
    check("rule_section: out-of-corpus → None",
          rule_section("How much does Saleor Cloud cost?") is None)
    check("checkout prefixes cover both live folders",
          "developer/checkout" in SECTION_PREFIXES["checkout"]
          and "quickstart/api" in SECTION_PREFIXES["checkout"])

    check("fuse_score: alpha=0.5 averages", near(fuse_score(0.5, 1.0, 0.0), 0.5))
    check("fuse_score: alpha=0 → pure lexical", near(fuse_score(0.0, 0.2, 0.8), 0.8))
    check("fuse_score: alpha=1 → pure semantic", near(fuse_score(1.0, 0.3, 0.9), 0.3))


# --------------------------------------------------------- retriever stories

def lexical_gap_test():
    print("\n== retrieval.py — hybrid fixes a lexical gap (MECH) ==")
    # The embedder is *scripted to lie*: the query vector lands nearest the
    # refund doc (B) while its tokens {cancel, order} live in the cancel doc
    # (A). Day 6's cosine-only regime picks B; the lexical half flips it to A.
    vQ, vA, vB = [0.9, 0.1], [1.0, 0.0], [0.9, 0.1]
    store = make_store([
        ("developer/order#cancel-order", "Cancelling orders",
         "orderCancel cancels a paid order after payment", vA),
        ("developer/payments#refunds", "Refunds",
         "refund a payment via stripe card", vB),
    ])
    gw = ScriptedGateway({"cancel an order": vQ, "orderCancel": vA,
                          "refund a payment": vB})
    ret = Retriever(gw, store, alpha=0.5)
    q = "cancel an order"

    sem = ret.retrieve(q, k=2, mode="semantic")
    lex = ret.retrieve(q, k=2, mode="lexical")
    hyb = ret.retrieve(q, k=2, mode="hybrid")
    check("baseline (cosine-only) picks the wrong doc (refund)",
          sem[0][1].doc_id == "developer/payments#refunds", sem[0][1].doc_id)
    check("pure lexical picks the token-matched doc",
          lex[0][1].doc_id == "developer/order#cancel-order", lex[0][1].doc_id)
    check("hybrid flips to the token-matched doc",
          hyb[0][1].doc_id == "developer/order#cancel-order", hyb[0][1].doc_id)

    # A's cosine is rank 2 under the semantic run; fused(A) = α·cos(A) + (1−α)·1.
    sem_a = sem[1][0]
    expected = 0.5 * sem_a + 0.5 * 1.0
    check("hybrid top1 is the α-weighted average (linear fusion)",
          near(hyb[0][0], expected, 1e-6), f"{hyb[0][0]:.4f} vs {expected:.4f}")
    check("results keep the Day-6 shape [(float, Chunk)]",
          all(isinstance(s, float) and isinstance(c, Chunk) for s, c in hyb))


def decompose_test():
    print("\n== retrieval.py — multi-query decomposition (MECH) ==")
    # The full question embeds (scripted) to the *payment* region — the
    # averaged-intent failure Day 7 measured: one vector lands between
    # intents. Its two sub-questions embed to checkout and order respectively,
    # and max-score merging drags BOTH docs into top-2.
    vP, vC, vO = [0.0, 1.0], [1.0, 0.0], [0.8, 0.2]
    store = make_store([
        ("developer/checkout/api-guide#creating", "Creating a Checkout",
         "checkoutCreate starts a checkout session", vC),
        ("developer/order/overview#lifecycle", "Order Lifecycle",
         "an order is created when a checkout completes", vO),
        ("developer/payments#charge", "Payments",
         "payment captures the transaction amount", vP),
    ])
    q = "How does a checkout become an order, and what changes after?"
    gw = ScriptedGateway({q: vP,
                          "how does a checkout become an order": vC,
                          "what changes after": vO})
    ret = Retriever(gw, store, alpha=1.0)  # isolate the decompose move: α=1

    with_d = ret.retrieve(q, k=2, do_decompose=True)
    embed_cost = ret.last_query["embeds"]
    without_d = ret.retrieve(q, k=2, do_decompose=False)
    ids_d = [c.doc_id for _, c in with_d]
    ids_wo = [c.doc_id for _, c in without_d]
    check("single embed lands wrong (both intents collapsed)",
          ids_wo[0] == "developer/payments#charge"
          and "developer/checkout/api-guide#creating" not in ids_wo, str(ids_wo))
    check("decompose surfaces BOTH sub-intent docs",
          "developer/checkout/api-guide#creating" in ids_d
          and "developer/order/overview#lifecycle" in ids_d, str(ids_d))
    check("decompose costs exactly +1 embed (2 total)", embed_cost == 2)


def filter_test():
    print("\n== retrieval.py — metadata filtering (MECH) ==")
    v = [1.0, 0.0]
    store = make_store([
        ("developer/checkout/api-guide#completing-checkout", "Completing",
         "call checkoutComplete on the checkout", v),
        ("developer/order/overview#lifecycle", "Order", "orders flow through "
         "fulfillment and ship", v),
        ("quickstart/api#create-a-checkout", "Quickstart",
         "checkoutCreate opens a checkout in the quickstart", v),
    ])
    gw = ScriptedGateway({"checkout": v, "order": v})
    ret = Retriever(gw, store, alpha=0.5)

    res = ret.retrieve("How do I complete a checkout?", k=3)
    check("derived filter narrows to the checkout folders",
          all(c.doc_id.startswith("developer/checkout")
              or c.doc_id.startswith("quickstart/api") for _, c in res),
          str(ret.last_query["filter"]))
    check("explicit filter wins over the rule table",
          [c.doc_id for _, c in
           ret.retrieve("How do I complete a checkout?", k=2,
                        filter={"doc_prefix": "developer/order"})]
          == ["developer/order/overview#lifecycle"])
    ret.retrieve("How do I complete a checkout?", k=2,
                 filter={"doc_prefix": "does/not/exist"})
    check("wrong filter degrades to full corpus + annotated miss",
          ret.filter_misses == 1 and ret.last_query["filter_miss"] is True)
    res = ret.retrieve("checkout to order flow", k=2)
    check("ambiguous query → no filter applied (rule len==1)",
          ret.last_query["filter"] is None and len(res) == 2)


def gate_intact_test():
    print("\n== retrieval.py — out-of-corpus gate survives fusion (MECH) ==")
    v = [1.0, 0.0]
    store = make_store([
        ("developer/checkout/api-guide#completing", "Completing",
         "checkoutComplete turns a checkout into an order", v),
    ])
    gw = ScriptedGateway({"checkout": [1.0, 0.0], "cloud": [0.1, 0.9]})
    ret = Retriever(gw, store, alpha=0.5)
    q = "How much does Saleor Cloud cost?"

    res = ret.retrieve(q, k=1)
    check("out-of-corpus lexical contributes zero (no token match)",
          all(x == 0.0 for x in ret.lexicon.normalized(tokenize(q))))
    # fused = 0.5·cos + 0.5·0 → strictly below the semantic vote, so a
    # calibrated τ can still refuse; hybrid never *invents* a lexical match.
    cos = ret.retrieve(q, k=1, mode="semantic")[0][0]
    check("hybrid top1 is α-scaled semantic (no lexical hallucination)",
          near(res[0][0], 0.5 * cos) and res[0][0] <= cos)
    check("gate can still refuse out-of-corpus under hybrid",
          should_refuse(res, tau=0.6))


def baseline_purity_test():
    print("\n== retrieval.py — mode=semantic reproduces Day-6 store.retrieve ==")
    v0, v1, v2 = [1.0, 0.0], [0.7, 0.7], [0.0, 1.0]
    store = make_store([
        ("d#a", "h", "checkout cart", v0),
        ("d#b", "h", "checkout order", v1),
        ("d#c", "h", "webhooks api", v2),
    ])
    gw = ScriptedGateway({"checkout order": v1, "orders": v1})
    ret = Retriever(gw, store, alpha=1.0)
    q = "checkout order"
    got = ret.retrieve(q, k=2, mode="semantic")
    naive = store.retrieve(gw.embed([q])[0].vector, k=2)
    check("identical (score, doc_id) top-2 as the Day-6 seam",
          [(round(s, 6), c.doc_id) for s, c in got]
          == [(round(s, 6), c.doc_id) for s, c in naive],
          str([(round(s, 4), c.doc_id) for s, c in got]))


def fixtures_regression_test():
    print("\n== fixtures on the improved retriever (the Day-8 claim, MECH) ==")
    gw = _rd.StubGateway(extra_vocab=_rd._STUB_EXTRA_VOCAB)
    store = _rd.build_stub_store(gw)
    data = load_questions()
    base = Retriever(gw, store, alpha=1.0)
    improved = Retriever(gw, store, alpha=0.5)

    def run_on(retriever, moves: bool):
        out = {}
        for fx in data["questions"]:
            results = retriever.retrieve(fx["question"], k=8,
                                         do_rewrite=moves, do_decompose=moves)
            out[fx["id"]] = check_fixture(fx, results)[0]
        return out

    base_rows = run_on(base, moves=False)
    imp_rows = run_on(improved, moves=True)
    check("baseline fails C1 (wrong app sense — as Day 7 diagnosed)",
          base_rows["C1"] is False)
    answerable = ["A1", "B1", "B2", "C1", "C2", "D1", "D2"]
    check("improved keeps all answerable families green (7/7)",
          all(imp_rows[q] for q in answerable),
          str({q: imp_rows[q] for q in answerable}))
    fixed = [q for q in answerable if not base_rows[q] and imp_rows[q]]
    check("a Day-7 red row turned green (C1 via section filter)", fixed == ["C1"])
    # the *moves* carry the win, so α=1 + moves must not lose what hybrid found.
    moves_only = Retriever(gw, store, alpha=1.0)
    rows = moves_only.retrieve("What is a checkout in Saleor?", k=8,
                               do_rewrite=True, do_decompose=True)
    check("moves alone (α=1) keep A1's expected doc",
          "quickstart/api#create-a-checkout" in {c.doc_id for _, c in rows})


def main() -> None:
    lexicon_tests()
    rewrite_tests()
    lexical_gap_test()
    decompose_test()
    filter_test()
    gate_intact_test()
    baseline_purity_test()
    fixtures_regression_test()

    print()
    if not FAILS:
        print("ALL DAY 8 (MECH) CHECKS PASSED")
        raise SystemExit(0)
    print(f"{FAILS} CHECK(S) FAILED")
    raise SystemExit(1)


if __name__ == "__main__":
    main()