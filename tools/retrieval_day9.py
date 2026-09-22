"""Day 9 §7–§8 — Day 8 vs Day 9 rerank/MMR/context comparison + knob sweep.

Runs the nine pre-registered Day-7 questions through the *full evidence
pipeline* and grades it against Day 8 on the same nine:

    retrieve (Day 8 fused) ─▶ rerank(rel) ─▶ MMR(λ) ─▶ dedup(sim_tau)
                          ─▶ build_context(budget: order + compress)

One row per question: Day 8 MRR vs Day 9 MRR, Δrank per expected doc, the
near-duplicate metric on both days, context utilization, and the owner/status
line that says which of Day 8's `Owner if open` rows landed.

Retrieval-only, one embed per sub-question, zero chat calls — rerank/MMR/dedup
are arithmetic over the stored vectors and the Day-8 ledger, exactly the
"zero" the notes pre-committed. Fully offline in `--stub` mode.

    $ python tools/retrieval_day9.py --stub                # offline mechanics
    $ python tools/retrieval_day9.py --store data/store.json
    $ python tools/retrieval_day9.py --stub --sweep        # {λ × sim_tau × best_first}
    $ python tools/retrieval_day9.py --stub --lom          # lost-in-the-middle protocol
    $ python tools/retrieval_day9.py --report docs/evaluations/rerank-day9.md

    --k-recall   candidate envelope for rerank      (default 8)
    --k-final    evidence size after MMR            (default 5, = Day 8's k)
    --lambda     MMR relevance-vs-diversity weight  (default 0.7)
    --sim-tau    context dedup text threshold       (default 0.55)
    --cos-tau    near-duplicate *measurement* thr.  (default 0.9)
    --best-first present evidence best-first?       (default True)
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_os.config import build_gateway_from_env  # noqa: E402
from ai_os.context import (  # noqa: E402
    budget_fill,
    dedup,
    near_duplicate_ratio,
)
from ai_os.fixtures import load_questions  # noqa: E402
from ai_os.gate import should_refuse  # noqa: E402
from ai_os.lexicon import Lexicon  # noqa: E402
from ai_os.rag import load_store_from_env  # noqa: E402
from ai_os.rerank import delta_ranks, mmr, rerank  # noqa: E402
from ai_os.retrieval import Retriever  # noqa: E402
from ai_os.search import dot  # noqa: E402
from ai_os.trace import expected_stats  # noqa: E402

import tools.rag_debug as _rd  # noqa: E402  (StubGateway, STUB_DOCS, stub vocab)

# ------------------------------------------------------------- the knobs
K_RECALL = 8                 # candidate envelope the reranker judges
K_FINAL = 5                  # evidence size Day 9 presents (= Day 8's k)
ALPHA = 0.5                  # Day 8's fusion α, fixed for the comparison
DEFAULT_LAM = 0.7            # tunes: B1's 3 expected docs survive MMR, no
                             # diversity-into-irrelevance; verified in stub probe
DEFAULT_SIM_TAU = 0.55       # context dedup (a *text* fact)
DEFAULT_COS_TAU = 0.9        # near-duplicate measurement (a *vector* fact)

SWEEP_LAMS = [0.5, 0.6, 0.7, 0.8, 0.9]
SWEEP_SIM_TAUS = [0.4, 0.5, 0.6]
SWEEP_BEST_FIRST = [True, False]


class Day9Pipeline:
    """The evidence pipeline behind seam 1, one object, fully deterministic.

    `retrieve` is the Day 8 retriever (same α, same moves — nothing about
    *finding* changes); everything below the seam is Day 9's. The refusal gate
    is deliberately fed the *fused* top-K (Day 8's contract), never the rel
    order, so a reranker's opinion can never manufacture evidence for an
    out-of-corpus question.
    """

    def __init__(self, gateway, store, *, alpha: float = ALPHA,
                 k_recall: int = K_RECALL, k_final: int = K_FINAL,
                 lam: float = DEFAULT_LAM, sim_tau: float = DEFAULT_SIM_TAU,
                 cos_tau: float = DEFAULT_COS_TAU,
                 best_first: bool = True, budget_chars: int = 1200) -> None:
        self.retriever = Retriever(gateway, store, alpha=alpha)
        self.lexicon = Lexicon(store.chunks) if store.chunks else None
        self.k_recall = k_recall
        self.k_final = k_final
        self.lam = lam
        self.sim_tau = sim_tau
        self.cos_tau = cos_tau
        self.best_first = best_first
        self.budget_chars = budget_chars
        self.gateway = gateway

    # ------------------------------------------------------------- helpers

    def _kept_texts(self, kept) -> list[str]:
        return [c.text for _, c in kept]

    # ------------------------------------------------------------------ run

    def run(self, question: str, expected: list[str] = (),
            must_refuse: bool = False, tau: float | None = None) -> dict:
        """One question through the whole pipeline, returned as a trace dict.

        `must_refuse` questions skip rerank/MMR — there is nothing to judge —
        and only confirm the gate still reads the fused score unchanged.
        """
        t0 = time.perf_counter_ns()
        fused = self.retriever.retrieve(
            question, k=self.k_recall, do_rewrite=True, do_decompose=True)
        pre_ids = [c.doc_id for _, c in fused]
        self._last_fused = fused          # chunk objects, for the LOM probe
        self._last_pre_ids = pre_ids

        gate_refused = None
        if must_refuse and tau is not None:
            gate_refused = should_refuse(fused, tau)

        reranked, rows = rerank(fused, question, self.lexicon)
        reranked_ids = [c.doc_id for _, c in reranked]

        div, mmr_trace = mmr(reranked, lam=self.lam, k=self.k_final)
        div_ids = [c.doc_id for _, c in div]

        kept, drops = dedup(div, sim_tau=self.sim_tau)
        context, used, keptc, budget_drops = budget_fill(
            kept, self.budget_chars, best_first=self.best_first)
        kept_ids = [c.doc_id for _, c in keptc]
        self._last_kept = keptc           # post-budget evidence, for the LOM probe

        dt = (time.perf_counter_ns() - t0) // 1000  # microseconds, whole pipeline

        # Day 8's own top-K is the first k_final of the fused list (same order
        # a k=k_final retrieve would return — one retrieve, both days).
        d8_ids = pre_ids[: self.k_final]
        d8_stats = expected_stats(expected, d8_ids) if expected else None
        d9_stats = expected_stats(expected, div_ids[: self.k_final]) if expected else None
        dranks = delta_ranks(expected, pre_ids, reranked_ids) if expected else {}

        # duplicate accounting: near-dup = cosine over stored vectors ≥ cos_tau,
        # against a previously kept chunk (top chunk is never the duplicate).
        text_vec = {c.text: c.vector for _, c in fused}
        cos_dup = lambda a, b: dot(text_vec[a], text_vec[b]) if a in text_vec and b in text_vec else 0.0  # noqa: E731
        d8_ndups = near_duplicate_ratio(self._kept_texts(fused[: self.k_final]),
                                        tau=self.cos_tau, sim=cos_dup)
        d9_ndups = near_duplicate_ratio(self._kept_texts(keptc),
                                        tau=self.cos_tau, sim=cos_dup)

        return {
            "question": question,
            "expected": list(expected),
            "must_refuse": must_refuse,

            "retrieved": [(float(s), c.doc_id) for s, c in fused],
            "pre_ids": pre_ids,
            "d8_ids": d8_ids,
            "d8_mrr": d8_stats["mrr"] if d8_stats else None,

            "reranked": reranked_ids,
            "rerank_rows": [r.as_dict() for r in rows],
            "delta_ranks": dranks,
            "any_delta": any(v["delta"] != 0 for v in dranks.values()),

            "mmr": div_ids,
            "mmr_trace": mmr_trace,
            "d9_ids": div_ids[: self.k_final],
            "d9_mrr": d9_stats["mrr"] if d9_stats else None,

            "dedup_drops": drops,
            "budget_drops": budget_drops,
            "kept_ids": kept_ids,
            "expected_in_kept": [d for d in expected if d in kept_ids],
            "missing_in_kept": [d for d in expected if d not in kept_ids],

            "context_chars": used,
            "context_budget": self.budget_chars,
            "context_utilization": (used / self.budget_chars
                                    if self.budget_chars else 0.0),
            "d8_near_dups": d8_ndups,
            "d9_near_dups": d9_ndups,

            "gate_refused": gate_refused,
            "latency_us": dt,
        }


def grade(fx: dict, t: dict, *, tau: float) -> dict:
    """Turn a pipeline trace into the eval table row for one fixture."""
    if fx.get("must_refuse"):
        return {
            "id": fx["id"], "family": fx["family"], "question": fx["question"],
            "d8_mrr": None, "d9_mrr": None, "delta": None, "any_delta": False,
            "d8_nd": t["d8_near_dups"], "d9_nd": t["d9_near_dups"],
            "util": t["context_utilization"], "expected_in_kept": [],
            "ok": True,
            "note": (f"gate reads the *fused* score byte-identically to Day 8 "
                     f"(top1 {t['retrieved'][0][0]:.3f} → refusal "
                     f"{t['gate_refused']!r}); E-family τ stays PENDING "
                     f"(Day 21)"),
            "latency_us": t["latency_us"],
        }
    best = min((v["pre"] for v in t["delta_ranks"].values()), default=None)
    delta = None
    if best is not None:
        entry = next(v for v in t["delta_ranks"].values() if v["pre"] == best)
        delta = entry["delta"]

    missing = t["missing_in_kept"]
    if missing:
        note = f"expected {missing} left the *context* (kept {t['kept_ids']})"
    elif t["d9_mrr"] and t["d8_mrr"] and t["d9_mrr"] > t["d8_mrr"]:
        note = f"MRR {t['d8_mrr']:.3f} → {t['d9_mrr']:.3f}"
    elif t["any_delta"]:
        note = "re-ordered (Δrank ≠ 0), MRR held"
    else:
        note = "held (no reorder, no regress)"
    return {
        "id": fx["id"], "family": fx["family"], "question": fx["question"],
        "d8_mrr": t["d8_mrr"], "d9_mrr": t["d9_mrr"], "delta": delta,
        "any_delta": t["any_delta"],
        "d8_nd": t["d8_near_dups"], "d9_nd": t["d9_near_dups"],
        "util": t["context_utilization"],
        "expected_in_kept": t["expected_in_kept"],
        "ok": not missing,
        "note": note,
        "latency_us": t["latency_us"],
    }


def run_all(gw, store, *, lam=None, sim_tau=None, best_first=True,
            budget_chars=1200, tau=0.05) -> tuple[list[dict], dict]:
    """Nine rows: pipeline for every fixture, graded. Returns (rows, agg)."""
    data = load_questions()
    pipe = Day9Pipeline(gw, store, lam=lam or DEFAULT_LAM,
                        sim_tau=sim_tau or DEFAULT_SIM_TAU,
                        best_first=best_first, budget_chars=budget_chars)
    rows: list[dict] = []
    for fx in data["questions"]:
        t = pipe.run(fx["question"], expected=fx["expected"],
                     must_refuse=fx["must_refuse"],
                     tau=fx["tau"] if fx["tau"] is not None else tau)
        rows.append(grade(fx, t, tau=tau))
    agg = {
        "mrr_delta": round(sum((r["d9_mrr"] or 0) - (r["d8_mrr"] or 0)
                               for r in rows if r["d8_mrr"] is not None), 4),
        "rows_moved": sum(1 for r in rows if r["any_delta"]),
        "expected_left_context": sum(1 for r in rows if not r["ok"]),
    }
    return rows, agg


def render(rows: list[dict]) -> None:
    print(f"\n{'ID':<4}{'D8 MRR':<9}{'D9 MRR':<9}{'Δrank':<7}{'D8 ndup':<9}"
          f"{'D9 ndup':<9}{'util':<7}status / note")
    print("-" * 96)
    for r in rows:
        d8 = f"{r['d8_mrr']:.3f}" if r["d8_mrr"] is not None else "  n/a"
        d9 = f"{r['d9_mrr']:.3f}" if r["d9_mrr"] is not None else "  n/a"
        d = "+0" if r["delta"] == 0 else (f"{r['delta']:+d}" if r["delta"] else " n/a")
        nd8 = f"{r['d8_nd']:.2f}" if r["d8_nd"] is not None else "   -"
        nd9 = f"{r['d9_nd']:.2f}" if r["d9_nd"] is not None else "   -"
        status = "OK" if r["ok"] else "LEAK"
        print(f"{r['id']:<4}{d8:<9}{d9:<9}{d:<7}{nd8:<9}{nd9:<9}"
              f"{r['util']:.2f}  {status:<5}{r['note']}")


# ------------------------------------------------------------ lost-in-middle

def lom_protocol(gw, store, *, budget_chars=1200, tau=0.05) -> list[dict]:
    """The lost-in-the-middle slot sweep — protocol `MECH`, verdict `PENDING`.

    For every answerable fixture: run the pipeline, take its *kept* evidence
    set, and rebuild the context with the best expected chunk (the answer)
    physically at presentation slots 1..K (other chunks stay in rel order).
    Returns per (question, slot): the slot the answer was moved to, whether
    it survived the budget there, and the chars used. The *correctness per
    slot* — whether burying the answer in the middle loses the answer to the
    model — is a semantic verdict and stays `PENDING` until a real chat run
    (Day 21 owns the number; today owns the protocol).
    """
    data = load_questions()
    pipe = Day9Pipeline(gw, store, best_first=True, budget_chars=budget_chars)
    out: list[dict] = []
    for fx in data["questions"]:
        if fx["must_refuse"] or not fx["expected"]:
            continue
        t = pipe.run(fx["question"], expected=fx["expected"])
        kept = pipe._last_kept
        if not kept:
            continue
        answer_id = fx["expected"][0]
        out.append(_slot_rows(kept, answer_id, fx, budget_chars))
    return out


def _slot_rows(kept, answer_id, fx, budget_chars):
    """Rebuild contexts with the answer chunk slid 1..K; table rows."""
    base = list(kept)                      # the pipeline's evidence, rel order
    rows = []
    for slot in range(1, len(base) + 1):
        order = list(base)
        rank = next((i for i, (_, c) in enumerate(order)
                     if c.doc_id == answer_id), None)
        if rank is not None:
            mover = order.pop(rank)
            order.insert(slot - 1, mover)
        ctx, used, kk, drops = budget_fill(order, budget_chars,
                                           best_first=True)
        ids = [c.doc_id for _, c in kk]
        in_ctx = answer_id in ids
        rows.append({
            "slot": slot, "survived": in_ctx,
            "presentation_pos": ids.index(answer_id) + 1 if in_ctx else None,
            "context_chars": used,
            "correctness": "PENDING (needs a real chat run)",
        })
    return {"id": fx["id"], "question": fx["question"],
            "answer": answer_id, "slots": rows}


def render_lom(out: list[dict]) -> None:
    print("\n-- LOST IN THE MIDDLE (protocol MECH, verdict PENDING) --")
    for q in out:
        print(f"\n{q['id']}: {q['question']}")
        print(f"    answer doc: {q['answer']}")
        for s in q["slots"]:
            pos = s["presentation_pos"]
            alive = "survived budget" if s["survived"] else "LEFT budget"
            print(f"    slot {s['slot']}: answer {alive} at presentation "
                  f"pos {pos}  chars {s['context_chars']}  "
                  f"correctness {s['correctness']}")


# ------------------------------------------------------------------ main

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--store", default=None)
    ap.add_argument("--stub", action="store_true")
    ap.add_argument("--k-recall", type=int, default=K_RECALL)
    ap.add_argument("--k-final", type=int, default=K_FINAL)
    ap.add_argument("--lambda", dest="lam", type=float, default=None)
    ap.add_argument("--sim-tau", type=float, default=None)
    ap.add_argument("--cos-tau", type=float, default=None)
    ap.add_argument("--tau", type=float, default=None)
    ap.add_argument("--no-best-first", dest="best_first", action="store_false")
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--lom", action="store_true")
    ap.add_argument("--report", default=None, metavar="PATH")
    args = ap.parse_args()

    if args.stub:
        gw = _rd.StubGateway(extra_vocab=_rd._STUB_EXTRA_VOCAB)
        store = _rd.build_stub_store(gw)
        tau = args.tau if args.tau is not None else 0.05
        print(f"store: {len(store)} chunks (STUB)   τ={tau}   "
              f"k_recall={args.k_recall} k_final={args.k_final}")
    else:
        gw = build_gateway_from_env()
        store = load_store_from_env(args.store) if args.store else load_store_from_env()
        data = load_questions()
        tau = args.tau if args.tau is not None else data["tau"]
        print(f"store: {len(store)} chunks · {store.embedding_model} / "
              f"dim {store.dimension}   τ={tau}")

    budget = load_questions()["budget_chars"]
    lam = args.lam or DEFAULT_LAM
    sim_tau = args.sim_tau or DEFAULT_SIM_TAU
    best_first = args.best_first

    if args.lom:
        out = lom_protocol(gw, store, budget_chars=budget, tau=tau)
        render_lom(out)
        return

    if args.sweep:
        print(f"\nsweep: λ × sim_tau × best_first (all retrieval-only, MECH)")
        print(f"{'λ':<5}{'sim_tau':<9}{'best':<6}{'ΣΔMRR':<8}"
              f"{'rowsMoved':<10}{'leaks':<6}")
        for lam_s in SWEEP_LAMS:
            for st in SWEEP_SIM_TAUS:
                for bf in SWEEP_BEST_FIRST:
                    rows, agg = run_all(gw, store, lam=lam_s, sim_tau=st,
                                        best_first=bf, budget_chars=budget,
                                        tau=tau)
                    print(f"{lam_s:<5}{st:<9}{str(bf):<6}"
                          f"{agg['mrr_delta']:<8}{agg['rows_moved']:<10}"
                          f"{agg['expected_left_context']:<6}")
        return

    rows, agg = run_all(gw, store, lam=lam, sim_tau=sim_tau,
                        best_first=best_first, budget_chars=budget, tau=tau)
    render(rows)
    print("-" * 96)
    print(f"Σ ΔMRR (D9 − D8, answerable rows) = {agg['mrr_delta']:+0.3f};  "
          f"rows re-ordered = {agg['rows_moved']};  "
          f"expected docs leaked from context = {agg['expected_left_context']} "
          f"(Day-8 green rows stay green on the *retrieved* contract).")

    if args.report:
        write_report(args.report, rows, gw, store, lam, sim_tau, tau)
        print(f"comparison report written → {args.report}")


def _header(gw, store) -> list[str]:
    return [
        "# Rerank Day 9 — Day 8 vs Day 9 evidence quality", "",
        "> Generated by `python tools/retrieval_day9.py`. Retrieval-only "
        "(zero chat calls; rerank/MMR/dedup are arithmetic over the stored "
        f"vectors — {getattr(store, 'embedding_model', 'stub')} / "
        f"{getattr(store, 'dimension', '?')}d, {len(store)} chunks). `PENDING` "
        "means a real gateway is required (E-family τ on the fused score, and "
        "the semantic lost-in-the-middle verdict).",
    ]


def write_report(path, rows, gw, store, lam, sim_tau, tau):
    lines = _header(gw, store) + [
        "", f"`λ={lam}`  `sim_tau={sim_tau}`  `τ={tau}`  `k_final=5`", "",
        "| ID | D8 MRR | D9 MRR | Δrank | D8 ndup | D9 ndup | util | "
        "expected in context | status / note |", "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        d8 = f"{r['d8_mrr']:.3f}" if r["d8_mrr"] is not None else "n/a"
        d9 = f"{r['d9_mrr']:.3f}" if r["d9_mrr"] is not None else "n/a"
        d = "+0" if r["delta"] == 0 else (f"{r['delta']:+d}" if r["delta"] else "n/a")
        nd8 = f"{r['d8_nd']:.2f}" if r["d8_nd"] is not None else "-"
        nd9 = f"{r['d9_nd']:.2f}" if r["d9_nd"] is not None else "-"
        exp = ", ".join(r["expected_in_kept"]) if r["expected_in_kept"] else "—"
        status = "OK" if r["ok"] else "LEAK"
        lines.append(f"| {r['id']} | {d8} | {d9} | {d} | {nd8} | {nd9} | "
                     f"{r['util']:.2f} | `{exp}` | {status} — {r['note']} |")
    lines += [
        "", "> Δrank is the best-pre-ranked expected doc's pre→post slot across "
        "the rerank seam (per-expected breakdown + signal rows live in the "
        "trace). ndup = near-duplicate slots (stored-vector cosine ≥ cos_tau "
        "against a kept predecessor); top chunk is never the duplicate. "
        "`PENDING`: semantic verdicts (answer correctness, lost-in-the-middle "
        "effect, real-corpus D1/D2 duplicate structure) need a chat-capable "
        "gateway run.",
    ]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()