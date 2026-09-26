"""Day 16 §4 — the planner: a question → a **validated** plan (a DAG of tool calls).

Day 12's loop is a *reflex*: it decides the next action only after reading the last
observation, so nothing holds the task's intent in view. Day 16 adds the reflective
counterpart — an explicit agenda written **before** any tool runs. The planner is
that prologue, and it is deliberately small because the alphabet already exists:
a step names a **registered** tool (Day 11) with **schema-valid** args, exactly as a
single call would.

Three decisions carry the module:

1. **The plan is data, not a program.** A `Plan` is a list of `Step`s
   (`id`, `tool`, `args`, `depends_on`, `expected`) — the loop still runs the steps,
   the plan only fixes *which* step is next. A one-step plan *is* the Day 12 loop,
   which is why `run_agent(plan=None)` is byte-for-byte Day 12 and P9 can hold the
   line: a trivial question is not planned at all.
2. **Validation is fail-closed, Day 11's way.** Every step names a registered tool
   with schema-valid args, every dependency id exists, and the graph is **acyclic**.
   An invalid plan is *never executed speculatively* — it is rejected, with **one**
   informative retry (the plan-level analogue of Day 12's self-correction).
3. **Grounding is the plan's honesty.** Day 14 grounded generation on the DB schema
   and rejected a query against the wrong schema *before* running it; Day 16 grounds
   the *plan* the same way — an unbuildable step is a **grounding** failure, caught
   by code, not a model failure discovered at runtime. `expected` is what makes the
   plan gradeable: it is what a step's observation must show to count as done, and
   it is the input to the executor's re-plan gate.

Everything here is `MECH`: JSON parsing, set membership, a cycle check, and cost
arithmetic. The planner's one model call (`plan`, `replan`, `repair_args`) is the
day's new chat — and whether a *real* model writes a *good* plan is the day's
`PENDING` cell, owned by Day 21.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from ai_os.tools import ToolRegistry

# --------------------------------------------------------------- provenance
# Where a plan came from. The stub world supplies a **stored** plan (deterministic,
# offline, free); a real run gets a **model** plan (one chat); a lower rung of the
# Day 13 ladder may fall back to a per-class **template**. The trace records which,
# so a mechanism proof is never mistaken for a verdict.
PLAN_SOURCE_MODEL = "model"
PLAN_SOURCE_STORED = "stored"
PLAN_SOURCE_TEMPLATE = "template"

# §6 — the four rungs of the retry ladder are bounded, and replan/repair are the two
# that regenerate a *model* artifact mid-run (so they are the expensive ones).
DEFAULT_MAX_REPLANS = 1
DEFAULT_MAX_REPAIRS = 1


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def max_replans_from_env() -> int:
    return max(0, _env_int("AGENT_MAX_REPLANS", DEFAULT_MAX_REPLANS))


def max_repairs_from_env() -> int:
    return max(0, _env_int("AGENT_MAX_REPAIRS", DEFAULT_MAX_REPAIRS))


# ------------------------------------------------------------------- prompts
# "Write the prompt for the grader" (Days 8–11), aimed at a planner: the model must
# produce JSON code can validate, name only tools the registry holds, and let
# dependencies be edges rather than list positions. The last rule is the day's
# counterweight (P9) stated where the model can read it.
PLAN_PROMPT = (
    "You are an investigation planner. Turn the user's question into an explicit, "
    "ordered plan of tool calls BEFORE acting. Reply with JSON only, of the shape:\n"
    '{"steps": [{"id": "s1", "tool": "<registered tool>", "args": {<schema-valid '
    'arguments>}, "depends_on": [], "expected": "<what observation would make this '
    'step done>"}]}\n'
    "Rules:\n"
    "1. Every step names a *registered* tool (see the catalog) with arguments its "
    "schema accepts — an unbuildable step is rejected before anything runs.\n"
    "2. `depends_on` lists earlier step ids this step needs; the graph must be "
    "acyclic. A step that reads a record another step must locate depends on it.\n"
    "3. Decompose so every required sub-question of the request is its own atomic "
    "step — do not lump three lookups into one step that hides which part failed.\n"
    "4. Do NOT over-decompose: a question one query answers is ONE step. A plan is "
    "not thoroughness theatre.\n"
    "Reply with the JSON object and nothing else."
)

REPLAN_PROMPT = (
    "Your plan met reality and lost: an observation contradicted it, so the remaining "
    "steps no longer fit the evidence. Rewrite the REMAINING steps only. Reply with "
    "JSON of the same shape, {\"steps\": [...]}, using new ids for the steps you "
    "replace. Keep the same rules: registered tools, schema-valid args, acyclic "
    "dependencies, atomic steps, and no over-decomposition."
)

REPAIR_PROMPT = (
    "A tool call was rejected with a *repairable* error (a misspelled or unknown "
    "argument — not a forbidden request). Reply with JSON "
    '{"args": {<corrected arguments for the same tool>}}, or {"args": null} if the '
    "call cannot be fixed. Do not change the tool and do not rephrase a policy "
    "refusal — this rung exists only for honest mistakes."
)

# §3's counterweight — the planner's **entry rule**. Planning costs a chat and buys
# coverage/order/budget; a single-intent question has none of those to buy, so it
# goes straight to the Day 12 loop (P9). MECH: a clause/conjunction heuristic over
# the question, not a model call — an entry rule that needed a chat to decide
# whether to chat would have lost before it started.
_MULTI_INTENT_RE = re.compile(
    r"\b(and|then|why|how come|because|investigate|diagnose|debug|compare|across|"
    r"trace|follow|depends?)\b", re.IGNORECASE)


def needs_plan(question: str) -> dict[str, Any]:
    """The entry rule: does this question need an agenda at all? (`MECH`)

    Returns `{"needs_plan": bool, "reason": str}`. A plan is worth its chat when the
    question is multi-part or causal ("investigate why…", "…, and …"), and not when
    it is a single lookup ("Fetch order 1"). The trace records the decision, so
    "the planner over-planned a trivial question" (P9) has an owner: this function.
    """
    q = (question or "").strip()
    words = len(q.split())
    hits = sorted({m.group(0).lower() for m in _MULTI_INTENT_RE.finditer(q)})
    clauses = len([c for c in re.split(r"[.;?!]+", q) if c.strip()])
    if hits:
        return {"needs_plan": True,
                "reason": f"multi-intent markers {hits} → decompose before acting"}
    if clauses >= 2:
        return {"needs_plan": True,
                "reason": f"{clauses} clauses → more than one sub-question"}
    if words > 18:
        return {"needs_plan": True,
                "reason": f"{words} words → long request, plan the order"}
    return {"needs_plan": False,
            "reason": "single-intent question — the Day 12 loop already answers it"}


# ---------------------------------------------------------------------- shape
@dataclass
class Step:
    """One planned tool call plus the metadata a plan can be wrong in.

    Each field is a place a plan breaks (Day 16 §2): `id` (an ambiguous plan),
    `tool` (an unbuildable step → grounding), `args` (a malformed call at runtime),
    `depends_on` (out-of-order execution made unrepresentable), and `expected`
    (a step that "succeeds" without evidence). `expected` is also the re-plan
    gate's input: the executor compares the observation to it.
    """

    id: str
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    expected: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "tool": self.tool, "args": self.args,
                "depends_on": list(self.depends_on), "expected": self.expected}

    @classmethod
    def from_dict(cls, raw: dict[str, Any], *, index: int = 0) -> "Step":
        if not isinstance(raw, dict):
            raise ValueError(f"step {index + 1} is not an object")
        tool = raw.get("tool") or raw.get("name")
        if not isinstance(tool, str) or not tool.strip():
            raise ValueError(f"step {index + 1} names no tool")
        args = raw.get("args", raw.get("arguments", {}))
        if isinstance(args, str):
            try:
                args = json.loads(args or "{}")
            except json.JSONDecodeError as exc:
                raise ValueError(f"step {index + 1} args are not valid JSON: {exc}")
        if not isinstance(args, dict):
            raise ValueError(f"step {index + 1} args must be an object")
        deps = raw.get("depends_on", raw.get("depends", raw.get("needs", [])))
        if isinstance(deps, str):
            deps = [deps]
        if not isinstance(deps, list):
            raise ValueError(f"step {index + 1} depends_on must be a list")
        sid = raw.get("id")
        sid = str(sid) if sid not in (None, "") else f"s{index + 1}"
        expected = raw.get("expected", raw.get("expected_evidence", raw.get("why", "")))
        return cls(id=sid, tool=tool.strip(), args=dict(args),
                   depends_on=[str(d) for d in deps], expected=str(expected or ""))


@dataclass
class Plan:
    """The agenda: an ordered list of `Step`s, plus where it came from.

    `errors` is the fail-closed channel — a `Plan` that failed validation carries its
    reasons and is *never executed*. `raw` keeps the model's text for the trace, so a
    bad plan is a fixable artifact rather than a mysterious outcome (Day 14's rule
    that the query/artifact stays visible, applied to the plan).
    """

    question: str
    steps: list[Step]
    source: str = PLAN_SOURCE_MODEL
    raw: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def ids(self) -> list[str]:
        return [s.id for s in self.steps]

    @property
    def by_id(self) -> dict[str, Step]:
        return {s.id: s for s in self.steps}

    def step(self, sid: str) -> Step | None:
        return self.by_id.get(sid)

    def as_dict(self) -> dict[str, Any]:
        return {"question": self.question, "source": self.source,
                "steps": [s.as_dict() for s in self.steps],
                "errors": list(self.errors)}

    @classmethod
    def from_dict(cls, raw: dict[str, Any], *, source: str = PLAN_SOURCE_STORED
                  ) -> "Plan":
        """Build a *stored* plan (the stub world's supply) from plain data.

        This is how the offline suite pre-registers a plan the way the fixture set
        pre-registers a question: the model-facing `plan()` is the real world's
        path, and this is the deterministic one the harness grades.
        """
        if not isinstance(raw, dict) or "steps" not in raw:
            raise ValueError("a stored plan must be {'question':..., 'steps':[...]}")
        steps = [Step.from_dict(s, index=i) for i, s in enumerate(raw["steps"])]
        return cls(question=str(raw.get("question", "")), steps=steps,
                   source=str(raw.get("source", source)), raw="",
                   errors=list(raw.get("errors") or []))


# --------------------------------------------------------------- parsing
_FENCE_RE = re.compile(r"```(?:json)?\s*(.+?)```", re.DOTALL | re.IGNORECASE)


def _extract_json(text: str) -> Any:
    """The first JSON object/array in `text`, fenced or bare.

    A model asked for JSON often wraps it in prose or a ```json fence; the parser
    must not fail on the wrapper (that would make a *formatting* slip look like a
    *planning* failure). It fails only when there is no JSON to read.
    """
    body = text or ""
    fenced = _FENCE_RE.search(body)
    if fenced:
        body = fenced.group(1)
    for opener, closer in (("{", "}"), ("[", "]")):
        i, j = body.find(opener), body.rfind(closer)
        if i != -1 and j > i:
            try:
                return json.loads(body[i:j + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("no JSON plan object found in the reply")


def parse_plan(text: str) -> list[Step]:
    """Model reply text → `Step`s. Raises `ValueError` on anything unreadable."""
    obj = _extract_json(text)
    if isinstance(obj, dict):
        obj = obj.get("steps", obj.get("plan"))
    if not isinstance(obj, list) or not obj:
        raise ValueError("the plan carries no steps")
    return [Step.from_dict(raw, index=i) for i, raw in enumerate(obj)]


# ------------------------------------------------------------ validation (§3)
@dataclass
class Validation:
    """The plan validator's verdict: fail-closed, `ok` only when every check passed."""

    ok: bool
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "errors": list(self.errors)}


def validate_plan(steps: list[Step], registry: ToolRegistry) -> Validation:
    """Every step buildable, every edge resolvable, the graph acyclic. (`MECH`)

    The four checks, cheap first (Day 14's ordering): ids unique and non-empty →
    tools registered → args schema-valid → dependency ids exist → no cycle. The
    first failure does not hide the rest: all errors are collected, because the
    informative retry (and the human auditing a bad plan) wants the whole list.
    """
    errors: list[str] = []
    if not steps:
        return Validation(False, ["the plan has no steps"])

    known_tools = set(registry.names())
    seen_ids: set[str] = set()
    for step in steps:
        if not step.id:
            errors.append("a step has an empty id")
        elif step.id in seen_ids:
            errors.append(f"duplicate step id {step.id!r}")
        seen_ids.add(step.id)
        if step.tool not in known_tools:
            errors.append(f"step {step.id!r} names unregistered tool {step.tool!r} "
                          f"(known: {sorted(known_tools)})")
            continue
        vr = registry.validate_args(step.tool, json.dumps(step.args))
        if not vr.ok:
            errors.append(f"step {step.id!r} args invalid: {vr.error}")

    for step in steps:
        for dep in step.depends_on:
            if dep not in seen_ids:
                errors.append(f"step {step.id!r} depends on unknown step {dep!r}")
            if dep == step.id:
                errors.append(f"step {step.id!r} depends on itself")

    cycle = _find_cycle(steps)
    if cycle:
        errors.append("the dependency graph is cyclic: " + " → ".join(cycle))
    return Validation(not errors, errors)


def _find_cycle(steps: list[Step]) -> list[str] | None:
    """DFS cycle report (the offending path), or None when the graph is a DAG."""
    deps = {s.id: [d for d in s.depends_on if d in {x.id for x in steps}]
            for s in steps}
    state: dict[str, int] = {}            # 0 unvisited · 1 on stack · 2 done
    stack: list[str] = []
    order = {s.id: i for i, s in enumerate(steps)}

    def visit(sid: str) -> list[str] | None:
        state[sid] = 1
        stack.append(sid)
        for dep in sorted(deps.get(sid, []), key=lambda d: order.get(d, 0)):
            if state.get(dep) == 1:
                return stack[stack.index(dep):] + [dep]
            if state.get(dep, 0) == 0:
                found = visit(dep)
                if found:
                    return found
        stack.pop()
        state[sid] = 2
        return None

    for s in steps:
        if state.get(s.id, 0) == 0:
            found = visit(s.id)
            if found:
                return found
    return None


def topological_order(steps: list[Step]) -> list[str]:
    """A deterministic linearization of the DAG: dependencies before dependents.

    Kahn's algorithm with a stable tie-break on the plan's own order, so the same
    plan always executes in the same sequence — the property P4's replay depends on.
    Assumes a validated plan; a leftover node (a cycle) is appended rather than
    silently dropped, so a caller that skipped validation sees the anomaly.
    """
    deps = {s.id: [d for d in s.depends_on] for s in steps}
    ids = [s.id for s in steps]
    index = {sid: i for i, sid in enumerate(ids)}
    remaining = set(ids)
    out: list[str] = []
    while remaining:
        ready = [sid for sid in ids
                 if sid in remaining and all(d not in remaining for d in deps[sid])]
        if not ready:                       # a cycle: emit the rest in plan order
            out.extend(sorted(remaining, key=lambda s: index[s]))
            break
        for sid in ready:                   # `ready` is already in plan order
            out.append(sid)
            remaining.discard(sid)
    return out


# ------------------------------------------------------ cost, before spending (§4)
def estimate_cost(plan: Plan, *, per_step_chats: int = 1, plan_chats: int = 0,
                  answer_chats: int = 1) -> dict[str, Any]:
    """`steps × expected chats` against the run budget — Day 12's cap, plan-wide.

    Planning is not free: the bet is that the agenda **reduces N** by asking the
    right questions in the right order. This is the pre-spend arithmetic the notes
    call for; `plan_chats` is 0 when the plan was stored (a stub run) and 1 when a
    model wrote it. The estimate is *reported*, and an infeasible plan is refused
    rather than half-executed into a spent ledger.
    """
    n = len(plan.steps)
    step_chats = n * max(0, per_step_chats)
    total = step_chats + max(0, plan_chats) + max(0, answer_chats)
    return {"steps": n, "step_chats": step_chats, "plan_chats": max(0, plan_chats),
            "answer_chats": max(0, answer_chats), "expected_chats": total}


def within_budget(plan: Plan, budget_chats: int | None, **kw: Any) -> bool:
    """`None`/`0` means no cap (Day 12's `AGENT_BUDGET_USD` convention)."""
    if not budget_chats:
        return True
    return estimate_cost(plan, **kw)["expected_chats"] <= int(budget_chats)


# --------------------------------------------------------------- the alphabet
def tool_catalog(registry: ToolRegistry) -> str:
    """The registry as the planner's alphabet: names + descriptions (Day 11's
    descriptions, written for a chooser, are exactly what a planner must read)."""
    lines: list[str] = []
    for schema in registry.schemas():
        fn = schema.get("function") or {}
        desc = str(fn.get("description") or "").strip().replace("\n", " ")
        lines.append(f"- {fn.get('name')}: {desc[:240]}")
    return "\n".join(lines)


def _retry_note(errors: list[str]) -> str:
    return ("Your plan was rejected by validation:\n"
            + "\n".join(f"- {e}" for e in errors)
            + "\nReply with a corrected JSON plan.")


# --------------------------------------------------------------- the one chat
def plan(question: str, registry: ToolRegistry, gateway, *,
         temperature: float = 0.0, max_tokens: int = 1024,
         retries: int = 1) -> Plan:
    """One Day 4 chat → a `Plan`. The model proposes, **code disposes**.

    The plan is validated before it is returned; on failure the model gets **one**
    informative retry carrying the validator's errors (the plan-level analogue of
    Day 12's self-correction, and the same shape: the error message *is* the
    interface). A plan that is still invalid comes back with `errors` populated and
    is never executed — fail-closed, and the retry is bounded.
    """
    q = (question or "").strip()
    if not q:
        raise ValueError("question must not be empty")
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": PLAN_PROMPT},
        {"role": "user", "content": f"Question: {q}\n\nTools:\n{tool_catalog(registry)}"},
    ]
    steps: list[Step] = []
    errors: list[str] = []
    raw = ""
    for attempt in range(max(0, retries) + 1):
        completion = gateway.chat(messages, temperature=temperature,
                                 max_tokens=max_tokens)
        raw = completion.text
        try:
            steps = parse_plan(raw)
            errors = validate_plan(steps, registry).errors
        except ValueError as exc:
            steps, errors = [], [str(exc)]
        if not errors:
            break
        if attempt < retries:
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": _retry_note(errors)})
    return Plan(question=q, steps=steps, source=PLAN_SOURCE_MODEL, raw=raw,
                errors=errors)


def replan(plan: Plan, step: Step, observation: dict[str, Any],
           registry: ToolRegistry, gateway, *,
           temperature: float = 0.0, max_tokens: int = 1024) -> Plan:
    """Rung 4 — rewrite the **remaining plan** because the evidence invalidated it.

    The most expensive and least deterministic rung: it regenerates a *model*
    artifact mid-run. The stub proves the **routing** (that it fires on a
    contradiction and that a policy wall never triggers it); the *quality* of a
    model's replan is the day's `PENDING` cell. Returns a `Plan` of the new steps
    (validated; possibly invalid, in which case the caller stops honestly).
    """
    prompt = [
        {"role": "system", "content": REPLAN_PROMPT},
        {"role": "user", "content": (
            f"Question: {plan.question}\n\n"
            f"Original plan:\n{json.dumps(plan.as_dict()['steps'], indent=1)}\n\n"
            f"Step {step.id!r} ({step.tool}) expected: {step.expected or '(unstated)'}\n"
            f"Its observation: {json.dumps(observation, default=str)[:1200]}\n\n"
            f"Tools:\n{tool_catalog(registry)}")},
    ]
    completion = gateway.chat(prompt, temperature=temperature, max_tokens=max_tokens)
    try:
        steps = parse_plan(completion.text)
        errors = validate_plan(steps, registry).errors
    except ValueError as exc:
        steps, errors = [], [str(exc)]
    return Plan(question=plan.question, steps=steps, source=PLAN_SOURCE_MODEL,
                raw=completion.text, errors=errors)


def repair_args(step: Step, error: dict[str, Any], gateway, *,
                temperature: float = 0.0, max_tokens: int = 512
                ) -> dict[str, Any] | None:
    """Rung 1 — fix **the arguments of the same step** after a repairable rejection.

    One chat, and only for an honest mistake (Day 15's rule: a `policy` refusal is
    never repaired, which the executor enforces by not calling this rung on one).
    Returns the corrected args, or `None` when the model cannot fix it — either way
    the ladder is bounded and the executor owns what happens next.
    """
    completion = gateway.chat([
        {"role": "system", "content": REPAIR_PROMPT},
        {"role": "user", "content": (
            f"Tool {step.tool!r} was called with args "
            f"{json.dumps(step.args, default=str)} and rejected: "
            f"{json.dumps(error, default=str)[:800]}")},
    ], temperature=temperature, max_tokens=max_tokens)
    try:
        obj = _extract_json(completion.text)
    except ValueError:
        return None
    if isinstance(obj, dict) and isinstance(obj.get("args"), dict):
        return obj["args"]
    return None
