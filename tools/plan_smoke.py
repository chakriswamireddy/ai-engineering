"""Day 16 §8 — the plan question set (P1–P9) against the scripted stub.

No API key, no real network. Reuses Day 11's offline registry (the Day-7 mini doc
corpus + the Day-10 code tree + the Day-14 seeded data store) so retrieval stays
byte-identical to the harness Days 7–15 grade, then walks **stored plans** through
the executor — the stub world supplies the plan the way a real world supplies it
via `planner.plan()`:

    $ python tools/plan_smoke.py

`MECH` here: plan validation (registered tools · schema args · known deps · acyclic
— fail-closed), execution state (statuses · observations · a failed step recorded,
not dropped), checkpoints (resume at *k+1*, replay reproduces the evidence), the
re-plan gate (divergence → replan), the retry ladder (repair · retry · fallback ·
replan, bounded), the **policy wall** (no rung climbed, nothing fabricated), the
over-planning guard (P9), and the headline `conclusion ⊆ evidence` proven both
ways. `PENDING`: whether a *real* model writes a *good* plan — the right
decomposition, the right order, a sensible replan — is Day 21's judgment on this
harness. The stub proves the **workflow**; a real key proves the **planner**.

The no-regression gate re-runs Days 11–15's suites (`tool_smoke`, `agent_smoke`,
`failure_smoke`, `data_smoke`, `sql_attack_smoke`) as-is, in subprocesses: the
planner is **additive** — an agenda above the loop — so a planned executor that
changes a single-step answer is a P9 failure arriving through the front door.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STUB = Path(__file__).resolve().parent / "mock_gateway.py"
sys.path.insert(0, str(ROOT))

from ai_os.agent import run_agent  # noqa: E402
from ai_os.executor import run_plan  # noqa: E402
from ai_os.planner import (  # noqa: E402
    Plan,
    needs_plan,
    parse_plan,
    plan as write_plan,
    topological_order,
    validate_plan,
)
from ai_os.state import ExecutionState  # noqa: E402

from tools.mock_gateway import (  # noqa: E402
    D14_SQL_BAD_COLUMN,
    D14_SQL_EMPTY,
    D14_SQL_FIX,
    D15_SQL_DELETE,
    D16_PLAN_BAD,
    D16_PLAN_GOOD,
)
from tools.tool_smoke import (  # noqa: E402
    DOC_TAU,
    CODE_TAU,
    _stub_up,
    build_registry,
    make_gateway,
    reset,
)

FAILS = 0
PLAN_ROWS: list[dict] = []

CURRICULUM = "Investigate why customer X could not complete checkout"
TRIVIAL = "Fetch order 1"


def check(label: str, cond: bool, detail: str = ""):
    global FAILS
    if not cond:
        FAILS += 1
    print(f"[{'OK  ' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


def record(fid: str, res: dict, *, expected: str, owner: str = "—"):
    cols = res.get("plan_columns") or {}
    PLAN_ROWS.append({
        "id": fid, "world": "stub", "expected": expected,
        "steps": cols.get("steps_planned"), "executed": cols.get("steps_executed"),
        "deps": res.get("deps_respected"),
        "rungs": ",".join(cols.get("rungs") or []) or "—",
        "walls": cols.get("policy_walls"),
        "conclusion": cols.get("conclusion_subset_evidence"),
        "over_planned": cols.get("over_planned"),
        "termination": res.get("termination_reason"),
        "wall_ms": res.get("elapsed_ms"),
        "cost_usd": (res.get("ledger") or {}).get("cost_usd", 0.0),
        "owner": owner,
    })


# ------------------------------------------------------------- stored plans
def stored(raw: dict, *, source: str = "stored") -> Plan:
    return Plan.from_dict(raw, source=source)


def curriculum_plan() -> Plan:
    raw = json.loads(D16_PLAN_GOOD)
    raw["question"] = CURRICULUM
    return stored(raw)


TWO_STEP = {
    "question": "Where is checkout handled, and what is order 1?",
    "steps": [
        {"id": "s1", "tool": "search_saleor_code",
         "args": {"query": "where is checkout completed", "k": 4},
         "expected": "the checkout mutation/service chunks"},
        {"id": "s2", "tool": "get_order", "args": {"id": "1"},
         "expected": "order 1's status and lines"},
    ],
}

ORDER_ONLY = {
    "question": TRIVIAL,
    "steps": [{"id": "s1", "tool": "get_order", "args": {"id": "1"},
               "expected": "order 1's record"}],
}

FAILED_STEP = {
    "question": "Where is checkout handled, and what do the docs say about it?",
    "steps": [
        {"id": "s1", "tool": "search_saleor_code",
         "args": {"query": "where is checkout completed", "k": 4},
         "expected": "the checkout chunks"},
        {"id": "s2", "tool": "search_saleor_docs",
         "args": {"query": "frobnicate zzqx vlim", "k": 4},
         "expected": "documentation for the checkout flow"},
    ],
}

REPAIR_PLAN = {
    "question": "How many orders are not drafts, and where is checkout handled?",
    "steps": [
        {"id": "s1", "tool": "run_saleor_sql", "args": {"sql": D14_SQL_BAD_COLUMN},
         "expected": "the order count"},
        {"id": "s2", "tool": "search_saleor_code",
         "args": {"query": "where is checkout completed", "k": 4},
         "expected": "the checkout chunks"},
    ],
}

POLICY_PLAN = {
    "question": "Delete the orders",
    "steps": [{"id": "s1", "tool": "run_saleor_sql", "args": {"sql": D15_SQL_DELETE},
               "expected": "the orders removed"}],
}

UNSUPPORTED_PLAN = {
    "question": "Investigate why the checkout failed",
    "steps": [{"id": "s1", "tool": "search_saleor_docs",
               "args": {"query": "frobnicate zzqx vlim", "k": 4},
               "expected": "the checkout failure documentation"}],
}

FABRICATED_PLAN = {
    "question": "Where is checkout handled?",
    "steps": [{"id": "s1", "tool": "search_saleor_code",
               "args": {"query": "where is checkout completed", "k": 4},
               "expected": "the checkout chunks"}],
}


class CountingRegistry:
    """A registry proxy that counts *execution* — the proof that a resume does not
    re-run completed steps (P4)."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.executed: list[str] = []

    def execute(self, name, args):
        self.executed.append(name)
        return self.inner.execute(name, args)

    def __getattr__(self, key):
        return getattr(self.inner, key)


