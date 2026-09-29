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
from dataclasses import dataclass, field
from typing import Any, Callable

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
    """name → `{schema, fn}`; `schemas()` feeds the gateway, `execute()` dispatches.

    The registry is the model's contract surface: an unknown tool name is a
    **rejection result**, never an exception and never a forged run.

    Day 19 adds one more surface: a registry may be **scoped** to a role, in which
    case a tool that exists elsewhere in the system is *denied* here with a typed
    `role_violation` (a `policy` rejection). `scope()` is how a specialist is "the
    existing loop with a tool-list slice" — not a new program, just a narrower
    contract.
    """

    def __init__(self, *, denied: dict[str, str] | None = None) -> None:
        self._tools: dict[str, dict[str, Any]] = {}
        self._denied: dict[str, str] = dict(denied or {})

    def register(self, name: str, schema: dict[str, Any],
                 fn: Callable[[dict[str, Any]], ToolResult]) -> None:
        if schema.get("name") != name:
            raise ValueError(f"schema name {schema.get('name')!r} != {name!r} "
                             "— a renamed tool is a broken tool_calls")
        self._tools[name] = {"schema": schema, "fn": fn}

    def names(self) -> list[str]:
        return list(self._tools)

    def denied(self) -> dict[str, str]:
        """The tools this registry holds but refuses (an out-of-role gate)."""
        return dict(self._denied)

    def scope(self, names: list[str] | tuple[str, ...], *,
              reason: str = "out of role") -> "ToolRegistry":
        """A role's view: keep `names`, deny every other registered tool.

        The denied tools are still *known* — the denial names them — so an
        out-of-role call is a **policy** rejection (`role_violation`), not an
        `unknown_tool`: the difference between "you may not" and "there is no such
        thing" is exactly the privilege boundary Day 15 drew.
        """
        keep = set(names)
        scoped = ToolRegistry(denied={
            n: f"{reason}: {n} is not in this role's tool group"
            for n in self._tools if n not in keep})
        for n, tool in self._tools.items():
            if n in keep:
                scoped._tools[n] = tool
        return scoped

    def schemas(self) -> list[dict[str, Any]]:
        """What the gateway sends the model — names stable, descriptions loud."""
        return [{"type": "function", "function": t["schema"]}
                for t in self._tools.values()]

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
        ok, result = validate_args(self._tools[name]["schema"], args_raw)
        if not ok:
            return ToolResult.failure(name, result, error_kind=ERROR_INVALID_ARGS)
        return ToolResult.success(name, args=result)

    def properties(self, name: str) -> dict[str, Any]:
        """The tool's declared argument properties (Day 13's sibling-arg filter)."""
        tool = self._tools.get(name)
        if tool is None:
            return {}
        return (tool["schema"].get("parameters") or {}).get("properties") or {}

    def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        if name in self._denied:
            return ToolResult.failure(
                name, self._denied[name], error_kind=ERROR_ROLE,
                rejection_class="policy")
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult.failure(
                name, f"unknown tool {name!r} (known: {self.names()})",
                error_kind=ERROR_UNKNOWN_TOOL)
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
        return result

    def run(self, name: str, args_raw: Any) -> ToolResult:
        """Validate then execute — the only path the answer loop uses."""
        vr = self.validate_args(name, args_raw)
        if not vr.ok:
            return vr
        return self.execute(name, vr.args or {})


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
                "query": {"type": "string"},
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
                "query": {"type": "string"},
                "kind": {"type": "string",
                         "enum": ["function", "class", "mutation", "test", "module"]},
                "module": {"type": "string"},
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
            "properties": {"id": {"type": "string"}},
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
            "properties": {"sql": {"type": "string"}},
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
            "properties": {"table": {"type": "string"}},
            "required": ["table"],
        },
    }


def _sql_tool(name: str, agent: DataAgent) -> Callable[[dict], ToolResult]:
    def fn(args: dict[str, Any]) -> ToolResult:
        return _data_result(name, args, agent.run(args["sql"]), query_kind="generated")
    return fn


def _schema_tool(name: str) -> Callable[[dict], ToolResult]:
    def fn(args: dict[str, Any]) -> ToolResult:
        entry = describe_table(args["table"])
        if entry is None:
            return ToolResult.failure(
                name, f"unknown table {args['table']!r} (known: {table_names()})",
                args=args, error_kind=ERROR_NO_DATA)
        return ToolResult.success(name, args=args, data={
            **entry, "dialect": DIALECT_NOTE, "definitions": GLOSSARY})
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
                           ) -> ToolRegistry:
    """The seven tools, registered with their schemas — Day 30's scenario set.

    τ is a property of the *corpus*, not of the registry: `doc_tau`/`code_tau`
    (both falling back to `tau`) let each gate keep the calibration its own
    store earned — a single shared τ would silently refuse one corpus's
    answerable questions the moment the other corpus's τ moved.

    `data` is the §3/§5 seam: the offline seeded stub by default, the real
    read-only Postgres store when `SALEOR_READONLY_DB_URL` is set. Passing it in
    is how a suite pins a small row limit for the truncation fixture.
    """
    base_tau = tool_tau() if tau is None else tau
    doc_gate = base_tau if doc_tau is None else doc_tau
    code_gate = base_tau if code_tau is None else code_tau
    k_default = tool_top_k() if k is None else k
    agent = data if data is not None else DataAgent(make_data_store())
    reg = ToolRegistry()

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

    reg.register("search_saleor_docs", search_docs_schema(), search_docs)
    reg.register("search_saleor_code", search_code_schema(), search_code)
    reg.register("get_product",
                 _record_schema("get_product",
                                "Look up a product by its id and return the "
                                "product record (name, type, price, variants). "
                                "A fixed, parameterized query — no SQL generated."),
                 _resolver_tool("get_product", agent, "get_product"))
    reg.register("get_order",
                 _record_schema("get_order",
                                "Look up an order by its id and return the order "
                                "record (number, status, total, lines). A fixed, "
                                "parameterized query — no SQL generated."),
                 _resolver_tool("get_order", agent, "get_order"))
    reg.register("get_customer",
                 _record_schema("get_customer",
                                "Look up a customer by their id and return the "
                                "customer record (email, order count). A fixed, "
                                "parameterized query — no SQL generated."),
                 _resolver_tool("get_customer", agent, "get_customer"))
    reg.register("run_saleor_sql", run_sql_schema(), _sql_tool("run_saleor_sql", agent))
    reg.register("describe_saleor_schema", describe_schema_schema(),
                 _schema_tool("describe_saleor_schema"))
    return reg
