"""Day 16 §5–§7 — the executor: the state machine that walks a plan.

The plan is the loop's **agenda, not its replacement**. This module walks it in
topological order, and each step is still Day 12's loop at work — one
`chat_with_tools` turn, Day 11's registry validating the emitted call, Day 13's
`execute_bounded` wall around it, Day 13/15's taxonomy classifying whatever came
back. What the executor adds is what a plan needs and a reflex cannot have:

- **execution state** — a status per step, its observation, and the running
  evidence union (`ai_os/state.py`), plus a **checkpoint** after every step, so the
  plan is *resumable* (start at *k+1*) and *auditable* (the checkpoint log is the
  investigation's provenance). Ephemeral by design; the persistent store is Day 17's
  seam, named and not built.
- **the re-plan gate** — after each step the observation is compared to the step's
  `expected`; when they diverge the executor **replans the remaining steps** instead
  of marching through an agenda the evidence has already invalidated (P5).
- **the four-rung retry ladder**, bounded, with **Day 15's classes deciding which
  rungs a failure may climb**: a `repairable` failure climbs (repair → retry →
  fallback → replan); a `policy` rejection is a **wall** — routed around or stopped
  on, never retried, never substituted, never fabricated. A planner that "fixes" its
  way past a denial is Day 15's attack loop one abstraction up (P7).
- **`conclusion ⊆ evidence`** — the eighth step may not assert a cause the plan never
  observed. The gate is structural (a failed or diverging step stops the run before
  `respond`, so the honest answer is "could not determine why; here is what was
  checked") and the invariant is a set-membership test over the collected
  observations, the third translation of `cited ⊆ retrieved` / `asserted ⊆ returned`.

Everything mechanical here is `MECH`: a state machine, a lookup, a step count, and
set arithmetic — deterministic and free under the stub. The two rungs that
regenerate a *model* artifact mid-run (**repair**, **replan**) are the plan's least
deterministic moves; the stub proves the *routing* (when they fire, that a policy
wall never triggers them, that the bound holds), and the *quality* of a real model's
repair/replan is Day 21's `PENDING` cell.
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable

from ai_os.agent import (
    SIBLING_TOOLS,
    _call_row,
    _ledger_delta,
    _ledger_row,
    _new_run_id,
    _run_cost,
    _tool_message,
    agent_budget_chars_from_env,
    agent_tool_retries_from_env,
    agent_tool_timeout_from_env,
    execute_bounded,
    mentioned_ids,
)
from ai_os.contract import ToolCall  # noqa: F401  (part of the documented seam)
from ai_os.data import asserted_subset_returned
from ai_os.planner import (
    PLAN_SOURCE_STORED,
    Plan,
    Step,
    estimate_cost,
    max_repairs_from_env,
    max_replans_from_env,
    repair_args,
    replan,
    topological_order,
    validate_plan,
)
from ai_os.sql_safety import POLICY as POLICY_CLASS
from ai_os.sql_safety import REPAIRABLE
from ai_os.state import (
    STATE_OFF,
    STATE_ON,
    ExecutionState,
    load_task_state,
    persist_task_state,
    task_key,
)
from ai_os.tools import (
    ERROR_TIMEOUT,
    REJECTION_KINDS,
    TOOL_ERROR_KINDS,
    ToolRegistry,
)
from ai_os.trace import (
    PLAN_TERMINATIONS,
    conclusion_subset_evidence,
    fingerprint,
    grounding_over_transcript,
    plan_adherence,
    plan_columns,
    step_trace,
)

# §7 — the execution prompt. The plan fixes *which* step; the model still emits the
# call (and may correct a malformed argument), and the answer is drawn only from the
# transcript's observations. Rule 4 is `conclusion ⊆ evidence` stated where the model
# can read it, the way the reasoning prompt states the loop's contracts.
PLAN_EXEC_PROMPT = (
    "You are executing a pre-registered investigation **plan**. The plan fixes which "
    "step is next; you decide the call, and when the plan's steps are done you write "
    "the answer. Rules:\n"
    "1. Execute the step you are given: call the tool it names, with arguments its "
    "schema accepts (correct the arguments if the plan's are malformed).\n"
    "2. Do not skip ahead and do not invent steps that are not in the plan.\n"
    "3. Answer only from the observations already in this transcript, citing the "
    "module#Symbol / doc_id ids that appear in the tool results. Never invent "
    "evidence, and never assert a cause the observations do not show.\n"
    "4. If the evidence does not support a cause, say plainly that you could not "
    "determine it and list what was checked — an unsupported conclusion is the one "
    "failure this plan exists to prevent."
)

# The note handed to the *forced* final turn when the plan cannot conclude honestly.
PLAN_TERMINATION_NOTE: dict[str, str] = {
    "policy_wall": (
        "The run stopped because a step attempted a request the safety layer refuses "
        "as a *policy* violation. Do not rephrase it and do not look for a "
        "workaround: say plainly that the request was refused, and answer only from "
        "the observations already in this transcript. Do NOT invent evidence."
    ),
    "plan_exhausted": (
        "The plan could not establish the cause: the evidence gathered is "
        "insufficient (or a step failed) and no replan was available. Say plainly "
        "that you could not determine why, list what was checked, and do NOT assert "
        "a cause the observations do not show. Do NOT invent evidence."
    ),
    "dependency_violation": (
        "The run stopped because a step's dependencies were not satisfied — the "
        "executor refused to run it out of order. Answer only from the observations "
        "already in this transcript and say plainly what could not be established. "
        "Do NOT invent evidence."
    ),
    "invalid_plan": (
        "The plan failed validation before anything ran. State that plainly and do "
        "NOT invent evidence."
    ),
    "budget": (
        "The run's budget ended the plan before it finished. Answer strictly from "
        "the observations already in this transcript and say that the budget "
        "stopped it. Do NOT invent evidence."
    ),
}

# A step that produced no evidence and no rows is a *finding* (Day 13's `no_data`),
# recorded as run — but it is not a satisfied step, and that is the re-plan gate's
# trigger. The status stays `done`; the `diverged` flag carries the judgment.
RUNG_REPAIR = "repair"
RUNG_RETRY = "retry"
RUNG_FALLBACK = "fallback"
RUNG_REPLAN = "replan"


# --------------------------------------------------------------- step helpers
def _step_note(step: Step, index: int, total: int, done: set[str]) -> str:
    """The system note that pins the loop to the plan's current step."""
    return (
        f"Plan step {index} of {total}: call tool {step.tool!r} with args "
        f"{json.dumps(step.args, default=str)}.\n"
        f"depends_on: {step.depends_on or 'nothing'} (completed: {sorted(done)}).\n"
        f"this step is done when: "
        f"{step.expected or 'its observation answers its part of the question'}.\n"
        "Execute exactly this step now."
    )