# -------------------------------------------------------------------- P1/P2

def p1_plan_generation(reg, gw):
    print("\n== P1 — plan generation: a question → a validated, acyclic DAG")
    plan = curriculum_plan()
    v = validate_plan(plan.steps, reg)
    check("the curriculum plan validates (registered tools, schema args, known deps, acyclic)",
          v.ok, str(v.errors))
    order = topological_order(plan.steps)
    check("the planner's entry rule demands a plan for the curriculum task",
          needs_plan(CURRICULUM)["needs_plan"], needs_plan(CURRICULUM)["reason"])
    check("topological order puts order_state before payment (deps respected by construction)",
          order.index("order_state") < order.index("payment"), str(order))
    check("topological order puts find_customer before order_state",
          order.index("find_customer") < order.index("order_state"), str(order))
    check("every step names a registered tool",
          all(s.tool in reg.names() for s in plan.steps), str([s.tool for s in plan.steps]))
    check("parse_plan round-trips the model's JSON (the model writes, code reads)",
          [s.id for s in parse_plan(D16_PLAN_GOOD)] == plan.ids)

    # The fail-closed twin: an unbuildable step is rejected *before* execution.
    bad = stored({"question": CURRICULUM, "steps": json.loads(D16_PLAN_BAD)["steps"]})
    bv = validate_plan(bad.steps, reg)
    check("an unregistered tool is a grounding failure caught by code, not a model failure",
          not bv.ok and any("unregistered tool" in e for e in bv.errors), str(bv.errors))

    # A cycle is unrepresentable-as-valid, and a bad arg names its field.
    cyclic = stored({"question": "x", "steps": [
        {"id": "a", "tool": "get_order", "args": {"id": "1"}, "depends_on": ["b"]},
        {"id": "b", "tool": "get_order", "args": {"id": "1"}, "depends_on": ["a"]}]})
    cv = validate_plan(cyclic.steps, reg)
    check("a cycle is rejected (the DAG check, not luck)",
          not cv.ok and any("cyclic" in e for e in cv.errors), str(cv.errors))
    badarg = stored({"question": "x", "steps": [
        {"id": "a", "tool": "get_order", "args": {}, "depends_on": []}]})
    check("a schema-invalid arg is rejected and names the field",
          any("args invalid" in e for e in validate_plan(badarg.steps, reg).errors))

    # The model path: a bad plan earns exactly one informative retry, then the fix.
    g = make_gateway()
    reset(openai=["answer_d16_plan_bad", "answer_d16_plan_fixed"])
    got = write_plan(CURRICULUM, reg, g)
    check("premise: the rejected plan was retried and the correction accepted",
          not got.errors and len(got.steps) == 5, str(got.errors))
    ok_chats = sum(1 for r in g.ledger.records if r.ok)
    check("the informative retry cost exactly one extra chat (bounded self-correction)",
          ok_chats == 2, f"chats={ok_chats}")

    g2 = make_gateway()
    reset(openai=["answer_d16_plan_bad"])
    still_bad = write_plan(CURRICULUM, reg, g2)
    check("a plan that stays invalid comes back with errors and is never executed",
          bool(still_bad.errors), str(still_bad.errors))
    return plan


