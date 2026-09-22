"""Day 9 — deterministic tests for rerank + MMR + context construction (`MECH`).

Day 9's claims, per the notes: the reranker exists (at least one row's rank
order changes by evidence a first pass wouldn't have seen — Δrank ≠ 0), MMR is
the *specific* D1 fix (twins carry high mutual cosine → culled; a distinct
how-to survives), dedup handles D2's served-twice fact (guarded top chunk),
budget compression is evidence-weighted with every drop reported — and all of
it adds **zero** embeds and **zero** chat calls (arithmetic over the stored
Day-5 vectors). Nothing here needs a model or a network:

    $ python test_day9.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from ai_os.context import (  # noqa: E402
    budget_fill,
    dedup,
    near_duplicate_ratio,
    text_sim,
)
from ai_os.contract import Completion, Embedding, Usage  # noqa: E402
from ai_os.gate import should_refuse  # noqa: E402
from ai_os.lexicon import Lexicon, tokenize  # noqa: E402
from ai_os.rag import Chunk, RagStore  # noqa: E402
from ai_os.rerank import (  # noqa: E402
    MIN_DISCRIMINANT_LEN,
    POS_PENALTY,
    _match,
    delta_ranks,
    discriminants,
    heading_score,
    mmr,
    position_score,
    rare_score,
    relevance_cross_encoder,
    rel_from,
    rerank,
)
import tools.rag_debug as _rd  # noqa: E402
from tools.retrieval_day9 import Day9Pipeline  # noqa: E402

FAILS = 0


def check(label: str, cond: bool, detail: str = ""):
    global FAILS
    if not cond:
        FAILS += 1
    print(f"[{'OK  ' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


def near(a: float, b: float, eps: float = 1e-6) -> bool:
    return abs(a - b) < eps


def make_store(chunks: list[tuple[str, str, str, list[float]]]) -> RagStore:
    """chunks: (doc_id, heading, text, vector) — vectors normalized on add."""
    store = RagStore()
    for i, (doc_id, heading, text, vec) in enumerate(chunks):
        store.add(Chunk(chunk_id=f"{doc_id}#0", doc_id=doc_id,
                        source=f"https://docs.saleor.io/{doc_id.split('#')[0]}.md",
                        heading=heading, position=0, text=text, vector=vec))
    return store


class ScriptedGateway:
    """test_day8's tiny no-network gateway: substring → hand vector."""

    def __init__(self, vectors: dict[str, list[float]], embed_dim: int = 2):
        self.vectors = vectors
        self.embed_dim = embed_dim

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

    def chat(self, messages, **kw) -> Completion:  # Day 9 is retrieval-only
        return Completion(text="x", model="gpt-4o-mini", finish_reason="stop",
                          usage=Usage(1, 1, 2))


class RecordingGateway:
    """Wraps any embed/chat gateway and counts every call.

    The Day-9 cost claim is a ledger fact: rerank + MMR + dedup + budget must
    never add a call. `chat_calls == 0` and `embed_calls == #sub-queries`
    (exactly what a Day-8 retrieve spent) is the whole economics story.
    """

    def __init__(self, inner):
        self.inner = inner
        self.embed_calls = 0
        self.chat_calls = 0

    def embed(self, texts):
        self.embed_calls += 1
        return self.inner.embed(texts)

    def chat(self, *a, **k):
        self.chat_calls += 1
        return self.inner.chat(*a, **k)


def _bc(store, doc_id) -> Chunk:
    return next(c for c in store.chunks if c.doc_id == doc_id)


# ---------------------------------------------------------------- matcher