def _satisfied(rows: list[dict[str, Any]]) -> bool:
    """Did the step produce the evidence its `expected` promised? (`MECH`)

    Satisfied = every call succeeded (nothing refused or errored) **and** at least
    one observation carries evidence ids or data rows. An empty result is not a
    satisfied step: it is the divergence that licenses a replan, because a plan whose
    step 2 ("find the checkout") returns nothing has had its shape invalidated by
    reality, not merely its arguments.
    """
    if not rows:
        return False
    for row in rows:
        if not row.get("result_ok") or row.get("refused"):
            return False
    for row in rows:
        if row.get("evidence_ids"):
            return True
        data = row.get("data")
        if isinstance(data, dict) and "columns" in data and (data.get("rows") or []):
            return True
    return False


def _call_error(call, result) -> dict[str, Any]:
    return {"tool": call.name, "error": result.error,
            "error_kind": result.error_kind,
            "rejection_class": result.rejection_class,
            "retryable": result.retryable}


def _execute_step_call(call, vr, step: Step, *, registry: ToolRegistry,
                       tool_timeout_s: float | None, retry_cap: int,
                       repair_cap: int, clock, budget_chars: int | None,
                       repair_fn: Callable[[Step, dict[str, Any]],
                                           dict[str, Any] | None] | None,
                       ) -> dict[str, Any]:
    """One planned step's call, through the ladder. The whole rung policy lives here.

    Rung order, cheapest first (Day 16 §6): a **retryable** execution error retries
    the *same* call (Day 13 rung 1); a **repairable** rejection repairs the
    arguments and re-issues (Day 12/13 self-correction, now a rung); a still-broken
    call **falls back** to the sibling corpus tool, disclosed via `fallback_from`
    (Day 13 rung 2). A **policy** rejection exits before any rung is considered — it
    is a wall, and the counter is `policy_violations`. `replan` is not here: it is a
    property of the *remaining plan*, so the caller owns it.
    """
    elapsed_ms = 0.0
    retries = repairs = fallbacks = failures = timeouts = 0
    fallback: dict[str, Any] | None = None
    rung: str | None = None

    if vr.ok:
        result, elapsed_ms = execute_bounded(
            registry, call.name, vr.args or {}, tool_timeout_s=tool_timeout_s,
            clock=clock, decision=vr.decision)
    else:
        result = vr

    # The wall comes first: nothing below may touch a policy refusal.
    if result.rejection_class == POLICY_CLASS:
        msg, clamped = _tool_message(call, result, budget_chars)
        row = _call_row(call, vr, result, clamped, elapsed_ms=elapsed_ms,
                        fp=fingerprint(call))
        return {"call": call, "result": result, "row": row, "msg": msg,
                "ledger_row": _ledger_row(call, result, clamped), "policy": True,
                "failed": True, "rung": None, "elapsed_ms": elapsed_ms,
                "retries": 0, "repairs": 0, "fallbacks": 0, "timeouts": 0,
                "error": _call_error(call, result)}

    # Rung: retry the *same* call on a retryable execution error.
    while (result.error_kind in TOOL_ERROR_KINDS and result.retryable
           and retries < retry_cap and vr.ok):
        retries += 1
        rung = RUNG_RETRY
        result, more = execute_bounded(registry, call.name, vr.args or {},
                                       tool_timeout_s=tool_timeout_s, clock=clock,
                                       decision=vr.decision)
        elapsed_ms += more
        if result.error_kind in TOOL_ERROR_KINDS:
            failures += 1
            timeouts += int(result.error_kind == ERROR_TIMEOUT)

    # Rung: repair a *repairable* rejection (fixed arguments, same step). Never a
    # policy refusal, and never a fabrication — the rung needs a real, valid fix.
    repairable = (not result.ok and not result.refused
                  and result.rejection_class != POLICY_CLASS
                  and (result.rejection_class == REPAIRABLE
                       or result.error_kind in REJECTION_KINDS))
    if repairable and repairs < repair_cap and repair_fn is not None:
        fixed = repair_fn(step, _call_error(call, result))
        if isinstance(fixed, dict) and fixed:
            repairs += 1
            rung = RUNG_REPAIR
            fvr = registry.validate_args(call.name, json.dumps(fixed))
            if fvr.ok:
                vr = fvr
                result, more = execute_bounded(
                    registry, call.name, fvr.args or {},
                    tool_timeout_s=tool_timeout_s, clock=clock,
                    decision=fvr.decision)
                elapsed_ms += more
            else:
                result = fvr

    # Rung: fall back to the sibling *corpus* tool, for the same intent, disclosed.
    still_broken = (result.error_kind in TOOL_ERROR_KINDS
                    or (not result.ok and not result.refused))
    if still_broken and result.rejection_class != POLICY_CLASS and fallback is None:
        sibling = SIBLING_TOOLS.get(call.name)
        if sibling and sibling in registry.names():
            allowed = registry.properties(sibling)
            src = vr.args if vr.ok else (step.args or {})
            s_args = {k: v for k, v in src.items() if k in allowed}
            fvr = registry.validate_args(sibling, json.dumps(s_args))
            if fvr.ok:
                fres, more = execute_bounded(registry, sibling, fvr.args or {},
                                             tool_timeout_s=tool_timeout_s,
                                             clock=clock, decision=fvr.decision)
                elapsed_ms += more
                if fres.error_kind in TOOL_ERROR_KINDS:
                    failures += 1
                    timeouts += int(fres.error_kind == ERROR_TIMEOUT)
                fres.fallback_from = call.name
                result = fres
                fallbacks += 1
                rung = RUNG_FALLBACK
                fallback = {"from": call.name, "to": sibling, "ok": fres.ok}

    if result.error_kind in TOOL_ERROR_KINDS and failures == 0:
        failures += 1
        timeouts += int(result.error_kind == ERROR_TIMEOUT)

    msg, clamped = _tool_message(call, result, budget_chars)
    row = _call_row(call, vr, result, clamped, elapsed_ms=elapsed_ms,
                    retries=retries, fallbacks=fallbacks, failures=failures,
                    timeouts=timeouts, fallback=fallback, fp=fingerprint(call))
    if repairs:
        row["plan_repairs"] = repairs
    failed = bool(result.error_kind in TOOL_ERROR_KINDS
                  or (not result.ok and not result.refused))
    return {"call": call, "result": result, "row": row, "msg": msg,
            "ledger_row": _ledger_row(call, result, clamped), "policy": False,
            "failed": failed, "rung": rung, "elapsed_ms": elapsed_ms,
            "retries": retries, "repairs": repairs, "fallbacks": fallbacks,
            "timeouts": timeouts, "error": _call_error(call, result)}