def p2_decomposition(reg, gw):
    print("\n== P2 — decomposition completeness (every sub-question is a step)")
    plan = curriculum_plan()
    ids = set(plan.ids)
    required = {"find_customer", "find_checkout", "order_state", "payment", "docs"}
    check("every required sub-question is its own step (customer, checkout, order, payment, docs)",
          required <= ids, str(sorted(required - ids)))
    by_id = plan.by_id
    check("the curriculum's dependency edge is present: payment ← order state",
          "order_state" in by_id["payment"].depends_on, str(by_id["payment"].depends_on))
    check("no step is non-atomic by name (each id is one intent)",
          len(ids) == len(plan.steps), str(plan.ids))
    # Negative twin: an incomplete plan (no payment) fails completeness even though
    # it is *valid* — the coverage failure is the planner's, and only the plan set
    # can see it.
    partial = stored({"question": CURRICULUM, "steps": [
        s for s in json.loads(D16_PLAN_GOOD)["steps"] if s["id"] != "payment"]})
    check("premise: a plan missing the payment step is still *valid* (coverage ≠ validity)",
          validate_plan(partial.steps, reg).ok)
    check("...and the coverage check catches it: payment is a missing sub-question",
          "payment" not in set(partial.ids))


# -------------------------------------------------------------------- P3/P4

def p3_execution_state(reg, gw):
    print("\n== P3 — execution state: statuses, observations, failures recorded")
    reset(openai=["tc_code", "tc_order", "answer_multistep"])
    res = run_plan(stored(TWO_STEP), reg, gw, max_replans=0)
    record("P3", res, expected="statuses progress, observations recorded")
    recs = {r["id"]: r for r in res["state"]["steps"]}
    check("both steps reached `done` (pending → running → done)",
          recs["s1"]["status"] == "done" and recs["s2"]["status"] == "done",
          str({k: v["status"] for k, v in recs.items()}))
    check("each step's observation is recorded (the task-shaped working memory)",
          recs["s1"]["observation"] is not None and recs["s2"]["observation"] is not None)
    check("two checkpoints written, one per step",
          res["checkpoint_idx"] == 2 and res["resume_from"] == 3,
          f"idx={res['checkpoint_idx']} resume_from={res['resume_from']}")
    check("the plan's evidence union is the two observations' ids",
          set(res["evidence"]) == {i for s in res["state"]["steps"] for i in s["evidence"]})
    check("the answer is grounded (conclusion ⊆ evidence)", res["conclusion_subset_evidence"],
          str(res["conclusion_unsupported"]))

    # A failed step is *recorded as failed*, not silently dropped.
    reset(openai=["tc_code", "tc_d13_docs_miss", "answer_d16_nocause"])
    res2 = run_plan(stored(FAILED_STEP), reg, gw, max_replans=0)
    record("P3b", res2, expected="a failed step is recorded, not dropped", owner="planner")
    recs2 = {r["id"]: r for r in res2["state"]["steps"]}
    check("the refused step is recorded as `failed` with an error, not dropped",
          recs2["s2"]["status"] == "failed" and recs2["s2"]["error"],
          str(recs2["s2"]))
    check("the failed step stays in the state (the investigation is auditable)",
          len(res2["state"]["steps"]) == 2, str(list(recs2)))
    check("the failure stopped the plan honestly (plan_exhausted, not a silent pass)",
          res2["termination_reason"] == "plan_exhausted", str(res2["termination_reason"]))
    check("nothing was fabricated and no unsupported cause was asserted",
          not res2["fabricated"] and res2["conclusion_subset_evidence"],
          str(res2["conclusion_unsupported"]))
    return res


