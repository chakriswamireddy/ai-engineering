"""Day 11 §3/§5 — the tool layer: the old seams made declarative.

Days 8–10 built a retriever that answers questions; Day 11 lets the *model*
ask it. The five tools are not a new subsystem — they are exactly the seams
that already exist, exposed as function schemas:

    search_saleor_docs()  → Day 6/8 retrieve() over data/store.json
    search_saleor_code()  → Day 10 retrieve() over data/code_store.json
    get_product/order/customer() → seeded fixtures (the real resolver is Day 14)

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
    pending_resolver: str | None = None

    @classmethod
    def success(cls, name: str, *, args: dict | None = None,
                evidence: list[dict] | None = None, data: Any = None,
                truncated: bool = False, pending_resolver: str | None = None,
                ) -> "ToolResult":
        return cls(name=name, ok=True, args=args, evidence=list(evidence or []),
                   data=data, truncated=truncated,
                   pending_resolver=pending_resolver)

    @classmethod
    def failure(cls, name: str, error: str, *,
                args: dict | None = None) -> "ToolResult":
        return cls(name=name, ok=False, args=args, error=error)

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
        elif self.evidence:
            out["evidence"] = self.evidence
        elif self.data is not None:
            out["data"] = self.data
        if self.truncated:
            out["truncated"] = True
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
    """

    def __init__(self) -> None:
        self._tools: dict[str, dict[str, Any]] = {}

    def register(self, name: str, schema: dict[str, Any],
                 fn: Callable[[dict[str, Any]], ToolResult]) -> None:
        if schema.get("name") != name:
            raise ValueError(f"schema name {schema.get('name')!r} != {name!r} "
                             "— a renamed tool is a broken tool_calls")
        self._tools[name] = {"schema": schema, "fn": fn}

    def names(self) -> list[str]:
        return list(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        """What the gateway sends the model — names stable, descriptions loud."""
        return [{"type": "function", "function": t["schema"]}
                for t in self._tools.values()]

    def validate_args(self, name: str, args_raw: Any) -> ToolResult:
        if name not in self._tools:
            return ToolResult.failure(
                name, f"unknown tool {name!r} (known: {self.names()})")
        ok, result = validate_args(self._tools[name]["schema"], args_raw)
        if not ok:
            return ToolResult.failure(name, result)
        return ToolResult.success(name, args=result)

    def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult.failure(
                name, f"unknown tool {name!r} (known: {self.names()})")
        try:
            result = tool["fn"](args or {})
        except Exception as exc:                     # never crash the turn
            return ToolResult.failure(name, f"tool error: {exc.__class__.__name__}: {exc}",
                                      args=args)
        if not isinstance(result, ToolResult):
            return ToolResult.failure(name, "tool returned a malformed result",
                                      args=args)
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
        return ToolResult.failure(name, "no store configured for this tool", args=args)
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


# ---------------------------------------------------------------- data stubs

# Day 11 ships the *interface* the real resolvers will fill: schema, `id`
# validation, result shape. The records are fixtures and say so — every one
# carries `pending_resolver: "day14"` so no one mistakes a fixture lookup for
# live Saleor data.
PENDING_RESOLVER = "day14"

SEEDED_RECORDS: dict[str, dict[str, dict[str, Any]]] = {
    "product": {
        "1": {"id": "1", "name": "Blue T-Shirt", "slug": "blue-t-shirt",
              "product_type": "Apparel", "currency": "USD", "price": "19.99",
              "variants": 3},
        "2": {"id": "2", "name": "Coffee Mug", "slug": "coffee-mug",
              "product_type": "Homeware", "currency": "USD", "price": "9.50",
              "variants": 2},
    },
    "order": {
        "1": {"id": "1", "number": "1001", "status": "UNFULFILLED",
              "currency": "USD", "total": "19.99", "lines": 1,
              "checkout_token": "chk_stub_1"},
        "2": {"id": "2", "number": "1002", "status": "FULFILLED",
              "currency": "USD", "total": "29.49", "lines": 2,
              "checkout_token": "chk_stub_2"},
    },
    "customer": {
        "1": {"id": "1", "email": "demo@example.com", "orders": 2,
              "currency": "USD"},
    },
}


def _record_tool(name: str, kind: str,
                 records: dict[str, dict[str, Any]]) -> Callable[[dict], ToolResult]:
    def fn(args: dict[str, Any]) -> ToolResult:
        record = records.get(str(args["id"]))
        if record is None:
            return ToolResult.failure(name, f"no such {kind} id: {args['id']!r}")
        return ToolResult.success(name, data={**record, "pending_resolver": PENDING_RESOLVER},
                                  pending_resolver=PENDING_RESOLVER)
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
                           tau: float | None = None,
                           doc_tau: float | None = None,
                           code_tau: float | None = None,
                           k: int | None = None,
                           doc_budget: int = DEFAULT_DOC_BUDGET,
                           code_budget: int = DEFAULT_CODE_BUDGET,
                           records: dict[str, dict[str, dict[str, Any]]] | None = None,
                           ) -> ToolRegistry:
    """The five tools, registered with their schemas — Day 30's scenario set.

    τ is a property of the *corpus*, not of the registry: `doc_tau`/`code_tau`
    (both falling back to `tau`) let each gate keep the calibration its own
    store earned — a single shared τ would silently refuse one corpus's
    answerable questions the moment the other corpus's τ moved.
    """
    base_tau = tool_tau() if tau is None else tau
    doc_gate = base_tau if doc_tau is None else doc_tau
    code_gate = base_tau if code_tau is None else code_tau
    k_default = tool_top_k() if k is None else k
    table = records or SEEDED_RECORDS
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
                                "product record (name, type, price, variants)."),
                 _record_tool("get_product", "product", table["product"]))
    reg.register("get_order",
                 _record_schema("get_order",
                                "Look up an order by its id and return the order "
                                "record (number, status, total, lines)."),
                 _record_tool("get_order", "order", table["order"]))
    reg.register("get_customer",
                 _record_schema("get_customer",
                                "Look up a customer by their id and return the "
                                "customer record (email, order count)."),
                 _record_tool("get_customer", "customer", table["customer"]))
    return reg
