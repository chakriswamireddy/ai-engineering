"""Day 11 §3/§5 — the tool layer: the old seams made declarative.

Days 8–10 built a retriever that answers questions; Day 11 lets the *model*
ask it. The tools are not a new subsystem — they are exactly the seams that
already exist, exposed as function schemas:

    search_saleor_docs()  → Day 6/8 retrieve() over data/store.json
    search_saleor_code()  → Day 10 retrieve() over data/code_store.json
    get_product/order/customer() → Day 14 fixed read-only queries (ai_os/data.py)
    run_saleor_sql()      → Day 14 generated read-only SQL (the open tool, Day 15's target)
    describe_saleor_schema() → Day 14 on-demand schema expansion

Three decisions carry the module:

1. **A tool result is the evidence record, not prose.** The retrieval tools
   return the same top-K shape the harness has graded since Day 7
   (`doc_id`/`heading`/`score`/`position`, plus `module#Symbol` for code), so
   `cited ⊆ retrieved` stays plain set membership — the Day-6 invariant did not
   move to a weaker place, it moved *home*, to where evidence is produced.
2. **The refusal gate is a return value.** Day 7's `should_refuse` ran as a
   `/rag` route outcome; here it runs *inside* the tool and returns
   `{refused: true}`. Same τ, same fused score, same honesty, new signature —
   and the model must be told a refusal is not an answer to restate.
3. **Rejection is a result, never an exception.** A missing argument, a wrong
   `enum`, an unknown key, malformed JSON, an unknown tool name: each is a
   `ToolResult.failure(...)` whose message names the offending field. Day 12's
   loop self-corrects against exactly this message; a raised exception would
   kill the turn instead.

Everything here is `MECH`: string/map arithmetic over stored schemas, stores,
and fixture records. Zero chats, zero embeds at the registry's own seam.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from ai_os import audit as audit_mod
from ai_os import observability as obs
from ai_os import permissions as perms_mod
from ai_os.data import (
    DIALECT_NOTE,
    GLOSSARY,
    DataAgent,
    describe_table,
    make_data_store,
    schema_card_text,
    table_names,
)
from ai_os.gate import should_refuse
from ai_os.rag import Chunk
from ai_os.retrieval import CODE_REWRITES, Retriever, code_tokens

# ------------------------------------------------------------------ defaults

DEFAULT_TAU = 0.55              # the gate threshold (Day 7); re-calibration is Day 21
DEFAULT_TOP_K = 4
DEFAULT_DOC_BUDGET = 1200       # chars of doc evidence per tool result
DEFAULT_CODE_BUDGET = 4000      # code bodies are long; the symbol is the unit
DEFAULT_MAX_TOOL_TURNS = 1      # Day 11's boundary — a config, not a constant


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def tool_tau() -> float:
    return _env_float("TOOL_GATE_TAU", DEFAULT_TAU)


def tool_top_k() -> int:
    return _env_int("TOOL_TOP_K", DEFAULT_TOP_K)


def max_tool_turns_from_env() -> int:
    """Day 11's single-turn cap. Day 12 lifts it by changing this one knob."""
    return max(1, _env_int("TOOL_MAX_TURNS", DEFAULT_MAX_TOOL_TURNS))


# ============================================================ Day 13 — taxonomy
# §2 — the third error family. Day 11's rejection is "you asked *wrong*" (a
# schema problem the model fixes by re-issuing different args); Day 13's tool
# error is "you asked *right, the world broke*" (an execution problem the loop
# fixes by retrying the *same* call, then climbing the fallback ladder). Same
# `{ok: false, ...}` surface, a different `error_kind`, a different recovery —
# which is why they get different names and different counters.
ERROR_INVALID_ARGS = "invalid_args"     # Day 11 — schema rejection (self-correct)
ERROR_UNKNOWN_TOOL = "unknown_tool"     # Day 11 — registry rejection (self-correct)
ERROR_TIMEOUT = "timeout"               # Day 13 — the wall clock (retry then fall back)
ERROR_TOOL = "tool_error"               # Day 13 — the tool raised / downstream 5xx
ERROR_NO_DATA = "no_data"               # Day 13 — the resolver found nothing
ERROR_INTERNAL = "internal"             # Day 13 — the harness itself is broken
# Day 19 — a *role* gate. The tool exists in the system but not in this
# specialist's tool group: an out-of-role call is a **policy** rejection (privileges
# beat parsers, Day 15, applied between loops), so the loop stops rather than
# self-correcting its way into another role's surface.
ERROR_ROLE = "role_violation"
# Day 26 — the *permission* gate. A tool that is registered but not granted to the
# principal, an unknown caller, a missing decision, or a result that broke its
# contract is a **policy** rejection on the same `{ok: false}` surface — so Day 12's
# STOP rule, Day 13's guard ring, the fallback ladder's policy exclusion, and the
# trace all handle it with code that already exists and **no new control plane**.
# It must never arrive as `repairable`: a denial that hands the model the retry the
# denial exists to prevent is a bypass with better manners.
ERROR_PERMISSION = "permission_denied"

# Day 26 §7 — the canonical tool names, in registration order. The permission
# loader validates that every grant names a registered tool against exactly this
# set, so a grant for a tool nobody registered is a load error (never a partial
# load), and `default` = these seven is a fact the C9 control can assert.
DEFAULT_TOOL_NAMES: tuple[str, ...] = (
    "search_saleor_docs", "search_saleor_code", "get_product", "get_order",
    "get_customer", "run_saleor_sql", "describe_saleor_schema")

# Rejections the model fixes with a *different* call. They are never retried
# with the same args — "same wrong thing, louder" is Day 12's disease.
REJECTION_KINDS = frozenset({ERROR_INVALID_ARGS, ERROR_UNKNOWN_TOOL})

# Execution failures the fallback ladder owns. `no_data` is excluded on purpose:
# an empty result is a *finding* to degrade from, not a broken tool to retry.
TOOL_ERROR_KINDS = frozenset({ERROR_TIMEOUT, ERROR_TOOL, ERROR_INTERNAL})