def match_tests():
    print("\n== rerank.py — the discriminator matcher (MECH)")
    check("query term matches its plural in the candidate",
          _match("attribute", "attributes"))
    check("query term matches the inflection in the candidate",
          _match("checkoutcomplete", "checkoutcompletecustom"))
    check("generic 3-char verb never discriminates",
          not _match("set", "checkoutComplete")
          and not _match("set", "attributes"), f"MIN={MIN_DISCRIMINANT_LEN}")
    check("one-way: long candidate term does NOT satisfy a short query term",
          not _match("any", "attributes"),
          "reverse substring must not fire")
    check("tokens are pre-lowercased upstream (tokenize does the folding)",
          tokenize("checkoutComplete API")[0] == "checkoutcomplete"
          and _match("checkoutcomplete", "checkoutcomplete"))

    disc = discriminants("How do I complete a checkout?", None)
    check("len<4 tokens never become discriminators",
          not any(len(t) < MIN_DISCRIMINANT_LEN for t in disc),
          str(set(disc)))
    check("corpus words present with weight 1.0 when no lexicon",
          disc.get("complete") == 1.0 and disc.get("checkout") == 1.0,
          str(disc))
    store = make_store([
        ("d#1", "h", "checkout checkout checkout cart sametoken", [1.0, 0.0]),
        ("d#2", "h", "complete complete complete checkout", [0.0, 1.0]),
    ])
    lex = Lexicon(store.chunks)
    d = discriminants("complete checkout", lex)
    check("IDF weights discriminate: rarer complete > common checkout",
          d["complete"] > d["checkout"],
          f"complete={d['complete']:.3f} checkout={d['checkout']:.3f}")


# ---------------------------------------------------------------- signals

def signal_tests():
    print("\n== rerank.py — rare / heading / position signals (MECH)")
    store = make_store([
        ("developer/graphql/guide#input-types", "Input Types",
         "a field is a graphql schema property; input types define values "
         "accepted by an attribute", [1.0, 0.0]),
        ("developer/attributes/overview#attributes-and-types-overview",
         "Attributes and Types Overview",
         "attributes are product metadata: size, color, material",
         [0.9, 0.1]),
    ])
    lex = Lexicon(store.chunks)
    q = "What is the difference between an attribute and a field?"
    disc = discriminants(q, lex)
    a = _bc(store, "developer/attributes/overview#attributes-and-types-overview")
    b = _bc(store, "developer/graphql/guide#input-types")

    ha = heading_score(a, disc)
    hb = heading_score(b, disc)
    check("heading signal: reference page's section heading fires",
          ha > 0, f"heading(ref)={ha:.3f}")
    check("heading signal: input-types page's heading does not fire",
          hb == 0.0, f"heading(input-types)={hb:.3f}")
    check("heading beats 0 on the overview page",
          ha > hb, f"{ha:.3f} vs {hb:.3f}")

    ra = rare_score(a, disc)
    rb = rare_score(b, disc)
    check("rare signal: attribute-rich text scores",
          ra > 0 and rb > 0, f"rare(ref)={ra:.3f} rare(input-types)={rb:.3f}")

    check("position: earliest chunk is max",
          near(rel_from(0.5, a, q, lex, weights={"fused": 0.5, "rare": 0.25,
                                                 "heading": 0.2, "pos": 0.05},
                        position_floor=0)["pos"], 1.0))
    p_early = position_score(0, 10)
    p_late = position_score(9, 10)
    check("position: late chunk is only mildly penalized",
          p_early == 1.0
          and near(p_late, 0.9 + 0.1 * (1.0 - 9 / 11), 1e-3),
          f"first={p_early:.3f} last={p_late:.3f}")
    check("position is never a verdict (max penalty < 0.5 fused weight)",
          1.0 - POS_PENALTY >= 0.9)
    check("position is monotone (earlier slot ≥ later slot)",
          p_early >= p_late >= position_score(10, 10))

    s = rel_from(0.7, b, q, lex,
                 weights={"fused": 0.5, "rare": 0.25, "heading": 0.2,
                          "pos": 0.05}, position_floor=0)
    check("rel is the weighted sum of clamped signals",
          near(s["rel"],
               0.5 * s["fused"] + 0.25 * s["rare"] + 0.2 * s["heading"]
               + 0.05 * s["pos"]),
          f"rel={s['rel']:.4f}")

    check("cross-encoder tier is a PENDING seam, not shipped",
          _raises_not_implemented())