# ------------------------------------------------------------ default rungs
def _default_repair(gateway) -> Callable[[Step, dict[str, Any]], dict | None]:
    def fn(step: Step, error: dict[str, Any]) -> dict[str, Any] | None:
        return repair_args(step, error, gateway)
    return fn


def _default_replan(gateway
                    ) -> Callable[[Plan, Step, dict, ToolRegistry], Plan | None]:
    def fn(plan: Plan, step: Step, observation: dict[str, Any],
           registry: ToolRegistry) -> Plan | None:
        return replan(plan, step, observation, registry, gateway)
    return fn


# ------------------------------------------------------------------ the walk
def run_plan(plan: Plan, registry: ToolRegistry, gateway, *,
             max_replans: int | None = None,
             max_repairs: int | None = None,
             tool_retries: int | None = None,
             tool_timeout_s: float | None = None,
             budget_chars: int | None = None,
             budget_usd: float | None = None,
             budget_chats: int | None = None,
             temperature: float = 0.0, max_tokens: int = 2048,
             clock=time.monotonic,
             repair_fn: Callable | None = None,
             replan_fn: Callable | None = None,
             stop_after: int | None = None,
             state: ExecutionState | None = None,
             resume: bool = False,
             state_store=None,
             principal: str | None = None,
             task_id: str | None = None,
             run_id: str | None = None,
             resume_from_store: bool = False,
             ) -> dict[str, Any]:
    """Walk a validated plan: execute · checkpoint · adapt · answer.

    `resume=True` with a `state` continues at the first step the state has not
    completed — the checkpoint's whole reason to exist. `stop_after=k` is the
    deterministic interruption the resume fixture uses (a crash / budget pause /
    human pause analogue): the run stops cleanly after *k* steps with
    `termination_reason: "paused"` and returns the state. `repair_fn`/`replan_fn`
    are the two model-backed rungs made injectable, so the offline suite supplies a
    **stored** repair/replan the way it supplies a stored plan.

    Returns a run object shaped like `run_agent`'s, plus the plan columns — so the
    A-report can read both worlds with one eye, and a plan run's verdict is a
    reproducible row rather than a narrated transcript.

    Day 17 §5 makes the checkpoint **durable**: with a `state_store`, every
    checkpoint is also a `put("/task/{task_id}", …)` — so `execution state` leaves
    the process. `resume_from_store=True` loads that record and continues at the
    first uncompleted step (a run resumed in a *new process*, S3); the read-only
    tools are what make the re-run safe (a completed step is skipped, not repeated).
    With `state_store=None` nothing changes: the executor is Day 16 unchanged, which
    is the S9 off-switch.
    """
    replan_cap = max_replans_from_env() if max_replans is None else max(0, max_replans)
    repair_cap = max_repairs_from_env() if max_repairs is None else max(0, max_repairs)
    retry_cap = (agent_tool_retries_from_env() if tool_retries is None
                 else max(0, tool_retries))
    timeout_cap = (agent_tool_timeout_from_env() if tool_timeout_s is None
                   else tool_timeout_s)
    obs_budget = (agent_budget_chars_from_env() if budget_chars is None
                  else budget_chars)
    stored_repair = repair_fn is not None
    stored_replan = replan_fn is not None
    if repair_fn is None:
        repair_fn = _default_repair(gateway)
    if replan_fn is None:
        replan_fn = _default_replan(gateway)
    ledger_start = len(gateway.ledger.records)

    # Day 17 §5 — resolve the store, then load the checkpoint it holds. The task is
    # keyed by `task_id`; a fresh process resumes because the *file* survived.
    store_on = state_store is not None
    if store_on:
        principal = principal or state_store.principal
        run_id = run_id or _new_run_id()
        task_id = task_id or f"task-{run_id}"
    resumed_from_store = False
    if state is None and resume_from_store and store_on:
        payload = load_task_state(state_store, principal, task_id)
        if payload is not None:
            state = ExecutionState.from_dict(payload)
            resume = True
            resumed_from_store = True

    validation = validate_plan(plan.steps, registry)
    st = state if state is not None else ExecutionState.fresh(plan.question, plan)
    for sid in plan.ids:                       # a stored state keeps its own order
        if sid not in st.plan_ids:
            st.plan_ids.append(sid)

    def flush(sid: str) -> None:
        """Hot to execute, cold to persist: the checkpoint, written to its shelf."""
        if store_on and task_id:
            persist_task_state(state_store, principal, task_id, st.as_dict(),
                               run_id=run_id, step=sid, source="executor")

    est = estimate_cost(plan, plan_chats=0 if plan.source == PLAN_SOURCE_STORED else 1)
    over_planned = bool(plan.source == PLAN_SOURCE_STORED and len(plan.steps) > 1
                        and not _plan_worth_it(plan))
    transcript: list[dict[str, Any]] = [
        {"role": "system", "content": PLAN_EXEC_PROMPT},
        {"role": "user", "content": plan.question},
    ]
    step_rows: list[dict[str, Any]] = []
    all_calls: list[Any] = []
    ledger_rows: list[dict[str, Any]] = []
    executed_rows: list[dict[str, Any]] = []
    policy_hits = tool_timeouts_total = 0
    answer_text = ""
    termination: str | None = None

    if not validation.ok:
        termination = "invalid_plan"
    elif not _fits_budget(est, budget_chats):
        termination = "infeasible"
    else:
        queue = topological_order(plan.steps)
        done = st.done_ids() if resume else set()
        while queue:
            sid = queue.pop(0)
            step = plan.step(sid)
            if step is None or sid in done:
                continue                       # resumed: a completed step is not re-run

            unmet = [d for d in step.depends_on if d not in st.done_ids()]
            if unmet:
                st.set_status(sid, "failed",
                              error=f"unmet dependency: {unmet}")
                st.checkpoint(sid)
                flush(sid)
                termination = "dependency_violation"
                break

            st.set_status(sid, "running")
            transcript.append({"role": "system",
                               "content": _step_note(step, len(step_rows) + 1,
                                                     len(plan.steps), st.done_ids())})
            completion = gateway.chat_with_tools(
                transcript, tools=registry.schemas(), temperature=temperature,
                max_tokens=max_tokens)
            if not completion.tool_calls:
                # The model answered instead of acting: the plan ended early, and
                # the remaining steps are recorded as skipped (never silently lost).
                answer_text = completion.text
                termination = "answer"
                for rest in queue:
                    st.set_status(rest, "skipped", error="plan ended early")
                break

            calls = list(completion.tool_calls)
            all_calls.extend(calls)
            rows: list[dict[str, Any]] = []
            observations: list[dict[str, Any]] = []
            step_policy = False
            for call in calls:
                vr = registry.validate_args(call.name, call.arguments)
                out = _execute_step_call(
                    call, vr, step, registry=registry,
                    tool_timeout_s=timeout_cap, retry_cap=retry_cap,
                    repair_cap=repair_cap, clock=clock, budget_chars=obs_budget,
                    repair_fn=repair_fn)
                rows.append(out["row"])
                observations.append(out["msg"])
                ledger_rows.append(out["ledger_row"])
                executed_rows.append(out["row"])
                tool_timeouts_total += int(out.get("timeouts") or 0)
                if out["rung"]:
                    st.add_rung(sid, out["rung"],
                                detail={**out["error"],
                                        "retries": out["retries"],
                                        "repairs": out["repairs"]})
                if out["policy"]:
                    step_policy = True

            transcript.append({"role": "assistant",
                               "content": completion.text or None,
                               "tool_calls": calls})
            transcript.extend(observations)
            row_trace = step_trace(len(step_rows) + 1, rows)
            step_rows.append(row_trace)

            rec = st.record(sid)
            rec.planned_tool = step.tool
            rec.tool = ", ".join(c.name for c in calls)
            rec.evidence = list(row_trace["retrieved"])
            rec.observation = rows[0] if rows else None

            if step_policy:
                policy_hits += 1
                st.add_policy_wall(sid, tool=rec.tool,
                                   reason="policy rejection — refused, not retried")
                st.set_status(sid, "failed", error="policy violation",
                              diverged=True)
                st.checkpoint(sid)
                flush(sid)
                termination = "policy_wall"       # a wall: no rung was climbed
                break

            ok = bool(rows) and all(r.get("result_ok") and not r.get("refused")
                                    for r in rows)
            satisfied = ok and _satisfied(rows)
            step_error = None
            if not ok:
                step_error = "; ".join(
                    str(r.get("error") or "step failed") for r in rows
                    if not r.get("result_ok") or r.get("refused")) or "step failed"
            st.set_status(sid, "done" if ok else "failed", diverged=not satisfied,
                          error=step_error)
            st.checkpoint(sid)
            flush(sid)

            if not satisfied:
                # §5's re-plan gate: the evidence invalidated the plan's shape.
                if replan_cap - st.replans > 0:
                    new = replan_fn(plan, step, rec.observation or {}, registry)
                    if new is not None and not new.errors and new.steps:
                        st.replans += 1
                        st.add_rung(sid, RUNG_REPLAN,
                                    detail={"failed_step": sid,
                                            "reason": "observation diverged"})
                        plan = _splice(plan, new)
                        for new_sid in new.ids:
                            if new_sid not in st.plan_ids:
                                st.plan_ids.append(new_sid)
                        queue = topological_order(new.steps) + queue
                        continue
                termination = "plan_exhausted"   # cannot conclude honestly → stop
                break

            if (budget_usd is not None
                    and _run_cost(gateway.ledger, ledger_start) >= budget_usd):
                termination = "budget"
                break
            if stop_after is not None and len(step_rows) >= stop_after:
                termination = "paused"           # a resumption point, not an exit
                break

        if termination is None:
            termination = "answer"               # the plan completed

    # -------------------------------------------------------------- the answer
    if termination == "answer" and not answer_text:
        if st.record(_last_id(plan)).diverged if plan.steps else False:
            termination = "plan_exhausted"        # 6→7 gate: no unsupported claim
        else:
            final = gateway.chat(transcript, temperature=temperature,
                                 max_tokens=max_tokens)
            answer_text = final.text
    if termination not in ("answer", "paused", "invalid_plan", "infeasible"):
        note = {"role": "system",
                "content": PLAN_TERMINATION_NOTE.get(
                    termination, "Answer only from the observations already in this "
                                 "transcript. Do NOT invent evidence.")}
        final = gateway.chat(transcript + [note], temperature=temperature,
                             max_tokens=max_tokens)
        answer_text = final.text

    # ---------------------------------------------------- the day's invariant
    mentioned = mentioned_ids(answer_text)
    grounding = grounding_over_transcript(mentioned, step_rows)
    asserted = asserted_subset_returned(answer_text, question=plan.question,
                                        records=st.data_records())
    conclusion = conclusion_subset_evidence(mentioned, st.evidence_union(), asserted)
    adherence = plan_adherence(plan.as_dict()["steps"], st.as_dict())
    state_dict = st.as_dict()
    columns = plan_columns(state_dict, over_planned=over_planned,
                           plan_source=plan.source, conclusion=conclusion,
                           adherence=adherence,
                           extra={"estimated_chats": est["expected_chats"],
                                  "rung_source": ("stored" if stored_repair and
                                                  stored_replan else "model"),
                                  "replan_source": "stored" if stored_replan else "model",
                                  "repair_source": "stored" if stored_repair else "model"})

    return {
        "question": plan.question,
        "answer": answer_text,
        "plan": plan.as_dict(),
        "plan_valid": validation.ok,
        "plan_errors": list(validation.errors),
        "plan_source": plan.source,
        "plan_columns": columns,
        "over_planned": over_planned,
        "topological_order": topological_order(plan.steps),
        "state": state_dict,
        "steps": step_rows,
        "tool_steps": len(step_rows),
        "turns": len(step_rows) + (1 if answer_text else 0),
        "tool_calls": [c.as_dict() for c in all_calls],
        "tool_ledger": ledger_rows,
        "rungs": state_dict["rungs"],
        "replans": st.replans,
        "policy_walls": state_dict["policy_walls"],
        "policy_violations": policy_hits,
        "deps_respected": not any(
            str(rec.get("error") or "").startswith("unmet dependency")
            for rec in state_dict["steps"]),
        "steps_executed": adherence["executed_steps"],
        "checkpoints": state_dict["checkpoints"],
        "checkpoint_idx": len(st.checkpoints),
        "resume_from": st.resume_from(),
        # Day 17 §5 — the state contract: whether the store was on, who owns the
        # task, the shelf key the checkpoint lives at, and whether this run loaded
        # its position from the store (a resume in a new process) rather than fresh.
        "state_on_off": STATE_ON if store_on else STATE_OFF,
        "state_principal": principal,
        "task_id": task_id if store_on else None,
        "task_state_key": (task_key(task_id) if (store_on and task_id) else None),
        "resumed_from_store": resumed_from_store,
        "hypothesis": st.hypothesis,
        "evidence": st.evidence_union(),
        "citations": mentioned,
        "fabricated": grounding["fabricated"],
        "cited_subset_retrieved": grounding["cited_subset_retrieved"],
        "asserted_subset_returned": asserted["ok"],
        "asserted_unsupported": (asserted["unsupported"]
                                 + asserted["unsupported_entities"]),
        "conclusion_subset_evidence": conclusion["ok"],
        "conclusion_unsupported": conclusion["unsupported"],
        "termination_reason": termination,
        "termination_note": PLAN_TERMINATIONS.get(termination, termination),
        "truncated": termination not in ("answer",),
        "failure_kind": _plan_failure_kind(termination, policy_hits),
        "tool_timeouts": tool_timeouts_total,
        "gen_sql": [r.get("gen_sql") for r in executed_rows
                    if r.get("gen_sql") and r.get("query_kind") == "generated"],
        "data_records": st.data_records(),
        "ledger": _ledger_delta(gateway.ledger, ledger_start),
        "plan_chats": 0 if plan.source == PLAN_SOURCE_STORED else 1 + st.replans,
        "elapsed_ms": round(sum(r.get("elapsed_ms") or 0.0 for r in executed_rows), 3),
    }