@dataclass
class ToolError:
    """Day 13 §6 — the typed execution failure, an *observation*, never a raise.

    The loop must not have to catch exceptions: the moment a stub is swapped for
    a real resolver (Day 14) an uncaught raise would turn a well-tested run into
    an untested one. So execution failure is a value with a `retryable` flag the
    ladder reads — `timeout` and a transient `tool_error` get one same-arg retry;
    a `no_data`/`internal` failure does not.
    """

    error_kind: str
    message: str
    retryable: bool

    def as_dict(self) -> dict[str, Any]:
        return {"error_kind": self.error_kind, "message": self.message,
                "retryable": self.retryable}


# ------------------------------------------------------------ result shapes

@dataclass
class ToolResult:
    """The one shape every tool returns — success, refusal, or rejection.

    `evidence` is the retrieval contract (top-K records, budget-clamped,
    `cited ⊆ retrieved` decidable); `data` is a data-tool record; `error` is a
    human- and model-readable message; `refused` marks the Day-7 gate verdict.
    All three are normal conversation messages — none is an exception.
    """

    name: str
    ok: bool
    args: dict[str, Any] | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)
    data: Any = None
    error: str | None = None
    refused: bool = False
    truncated: bool = False
    # Day 13 — the typed-failure columns. `error_kind` names the disease
    # (`invalid_args`/`unknown_tool`/`timeout`/`tool_error`/`no_data`/`internal`);
    # `retryable` is the ladder's gate; `fallback_from` records a disclosed
    # substitution (the docs tool answering where the code tool died).
    # Day 15 adds `rejection_class`: the *follow-up* the loop must take. A
    # `repairable` (a syntax / unknown-column error) may be self-corrected; a
    # `policy` (a write, a denied table/column, an ambiguous definition, an
    # over-cost or runaway query) must NOT — the loop stops and surfaces it.
    error_kind: str | None = None
    retryable: bool = False
    fallback_from: str | None = None
    rejection_class: str | None = None
    # Day 26 §7 — the permission decision, carried *on the value* rather than
    # beside it. `permission` is the serialized four-key view
    # (`{"decision","code","effect","data"}`) the trace promotes; `decision` is the
    # full `Decision` object `execute` receives, so the decision cannot be
    # separated from the result a future call site holds. Neither is serialized
    # into the model-facing `as_dict()`/`as_message()`: the decision is metadata
    # about the call, not part of the observation, and adding it to the wire would
    # move a legitimate run's bytes (C9).
    permission: dict[str, Any] | None = None
    decision: Any = None
    # Day 26 §7 — the columns the result contract dropped (and *recorded*): a
    # redaction that is not in the record and not in the audit entry is Day 24's
    # missing span arriving through the back door.
    redacted: list[str] = field(default_factory=list)

    @classmethod
    def success(cls, name: str, *, args: dict | None = None,
                evidence: list[dict] | None = None, data: Any = None,
                truncated: bool = False,
                ) -> "ToolResult":
        return cls(name=name, ok=True, args=args, evidence=list(evidence or []),
                   data=data, truncated=truncated)

    @classmethod
    def failure(cls, name: str, error: str, *,
                args: dict | None = None,
                error_kind: str = ERROR_INVALID_ARGS,
                retryable: bool = False,
                rejection_class: str | None = None) -> "ToolResult":
        return cls(name=name, ok=False, args=args, error=error,
                   error_kind=error_kind, retryable=retryable,
                   rejection_class=rejection_class)

    @classmethod
    def tool_error(cls, name: str, *, error_kind: str, message: str,
                   args: dict | None = None, retryable: bool = False,
                   rejection_class: str | None = None,
                   ) -> "ToolResult":
        """Day 13 — a tool error is an *observation*, not an exception."""
        return cls(name=name, ok=False, args=args, error=message,
                   error_kind=error_kind, retryable=retryable,
                   rejection_class=rejection_class)

    @classmethod
    def refusal(cls, name: str, *, args: dict | None = None,
                reason: str = "no defensible match in the corpus (gate refused)",
                ) -> "ToolResult":
        return cls(name=name, ok=True, args=args, refused=True, error=reason)

    @classmethod
    def rejected(cls, name: str, *, reason: str, args: dict | None = None,
                 error_kind: str = "policy_violation",
                 rejection_class: str = "policy") -> "ToolResult":
        """Day 25 §7 — a *policy-class* refusal, normalized into the existing shape.

        The injection firewall's privileged-action refusal becomes exactly the
        `ToolResult` Day 15 already produces for a forbidden request, so Day 12's
        STOP rule, Day 13's guard ring, the `policy_violations` counter, and the
        trace handle it with code that already exists — **no parallel mechanism**.
        `refused=True` marks it a refusal; `rejection_class="policy"` is the STOP.
        """
        return cls(name=name, ok=False, args=args, refused=True, error=reason,
                   error_kind=error_kind, rejection_class=rejection_class,
                   retryable=False)

    @property
    def doc_ids(self) -> list[str]:
        return [str(r.get("doc_id")) for r in self.evidence if r.get("doc_id")]

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"ok": self.ok, "tool": self.name}
        if self.args is not None:
            out["args"] = self.args
        if self.refused:
            out["refused"] = True
            out["reason"] = self.error
        elif not self.ok:
            out["error"] = self.error
            if self.error_kind:
                out["error_kind"] = self.error_kind
            if self.rejection_class:
                # The model is told *which class* it is — a repairable error
                # invites a fixed query; a policy error is surfaced, never
                # handed back as something to rephrase around.
                out["rejection_class"] = self.rejection_class
            if self.retryable:
                out["retryable"] = True
        elif self.evidence:
            out["evidence"] = self.evidence
        elif self.data is not None:
            out["data"] = self.data
        if self.truncated:
            out["truncated"] = True
        if self.fallback_from:
            # Rung 2 must *say* it fell back — a silent substitution is F1's
            # disease arriving through the back door.
            out["fallback_from"] = self.fallback_from
        return out

    def as_message(self) -> str:
        """The `tool` message content: the evidence, serialized, never prose."""
        return json.dumps(self.as_dict(), ensure_ascii=False)


# ------------------------------------------------------ the hand-rolled schema validator

_TYPE_NAMES = {
    "string": str, "integer": int, "number": (int, float),
    "boolean": bool, "object": dict, "array": list,
}