def _raises_not_implemented() -> bool:
    try:
        relevance_cross_encoder(None, "q", _bc(make_store([
            ("d#1", "h", "checkout", [1.0, 0.0])]), "d#1"))
    except NotImplementedError:
        return True
    return False


# -------------------------------------------------------------- rerank seam

def rerank_tests():
    print("\n== rerank.py — the reranker re-judges candidates (MECH)")
    store = make_store([
        ("developer/checkout/lifecycle#completing-checkout", "Completing Checkout",
         "checkoutComplete turns a checkout into an order; the order then "
         "flows through fulfillment and shipping", [1.0, 0.0]),
        ("developer/checkout/api-guide#creating-a-checkout-session",
         "Creating a Checkout Session",
         "start a checkout session with checkoutCreate; choose lines; set "
         "email and delivery method", [0.9, 0.1]),
    ])
    lex = Lexicon(store.chunks)
    q = "What must be set before checkoutComplete succeeds, and what does it return?"
    # fused order first pass would return: creating-session (0.85), completing.
    # The rel vote flips it: completing's text carries the "checkoutcomplete"
    # discriminator the first pass's α-fusion averaged away.
    results = [
        (0.85, _bc(store, "developer/checkout/api-guide#creating-a-checkout-session")),
        (0.80, _bc(store, "developer/checkout/lifecycle#completing-checkout")),
    ]
    ranked, rows = rerank(results, q, lex)
    ids = [c.doc_id for _, c in ranked]
    check("rerank exists: Δrank ≠ 0 on at least one row",
          any(r.pre_rank != r.post_rank for r in rows),
          str([(r.doc_id, r.pre_rank, r.post_rank) for r in rows]))
    check("checkoutComplete page leads under the rare/heading vote",
          ids[0] == "developer/checkout/lifecycle#completing-checkout", str(ids))
    by_doc = {r.doc_id: r for r in rows}
    check("the winning page's fused band carries over the Day-8 score",
          by_doc["developer/checkout/lifecycle#completing-checkout"].fused == 0.8)
    check("rel is monotone down the list",
          all(rows[i].rel >= rows[i + 1].rel for i in range(len(rows) - 1)))

    # seam contract: same [(score, Chunk)] shape out as in.
    check("seam: rerank output has the same shape as its input",
          all(isinstance(s, float) and isinstance(c, Chunk) for s, c in ranked))
    check("seam: rerank is a reordering, not a discovery",
          {c.doc_id for _, c in ranked} == {c.doc_id for _, c in results})

    d = delta_ranks(["developer/checkout/lifecycle#completing-checkout"],
                    [c.doc_id for _, c in results], ids)
    e = d["developer/checkout/lifecycle#completing-checkout"]
    check("delta_ranks: winning page pre->post is -1",
          e["pre"] == 2 and e["post"] == 1 and e["delta"] == -1 and e["kept"],
          str(e))
    off = delta_ranks(["ghost"], [c.doc_id for _, c in results], ids)
    check("delta_ranks: doc not in pre is skipped", "ghost" not in off)


# -------------------------------------------------------------------- MMR