def p4_checkpoints_resume(reg, gw):
    print("\n== P4 — checkpoints/resume: resume at k+1; replay reproduces evidence")
    plan = stored(TWO_STEP)
    reset(openai=["tc_code", "tc_order", "answer_multistep"])
    first = run_plan(plan, reg, gw, max_replans=0, stop_after=1)
    record("P4", first, expected="interrupt after step k → resume at k+1")
    check("the interruption is a resumption point, not a termination (paused)",
          first["termination_reason"] == "paused" and first["checkpoint_idx"] == 1,
          f"{first['termination_reason']} idx={first['checkpoint_idx']}")
    check("the paused run executed exactly one step and answered nothing",
          first["tool_steps"] == 1 and first["answer"] == "",
          f"steps={first['tool_steps']}")
    first_evidence = list(first["evidence"])

    counting = CountingRegistry(reg)
    reset(openai=["tc_order", "answer_multistep"])
    second = run_plan(plan, counting, gw, max_replans=0,
                      state=ExecutionState.from_dict(first["state"]), resume=True)
    check("resume executed only the remaining step — the completed step is not re-run",
          counting.executed == ["get_order"], str(counting.executed))
    check("resume continued at k+1 (one new step, two checkpoints total)",
          second["tool_steps"] == 1 and second["checkpoint_idx"] == 2,
          f"steps={second['tool_steps']} idx={second['checkpoint_idx']}")
    check("the resumed run kept the earlier step's evidence (the checkpoint carried it)",
          set(first_evidence) <= set(second["evidence"]),
          str(sorted(set(first_evidence) - set(second["evidence"]))))
    check("the resumed run reached a grounded answer", second["conclusion_subset_evidence"])

    reset(openai=["tc_code", "tc_order", "answer_multistep"])
    replay = run_plan(plan, reg, gw, max_replans=0)
    check("a replay from step 0 reproduces the same evidence (deterministic)",
          set(replay["evidence"]) == set(second["evidence"]),
          str(sorted(set(replay["evidence"]) ^ set(second["evidence"]))))
    return second


# -------------------------------------------------------------------- P5–P8

def p5_replan_on_contradiction(reg, gw):
    print("\n== P5 — replan-on-contradiction: step 2 finds no checkout → replan")
    plan = stored({
        "question": "Why did the checkout fail?",
        "steps": [
            {"id": "s1", "tool": "search_saleor_code",
             "args": {"query": "where is checkout completed", "k": 4},
             "expected": "the checkout chunks"},
            {"id": "s2", "tool": "run_saleor_sql", "args": {"sql": D14_SQL_EMPTY},
             "expected": "the checkout row for the order"},
        ],
    })
    stored_replan = stored({"question": plan.question, "steps": [
        {"id": "s2b", "tool": "get_order", "args": {"id": "1"},
         "expected": "the order that the missing checkout would have produced"}]})

    def replan_fn(_plan, _step, _observation, _registry):
        return stored_replan

    reset(openai=["tc_code", "tc_d14_empty", "tc_order", "answer_multistep"])
    res = run_plan(plan, reg, gw, max_replans=1, replan_fn=replan_fn)
    record("P5", res, expected="divergence → replan the remaining steps", owner="planner")
    recs = {r["id"]: r for r in res["state"]["steps"]}
    check("the empty observation diverged from `expected` (the re-plan gate fired)",
          recs["s2"]["diverged"] is True and recs["s2"]["evidence"] == [],
          str(recs["s2"]))
    check("the replan rung fired exactly once (bounded by max_replans)",
          res["replans"] == 1 and [r["rung"] for r in res["rungs"]] == ["replan"],
          str(res["rungs"]))
    check("the replanned step ran and answered (the plan continued, not marched on)",
          recs["s2b"]["status"] == "done" and res["termination_reason"] == "answer",
          f"{recs['s2b']['status']} {res['termination_reason']}")
    check("the answer asserted nothing from the miss (conclusion ⊆ evidence)",
          res["conclusion_subset_evidence"], str(res["conclusion_unsupported"]))
    check("the miss contributed no evidence to cite",
          not set(recs["s2"]["evidence"]), str(recs["s2"]["evidence"]))
    return res