def _check_type(value: Any, spec_type: str) -> bool:
    if spec_type == "integer":
        # JSON has one number type; a float that *is* an int is accepted, and
        # the value is never coerced here — coercion is Day 11's cardinal sin.
        return isinstance(value, int) and not isinstance(value, bool)
    if spec_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if spec_type == "boolean":
        return isinstance(value, bool)
    py = _TYPE_NAMES.get(spec_type)
    return True if py is None else isinstance(value, py)


def validate_args(schema: dict[str, Any], args_raw: Any) -> tuple[bool, Any]:
    """Rule-1 JSON-Schema subset: the ten lines we need, hand-rolled.

    Returns `(ok, args_or_error_message)`. Every failure names the field —
    that message is the interface Day 12's loop corrects against.
    """
    if not isinstance(args_raw, str):
        # Some wires hand a dict already; normalize without coercing values.
        args_raw = json.dumps(args_raw) if args_raw is not None else "{}"
    try:
        args = json.loads(args_raw)
    except json.JSONDecodeError:
        return False, "malformed arguments: not valid JSON"
    if not isinstance(args, dict):
        return False, "arguments must be a JSON object"

    params = schema.get("parameters") or {}
    props: dict[str, Any] = params.get("properties") or {}
    required: list[str] = params.get("required") or []

    missing = [p for p in required if p not in args]
    if missing:
        return False, f"missing required argument(s): {missing}"
    unknown = [k for k in args if k not in props]
    if unknown:
        return False, f"unknown argument(s): {unknown} (known: {sorted(props)})"

    for key, value in args.items():
        spec = props.get(key) or {}
        spec_type = spec.get("type")
        if spec_type and not _check_type(value, spec_type):
            return False, (f"argument {key!r} must be {spec_type}, "
                           f"got {type(value).__name__}")
        enum = spec.get("enum")
        if enum is not None and value not in enum:
            return False, f"argument {key!r} must be one of {enum}, got {value!r}"
        # Day 25 §6 — the argument *shape* restrictions, enforced (not advisory).
        # A free-text argument is a channel: whatever the model puts there leaves the
        # system through a provider call, a log, a trace, or a search index. An
        # energetic `maxLength` + `pattern` turns "smuggle 4 KB out through a query"
        # into a *validation failure* the loop already knows how to route — and the
        # message names the field and the bound, because the loop self-corrects
        # against exactly this message. The value is never coerced or truncated.
        if isinstance(value, str):
            max_len = spec.get("maxLength")
            if max_len is not None and len(value) > max_len:
                return False, (f"argument {key!r} exceeds maxLength={max_len} "
                               f"(got {len(value)} chars)")
            min_len = spec.get("minLength")
            if min_len is not None and len(value) < min_len:
                return False, (f"argument {key!r} must be at least minLength="
                               f"{min_len} (got {len(value)} chars)")
            pattern = spec.get("pattern")
            if pattern is not None:
                try:
                    matched = re.fullmatch(pattern, value) is not None
                except re.error:
                    return False, (f"argument {key!r}: schema pattern {pattern!r} "
                                   "is not a valid regular expression")
                if not matched:
                    return False, (f"argument {key!r} must match pattern {pattern!r}")
        if isinstance(value, int) and not isinstance(value, bool):
            if spec.get("minimum") is not None and value < spec["minimum"]:
                return False, (f"argument {key!r} must be >= {spec['minimum']}, "
                               f"got {value}")
            if spec.get("maximum") is not None and value > spec["maximum"]:
                return False, (f"argument {key!r} must be <= {spec['maximum']}, "
                               f"got {value}")
    return True, args


# ------------------------------------------------------------------- registry