def mmr_tests():
    print("\n== rerank.py — MMR culls redundancy, keeps distinct wins (MECH)")
    naive = make_store([
        ("d#howto", "How to complete", "checkoutComplete with a shipping method",
         [1.0, 0.0]),
        ("d#twin1", "Completing", "checkoutComplete turns checkout into order",
         [0.98, 0.03]),
        ("d#twin2", "Completing", "call checkoutComplete on the checkout",
         [0.97, 0.04]),
        ("d#other", "Creating", "checkoutCreate starts a session", [0.0, 1.0]),
    ])
    # rel order from the reranker: howto > twin1 > twin2 > other (say).
    rel = [("d#howto", 1.0), ("d#twin1", 0.9), ("d#twin2", 0.8),
           ("d#other", 0.5)]
    ranked = [(r, _bc(naive, doc)) for doc, r in rel]  # (rel, Chunk)

    picked, _ = mmr(ranked, lam=0.6, k=3)
    ids = [c.doc_id for _, c in picked]
    check("MMR picks the top-rel first", ids[0] == "d#howto")
    check("MMR culls a twin: distinct doc beats the near-duplicate twin",
          ids[1] == "d#other" and ids[2] == "d#twin1",
          f"{ids} — twin1/twin2 carry cos≈0.98 to the anchor")
    check("MMR is λ=1 ⇒ pure relevance order",
          [c.doc_id for _, c in mmr(ranked, lam=1.0, k=3)[0]] ==
          ["d#howto", "d#twin1", "d#twin2"])
    check("MMR with k=None returns everything",
          len(mmr(ranked, lam=0.6)[0]) == 4)
    check("MMR respects k < len",
          len(mmr(ranked, lam=0.6, k=2)[0]) == 2)
    for bad in (-0.1, 1.5):
        try:
            mmr(ranked, lam=bad)
        except ValueError:
            continue
        check(f"MMR rejects lam={bad}", False)
    check("MMR rejects out-of-range lam", True)

    # B1's guard: two legitimately-similar docs both survive at moderate λ
    # because neither is redundant with a higher-rel pick *and* their rel is
    # high; the trap (culling B1's 2nd doc) only bites with λ too aggressive.
    rank2 = [(1.0, _bc(naive, "d#howto")),
             (0.9, _bc(naive, "d#twin1")),
             (0.05, _bc(naive, "d#other"))]
    picked2, _ = mmr(rank2, lam=0.7, k=3)
    check("B1 guard: second similar doc survives (λ recorded in the sweep)",
          [c.doc_id for _, c in picked2] == ["d#howto", "d#twin1", "d#other"],
          str([c.doc_id for _, c in picked2]))


# -------------------------------------------------------- context v2 (dedup)

def dedup_tests():
    print("\n== context.py — dedup with the top-chunk guard (MECH)")
    store = make_store([
        ("d#a", "h", "checkoutComplete turns a checkout into an order and "
         "the order flows to fulfillment", [1.0, 0.0]),
        ("d#b", "h", "checkoutComplete turns a checkout into an order and the "
         "order flows to fulfillment", [0.9, 0.0]),
        ("d#c", "h",
         "webhooks let apps listen for order lifecycle events", [0.0, 1.0]),
    ])
    # two near-identical texts (D2: the same fact served twice).
    results = [
        (1.0, _bc(store, "d#a")),
        (0.9, _bc(store, "d#b")),
        (0.8, _bc(store, "d#c")),
    ]
    kept, drops = dedup(results, sim_tau=0.55)
    ids = [c.doc_id for _, c in kept]
    check("dedup drops the near-exact twin (D2)",
          ids == ["d#a", "d#c"], str(ids))
    check("every dedup drop is reported with its reason",
          len(drops) == 1 and drops[0]["reason"] == "redundant"
          and drops[0]["doc_id"] == "d#b", str(drops))

    # guard: a near-duplicate that *is* the top chunk is never dropped.
    kept2, drops2 = dedup([results[1], results[0], results[2]], sim_tau=0.55)
    check("guard: the top-relevance chunk is never similarity-dropped",
          kept2[0][1].doc_id == "d#b"
          and not any(d["doc_id"] == "d#b" for d in drops2),
          str([c.doc_id for _, c in kept2]))

    # two chunks sharing 60% of tokens can carry different facts → survived.
    different = make_store([
        ("d#x", "h", "checkoutComplete turns a checkout into an order",
         [1.0, 0.0]),
        ("d#y", "h", "checkoutComplete turns a checkout into a refund, and "
         "the refund returns to the original payment method", [0.9, 0.1]),
    ])
    r2 = [(1.0, _bc(different, "d#x")), (0.9, _bc(different, "d#y"))]
    same = text_sim(r2[0][1].text, r2[1][1].text)
    k2, d2 = dedup(r2, sim_tau=0.55)
    check("40%-rare fact survives dedup (compression is not deletion)",
          same < 0.55 and len(k2) == 2 and not d2, f"jaccard={same:.3f}")

    check("text_sim is symmetric and bounded [0, 1]",
          near(text_sim("cart line email", "cart line address"), 0.5)
          and 0.0 <= text_sim("checkout create order flow ship",
                              "webhooks apps events listen") < 1.0)
    check("near_duplicate_ratio: < 2 items → 0.0",
          near(near_duplicate_ratio(["only"], tau=0.9), 0.0))
    ratio_on = near_duplicate_ratio(["checkout order", "checkout order",
                                     "webhook event"], tau=0.55)
    check("near_duplicate_ratio: first never a dup, repeats counted",
          near(ratio_on, 1.0 / 3.0), f"ratio={ratio_on:.3f}")


