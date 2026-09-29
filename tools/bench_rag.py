"""Day 21 §5/§7/§8 — the RAG benchmark runner: Day 20's court, real scores.

The benchmark is **Day 20's harness plus a metrics layer plus a pinned
baseline** — nothing new in the answer path. For each golden row it measures the
retrieval (`ai_os/rag_bench.py`) and grades the answer's relationship to the
evidence the run actually showed (`ai_os/semantic_judge.py`), on a chosen store:

    stub (default)          Day-7 mini-corpus + Day-10 symbol tree, 0 chats
    --store data/store.json real doc corpus (Day-6 real store)
    --code-store …          real code corpus (Day-10 store)

The output is the **R-report** — one row per golden entry:

    id · store · K · recall@K · precision@K · MRR · retrieval relevance ·
    cited ⊆ retrieved · citation support · groundedness · coverage · must_not ·
    verdict · owner · tokens · latency

Nothing here decides a new answer: the retrieval cells are pure functions of a
trace (`rag_bench`), and the semantic cells read the run's own context — the
scripted, offline answer exists only to give the deterministic subnet something
to grade, and the real verdicts wait on a real key (the series' oldest rule).

    $ python tools/bench_rag.py --stub            # the whole R-report, offline
    $ python tools/bench_rag.py --stub --baseline data/bench-baseline.json
    $ python tools/bench_rag.py --stub --tau-sweep
    $ python tools/bench_rag.py --fixtures        # the R1–R9 fixture set
    $ python tools/bench_rag.py --fixtures --report docs/evaluations/bench-day21.md
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_os.eval_dataset import (  # noqa: E402
    AGENT_KIND,
    GOLDEN_PATH,
    JUDGE_HUNTING_KIND,
    evidence_text_for,
    load_golden,
)
from ai_os.judge import judge_kind_from_env  # noqa: E402
from ai_os.rag import NOT_COVERED  # noqa: E402
from ai_os.rag_bench import (  # noqa: E402
    build_lexicon,
    control_agrees,
    retrieve_bench,
    tau_sweep,
)
from ai_os.retrieval import code_tokens  # noqa: E402
from ai_os.semantic_judge import (  # noqa: E402
    grade_claims,
    model_explain,
    semantic_owner,
    semantic_verdict,
)
from ai_os.trace import retrieve_trace  # noqa: E402

import tools.code_debug as _cd  # noqa: E402
import tools.rag_debug as _rd  # noqa: E402

BENCH_ENV = "BENCH_MODE"
GOLDEN_SET_ENV = "GOLDEN_SET"

# The pinned K per corpus (a budget artifact — Day 7's `k_from_budget`). The
# doc mini-corpus holds 9 chunks and the symbol tree 14, so these are the
# corpus sizes; the real corpora pin their own K via the CLI.
DOC_K = 9
CODE_K = 14
DOC_BUDGET = 1200
CODE_BUDGET = 4000
# The stub demo gates (Day 7's 0.05, Day 10's 0.75) — exercises, not calibrations.
DOC_TAU_STUB = 0.05
CODE_TAU_STUB = 0.75
FAMILY_E = ("E1", "E2", "CE1")


# ---------------------------------------------------------------- the stores

def build_env(mode: str, *, store: str | None = None,
              code_store: str | None = None, need_code: bool = True) -> dict:
    """The two corpora + gateways + lexicons + the `id -> text` corpus map.

    `need_code=False` skips the code corpus entirely — the real code store is
    Day 10's quota-blocked build, so a `--source doc` real run must not require
    it (the code rows run on the stub's mechanism proof until it lands).
    """
    if mode == "real":
        from ai_os.config import build_gateway_from_env
        from ai_os.rag import load_code_store_from_env, load_store_from_env

        gw_doc = build_gateway_from_env()
        doc = (load_store_from_env(store) if store else load_store_from_env())
        gw_code = gw_doc
        code = ((load_code_store_from_env(code_store) if code_store
                 else load_code_store_from_env()) if need_code else None)
        doc_tau, code_tau = 0.55, 0.55
    else:
        gw_doc = _rd.StubGateway(extra_vocab=_rd._STUB_EXTRA_VOCAB)
        doc = _rd.build_stub_store(gw_doc)
        gw_code = _cd.HashEmbedGateway()
        code = _cd.build_stub_code_store(gw_code)
        doc_tau, code_tau = DOC_TAU_STUB, CODE_TAU_STUB

    env = {
        "mode": mode,
        "doc": {"store": doc, "gateway": gw_doc, "tau": doc_tau,
                "lexicon": build_lexicon(doc), "tokenizer": None},
        "code": {"store": code, "gateway": gw_code, "tau": code_tau,
                 "lexicon": (build_lexicon(code, tokenizer=code_tokens)
                             if code is not None else None),
                 "tokenizer": code_tokens},
    }
    env["corpus"] = {**evidence_text_for(doc), **evidence_text_for(code)}
    # The judge-hunting probes are *fixtures*, not production rows: grade them
    # against a fixed (stub) corpus so their verdicts are mode-independent and
    # byte-reproducible (V2). A real store's chunk text would make a fixture's
    # verdict depend on the store — exactly what a fixture must not do — turning
    # I1's `pending-human` into a `fail` on a store whose page reads differently.
    probe_gw = _rd.StubGateway(extra_vocab=_rd._STUB_EXTRA_VOCAB)
    env["probe_corpus"] = {
        **evidence_text_for(_rd.build_stub_store(probe_gw)),
        **evidence_text_for(_cd.build_stub_code_store(_cd.HashEmbedGateway())),
    }
    return env


def _source(row: dict) -> str:
    return "code" if row.get("source") == "code" else "doc"


# --------------------------------------------------------------- row grading

def _scripted_answer(row: dict, bench: dict) -> tuple[str, list[str]]:
    """The offline answer: the pre-registered claims + citations of evidence the
    retrieval actually kept. It is a *grading target*, not a claim about a real
    model — the real answers are the Day-21 real-key handoff. Citations are the
    **kept** expected docs only (never the full expected set), so
    `cited ⊆ retrieved` is `ok` by construction on every store — the §9 table's
    "ok on kept" — and a retrieval miss is debited to retrieval, not disguised as
    a fabricated citation."""
    if row.get("must_refuse"):
        return NOT_COVERED, []
    claims = list((row.get("expected_answer") or {}).get("claims") or [])
    kept = set(bench["kept_ids"])
    cites = [e for e in row["expected_evidence"] if e in kept]
    text = " ".join(claims) + " " + " ".join(f"({c})" for c in cites)
    return text.strip(), cites


def _agent_row(row: dict, env: dict, *, k_doc: int, k_code: int,
               tau: float | None = None) -> dict:
    """Measure + grade one answerable golden row (retrieval run, 0 chats)."""
    src = _source(row)
    e = env[src]
    k = k_code if src == "code" else k_doc
    budget = CODE_BUDGET if src == "code" else DOC_BUDGET
    eff_tau = e["tau"] if tau is None else tau
    started = time.perf_counter_ns()

    bench = retrieve_bench(
        e["gateway"], e["store"], row["question"],
        expected=row["expected_evidence"],
        relevant_also=row.get("relevant_also") or (),
        k=k, budget_chars=budget, tau=eff_tau, lexicon=e["lexicon"],
        tokenizer=e["tokenizer"])
    answer, citations = _scripted_answer(row, bench)
    sem = grade_claims(
        answer=answer,
        claims=(row.get("expected_answer") or {}).get("claims") or [],
        must_not_assert=(row.get("expected_answer") or {}).get("must_not_assert") or [],
        context_texts=bench["context_texts"], citations=citations,
        corpus_text=env["corpus"])
    latency = round((time.perf_counter_ns() - started) / 1e6, 3)

    cited_ok = set(citations) <= set(bench["retrieved_ids"])
    must_refuse = bool(row.get("must_refuse"))
    # Retrieval verdict: recall@K (when expected) + cited ⊆ retrieved.
    fails: list[str] = []
    if row["expected_evidence"] and not all(bench["expected_in_retrieved"]):
        fails.append("retrieval")
    if not cited_ok:
        fails.append("generation")
    if semantic_verdict(sem) == "fail":
        fails.append(semantic_owner(sem) or "semantic")
    if must_refuse:
        verdict = "n/a (refusal row)"
    else:
        verdict = "pass" if not fails else "fail"
    return _rrow(row, store=src, k=bench["k"], bench=bench, sem=sem,
                 cited_ok=cited_ok, verdict=verdict,
                 owner=(fails[0] if fails else None), latency=latency,
                 answer=answer, citations=citations)


def _probe_row(row: dict, env: dict) -> dict:
    """Grade one judge-hunting row against its injected hostile probe."""
    p = row["probe"]
    evidence = list(p["evidence"])
    citations = list(p["citations"])
    probe_text = env.get("probe_corpus") or env["corpus"]
    context_texts = [probe_text[i] for i in evidence if i in probe_text]
    sem = grade_claims(
        answer=p["answer"],
        claims=(row.get("expected_answer") or {}).get("claims") or [],
        must_not_assert=(row.get("expected_answer") or {}).get("must_not_assert") or [],
        context_texts=context_texts, citations=citations,
        corpus_text=probe_text)
    cited_ok = set(citations) <= set(evidence)
    fails: list[str] = []
    if not cited_ok:
        fails.append("generation")
    if semantic_verdict(sem) == "fail":
        fails.append(semantic_owner(sem) or "semantic")
    if fails:
        verdict = "fail (rejected)"
        owner = fails[0]
    elif row.get("human_review"):
        verdict = "pending-human"
        owner = "reader"
    else:
        verdict = "pass"
        owner = None
    return _rrow(row, store="judge-hunting", k=0, bench=None, sem=sem,
                 cited_ok=cited_ok, verdict=verdict, owner=owner, latency=0.0,
                 answer=p["answer"], citations=citations)


def _rrow(row: dict, *, store: str, k: int, bench: dict | None, sem: dict,
          cited_ok: bool, verdict: str, owner: str | None, latency: float,
          answer: str, citations: list[str]) -> dict:
    return {
        "id": row["id"],
        "family": row.get("family"),
        "kind": row.get("kind"),
        "store": store,
        "k": k,
        "recall_at_k": (bench or {}).get("recall_at_k"),
        "precision_at_k": (bench or {}).get("precision_at_k"),
        "mrr": (bench or {}).get("mrr"),
        "retrieval_relevance": (bench or {}).get("retrieval_relevance"),
        "cited_subset_retrieved": cited_ok,
        "citation_support": sem.get("citation_support_ok"),
        "groundedness": sem.get("groundedness_ok"),
        "coverage": sem.get("coverage"),
        "must_not_ok": sem.get("must_not_ok"),
        "coverage_ok": sem.get("coverage_ok"),
        "verdict": verdict,
        "owner": owner,
        "human_review": bool(row.get("human_review")),
        "semantic": sem,
        "answer": answer,
        "citations": citations,
        "tokens": 0,          # retrieval + deterministic judge: 0 chats
        "latency_ms": latency,
    }


def run_row(row: dict, env: dict, *, k_doc: int, k_code: int,
            tau: float | None = None) -> dict:
    if row.get("kind") == JUDGE_HUNTING_KIND:
        return _probe_row(row, env)
    return _agent_row(row, env, k_doc=k_doc, k_code=k_code, tau=tau)


def run_golden(data: dict, env: dict, *, k_doc: int, k_code: int,
               tau: float | None = None) -> list[dict]:
    return [run_row(r, env, k_doc=k_doc, k_code=k_code, tau=tau)
            for r in data["rows"]]


# --------------------------------------------------------------- tau sweep

def do_tau_sweep(env: dict, *, k_doc: int, k_code: int) -> dict:
    """Real fused top-1 for answerable vs E-family, on the doc corpus."""
    data = load_golden(verify_hash=True)
    rows: list[dict] = []
    for row in data["rows"]:
        if row.get("kind") == JUDGE_HUNTING_KIND or _source(row) != "doc":
            continue
        bench = retrieve_bench(
            env["doc"]["gateway"], env["doc"]["store"], row["question"],
            expected=row["expected_evidence"], k=k_doc,
            budget_chars=DOC_BUDGET, lexicon=env["doc"]["lexicon"])
        rows.append({"id": row["id"], "family": row.get("family"),
                     "top1": bench["top1"]})
    return tau_sweep(rows, family_e=FAMILY_E)


# ------------------------------------------------------------- the report

def _fmt(value, *, pct: bool = False, places: int = 3) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "ok" if value else "FAIL"
    if isinstance(value, str):
        return value
    if pct:
        return f"{value:.0%}"
    return f"{value:.{places}f}"


def render_table(rows: list[dict]) -> None:
    print("\n" + "=" * 138)
    print("   DAY 21 — THE R-REPORT (retrieval measured · semantic tier graded "
          "from the run's own evidence · 0 chats)")
    print("=" * 138)
    print(f"{'id':<5}{'store':<15}{'K':<4}{'recall':<8}{'prec':<7}{'MRR':<7}"
          f"{'rel':<7}{'cited':<7}{'support':<9}{'ground':<8}{'cov':<7}"
          f"{'must_not':<9}{'verdict':<19}{'owner':<16}")
    print("-" * 138)
    for r in rows:
        print(f"{r['id']:<5}{r['store']:<15}{r['k']:<4}"
              f"{_fmt(r['recall_at_k'], pct=True):<8}"
              f"{_fmt(r['precision_at_k']):<7}{_fmt(r['mrr']):<7}"
              f"{_fmt(r['retrieval_relevance']):<7}"
              f"{_fmt(r['cited_subset_retrieved']):<7}"
              f"{_fmt(r['citation_support']):<9}{_fmt(r['groundedness']):<8}"
              f"{_fmt(r['coverage'], pct=True):<7}{_fmt(r['must_not_ok']):<9}"
              f"{r['verdict']:<19}{_fmt(r['owner'], places=0):<16}")
    print("-" * 138)
    green = sum(1 for r in rows if r["verdict"] in ("pass", "fail (rejected)",
                                                    "pending-human",
                                                    "n/a (refusal row)"))
    print(f"rows: {len(rows)}   verdicts issued: {green}/{len(rows)}   "
          f"cost: retrieval bench + deterministic judge = 0 chats, 0 embeds "
          f"(one query embed/row)")
    print("`n/a` is honored (E-family precision; undefined ≠ 0.0); "
          "`pending-human` is the corpus-can-be-wrong boundary (I1) by design.")


def report_markdown(data: dict, rows: list[dict], *, config: dict,
                    sweep: dict | None = None, baseline: dict | None = None,
                    r_rows: list[dict] | None = None) -> str:
    lines = [
        "# RAG Benchmark Day 21 — the R-report (measure retrieval)", "",
        "> Generated by `python tools/bench_rag.py`. Retrieval cells are pure "
        "functions of the trace (`ai_os/rag_bench.py`); the semantic cells read the "
        "run's **own** context (`ai_os/semantic_judge.py`) and never re-answer. "
        "`n/a` is honored where relevance is undefined; `pending-human` is the "
        "corpus-can-be-wrong boundary (I1) by design. Cost: 0 chats for the bench "
        "and the deterministic subnet; only the flagged per-claim lane bills.", "",
        f"dataset hash: `{data['dataset_hash']}`  (pinned `{data['pinned_hash']}`)",
        f"registered: {data['registered_at']}   mode: `{config.get('mode')}`   "
        f"EVAL_JUDGE: `{judge_kind_from_env()}`   "
        f"k: doc={config.get('k_doc')} code={config.get('k_code')}", "",
        "## §9 — one row per golden entry (every cell a real run's)", "",
        "| id | family | store | K | recall@K | precision@K | MRR | relevance | "
        "cited ⊆ retrieved | citation-support | groundedness | coverage | "
        "must_not | verdict | owner | tokens | latency(ms) |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['id']} | {r.get('family')} | {r['store']} | {r['k']} | "
            f"{_fmt(r['recall_at_k'], pct=True)} | {_fmt(r['precision_at_k'])} | "
            f"{_fmt(r['mrr'])} | {_fmt(r['retrieval_relevance'])} | "
            f"{_fmt(r['cited_subset_retrieved'])} | {_fmt(r['citation_support'])} | "
            f"{_fmt(r['groundedness'])} | {_fmt(r['coverage'], pct=True)} | "
            f"{_fmt(r['must_not_ok'])} | **{r['verdict']}** | {_fmt(r['owner'], places=0)} | "
            f"{r['tokens']} | {r['latency_ms']} |")
    lines += [
        "",
        "> `precision@K = |retrieved ∩ (expected ∪ relevant_also)| / K`; the cell is "
        "`n/a` where the relevance set is undefined (E-family refusals; judge-hunting "
        "rows) — never `0.0`, because `0.0` would grade \"retrieved nothing, pass\". "
        "`retrieval relevance` is the mean free `rel()` (Day 9) over the kept "
        "context; the paid cross-encoder lane stays shelved behind its "
        "`NotImplementedError` until this column shows a leak it misses.", "",
    ]

    if baseline:
        lines += [
            "## The pinned baseline (Day 8/9's floor)", "",
            "| id | recall@K | MRR | precision@K | relevance |", "|---|---|---|---|---|",
        ]
        for rid, v in baseline.items():
            lines.append(f"| {rid} | {_fmt(v.get('recall_at_k'), pct=True)} | "
                         f"{_fmt(v.get('mrr'))} | {_fmt(v.get('precision_at_k'))} | "
                         f"{_fmt(v.get('retrieval_relevance'))} |")
        lines.append("")

    if sweep:
        lines += [
            "## §7 — the τ calibration (real fused top-1, answerable vs E-family)", "",
            f"- answerable top-1: `{['%.4f' % s for s in sweep['answerable']]}`",
            f"- E-family top-1: `{['%.4f' % s for s in sweep['e_family']]}`",
            f"- separation (min answerable − max E): **{_fmt(sweep['separation'])}**",
            f"- best τ (refuse every E, no answerable): `{_fmt(sweep['tau'])}`   "
            f"leaks: `{sweep['leaks']}`",
            f"- **finding:** {sweep['verdict']}", "",
        ]

    if r_rows is not None:
        lines += [
            "## §8 — the R-bench fixtures (R1–R9): the properties of measurement", "",
            "| id | what it proves | expected | observed | status |",
            "|---|---|---|---|---|",
        ]
        for r in r_rows:
            lines.append(f"| {r['id']} | {r['proves']} | {r['expected']} | "
                         f"{r['observed']} | {'green' if r['ok'] else '**MOVED**'} |")
        lines += [
            "",
            "**R9 is the control** — the bench installed and unused ≡ production, "
            "byte-for-byte: the bench is not a fixer (V9 on the benchmark surface). "
            "R1–R9 are all `MECH` (0 chats).", "",
        ]

    lines += [
        "## Cost (the price of semantics, quoted before paid)", "",
        f"- retrieval bench + deterministic semantic subnet: **0 chats, 0 embeds** "
        "(one query embed per row)",
        "- flagged per-claim lane (`EVAL_JUDGE=model`): 1 chat per *undecided* claim, "
        "off by default, never flips a `MECH` verdict",
        "- cross-encoder relevance lane: priced, `NotImplementedError`, off", "",
        "**The `PENDING` line, owned:** support-in-meaning and the undeducible claims "
        "are the flagged lane's (off by default); the LOM real verdict is R7 across a "
        "`best_first` sweep; real agent behaviour (steps, tool-selection, recovery) is "
        "Day 22's agent benchmark on the same golden set.", "",
    ]
    return "\n".join(lines) + "\n"


# ================== Day 21 §8 — the R-bench fixtures (R1…R9) ==================
# Written before the runs, Day 20's way. All `MECH` (0 chats): metrics over
# traces, membership over sets, coverage over claims, τ over arithmetic.

FAILS = 0
R_ROWS: list[dict] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    global FAILS
    if not cond:
        FAILS += 1
    print(f"[{'OK  ' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


def rrecord(rid: str, proves: str, expected: str, observed: str, ok: bool) -> None:
    R_ROWS.append({"id": rid, "proves": proves, "expected": expected,
                   "observed": observed, "ok": ok})


def r1_recall(data: dict, env: dict, rows: list[dict]) -> None:
    print("\n== R1 — recall@K (expected ⊆ top-K; leaks reproduced, never hidden)")
    answerable = [r for r in rows if r["kind"] == AGENT_KIND
                  and r["verdict"] != "n/a (refusal row)"]
    ids = {r["id"] for r in answerable}
    expect = {row["id"] for row in data["rows"] if row["kind"] == AGENT_KIND
              and row["expected_evidence"]}
    ok = ids == expect and all(r["recall_at_k"] == 1.0 for r in answerable)
    detail = (f"{sum(1 for r in answerable if r['recall_at_k'] == 1.0)}/"
              f"{len(answerable)} rows expected ⊆ top-K")
    check("every answerable golden row surfaces its expected docs on the stub", ok, detail)
    rrecord("R1", "recall@K = expected ⊆ top-K", "all answerable rows green",
            detail, ok)


def r2_precision(data: dict, rows: list[dict]) -> None:
    print("\n== R2 — precision@K (relevance set → a number; else the honest `n/a`)")
    by_id = {r["id"]: r for r in rows}
    rel_rows = [row["id"] for row in data["rows"]
                if row.get("relevant_also")]
    have = all(by_id[i]["precision_at_k"] is not None for i in rel_rows)
    check("rows with a relevance set produce a precision number", have,
          f"{len(rel_rows)} rows with relevant_also")
    e_na = all(by_id[i]["precision_at_k"] is None for i in FAMILY_E)
    hunt_na = all(by_id[row["id"]]["precision_at_k"] is None
                  for row in data["rows"] if row["kind"] == JUDGE_HUNTING_KIND)
    check("E-family precision is `n/a` by construction (nothing is relevant)", e_na)
    check("judge-hunting rows' precision is `n/a` (relevance is the trap)", hunt_na)
    d1 = by_id["D1"]["precision_at_k"]
    detail = f"D1 precision@{by_id['D1']['k']} = {_fmt(d1)}"
    check("D1 — a recall/MRR-perfect row gets its precision number (the D exhibit)",
          d1 is not None, detail)
    ok = have and e_na and hunt_na and d1 is not None
    rrecord("R2", "precision@K over expected ∪ relevant_also; `n/a` where undefined",
            "a number with a relevance set; n/a without", detail, ok)


def r3_mrr(data: dict, env: dict, rows: list[dict]) -> None:
    print("\n== R3 — MRR (first-expected rank; the bench ≡ the trace)")
    sample = ["A1", "B1", "D1", "CA1", "CB2"]
    ok, where = True, ""
    for rid in sample:
        row = data["by_id"][rid]
        r = next(x for x in rows if x["id"] == rid)
        e = env[_source(row)]
        trace = retrieve_trace(e["gateway"], e["store"], row["question"],
                               expected=row["expected_evidence"],
                               k=r["k"], budget_chars=(
                                   CODE_BUDGET if _source(row) == "code" else DOC_BUDGET))
        exp_mrr = trace["mrr"] if row["expected_evidence"] else None
        if r["mrr"] != exp_mrr:
            ok, where = False, f"{rid}: {r['mrr']} != {exp_mrr}"
    check("the bench's MRR is the trace's MRR (the metric is inherited, not new)",
          ok, where or f"{len(sample)} rows agree")
    rrecord("R3", "MRR = 1/first-expected-rank", "bench ≡ trace",
            "agree" if ok else where, ok)


def r4_relevance(data: dict, env: dict, rows: list[dict]) -> None:
    print("\n== R4 — retrieval relevance (the free rel() column; the paid lane OFF)")
    values = [r["retrieval_relevance"] for r in rows
              if r["kind"] == AGENT_KIND and r["retrieval_relevance"] is not None]
    in_band = all(0.0 <= v <= 1.0 for v in values) and bool(values)
    check("the free rel() column is measured for every kept context", in_band,
          f"{len(values)} rows in [0, 1]")
    try:
        from ai_os.rerank import relevance_cross_encoder
        relevance_cross_encoder(None, "q", None)
        raised = False
    except NotImplementedError:
        raised = True
    check("the paid cross-encoder lane still refuses to bill (NotImplementedError)",
          raised)
    rrecord("R4", "retrieval relevance = mean rel() over the kept context",
            "free signal measured; paid lane raises",
            f"{len(values)} rows measured; paid lane raises={raised}",
            in_band and raised)


def r5_citations(rows: list[dict]) -> None:
    print("\n== R5 — citation correctness (cited ⊆ retrieved · and the support half)")
    by_id = {r["id"]: r for r in rows}
    g = by_id["G"]
    check("G fails `cited ⊆ retrieved` (a fabricated citation, Day 6's invariant)",
          g["cited_subset_retrieved"] is False)
    k1 = by_id["K1"]
    ok = (k1["cited_subset_retrieved"] is True
          and k1["citation_support"] is False)
    check("K1 passes `cited ⊆ retrieved` yet FAILS citation support "
          "(the tier's reason to exist)", ok,
          f"cited={k1['cited_subset_retrieved']} support={k1['citation_support']}")
    rrecord("R5", "citation correctness: membership + support-in-meaning",
            "G fails membership; K1 fails support while passing membership",
            f"G={g['cited_subset_retrieved']} K1 support={k1['citation_support']}",
            g["cited_subset_retrieved"] is False and ok)


def r6_groundedness(data: dict, env: dict) -> None:
    print("\n== R6 — groundedness (claims ⊆ shown evidence)")
    good = grade_claims(answer="a checkout is created with checkoutCreate",
                        claims=["a checkout is created with checkoutCreate"],
                        context_texts=[env["corpus"]["quickstart/api#create-a-checkout"]])
    check("a claim present in the shown context is grounded", good["groundedness_ok"] is True)
    bad = grade_claims(answer="a checkout is created with checkoutCreate",
                       claims=["a checkout is created with checkoutCreate"],
                       context_texts=[env["corpus"]["developer/order/overview#lifecycle"]])
    check("the same claim with no line of evidence in the context is a FAIL "
          "(a hallucination by definition)", bad["groundedness_ok"] is False,
          f"ungrounded={bad['ungrounded']}")
    rrecord("R6", "groundedness: every asserted claim traces to shown evidence",
            "grounded passes; ungrounded fails",
            f"good={good['groundedness_ok']} bad={bad['groundedness_ok']}",
            good["groundedness_ok"] is True and bad["groundedness_ok"] is False)


def r7_coverage(rows: list[dict]) -> None:
    print("\n== R7 — answer relevance (claim coverage; a must_not hit always fails)")
    by_id = {r["id"]: r for r in rows}
    j1 = by_id["J1"]
    check("J1 asserts a `must_not_assert` claim → coverage failure of class forbidden",
          j1["must_not_ok"] is False, f"forbidden hit: must_not_ok={j1['must_not_ok']}")
    a1 = by_id["A1"]
    check("A1 asserts its mandatory claims → coverage ok",
          a1["coverage_ok"] is True, f"coverage={_fmt(a1['coverage'])}")
    f = by_id["F"]
    check("the F-row (fluent but wrong) fails coverage / must_not — prose buys no pass",
          f["verdict"] == "fail (rejected)",
          f"failed cells: coverage={f['coverage_ok']} must_not={f['must_not_ok']}")
    rrecord("R7", "answer relevance = coverage (must_not hit always fails)",
            "J1 fails; A1 passes; F rejected",
            f"J1 must_not={j1['must_not_ok']} A1 cov={_fmt(a1['coverage'])}",
            j1["must_not_ok"] is False and a1["coverage_ok"] is True
            and f["verdict"] == "fail (rejected)")


def r8_tau(env: dict, *, k_doc: int) -> None:
    print("\n== R8 — τ calibration (real fused top-1, answerable vs E-family)")
    sweep = do_tau_sweep(env, k_doc=k_doc, k_code=CODE_K)
    has = sweep["min_answerable"] is not None and sweep["max_e"] is not None
    check("the sweep reports the answerable/E top-1 numbers and a decision",
          has, f"sep={_fmt(sweep['separation'])} → {sweep['verdict']}")
    rrecord("R8", "τ calibrated on real scores; the gate-flip decision",
            "a separation, or the documented overlap",
            f"sep={_fmt(sweep['separation'])}", has)


def r9_control(data: dict, env: dict, rows: list[dict], *, k_doc: int,
               k_code: int) -> None:
    print("\n== R9 — the control (bench off ≡ days-without; the bench doesn't fix answers)")
    ok, where = True, ""
    for rid in ("A1", "B1", "D1", "CA1"):
        row = data["by_id"][rid]
        r = next(x for x in rows if x["id"] == rid)
        e = env[_source(row)]
        trace = retrieve_trace(e["gateway"], e["store"], row["question"],
                               expected=row["expected_evidence"], k=r["k"],
                               budget_chars=(CODE_BUDGET if _source(row) == "code"
                                             else DOC_BUDGET))
        bench = retrieve_bench(e["gateway"], e["store"], row["question"],
                               expected=row["expected_evidence"],
                               relevant_also=row.get("relevant_also") or (),
                               k=r["k"], budget_chars=(
                                   CODE_BUDGET if _source(row) == "code" else DOC_BUDGET),
                               lexicon=e["lexicon"], tokenizer=e["tokenizer"])
        if not control_agrees(bench, trace):
            ok, where = False, rid
    check("the bench's retrieval ≡ the same row's production trace (byte-for-byte)",
          ok, where)
    before = {k: len(env[k]["store"]) for k in ("doc", "code")}
    run_golden(data, env, k_doc=k_doc, k_code=k_code)
    after = {k: len(env[k]["store"]) for k in ("doc", "code")}
    check("running the bench grows no store and adds no corpus id", before == after,
          f"{before} -> {after}")
    rrecord("R9", "the bench is not a fixer", "bench off ≡ production",
            "identical" if ok else f"drifted at {where}", ok and before == after)


def render_r_table() -> None:
    print("\n" + "=" * 108)
    print("   DAY 21 §8 — THE R-BENCH FIXTURES (R1–R9): the properties of measurement")
    print("=" * 108)
    print(f"{'id':<8}{'what it proves':<42}{'expected':<26}{'observed':<20}{'status':<6}")
    print("-" * 108)
    for r in R_ROWS:
        print(f"{r['id']:<8}{r['proves'][:40]:<42}{r['expected'][:24]:<26}"
              f"{str(r['observed'])[:18]:<20}{'green' if r['ok'] else 'MOVED':<6}")
    print("-" * 108)
    moved = [r["id"] for r in R_ROWS if not r["ok"]]
    print(f"fixtures: {len(R_ROWS)}   moved: {len(moved)}"
          + (f" → {moved}" if moved else ""))
    print("R1–R9 are 0 chats; the semantic residue (support-in-meaning, undecided "
          "claims, LOM) carries its owners.")


def run_fixture_suite(*, report_path: str | None = None) -> int:
    global FAILS
    FAILS = 0
    env = build_env("stub")
    data = load_golden(verify_hash=True, sources=True,
                       corpus_text=env["corpus"])
    rows = run_golden(data, env, k_doc=DOC_K, k_code=CODE_K)

    r1_recall(data, env, rows)
    r2_precision(data, rows)
    r3_mrr(data, env, rows)
    r4_relevance(data, env, rows)
    r5_citations(rows)
    r6_groundedness(data, env)
    r7_coverage(rows)
    r8_tau(env, k_doc=DOC_K)
    r9_control(data, env, rows, k_doc=DOC_K, k_code=CODE_K)

    render_r_table()
    if report_path:
        sweep = do_tau_sweep(env, k_doc=DOC_K, k_code=CODE_K)
        Path(report_path).write_text(
            report_markdown(data, rows, config={"mode": "stub", "k_doc": DOC_K,
                                                "k_code": CODE_K},
                            sweep=sweep, r_rows=R_ROWS),
            encoding="utf-8")
        print(f"report written → {report_path}")
    print("\n" + ("ALL DAY 21 (R1–R9) CHECKS PASSED" if not FAILS
                  else f"{FAILS} CHECK(S) FAILED"))
    return 1 if FAILS else 0


# --------------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stub", action="store_true",
                    help="offline stub stores (default)")
    ap.add_argument("--real", action="store_true",
                    help="real stores + gateway from .env (Day-21 handoff)")
    ap.add_argument("--store", default=None, help="doc store path (real mode)")
    ap.add_argument("--code-store", default=None, help="code store path (real mode)")
    ap.add_argument("--golden", default=os.getenv(GOLDEN_SET_ENV) or str(GOLDEN_PATH))
    ap.add_argument("--k-doc", type=int, default=None, help=f"doc pin (default {DOC_K})")
    ap.add_argument("--k-code", type=int, default=None, help=f"code pin (default {CODE_K})")
    ap.add_argument("--tau", type=float, default=None)
    ap.add_argument("--tau-sweep", action="store_true", help="the §7 calibration sweep")
    ap.add_argument("--baseline", default=None, metavar="PATH",
                    help="write (or read, if it exists) the pinned baseline")
    ap.add_argument("--report", default=None, metavar="PATH")
    ap.add_argument("--fixtures", action="store_true",
                    help="run the Day 21 §8 R-bench set (R1–R9) and exit")
    ap.add_argument("--source", choices=["all", "doc", "code"], default="all",
                    help="restrict the R-report to one corpus; `--source doc` runs "
                         "the real doc store without the code store (Day 10's "
                         "quota-blocked build)")
    args = ap.parse_args()

    if args.fixtures:
        sys.exit(run_fixture_suite(report_path=args.report))

    mode = "real" if args.real else "stub"
    k_doc = args.k_doc if args.k_doc is not None else (9 if mode == "stub" else DOC_K)
    k_code = args.k_code if args.k_code is not None else (14 if mode == "stub" else CODE_K)
    env = build_env(mode, store=args.store, code_store=args.code_store,
                    need_code=args.source != "doc")

    data = load_golden(args.golden, verify_hash=True, sources=True,
                       corpus_text=env["corpus"] if mode == "stub" else None)
    if args.source != "all":
        data = {**data, "rows": [r for r in data["rows"]
                                 if _source(r) == args.source]}
        data["by_id"] = {r["id"]: r for r in data["rows"]}
    print(f"golden: {len(data['rows'])} rows  hash={data['dataset_hash'][:12]}…  "
          f"mode={mode}  source={args.source}  k_doc={k_doc} k_code={k_code}")

    rows = run_golden(data, env, k_doc=k_doc, k_code=k_code, tau=args.tau)
    render_table(rows)

    sweep = do_tau_sweep(env, k_doc=k_doc, k_code=k_code) if args.tau_sweep else None
    if sweep:
        print(f"\nτ sweep: answerable {['%.3f' % s for s in sweep['answerable']]}  "
              f"E {['%.3f' % s for s in sweep['e_family']]}  "
              f"separation {_fmt(sweep['separation'])}  → {sweep['verdict']}")

    # The baseline: read it if present (the floor), else write the pin.
    baseline = None
    if args.baseline:
        p = Path(args.baseline)
        if p.exists():
            baseline = json.loads(p.read_text(encoding="utf-8"))
            print(f"baseline: {p} ({len(baseline)} rows)")
        else:
            baseline = {r["id"]: {"recall_at_k": r["recall_at_k"], "mrr": r["mrr"],
                                  "precision_at_k": r["precision_at_k"],
                                  "retrieval_relevance": r["retrieval_relevance"]}
                        for r in rows}
            p.write_text(json.dumps(baseline, indent=2, sort_keys=True),
                         encoding="utf-8")
            print(f"baseline pinned → {p} ({len(baseline)} rows)")
            baseline = None

    if args.report:
        Path(args.report).write_text(
            report_markdown(data, rows, config={"mode": mode, "k_doc": k_doc,
                                                "k_code": k_code},
                            sweep=sweep, baseline=baseline),
            encoding="utf-8")
        print(f"report written → {args.report}")


if __name__ == "__main__":
    main()