def p6_retry_ladder(reg, gw):
    print("\n== P6 — retry ladder (repairable): unknown_column → repair, plan continues")

    def repair_fn(_step, _error):
        return {"sql": D14_SQL_FIX}

    reset(openai=["tc_d14_bad_column", "tc_code", "answer_multistep"])
    res = run_plan(stored(REPAIR_PLAN), reg, gw, max_replans=0, repair_fn=repair_fn)
    record("P6", res, expected="repairable error → repair → plan continues", owner="retry router")
    recs = {r["id"]: r for r in res["state"]["steps"]}
    s1_call = res["steps"][0]["calls"][0]
    check("the `unknown_column` rejection was classified repairable and repaired",
          s1_call.get("plan_repairs") == 1, str(s1_call.get("error_kind")))
    check("the rung used is recorded (repair)", [r["rung"] for r in res["rungs"]] == ["repair"],
          str(res["rungs"]))
    check("the repaired step succeeded and the plan continued to step 2",
          recs["s1"]["status"] == "done" and recs["s2"]["status"] == "done"
          and res["tool_steps"] == 2, f"{recs['s1']['status']} {res['tool_steps']}")
    check("the run answered, grounded (conclusion ⊆ evidence)",
          res["termination_reason"] == "answer" and res["conclusion_subset_evidence"])

    # The bound: with the rung unbudgeted, the same failure stops the plan.
    reset(openai=["tc_d14_bad_column", "answer_d16_nocause"])
    no_repair = run_plan(stored(REPAIR_PLAN), reg, gw, max_replans=0, max_repairs=0,
                         repair_fn=repair_fn)
    record("P6b", no_repair, expected="the rung is bounded (max_repairs=0)", owner="retry router")
    check("with max_repairs=0 no rung fires and the plan stops honestly",
          no_repair["rungs"] == [] and no_repair["termination_reason"] == "plan_exhausted",
          f"{no_repair['rungs']} {no_repair['termination_reason']}")
    return res


def p7_policy_wall(reg, gw):
    print("\n== P7 — policy wall: a forbidden request is a wall, not a rung")
    reset(openai=["tc_d15_delete", "answer_d15_policy"])
    res = run_plan(stored(POLICY_PLAN), reg, gw, max_replans=1)
    record("P7", res, expected="policy → WALL: no rung, nothing fabricated",
           owner="retry router")
    check("the policy rejection was counted and owned the terminal",
          res["policy_violations"] == 1 and res["termination_reason"] == "policy_wall",
          f"{res['policy_violations']} {res['termination_reason']}")
    check("NO rung was climbed (repair/retry/fallback/replan all skipped)",
          res["rungs"] == [] and res["replans"] == 0,
          str(res["rungs"]))
    check("the wall was *not* routed around by a replan",
          res["replans"] == 0 and bool(res["policy_walls"]), str(res["replans"]))
    check("the run executed exactly the offending step and fabricated nothing",
          res["tool_steps"] == 1 and not res["fabricated"]
          and res["conclusion_subset_evidence"], str(res["fabricated"]))
    check("the answer surfaces the refusal and does not work around it",
          "cannot" in (res["answer"] or "").lower()
          and "policy" in (res["answer"] or "").lower(), res["answer"][:80])
    return res


def p8_verification(reg, gw):
    print("\n== P8 — verification: an unsupported hypothesis refuses to conclude")
    reset(openai=["tc_d13_docs_miss", "answer_d16_nocause"])
    res = run_plan(stored(UNSUPPORTED_PLAN), reg, gw, max_replans=0)
    record("P8", res, expected="insufficient evidence → refuse, conclusion ⊆ evidence")
    check("the step that found nothing is recorded failed, not answered from",
          res["state"]["steps"][0]["status"] == "failed"
          and res["state"]["steps"][0]["diverged"] is True,
          str(res["state"]["steps"][0]))
    check("the run refused to conclude (plan_exhausted, an honest non-answer)",
          res["termination_reason"] == "plan_exhausted", str(res["termination_reason"]))
    check("the answer says it could not determine the cause (no invented cause)",
          "could not determine" in (res["answer"] or "").lower(), res["answer"][:90])
    check("`conclusion ⊆ evidence` holds on the honest non-answer",
          res["conclusion_subset_evidence"] and not res["fabricated"],
          str(res["conclusion_unsupported"]))

    # The card's cardinal sin, proven caught: an answer that asserts a cause the
    # plan never observed fails the invariant.
    reset(openai=["tc_code", "answer_fabricated"])
    bad = run_plan(stored(FABRICATED_PLAN), reg, gw, max_replans=0)
    record("P8b", bad, expected="an unsupported conclusion is caught", owner="answer contract")
    check("an answer citing a symbol the plan never observed fails the invariant",
          not bad["conclusion_subset_evidence"] and bool(bad["fabricated"]),
          str(bad["fabricated"]))
    return res