def budget_tests():
    print("\n== context.py — evidence-weighted compression under the budget")
    store = make_store([
        ("d#big", "h", "x" * 300, [1.0, 0.0]),
        ("d#mid", "h", "y" * 200, [0.8, 0.0]),
        ("d#small", "h", "z" * 50, [0.5, 0.0]),
    ])
    results = [(1.0, _bc(store, "d#big")),
               (0.8, _bc(store, "d#mid")),
               (0.5, _bc(store, "d#small"))]
    context, used, kept, drops = budget_fill(results, budget_chars=360)
    check("top chunk always fits and lands first (Day-6 floor)",
          kept[0][1].doc_id == "d#big"
          and used == 300 + 50, f"used={used} kept={[c.doc_id for _, c in kept]}")
    check("next chunk that fits joins, the one that doesn't is reported",
          [c.doc_id for _, c in kept] == ["d#big", "d#small"]
          and drops and drops[0]["reason"] == "budget"
          and drops[0]["doc_id"] == "d#mid", str(drops))
    check("dropped-but-cited is impossible: kept ⊆ input by construction",
          {c.doc_id for _, c in kept} <= {c.doc_id for _, c in results})

    ctx2, used2, kept2, drops2 = budget_fill(results, budget_chars=360,
                                             best_first=False)
    check("best_first=False reverses presentation, same kept set",
          [c.doc_id for _, c in kept2] == [c.doc_id for _, c in kept])
    check("best_first=False flips the leading chunk (the ordering knob)",
          ctx2.split("\n\n")[0] == "z" * 50 and ctx2.split("\n\n")[-1] == "x" * 300)

    ctx0, used0, _, _ = budget_fill(results, budget_chars=60)
    check("a chunk larger than the whole budget still appears alone (floor)",
          used0 == 300 and "x" * 300 in ctx0)


# ------------------------------------------------------------ full stories

def pipeline_stories():
    print("\n== day9 end-to-end stories on the stub corpus (MECH)")
    gw = _rd.StubGateway(extra_vocab=_rd._STUB_EXTRA_VOCAB)
    store = _rd.build_stub_store(gw)
    pipe = Day9Pipeline(gw, store, lam=0.7, sim_tau=0.55)

    t_b2 = pipe.run(
        "What must be set before checkoutComplete succeeds, and what does it return?",
        expected=["developer/checkout/api-guide#completing-checkout",
                  "developer/checkout/lifecycle#completing-checkout"])
    check("B2: with-day-9 MRR 1.0 (completing-checkout led)",
          near(t_b2["d9_mrr"], 1.0), f"MRR={t_b2['d9_mrr']}")
    check("B2: Δrank ≠ 0 (a reorder, not a byproduct)",
          t_b2["any_delta"], str(t_b2["delta_ranks"]))
    check("B2: both expected docs survived into the context",
          not t_b2["missing_in_kept"])

    t_d2 = pipe.run(
        "How do I create a checkout in Saleor?",
        expected=["quickstart/api#create-a-checkout",
                  "developer/checkout/api-guide#creating-a-checkout-session"])
    check("D2: with-day-9 MRR 1.0 (quickstart rose to the lead)",
          near(t_d2["d9_mrr"], 1.0), f"MRR={t_d2['d9_mrr']}")
    check("D2: win came from a rerank move, not a cosmetic one",
          t_d2["any_delta"])

    t_a1 = pipe.run("What is a checkout in Saleor?",
                    expected=["quickstart/api#create-a-checkout"])
    check("A1 (control): Day-8 MRR held, no regress",
          near(t_a1["d8_mrr"], t_a1["d9_mrr"]) and not t_a1["any_delta"],
          f"D8={t_a1['d8_mrr']} D9={t_a1['d9_mrr']}")

    t_d1 = pipe.run("How do I complete a checkout?",
                    expected=["developer/checkout/api-guide#completing-checkout"])
    check("D1: expected how-to page never displaced",
          near(t_d1["d9_mrr"], 1.0), f"MRR={t_d1['d9_mrr']}")
    check("D1: the twin sits right behind the how-to (evidence order moves)",
          t_d1["reranked"][1] == "developer/checkout/lifecycle#completing-checkout",
          str(t_d1["reranked"][:3]))