class ToolRegistry:
    """name → `{schema, fn, grant}`; the **one choke point** every call passes.

    `schemas()` feeds the gateway, `validate_args` produces the decision, and
    `execute` dispatches — and, when the registry is **governed**
    (`permissions=` set), it is the *only* code that may say no. There are exactly
    five ways this system reaches a tool — the loop, `run_tool_answer`, Day 16's
    `executor.run_plan`, the fallback ladder's sibling substitution, and Day 19's
    `RoleAgent.run` — and all five go through `validate_args` + `execute`, so the
    check belongs here and a hypothetical sixth path is governed by omission.

    Three Day 26 rules live in this class and nowhere else:

    * **registration is a grant.** `register()` without a grant raises: a new tool
      cannot arrive with authority nobody declared. `build_default_registry`
      supplies each tool's declaration from `permissions.yaml`.
    * **the decision travels on the value.** `validate_args` attaches the
      `Decision` (and its four-key `permission` view) to the `ToolResult`, and
      `execute(name, args, decision=None)` **denies when it is handed no
      decision** — the clause that converts "every caller remembered" into "every
      caller is safe by default".
    * **the result is checked before it is serialized.** `check_result` runs *after*
      the tool returns and *before* the value becomes a `tool` message; a violation
      keeps the shape (`columns`/`query_kind`/`executed`/`redacted`), drops every
      value, and routes as a `policy` failure. A redaction drops the named columns
      and records them.

    Day 19's role surface survives: a registry may be **scoped** to a role, in
    which case a tool that exists elsewhere is *denied* here with a typed
    `role_violation`. `scope()` is now a **derived view** of the grant file — a
    role's tool list is the tools its principal is granted, filtered by the role's
    declared interest — so `ROLE_TOOLS` and the grants can no longer disagree.

    With `permissions=None` the registry is **ungoverned**: no permission check, no
    result contract, no audit — Days 11–25 exactly as they were. That is the C9
    off-switch, and it is why the pre-Day-26 fixtures that call `execute()`
    directly (a tokenless `search_saleor_code` probe) still pass.
    """

    def __init__(self, *, denied: dict[str, str] | None = None,
                 permissions: "perms_mod.PermissionSet | None" = None,
                 principal: str | None = None,
                 audit: "audit_mod.AuditLog | audit_mod.NullAuditLog | None" = None
                 ) -> None:
        self._tools: dict[str, dict[str, Any]] = {}
        self._grants: dict[str, Any] = {}
        self._denied: dict[str, str] = dict(denied or {})
        self._permissions = permissions
        self._principal = (principal if principal is not None
                           else (perms_mod.principal_from_env()
                                 if permissions is not None else None))
        self._audit = audit if audit is not None else audit_mod.NullAuditLog()
        # Day 26 — provenance for the audit entry. The loop sets these on the
        # registry it owns; unset, the entry records `None` and the decision is
        # still a pure function of (principal, tool, args, perms).
        self.run_id: str | None = None
        self.trace_id: str | None = None

    # ------------------------------------------------------------------ setup
    def register(self, name: str, schema: dict[str, Any],
                 fn: Callable[[dict[str, Any]], ToolResult], *,
                 grant: Any = None) -> None:
        if schema.get("name") != name:
            raise ValueError(f"schema name {schema.get('name')!r} != {name!r} "
                             "— a renamed tool is a broken tool_calls")
        if grant is None and self._permissions is not None:
            # §3 — "no grant, no tool". A tool with no declared effect/data class
            # is a tool whose authority is whatever its author happened to write
            # down; the refusal here is what makes that impossible. It is enforced
            # on a **governed** registry — one that carries a permission model. An
            # ungoverned harness registry has no authority model to violate, and
            # the seven system tools declare their grants either way.
            raise ValueError(
                f"tool {name!r} registered without a grant — every tool must "
                "declare effect and data class (add a grant in permissions.yaml)")
        self._tools[name] = {"schema": schema, "fn": fn}
        if grant is not None:
            self._grants[name] = grant

    def names(self) -> list[str]:
        return list(self._tools)

    def granted_tools(self) -> list[str]:
        """The principal's granted slice, in registration order (the prompt surface)."""
        if self._permissions is None:
            return self.names()
        return [n for n in self.names()
                if n in set(self._permissions.granted_tools(self._principal))]

    def permissions_on(self) -> bool:
        return self._permissions is not None

    def grant(self, name: str) -> Any:
        return self._grants.get(name)

    def denied(self) -> dict[str, str]:
        """The tools this registry holds but refuses (an out-of-role gate)."""
        return dict(self._denied)

    def set_context(self, *, run_id: str | None = None,
                    trace_id: str | None = None) -> None:
        """Attach the run/trace ids the audit entry records (optional)."""
        if run_id is not None:
            self.run_id = run_id
        if trace_id is not None:
            self.trace_id = trace_id

    def scope(self, names: list[str] | tuple[str, ...], *,
              reason: str = "out of role") -> "ToolRegistry":
        """A role's view: the tools it is granted, filtered by `names`.

        The denied tools are still *known* — the denial names them — so an
        out-of-role call is a **policy** rejection (`role_violation`), not an
        `unknown_tool`: the difference between "you may not" and "there is no such
        thing" is exactly the privilege boundary Day 15 drew. When governed, the
        view is intersected with the principal's grants, so a **wrong**
        `ROLE_TOOLS` entry cannot reach a tool the principal was never granted.
        """
        keep = set(names) & set(self._tools)
        if self._permissions is not None:
            keep &= set(self._permissions.tools_for(self._principal))
        scoped = ToolRegistry(
            denied={n: f"{reason}: {n} is not in this role's tool group"
                    for n in self._tools if n not in keep},
            permissions=self._permissions, principal=self._principal,
            audit=self._audit)
        for n, tool in self._tools.items():
            if n in keep:
                scoped._tools[n] = tool
                if n in self._grants:
                    scoped._grants[n] = self._grants[n]
        return scoped

    def schemas(self) -> list[dict[str, Any]]:
        """What the gateway sends the model — names stable, descriptions loud.

        §§3/4 — least privilege at the *contract surface*: when governed, an
        ungranted tool is not merely refused when called, it is **never named in
        the prompt**, so the model cannot spend turns discovering it exists.
        """
        granted = set(self.granted_tools())
        return [{"type": "function", "function": t["schema"]}
                for n, t in self._tools.items() if n in granted]

    # ------------------------------------------------------------- validation
    def _effective_schema(self, name: str) -> dict[str, Any]:
        """The schema a call is validated against — the *grant's* argument rules
        merged over the model-facing schema, so one function (`validate_args`)
        enforces both and an argument is validated once, by one vocabulary."""
        schema = self._tools[name]["schema"]
        grant = self._grants.get(name)
        if grant is None or not getattr(grant, "args", None):
            return schema
        params = dict(schema.get("parameters") or {})
        props = dict(params.get("properties") or {})
        merged: dict[str, Any] = {}
        for key, spec in props.items():
            rule = grant.args.get(key)
            if rule is None:
                merged[key] = spec
                continue
            s = dict(spec)
            if rule.maxLength is not None:
                s["maxLength"] = rule.maxLength
            if rule.pattern is not None:
                s["pattern"] = rule.pattern
            if rule.enum is not None:
                s["enum"] = list(rule.enum)
            if rule.minimum is not None:
                s["minimum"] = rule.minimum
            if rule.maximum is not None:
                s["maximum"] = rule.maximum
            merged[key] = s
        params["properties"] = merged
        merged_schema = dict(schema)
        merged_schema["parameters"] = params
        return merged_schema

    def validate_args(self, name: str, args_raw: Any) -> ToolResult:
        if name in self._denied:
            # Day 19 §7 — the role gate. A tool the system holds but this role does
            # not is a *policy* refusal (privileges beat parsers, between loops):
            # `policy` keeps it out of both ladder rungs and stops the loop.
            return ToolResult.failure(
                name, self._denied[name], error_kind=ERROR_ROLE,
                rejection_class="policy")
        if name not in self._tools:
            return ToolResult.failure(
                name, f"unknown tool {name!r} (known: {self.names()})",
                error_kind=ERROR_UNKNOWN_TOOL)
        decision = None
        if self._permissions is not None:
            decision = perms_mod.check_call(self._principal, name, args_raw,
                                            perms=self._permissions)
            if not decision.allow:
                # §5 — a permission denial is a **policy** refusal, never a
                # `repairable` one: the model must not be handed the retry the
                # denial exists to prevent. It is audited at the point it happens.
                res = ToolResult.failure(name, decision.reason,
                                         error_kind=ERROR_PERMISSION,
                                         rejection_class="policy")
                res.permission = decision.meta()
                res.decision = decision
                self._record_audit(name, decision, args_raw, None)
                return res
        ok, result = validate_args(self._effective_schema(name), args_raw)
        if not ok:
            res = ToolResult.failure(name, result, error_kind=ERROR_INVALID_ARGS)
            if decision is not None:
                res.permission = decision.meta()
                res.decision = decision
            return res
        res = ToolResult.success(name, args=result)
        if decision is not None:
            res.permission = decision.meta()
            res.decision = decision
        return res

    def properties(self, name: str) -> dict[str, Any]:
        """The tool's declared argument properties (Day 13's sibling-arg filter)."""
        tool = self._tools.get(name)
        if tool is None:
            return {}
        return (tool["schema"].get("parameters") or {}).get("properties") or {}

    # -------------------------------------------------------------- execution
    def execute(self, name: str, args: dict[str, Any],
                decision: Any = None) -> ToolResult:
        if name in self._denied:
            return ToolResult.failure(
                name, self._denied[name], error_kind=ERROR_ROLE,
                rejection_class="policy")
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult.failure(
                name, f"unknown tool {name!r} (known: {self.names()})",
                error_kind=ERROR_UNKNOWN_TOOL)
        if self._permissions is not None:
            gate = self._execution_gate(name, decision, args)
            if gate is not None:
                return gate
        try:
            result = tool["fn"](args or {})
        except Exception as exc:                     # never crash the turn
            # Day 13 §2 — the executor owns "the tool broke": a typed, retryable
            # observation the ladder reads, not an exception into the loop.
            return ToolResult.tool_error(
                name, error_kind=ERROR_TOOL,
                message=f"tool error: {exc.__class__.__name__}: {exc}",
                args=args, retryable=True)
        if not isinstance(result, ToolResult):
            return ToolResult.tool_error(
                name, error_kind=ERROR_INTERNAL,
                message="tool returned a malformed result", args=args)
        if result.args is None:
            result.args = dict(args or {})
        if self._permissions is not None:
            result = self._apply_result_contract(name, result, decision, args)
        return result

    def _execution_gate(self, name: str, decision: Any,
                        args: Any) -> ToolResult | None:
        """`execute` refuses to run without a decision (§5, decision 1).

        A check in `validate_args` alone is a check every caller must remember to
        route through; a check that refuses to run without one is a check where a
        *future* call site is safe by omission. `decision=None` here is an
        internal-invariant violation: `policy`, and loud.
        """
        if decision is None:
            dec = self._reject_decision(
                name, perms_mod.CODE_MISSING_DECISION,
                "execute() received no permission decision — the caller did not "
                "route this call through validate_args")
            res = ToolResult.failure(name, dec.reason, args=dict(args or {}),
                                     error_kind=ERROR_PERMISSION,
                                     rejection_class="policy")
            res.permission = dec.meta()
            res.decision = dec
            self._record_audit(name, dec, args, None)
            return res
        allow = (decision.get("decision") == "allow" if isinstance(decision, dict)
                 else bool(getattr(decision, "allow", False)))
        if not allow:
            code = (decision.get("code") if isinstance(decision, dict)
                    else getattr(decision, "code", perms_mod.CODE_NO_GRANT))
            dec = self._reject_decision(name, code or perms_mod.CODE_NO_GRANT,
                                        f"denied at execution: {code}")
            res = ToolResult.failure(name, dec.reason, args=dict(args or {}),
                                     error_kind=ERROR_PERMISSION,
                                     rejection_class="policy")
            res.permission = dec.meta()
            res.decision = dec
            self._record_audit(name, dec, args, None)
            return res
        return None

    def _reject_decision(self, name: str, code: str,
                         reason: str) -> "perms_mod.Decision":
        grant = self._grants.get(name)
        known = self._permissions.is_known(self._principal) if self._permissions else True
        return perms_mod.Decision(
            False, code, reason, grant,
            effect=getattr(grant, "effect", None), data=getattr(grant, "data", None),
            unknown_principal_fallback=not known)

    def _apply_result_contract(self, name: str, result: ToolResult, decision: Any,
                               args: Any) -> ToolResult:
        """§5 decision 5 — the output half of the policy, and no partial rows.

        Runs *after* the tool returns and *before* the value is serialized, so the
        value that becomes a `tool` message is the value that was checked. A
        violation becomes a `policy` failure carrying the shape and **no values**;
        a redaction drops the named columns and records them in the result and the
        audit entry's `result_shape`.
        """
        if not result.ok:
            # A refused/errored tool still executed under an allow decision; the
            # *permission* fact is recorded, the failure is Day 13's.
            result.permission = (result.permission
                                 or (decision.meta() if hasattr(decision, "meta")
                                     else None))
            result.decision = result.decision or decision
            self._record_audit(name, decision, args, result)
            return result
        verdict = perms_mod.check_result(name, result, perms=self._permissions,
                                         decision=decision if hasattr(decision, "meta")
                                         else None)
        if not verdict.allow:
            shape = _shape_only(result)
            fail = ToolResult.failure(name, verdict.reason, args=result.args,
                                      error_kind=ERROR_PERMISSION,
                                      rejection_class="policy")
            if shape is not None:
                fail.data = shape
            fail.permission = verdict.meta()
            fail.decision = decision
            self._record_audit(name, verdict, args, fail)
            return fail
        if verdict.redacted:
            _apply_redaction(result, verdict.redacted)
            result.redacted = list(verdict.redacted)
        result.permission = (result.permission
                             or (decision.meta() if hasattr(decision, "meta") else None))
        result.decision = result.decision or decision
        self._record_audit(name, decision if hasattr(decision, "meta") else verdict, args, result)
        return result

    # ------------------------------------------------------------------- audit
    def _record_audit(self, name: str, decision: Any, args: Any,
               result: ToolResult | None) -> None:
        if self._audit is None:
            return
        decision_meta = decision if hasattr(decision, "meta") else None
        self._audit.append({
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "principal": self._principal,
            "tool": name,
            "effect": getattr(decision, "effect", None),
            "data": getattr(decision, "data", None),
            "decision": ("allow" if getattr(decision, "allow", False) else "deny"),
            "code": getattr(decision, "code", None),
            "args_hash": audit_mod.args_hash(args),
            "args_shape": audit_mod.args_shape(args),
            "result_shape": _result_shape(result),
            "latency_ms": 0,
            "redacted": list(getattr(result, "redacted", []) or []),
        })

    def run(self, name: str, args_raw: Any) -> ToolResult:
        """Validate then execute — the only path the answer loop uses."""
        vr = self.validate_args(name, args_raw)
        if not vr.ok:
            return vr
        return self.execute(name, vr.args or {}, decision=vr.decision)


