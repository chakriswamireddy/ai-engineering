"""Day 22 §7/§8 — the agent benchmark runner: the agent on Day 21's stand.

The benchmark is **Day 20's harness plus a behaviour layer plus a pinned
baseline** — nothing new in the answer path. For each golden row it runs the
production loop once (`ai_os/agent.agent.run_agent`) and measures the run's
**own** trace (`ai_os/agent_bench.py`): task success · TSR@1 · argument
correctness · steps · unnecessary calls · failure recovery · cost · latency.

    stub (default)          Day-20's scripted in-process gateway, 0 new chats
    --real                  the real gateway from `.env` (the day's headline)

The output is the **A-report** — one row per golden entry:

    id · world · termination · coverage · TSR@1 · tool_cov · args_valid · steps ·
    unnecessary · recovery · cost(chats/embeds/$) · latency(ms) · verdict · owner

The day's discipline: the judge reads the run's trace and **never re-executes**
it (an agent's second run is a second production run and a verdict on a run
nobody graded), and the world (`stub` vs `real`) is always labelled — a real
agent run is an outcome, not an identity.

    $ python tools/bench_agent.py --stub            # the whole A-report, offline
    $ python tools/bench_agent.py --stub --baseline data/agent-baseline.json
    $ python tools/bench_agent.py --fixtures        # the AB1–AB9 fixture set
    $ python tools/bench_agent.py --real            # the real gateway (headline)
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

from ai_os.agent import run_agent  # noqa: E402
from ai_os.agent_bench import (  # noqa: E402
    argument_correctness,
    control_agrees,
    failure_recovery,
    measure,
    necessary_call_coverage,
    step_count,
    task_success,
    tool_selection,
    unnecessary_calls,
)
from ai_os.contract import Completion, ToolCall, Usage  # noqa: E402
from ai_os.eval_dataset import (  # noqa: E402
    AGENT_KIND,
    GOLDEN_PATH,
    JUDGE_HUNTING_KIND,
    load_golden,
)
from ai_os.ledger import Ledger  # noqa: E402
from ai_os.rag import NOT_COVERED  # noqa: E402
from ai_os.semantic_judge import (  # noqa: E402
    grade_claims,
    semantic_owner,
    semantic_verdict,
)
from ai_os.tools import ToolRegistry, ToolResult  # noqa: E402

from tools.eval_harness import (  # noqa: E402
    ScriptedGateway,
    build_real_env,
    build_stub_env,
)

BENCH_ENV = "AGENT_BENCH_MODE"
GOLDEN_SET_ENV = "GOLDEN_SET"
RETRIEVAL_TOOLS = {"search_saleor_docs", "search_saleor_code"}

ANSWER_CODE = (
    "Checkout is completed by complete_checkout "
    "(saleor/checkout/complete_checkout.py#complete_checkout), reached from the "
    "CheckoutComplete mutation "
    "(saleor/graphql/checkout/mutations/checkout_complete.py#CheckoutComplete).")
# The doc scenario's grounded answer: it cites the doc the stub's "checkout"
# query actually surfaces (`api-guide#completing-checkout`), so `cited ⊆ retrieved`
# holds and a U-rule verdict is not polluted by a fabrication.
ANSWER_DOC = (
    "call checkoutComplete on the checkout "
    "(developer/checkout/api-guide#completing-checkout)")


# ------------------------------------------------------------- the worlds

def build_env(mode: str, *, store: str | None = None,
              code_store: str | None = None) -> dict:
    """The registry + gateway + corpus for the chosen world (stub | real)."""
    if mode == "real":
        reg, gw, corpus = build_real_env()
        return {"mode": "real", "reg": reg, "chat": gw, "corpus": corpus}
    reg, stores, corpus = build_stub_env()
    return {"mode": "stub", "reg": reg, "stores": stores, "corpus": corpus}


def _ledger_view(gateway, run: dict, start: int = 0) -> dict:
    base = getattr(gateway, "ledger", None)
    recs = list(getattr(base, "records", []) or [])[start:]
    embeds = sum(1 for s in run.get("steps") or [] for c in s.get("calls") or []
                 if c.get("name") in RETRIEVAL_TOOLS and c.get("result_ok"))
    return {
        "chats": len(recs),
        "ok": sum(1 for r in recs if r.ok),
        "failed": sum(1 for r in recs if not r.ok),
        "embeds": embeds,
        "tokens": sum(r.input_tokens + r.output_tokens for r in recs),
        "cost_usd": round(sum(r.cost_usd for r in recs), 8),
        "latency_ms": round(sum(r.latency_ms for r in recs), 3),
    }


def _max_steps(row: dict) -> int:
    env = row.get("expected_steps") or {}
    return max(4, int(env.get("max", 3)) + 2)


def run_row(row: dict, env: dict, *, world: str, gateway=None) -> dict:
    """Run one golden row through production; return the A-row (measured)."""
    started = time.perf_counter_ns()
    if world == "real":
        gw = gateway or env["chat"]
        start = len(getattr(gw.ledger, "records", []) or [])
    else:
        gw = ScriptedGateway(row)
        start = 0
    run = run_agent(row["question"], env["reg"], gw, max_steps=_max_steps(row))
    wall_ms = round((time.perf_counter_ns() - started) / 1e6, 3)
    res = measure(run, row, ledger=_ledger_view(gw, run, start),
                  corpus_text=env["corpus"], wall_ms=wall_ms)
    res["world"] = world
    res["run"] = run
    return res


def run_golden(data: dict, env: dict, *, world: str) -> list[dict]:
    return [run_row(r, env, world=world) for r in data["rows"]
            if r["kind"] == AGENT_KIND]


def _probe_row(row: dict, env: dict) -> dict:
    """Grade a judge-hunting row against its injected probe (never a run)."""
    p = row["probe"]
    evidence = list(p["evidence"])
    citations = list(p["citations"])
    corpus = env["corpus"]
    context_texts = [corpus[i] for i in evidence if i in corpus]
    sem = grade_claims(
        answer=p["answer"],
        claims=(row.get("expected_answer") or {}).get("claims") or [],
        must_not_assert=(row.get("expected_answer") or {}).get("must_not_assert") or [],
        context_texts=context_texts, citations=citations, corpus_text=corpus)
    cited_ok = set(citations) <= set(evidence)
    fails: list[str] = []
    if not cited_ok:
        fails.append("generation")
    if semantic_verdict(sem) == "fail":
        fails.append(semantic_owner(sem) or "semantic")
    if fails:
        verdict, owner = "fail (rejected)", fails[0]
    elif row.get("human_review"):
        verdict, owner = "pending-human", "reader"
    else:
        verdict, owner = "pass", None
    return {
        "id": row["id"], "family": row.get("family"), "source": row.get("source"),
        "world": "judge-hunting", "probe": True,
        "task_success": {"ok": not fails, "cited_ok": cited_ok, "semantic": sem,
                         "fabricated": not cited_ok,
                         "termination_ok": None, "coverage_ok": sem.get("coverage_ok"),
                         "must_not_ok": sem.get("must_not_ok"), "refusal_ok": None},
        "tool_selection": {"tsr_at_1": None, "tool_coverage": None,
                           "tool_omissions": [], "tool_misses": []},
        "arguments": {"args_valid_frac": None, "arg_deviations": []},
        "steps": {"tool_steps": 0, "in_envelope": True},
        "unnecessary": {"unnecessary": None, "total": None, "efficiency": None,
                        "u1": 0, "u2": 0, "u3": 0, "undecided": []},
        "necessary": {"coverage": None},
        "recovery": {"recovery_rate": None, "failures": 0, "gave_up": False},
        "cost": {"chats": 0, "embeds": 0, "cost_usd": 0.0},
        "latency": {"per_step_ms": {}, "tool_timeouts": 0, "provider_retries": None},
        "wall_ms": 0.0, "fails": fails, "verdict": verdict, "owner": owner,
        "human_review": bool(row.get("human_review")),
    }


def grade_all(data: dict, env: dict, *, world: str) -> list[dict]:
    rows = []
    for r in data["rows"]:
        if r["kind"] == JUDGE_HUNTING_KIND:
            rows.append(_probe_row(r, env))
        else:
            rows.append(run_row(r, env, world=world))
    return rows


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


def render_table(rows: list[dict], *, world: str) -> None:
    print("\n" + "=" * 150)
    print(f"   DAY 22 — THE A-REPORT (agent behaviour measured · world={world} · "
          "deterministic columns, 0 new chats)")
    print("=" * 150)
    print(f"{'id':<5}{'world':<14}{'term':<13}{'cov':<7}{'TSR@1':<7}{'tool_cov':<9}"
          f"{'args':<7}{'steps':<7}{'unnec':<7}{'recov':<7}{'chats':<6}{'embeds':<7}"
          f"{'$':<9}{'lat(ms)':<9}{'verdict':<18}{'owner':<14}")
    print("-" * 150)
    for r in rows:
        ts = r["task_success"]
        sel, ar, st = r["tool_selection"], r["arguments"], r["steps"]
        unc, rec, cost, lat = r["unnecessary"], r["recovery"], r["cost"], r["latency"]
        print(f"{r['id']:<5}{r['world']:<14}"
              f"{_fmt(ts.get('observed_termination')):<13}"
              f"{_fmt(ts.get('coverage'), pct=True):<7}"
              f"{_fmt(sel.get('tsr_at_1'), pct=True):<7}"
              f"{_fmt(sel.get('tool_coverage'), pct=True):<9}"
              f"{_fmt(ar.get('args_valid_frac'), pct=True):<7}"
              f"{_fmt(st.get('tool_steps'), places=0):<7}"
              f"{_fmt(unc.get('unnecessary'), places=0):<7}"
              f"{_fmt(rec.get('recovery_rate'), pct=True):<7}"
              f"{_fmt(cost.get('chats'), places=0):<6}"
              f"{_fmt(cost.get('embeds'), places=0):<7}"
              f"{_fmt(cost.get('cost_usd')):<9}"
              f"{_fmt(r.get('wall_ms')):<9}"
              f"{r['verdict']:<18}{_fmt(r['owner'], places=0):<14}")
    print("-" * 150)
    green = sum(1 for r in rows if r["verdict"] in ("pass", "fail (rejected)",
                                                    "pending-human"))
    print(f"rows: {len(rows)}   verdicts issued: {green}/{len(rows)}   "
          f"bench cost: 0 chats (metrics over the run dict); run cost: the ledger's")
    print("`n/a` is honored (E-family TSR@1; undefined ≠ 0.0); "
          "`undecided` calls are owned, never counted; `pending-human` is I1.")


def report_markdown(data: dict, rows: list[dict], *, world: str,
                    sweep: dict | None = None, baseline: dict | None = None,
                    ab_rows: list[dict] | None = None) -> str:
    lines = [
        "# Agent Benchmark Day 22 — the A-report (measure agent behaviour)", "",
        "> Generated by `python tools/bench_agent.py`. Every column is a **pure "
        "function of one `run_agent` dict** (`ai_os/agent_bench.py`): the judge "
        "reads the run's trace and **never re-executes** it, and every row is "
        "labelled with its **world** (`stub` / `real`) — a real agent run is an "
        "outcome, not an identity. `n/a` where a column is undefined; the "
        "`undecided` bucket is owned, never counted.", "",
        f"dataset hash: `{data['dataset_hash']}`  (pinned `{data['pinned_hash']}`)",
        f"registered: {data['registered_at']}   world: `{world}`", "",
        "## §9 — one row per golden entry (every cell a real run's)", "",
        "| id | world | termination | coverage | TSR@1 | tool_cov | args_valid | "
        "steps | in-envelope | unnecessary | recovery | cost (chats/embeds/$) | "
        "latency(ms) | verdict | owner |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        ts, sel, ar = r["task_success"], r["tool_selection"], r["arguments"]
        st, unc, rec = r["steps"], r["unnecessary"], r["recovery"]
        cost = r["cost"]
        lines.append(
            f"| {r['id']} | {r['world']} | {_fmt(ts.get('observed_termination'))} | "
            f"{_fmt(ts.get('coverage'), pct=True)} | {_fmt(sel.get('tsr_at_1'), pct=True)} | "
            f"{_fmt(sel.get('tool_coverage'), pct=True)} | "
            f"{_fmt(ar.get('args_valid_frac'), pct=True)} | "
            f"{_fmt(st.get('tool_steps'), places=0)} | {_fmt(st.get('in_envelope'))} | "
            f"{_fmt(unc.get('unnecessary'), places=0)} | "
            f"{_fmt(rec.get('recovery_rate'), pct=True)} | "
            f"{_fmt(cost.get('chats'), places=0)}/{_fmt(cost.get('embeds'), places=0)}/"
            f"{_fmt(cost.get('cost_usd'))} | {_fmt(r.get('wall_ms'))} | "
            f"**{r['verdict']}** | {_fmt(r['owner'], places=0)} |")
    lines += [
        "",
        "> `TSR@1` = the fraction of steps whose chosen tools all lie in the row's "
        "`expected_tools` (membership, not a script); the cell is `n/a` where the "
        "row pre-registers no tools (E-family). `unnecessary` counts U1 (duplicate "
        "fingerprint) + U2 (no-Δ evidence) + U3 (never-cited) over "
        "*efficiency-eligible* calls; a refusal is never a wasted purchase. "
        "`recovery` = progress-after-failure with `fabricated = false` as its "
        "gate; `gave_up` is the honest zero.", "",
    ]

    if baseline:
        lines += ["## The pinned baseline (Day 12/13's stub behaviour)", "",
                  "| id | termination | steps | TSR@1 | unnecessary | recovery |",
                  "|---|---|---|---|---|---|"]
        for rid, v in baseline.items():
            lines.append(f"| {rid} | {v.get('termination')} | {v.get('steps')} | "
                         f"{_fmt(v.get('tsr_at_1'), pct=True)} | "
                         f"{_fmt(v.get('unnecessary'), places=0)} | "
                         f"{_fmt(v.get('recovery'), pct=True)} |")
        lines.append("")

    if ab_rows is not None:
        lines += ["## §8 — the AB-fixtures (AB1–AB9) and the AB9 control", "",
                  "| id | what it proves | expected | observed | status |",
                  "|---|---|---|---|---|"]
        for r in ab_rows:
            lines.append(f"| {r['id']} | {r['proves']} | {r['expected']} | "
                         f"{r['observed']} | {'green' if r['ok'] else '**MOVED**'} |")
        lines += [
            "",
            "**AB9 is the control** — the bench installed and unused ≡ production: "
            "the bench does not fix the agent (V9/R9 on the loop surface). "
            "AB1–AB9 are all `MECH` (0 new chats).", "",
        ]

    lines += [
        "## Cost (the price of measurement vs the price of being measured)", "",
        "- the agent bench (all `MECH` columns): **0 chats** — arithmetic over one "
        "run dict;",
        "- the **stub world** bills nothing (the in-process scripted gateway); the "
        "**real world** bills exactly what `run_agent` billed;",
        "- the run's worst case is `max_steps × 2 + 1` chats; the ``undecided`` "
        "residue lane is Day 21's (off by default) and never flips a `MECH` verdict.",
        "",
        "**The `PENDING` line, owned:** claim truth & support-in-meaning are Day "
        "21's lanes (re-used); good-recovery and defensible-uncited calls are Day "
        "22's residue (off by default); observability (why that latency, which "
        "model, which prompt) is **Day 23's** trace; distributed tracing is **Day "
        "24's**; judge poisoning is **Day 25's**.", "",
    ]
    return "\n".join(lines) + "\n"


# ================== Day 22 §6 — the scripted agent scenarios (stub world) =====

class BenchGateway:
    """A script-driven in-process gateway: one turn per script entry, then answer.

    Turns are `("tool", [(name, args), ...])` or `("answer", text)`. It records a
    ledger row per call, so the A-report's cost columns are the run's own.
    """

    MODEL = "stub-agent-bench"

    def __init__(self, turns: list, *, final_answer: str = ANSWER_CODE,
                 latency_ms: float = 2.0) -> None:
        self.turns = list(turns)
        self.final_answer = final_answer
        self.i = 0
        self.ledger = Ledger()
        self.last_provider = "stub"
        self.last_attempts = 1
        self.last_latency_ms = latency_ms
        self._lat = latency_ms

    def _record(self, it: int, ot: int) -> None:
        self.ledger.record(provider="stub", model=self.MODEL, input_tokens=it,
                           output_tokens=ot, latency_ms=self._lat, ok=True)

    def _tool_turn(self, calls: list) -> Completion:
        self._record(12, 8)
        tc = [ToolCall(name=n, arguments=json.dumps(a), id=f"call_bench_{i+1}")
              for i, (n, a) in enumerate(calls)]
        return Completion(text="", model=self.MODEL, finish_reason="tool_calls",
                          usage=Usage(12, 8, 20), tool_calls=tc)

    def _answer_turn(self, text: str) -> Completion:
        self._record(40, 30)
        return Completion(text=text, model=self.MODEL, finish_reason="stop",
                          usage=Usage(40, 30, 70))

    def chat_with_tools(self, messages, tools=None, **kw) -> Completion:
        if self.i < len(self.turns):
            kind, payload = self.turns[self.i]
            self.i += 1
            if kind == "tool":
                return self._tool_turn(payload)
            return self._answer_turn(payload)
        return self._answer_turn(self.final_answer)

    def chat(self, messages, **kw) -> Completion:
        return self._answer_turn(self.final_answer)


class FakeClock:
    """A deterministic wall — `first` on the first reading, `then` thereafter."""

    def __init__(self, first: float = 0.0, then: float = 1000.0) -> None:
        self.first, self.then, self.reads = first, then, 0

    def __call__(self) -> float:
        self.reads += 1
        return self.first if self.reads == 1 else self.then


def _hang_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register("slow_tool", {
        "name": "slow_tool", "description": "a tool that hangs",
        "parameters": {"type": "object", "properties": {}, "required": []}},
        lambda args: ToolResult.success("slow_tool", args=args, evidence=[]))
    return reg


def _scenario_row(**over) -> dict:
    """A minimal agent row for a scripted scenario (not from the golden set)."""
    row = {"id": "scn", "kind": AGENT_KIND, "source": "code", "family": "scenario",
           "expected_tools": ["search_saleor_code"],
           "expected_evidence": [], "expected_answer": {"claims": [],
                                                        "must_not_assert": []},
           "necessary_calls": [], "expected_steps": None,
           "expected_termination": "answer", "must_refuse": False}
    row.update(over)
    return row


def _run_scenario(env: dict, question: str, turns: list, *, final: str,
                  reg=None, max_steps: int = 4, **kw) -> dict:
    gw = BenchGateway(turns, final_answer=final)
    run = run_agent(question, reg or env["reg"], gw, max_steps=max_steps, **kw)
    return run


# ================== Day 22 §8 — the AB-fixtures (AB1…AB9) =====================

FAILS = 0
AB_ROWS: list[dict] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    global FAILS
    if not cond:
        FAILS += 1
    print(f"[{'OK  ' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


def abrecord(ab_id: str, proves: str, expected: str, observed: str, ok: bool) -> None:
    AB_ROWS.append({"id": ab_id, "proves": proves, "expected": expected,
                    "observed": observed, "ok": ok})


def _measure_scenario(env, question, turns, row, *, final, reg=None, **kw) -> dict:
    run = _run_scenario(env, question, turns, final=final, reg=reg, **kw)
    return run, measure(run, row, ledger={"chats": 0, "embeds": 0, "cost_usd": 0.0},
                        corpus_text=env["corpus"])


def ab1_task_success(env: dict, rows: list[dict]) -> None:
    print("\n== AB1 — task success (termination == expected · coverage · must_not)")
    agent = [r for r in rows if not r.get("probe")]
    answerable = [r for r in agent if not r["task_success"].get("refusal_ok")]
    refusal = [r for r in agent if r["task_success"].get("refusal_ok") is not None]
    ok_term = all(r["task_success"]["termination_ok"] for r in agent)
    check("every answerable golden row terminates `answer` (Day 12's contract)",
          ok_term, f"{len(agent)} rows")
    ok_cov = all(r["task_success"]["coverage_ok"] is not False for r in answerable)
    ok_mn = all(r["task_success"]["must_not_ok"] is not False for r in agent)
    check("every asserted claim is covered and no must_not is hit", ok_cov and ok_mn,
          f"answerable={len(answerable)}")
    ok_ref = all(r["task_success"]["refusal_ok"] is True for r in refusal)
    check("every E-family row refused (and thereby passed)", ok_ref,
          f"{len(refusal)} refusal rows")
    by_id = {r["id"]: r for r in rows}
    f, g = by_id["F"], by_id["G"]
    check("F fails coverage/must_not (fluent but wrong — Day 21's lane, re-used)",
          f["verdict"] == "fail (rejected)", f"failed={f['fails']}")
    check("G fails `cited ⊆ retrieved` (a fabricated citation, Day 6's invariant)",
          g["fails"] == ["generation"] and not g["task_success"]["cited_ok"],
          f"failed={g['fails']}")
    ok = ok_term and ok_cov and ok_mn and ok_ref and f["verdict"] == "fail (rejected)"
    abrecord("AB1", "task success: termination + coverage + must_not + refusal",
             "answerable answered; E refused; F/G rejected", f"{len(agent)} agent rows", ok)


def ab2_tool_selection(env: dict, rows: list[dict]) -> None:
    print("\n== AB2 — tool selection (TSR@1 · tool coverage · misses; owner=description)")
    agent = [r for r in rows if not r.get("probe")]
    answerable = [r for r in agent if r["tool_selection"]["tsr_at_1"] is not None]
    ok_tsr = all(r["tool_selection"]["tsr_at_1"] == 1.0 for r in answerable)
    check("every answerable golden row picks the expected tool per step (TSR@1=1.0)",
          ok_tsr, f"{len(answerable)} rows")
    e_family = [r for r in agent if r["tool_selection"]["tsr_at_1"] is None]
    check("E-family TSR@1 is `n/a` (refusing to choose is the pass; 0.0 would fail it)",
          len(e_family) >= 3, f"{len(e_family)} n/a rows")
    row = _scenario_row(expected_tools=["search_saleor_code"])
    _, res = _measure_scenario(
        env, "Where is checkout completed?",
        [("tool", [("search_saleor_docs", {"query": "frobnicate zzqx vlim", "k": 4})]),
         ("tool", [("search_saleor_code", {"query": "checkout complete", "k": 6})]),
         ("answer", ANSWER_CODE)], row, final=ANSWER_CODE)
    sel = res["tool_selection"]
    check("the injected wrong-tool step is a miss, owned by the description",
          sel["tsr_at_1"] == 0.5 and sel["owner"] == "description",
          f"tsr={sel['tsr_at_1']} misses={sel['tool_misses']}")
    check("the required tool was still called (tool_coverage = 1.0)",
          sel["tool_coverage"] == 1.0)
    abrecord("AB2", "tool selection: TSR@1 · coverage · misses (owner: description)",
             "golden 1.0; injected miss at 0.5",
             f"golden={_fmt(ok_tsr)} injected={sel['tsr_at_1']}", ok_tsr and sel["tsr_at_1"] == 0.5)


def ab3_argument_correctness(env: dict) -> None:
    print("\n== AB3 — argument correctness (args_valid · arg_deviation)")
    row = _scenario_row()
    _, res = _measure_scenario(
        env, "Where is checkout completed?",
        [("tool", [("search_saleor_code", {"query": "checkout", "kind": "banana"})]),
         ("tool", [("search_saleor_code", {"query": "checkout complete", "k": 6})]),
         ("answer", ANSWER_CODE)], row, final=ANSWER_CODE)
    ar = res["arguments"]
    check("the injected bad-args step is a rejection, the message names the field",
          ar["args_valid_frac"] == 0.5 and any("kind" in (m or "") for m in ar["rejections"]),
          f"frac={ar['args_valid_frac']} rej={ar['rejections']}")

    # A valid-shaped call with the wrong structured payload → an arg_deviation.
    dev_row = _scenario_row(necessary_calls=[{"tool": "search_saleor_code",
                                             "args": {"kind": "function"}}])
    dev_run = _run_scenario(
        env, "Where is checkout completed?",
        [("tool", [("search_saleor_code", {"query": "checkout", "kind": "class", "k": 6})]),
         ("answer", ANSWER_CODE)], final=ANSWER_CODE)
    dev = argument_correctness(dev_run["steps"], dev_row["necessary_calls"])
    check("a valid-shaped call with a wrong payload is an arg_deviation (flagged)",
          len(dev["arg_deviations"]) == 1 and dev["args_valid_frac"] == 1.0,
          f"deviations={dev['arg_deviations']}")
    abrecord("AB3", "argument correctness: args_valid + arg_deviation",
             "bad-args rejected naming the field; a payload deviation flagged",
             f"frac={ar['args_valid_frac']} dev={len(dev['arg_deviations'])}",
             ar["args_valid_frac"] == 0.5 and len(dev["arg_deviations"]) == 1)


def ab4_step_count(env: dict, rows: list[dict]) -> None:
    print("\n== AB4 — number of steps (the pre-registered envelope; honest exits)")
    agent = [r for r in rows if not r.get("probe") and r["run"].get("tool_steps")]
    in_env = all(r["steps"]["in_envelope"] for r in agent)
    check("every golden run falls inside its `expected_steps` envelope", in_env,
          f"{len(agent)} rows")
    row = _scenario_row(expected_steps={"min": 1, "max": 1})
    ok_out = step_count({"tool_steps": 3}, row)["in_envelope"] is False
    ok_in = step_count({"tool_steps": 1}, row)["in_envelope"] is True
    check("an over-envelope run is out of envelope (MECH)", ok_out and ok_in)
    # A gave_up run is an honest fail, never a quiet pass.
    grow = _run_scenario(
        env, "Where is checkout completed?",
        [("tool", [("search_saleor_code", {"query": "checkout", "kind": "banana"})]),
         ("tool", [("search_saleor_code", {"query": "checkout", "kind": "banana"})]),
         ("tool", [("search_saleor_code", {"query": "checkout", "kind": "banana"})])],
        final="I could not fix the rejected call within the bound.", self_correct=1)
    check("a gave_up run is reported honestly (not truncated to a pass)",
          grow["termination_reason"] == "gave_up" and grow["gave_up"],
          str(grow["termination_reason"]))
    abrecord("AB4", "number of steps: the envelope; honest gave_up",
             "golden in-envelope; a cap/gave_up run is an honest fail",
             f"in={in_env} gave_up={grow['termination_reason']}", in_env and grow["gave_up"])


def ab5_unnecessary(env: dict) -> None:
    print("\n== AB5 — unnecessary calls (U1 · U2 · U3 · necessary ⊆ made · undecided)")
    # U1 — the no-progress repeat (Day 13's fingerprint promoted to a unit).
    dup = [("tool", [("search_saleor_code", {"query": "checkout complete", "k": 6})]),
           ("tool", [("search_saleor_code", {"query": "checkout complete", "k": 6})])]
    run = _run_scenario(env, "Where is checkout completed?", dup, final=ANSWER_CODE)
    unc = unnecessary_calls(run["steps"], answer=run["answer"],
                            citations=run["citations"], corpus_text=env["corpus"])
    check("the no-progress script counts U1 (the repeat) with fabricated = false",
          unc["u1"] == 1 and not run["fabricated"],
          f"u1={unc['u1']} unnec={unc['unnecessary']} fab={run['fabricated']}")
    check("the refusal/rejection family is never counted unnecessary (eligible only)",
          unc["total"] == 2, f"eligible={unc['total']}")

    # U2 — a *different* query that returns the same evidence (same top-1, a
    # different fingerprint: "complete" is not in the stub vocabulary, so the
    # two queries share a vector but not a fingerprint).
    u2_turns = [("tool", [("search_saleor_docs", {"query": "checkout", "k": 1})]),
                ("tool", [("search_saleor_docs", {"query": "checkout complete", "k": 1})]),
                ("answer", ANSWER_DOC)]
    run2 = _run_scenario(env, "How do I create a checkout?", u2_turns, final=ANSWER_DOC)
    unc2 = unnecessary_calls(run2["steps"], answer=run2["answer"],
                             citations=run2["citations"], corpus_text=env["corpus"])
    check("a call that adds no new evidence is U2", unc2["u2"] == 1,
          f"u2={unc2['u2']} u1={unc2['u1']}")

    # U3 — a call whose evidence no later step and no answer cites.
    u3_turns = [("tool", [("search_saleor_docs", {"query": "checkout", "k": 1})]),
                ("tool", [("search_saleor_docs", {"query": "apps webhook sync", "k": 1})]),
                ("answer", ANSWER_DOC)]
    run3 = _run_scenario(env, "How do I create a checkout?", u3_turns, final=ANSWER_DOC)
    unc3 = unnecessary_calls(run3["steps"], answer=run3["answer"],
                             citations=run3["citations"], corpus_text=env["corpus"])
    check("a call whose evidence is never cited is U3", unc3["u3"] == 1,
          f"u3={unc3['u3']} undecided={len(unc3['undecided'])}")

    # necessary ⊆ made + the undecided bucket (recorded, never counted).
    cov = necessary_call_coverage(run3["steps"], [{"tool": "search_saleor_docs"}])
    check("`necessary ⊆ made` reads the envelope (a required call was made)",
          cov["coverage"] == 1.0, str(cov))
    unc_missing = unnecessary_calls(
        run3["steps"], answer=run3["answer"], citations=run3["citations"],
        corpus_text=None)   # no corpus → cannot prove → undecided, not counted
    check("with no provable evidence text, a U3 candidate is `undecided` (not counted)",
          len(unc_missing["undecided"]) >= 1 and unc_missing["u3"] == 0,
          f"undecided={len(unc_missing['undecided'])} u3={unc_missing['u3']}")
    ok = (unc["u1"] == 1 and unc2["u2"] == 1 and unc3["u3"] == 1
          and cov["coverage"] == 1.0 and len(unc_missing["undecided"]) >= 1)
    abrecord("AB5", "unnecessary calls: U1/U2/U3 + necessary ⊆ made + undecided",
             "each U-rule fires once; undecided owned, never counted",
             f"u1={unc['u1']} u2={unc2['u2']} u3={unc3['u3']}", ok)


def ab6_recovery(env: dict) -> None:
    print("\n== AB6 — failure recovery (progress-after-failure · fabricated gate)")
    # rejection → self-correction.
    fix_row = _scenario_row()
    _, fix = _measure_scenario(
        env, "Where is checkout completed?",
        [("tool", [("search_saleor_code", {"query": "checkout", "kind": "banana"})]),
         ("tool", [("search_saleor_code", {"query": "checkout complete", "k": 6})]),
         ("answer", ANSWER_CODE)], fix_row, final=ANSWER_CODE)
    check("rejection → self-correction recovers (rate 1)",
          fix["recovery"]["recovery_rate"] == 1.0, str(fix["recovery"]))
    # refusal → pivot.
    piv_row = _scenario_row(expected_tools=["search_saleor_code"])
    _, piv = _measure_scenario(
        env, "Where is checkout completed?",
        [("tool", [("search_saleor_docs", {"query": "frobnicate zzqx vlim", "k": 4})]),
         ("tool", [("search_saleor_code", {"query": "checkout complete", "k": 6})]),
         ("answer", ANSWER_CODE)], piv_row, final=ANSWER_CODE)
    check("refusal → pivot recovers (rate 1)",
          piv["recovery"]["recovery_rate"] == 1.0, str(piv["recovery"]))
    # gave_up → the honest zero.
    gv = _run_scenario(
        env, "Where is checkout completed?",
        [("tool", [("search_saleor_code", {"query": "checkout", "kind": "banana"})]),
         ("tool", [("search_saleor_code", {"query": "checkout", "kind": "banana"})]),
         ("tool", [("search_saleor_code", {"query": "checkout", "kind": "banana"})])],
        final="stuck", self_correct=1)
    gv_rec = failure_recovery(gv)
    check("gave_up is the honest zero (recovered 0 / failures > 0)",
          gv_rec["gave_up"] and gv_rec["recovered"] == 0 and gv_rec["failures"] > 0,
          str(gv_rec))
    # a fabricating run is not a recovery.
    fabricated_run = {"steps": [{"step": 1, "retrieved": [], "calls": [
        {"name": "search_saleor_code", "args_valid": False, "result_ok": False,
         "error_kind": "invalid_args", "refused": False, "timeouts": 0}]}],
        "fabricated": ["ghost#X"], "termination_reason": "answer",
        "cited_subset_retrieved": False, "answer": "ghost#X", "gave_up": False}
    fab_rec = failure_recovery(fabricated_run)
    check("a recovery that fabricates is NOT a recovery (the gate)",
          fab_rec["recovered"] == 0, str(fab_rec))
    ok = (fix["recovery"]["recovery_rate"] == 1.0 and piv["recovery"]["recovery_rate"] == 1.0
          and gv_rec["recovered"] == 0 and fab_rec["recovered"] == 0)
    abrecord("AB6", "failure recovery: progress-after-failure, fabricated gate",
             "rejection/refusal recover; gave_up & fabrication are zeros",
             f"fix={fix['recovery']['recovery_rate']} piv={piv['recovery']['recovery_rate']} "
             f"gave_up={gv_rec['recovered']}", ok)


def ab7_cost(env: dict, rows: list[dict]) -> None:
    print("\n== AB7 — cost (the stub world bills nothing; the bench 0 chats)")
    agent = [r for r in rows if not r.get("probe")]
    stub_zero = all((r["cost"].get("cost_usd") or 0.0) == 0.0 for r in agent)
    check("the stub world bills $0 (the in-process scripted gateway)", stub_zero,
          f"{sum(1 for r in agent if (r['cost'].get('cost_usd') or 0.0) == 0.0)}/"
          f"{len(agent)} rows")
    # The bench itself makes no chat call: measure() reads only the run dict.
    row = _scenario_row()
    run = _run_scenario(env, "Where is checkout completed?",
                        [("tool", [("search_saleor_code", {"query": "checkout complete", "k": 6})]),
                         ("answer", ANSWER_CODE)], final=ANSWER_CODE)
    before = len(run["steps"])
    _ = measure(run, row, ledger={"chats": 0, "embeds": 0, "cost_usd": 0.0},
                corpus_text=env["corpus"])
    check("measuring a run adds no step and no call (the judge never re-executes)",
          len(run["steps"]) == before)
    check("the run's chats are counted (one per scripted turn)",
          run["ledger"]["records"] == 2, str(run["ledger"]))
    abrecord("AB7", "cost: stub $0; the bench 0 chats; the run's ledger delta",
             "stub = $0; bench adds no call; chats counted",
             f"stub_zero={stub_zero} chats={run['ledger']['records']}",
             stub_zero and run["ledger"]["records"] == 2)


def ab8_latency(env: dict) -> None:
    print("\n== AB8 — latency (per-step/tool attribution; tool_timeouts vs retries)")
    run = _run_scenario(env, "Where is checkout completed?",
                        [("tool", [("search_saleor_code", {"query": "checkout complete", "k": 6})]),
                         ("answer", ANSWER_CODE)], final=ANSWER_CODE)
    res = measure(run, _scenario_row(), ledger={"chats": 0, "embeds": 0, "cost_usd": 0.0},
                  corpus_text=env["corpus"], wall_ms=12.5)
    lat = res["latency"]
    check("latency is attributed per step and per tool",
          bool(lat["per_step_ms"]) and bool(lat["per_tool_ms"]),
          f"steps={lat['per_step_ms']} tools={lat['per_tool_ms']}")
    # A hanging tool on an injected clock: the wall fires, tool_timeouts = 1.
    trow = _scenario_row(expected_tools=["slow_tool"],
                         expected_evidence=[], expected_answer={"claims": [],
                                                                "must_not_assert": []})
    t_run = _run_scenario(
        env, "run the slow tool",
        [("tool", [("slow_tool", {})])], final="the tool timed out, no evidence",
        reg=_hang_registry(), max_steps=3, tool_timeout_s=5.0, tool_retries=0,
        fallbacks=0, clock=FakeClock(0.0, 1000.0))
    t_lat = measure(t_run, _scenario_row(), ledger={"chats": 0, "embeds": 0,
                                                    "cost_usd": 0.0},
                    corpus_text=env["corpus"])["latency"]
    check("a hung tool fires the wall: tool_timeouts = 1, distinct from provider retries",
          int(t_run["tool_timeouts"]) == 1 and t_lat["tool_timeouts"] == 1,
          f"timeouts={t_run['tool_timeouts']} term={t_run['termination_reason']}")
    check("elapsed_ms proves the wall held (>= 5000 ms)", 
          max(t_lat["per_step_ms"].values(), default=0.0) >= 5000,
          str(t_lat["per_step_ms"]))
    abrecord("AB8", "latency: attribution + tool_timeouts distinct from retries",
             "per-step/tool attribution; a hung tool counts the wall",
             f"tools={list(lat['per_tool_ms'])} timeouts={t_run['tool_timeouts']}",
             bool(lat["per_step_ms"]) and int(t_run["tool_timeouts"]) == 1)


def ab9_control(env: dict, data: dict, rows: list[dict]) -> None:
    print("\n== AB9 — the control (bench off ≡ production; the bench doesn't fix)")
    row = data["by_id"]["A1"]
    bench = run_row(row, env, world="stub")
    gw = ScriptedGateway(row)
    prod = run_agent(row["question"], env["reg"], gw, max_steps=_max_steps(row))
    prod_res = measure(prod, row, ledger=_ledger_view(gw, prod),
                       corpus_text=env["corpus"], wall_ms=bench["wall_ms"])
    same = control_agrees(bench, prod_res)
    check("a golden row's A-report ≡ the same run's production verdict (byte-for-byte)",
          same)
    before = len(env["corpus"])
    grade_all(data, env, world="stub")
    check("running the whole A-report grows no corpus (no new evidence id)",
          len(env["corpus"]) == before, f"{before} -> {len(env['corpus'])}")
    abrecord("AB9", "the bench is not a fixer", "bench off ≡ production",
             "identical" if same else "drifted", same and len(env["corpus"]) == before)


def render_ab_table() -> None:
    print("\n" + "=" * 116)
    print("   DAY 22 §8 — THE A-BENCH FIXTURES (AB1–AB9): the properties of measurement")
    print("=" * 116)
    print(f"{'id':<8}{'what it proves':<48}{'expected':<26}{'observed':<20}{'status':<6}")
    print("-" * 116)
    for r in AB_ROWS:
        print(f"{r['id']:<8}{r['proves'][:46]:<48}{r['expected'][:24]:<26}"
              f"{str(r['observed'])[:18]:<20}{'green' if r['ok'] else 'MOVED':<6}")
    print("-" * 116)
    moved = [r["id"] for r in AB_ROWS if not r["ok"]]
    print(f"fixtures: {len(AB_ROWS)}   moved: {len(moved)}"
          + (f" → {moved}" if moved else ""))
    print("AB1–AB9 are 0 new chats; the semantic residue (claim truth, "
          "good-recovery, defensible-uncited) carries its owners.")


def run_fixture_suite(*, report_path: str | None = None) -> int:
    global FAILS
    FAILS = 0
    env = build_env("stub")
    data = load_golden(verify_hash=True, sources=True, corpus_text=env["corpus"])
    rows = grade_all(data, env, world="stub")

    ab1_task_success(env, rows)
    ab2_tool_selection(env, rows)
    ab3_argument_correctness(env)
    ab4_step_count(env, rows)
    ab5_unnecessary(env)
    ab6_recovery(env)
    ab7_cost(env, rows)
    ab8_latency(env)
    ab9_control(env, data, rows)

    render_ab_table()
    if report_path:
        Path(report_path).write_text(
            report_markdown(data, rows, world="stub", ab_rows=AB_ROWS),
            encoding="utf-8")
        print(f"report written → {report_path}")
    print("\n" + ("ALL DAY 22 (AB1–AB9) CHECKS PASSED" if not FAILS
                  else f"{FAILS} CHECK(S) FAILED"))
    return 1 if FAILS else 0


# --------------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stub", action="store_true",
                    help="offline stub world (default)")
    ap.add_argument("--real", action="store_true",
                    help="the real gateway from .env (the day's headline)")
    ap.add_argument("--golden", default=os.getenv(GOLDEN_SET_ENV) or str(GOLDEN_PATH))
    ap.add_argument("--baseline", default=None, metavar="PATH",
                    help="write (or read, if it exists) the pinned baseline")
    ap.add_argument("--report", default=None, metavar="PATH")
    ap.add_argument("--fixtures", action="store_true",
                    help="run the Day 22 §8 A-bench set (AB1–AB9) and exit")
    args = ap.parse_args()

    if args.fixtures:
        sys.exit(run_fixture_suite(report_path=args.report))

    mode = "real" if args.real else "stub"
    env = build_env(mode)
    data = load_golden(args.golden, verify_hash=True, sources=True,
                       corpus_text=env["corpus"] if mode == "stub" else None)
    print(f"golden: {len(data['rows'])} rows  hash={data['dataset_hash'][:12]}…  "
          f"world={mode}")

    rows = grade_all(data, env, world=mode)
    render_table(rows, world=mode)

    baseline = None
    if args.baseline:
        p = Path(args.baseline)
        if p.exists():
            baseline = json.loads(p.read_text(encoding="utf-8"))
            print(f"baseline: {p} ({len(baseline)} rows)")
        else:
            baseline = {r["id"]: {"termination": r["task_success"].get("observed_termination"),
                                  "steps": r["steps"].get("tool_steps"),
                                  "tsr_at_1": r["tool_selection"].get("tsr_at_1"),
                                  "unnecessary": r["unnecessary"].get("unnecessary"),
                                  "recovery": r["recovery"].get("recovery_rate")}
                        for r in rows}
            p.write_text(json.dumps(baseline, indent=2, sort_keys=True), encoding="utf-8")
            print(f"baseline pinned → {p} ({len(baseline)} rows)")
            baseline = None

    if args.report:
        Path(args.report).write_text(
            report_markdown(data, rows, world=mode, baseline=baseline),
            encoding="utf-8")
        print(f"report written → {args.report}")


if __name__ == "__main__":
    main()