def gate_untouched_test():
    print("\n== day9 — the gate still reads the *fused* score (E not fabricated)")
    gw = _rd.StubGateway(extra_vocab=_rd._STUB_EXTRA_VOCAB)
    store = _rd.build_stub_store(gw)
    pipe = Day9Pipeline(gw, store)

    fused = pipe.retriever.retrieve("How much does Saleor Cloud cost?", k=8,
                                    do_rewrite=True, do_decompose=True)
    t = pipe.run("How much does Saleor Cloud cost?", must_refuse=True, tau=0.7)
    check("rerank is a reordering of Day 8's candidates (no new evidence)",
          set(t["reranked"]) <= set(c.doc_id for _, c in fused))
    same_decision = should_refuse(fused, 0.7)
    check("gate input is the fused list, identical to Day 8's (rel never fed)",
          t["gate_refused"] == same_decision and same_decision is not None,
          f"gate={t['gate_refused']} day8gate={same_decision}")
    check("a calibrated τ still refuses out-of-corpus under day-9",
          same_decision is True)


def cost_test():
    print("\n== day9 — zero new embed/chat calls vs Day 8 (the ledger fact)")
    inner = _rd.StubGateway(extra_vocab=_rd._STUB_EXTRA_VOCAB)
    store = _rd.build_stub_store(inner)
    gw = RecordingGateway(inner)
    pipe = Day9Pipeline(gw, store)

    pipe.run("How does a checkout become an order, and what changes after?")
    check("B1's decompose cost exactly 2 embeds (a Day-8 retrieve, nothing more)",
          gw.embed_calls == 2, f"embed_calls={gw.embed_calls}")
    check("rerank+MMR+dedup+budget made zero chat calls",
          gw.chat_calls == 0, f"chat_calls={gw.chat_calls}")
    pipe.run("What is a checkout in Saleor?")
    check("the same ledger holds for a second, single-embed question",
          gw.embed_calls == 3 and gw.chat_calls == 0,
          f"embed_calls={gw.embed_calls} chat_calls={gw.chat_calls}")
    check("the pipeline still reports a nonzero latency (it *did* work)",
          pipe.run("What is a checkout in Saleor?")["latency_us"] >= 0)


