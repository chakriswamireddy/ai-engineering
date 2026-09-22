"""Day 8 §7–§8 — baseline-vs-improved retrieval comparison + α sweep.

Runs the nine pre-registered Day-7 questions through BOTH retrievers —
Day 6's baseline (pure cosine, `mode="semantic"`) and Day 8's improved
(hybrid fusion, rewrite, decompose, filter) — and prints the Rule-4 table:
per-question recall@K, MRR, and gate status, on *this run's* real numbers.

Retrieval-only: one embed per sub-question, zero chat calls. Fully offline
in `--stub` mode, so the mechanics are green without an API key (semantic
confirmation stays `PENDING` — a real embedder must confirm the ranks, and
the E-family τ must be recalibrated onto the *fused* score).

    $ python tools/retrieval_day8.py --stub            # offline (no key)
    $ python tools/retrieval_day8.py --alpha 0.5       # real store via $RAG_STORE_PATH
    $ python tools/retrieval_day8.py --stub --sweep    # α ∈ {0, .25, .5, .75, 1}
    $ python tools/retrieval_day8.py --report docs/evaluations/retrieval-day8.md

    --k        top-K to measure                       (default 5)
    --tau      similarity gate for the E-family rows  (stub default 0.05)
    --sweep    α sweep instead of a single run
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_os.config import build_gateway_from_env  # noqa: E402
from ai_os.fixtures import check_fixture, load_questions  # noqa: E402
from ai_os.gate import should_refuse  # noqa: E402
from ai_os.rag import load_store_from_env  # noqa: E402
from ai_os.retrieval import Retriever  # noqa: E402
from ai_os.trace import expected_stats  # noqa: E402

import tools.rag_debug as _rd  # noqa: E402  (StubGateway, STUB_DOCS, stub vocab)

SWEEP_ALPHAS = [0.0, 0.25, 0.5, 0.75, 1.0]


def _metrics(fx: dict, results: list[tuple[float, object]]) -> dict:
    """Recall@K / MRR / top1 / gate for a fixture's retrieval, as a dict."""
    ids = [c.doc_id for _, c in results]
    stat = expected_stats(fx["expected"], ids)
    mrr = round(stat["mrr"], 4) if stat["mrr"] else None
    recall = stat["recall_at_k"]
    top1 = float(results[0][0]) if results else None
    return {"recall": recall, "mrr": mrr, "top1": top1, "ids": ids}


def run_one(retriever: Retriever, fx: dict, *, k: int, tau: float,
            moves: bool = True) -> dict:
    """Retrieve one fixture and grade it, E-family included.

    `moves` toggles the query-side Day-8 moves (rewrite + decompose +
    derived section filter) — off for the semantic baseline, on for the
    improved run.
    """
    results = retriever.retrieve(fx["question"], k=k,
                                 do_rewrite=moves, do_decompose=moves)
    m = _metrics(fx, results)
    row = {"id": fx["id"], "family": fx["family"], "question": fx["question"],
           "expected": list(fx["expected"]), "recall": m["recall"],
           "mrr": m["mrr"], "top1": m["top1"]}
    if fx.get("must_refuse"):
        refused = bool(results) and should_refuse(results, tau)
        row["ok"] = refused
        row["note"] = (f"refused (top1 {row['top1']:.3f} < τ {tau})" if refused
                       else f"top1 {row['top1']:.3f} ≥ τ {tau} — recalibrate onto "
                            "fused score (PENDING)")
        row["recall"] = None
        row["mrr"] = None
    else:
        ok, reason = check_fixture(fx, results)
        row["ok"] = ok
        row["note"] = reason if not ok else "expected ⊆ top-K → OK"
    return row


def run_all(gw, store, *, alpha: float, k: int, tau: float,
            moves: bool = True) -> tuple[dict, list[dict], list[dict]]:
    """Baseline (semantic) + improved (hybrid) rows for all nine questions.

    Returns (summary, baseline_rows, improved_rows).
    """
    data = load_questions()
    base = Retriever(gw, store, alpha=1.0)
    improved = Retriever(gw, store, alpha=alpha)

    base_rows = [run_one(base, fx, k=k, tau=tau, moves=False)
                 for fx in data["questions"]]
    imp_rows = [run_one(improved, fx, k=k, tau=tau, moves=moves)
                for fx in data["questions"]]

    def agg(rows):
        recalls = [r["recall"] for r in rows if r["recall"] is not None]
        mrrs = [r["mrr"] for r in rows if r["mrr"] is not None]
        refusals = sum(1 for r in rows if r["id"][0] == "E" and r["ok"])
        total_recall = (sum(recalls) / len(recalls)) if recalls else None
        return {
            "mean_recall": round(total_recall, 4) if total_recall is not None else None,
            "mean_mrr": round(sum(mrrs) / len(mrrs), 4) if mrrs else None,
            "fixtures_green": sum(1 for r in rows if r["ok"]),
            "e_refused": refusals,
        }

    return {"baseline": agg(base_rows), "improved": agg(imp_rows)}, base_rows, imp_rows


def render(rows: list[dict], label: str) -> None:
    print(f"\n-- {label} --")
    print(f"{'ID':<4}{'recall@K':<10}{'MRR':<8}{'top1':<8}ok   note")
    for r in rows:
        rec = f"{r['recall']:.0%}" if r["recall"] is not None else "  n/a"
        mrr = f"{r['mrr']:.3f}" if r["mrr"] is not None else "  n/a"
        top = (f"{r['top1']:.3f}" if r["top1"] is not None else "  \u2205")
        print(f"{r['id']:<4}{rec:<10}{mrr:<8}{top:<8}{'PASS' if r['ok'] else 'FAIL':<5}"
              f"{r['note']}")


