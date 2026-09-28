"""Day 11 §6 / Day 12 §3–§6 — the agent: one tool turn, generalized into a loop.

```text
Day 11 (single turn)                       Day 12 (loop)
question                                   request
   │                                          │
   ├─ chat_with_tools ─────────────────────── ├─ while steps < max_steps:
   │     no tool_calls → plain answer         │     chat_with_tools
   │     tool_calls    → validate → execute   │        no tool_calls → answer (exit 1)
   │                     → append the result  │        tool_calls    → validate → execute
   └─ chat(conversation) → grounded answer     │                        → append observation
                                               │                        steps += 1
                                               └─ cap → one final chat → answer (exit 2/3)
```

Three decisions carry Day 12's honesty — and one prompt:

- **Agent state is the transcript, nothing else.** The user request, every
  assistant `tool_calls` turn, every `tool` observation, and the final answer
  *are* the memory; nothing is mutated and nothing is hidden in a scratch dict
  the model cannot see. Forgetting is a string problem, not a cache problem.
- **Termination is a return value.** Three legitimate exits — an answer turn
  with no `tool_calls`, `max_steps`, and budget — and the run reports which one
  fired (`termination_reason`) because termination is a contract with the user.
  A run that ends at the cap is a *failed-agent* transcript and says so.
- **Grounding is per-step, so it holds over the transcript.** Each step's
  evidence is the graded shape Day 7 has checked since 2026-09; the final
  answer's citations must lie in the union of the observations
  (`cited ⊆ ∪ observations`). A multi-step agent whose answer cites nothing it
  saw is the Day 11 lie stretched over more turns.
- **The prompt is part of the loop.** `REASONING_PROMPT` precedes every reasoning
  turn, because the loop cannot *enforce* that observations are evidence for the
  next step — it can only say so. Without it a real model re-asks the question in
  new words instead of following what the evidence named; with it, the live run
  walks the map (`complete_checkout` → `create_order_from_checkout` → …).

Everything Day 11 built — the registry, the gate-as-return-value, the Day 4
gateway's tool wire — is exercised **as-is**: `run_agent(max_steps=1)` *is*
Day 11's `run_tool_answer`, a config rather than a different program.

Economics, written plainly: one question costs **`tool_steps` chat turns +
`tool_steps` embeds + 1 final chat** (worst case `max_steps × 2 + 1` bills).
Day 9's "zero new embeds at evidence time" survives the loop, because every tool
still runs retrieval, not generation.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from ai_os.contract import ToolCall
from ai_os.data import asserted_subset_returned
from ai_os.rag import NOT_COVERED
from ai_os.sql_safety import POLICY as POLICY_CLASS
from ai_os.state import (
    STATE_OFF,
    STATE_ON,
    load_conversation,
    persist_conversation,
)
from ai_os.tools import (
    ERROR_TIMEOUT,
    ERROR_TOOL,
    TOOL_ERROR_KINDS,
    ToolRegistry,
    ToolResult,
)
from ai_os.trace import (
    AGENT_TERMINATIONS,
    contradiction_coverage,
    contradiction_rows,
    cost_estimates,
    data_columns,
    detect_oscillation,
    detect_repeat,
    fingerprint,
    generated_sqls,
    grounding_over_transcript,
    observed_failures,
    policy_violations,
    rejection_classes,
    step_trace,
)

# --------------------------------------------------------------- config knobs

DEFAULT_MAX_STEPS = 6           # the loop's step cap (exit 2)
DEFAULT_BUDGET_CHARS = 4000     # per-observation clamp, like Day 11's max_chars
DEFAULT_SELF_CORRECT = 2        # re-issues allowed after a rejection, per question

# Day 13 §3/§4/§6 — the guard ring's knobs. Each is a config with a default and
# is counted per run, because an unbounded guard is an infinite loop in costume.
DEFAULT_TOOL_TIMEOUT_S = 30.0   # the wall around one tool call (time, not money)
DEFAULT_REPEAT_WINDOW = 4       # the fingerprint's cycle window (A,B,A,B)
DEFAULT_TOOL_RETRIES = 1        # rung 1: same-arg execution retries, per call
DEFAULT_FALLBACKS = 1           # rung 2: sibling-tool substitutions, per run

# Day 13 §6 rung 2 — the sibling map. A dead corpus tool may legally be answered
# by the other corpus *only* when the question's intent allows it, and the
# substitution must be disclosed (`fallback_from`).
SIBLING_TOOLS: dict[str, str] = {
    "search_saleor_code": "search_saleor_docs",
    "search_saleor_docs": "search_saleor_code",
}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def agent_max_steps_from_env() -> int:
    return max(1, _env_int("AGENT_MAX_STEPS", DEFAULT_MAX_STEPS))


def agent_budget_chars_from_env() -> int:
    return max(1, _env_int("AGENT_BUDGET_CHARS", DEFAULT_BUDGET_CHARS))


def agent_self_correct_from_env() -> int:
    return max(0, _env_int("AGENT_SELF_CORRECT", DEFAULT_SELF_CORRECT))


def agent_cost_budget_from_env() -> float | None:
    """Run-level cost cap (exit 3). `0`/unset means no cap."""
    raw = _env_float("AGENT_BUDGET_USD", 0.0)
    return raw if raw > 0 else None


def agent_tool_timeout_from_env() -> float:
    """Day 13 — the wall around one tool call, in seconds."""
    return _env_float("AGENT_TOOL_TIMEOUT_S", DEFAULT_TOOL_TIMEOUT_S)


def agent_repeat_window_from_env() -> int:
    return max(2, _env_int("AGENT_REPEAT_WINDOW", DEFAULT_REPEAT_WINDOW))


def agent_tool_retries_from_env() -> int:
    return max(0, _env_int("AGENT_TOOL_RETRIES", DEFAULT_TOOL_RETRIES))


def agent_fallbacks_from_env() -> int:
    return max(0, _env_int("AGENT_FALLBACKS", DEFAULT_FALLBACKS))


def _new_run_id() -> str:
    """A per-run provenance id (Day 17) — state is attributable to the run that
    wrote it (`recalled ⊆ authored`). Injectable at the call site for determinism."""
    import uuid
    return f"run-{uuid.uuid4().hex[:12]}"


# Ids the answer is allowed to cite: `module#Symbol` (code) and `doc_id#heading`
# (docs) share one shape, so one regex covers both corpora. A mention that is
# not in the observations is a fabricated citation.
ID_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]*#[A-Za-z0-9_][A-Za-z0-9_.-]*")

REFUSAL_NOTE = (
    "Every tool call was refused: the corpus cannot cover this question "
    f"(the gate's answer is {NOT_COVERED!r}). A refusal is not an answer — say "
    "so plainly and do NOT invent a doc_id or a module#Symbol."
)

# The note appended before the *forced* final turn when a guardrail stopped the
# loop: the model is asked to say what the evidence shows and that the loop
# stopped — never to "complete" what the cap cut off.
TERMINATION_NOTE: dict[str, str] = {
    "max_steps": (
        "The step limit was reached before you produced a final answer. Answer "
        "strictly from the tool observations already in this transcript and say "
        "plainly that the loop stopped at the step limit. Do NOT invent evidence."
    ),
    "budget": (
        "The run's budget was exhausted before you produced a final answer. "
        "Answer strictly from the tool observations already in this transcript "
        "and say plainly that the budget ended the loop. Do NOT invent evidence."
    ),
    "gave_up": (
        "Your tool arguments were rejected and the self-correction limit was "
        "reached. State plainly the point you were stuck at, using only the "
        "observations already in this transcript. Do NOT invent evidence."
    ),
    # Day 13 — the diagnosis exits. The note still forbids fabrication; it only
    # changes *which* honest thing the model is asked to say.
    "no_progress": (
        "The loop stopped early because you repeated an identical tool call "
        "instead of correcting it or moving on. Answer strictly from the "
        "observations already in this transcript and say plainly that the loop "
        "stopped on a repeated call. Do NOT invent evidence."
    ),
    "oscillation": (
        "The loop stopped early because you alternated the same small set of "
        "tool calls without making progress. Answer strictly from the "
        "observations already in this transcript, name the ambiguity that kept "
        "you cycling, and do NOT invent evidence."
    ),
    "tool_failure": (
        "A tool call failed (a timeout or an execution error) and the fallback "
        "ladder was exhausted, so the run stopped. Answer strictly from the "
        "observations already in this transcript and say plainly that the tool "
        "failed and what is therefore missing. Do NOT invent evidence."
    ),
    # Day 15 — the policy wall. The note explicitly forbids the rephrase-the-
    # forbidden-request move, because that is exactly the attack a helpful loop
    # would otherwise assist.
    "policy": (
        "The run stopped because the safe SQL layer refused a *policy* violation "
        "(a write, a denied/sensitive table or column, an ambiguous definition, "
        "or a query over the resource cap). This is not a mistake to fix by "
        "rephrasing: say plainly that the request was refused and why (naming the "
        "class, not schema details), and do NOT attempt a workaround. Answer only "
        "from the observations already in this transcript. Do NOT invent evidence."
    ),
}


# Day 12 §6 — the reasoning-turn prompt. It states what the loop depends on: tool
# results are *evidence for the next step*, a refusal is a finding (not an answer),
# and self-correction re-issues a *fixed* call. It is the difference between an
# agent that follows what the evidence named and one that re-asks the question in
# new words — the prompt is written for the grader, the way the schemas were.
REASONING_PROMPT = (
    "You are a code-investigation agent. Tools return *evidence* — graded source "
    "or documentation records — not prose. Rules:\n"
    "1. Investigate before answering: if the question asks how or where something "
    "works and you have no evidence yet, call a tool first.\n"
    "2. Act on the evidence: if a result names an import, dependency, helper or "
    "symbol relevant to the question (for example a mutation's service, or a "
    "service's order-creation helper), that name is your NEXT query — issue "
    "another tool call for it. Do not re-ask the question in different words.\n"
    "3. A refusal is a finding, not an answer: if a tool refuses, do not repeat "
    "the same call — pivot to another tool or say plainly the corpus cannot cover "
    "it. Never invent a doc_id or a module#Symbol.\n"
    "4. If a call was rejected, fix the argument and retry once — a corrected call "
    "is a different call; repeating the same wrong call is not.\n"
    "5. If a tool result carries rejection_class 'policy', STOP: it is a forbidden "
    "request (a write, sensitive data, an ambiguous definition, an over-cost "
    "query). Do not rephrase it and do not look for a workaround — report the "
    "refusal plainly. Only a 'repairable' rejection invites a fixed retry.\n"
    "6. Answer only from the evidence you gathered, citing the module#Symbol / "
    "doc_id ids that appear in the tool results."
)


def mentioned_ids(text: str) -> list[str]:
    """Every `doc_id` / `module#Symbol` the answer text names, de-duplicated.

    Sentence punctuation is trimmed — "...checkout.py#CheckoutComplete." ends a
    sentence, it does not extend the id — so a fabricated mention is exactly
    the id, and set membership against the evidence stays honest.
    """
    seen: dict[str, None] = {}
    for match in ID_RE.findall(text or ""):
        seen.setdefault(match.rstrip(".-"), None)
    return list(seen)


# ------------------------------------------------------------- small helpers

def _ledger_delta(ledger, start: int) -> dict[str, Any]:
    records = ledger.records[start:]
    return {
        "records": len(records),
        "ok": sum(1 for r in records if r.ok),
        "failed": sum(1 for r in records if not r.ok),
        "cost_usd": round(sum(r.cost_usd for r in records), 8),
    }


def _run_cost(ledger, start: int) -> float:
    return sum(r.cost_usd for r in ledger.records[start:])


def _tool_message(call, result: ToolResult,
                  budget_chars: int | None = None) -> tuple[dict[str, Any], bool]:
    """The `tool`-role observation: the evidence record, serialized — never prose.

    `budget_chars` clamps the observation (Day 11's `max_chars` discipline,
    applied per step). The clamp is a string truncation and is *recorded*, not
    silent; grounding is computed from the result's evidence ids, so a clamped
    observation can never make a citation look fabricated.
    """
    content = result.as_message()
    clamped = budget_chars is not None and len(content) > budget_chars
    if clamped:
        content = content[:budget_chars] + " …[clamped by budget_chars]"
    msg = {"role": "tool", "tool_call_id": call.id or "call_0",
           "name": call.name, "content": content}
    return msg, clamped


# ============================================================== Day 13 guards

def execute_bounded(registry: ToolRegistry, name: str, args: dict[str, Any], *,
                    tool_timeout_s: float | None,
                    clock=time.monotonic) -> tuple[ToolResult, float]:
    """§3 — the wall-clock budget around *one* tool call, not around the step.

    The orchestrator knows *a step* hung; the boundary knows *which call* hung
    and with what args, which is the trace the debugger wants. A timeout is an
    **observation** (`{ok: false, error_kind: "timeout"}`) exactly as a refusal
    is — nothing unwinds, nothing crashes, and the transcript stays the memory.
    The clock is injectable so the suite is deterministic (no real sleeping).
    """
    t0 = clock()
    try:
        result = registry.execute(name, args)
    except Exception as exc:                       # an executor bug, not the model's
        return ToolResult.tool_error(
            name, error_kind=ERROR_TOOL,
            message=f"tool error: {exc.__class__.__name__}: {exc}",
            args=args, retryable=True), (clock() - t0) * 1000
    elapsed_ms = (clock() - t0) * 1000
    if tool_timeout_s is not None and elapsed_ms > tool_timeout_s * 1000:
        return ToolResult.tool_error(
            name, error_kind=ERROR_TIMEOUT,
            message=f"exceeded tool_timeout_s={tool_timeout_s}s",
            args=args, retryable=True), elapsed_ms
    return result, elapsed_ms


def _claims(result: ToolResult) -> list[dict[str, Any]]:
    """Structured claims a result bears, as `(field, value, subject)` triples.

    §6's contradiction detector needs *values* to compare. Retrieval evidence
    rows may carry a `claims` mapping (rarely); structured data records carry one
    where two resolvers can disagree. Absent claims mean the detector is silent,
    which is what keeps Day 10's split-brain from firing.
    """
    out: list[dict[str, Any]] = []
    for row in result.evidence or []:
        claims = row.get("claims")
        if isinstance(claims, dict):
            for field, value in claims.items():
                out.append({"field": field, "value": value,
                            "subject": row.get("doc_id")})
    if isinstance(result.data, dict) and isinstance(result.data.get("claims"), dict):
        for field, value in result.data["claims"].items():
            out.append({"field": field, "value": value,
                        "subject": str(result.data.get("id"))})
    return out


def _call_row(call, vr: ToolResult, result: ToolResult,
              clamped: bool, *, elapsed_ms: float | None = None,
              retries: int = 0, fallbacks: int = 0, failures: int = 0,
              timeouts: int = 0, fallback: dict[str, Any] | None = None,
              fp: str | None = None) -> dict[str, Any]:
    """One executed call's audit row — the atom of the per-step trace.

    Day 13 beside the Day 11/12 columns: the *fingerprint* (the loop's state),
    the *elapsed_ms* (the wall), the *error_kind* (the taxonomy), the retry and
    fallback counters (the ladder), and any *claims* (the contradiction input).
    """
    row: dict[str, Any] = {
        "name": call.name,
        "arguments": call.arguments,
        "args_parsed": vr.args if vr.ok else None,
        "args_valid": vr.ok,
        "result_ok": result.ok,
        "refused": result.refused,
        "error": result.error,
    }
    if result.error_kind:
        row["error_kind"] = result.error_kind
    if result.retryable:
        row["retryable"] = True
    if result.fallback_from:
        row["fallback_from"] = result.fallback_from
    if fp:
        row["fingerprint"] = fp
    if elapsed_ms is not None:
        row["elapsed_ms"] = round(float(elapsed_ms), 3)
    if retries:
        row["tool_retries"] = retries
    if fallbacks:
        row["fallback_used"] = fallbacks
    if failures:
        row["tool_failures"] = failures
    if timeouts:
        row["timeouts"] = timeouts
    if fallback:
        row["fallback"] = fallback
    if result.evidence:
        row["evidence_ids"] = result.doc_ids
    claims = _claims(result)
    if claims:
        row["claims"] = claims
    if result.data is not None:
        row["data"] = result.data
    if result.truncated:
        row["truncated"] = True
    if clamped:
        row["obs_clamped"] = True
    row.update(data_columns(result))          # Day 14 §8 — gen_sql/validation/…
    return row


_DATA_SUMMARY_KEYS = ("sql", "query_kind", "validation", "executed", "columns",
                      "row_count", "truncated", "error_kind", "elapsed_ms",
                      "rejection_class", "stage", "executed_sql", "cost_estimate",
                      "statement_timeout_ms")


def _data_summary(record: dict[str, Any]) -> dict[str, Any]:
    """The compact data-record view for the run object (rows stay in the steps)."""
    return {k: record[k] for k in _DATA_SUMMARY_KEYS if record.get(k) is not None}


def _ledger_row(call, result: ToolResult, clamped: bool) -> dict[str, Any]:
    """The Day-11 `tool_ledger` row — kept byte-compatible for the T1…T7 suite."""
    row: dict[str, Any] = {
        "name": call.name,
        "arguments": call.arguments,
        "valid": bool(result.ok or result.refused),
        "ok": result.ok,
        "refused": result.refused,
        "error": result.error,
    }
    if result.error_kind:
        row["error_kind"] = result.error_kind
    if result.evidence:
        row["evidence_ids"] = result.doc_ids
    if result.data is not None:
        row["data"] = result.data
    if result.truncated:
        row["truncated"] = True
    if clamped:
        row["obs_clamped"] = True
    return row


def _pivot_recovered(steps: list[dict[str, Any]]) -> bool:
    """§2's attribution: a refusal followed by a *different* tool that succeeded.

    That is a wrong-tool miss the description owns (F1) — the loop recovered by
    changing corpus, not by retrying the refused call.
    """
    refused_tools: set[str] = set()
    for step in steps:
        for call in step.get("calls") or []:
            if call.get("refused"):
                refused_tools.add(call["name"])
        for call in step.get("calls") or []:
            if (refused_tools and call.get("args_valid") and call.get("result_ok")
                    and not call.get("refused") and call["name"] not in refused_tools):
                return True
    return False


def _derive_failure_kind(*, termination: str | None, gave_up: bool,
                         last_rejection_kind: str | None, refused_all: bool,
                         any_tool_failure: bool, any_timeout: bool,
                         pivot_recovered: bool, contradictions: list[dict],
                         policy_hits: int = 0,
                         ) -> str | None:
    """The taxonomy's payoff: one name for the disease, not the symptom.

    A `failure_kind` turns "the loop stopped" into "the loop stopped because it
    repeated its own rejected call" — and points at an owning file. Day 15's
    `policy_violation` outranks the rest: a forbidden request is the *cause*,
    whatever the loop was doing when it made it.
    """
    if policy_hits:
        return "policy_violation"
    if termination == "no_progress":
        return "no_progress"
    if termination == "oscillation":
        return "oscillation"
    if termination == "tool_failure":
        return "timeout" if any_timeout else "tool_error"
    if gave_up:
        return last_rejection_kind or "invalid_args"
    if refused_all:
        return "refused"
    if last_rejection_kind:
        return last_rejection_kind
    if any_tool_failure:
        return "timeout" if any_timeout else "tool_error"
    if pivot_recovered:
        return "tool_miss"
    if contradictions:
        return "contradiction"
    return None


# -------------------------------------------------------------------- the loop

def run_agent(request: str, registry: ToolRegistry, gateway, *,
              max_steps: int | None = None,
              budget_chars: int | None = None,
              self_correct: int | None = None,
              budget_usd: float | None = None,
              max_tool_calls_per_step: int | None = None,
              tool_timeout_s: float | None = None,
              repeat_window: int | None = None,
              tool_retries: int | None = None,
              fallbacks: int | None = None,
              clock=time.monotonic,
              temperature: float = 0.0, max_tokens: int = 2048,
              plan=None,
              state=None, principal: str | None = None,
              session_id: str | None = None, task_id: str | None = None,
              run_id: str | None = None,
              ) -> dict[str, Any]:
    """Reason → choose → execute → observe → reason again → answer.

    Day 12's three legitimate exits, all reported in `termination_reason`:

    1. **`answer`** — the model's turn carried no `tool_calls` (it answered from
       the evidence in hand). The only exit Day 11 knew, unchanged.
    2. **`max_steps`** — the model asked for tools again with no steps left; those
       calls are *recorded, not executed* (`capped_tool_calls`). One final *plain*
       chat asks the model to answer from already-gathered evidence and say the
       loop stopped; nothing is invented to fill the gap.
    3. **`budget`** — the run's cumulative ledger cost tripped `budget_usd`.
       Same forced final turn, `termination_reason: budget`.

    A guardrail stop (`gave_up`, when rejections exceed `self_correct`) is the
    fourth, narrower exit. `max_tool_calls_per_step` is Day 11's cap expressed
    as a per-step config: `run_tool_answer` is exactly
    `run_agent(max_steps=1, max_tool_calls_per_step=1)`.

    Day 13 layers a **guard ring** around each tool call and a **failure ledger**
    beside each step row, without changing the loop above:

    - **`execute_bounded`** — a wall-clock timeout (`tool_timeout_s`) around
      `registry.execute`; a hung tool becomes a `timeout` observation.
    - **the state fingerprint** — identical valid calls (`no_progress`) or a
      short cycle (`oscillation`) stop the loop *before* `max_steps`, and say
      which disease it was. `max_steps`/budget stay exactly where Day 12 left
      them: the fingerprint is a diagnosis on top, never a replacement.
    - **the fallback ladder** — on a retryable tool error, retry the *same* call
      once, then substitute a sibling corpus tool (`fallbacks`, disclosed via
      `fallback_from`), then degrade/stop honestly. Bounded, and it never
      fabricates: a failed tool never licenses an invented result.

    The run object carries both `termination_reason` (what stopped it) and
    `failure_kind` (why), so "stopped at the cap" and "stopped because it was
    looping" are distinguishable — the way Day 12 made "answer" distinguishable
    from "max_steps". `clock` is injectable so the timeout wall is testable
    without real sleeping.

    Day 16 §4 adds one optional parameter: **`plan`**. With a `Plan` the loop's
    agenda is pre-written — the request delegates to `executor.run_plan`, which walks
    the plan's steps through these same atoms while tracking execution state and
    checkpoints. With `plan=None` nothing below changes, which is what makes a
    **one-step plan the identity case** and keeps Day 12's exits, guards, and costs
    exactly where they were.

    Day 17 §4 adds one optional parameter: **`state`** (a `StateStore`). With a
    store and a `session_id`, the loop's **conversation state** persists per
    session/principal — prior turns are loaded as headers on the transcript (not a
    hidden variable), so a back-reference is answered from the stored exchange, and
    a self-correction stays attributed to the same conversation. Working/task state
    is the executor's shelf (it has the plan position and the hypothesis). With
    `state=None` nothing below changes: the store **augments** the run and never
    replaces it, which is what makes **state off ≡ Days 12–16** (S9).
    """
    q = (request or "").strip()
    if not q:
        raise ValueError("request must not be empty")

    store_on = state is not None
    principal = principal or (state.principal if state is not None else None)
    run_id = run_id or _new_run_id()

    if plan is not None:
        # Day 16 §4 — the plan is the loop's **agenda, not its replacement**: a
        # planned request delegates to the executor, which walks the plan through
        # this same loop's atoms (one `chat_with_tools` turn per step, the registry,
        # `execute_bounded`, the ladder). With no plan the body below is Day 12
        # *unchanged* — and a one-step plan is the identity case (P9), which is why
        # `run_agent(max_steps=1)` still is Day 11. Day 17 passes the store through
        # so the executor can put its **task state** on a shelf (D17).
        from ai_os.executor import run_plan
        result = run_plan(plan, registry, gateway, budget_chars=budget_chars,
                          tool_timeout_s=tool_timeout_s, tool_retries=tool_retries,
                          budget_usd=budget_usd, temperature=temperature,
                          max_tokens=max_tokens, clock=clock,
                          state_store=state, principal=principal,
                          task_id=task_id, run_id=run_id)
        if store_on and session_id:
            persist_conversation(state, principal, session_id, [
                {"role": "user", "content": q},
                {"role": "assistant", "content": result.get("answer", "")},
            ], run_id=run_id)
            result["session_id"] = session_id
        return result

    steps_cap = agent_max_steps_from_env() if max_steps is None else max(1, max_steps)
    obs_budget = (agent_budget_chars_from_env() if budget_chars is None
                  else budget_chars)
    correct_cap = (agent_self_correct_from_env() if self_correct is None
                   else max(0, self_correct))
    cost_cap = agent_cost_budget_from_env() if budget_usd is None else budget_usd
    timeout_cap = (agent_tool_timeout_from_env() if tool_timeout_s is None
                   else tool_timeout_s)
    window = (agent_repeat_window_from_env() if repeat_window is None
              else max(2, repeat_window))
    retry_cap = (agent_tool_retries_from_env() if tool_retries is None
                 else max(0, tool_retries))
    fallback_cap = (agent_fallbacks_from_env() if fallbacks is None
                    else max(0, fallbacks))
    ledger_start = len(gateway.ledger.records)

    # Day 17 §4 — conversation state: prior turns for this session are *headers on
    # the transcript*, not a hidden variable (Day 12's no-hidden-variable rule
    # survives the upgrade to a store). With no store the list is empty, so the
    # transcript below is byte-for-byte Day 12.
    prior_turns = (load_conversation(state, principal, session_id)
                   if (store_on and session_id) else [])
    transcript: list[dict[str, Any]] = (
        [{"role": "system", "content": REASONING_PROMPT}]
        + prior_turns
        + [{"role": "user", "content": q}])
    steps: list[dict[str, Any]] = []
    all_calls: list[Any] = []
    executed_rows: list[dict[str, Any]] = []
    ledger_rows: list[dict[str, Any]] = []
    deferred: list[Any] = []
    capped_calls: list[Any] = []
    signatures: list[str] = []
    fallback_log: list[dict[str, Any]] = []
    rejections = refusals = 0
    tool_timeouts_total = 0
    fallbacks_used = 0
    policy_hits = 0
    no_progress_at_step: int | None = None
    oscillation_detected = False
    last_rejection_kind: str | None = None
    gave_up = False
    termination: str | None = None
    answer_text = ""

    while True:
        completion = gateway.chat_with_tools(transcript, tools=registry.schemas(),
                                             temperature=temperature,
                                             max_tokens=max_tokens)
        if not completion.tool_calls:
            answer_text = completion.text
            termination = "answer"                      # exit 1
            break

        if len(steps) >= steps_cap:
            # The model asked again with no steps left. The calls are *recorded,
            # not executed* (exit 2) — evidence of the spiral, not a step. This
            # branch is why the loop still calls once more after the last allowed
            # tool step: `run_agent(max_steps=1)` never lands here on a single
            # tool turn, so Day 11's `run_tool_answer` is reproduced exactly
            # (one tool turn + one *natural* answer, never a bogus cap note).
            capped_calls = list(completion.tool_calls)
            termination = "max_steps"
            break

        all_calls.extend(completion.tool_calls)
        calls = completion.tool_calls
        if max_tool_calls_per_step is not None and len(calls) > max_tool_calls_per_step:
            deferred.extend(calls[max_tool_calls_per_step:])
            calls = calls[:max_tool_calls_per_step]

        observations: list[dict[str, Any]] = []
        rows: list[dict[str, Any]] = []
        step_failures = 0
        step_policy = False
        for call in calls:
            vr = registry.validate_args(call.name, call.arguments)
            if not vr.ok:
                # §2 — a *rejection* ("you asked wrong"): no execution, no
                # fingerprint (a call that never ran is not a step's identity),
                # no ladder — Day 12's self-correction owns it.
                rejections += 1
                if vr.error_kind:
                    last_rejection_kind = vr.error_kind
                msg, clamped = _tool_message(call, vr, obs_budget)
                observations.append(msg)
                row = _call_row(call, vr, vr, clamped)
                rows.append(row)
                executed_rows.append(row)
                ledger_rows.append(_ledger_row(call, vr, clamped))
                continue

            # §6 — climb the ladder on a tool error. Day 15: a *policy* rejection
            # is excluded from both rungs — re-issuing a forbidden query (or
            # substituting a sibling tool for it) is the loop assisting a bypass.
            result, elapsed_ms = execute_bounded(
                registry, call.name, vr.args or {},
                tool_timeout_s=timeout_cap, clock=clock)
            retries = failures = timeouts = 0
            if result.rejection_class == POLICY_CLASS:
                policy_hits += 1
                step_policy = True
            elif result.error_kind in TOOL_ERROR_KINDS:
                failures += 1
                timeouts += int(result.error_kind == ERROR_TIMEOUT)
            # Rung 1 — retry the *same* call (execution retry, not self-correction;
            # different counter, different config, because a 502 and a missing
            # `query` are not the same animal).
            while (result.error_kind in TOOL_ERROR_KINDS and result.retryable
                   and result.rejection_class != POLICY_CLASS
                   and retries < retry_cap):
                retries += 1
                result, more = execute_bounded(
                    registry, call.name, vr.args or {},
                    tool_timeout_s=timeout_cap, clock=clock)
                elapsed_ms += more
                if result.error_kind in TOOL_ERROR_KINDS:
                    failures += 1
                    timeouts += int(result.error_kind == ERROR_TIMEOUT)
            # Rung 2 — a sibling *corpus* tool, only with the question's intent
            # and only if the rung is still budgeted. The substitution is
            # disclosed (`fallback_from`) — a silent one is F1's disease.
            fallback: dict[str, Any] | None = None
            if (result.error_kind in TOOL_ERROR_KINDS
                    and result.rejection_class != POLICY_CLASS
                    and fallbacks_used < fallback_cap):
                sibling = SIBLING_TOOLS.get(call.name)
                if sibling and sibling in registry.names():
                    allowed = registry.properties(sibling)
                    s_args = {k: v for k, v in (vr.args or {}).items() if k in allowed}
                    fb_vr = registry.validate_args(sibling, json.dumps(s_args))
                    if fb_vr.ok:
                        fb_result, fb_elapsed = execute_bounded(
                            registry, sibling, fb_vr.args or {},
                            tool_timeout_s=timeout_cap, clock=clock)
                        elapsed_ms += fb_elapsed
                        fallbacks_used += 1
                        if fb_result.error_kind in TOOL_ERROR_KINDS:
                            failures += 1
                            timeouts += int(fb_result.error_kind == ERROR_TIMEOUT)
                        fb_result.fallback_from = call.name
                        result = fb_result
                        fallback = {"from": call.name, "to": sibling,
                                    "ok": fb_result.ok}
                        fallback_log.append({"step": len(steps) + 1, **fallback})
            if result.rejection_class == POLICY_CLASS and not step_policy:
                # A retry/substitution that ended in a policy rejection still
                # counts and still stops the run.
                policy_hits += 1
                step_policy = True
            if result.error_kind in TOOL_ERROR_KINDS:
                step_failures += 1
            tool_timeouts_total += timeouts

            msg, clamped = _tool_message(call, result, obs_budget)
            observations.append(msg)
            row = _call_row(call, vr, result, clamped,
                            elapsed_ms=elapsed_ms, retries=retries,
                            fallbacks=1 if fallback else 0, failures=failures,
                            timeouts=timeouts, fallback=fallback,
                            fp=fingerprint(call))
            rows.append(row)
            executed_rows.append(row)
            ledger_rows.append(_ledger_row(call, result, clamped))
            if result.refused:
                refusals += 1

        # Observations are appended as `tool` messages, never summarized — the
        # transcript is the memory, and the wire requires one result per call.
        transcript.append({"role": "assistant", "content": completion.text or None,
                           "tool_calls": calls})
        transcript.extend(observations)
        step_no = len(steps) + 1
        row_trace = step_trace(step_no, rows)
        if deferred and max_tool_calls_per_step is not None:
            row_trace["truncated"] = True             # calls recorded, not executed
            row_trace["deferred"] = [c.as_dict() for c in deferred]
        steps.append(row_trace)
        signatures.extend(row_trace["fingerprints"])

        # §7 — a policy rejection *stops* the run, before any diagnosis. It is a
        # wall, not a wall to route around: the model asked for something it may
        # not have, so self-correction must not apply and the loop must not
        # invite another attempt. Checked first so a policy hit owns the terminal,
        # not `no_progress`/`oscillation`/`tool_failure` arriving the same step.
        if step_policy:
            termination = "policy"
            break

        # §4 — diagnose *before* the cap. Repeat first (stricter/faster), then a
        # short cycle; both name the disease the cap could only call max_steps.
        if detect_repeat(signatures):
            no_progress_at_step = step_no
            termination = "no_progress"
            break
        if detect_oscillation(signatures, window):
            oscillation_detected = True
            termination = "oscillation"
            break
        # §6 rung 4 — every call this step failed as a tool error and the ladder
        # is spent: stop honestly, surfacing what evidence existed.
        if calls and step_failures == len(calls):
            termination = "tool_failure"
            break
        if cost_cap is not None and _run_cost(gateway.ledger, ledger_start) >= cost_cap:
            termination = "budget"                      # exit 3
            break
        if rejections > correct_cap:
            gave_up = True
            termination = "gave_up"
            break

    if termination != "answer":
        note = {"role": "system", "content": TERMINATION_NOTE[termination]}
        final = gateway.chat(transcript + [note], temperature=temperature,
                             max_tokens=max_tokens)
        answer_text = final.text

    mentioned = mentioned_ids(answer_text)
    grounding = grounding_over_transcript(mentioned, steps)
    evidence_ids = grounding["retrieved"]
    # Day 14 §7 — `asserted ⊆ returned`: every number/entity the answer asserts
    # must be a returned cell or a stated aggregate of one. The SQL sibling of
    # `cited ⊆ retrieved`, computed over the data records of the transcript.
    data_records = [r["data"] for r in executed_rows
                    if isinstance(r.get("data"), dict) and "columns" in r["data"]]
    asserted = asserted_subset_returned(answer_text, question=q, records=data_records)
    refused_all = bool(executed_rows) and all(r["refused"] for r in executed_rows)
    answered = bool((answer_text or "").strip())
    refusal_restated = bool(refused_all and answered and NOT_COVERED not in answer_text)
    contradictions = contradiction_rows(steps)
    observed = observed_failures(steps)
    failure_kind = _derive_failure_kind(
        termination=termination, gave_up=gave_up,
        last_rejection_kind=last_rejection_kind, refused_all=refused_all,
        any_tool_failure=observed > 0, any_timeout=tool_timeouts_total > 0,
        pivot_recovered=_pivot_recovered(steps), contradictions=contradictions,
        policy_hits=policy_hits)

    result: dict[str, Any] = {
        "question": q,
        "answer": answer_text,
        "turns": len(steps) + 1,
        "termination_reason": termination,
        "termination_note": AGENT_TERMINATIONS[termination],
        "truncated": termination != "answer",
        "gave_up": gave_up,
        "tool_steps": len(steps),
        "no_tool_call": not all_calls,
        "tool_calls": [c.as_dict() for c in all_calls],
        "capped_tool_calls": [c.as_dict() for c in capped_calls],
        "tool_ledger": ledger_rows,
        "steps": steps,
        "deferred_tool_calls": [c.as_dict() for c in deferred],
        "loop_deferred": "day12" if deferred else None,
        "evidence": evidence_ids,
        "refused": refused_all,
        "refusal_surfaced": refusals > 0,
        "refusal_restated": refusal_restated,
        "observed_refusals": refusals,
        "rejections": rejections,
        "citations": mentioned,
        "fabricated": grounding["fabricated"],
        "cited_subset_retrieved": grounding["cited_subset_retrieved"],
        "ledger": _ledger_delta(gateway.ledger, ledger_start),
        # Day 13 — the failure contract: the taxonomy, the wall, the detectors,
        # the ladder, and the contradiction flag. `fabricated` above is the one
        # every F-row asserts stays false.
        "failure_kind": failure_kind,
        "tool_timeouts": tool_timeouts_total,
        "observed_failures": observed,
        "no_progress_at_step": no_progress_at_step,
        "oscillation_detected": oscillation_detected,
        "distinct_step_fingerprints": len(set(signatures)),
        "contradiction": contradictions,
        "contradiction_covered": contradiction_coverage(contradictions, mentioned),
        "fallback_used": fallback_log,
        # Day 14 — the data contract: the *generated* SQL (the artifact Day 15
        # attacks), the data records (compact; rows live in the steps), and the
        # day's headline invariant `asserted ⊆ returned` over the whole run.
        "gen_sql": generated_sqls(steps),
        "data_tool_calls": len(data_records),
        "data_records": [_data_summary(r) for r in data_records],
        "asserted_subset_returned": asserted["ok"],
        "asserted_unsupported": (asserted["unsupported"]
                                 + asserted["unsupported_entities"]),
        # Day 15 — the safety contract: how many policy violations stopped the
        # run, the rejection classes seen, and the cost pre-check's estimates.
        # `policy_violations` must be 0 on every legitimate run; a non-zero value
        # is the day's hard stop, and it owns the run's `failure_kind`.
        "policy_violations": policy_violations(steps),
        "rejection_classes": rejection_classes(steps),
        "cost_estimates": cost_estimates(steps),
    }

    # Day 17 §4 — persist the conversation delta so the next turn in this session
    # is answerable from the stored exchange. The store records *what the run
    # already produced* (the question and the answer); it never invents a turn.
    if store_on and session_id:
        persist_conversation(state, principal, session_id, [
            {"role": "user", "content": q},
            {"role": "assistant", "content": answer_text},
        ], run_id=run_id)
    result["state_on_off"] = STATE_ON if store_on else STATE_OFF
    result["state_principal"] = principal
    result["session_id"] = session_id
    result["run_id"] = run_id
    result["conversation_turns"] = len(prior_turns)
    return result


def run_tool_answer(question: str, registry: ToolRegistry, gateway, *,
                    max_tool_turns: int | None = None,
                    temperature: float = 0.0, max_tokens: int = 2048,
                    ) -> dict[str, Any]:
    """Day 11 §6 — the single-turn tool answer, now a config of the loop.

    `run_agent(max_steps=1, max_tool_calls_per_step=cap)`. The Day-11 two-turn
    behavior (one tool turn, one grounded answer) is reproduced exactly — which
    is why T1…T7 stay green *by construction*: a loop that changed a single-tool
    answer would be a bug wearing an agent. Day 11's `max_tool_turns` is the
    per-step call cap; calls beyond it are deferred and recorded, exactly as
    before.
    """
    from ai_os.tools import max_tool_turns_from_env
    cap = max_tool_turns_from_env() if max_tool_turns is None else max(1, max_tool_turns)
    return run_agent(question, registry, gateway, max_steps=1,
                     max_tool_calls_per_step=cap, temperature=temperature,
                     max_tokens=max_tokens)