# ------------------------------------------------------------ Day 26 result shapes

def _shape_only(result: ToolResult) -> dict[str, Any] | None:
    """Keep the shape, drop every value — the no-partial-rows contract.

    `columns` (which columns *arrived*), `query_kind`, `executed`, and `redacted`
    survive so the trace can answer the forensic question the model may not be
    told; `rows` is empty and `row_count` is 0, because a partial answer is how a
    leak becomes untraceable and the two missing columns would be nobody's
    decision.
    """
    data = getattr(result, "data", None)
    if not isinstance(data, dict) or "columns" not in data:
        return None
    return {"columns": perms_mod._columns_of(data),
            "query_kind": data.get("query_kind"),
            "executed": bool(data.get("executed")),
            "redacted": list(getattr(result, "redacted", []) or []),
            "rows": [], "row_count": 0}


def _apply_redaction(result: ToolResult, columns: "Iterable[str]") -> None:
    """Drop the named columns from a data record — values and header together."""
    data = getattr(result, "data", None)
    if not isinstance(data, dict):
        return
    drop = set(columns)
    cols = data.get("columns")
    if isinstance(cols, list):
        keep_idx = [i for i, c in enumerate(cols) if c not in drop]
        data["columns"] = [cols[i] for i in keep_idx]
        if isinstance(data.get("rows"), list):
            data["rows"] = [[row[i] for i in keep_idx if i < len(row)]
                            for row in data["rows"]]
    elif isinstance(cols, dict):
        for c in list(cols):
            if c in drop:
                cols.pop(c)
    data["redacted"] = sorted(drop)