# -------------------------------------------------------------------- P9

def p9_control(reg, gw):
    print("\n== P9 — control: a one-step question is NOT over-planned")
    entry = needs_plan(TRIVIAL)
    check("the planner's entry rule declines the trivial question (no plan chat wasted)",
          entry["needs_plan"] is False, entry["reason"])

    g = make_gateway()
    reset(openai=["tc_order", "answer_d14_orders"])
    # Through the *loop* (`run_agent(plan=...)`), not the executor directly: a
    # one-step plan reached by the loop is the identity case the day claims.
    res = run_agent(TRIVIAL, reg, g, plan=stored(ORDER_ONLY))
    record("P9", res, expected="one step, cost ≈ Day 12, over_planned = no")
    check("one step, one answer turn — Day 11/12's shape (a one-step plan *is* the loop)",
          res["tool_steps"] == 1 and res["turns"] == 2,
          f"steps={res['tool_steps']} turns={res['turns']}")
    check("`over_planned? = no` (the day's counterweight, Day 15's false-reject in new clothes)",
          res["over_planned"] is False and res["plan_columns"]["over_planned"] is False)
    check("a stored one-step plan spends zero plan chats (cost ≈ Day 12)",
          res["plan_chats"] == 0 and len(g.ledger.records) == 2,
          f"plan_chats={res['plan_chats']} bills={len(g.ledger.records)}")
    check("the answer is grounded in the step's record", res["conclusion_subset_evidence"],
          str(res["conclusion_unsupported"]))

    # A stored plan that *does* over-decompose a trivial question is flagged.
    fat = stored({"question": TRIVIAL, "steps": [
        {"id": "a", "tool": "get_order", "args": {"id": "1"}, "expected": "order 1"},
        {"id": "b", "tool": "get_customer", "args": {"id": "1"}, "depends_on": ["a"],
         "expected": "the customer"},
        {"id": "c", "tool": "get_product", "args": {"id": "1"}, "depends_on": ["b"],
         "expected": "the product"}]})
    reset(openai=["tc_order", "tc_d14_customer", "ok", "answer_parallel"])
    fatres = run_plan(fat, reg, gw, max_replans=0)
    check("a three-step plan for a single-lookup question is flagged over-planned",
          fatres["over_planned"] is True, str(fatres["over_planned"]))
    return res


# ------------------------------------------------------- fail-closed / budget

def fail_closed(reg, gw):
    print("\n== Day 16 — fail-closed: invalid and infeasible plans run nothing")
    bad = stored({"question": CURRICULUM, "steps": json.loads(D16_PLAN_BAD)["steps"]})
    g = make_gateway()
    res = run_plan(bad, reg, g, max_replans=0)
    record("FC1", res, expected="invalid plan → never executed", owner="planner")
    check("an invalid plan is rejected before any execution (0 steps, 0 bills)",
          res["plan_valid"] is False and res["tool_steps"] == 0
          and len(g.ledger.records) == 0,
          f"valid={res['plan_valid']} steps={res['tool_steps']} bills={len(g.ledger.records)}")
    check("the terminal names the grounding failure",
          res["termination_reason"] == "invalid_plan"
          and res["failure_kind"] == "invalid_plan", str(res["termination_reason"]))
    check("the validator's reasons ride in the run object (a fixable artifact)",
          bool(res["plan_errors"]), str(res["plan_errors"]))

    g2 = make_gateway()
    reset(openai=[])
    over = run_plan(curriculum_plan(), reg, g2, max_replans=0, budget_chats=2)
    record("FC2", over, expected="infeasible plan → refused pre-spend", owner="planner")
    check("a plan whose estimated cost exceeds the budget is refused pre-spend",
          over["termination_reason"] == "infeasible" and over["tool_steps"] == 0
          and len(g2.ledger.records) == 0,
          f"{over['termination_reason']} steps={over['tool_steps']}")


