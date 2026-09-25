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

Three decisions carry Day 12's honesty:

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

Everything Day 11 built — the registry, the gate-as-return-value, the Day 4
gateway's tool wire — is exercised **as-is**: `run_agent(max_steps=1)` *is*
Day 11's `run_tool_answer`, a config rather than a different program.

Economics, written plainly: one question costs **`tool_steps` chat turns +
`tool_steps` embeds + 1 final chat** (worst case `max_steps × 2 + 1` bills).
Day 9's "zero new embeds at evidence time" survives the loop, because every tool
still runs retrieval, not generation.
"""
from __future__ import annotations

import os
import re
from typing import Any

from ai_os.rag import NOT_COVERED
from ai_os.tools import ToolRegistry, ToolResult
from ai_os.trace import (
    AGENT_TERMINATIONS,
    grounding_over_transcript,
    step_trace,
)

# --------------------------------------------------------------- config knobs

DEFAULT_MAX_STEPS = 6           # the loop's step cap (exit 2)
DEFAULT_BUDGET_CHARS = 4000     # per-observation clamp, like Day 11's max_chars
DEFAULT_SELF_CORRECT = 2        # re-issues allowed after a rejection, per question


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
}


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


def _call_row(call, vr: ToolResult, result: ToolResult,
              clamped: bool) -> dict[str, Any]:
    """One executed call's audit row — the atom of the per-step trace."""
    row: dict[str, Any] = {
        "name": call.name,
        "arguments": call.arguments,
        "args_parsed": vr.args if vr.ok else None,
        "args_valid": vr.ok,
        "result_ok": result.ok,
        "refused": result.refused,
        "error": result.error,
    }
    if result.evidence:
        row["evidence_ids"] = result.doc_ids
    if result.data is not None:
        row["data"] = result.data
    if result.truncated:
        row["truncated"] = True
    if clamped:
        row["obs_clamped"] = True
    return row


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
    if result.evidence:
        row["evidence_ids"] = result.doc_ids
    if result.data is not None:
        row["data"] = result.data
    if result.truncated:
        row["truncated"] = True
    if clamped:
        row["obs_clamped"] = True
    return row


# -------------------------------------------------------------------- the loop

def run_agent(request: str, registry: ToolRegistry, gateway, *,
              max_steps: int | None = None,
              budget_chars: int | None = None,
              self_correct: int | None = None,
              budget_usd: float | None = None,
              max_tool_calls_per_step: int | None = None,
              temperature: float = 0.0, max_tokens: int = 2048,
              ) -> dict[str, Any]:
    """Reason → choose → execute → observe → reason again → answer.

    Three legitimate exits, all reported in `termination_reason`:

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
    """
    q = (request or "").strip()
    if not q:
        raise ValueError("request must not be empty")
    steps_cap = agent_max_steps_from_env() if max_steps is None else max(1, max_steps)
    obs_budget = (agent_budget_chars_from_env() if budget_chars is None
                  else budget_chars)
    correct_cap = (agent_self_correct_from_env() if self_correct is None
                   else max(0, self_correct))
    cost_cap = agent_cost_budget_from_env() if budget_usd is None else budget_usd
    ledger_start = len(gateway.ledger.records)

    transcript: list[dict[str, Any]] = [{"role": "user", "content": q}]
    steps: list[dict[str, Any]] = []
    all_calls: list[Any] = []
    executed_rows: list[dict[str, Any]] = []
    ledger_rows: list[dict[str, Any]] = []
    deferred: list[Any] = []
    capped_calls: list[Any] = []
    rejections = refusals = 0
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
        for call in calls:
            vr = registry.validate_args(call.name, call.arguments)
            result = registry.execute(call.name, vr.args or {}) if vr.ok else vr
            msg, clamped = _tool_message(call, result, obs_budget)
            observations.append(msg)
            row = _call_row(call, vr, result, clamped)
            rows.append(row)
            executed_rows.append(row)
            ledger_rows.append(_ledger_row(call, result, clamped))
            if not vr.ok:
                rejections += 1
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
    refused_all = bool(executed_rows) and all(r["refused"] for r in executed_rows)
    answered = bool((answer_text or "").strip())
    refusal_restated = bool(refused_all and answered and NOT_COVERED not in answer_text)

    return {
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
    }


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