def _result_shape(result: ToolResult | None) -> dict[str, Any] | None:
    """The result *shape* an audit entry may carry — never a value."""
    if result is None:
        return None
    data = getattr(result, "data", None)
    if isinstance(data, dict) and "columns" in data:
        return {"row_count": int(data.get("row_count") or 0),
                "columns": perms_mod._columns_of(data),
                "truncated": bool(data.get("truncated")),
                "redacted": list(data.get("redacted") or [])}
    evidence = list(getattr(result, "evidence", None) or [])
    if evidence:
        return {"evidence": len(evidence), "truncated": bool(result.truncated)}
    if not result.ok:
        return {"error_kind": result.error_kind}
    return None


# ------------------------------------------------------------------ evidence

def _evidence_rows(results: list[tuple[float, Chunk]], *,
                   max_chars: int | None) -> tuple[list[dict[str, Any]], bool]:
    """Top-K → serializable evidence records, clamped to a char budget.

    Rank priority wins (Day 6's budget floor, re-used): a later record that
    would overflow is dropped, never reordered past an earlier one. The top
    record is never dropped — a retrieval that found something must hand the
    model *something* (Day 9's "top chunk never dropped", on the tool shelf).
    """
    rows: list[dict[str, Any]] = []
    used = 0
    truncated = False
    for i, (score, chunk) in enumerate(results):
        row: dict[str, Any] = {
            "rank": i + 1,
            "score": round(float(score), 4),
            "doc_id": chunk.doc_id,
            "chunk_id": chunk.chunk_id,
            "heading": chunk.heading,
            "chars": len(chunk.text),
            "text": chunk.text,
        }
        if chunk.symbol:
            row.update({"symbol": chunk.symbol, "kind": chunk.kind,
                        "line": chunk.position})
        if max_chars is not None and rows and used + len(chunk.text) > max_chars:
            truncated = True
            continue
        used += len(chunk.text)
        rows.append(row)
    return rows, truncated


def _retrieval_result(name: str, retriever: Retriever | None, args: dict[str, Any], *,
                      k: int, tau: float, max_chars: int | None,
                      filter: dict[str, str] | None = None) -> ToolResult:
    if retriever is None:
        return ToolResult.failure(name, "no store configured for this tool",
                                  args=args, error_kind=ERROR_INTERNAL)
    results = retriever.retrieve(args["query"], k=k, filter=filter)
    if should_refuse(results, tau):
        return ToolResult.refusal(name, args=args)
    rows, truncated = _evidence_rows(results, max_chars=max_chars)
    return ToolResult.success(name, args=args, evidence=rows, truncated=truncated)


# -------------------------------------------------------------------- schemas

# Day 25 §6 — the caps the validator enforces. Free-text gets an explicit
# `maxLength`; identifier-shaped fields get a `pattern`. They are wide enough that
# every legitimate demo question and query passes (measured on the pinned
# legitimate set), and narrow enough that an injected 4 KB payload is a rejection.
DEFAULT_MAX_QUERY = 512
DEFAULT_MAX_SQL = 4096
DEFAULT_MAX_ID = 64
DEFAULT_MAX_MODULE = 200
_ID_PATTERN = r"[A-Za-z0-9_.:-]+"
_MODULE_PATTERN = r"[A-Za-z0-9_./-]+"
_TABLE_PATTERN = r"[A-Za-z_][A-Za-z0-9_]*"


def search_docs_schema() -> dict[str, Any]:
    return {
        "name": "search_saleor_docs",
        "description": (
            "Search Saleor's *documentation* (prose guides) and return top-K "
            "documentation chunks with doc_id, heading, score and text. Use this "
            "for how-to and conceptual questions ('how do I create a checkout', "
            "'what does the app store do'). It does NOT search source code — for "
            "'where is X implemented' use search_saleor_code."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1,
                          "maxLength": DEFAULT_MAX_QUERY},
                "k": {"type": "integer", "minimum": 1, "maximum": 10, "default": 4},
                "max_chars": {"type": "integer", "minimum": 1},
            },
            "required": ["query"],
        },
    }


