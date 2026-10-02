"""Day 10 §8 — Doc-RAG vs Code-RAG: one harness, two datasets.

Runs the pre-registered **code** questions (`ai_os/code_questions.yaml`) through
the full evidence pipeline (Day-8 `Retriever` with the identifier tokenizer +
code rewrite table → Day-9 rerank/MMR/dedup/budget, zero lines changed) and, in
parallel, re-runs the **doc** nine to prove no regression. Two stores, one
harness, one Rule-4 table per dataset — Day 21 inherits both as its benchmarks.

The only code-specific pieces are vocabulary (`code_tokens`, `CODE_REWRITES`)
and metadata (`kind` / `module_prefix` filters); the metric machinery
(`recall@K`, `MRR`, `Δrank`, dup-ratio, `should_refuse`, `cited ⊆ retrieved`) is
identical, because it never depended on what the text was.

    $ python tools/retrieval_day10.py --stub            # offline, both corpora
    $ python tools/retrieval_day10.py --store data/code_store.json \
        --doc-store data/store.json                      # real corpora
    $ python tools/retrieval_day10.py --stub --report docs/evaluations/code-day10.md
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_os.config import build_gateway_from_env  # noqa: E402
from ai_os.fixtures import (  # noqa: E402
    CODE_FIXTURES_PATH,
    check_fixture,
    load_questions,
)
from ai_os.rag import (  # noqa: E402
    load_code_store_from_env,
    load_store_from_env,
)
from ai_os.retrieval import CODE_REWRITES, code_tokens  # noqa: E402
from ai_os.trace import expected_stats  # noqa: E402

import tools.code_debug as _cd  # noqa: E402  (code stub corpus + hash gateway)
import tools.rag_debug as _rd  # noqa: E402  (doc stub corpus + gateway)
from tools.retrieval_day9 import Day9Pipeline  # noqa: E402
from tools.retrieval_day9 import run_all as run_doc_all  # noqa: E402

CODE_K_RECALL = 8
CODE_K_FINAL = 5
CODE_ALPHA = 0.5
CODE_BUDGET = 4000       # code bodies are long; the symbol is the unit
DOC_BUDGET = 1200


def code_pipeline(gw, store, *, budget_chars: int = CODE_BUDGET) -> Day9Pipeline:
    """The Day-9 pipeline retargeted at code: identifier tokenizer, code rewrite
    table, and *no* prose section rules (code filters are explicit/metadata)."""
    return Day9Pipeline(
        gw, store, alpha=CODE_ALPHA, k_recall=CODE_K_RECALL,
        k_final=CODE_K_FINAL, budget_chars=budget_chars,
        tokenizer=code_tokens, rewrites=CODE_REWRITES,
        sections={}, section_prefixes={})


def run_code_all(gw, store, *, tau: float) -> list[dict]:
    """Every code fixture through the pipeline, graded. Retrieval-only."""
    data = load_questions(CODE_FIXTURES_PATH)
    pipe = code_pipeline(gw, store)
    rows: list[dict] = []
    for fx in data["questions"]:
        eff_tau = fx["tau"] if fx["tau"] is not None else tau
        t = pipe.run(fx["question"], expected=fx["expected"],
                     must_refuse=fx["must_refuse"], tau=eff_tau)
        fk = dict(fx)
        fk["tau"] = eff_tau
        ok, reason = check_fixture(fk, t["retrieved"])
        exp = fk["expected"]
        stats = expected_stats(exp, t["d9_ids"]) if exp else None
        best = min((v["pre"] for v in t["delta_ranks"].values()), default=None)
        delta = None
        if best is not None:
            delta = next(v for v in t["delta_ranks"].values()
                         if v["pre"] == best)["delta"]
        rows.append({
            "id": fx["id"], "family": fx["family"], "question": fx["question"],
            "must_refuse": fx["must_refuse"],
            "top1": t["retrieved"][0][0] if t["retrieved"] else None,
            "recall": stats["recall_at_k"] if stats else None,
            "mrr": stats["mrr"] if stats else None,
            "delta": delta, "any_delta": t["any_delta"],
            "d9_ids": t["d9_ids"], "kept_ids": t["kept_ids"],
            "expected_in_kept": t["expected_in_kept"],
            "missing_in_kept": t["missing_in_kept"],
            "gate_refused": t["gate_refused"],
            "util": t["context_utilization"],
            "ok": ok, "reason": reason, "latency_us": t["latency_us"],
        })
    return rows


def render_code(rows: list[dict]) -> None:
    print(f"\n{'ID':<5}{'top1':<8}{'recall':<8}{'MMR':<7}{'Δ':<5}{'util':<7}"
          f"status / evidence")
    print("-" * 96)
    for r in rows:
        top1 = f"{r['top1']:.3f}" if r["top1"] is not None else "  \u2205"
        rec = f"{r['recall']:.0%}" if r["recall"] is not None else " n/a"
        mrr = f"{r['mrr']:.3f}" if r["mrr"] is not None else " n/a"
        d = "+0" if r["delta"] == 0 else (f"{r['delta']:+d}" if r["delta"] else " -")
        status = "OK" if r["ok"] else "FAIL"
        print(f"{r['id']:<5}{top1:<8}{rec:<8}{mrr:<7}{d:<5}{r['util']:.2f}  "
              f"{status:<5}{'; '.join(r['d9_ids'][:2]) or r['reason']}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stub", action="store_true",
                    help="offline: stub code corpus + stub doc store")
    ap.add_argument("--store", default=None,
                    help="code store path (default: $RAG_CODE_STORE_PATH)")
    ap.add_argument("--doc-store", default=None,
                    help="doc store path for the no-regression line")
    ap.add_argument("--tau", type=float, default=None)
    ap.add_argument("--no-docs", action="store_true",
                    help="skip the doc-corpus regression table")
    ap.add_argument("--report", default=None, metavar="PATH")
    ap.add_argument("--live", action="store_true",
                    help="WS5: alias for real-store mode (no --stub); labels "
                         "S2 latency cold (first query incl. store load) vs "
                         "warm (steady state)")
    args = ap.parse_args()
    import time as _time
    _t_cold0 = _time.perf_counter()

    if args.stub:
        gw_code = _cd.HashEmbedGateway()
        code_store = _cd.build_stub_code_store(gw_code)
        gw_doc = _rd.StubGateway(extra_vocab=_rd._STUB_EXTRA_VOCAB)
        doc_store = _rd.build_stub_store(gw_doc)
        # Stub τ is a demo choice, not a calibration: on this 14-symbol stub the
        # answerable top-1s run 0.636-0.863 and CE1's is 0.703 — they *overlap*
        # (no τ separates E from the answerable family), so 0.75 is picked only
        # to exercise the refusal path. Real calibration is Day 21's.
        tau = args.tau if args.tau is not None else 0.75
        print(f"code store: {len(code_store)} symbols (STUB)   "
              f"doc store: {len(doc_store)} chunks (STUB)   tau={tau}")
    else:
        gw_code = build_gateway_from_env()
        code_store = (load_code_store_from_env(args.store) if args.store
                      else load_code_store_from_env())
        gw_doc = gw_code
        doc_store = (load_store_from_env(args.doc_store) if args.doc_store
                     else load_store_from_env())
        data = load_questions(CODE_FIXTURES_PATH)
        tau = args.tau if args.tau is not None else data["tau"]
        print(f"code store: {len(code_store)} symbols · {code_store.embedding_model}"
              f" / dim {code_store.dimension}   tau={tau}")

    import time as _time2
    rows = run_code_all(gw_code, code_store, tau=tau)
    _cold_ms = (_time2.perf_counter() - _t_cold0) * 1000.0
    # WS5 S2 — cold vs warm: the first row paid store load + first embed;
    # re-time one warm query over the loaded store for the honest comparison.
    _w0 = _time2.perf_counter()
    _pipe = code_pipeline(gw_code, code_store)
    _pipe.run(rows[0]["question"] if rows else "checkout",
              expected=[], must_refuse=False, tau=tau)
    _warm_ms = (_time2.perf_counter() - _w0) * 1000.0
    print("\n" + "=" * 96)
    print("   DAY 10 — CODE-RAG (symbol fixtures; MECH under the stub gateway)")
    print("=" * 96)
    print(f"S2 latency: cold={_cold_ms:.1f}ms (store load + first query) "
          f"warm={_warm_ms:.1f}ms (steady state)")
    render_code(rows)
    green = sum(1 for r in rows if r["ok"])
    print("-" * 96)
    print(f"{green}/{len(rows)} code fixtures green. "
          f"Semantic verdicts (does the model turn the symbol into a "
          f"file:line answer) stay PENDING — Day 21.")

    doc_rows = []
    if not args.no_docs:
        doc_rows, agg = run_doc_all(gw_doc, doc_store, budget_chars=DOC_BUDGET,
                                    tau=tau if args.stub else None)
        print("\n" + "=" * 96)
        print("   NO-REGRESSION — the Day 7/8/9 doc nine on the doc store")
        print("=" * 96)
        dgreen = sum(1 for r in doc_rows if r["ok"])
        print(f"{dgreen}/{len(doc_rows)} doc fixtures green; "
              f"Sigma DeltaMRR(D9-D8)={agg['mrr_delta']:+.3f}, "
              f"leaks={agg['expected_left_context']} (additive: the code corpus "
              f"never touches the doc store)")

    if args.report:
        write_report(args.report, rows, doc_rows)
        print(f"\nreport written -> {args.report}")


def write_report(path, code_rows: list[dict], doc_rows: list[dict]) -> None:
    lines = [
        "# Code RAG Day 10 — Doc-RAG vs Code-RAG", "",
        "> Generated by `python tools/retrieval_day10.py`. Retrieval-only "
        "(zero chat calls; rerank/MMR/dedup arithmetic over stored vectors). "
        "`PENDING` = a real gateway is required (whether a chat model turns the "
        "right symbol into the right `file:line` answer — Day 21).", "",
        "## Code corpus — the symbol question set (CA1-CE1)", "",
        "| ID | expected `module#Symbol` | top1 | recall@K | MRR | Δrank | "
        "gate | status / note |", "|---|---|---|---|---|---|---|---|",
    ]
    for r in code_rows:
        d = "+0" if r["delta"] == 0 else (f"{r['delta']:+d}" if r["delta"] else "—")
        top1 = f"{r['top1']:.3f}" if r["top1"] is not None else "∅"
        rec = f"{r['recall']:.0%}" if r["recall"] is not None else "n/a"
        mrr = f"{r['mrr']:.3f}" if r["mrr"] is not None else "n/a"
        exp = "; ".join(r["expected_in_kept"]) or ", ".join(r["d9_ids"][:2]) or "—"
        gate = ("refused" if r["gate_refused"] is True
                else "pass" if r["gate_refused"] is False else "n/a")
        lines.append(f"| {r['id']} | `{exp}` | {top1} | {rec} | {mrr} | {d} | "
                     f"{gate} | {'OK' if r['ok'] else 'FAIL'} — "
                     f"{r['reason'].split(': ', 1)[-1]} |")
    if doc_rows:
        lines += [
            "", "## Doc corpus — no-regression (the Day 7/8/9 nine)", "",
            "| ID | D8 MRR | D9 MRR | Δrank | expected in context | status |",
            "|---|---|---|---|---|---|",
        ]
        for r in doc_rows:
            d8 = f"{r['d8_mrr']:.3f}" if r["d8_mrr"] is not None else "n/a"
            d9 = f"{r['d9_mrr']:.3f}" if r["d9_mrr"] is not None else "n/a"
            d = "+0" if r["delta"] == 0 else (f"{r['delta']:+d}" if r["delta"] else "n/a")
            exp = ", ".join(r["expected_in_kept"]) or "—"
            lines.append(f"| {r['id']} | {d8} | {d9} | {d} | `{exp}` | "
                         f"{'OK' if r['ok'] else 'LEAK'} |")
    lines += [
        "", "## Cost shape (build vs run)", "",
        "| Axis | Doc corpus | Code corpus |", "|---|---|---|",
        "| retrieval unit | chunk (200/40 window) | symbol (AST-bound) |",
        "| expected evidence | `doc_id#heading` | `module#Symbol` |",
        "| build embeds | ~1 per chunk | **one per symbol** |",
        "| run cost | 1 embed + 1 chat | 1 embed + 1 chat (same) |",
        "", "> Δrank is the best-pre-ranked expected symbol's pre→post slot "
        "across the rerank seam. The code corpus adds a corpus, not a subsystem: "
        "`retrieve`/`rerank`/`mmr`/`dedup`/`build_context`/`/rag` are unchanged; "
        "only ingestion (AST), metadata (`symbol/kind/signature/imports`), and "
        "vocabulary (`code_tokens`, `CODE_REWRITES`) are code-specific.",
    ]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