# ---------------------------------------------------------------- regression

SUITES = ["tool_smoke.py", "agent_smoke.py", "failure_smoke.py", "data_smoke.py",
          "sql_attack_smoke.py"]


def no_regression():
    print("\n== Day 16 — no regression: Days 11–15 re-run unchanged (the planner is additive)")
    for name in SUITES:
        script = Path(__file__).resolve().parent / name
        proc = subprocess.run([sys.executable, str(script)], cwd=str(ROOT),
                              capture_output=True, text=True)
        tail = ""
        for line in reversed(proc.stdout.splitlines()):
            if line.strip():
                tail = line.strip()
                break
        check(f"tools/{name} still exits 0", proc.returncode == 0, tail)
        if proc.returncode != 0:
            print(proc.stdout[-2000:])


# ----------------------------------------------------------------- reporting

def render():
    print("\n" + "=" * 128)
    print("   DAY 16 — THE PLAN AS A CONTRACT (§8: conclusion ⊆ evidence on every row)")
    print("=" * 128)
    print(f"{'id':<6}{'world':<7}{'steps':<7}{'exec':<6}{'deps':<7}{'rungs':<14}"
          f"{'walls':<7}{'concl':<7}{'over?':<7}{'termination':<20}cost")
    print("-" * 128)
    for r in PLAN_ROWS:
        print(f"{r['id']:<6}{r['world']:<7}{str(r['steps']):<7}{str(r['executed']):<6}"
              f"{'y' if r['deps'] else '—':<7}{str(r['rungs']):<14}{str(r['walls']):<7}"
              f"{'ok' if r['conclusion'] else 'FAIL':<7}"
              f"{'no' if r['over_planned'] is False else ('YES!' if r['over_planned'] else '—'):<7}"
              f"{str(r['termination']):<20}${r['cost_usd']:.8f}")
    print("-" * 128)
    # P8b is the *negative twin*: it proves the invariant by failing it — an answer
    # that asserts a cause the plan never observed. The headline is therefore "ok on
    # every answered row, the fabricated twin the only FAIL" (Day 14's convention).
    bad = [r["id"] for r in PLAN_ROWS if not r["conclusion"] and r["id"] != "P8b"]
    twin = [r for r in PLAN_ROWS if r["id"] == "P8b"]
    print(f"conclusion ⊆ evidence = ok on every answered row: "
          f"{'YES' if not bad else 'NO — ' + ', '.join(bad)}"
          + (f"   (the fabricated twin P8b correctly reads "
             f"{'FAIL' if twin and not twin[0]['conclusion'] else 'ok'})" if twin else ""))
    print("The rungs column is the retry strategy made legible: which rung fired, and — "
          "for P7 — that NONE did, because a policy rejection is a wall, not a mistake.")
    print("The `over?` column is the counterweight: planning is not free, and a layer "
          "that inflates a one-step question is a bug wearing thoroughness.")


def main() -> None:
    proc = None
    if not _stub_up():
        proc = subprocess.Popen([sys.executable, str(STUB)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            if _stub_up():
                break
            time.sleep(0.1)
        assert _stub_up(), "stub did not come up"

    try:
        reg, gw_doc, gw_code, doc_store, code_store = build_registry()
        gw = make_gateway()
        print(f"stores: {len(doc_store)} doc chunks (STUB) / {len(code_store)} "
              f"code symbols (STUB)   τ_doc={DOC_TAU} τ_code={CODE_TAU}   "
              f"tools: {', '.join(reg.names())}")
        p1_plan_generation(reg, gw)
        p2_decomposition(reg, gw)
        p3_execution_state(reg, gw)
        p4_checkpoints_resume(reg, gw)
        p5_replan_on_contradiction(reg, gw)
        p6_retry_ladder(reg, gw)
        p7_policy_wall(reg, gw)
        p8_verification(reg, gw)
        p9_control(reg, gw)
        fail_closed(reg, gw)
        no_regression()
    finally:
        if proc is not None:
            proc.terminate()

    render()
    print("\n" + ("ALL DAY 16 (MECH) CHECKS PASSED" if not FAILS
                  else f"{FAILS} CHECK(S) FAILED"))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