def search_code_schema() -> dict[str, Any]:
    return {
        "name": "search_saleor_code",
        "description": (
            "Search Saleor's *source code* and return top-K module#Symbol records "
            "(function/class/mutation/test) with scores, line numbers and the "
            "symbol body. Use this for 'where is X implemented/handled'. A query "
            "about checkout legitimately hits BOTH the GraphQL mutation "
            "(saleor/graphql/checkout/...) and the domain service "
            "(saleor/checkout/...) — a cross-layer double-hit is correct."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1,
                          "maxLength": DEFAULT_MAX_QUERY},
                "kind": {"type": "string",
                         "enum": ["function", "class", "mutation", "test", "module"]},
                "module": {"type": "string", "maxLength": DEFAULT_MAX_MODULE,
                           "pattern": _MODULE_PATTERN},
                "k": {"type": "integer", "minimum": 1, "maximum": 10, "default": 4},
            },
            "required": ["query"],
        },
    }


def _record_schema(name: str, description: str) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": {"id": {"type": "string", "minLength": 1,
                                  "maxLength": DEFAULT_MAX_ID,
                                  "pattern": _ID_PATTERN}},
            "required": ["id"],
        },
    }


# --------------------------------------------------------- Day 14 data tools

# Two shapes of data tool, and the difference is the day's second design
# decision. `get_product/order/customer(id)` are **safe by construction** — a
# fixed query with one validated `id`, zero generation — and they finally *fill*
# the interface Day 11 shipped (the `pending_resolver` stub is gone because the
# resolver is real). `run_saleor_sql(sql)` is the **open, generated-query** tool:
# the agent's power (aggregation, joins, arbitrary set logic) and Day 15's
# target. Both return the §5 evidence record `{ok, sql, columns, rows, row_count,
# truncated, elapsed_ms, query_kind}` so the harness grades all four corpora
# with **one** shape — and the trace can tell a parameterized answer (`fixed`)
# from a generated one (`generated`).


def _data_result(name: str, args: dict[str, Any], qr, *,
                 query_kind: str) -> ToolResult:
    """Wrap a `QueryResult` as a `ToolResult` — success or a typed observation.

    A validator rejection is `{validation: "rejected", executed: false}`; a DB
    refusal or bad query is `executed: true` with the engine's `error_kind`. Both
    are *observations* the loop can repair against, never exceptions.
    """
    record = {**qr.as_dict(), "query_kind": query_kind}
    if qr.ok:
        return ToolResult.success(name, args=args, data=record, truncated=qr.truncated)
    result = ToolResult.tool_error(
        name, error_kind=qr.error_kind or ERROR_TOOL,
        message=qr.error or "query failed", args=args, retryable=qr.retryable,
        rejection_class=qr.rejection_class)
    # The record survives the failure on purpose: a *rejected* query must still
    # show `gen_sql` + `validation: rejected` + `executed: false` in the trace,
    # or the §8 table cannot distinguish "the validator refused it" from "the
    # database refused it".
    result.data = record
    return result


def run_sql_schema(*, card: str | None = None) -> dict[str, Any]:
    """The *generator's* description: the schema card is the tool's context.

    The card rides in the description — a long, stable prefix — because a model
    cannot query a column it was never told exists. Day 8 wrote stopword-lean
    queries for a *scorer*, Day 11 wrote descriptions for a *chooser*; this is
    the same discipline for a *generator*.
    """
    return {
        "name": "run_saleor_sql",
        "description": (
            "Run ONE read-only SQL query against Saleor's Postgres database and "
            "return columns + rows. Use it for 'how much / how many / which "
            "rows' questions (aggregation, joins) that no text search can "
            "answer. Ground every table and column in the schema card below, and "
            "resolve business terms with the definitions as written. Only a "
            "single SELECT/WITH statement is allowed.\n\n"
            + (schema_card_text() if card is None else card)
        ),
        "parameters": {
            "type": "object",
            "properties": {"sql": {"type": "string", "minLength": 1,
                                   "maxLength": DEFAULT_MAX_SQL}},
            "required": ["sql"],
        },
    }


def describe_schema_schema() -> dict[str, Any]:
    return {
        "name": "describe_saleor_schema",
        "description": (
            "Return one database table's purpose and columns (on-demand schema "
            "expansion). Use it when the schema card's curated core subset is "
            "not enough; it adds nothing outside the core tables."
        ),
        "parameters": {
            "type": "object",
            "properties": {"table": {"type": "string", "minLength": 1,
                                     "maxLength": DEFAULT_MAX_ID,
                                     "pattern": _TABLE_PATTERN}},
            "required": ["table"],
        },
    }


def _sql_tool(name: str, agent: DataAgent) -> Callable[[dict], ToolResult]:
    def fn(args: dict[str, Any]) -> ToolResult:
        return _data_result(name, args, agent.run(args["sql"]), query_kind="generated")
    return fn


def _schema_tool(name: str) -> Callable[[dict], ToolResult]:
    def fn(args: dict[str, Any]) -> ToolResult:
        # Day 24 — the schema card is in-process, so this is an *internal* span,
        # not a client one. The day names the chain's Saleor/Postgres hops; it
        # does not license marking a dict lookup as a boundary we crossed.
        tr = obs.current_trace()
        span = None
        if tr is not None and getattr(tr, "recording", False):
            span = tr.start_span(obs.KIND_TOOL, "schema.describe",
                                 span_kind=obs.SPAN_INTERNAL, component="data",
                                 attributes={"operation": "describe_saleor_schema",
                                             "table": args.get("table")})
        try:
            entry = describe_table(args["table"])
            if entry is None:
                if span is not None:
                    span.update(found=False)
                    obs.end_span(span, status=obs.STATUS_ERROR,
                                 error={"kind": "no_data",
                                        "message": f"unknown table {args['table']!r}"})
                return ToolResult.failure(
                    name, f"unknown table {args['table']!r} (known: {table_names()})",
                    args=args, error_kind=ERROR_NO_DATA)
            if span is not None:
                span.update(found=True, columns=len(entry.get("columns") or {}))
            return ToolResult.success(name, args=args, data={
                **entry, "dialect": DIALECT_NOTE, "definitions": GLOSSARY})
        finally:
            if span is not None:
                if not span.closed:
                    obs.end_span(span)
                tr.leave(span)
    return fn


def _resolver_tool(name: str, agent: DataAgent,
                   method: str) -> Callable[[dict], ToolResult]:
    def fn(args: dict[str, Any]) -> ToolResult:
        return _data_result(name, args, getattr(agent, method)(args["id"]),
                            query_kind="fixed")
    return fn