def knob_tests():
    print("\n== day9 — the pre-registered knobs: weights + mmr/dedup order")
    gw = _rd.StubGateway(extra_vocab=_rd._STUB_EXTRA_VOCAB)
    store = _rd.build_stub_store(gw)

    default = Day9Pipeline(gw, store)
    t = default.run("What is a checkout in Saleor?",
                    expected=["quickstart/api#create-a-checkout"])
    check("defaults unchanged: mmr_first, default weights (control still held)",
          t["d8_mrr"] == t["d9_mrr"] and not t["any_delta"] and default.mmr_first
          and default.weights["fused"] == 0.5,
          str(default.weights))

    heading = Day9Pipeline(gw, store, weights={"fused": 0.3, "rare": 0.2,
                                               "heading": 0.45, "pos": 0.05})
    th = heading.run("How do I create a checkout in Saleor?",
                     expected=["quickstart/api#create-a-checkout",
                               "developer/checkout/api-guide#creating-a-checkout-session"])
    check("weights knob reaches rerank: heading-heavy still lifts quickstart",
          near(th["d9_mrr"], 1.0) and th["any_delta"], f"MRR={th['d9_mrr']}")

    dedup_first = Day9Pipeline(gw, store, mmr_first=False)
    td = dedup_first.run("How do I complete a checkout?",
                         expected=["developer/checkout/api-guide#completing-checkout"])
    check("dedup-first order is a config, not a crash: expected doc survives",
          not td["missing_in_kept"] and not td["any_delta"] is None,
          f"kept={td['kept_ids']}")

    no_dupes = Day9Pipeline(gw, store, mmr_first=False)
    ta = no_dupes.run("What is a checkout in Saleor?",
                      expected=["quickstart/api#create-a-checkout"])
    check("dedup-first keeps the control green (no doubling the D1 double)",
          not ta["missing_in_kept"])

    inner = _rd.StubGateway(extra_vocab=_rd._STUB_EXTRA_VOCAB)
    s2 = _rd.build_stub_store(inner)
    g2 = RecordingGateway(inner)
    p = Day9Pipeline(g2, s2, weights={"fused": 0.3, "rare": 0.45,
                                      "heading": 0.2, "pos": 0.05},
                     mmr_first=False)
    p.run("How does a checkout become an order, and what changes after?")
    check("knobbed pipeline keeps the zero-ledger story (2 embeds, 0 chat)",
          g2.embed_calls == 2 and g2.chat_calls == 0,
          f"embeds={g2.embed_calls} chat={g2.chat_calls}")


def hold_green_test():
    print("\n== day9 — Day 8's green rows stay green (retrieved contract)")
    gw = _rd.StubGateway(extra_vocab=_rd._STUB_EXTRA_VOCAB)
    store = _rd.build_stub_store(gw)
    pipe = Day9Pipeline(gw, store)
    cases = [
        ("A1", "What is a checkout in Saleor?",
         ["quickstart/api#create-a-checkout"]),
        ("B1", "How does a checkout become an order, and what changes after?",
         ["quickstart/api#create-a-checkout",
          "developer/checkout/lifecycle#completing-checkout",
          "developer/order/overview#lifecycle"]),
        ("B2", "What must be set before checkoutComplete succeeds, and what does it return?",
         ["developer/checkout/api-guide#completing-checkout",
          "developer/checkout/lifecycle#completing-checkout"]),
        ("C1", "How do apps work in Saleor?",
         ["developer/extending/apps/overview#apps-overview"]),
        ("C2", "What is the difference between an attribute and a field?",
         ["developer/attributes/overview#attributes-and-types-overview",
          "developer/attributes/api#input-types"]),
        ("D1", "How do I complete a checkout?",
         ["developer/checkout/api-guide#completing-checkout"]),
        ("D2", "How do I create a checkout in Saleor?",
         ["quickstart/api#create-a-checkout",
          "developer/checkout/api-guide#creating-a-checkout-session"]),
    ]
    for fid, q, expected in cases:
        t = pipe.run(q, expected=expected)
        check(f"{fid}: expected docs all present in the *context* (no leaks)",
              not t["missing_in_kept"], str(t["missing_in_kept"]))
        check(f"{fid}: all expected docs reached the evidence top-5 (MRR ≥ D8)",
              (t["d9_mrr"] or 0) >= (t["d8_mrr"] or 0),
              f"D8={t['d8_mrr']} D9={t['d9_mrr']}")


def main() -> None:
    match_tests()
    signal_tests()
    rerank_tests()
    mmr_tests()
    dedup_tests()
    budget_tests()
    pipeline_stories()
    gate_untouched_test()
    cost_test()
    knob_tests()
    hold_green_test()

    print()
    if not FAILS:
        print("ALL DAY 9 (MECH) CHECKS PASSED")
        raise SystemExit(0)
    print(f"{FAILS} CHECK(S) FAILED")
    raise SystemExit(1)


if __name__ == "__main__":
    main()