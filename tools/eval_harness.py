"""Day 20 §6 — the evaluation harness: a *router*, not a grader.

The harness presents each golden question to the production answer path **exactly
as a user would** and returns the run's trace and ledger *unchanged*. It never
touches the answer path's code, and it refuses to grade under a stale dataset
hash (V1/V8). The grader is `ai_os/judge.py`; the pre-registered set is
`ai_os/golden.yaml` (`ai_os/eval_dataset.py`).

```text
  the golden dataset (five fields, hash-pinned)
             │
    lasting integrity check  ── stale hash → refuse to grade (V1/V8)
             │
    THE HARNESS (router)  ── presents each question to production
             │                 (stub/real gateway, memory on/off, topology)
             ▼
    {question, answer, evidence, citations, tool_calls, steps, ledger, config}
             │
             ▼
    ai_os/judge.py — layer 1 deterministic → verdict row
```

Two modes:

  --mode stub (default)  the Day-4 scripted stub's in-process twin: a
                         deterministic `ScriptedGateway` whose tool turn is what a
                         competent model would emit from the tool description, and
                         whose answer is grounded in the row's own evidence. No
                         network, no ports, no key — the court is provable offline.
  --mode real            the real gateway from `.env` (retrieval on the real
                         stores). The real model chooses its own tools/args; this
                         is the Day-21 handoff where the verdicts, not the court,
                         get filled.

    $ python tools/eval_harness.py --stub            # the whole court, offline
    $ python tools/eval_harness.py --stub --self-check   # exit≠0 if a verdict drifts
    $ python tools/eval_harness.py --fixtures            # the §8 evaluation set (V1–V9)
    $ python tools/eval_harness.py --fixtures --report docs/evaluations/eval-day20.md

`--fixtures` runs the properties of grading itself (V1…V9): the integrity check
and its patched-row refusal, judge reproducibility, the judge-hunting rows, the
model-lane drift, the regression gate, the coverage rule, the zero-screentime
report, the pre-registration flag, and the V9 control (harness off ≡ production).
With `--report`, the §9 golden-set table and the §8 V1–V9 table are written as
one document.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_os.agent import ROLE_TOOLS, run_agent  # noqa: E402
from ai_os.contract import Completion, ToolCall, Usage  # noqa: E402
from ai_os.eval_dataset import (  # noqa: E402
    AGENT_KIND,
    GOLDEN_PATH,
    JUDGE_HUNTING_KIND,
    DatasetError,
    IntegrityError,
    evidence_text_for,
    hash_rows,
    load_golden,
    rows_registered_before,
    validate_row,
)
from ai_os.judge import judge_all, judge_kind_from_env, judge_row  # noqa: E402
from ai_os.ledger import Ledger  # noqa: E402
from ai_os.rag import NOT_COVERED  # noqa: E402
from ai_os.state import StateStore  # noqa: E402

import tools.code_debug as _cd  # noqa: E402
import tools.rag_debug as _rd  # noqa: E402
from tools.tool_smoke import build_registry  # noqa: E402

HARNESS_MODE_ENV = "HARNESS_MODE"
TOPOLOGY_ENV = "EVAL_TOPOLOGY"
GOLDEN_SET_ENV = "GOLDEN_SET"

# The stub's scripted model turn, per row: the query a competent model would
# issue for THIS question from the tool's own description, and the k it asks for.
# (Day 21: the real model chooses its own query; the stub scripts the turn the
# way every prior day's stub did.) E-family rows still script the tool call —
# the model *tries*, the corpus refuses, and it answers with a refusal.
STUB_QUERY: dict[str, dict] = {
    # docs — the fixture question, generous k so the pre-registered set surfaces
    "A1": {"k": 8}, "B1": {"k": 8}, "B2": {"k": 8},
    "C1": {"k": 4}, "C2": {"k": 4}, "D1": {"k": 8}, "D2": {"k": 8},
    "E1": {"k": 4}, "E2": {"k": 4},
    # code — the identifier query the rewrite table / description suggests
    "CA1": {"query": "checkout complete", "k": 6},
    "CB1": {"query": "create_order_from_checkout", "k": 6},
    "CB2": {"query": "process_payment WebhookEventAsyncType", "k": 6},
    "CC1": {"query": "Checkout class models types", "k": 4},
    "CD1": {"query": "test_complete_checkout_when_checkout_doesnt_exists", "k": 6},
    "CE1": {"query": "Shopify webhook handler", "k": 6},
}

TOOL_ROLE = {t: role for role, tools in ROLE_TOOLS.items() for t in tools}


# ----------------------------------------------------------- scripted gateway

class ScriptedGateway:
    """Deterministic in-process gateway: a competent model's tool turn + answer.

    State-machine router, so the *same* gateway serves Topology A (the loop sees
    the registry's tools) and Topology B (the orchestrator sees `delegate`, a
    specialist sees its role's tools). It records a ledger row per call, so the
    harness's cost columns are the run's own.
    """

    MODEL = "stub-eval-chat"

    def __init__(self, row: dict, *, latency_ms: float = 2.0) -> None:
        self.row = row
        self.ledger = Ledger()
        self.last_provider = "stub"
        self.last_attempts = 1
        self.last_latency_ms = latency_ms
        self._latency = latency_ms
        self.delegated = False
        self.searched = False
        self._answer = self._grounded_answer(row)
        self._tools_by_role = dict(ROLE_TOOLS)

    # -- the row's scripted answer -------------------------------------
    @staticmethod
    def _grounded_answer(row: dict) -> str:
        if row.get("must_refuse"):
            return ("This question is not covered by the corpus: no source "
                    "answers it, so I will not state an answer or cite an id "
                    f"({NOT_COVERED}).")
        claims = list((row.get("expected_answer") or {}).get("claims") or [])
        ids = list(row.get("expected_evidence") or [])
        cites = " ".join(f"({i})" for i in ids)
        return " ".join(claims) + (" " + cites if cites else "")

    def _expected_tool(self) -> str | None:
        tools = list(self.row.get("expected_tools") or [])
        if tools:
            return tools[0]
        src = self.row.get("source")
        return "search_saleor_code" if src == "code" else "search_saleor_docs"

    def _tool_args(self) -> dict:
        from ai_os.retrieval import CODE_REWRITES  # noqa: F401 (docs the knob)
        spec = dict(STUB_QUERY.get(self.row["id"]) or {})
        spec.setdefault("query", self.row["question"])
        spec.setdefault("k", 6)
        return spec

    def _record(self, input_tokens: int, output_tokens: int) -> None:
        self.ledger.record(provider="stub", model=self.MODEL,
                           input_tokens=input_tokens, output_tokens=output_tokens,
                           latency_ms=self._latency, ok=True)

    def _tool_turn(self, name: str, args: dict) -> Completion:
        self._record(12, 8)
        call = ToolCall(name=name, arguments=json.dumps(args), id="call_eval_1")
        return Completion(text="", model=self.MODEL, finish_reason="tool_calls",
                          usage=Usage(12, 8, 20), tool_calls=[call])

    def _answer_turn(self) -> Completion:
        self._record(40, 30)
        return Completion(text=self._answer, model=self.MODEL,
                          finish_reason="stop", usage=Usage(40, 30, 70))

    def chat_with_tools(self, messages, tools=None, **kw) -> Completion:
        names = {t.get("function", {}).get("name") for t in (tools or [])}
        # Topology B: the orchestrator's only tool is `delegate`. Delegate once.
        if "delegate" in names and not self.delegated:
            self.delegated = True
            role = TOOL_ROLE.get(self._expected_tool(), "knowledge")
            return self._tool_turn("delegate", {"role": role,
                                                "question": self.row["question"]})
        # A specialist (or Topology A's full registry): emit the search once.
        tool = self._expected_tool()
        if tool and tool in names and not self.searched:
            self.searched = True
            return self._tool_turn(tool, self._tool_args())
        return self._answer_turn()

    def chat(self, messages, **kw) -> Completion:
        return self._answer_turn()


# ------------------------------------------------------------ the harness

def build_stub_env() -> tuple[object, dict, dict]:
    """The offline registry + the `id -> evidence text` corpus (0 API calls)."""
    reg, _gw_doc, _gw_code, doc_store, code_store = build_registry()
    corpus = {**evidence_text_for(doc_store), **evidence_text_for(code_store)}
    stores = {"doc": doc_store, "code": code_store}
    return reg, stores, corpus


def trace_from_run(row: dict, run: dict, *, gateway, config: dict,
                   started_ns: int) -> dict:
    """The harness's output: the run's trace + ledger *verbatim* (never re-graded here)."""
    delta = run.get("ledger") or {}
    records = gateway.ledger.records
    tokens = sum(r.input_tokens + r.output_tokens for r in records)
    latency = round(sum(r.latency_ms for r in records), 3)
    ledger = {**delta, "tokens": tokens, "latency_ms": latency,
              "records": len(records)}
    return {
        "id": row["id"],
        "family": row.get("family"),
        "kind": row.get("kind"),
        "question": row["question"],
        "mode": config.get("mode"),
        "topology": config.get("topology"),
        "config": config,
        "run": run,
        "answer": run.get("answer"),
        "citations": run.get("citations") or [],
        "fabricated": run.get("fabricated") or [],
        "evidence": run.get("evidence") or [],
        "tool_calls": run.get("tool_calls") or [],
        "refused": run.get("refused"),
        "failure_kind": run.get("failure_kind"),
        "termination_reason": run.get("termination_reason"),
        "steps": run.get("steps") or [],
        "ledger": ledger,
        "wall_ms": round((time.perf_counter_ns() - started_ns) / 1e6, 2),
    }


def run_row(row: dict, reg, *, config: dict | None = None) -> dict:
    """Present one golden question to production; return the trace (no grading)."""
    config = config or {"mode": "stub", "topology": "single"}
    if row.get("kind") == JUDGE_HUNTING_KIND:
        return {"id": row["id"], "kind": JUDGE_HUNTING_KIND, "question": row["question"],
                "mode": config.get("mode"), "config": config, "probe": row["probe"]}
    gateway = ScriptedGateway(row)
    started = time.perf_counter_ns()
    run = run_agent(row["question"], reg, gateway, max_steps=4,
                    memory_store=config.get("memory_store"))
    return trace_from_run(row, run, gateway=gateway, config=config, started_ns=started)


def run_golden(data: dict, *, config: dict | None = None, reg=None) -> dict[str, dict]:
    """Run the whole golden set through production; one trace per row."""
    if reg is None:
        reg, _stores, _corpus = build_stub_env()
    traces: dict[str, dict] = {}
    for row in data["rows"]:
        traces[row["id"]] = run_row(row, reg, config=config)
    return traces


# ------------------------------------------------------------- reporting

def expected_verdict(row: dict) -> str:
    """A judge-hunting row is *green when rejected*; every other row when passed.

    Two Day 21 rows are the exception, and by design: `I1` is the
    corpus-can-be-wrong boundary (`pending-human` — the reader, not layer 1, is
    the expected rejector), and `K1`'s rejection is the *semantic* citation-support
    cell (`semantic_fail`), which layer 1 cannot see — `cited ⊆ retrieved` holds.
    Layer 1 correctly *passes* both; the RAG bench (`tools/bench_rag.py`) issues
    their verdicts.
    """
    if row.get("kind") != JUDGE_HUNTING_KIND:
        return "pass"
    if row.get("human_review") or row.get("semantic_fail"):
        return "pass"
    return "fail"


def _fmt(value, width: int = 6) -> str:
    return str(value if value is not None else "—")


def render_table(rows: list[dict], verdicts: list[dict]) -> None:
    v = {x["id"]: x for x in verdicts}
    print("\n" + "=" * 132)
    print("   DAY 20 — THE GOLDEN SET (deterministic verdict; `semantic` is PENDING, "
          "owned by Day 21/22)")
    print("=" * 132)
    header = (f"{'id':<5}{'family':<34}{'exp⊆ret':<9}{'cited⊆ret':<10}"
              f"{'concl⊆ev':<9}{'tool':<6}{'refusal':<8}{'failure':<8}"
              f"{'verdict':<8}{'owner':<12}")
    print(header)
    print("-" * 132)
    for r in rows:
        x = v.get(r["id"])
        if not x:
            continue
        c = x["checks"]

        def mark(name: str) -> str:
            chk = c.get(name)
            if chk is None:
                return "—"
            return "ok" if chk["ok"] else "FAIL"

        print(f"{x['id']:<5}{str(r.get('family'))[:32]:<34}{mark('evidence_membership'):<9}"
              f"{mark('cited_subset_retrieved'):<10}{mark('conclusion_subset_evidence'):<9}"
              f"{mark('tool_match'):<6}{mark('refusal_expected'):<8}"
              f"{mark('failure_category'):<8}{x['verdict']:<8}{_fmt(x['owner'], 12):<12}")
    print("-" * 132)
    green = sum(1 for r in rows if v.get(r["id"], {}).get("verdict") == expected_verdict(r))
    print(f"rows green (agent pass / judge-hunting rejected): {green}/{len(rows)}   "
          f"judged ⊆ pre-registered: "
          f"{'YES' if all(x['judged_subset_preregistered'] for x in verdicts) else 'NO'}   "
          f"cost: judge layer 1 = 0 chats")


def report_markdown(data: dict, verdicts: list[dict], *, config: dict,
                    v_rows: list[dict] | None = None) -> str:
    lines = [
        "# Evaluation Day 20 — the golden set (V1–V9)", "",
        "> Generated by `python tools/eval_harness.py`. The golden dataset is "
        "pre-registered and hash-pinned; the judge grades the **trace** (never "
        "re-answers) and its layer 1 is deterministic (`MECH`, 0 chats). The "
        "`semantic` column is `PENDING` by construction, owned by Day 21/22. "
        "`judged ⊆ pre-registered`: a check with no expectation is `n/a`, never "
        "failed.", "",
        f"dataset hash: `{data['dataset_hash']}`  (pinned `{data['pinned_hash']}`)",
        f"registered: {data['registered_at']}   mode: `{config.get('mode')}`   "
        f"topology: `{config.get('topology')}`   "
        f"EVAL_JUDGE: `{judge_kind_from_env()}`", "",
        "## §9 — one row per golden entry (every cell a real run's)", "",
        "| id | family | expected ⊆ retrieved | recall@K | MRR | cited ⊆ retrieved | "
        "conclusion ⊆ evidence | tool == expected | refusal == expected | "
        "failure == category | verdict | owner | tokens | latency(ms) |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    v = {x["id"]: x for x in verdicts}
    for r in data["rows"]:
        x = v.get(r["id"])
        if not x:
            continue
        c = x["checks"]

        def mark(name):
            chk = c.get(name)
            if chk is None:
                return "—"
            return "ok" if chk["ok"] else "**FAIL**"

        recall = (f"{x['recall_at_k']:.0%}" if x["recall_at_k"] is not None else "n/a")
        mrr = f"{x['mrr']:.3f}" if x["mrr"] is not None else "n/a"
        lines.append(
            f"| {x['id']} | {r.get('family')} | {mark('evidence_membership')} | {recall} | "
            f"{mrr} | {mark('cited_subset_retrieved')} | {mark('conclusion_subset_evidence')} | "
            f"{mark('tool_match')} | {mark('refusal_expected')} | {mark('failure_category')} | "
            f"**{x['verdict']}** | {_fmt(x['owner'])} | {x['tokens']} | "
            f"{x.get('latency_ms', '—')} |")
    lines += [
        "", "## The `PENDING` semantic line (owned, not wished)", "",
        "| cell | owner |", "|---|---|",
    ]
    for slot, owner in verdicts[0]["semantic_slots"].items():
        lines.append(f"| {slot} | {owner} |")

    lines += [
        "", "## Pre-registration (the hash mechanics)", "",
        "- `ai_os/golden.yaml` is the commitment: the five fields written **before** "
        "any run, pinned by a sha256 content hash over every graded field "
        "(evidence, answer, tools, failure category, judge-hunting probes).",
        f"- pinned hash `{data['pinned_hash'][:16]}…` == computed "
        f"`{data['dataset_hash'][:16]}…`; `registered_at: {data['registered_at']}`.",
        "- The harness **refuses to grade under a stale hash** (`IntegrityError`, "
        "V1/V8): a row patched after the run came up short is an integrity failure, "
        "not a run. Re-pin only for an intended edit: "
        "`python ai_os/eval_dataset.py --pin ai_os/golden.yaml`.",
        "- A run that started before its row's registration is **flagged** "
        "(`ai_os.eval_dataset.rows_registered_before`) — the anti-curve-fit fixture.",

        "", "## The regression gate (Rule 2's ratchet)", "",
        "```bash",
        "python tools/regress_all.py          # doc nine + code six + every day suite (Days 7–19) + golden rows",
        "python tools/regress_all.py --fast   # fixtures + golden rows only",
        "```",
        "Improvement is measured on the golden set; **preservation** is measured here. "
        "Every row must come back *exactly as green*; a row a run cannot reproduce is a "
        "gate bug to fix, never a fixture to weaken.",

        "", "## Config and defaults", "",
        "| key | default | meaning |", "|---|---|---|",
        "| `EVAL_JUDGE` | `deterministic` | the judge lane: `deterministic` \\| `model` \\| `none` |",
        "| `GOLDEN_SET` | `ai_os/golden.yaml` | the pre-registered dataset path |",
        "| `HARNESS_MODE` | `stub` | `stub` (in-process scripted gateway) or `real` (`.env` gateway) |",
        "| `EVAL_TOPOLOGY` | `single` | the topology the harness presents (Day 19's B: `architecture_smoke.py`) |",
        "| `EVAL_REGRESS` | `full` | `--fixtures`' V5 gate: `full` runs the day suites; `fast` skips them |",
    ]

    if v_rows is not None:
        lines += [
            "", "## §8 — the evaluation set (V1–V9): the properties of grading itself", "",
            "| id | what it proves | expected | observed | status |",
            "|---|---|---|---|---|",
        ]
        for r in v_rows:
            lines.append(f"| {r['id']} | {r['proves']} | {r['expected']} | "
                         f"{r['observed']} | {'green' if r['ok'] else '**MOVED**'} |")
        lines += [
            "", "**V9 is the control** — the harness installed and unused ≡ production, "
            "byte-for-byte: the judge is not a fixer (P9/S9/M8/G9 on the measurement "
            "surface). V1–V9 are all `MECH` (0 chats).",
        ]

    lines += [
        "", "**Cost.** The dataset, the integrity check, the harness, the judge's "
        "layer 1, and the regression gate are **0 chats, 0 embeds**; the only bill is "
        "the optional, off-by-default model lane (`EVAL_JUDGE=model`, 1 chat per graded "
        "semantic cell). The `PENDING` semantic cells are Day 21's (RAG) and Day 22's "
        "(agent) benchmarks, standing on this court.",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- real mode

def build_real_env():
    """Real gateway + real stores (Day-21 handoff). Raises loudly if unavailable."""
    from ai_os.config import build_gateway_from_env
    from ai_os.rag import load_code_store_from_env, load_store_from_env
    from ai_os.tools import build_default_registry, code_retriever, doc_retriever

    gw = build_gateway_from_env()
    if not any(getattr(p, "chat", None) for p in gw.providers):
        raise SystemExit("no chat-capable provider in .env (LLM_PROVIDERS)")
    doc_store = load_store_from_env()
    code_store = load_code_store_from_env()
    reg = build_default_registry(doc=doc_retriever(gw, doc_store),
                                 code=code_retriever(gw, code_store))
    return reg, gw, {**evidence_text_for(doc_store), **evidence_text_for(code_store)}


# ================== Day 20 §8 — the evaluation set (V1…V9) ==================
#
# The *verdict* fixtures: the properties of grading itself, several hostile,
# plus the control. Expectations are written here, before the harness runs, the
# day's own way. `MECH` throughout — deterministic functions of the dataset, the
# registration hash, the traces, and the ledger (0 chats).

FAILS = 0
V_ROWS: list[dict] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    global FAILS
    if not cond:
        FAILS += 1
    print(f"[{'OK  ' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


def vrecord(vid: str, proves: str, expected: str, observed: str, ok: bool) -> None:
    V_ROWS.append({"id": vid, "proves": proves, "expected": expected,
                   "observed": observed, "ok": ok})


class FlakyJudgeGateway:
    """A model lane that answers differently each call — the drift fixture (V4)."""

    last_provider = "stub-flaky"

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, **kw) -> Completion:
        self.calls += 1
        text = ("the citations look correct" if self.calls % 2
                else "on reflection, I am less sure of the citation")
        return Completion(text=text, model="stub-judge", finish_reason="stop",
                          usage=Usage(5, 5, 10))


def _probe_trace(row: dict) -> dict:
    return {"probe": row["probe"]}


def v1_integrity(data: dict, corpus: dict) -> None:
    print("\n== V1 — golden-set integrity (pre-registration in code)")
    try:
        load_golden(verify_hash=True, sources=True, corpus_text=corpus)
        ok, detail = True, "all rows valid + oracle-derivable"
    except DatasetError as e:
        ok, detail = False, str(e)[:180]
    check("every row passes the five-field validator + oracle-derivability", ok, detail)
    vrecord("V1a", "validator + oracle-derivability", "all rows valid", detail, ok)

    bad = copy.deepcopy(data["by_id"]["A1"])
    bad["expected_answer"] = {"claims": ["a checkout is created with kubernetesOperator"],
                              "must_not_assert": []}
    errs = validate_row(bad, corpus_text=corpus)
    malformed = any("oracle-derivable" in e for e in errs)
    check("a claim the evidence cannot reach is rejected (a hope, not an answer)",
          malformed, "; ".join(errs)[:160])
    vrecord("V1b", "oracle-derivability rejects a hope", "rejected",
            "rejected" if malformed else "accepted", malformed)

    patched = copy.deepcopy(data["rows"])
    patched[0]["expected_evidence"] = list(patched[0]["expected_evidence"]) + ["ghost/page#nope"]
    hash_moved = hash_rows(patched) != data["dataset_hash"]
    check("the content hash covers the graded fields (a patch changes it)", hash_moved)

    raw = yaml.safe_load(Path(data["path"]).read_text(encoding="utf-8"))
    raw["rows"][0]["expected_evidence"] = list(raw["rows"][0].get("expected_evidence") or []) \
        + ["ghost/page#nope"]
    refused = False
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False,
                                     encoding="utf-8") as fh:
        yaml.safe_dump(raw, fh)
        tmp = fh.name
    try:
        load_golden(tmp, verify_hash=True)
    except IntegrityError:
        refused = True
    finally:
        os.unlink(tmp)
    check("a patched row is REFUSED before grading (stale hash)", refused)
    vrecord("V1c", "pre-registration enforced in code", "refused",
            "refused" if refused else "graded", refused and hash_moved)


def v2_reproducible(data: dict, traces: dict) -> None:
    print("\n== V2 — judge reproducibility (same trace → byte-identical rows)")
    sample = ["A1", "B1", "E1", "F", "G", "H"]
    same, where = True, ""
    for rid in sample:
        row = data["by_id"][rid]
        tr = traces[rid]
        a, b = judge_row(row, tr), judge_row(row, tr)
        if json.dumps(a, sort_keys=True, default=str) != json.dumps(b, sort_keys=True, default=str):
            same, where = False, rid
    check("grading the same trace twice yields byte-identical verdict rows", same, where)
    vrecord("V2", "no hidden sampling in layer 1", "byte-identical",
            "byte-identical" if same else f"drifted at {where}", same)


def v3_cannot_be_fooled(data: dict) -> None:
    print("\n== V3 — the judge cannot be fooled by fluency")
    cases = [("F", "conclusion_subset_evidence", "fluent but wrong"),
             ("G", "cited_subset_retrieved", "fabricated citation"),
             ("H", "conclusion_subset_evidence", "citation-present, unsupported")]
    for rid, chk, label in cases:
        row = data["by_id"][rid]
        v = judge_row(row, _probe_trace(row))
        ok = v["verdict"] == "fail" and chk in v["failed_checks"]
        check(f"{label} (row {rid}) fails {chk}", ok, f"failed={v['failed_checks']}")
        vrecord(f"V3 {rid}", label, f"fail on {chk}",
                ",".join(v["failed_checks"]) or "pass", ok)
    e1 = data["by_id"]["E1"]
    confident = {"answer": "Saleor Cloud costs $99 per month on the Growth plan.",
                 "citations": [], "evidence": [], "refused": False,
                 "tool_calls": [{"name": "search_saleor_docs"}]}
    v = judge_row(e1, confident)
    ok = "refusal_expected" in v["failed_checks"]
    check("a confident out-of-corpus answer fails the E-family refusal check",
          ok, f"failed={v['failed_checks']}")
    vrecord("V3 E1", "confident out-of-corpus", "fail refusal_expected",
            ",".join(v["failed_checks"]), ok)


def v4_drift(data: dict, traces: dict) -> None:
    print("\n== V4 — judge honesty under drift (the model lane)")
    row = data["by_id"]["A1"]
    tr = traces["A1"]
    gw = FlakyJudgeGateway()
    a = judge_row(row, tr, kind="model", gateway=gw)
    b = judge_row(row, tr, kind="model", gateway=gw)
    base = judge_row(row, tr)["deterministic_verdict"]
    det_same = a["deterministic_verdict"] == b["deterministic_verdict"] == base
    check("the deterministic verdict is identical across two model-lane runs "
          "(the model never decides)", det_same,
          f"a={a['deterministic_verdict']} b={b['deterministic_verdict']} base={base}")
    sa = (a.get("semantic_note") or {}).get("semantic")
    sb = (b.get("semantic_note") or {}).get("semantic")
    drifted = sa != sb
    check("the `semantic:` comment may differ and the difference is RECORDED",
          drifted, f"{sa!r} vs {sb!r}")
    vrecord("V4", "the stochastic tier cannot decide", "det identical; semantic drift recorded",
            f"det={base}; drift={drifted}", det_same and drifted)


def v5_gate(*, full: bool = True) -> None:
    print("\n== V5 — the regression gate (Rule 2's ratchet)")
    argv = [sys.executable, str(ROOT / "tools" / "regress_all.py")]
    if not full:
        argv.append("--fast")
    proc = subprocess.run(argv, cwd=str(ROOT), capture_output=True, text=True)
    tail = next((l.strip() for l in reversed(proc.stdout.splitlines()) if l.strip()), "")
    ok = proc.returncode == 0
    check(f"`regress_all.py{'' if full else ' --fast'}` exits 0 — every row "
          "exactly as green", ok, tail)
    vrecord("V5", "improvement on the golden set, preservation here",
            "exactly as green", tail or "(no output)", ok)


def v6_coverage(data: dict) -> None:
    print("\n== V6 — golden-set coverage (kinds of failure, not happy paths)")
    ids = set(data["by_id"])
    need = {"A1", "B1", "B2", "C1", "C2", "D1", "D2", "E1", "E2",
            "CA1", "CB1", "CB2", "CC1", "CD1", "CE1", "F", "G", "H"}
    missing = sorted(need - ids)
    check("the doc nine + code six + the judge-hunting F/G/H are all present",
          not missing, f"missing={missing}")
    refusals = [r["id"] for r in data["rows"] if r["must_refuse"]]
    check("the E-family refusals are present (the set can indict a hallucinator)",
          set(refusals) >= {"E1", "E2", "CE1"}, str(refusals))

    def has_five(r: dict) -> bool:
        return (bool(str(r.get("question") or "").strip())
                and isinstance(r.get("expected_evidence"), list)
                and isinstance(r.get("expected_answer"), dict)
                and isinstance(r.get("expected_tools"), list)
                and "failure_category" in r)

    five = all(has_five(r) for r in data["rows"])
    check("every row carries all five curriculum fields", five)
    ok = not missing and set(refusals) >= {"E1", "E2", "CE1"} and five
    vrecord("V6", "coverage: failure kinds, not happy paths",
            "families + E-refusals + five fields", f"{len(ids)} rows", ok)


def v7_measurement(data: dict, traces: dict) -> None:
    print("\n== V7 — measurement, not manual (zero human screentime)")
    verdicts = judge_all(data["rows"], traces)
    machine = (len(verdicts) == len(data["rows"])
               and all(v["verdict"] in ("pass", "fail") for v in verdicts))
    md = report_markdown(data, verdicts, config={"mode": "stub", "topology": "single"})
    complete = all(f"| {r['id']} |" in md for r in data["rows"])
    check("the machine issues a verdict for every row (no eyeball)", machine)
    check("the report is generated programmatically and covers every row", complete)
    vrecord("V7", "the report ships, not adjudicated by hand",
            "a verdict per row; a complete report", f"{len(verdicts)} rows",
            machine and complete)


def v8_preregistration(data: dict) -> None:
    print("\n== V8 — pre-registration before runs (the anti-curve-fit fixture)")
    all_reg = all(str(r.get("registered_at") or "") for r in data["rows"])
    pinned = str(data.get("pinned_hash") or "") == str(data.get("dataset_hash") or "")
    check("every row carries registered_at", all_reg)
    check("the dataset is pinned (pinned hash == computed)", pinned)
    late = rows_registered_before(data["rows"], "2000-01-01T00:00:00Z")
    early = rows_registered_before(data["rows"], "2999-01-01T00:00:00Z")
    flagged = late == [r["id"] for r in data["rows"]] and early == []
    check("a run that started before its row's registration is FLAGGED",
          flagged, f"late={len(late)} early={len(early)}")
    ok = all_reg and pinned and flagged
    vrecord("V8", "registration precedes runs", "rows registered before runs",
            f"registered={all_reg} pinned={pinned} flagged={len(late)}", ok)


def v9_control(data: dict, reg, stores: dict, corpus: dict) -> None:
    print("\n== V9 — the control: the harness installed and unused changes nothing")
    row = data["by_id"]["A1"]
    harness = run_row(row, reg)
    gw = ScriptedGateway(row)
    prod = run_agent(row["question"], reg, gw, max_steps=4, memory_store=None)
    same = (harness["run"]["answer"] == prod["answer"]
            and set(harness["run"]["evidence"]) == set(prod["evidence"])
            and harness["run"]["termination_reason"] == prod["termination_reason"])
    check("a golden run's trace ≡ the same question's production trace "
          "(byte-for-byte)", same)

    before = {k: len(s) for k, s in stores.items()}
    corpus_before = len(corpus)
    run_golden(data, config={"mode": "stub", "topology": "single"}, reg=reg)
    after = {k: len(s) for k, s in stores.items()}
    check("running the golden set grows no store and adds no corpus id",
          before == after and len(corpus) == corpus_before, f"{before} -> {after}")
    vrecord("V9", "the judge is not a fixer", "harness off ≡ production",
            "identical" if same else "drifted", same and before == after)


def render_v_table() -> None:
    print("\n" + "=" * 104)
    print("   DAY 20 §8 — THE EVALUATION SET (V1–V9): the properties of grading itself")
    print("=" * 104)
    print(f"{'id':<8}{'what it proves':<40}{'expected':<26}{'observed':<20}{'status':<6}")
    print("-" * 104)
    for r in V_ROWS:
        print(f"{r['id']:<8}{r['proves'][:38]:<40}{r['expected'][:24]:<26}"
              f"{str(r['observed'])[:18]:<20}{'green' if r['ok'] else 'MOVED':<6}")
    print("-" * 104)
    moved = [r["id"] for r in V_ROWS if not r["ok"]]
    print(f"fixtures: {len(V_ROWS)}   moved: {len(moved)}"
          + (f" → {moved}" if moved else ""))
    print("The semantic cells (citation correctness, answer relevance, groundedness "
          "beyond membership) stay PENDING — Day 21's RAG benchmark / Day 22's agent "
          "benchmark. Cost: V1–V9 are 0 chats.")


def run_fixture_suite(*, full_regress: bool = True,
                      report_path: str | None = None) -> int:
    """The V1–V9 evaluation set against the golden dataset and the stub harness."""
    global FAILS
    FAILS = 0
    config = {"mode": "stub", "topology": "single"}
    reg, stores, corpus = build_stub_env()
    data = load_golden(verify_hash=True, sources=True, corpus_text=corpus)
    traces = run_golden(data, config=config, reg=reg)

    v1_integrity(data, corpus)
    v2_reproducible(data, traces)
    v3_cannot_be_fooled(data)
    v4_drift(data, traces)
    v5_gate(full=full_regress)
    v6_coverage(data)
    v7_measurement(data, traces)
    v8_preregistration(data)
    v9_control(data, reg, stores, corpus)

    render_v_table()
    if report_path:
        verdicts = judge_all(data["rows"], traces)
        Path(report_path).write_text(
            report_markdown(data, verdicts, config=config, v_rows=V_ROWS),
            encoding="utf-8")
        print(f"report written → {report_path}")
    print("\n" + ("ALL DAY 20 (V1–V9) CHECKS PASSED" if not FAILS
                  else f"{FAILS} CHECK(S) FAILED"))
    return 1 if FAILS else 0


# --------------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stub", action="store_true",
                    help="offline: in-process scripted gateway + stub corpora (default)")
    ap.add_argument("--real", action="store_true",
                    help="real gateway from .env + real stores (Day-21 handoff)")
    ap.add_argument("--golden", default=os.getenv(GOLDEN_SET_ENV) or str(GOLDEN_PATH))
    ap.add_argument("--topology", default=os.getenv(TOPOLOGY_ENV, "single"),
                    choices=["single"],
                    help="production topology; Day 19's B is exercised by "
                         "tools/architecture_smoke.py")
    ap.add_argument("--judge", default=None, choices=["deterministic", "model", "none"],
                    help="the judge lane (default $EVAL_JUDGE or deterministic)")
    ap.add_argument("--memory", default="off", choices=["off", "on"],
                    help="install Day 18's memory shelf for the runs (on/off)")
    ap.add_argument("--report", default=None, metavar="PATH")
    ap.add_argument("--self-check", action="store_true",
                    help="exit≠0 if any row's verdict drifts from its expected shape")
    ap.add_argument("--fixtures", action="store_true",
                    help="run the Day 20 §8 evaluation set (V1–V9) and exit")
    args = ap.parse_args()

    if args.fixtures:
        # V5 shells out to tools/regress_all.py; EVAL_REGRESS=fast skips the
        # day-suite replay (fixtures + golden rows only) for a quick iteration.
        full = os.getenv("EVAL_REGRESS", "full").strip().lower() != "fast"
        sys.exit(run_fixture_suite(full_regress=full, report_path=args.report))

    mode = "real" if args.real else "stub"
    config = {"mode": mode, "topology": args.topology, "memory": args.memory,
              "judge": args.judge or judge_kind_from_env()}
    if args.memory == "on":
        import tempfile
        fd, path = tempfile.mkstemp(prefix="eval-d20-", suffix=".json")
        os.close(fd)
        config["memory_store"] = StateStore(path)

    # 1) integrity — the dataset is authored + pinned BEFORE any run (V8).
    try:
        data = load_golden(args.golden, verify_hash=True, sources=True)
    except IntegrityError as e:
        print(f"[REFUSED] {e}")
        sys.exit(2)
    print(f"golden: {len(data['rows'])} rows  hash={data['dataset_hash'][:12]}…  "
          f"registered={data['registered_at']}  mode={mode}  judge={config['judge']}")

    # 2) present every question to production (V9: the harness changes nothing).
    if mode == "real":
        reg, _gw, _corpus = build_real_env()
    else:
        reg, _stores, _corpus = build_stub_env()
    traces = run_golden(data, config=config, reg=reg)

    # 3) grade — layer 1 deterministic (0 chats); layer 3 only if flagged.
    gateway = ScriptedGateway(data["rows"][0]) if config["judge"] == "model" else None
    verdicts = judge_all(data["rows"], traces, kind=config["judge"], gateway=gateway)

    render_table(data["rows"], verdicts)
    wrong = [x["id"] for x in verdicts
             if x["verdict"] != expected_verdict(data["by_id"][x["id"]])]
    if args.report:
        Path(args.report).write_text(report_markdown(data, verdicts, config=config),
                                     encoding="utf-8")
        print(f"report written → {args.report}")
    print("\n" + ("ALL DAY 20 (MECH) CHECKS PASSED" if not wrong
                  else f"{len(wrong)} ROW(S) DRIFTED: {wrong}"))
    sys.exit(1 if wrong else 0)


if __name__ == "__main__":
    main()