# ------------------------------------------------------------------ factories

def doc_retriever(gateway, store, *, alpha: float = 0.5) -> Retriever:
    """The Day-6/8 prose retriever behind `search_saleor_docs`."""
    return Retriever(gateway, store, alpha=alpha)


def code_retriever(gateway, store, *, alpha: float = 0.5) -> Retriever:
    """The Day-10 identifier retriever behind `search_saleor_code`."""
    return Retriever(gateway, store, alpha=alpha, tokenizer=code_tokens,
                     rewrites=CODE_REWRITES, sections={}, section_prefixes={})


def build_default_registry(*, doc: Retriever | None = None,
                           code: Retriever | None = None,
                           data: DataAgent | None = None,
                           tau: float | None = None,
                           doc_tau: float | None = None,
                           code_tau: float | None = None,
                           k: int | None = None,
                           doc_budget: int = DEFAULT_DOC_BUDGET,
                           code_budget: int = DEFAULT_CODE_BUDGET,
                           permissions: "perms_mod.PermissionSet | None" = None,
                           principal: str | None = None,
                           audit: "audit_mod.AuditLog | audit_mod.NullAuditLog | None"
                           = None,
                           ) -> ToolRegistry:
    """The seven tools, registered with their schemas **and grants** — Day 30's set.

    τ is a property of the *corpus*, not of the registry: `doc_tau`/`code_tau`
    (both falling back to `tau`) let each gate keep the calibration its own
    store earned — a single shared τ would silently refuse one corpus's
    answerable questions the moment the other corpus's τ moved.

    `data` is the §3/§5 seam: the offline seeded stub by default, the real
    read-only Postgres store when `SALEOR_READONLY_DB_URL` is set. Passing it in
    is how a suite pins a small row limit for the truncation fixture.

    Day 26 §7 — every `register` call now carries a `grant` and therefore can no
    longer be called without one: a tool arrives with a **declared** effect and
    data class or it does not arrive. `permissions` is the *governance* switch:
    when it is `None` the registry is ungoverned (Days 11–25 exactly — the C9
    off-switch and how the pre-Day-26 fixtures stay green); when a
    `PermissionSet` is passed, `schemas()` returns only the principal's granted
    slice and `execute()` denies without a decision. The grant *declarations* are
    always read (from `permissions` or the shipped `permissions.yaml`), so the
    registration-time requirement holds either way.
    """
    base_tau = tool_tau() if tau is None else tau
    doc_gate = base_tau if doc_tau is None else doc_tau
    code_gate = base_tau if code_tau is None else code_tau
    k_default = tool_top_k() if k is None else k
    agent = data if data is not None else DataAgent(make_data_store())
    decls = permissions if permissions is not None else perms_mod.default_permission_set()
    reg = ToolRegistry(permissions=permissions, principal=principal, audit=audit)

    def _grant(name: str) -> Any:
        g = decls.grant(name)
        if g is None:
            raise ValueError(f"permissions file declares no grant for {name!r} — a "
                             "tool cannot be registered without a declaration")
        return g

    def search_docs(args: dict[str, Any]) -> ToolResult:
        budget = args.get("max_chars", doc_budget)
        return _retrieval_result("search_saleor_docs", doc, args,
                                 k=int(args.get("k", k_default)), tau=doc_gate,
                                 max_chars=budget)

    def search_code(args: dict[str, Any]) -> ToolResult:
        filter_spec: dict[str, str] = {}
        if args.get("kind"):
            filter_spec["kind"] = args["kind"]
        if args.get("module"):
            filter_spec["module_prefix"] = args["module"]
        return _retrieval_result("search_saleor_code", code, args,
                                 k=int(args.get("k", k_default)), tau=code_gate,
                                 max_chars=code_budget,
                                 filter=filter_spec or None)

    reg.register("search_saleor_docs", search_docs_schema(), search_docs,
                 grant=_grant("search_saleor_docs"))
    reg.register("search_saleor_code", search_code_schema(), search_code,
                 grant=_grant("search_saleor_code"))
    reg.register("get_product",
                 _record_schema("get_product",
                                "Look up a product by its id and return the "
                                "product record (name, type, price, variants). "
                                "A fixed, parameterized query — no SQL generated."),
                 _resolver_tool("get_product", agent, "get_product"),
                 grant=_grant("get_product"))
    reg.register("get_order",
                 _record_schema("get_order",
                                "Look up an order by its id and return the order "
                                "record (number, status, total, lines). A fixed, "
                                "parameterized query — no SQL generated."),
                 _resolver_tool("get_order", agent, "get_order"),
                 grant=_grant("get_order"))
    reg.register("get_customer",
                 _record_schema("get_customer",
                                "Look up a customer by their id and return the "
                                "customer record (email, order count). A fixed, "
                                "parameterized query — no SQL generated."),
                 _resolver_tool("get_customer", agent, "get_customer"),
                 grant=_grant("get_customer"))
    reg.register("run_saleor_sql", run_sql_schema(), _sql_tool("run_saleor_sql", agent),
                 grant=_grant("run_saleor_sql"))
    reg.register("describe_saleor_schema", describe_schema_schema(),
                 _schema_tool("describe_saleor_schema"),
                 grant=_grant("describe_saleor_schema"))
    return reg


def build_governed_registry(*, permissions: "perms_mod.PermissionSet | None" = None,
                            principal: str | None = None,
                            audit: "audit_mod.AuditLog | audit_mod.NullAuditLog | None"
                            = None,
                            **kw: Any) -> ToolRegistry:
    """The governed entry point: the same seven tools behind the permission layer.

    With `PERMISSIONS=none` (or a permissions file that will not load) it falls
    back to the **ungoverned** registry, which is exactly the C9 off-switch: the
    layer is passive, so turning it off restores Days 11–25 byte-for-byte. When
    governed, `permissions.yaml` supplies both the grant declarations and the
    `default` set (today's seven tools), so a legitimate request is unchanged.
    """
    if permissions is None:
        if not perms_mod.enabled():
            return build_default_registry(**kw)
        permissions = perms_mod.default_permission_set()
    return build_default_registry(permissions=permissions,
                                 principal=(principal
                                            or perms_mod.principal_from_env()),
                                 audit=audit, **kw)