def argv_alpha(args) -> float:
    return args.alpha if args.alpha is not None else 0.5


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--store", default=None)
    ap.add_argument("--stub", action="store_true")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--tau", type=float, default=None)
    ap.add_argument("--alpha", type=float, default=None)
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--no-rewrite", action="store_true",
                    help="turn the rewrite/decompose/filter moves off (hybrid "
                         "with only lexical fusion)")
    ap.add_argument("--report", default=None, metavar="PATH")
    args = ap.parse_args()

    if args.stub:
        gw = _rd.StubGateway(extra_vocab=_rd._STUB_EXTRA_VOCAB)
        store = _rd.build_stub_store(gw)
        tau = args.tau if args.tau is not None else 0.05
        print(f"store: {len(store)} chunks (STUB)   tau={tau}   k={args.k}")
    else:
        gw = build_gateway_from_env()
        store = load_store_from_env(args.store) if args.store else load_store_from_env()
        data = load_questions()
        tau = args.tau if args.tau is not None else data["tau"]
        print(f"store: {len(store)} chunks · {store.embedding_model} / "
              f"dim {store.dimension}   tau={tau}   k={args.k}")

    rewrite_rules = not args.no_rewrite

    if args.sweep:
        print(f"\nα sweep over {SWEEP_ALPHAS} (all retrieval-only)")
        summary = []
        for a in SWEEP_ALPHAS:
            s, base_rows, imp_rows = run_all(gw, store, alpha=a, k=args.k,
                                             tau=tau, moves=rewrite_rules)
            summary.append((a, s["baseline"], s["improved"]))
            print(f"α={a:<5} base(MRR {s['baseline']['mean_mrr']}, "
                  f"recall {s['baseline']['mean_recall']}, "
                  f"fx {s['baseline']['fixtures_green']}/9)  →  "
                  f"improved(MRR {s['improved']['mean_mrr']}, "
                  f"recall {s['improved']['mean_recall']}, "
                  f"fx {s['improved']['fixtures_green']}/9, "
                  f"E-refusal {s['improved']['e_refused']}/2)")
        if args.report:
            write_sweep_report(args.report, summary, store)
            print(f"sweep report written → {args.report}")
        return

    s, base_rows, imp_rows = run_all(
        gw, store, alpha=argv_alpha(args), k=args.k, tau=tau, moves=rewrite_rules)
    render(base_rows, f"BASELINE (mode=semantic, α=1, Day 6 verbatim)")
    render(imp_rows, f"IMPROVED (mode=hybrid, α={argv_alpha(args)})")
    print(f"\nbaseline:  mean recall@K {s['baseline']['mean_recall']}  "
          f"mean MRR {s['baseline']['mean_mrr']}  "
          f"green {s['baseline']['fixtures_green']}/9  "
          f"E-refusal {s['baseline']['e_refused']}/2")
    print(f"improved:  mean recall@K {s['improved']['mean_recall']}  "
          f"mean MRR {s['improved']['mean_mrr']}  "
          f"green {s['improved']['fixtures_green']}/9  "
          f"E-refusal {s['improved']['e_refused']}/2")
    if args.report:
        write_report(args.report, base_rows, imp_rows, store, tau, args.k)
        print(f"comparison report written → {args.report}")


def _md_header(store) -> list[str]:
    return [
        "# Retrieval Day 8 — Baseline vs Improved", "",
        f"> Generated by `python tools/retrieval_day8.py`. Retrieval-only "
        f"(zero chat calls), store = {getattr(store, 'embedding_model', 'stub')} "
        f"/ {getattr(store, 'dimension', '?')}d, {len(store)} chunks. The rules: "
        "every number is a real run's; `PENDING` means a real gateway is "
        "required (E-family τ recalibration is the price of the fused score).",
    ]


def write_report(path, base, imp, store, tau, k):
    lines = _md_header(store) + [
        "", f"`τ={tau}`  `k={k}`", "",
        "| ID | base recall@K | new recall@K | base MRR | new MRR | "
        "base note | new note |", "|---|---|---|---|---|---|---|",
    ]
    for b, m in zip(base, imp):
        b_rec = f"{b['recall']:.0%}" if b["recall"] is not None else "n/a"
        m_rec = f"{m['recall']:.0%}" if m["recall"] is not None else "n/a"
        b_mrr = f"{b['mrr']:.3f}" if b["mrr"] is not None else "n/a"
        m_mrr = f"{m['mrr']:.3f}" if m["mrr"] is not None else "n/a"
        lines.append(
            f"| {b['id']} | {b_rec} | {m_rec} | {b_mrr} | {m_mrr} | "
            f"{b['ok'] and 'PASS' or 'FAIL'} — {b['note']} | "
            f"{m['ok'] and 'PASS' or 'FAIL'} — {m['note']} |")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_sweep_report(path, summary, store):
    lines = _md_header(store) + [
        "", "## α sweep (MECH: arithmetic on the stub/real store, no chat)", "",
        "| α | base mean recall | base MRR | new mean recall | new MRR | "
        "new green | new E-refusal |", "|---|---|---|---|---|---|---|",
    ]
    for a, base, imp in summary:
        lines.append(
            f"| {a} | {base['mean_recall']} | {base['mean_mrr']} | "
            f"{imp['mean_recall']} | {imp['mean_mrr']} | "
            f"{imp['fixtures_green']}/9 | {imp['e_refused']}/2 |")
    lines += [
        "",
        "> Choosing α by this table alone is only the *mechanical* half — the "
        "fixtures pass/fail counts are MECH, but the ranks they grade are the "
        "stub's. Real-corpus α selection (and E-family τ on the fused score) "
        "is `PENDING` until `--store data/store.json` runs on a live embedder.",
    ]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()