def _last_id(plan: Plan) -> str:
    order = topological_order(plan.steps)
    return order[-1] if order else ""


def _plan_failure_kind(termination: str | None, policy_hits: int) -> str | None:
    """The plan's disease, named (Day 13's `failure_kind`, one level up)."""
    if policy_hits:
        return "policy_violation"
    if termination == "invalid_plan":
        return "invalid_plan"
    if termination == "dependency_violation":
        return "dependency_violation"
    if termination == "plan_exhausted":
        return "insufficient_evidence"
    if termination == "infeasible":
        return "over_budget"
    if termination in (None, "answer", "paused"):
        return None
    return termination


def _splice(plan: Plan, new: Plan) -> Plan:
    """Append a replanned remainder to the plan — ids preserved, DAG extended."""
    return Plan(question=plan.question, steps=list(plan.steps) + list(new.steps),
                source=f"{plan.source}+{new.source}", raw=new.raw,
                errors=list(new.errors))


def _fits_budget(est: dict[str, Any], budget_chats: int | None) -> bool:
    if not budget_chats:
        return True
    return int(est["expected_chats"]) <= int(budget_chats)


def _plan_worth_it(plan: Plan) -> bool:
    """P9's counterweight on the *plan* side: would this plan have been needed?

    The entry rule (`planner.needs_plan`) answers the question before the plan
    exists; this answers it about a plan that already exists, so a stored plan the
    stub hands over can still be graded `over_planned` when it spends N steps on a
    single-intent question.
    """
    from ai_os.planner import needs_plan
    return bool(needs_plan(plan.question).get("needs_plan"))
