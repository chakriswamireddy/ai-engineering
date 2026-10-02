"""Day 23 §6 — the trace core: one request, one root, nested spans, safe events.

The repository owns evidence everywhere and causality nowhere: `Ledger` knows a
model attempt happened, `trace.py` knows evidence was retrieved, the endpoint
knows a response left — but nothing proves those records belong to the *same*
request. This module mints the missing identity.

```text
request (trace_id · operation)
├── model.embed attempt
├── retrieval ── document events
└── generation / agent step
    ├── model.chat attempt
    ├── tool-call event
    └── tool-result event
final-answer event

TOTALS: latency from the root span · tokens/cost from linked ledger records
```

Five decisions, pre-committed (day-23.md §5):

1. **One request, one root.** A root is created at the process boundary; every
   internal span descends from it. A nested helper attaches to the active
   context — it never mints a disconnected identity.
2. **Wrap boundaries, never re-run them.** The tracer measures operations where
   they happen; it never calls chat/retrieval/tool again to "observe" them.
3. **The ledger owns money.** A span links the exact `CallRecord`; totals
   aggregate the linked rows. There is no second pricing function here.
4. **Metadata before content.** The default record carries ids, names, counts,
   sizes, hashes, ranks, scores, model names, timings, costs, and typed errors —
   never raw prompts, documents, tool payloads, headers, or answers.
5. **Local before distributed.** A scoped context variable and a bounded
   thread-safe in-memory store are enough to make one in-process request
   inspectable. Cross-process propagation is Day 24's.

Mechanics (`MECH`): identity, parent paths, monotonic duration, status
vocabulary, ledger arithmetic, prompt hashes, JSON serialization, lookup,
retention, and redaction are deterministic. Setting tracing off makes every
entry point a no-op, so **O9** (observation changes nothing) is structural, not
promised.
"""
from __future__ import annotations

import hashlib
import os
import re
import threading
import time
import uuid
from collections import Counter, OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Mapping

SCHEMA_VERSION = 2

# --------------------------------------------------------------- vocabulary
# The status vocabulary stays small and structural: a domain outcome (refused,
# rejected) is an attribute, never a status. `ok`/`error`/`timeout`/`cancelled`
# describe *how the operation ended*, not whether it was correct.

STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_TIMEOUT = "timeout"
STATUS_CANCELLED = "cancelled"
STATUSES = frozenset({STATUS_OK, STATUS_ERROR, STATUS_TIMEOUT, STATUS_CANCELLED})

# Span kinds — the tree's edges are (parent, child), the kind names the operation.
# Day 23's `kind` names the operation family (model / retrieval / agent_step …).
KIND_REQUEST = "request"
KIND_MODEL = "model"
KIND_RETRIEVAL = "retrieval"
KIND_STEP = "agent_step"
KIND_TOOL = "tool"
KIND_ANSWER = "final_answer"

# Day 24 — `span_kind` is a *second*, independent axis: the honest statement of
# who did the work at a control edge. `server` = we handled an inbound request;
# `client` = we called something else; `internal` = local computation with no
# boundary crossed; `producer`/`consumer` = a queue/message boundary. Keeping it
# distinct from Day 23's `kind` is deliberate: reusing one field for both the
# operation family and the edge classification would fork the grammar, and the
# day's rule is "one grammar, two scopes" (add `span_kind`/`remote`/endpoint, not
# a second trace type). A chain whose spans are all `server` never left the
# building; marking a local JSON read `client` is inflation, not observation.
SPAN_SERVER = "server"
SPAN_CLIENT = "client"
SPAN_INTERNAL = "internal"
SPAN_PRODUCER = "producer"
SPAN_CONSUMER = "consumer"
SPAN_KINDS = frozenset({SPAN_SERVER, SPAN_CLIENT, SPAN_INTERNAL,
                        SPAN_PRODUCER, SPAN_CONSUMER})

# Day 24 — why a request was continued or started fresh. Every non-continuation
# is *explained*, never silently minted (`no_header` is the ordinary case).
REASON_CONTINUED = "continued"
REASON_NO_HEADER = "no_header"
REASON_MALFORMED = "malformed"
REASON_ALL_ZERO = "all_zero"
REASON_VERSION_UNSUPPORTED = "version_unsupported"
REASON_REJECTED = "rejected_untrusted"
CONTINUATION_REASONS = frozenset({REASON_CONTINUED, REASON_NO_HEADER,
                                  REASON_MALFORMED, REASON_ALL_ZERO,
                                  REASON_VERSION_UNSUPPORTED, REASON_REJECTED})

# Error taxonomy (Day 13's families, promoted to the trace).
ERR_PROVIDER = "provider_error"
ERR_RATE_LIMIT = "rate_limit"
ERR_INVALID_KEY = "invalid_key"
ERR_BAD_REQUEST = "bad_request"
ERR_NETWORK = "network"
ERR_TIMEOUT = "timeout"
ERR_DIMENSION = "dimension_mismatch"
ERR_TOOL = "tool_error"
ERR_TOOL_REJECTION = "tool_rejection"
ERR_BUDGET = "budget_exceeded"
ERR_OUT_OF_ROLE = "role_violation"
ERR_INTERNAL = "internal"

# Keys whose *values* are secrets and must never enter a trace, even in raw mode.
_SECRET_EXACT = frozenset({
    "api_key", "apikey", "authorization", "auth", "secret", "password",
    "token", "access_token", "refresh_token", "bearer", "session_key",
})
_SECRET_SUBSTR = ("authorization", "api_key", "apikey", "secret", "password",
                  "bearer")

# ------------------------------------------------------------------ switches

_TRACING = ContextVar("cc_ai_tracing", default=None)   # None = follow module flag
_MODULE_ENABLED = os.getenv("AI_TRACING", "1").strip().lower() not in {
    "0", "off", "false", "no"}

_raw_debug = False
_raw_cap = 512
RAW_ATTR_CAP = 200          # default: any string longer than this is hashed


def tracing_enabled() -> bool:
    override = _TRACING.get()
    return _MODULE_ENABLED if override is None else bool(override)


def set_tracing(on: bool) -> None:
    """Global on/off. The O9 control flips this; `off` must be byte-equivalent
    to a pre-Day-23 process on every observable output except the absent id."""
    global _MODULE_ENABLED
    _MODULE_ENABLED = bool(on)


@contextmanager
def tracing(on: bool) -> Iterator[None]:
    """Scoped switch (thread/task-local) — used by the suite to run the same
    fixture with collection off, then on, inside one process."""
    token = _TRACING.set(bool(on))
    try:
        yield
    finally:
        _TRACING.reset(token)


def set_raw_debug(on: bool, *, cap: int = 512) -> None:
    """Explicit, bounded diagnostic mode. Still redacts secrets; still off by
    default; never required by O1–O9."""
    global _raw_debug, _raw_cap
    _raw_debug = bool(on)
    _raw_cap = max(1, int(cap))


def raw_debug_enabled() -> bool:
    return _raw_debug


# ------------------------------------------------------------------ redaction

def _is_secret_key(key: Any) -> bool:
    k = str(key).lower()
    if k in _SECRET_EXACT:
        return True
    return any(s in k for s in _SECRET_SUBSTR)


def sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", "replace")).hexdigest()


def _safe_value(value: Any, depth: int = 0) -> Any:
    """A JSON-safe, size-bounded view of a value: content becomes a hash.

    Metadata (ids, enums, numbers, booleans, short strings) passes through;
    anything long becomes `{sha256, chars}` (or a bounded prefix in raw mode).
    Unknown types degrade to a type name rather than an exception, because a
    tracer must never break the run it observes.
    """
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        cap = _raw_cap if _raw_debug else RAW_ATTR_CAP
        if len(value) <= cap:
            return value
        out: dict[str, Any] = {"sha256": sha256_text(value), "chars": len(value)}
        if _raw_debug:
            out["prefix"] = value[:cap]
        out["truncated"] = True
        return out
    if depth >= 6:
        return "<max-depth>"
    if isinstance(value, dict):
        return {str(k): _safe_value(v, depth + 1) for k, v in list(value.items())[:64]}
    if isinstance(value, (list, tuple, set)):
        return [_safe_value(v, depth + 1) for v in list(value)[:64]]
    return f"<{type(value).__name__}>"


def sanitize(attrs: dict[str, Any]) -> dict[str, Any]:
    """Drop secrets by key, bound every value. The default record's safety."""
    out: dict[str, Any] = {}
    for key, value in (attrs or {}).items():
        if _is_secret_key(key):
            out[str(key)] = "[redacted]"
            continue
        out[str(key)] = _safe_value(value)
    return out


def prompt_attrs(prompt_ref: "PromptRef | None") -> dict[str, Any] | None:
    return None if prompt_ref is None else prompt_ref.as_dict()


# ------------------------------------------------------------- prompt identity

@dataclass(frozen=True)
class PromptRef:
    """Prompt provenance: a stable semantic name, an explicit version, and the
    exact-content hash. The caller supplies it; the tracer never guesses."""

    name: str
    version: str
    sha256: str

    @classmethod
    def of(cls, name: str, version: str, text: str) -> "PromptRef":
        return cls(name=name, version=version, sha256=sha256_text(text or ""))

    @property
    def key(self) -> str:
        return f"{self.name}@{self.version}"

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "version": self.version, "sha256": self.sha256}


# ----------------------------------------------------------- status & errors

def error_kind_of(exc: BaseException) -> str:
    """Map an exception to the trace's error taxonomy by class *name*, so this
    module never imports the contract/rag/tools layers (no cycles)."""
    name = type(exc).__name__
    return {
        "RateLimitError": ERR_RATE_LIMIT,
        "ProviderTimeout": ERR_TIMEOUT,
        "InvalidKeyError": ERR_INVALID_KEY,
        "BadRequestError": ERR_BAD_REQUEST,
        "NetworkError": ERR_NETWORK,
        "URLError": ERR_NETWORK,
        "ProviderError": ERR_PROVIDER,
        "DimensionMismatch": ERR_DIMENSION,
        "ToolError": ERR_TOOL,
        "TimeoutError": ERR_TIMEOUT,
        "CancelledError": STATUS_CANCELLED,
    }.get(name, ERR_INTERNAL)


def status_for_error(exc: BaseException) -> str:
    if type(exc).__name__ == "ProviderTimeout":
        return STATUS_TIMEOUT
    if type(exc).__name__ in ("CancelledError", "GeneratorExit", "KeyboardInterrupt"):
        return STATUS_CANCELLED
    return STATUS_ERROR


def error_fact(exc: BaseException, *, retryable: bool | None = None) -> dict[str, Any]:
    kind = error_kind_of(exc)
    fact: dict[str, Any] = {
        "kind": kind,
        "type": type(exc).__name__,
        "message": _safe_value(str(exc)),
    }
    provider = getattr(exc, "provider", None)
    if provider:
        fact["provider"] = str(provider)
    if retryable is None:
        retryable = bool(getattr(exc, "retryable", False))
    fact["retryable"] = bool(retryable)
    return fact


# ---------------------------------------------------------------------- Event

@dataclass
class Event:
    name: str
    at: float
    attributes: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "at": self.at,
                "attributes": sanitize(self.attributes)}


# ----------------------------------------------------------------------- Span

@dataclass
class Span:
    """A timed operation: who ran, under which parent, for how long, how it ended.

    `started_at`/`ended_at` are UTC wall timestamps (for humans and search);
    `duration_ms` is derived from `time.monotonic_ns()` so a host-clock
    correction can never make a span negative. Events on a closed span are
    rejected — the tree must not be able to lie.
    """

    trace_id: str
    span_id: str
    parent_span_id: str | None
    kind: str
    name: str
    started_at: float
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[Event] = field(default_factory=list)
    status: str = STATUS_OK
    error: dict[str, Any] | None = None
    ended_at: float | None = None
    duration_ms: float | None = None
    closed: bool = False
    # Day 24 — the control edge this span crosses, and the far side we do not own.
    span_kind: str = SPAN_INTERNAL
    remote: bool = False
    remote_endpoint: str | None = None
    component: str = ""
    _start_ns: int = field(default_factory=time.monotonic_ns)
    _end_ns: int | None = None

    def update(self, **attrs: Any) -> None:
        self.attributes.update(attrs)

    def add_event(self, event: str, **attrs: Any) -> Event:
        if self.closed:
            raise RuntimeError(f"span {self.span_id} is closed; events are not "
                               "accepted after it ends")
        ev = Event(name=event, at=time.time(), attributes=dict(attrs))
        self.events.append(ev)
        return ev

    def end(self, *, status: str = STATUS_OK, error: dict[str, Any] | None = None,
            **attrs: Any) -> None:
        if self.closed:
            raise RuntimeError(f"span {self.span_id} already ended")
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}")
        self.attributes.update(attrs)
        self._end_ns = time.monotonic_ns()
        self.ended_at = time.time()
        self.duration_ms = round((self._end_ns - self._start_ns) / 1e6, 3)
        self.status = status
        self.error = error
        self.closed = True
        _fire_span_hooks(self)

    def as_dict(self) -> dict[str, Any]:
        out = {
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "kind": self.kind,
            "span_kind": self.span_kind,
            "name": self.name,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "remote": self.remote,
            "attributes": sanitize(self.attributes),
            "error": self.error,
            "events": [e.as_dict() for e in self.events],
        }
        if self.remote_endpoint:
            out["remote_endpoint"] = self.remote_endpoint
        if self.component:
            out["component"] = self.component
        return out


# ---------------------------------------------------------------------- Trace

_DEFAULT_PARENT = object()


class Trace:
    """One root request: identity, wall clock, monotonic duration, span tree,
    linked ledger records, and safe totals."""

    def __init__(self, operation: str, *, trace_id: str | None = None,
                 attributes: dict[str, Any] | None = None,
                 store: "TraceStore | None" = None,
                 parent_context: "TraceContext | None" = None,
                 kind: str = SPAN_SERVER,
                 continuation_reason: str | None = None,
                 baggage: dict[str, str] | None = None) -> None:
        self.schema_version = SCHEMA_VERSION
        self.trace_id = (parent_context.trace_id if parent_context is not None
                         else (trace_id or new_trace_id()))
        self.operation = operation
        self.attributes: dict[str, Any] = dict(attributes or {})
        self.spans: list[Span] = []
        self.records: list[Any] = []           # linked CallRecord objects
        self.status: str = STATUS_OK
        self.error: dict[str, Any] | None = None
        self.started_at: float = time.time()
        self.ended_at: float | None = None
        self.duration_ms: float | None = None
        self.closed: bool = False
        self.recording: bool = True
        self.store = store
        self.root: Span | None = None
        # Day 24 — how this request got its identity (continued or a named reason)
        # and the bounded, non-secret baggage it carries onward.
        self.parent_context = parent_context
        self.kind = kind
        self.continuation_reason = continuation_reason
        self.baggage: dict[str, str] = dict(baggage or {})
        self._start_ns = time.monotonic_ns()
        self._seq = 0
        self._stack: list[Span] = []
        self._trace_token = None
        self._span_token = None

    # ----------------------------------------------------------- lifecycle

    def start(self) -> "Trace":
        self.root = self.start_span(KIND_REQUEST, self.operation, parent=None,
                                    span_kind=self.kind, component=self.attributes.get("component", ""))
        if self.parent_context is not None:
            # Continuation: the root is a *remote child* — a fresh span whose
            # parent lives in another process. Its own tree is unchanged; only
            # the edge is marked remote (the flag exists so nobody later claims
            # we measured the caller's internals).
            self.root.parent_span_id = self.parent_context.span_id
            self.root.remote = True
        self._trace_token = _CURRENT_TRACE.set(self)
        self._span_token = _CURRENT_SPAN.set(self.root)
        if self.store is not None:
            self.store.put(self)
        return self

    def finish(self, *, status: str | None = None,
               error: dict[str, Any] | None = None) -> "Trace":
        """Close the root exactly once.

        The root's status is the **request's** outcome, not the worst descendant:
        a request that recovered from a failed attempt via retry/fallback is
        `ok`, and its error child stays visible in the tree. A caller that caught
        a terminal failure passes `status=`/`error=` explicitly. Any span left
        open at finish is an instrumentation bug and is closed as `internal` —
        so no descendant can be silently abandoned.
        """
        if self.closed:
            return self
        abandoned = [s for s in self.spans
                     if s is not self.root and not s.closed]
        for span in abandoned:
            span.end(status=STATUS_ERROR,
                     error={"kind": ERR_INTERNAL,
                            "message": "unclosed span at trace finish"})
        if self.root is not None and not self.root.closed:
            if status is None and abandoned:
                status = STATUS_ERROR
            self.root.end(status=status or STATUS_OK, error=error)
        self.status = self.root.status if self.root else (status or STATUS_OK)
        self.error = self.root.error if self.root else error
        self.ended_at = time.time()
        self.duration_ms = (round((time.monotonic_ns() - self._start_ns) / 1e6, 3)
                            if self.root is None else self.root.duration_ms)
        self.closed = True
        if _CURRENT_TRACE.get() is self:
            if self._span_token is not None:
                _CURRENT_SPAN.reset(self._span_token)
            if self._trace_token is not None:
                _CURRENT_TRACE.reset(self._trace_token)
        _fire_trace_hooks(self)
        return self

    def _next_id(self) -> str:
        """A 16-hex span id, W3C-valid and globally unique within the trace.

        Day 24 — the id is 8 bytes (16 lowercase hex) so it is a legal
        `traceparent` parent id, and it is *random* rather than a per-trace
        sequence: two processes continuing the same trace each mint their own
        spans, and a sequence-based id would collide at `…0001` the moment a
        worker adopted the parent's trace id (the exact bug the assembler would
        then have to dedupe as a "duplicate"). The trace-id prefix is kept so
        Day 23's "every span shares the root's id" property holds unchanged.
        """
        self._seq += 1
        return f"{self.trace_id[:10]}{uuid.uuid4().hex[:6]}"

    # -------------------------------------------------------------- spans

    def start_span(self, kind: str, name: str, *,
                   attributes: dict[str, Any] | None = None,
                   parent: Any = _DEFAULT_PARENT,
                   span_kind: str = SPAN_INTERNAL,
                   remote: bool = False,
                   remote_endpoint: str | None = None,
                   component: str = "") -> Span:
        if self.closed:
            raise RuntimeError(f"trace {self.trace_id} is closed")
        if parent is _DEFAULT_PARENT:
            parent = self._stack[-1] if self._stack else self.root
        span = Span(
            trace_id=self.trace_id,
            span_id=self._next_id(),
            parent_span_id=(parent.span_id if isinstance(parent, Span) else None),
            kind=kind,
            name=name,
            started_at=time.time(),
            attributes=dict(attributes or {}),
            span_kind=span_kind,
            remote=remote,
            remote_endpoint=remote_endpoint,
            component=component,
        )
        self.spans.append(span)
        self._stack.append(span)
        return span

    def leave(self, span: Span) -> None:
        """Pop a manually-managed span off the active stack (idempotent)."""
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i] is span:
                del self._stack[i]
                return

    def current_span(self) -> Span | None:
        return self._stack[-1] if self._stack else self.root

    @contextmanager
    def span(self, kind: str, name: str, *,
             attributes: dict[str, Any] | None = None,
             parent: Any = _DEFAULT_PARENT,
             span_kind: str = SPAN_INTERNAL,
             remote: bool = False,
             remote_endpoint: str | None = None,
             component: str = "") -> Iterator[Span]:
        span = self.start_span(kind, name, attributes=attributes, parent=parent,
                               span_kind=span_kind, remote=remote,
                               remote_endpoint=remote_endpoint, component=component)
        try:
            yield span
            if not span.closed:
                span.end(status=STATUS_OK)
        except BaseException as exc:
            if not span.closed:
                span.end(status=status_for_error(exc), error=error_fact(exc))
            raise
        finally:
            self.leave(span)

    # ------------------------------------------------------------- events

    def add_event(self, event: str, **attrs: Any) -> Event | None:
        span = self.current_span()
        while span is not None and span.closed and self._stack:
            self._stack.pop()
            span = self.current_span()
        if span is None or span.closed:
            return None
        return span.add_event(event, **attrs)

    # ----------------------------------------------------------- ledger

    def link(self, record: Any) -> Any:
        """Attach a `CallRecord` — the trace's billing truth. Totals aggregate
        exactly these rows under the ledger's own policy."""
        if record is not None:
            self.records.append(record)
        return record

    def totals(self) -> dict[str, Any]:
        recs = self.records
        unknown = any(getattr(r, "price_status", "known") == "unknown" for r in recs)
        return {
            "records": len(recs),
            "attempts": len(recs),
            "ok": sum(1 for r in recs if getattr(r, "ok", False)),
            "failed": sum(1 for r in recs if not getattr(r, "ok", False)),
            "input_tokens": sum(int(getattr(r, "input_tokens", 0)) for r in recs),
            "output_tokens": sum(int(getattr(r, "output_tokens", 0)) for r in recs),
            "tokens": sum(int(getattr(r, "input_tokens", 0))
                          + int(getattr(r, "output_tokens", 0)) for r in recs),
            "cost_usd": round(sum(float(getattr(r, "cost_usd", 0.0)) for r in recs), 8),
            "price_status": "unknown" if unknown else "known",
        }

    # ------------------------------------------------------- serialization

    def events(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for span in self.spans:
            for ev in span.events:
                out.append({"span_id": span.span_id, **ev.as_dict()})
        return out

    def as_dict(self) -> dict[str, Any]:
        out = {
            "schema_version": self.schema_version,
            "trace_id": self.trace_id,
            "operation": self.operation,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "attributes": sanitize(self.attributes),
            "totals": self.totals(),
            "spans": [s.as_dict() for s in self.spans],
            "events": self.events(),
        }
        # Day 24 — the continuation record is additive: a reader can always see
        # whether identity crossed a wire and, if it did not, *why* a fresh root
        # was minted. A trace that never touched Day 24 machinery omits nothing
        # it had before.
        if self.continuation_reason is not None or self.parent_context is not None:
            out["continuation"] = {
                "reason": self.continuation_reason,
                "continued": self.parent_context is not None,
                "parent_span_id": (self.parent_context.span_id
                                   if self.parent_context is not None else None),
                "baggage_keys": sorted(self.baggage),
            }
        return out


class NullTrace:
    """The tracing-off object: every entry point is a no-op, so an untraced run
    is byte-equivalent to the pre-Day-23 process (O9)."""

    schema_version = SCHEMA_VERSION
    trace_id = None
    operation = ""
    recording = False
    closed = True
    status = STATUS_OK
    error = None
    duration_ms = None
    spans: list = []
    records: list = []
    attributes: dict = {}
    baggage: dict = {}
    kind = SPAN_SERVER
    continuation_reason = None
    parent_context = None

    def start(self) -> "NullTrace":
        return self

    def finish(self, **_: Any) -> "NullTrace":
        return self

    def start_span(self, *_: Any, **__: Any) -> None:
        return None

    def leave(self, *_: Any, **__: Any) -> None:
        return None

    def current_span(self) -> None:
        return None

    @contextmanager
    def span(self, *_: Any, **__: Any) -> Iterator[None]:
        yield None

    def add_event(self, *_: Any, **__: Any) -> None:
        return None

    def link(self, record: Any) -> Any:
        return record

    def totals(self) -> dict[str, Any]:
        return {"records": 0, "attempts": 0, "ok": 0, "failed": 0,
                "input_tokens": 0, "output_tokens": 0, "tokens": 0,
                "cost_usd": 0.0, "price_status": "known"}

    def events(self) -> list:
        return []

    def as_dict(self) -> dict[str, Any]:
        return {}


_NULL = NullTrace()

# --------------------------------------------------------------- trace store

class _SinkTraceView:
    """A rehydrated trace read back from the durable sink (WS7).

    The in-process `Trace` is gone after a restart; this view carries its
    sanitized `as_dict()` rendering so `GET /traces/{id}` keeps working.
    """

    recording = True
    closed = True

    def __init__(self, doc: dict) -> None:
        self._doc = dict(doc)
        self.trace_id = self._doc.get("trace_id")

    def as_dict(self) -> dict:
        return dict(self._doc)


class TraceStore:
    """A bounded, thread-safe, oldest-first-evicting local store.

    Deliberately not a database or an exporter: Day 23 needs `GET
    /traces/{id}` to work in-process, and nothing more. A later day may swap in
    a real backend behind the same schema.

    WS7 — the later day is today: an optional durable `sink` (see
    `ai_os/trace_sink.py`) sits behind the same schema. `put` writes through
    to the sink (best-effort, fail-open); `get` falls back to the sink, so a
    trace survives a restart. The 256 cap now bounds only the *local*
    fallback ring — the sink is bounded by retention, not eviction.
    """

    def __init__(self, max_traces: int = 256, sink: Any = None) -> None:
        self.max_traces = max(1, int(max_traces))
        self._lock = threading.Lock()
        self._traces: "OrderedDict[str, Trace]" = OrderedDict()
        self.evictions = 0
        self.sink = sink

    def put(self, trace: "Trace | NullTrace") -> None:
        tid = getattr(trace, "trace_id", None)
        if not tid:
            return
        with self._lock:
            self._traces[tid] = trace
            self._traces.move_to_end(tid)
            while len(self._traces) > self.max_traces:
                self._traces.popitem(last=False)
                self.evictions += 1
        if self.sink is not None and getattr(trace, "recording", True):
            try:
                self.sink.put(tid, trace.as_dict())
            except Exception:                               # noqa: BLE001
                pass            # sink is fail-open; the local ring is the fact
            else:
                # WS7 — the SinkLag alert reads this. Set on *successful* put
                # only: a sink that throws must age toward the alert, not be
                # papered over by an attempted write.
                try:
                    from ai_os import metrics as _m
                    import time as _t
                    _m.set_gauge("trace_sink_last_write_epoch", _t.time())
                except Exception:                           # noqa: BLE001
                    pass        # metrics are fail-open, same as the sink

    def get(self, trace_id: str) -> "Trace | NullTrace | None":
        with self._lock:
            hit = self._traces.get(trace_id)
        if hit is not None:
            return hit
        if self.sink is not None:
            try:
                doc = self.sink.get(trace_id)
            except Exception:                               # noqa: BLE001
                doc = None
            if isinstance(doc, dict) and doc.get("trace_id"):
                return _SinkTraceView(doc)
        return None

    def ids(self) -> list[str]:
        with self._lock:
            return list(self._traces)

    def __len__(self) -> int:
        with self._lock:
            return len(self._traces)


_DEFAULT_STORE = TraceStore()


def default_store() -> TraceStore:
    return _DEFAULT_STORE


def set_default_store(store: TraceStore) -> None:
    global _DEFAULT_STORE
    _DEFAULT_STORE = store


# ------------------------------------------------------------------ context

_CURRENT_TRACE: ContextVar[Any] = ContextVar("cc_ai_current_trace", default=None)
_CURRENT_SPAN: ContextVar[Any] = ContextVar("cc_ai_current_span", default=None)


def new_trace_id() -> str:
    """A fresh 16-byte trace id, 32 lowercase hex — a valid W3C `trace_id`.

    Day 24 changed this from a `tr-`-prefixed short id to the wire shape for one
    reason: the id must be able to *cross a socket* unchanged. A local-only id
    that cannot be a `traceparent` trace id would force a mapping layer, and a
    mapping layer is where "continue, don't restart" quietly becomes "restart".
    """
    return uuid.uuid4().hex


def new_span_id() -> str:
    """A fresh 8-byte span id, 16 lowercase hex — a valid W3C `parent_id`."""
    return uuid.uuid4().hex[:16]


def current_trace() -> Any:
    tr = _CURRENT_TRACE.get()
    if tr is not None and getattr(tr, "closed", False):
        return None
    return tr


def current_span() -> Any:
    tr = current_trace()
    return tr.current_span() if tr is not None else None


def current_context() -> "TraceContext | None":
    """The active span's W3C context — the parent every outbound call carries."""
    tr = current_trace()
    if tr is None:
        return None
    span = tr.current_span() or getattr(tr, "root", None)
    sid = getattr(span, "span_id", None) or new_span_id()
    ctx = getattr(tr, "parent_context", None)
    if ctx is not None:
        return TraceContext(trace_id=tr.trace_id, span_id=sid, flags=ctx.flags)
    return TraceContext(trace_id=tr.trace_id, span_id=sid)


def start_trace(operation: str, *, attributes: dict[str, Any] | None = None,
                store: "TraceStore | None" = None,
                trace_id: str | None = None,
                parent_context: "TraceContext | None" = None,
                kind: str = SPAN_SERVER,
                continuation_reason: str | None = None,
                baggage: dict[str, str] | None = None) -> Any:
    """Create and activate a root trace, or return the `NullTrace` when tracing
    is off. The id is minted at the process boundary only — children attach.

    Day 24 — when `parent_context` is supplied the root is a **remote child**:
    the incoming trace id is adopted, a fresh span id is minted, and the edge is
    marked remote. A request that arrives with no (or a bad) header mints a fresh
    root and records *why* via `continuation_reason`.
    """
    if not tracing_enabled():
        return _NULL
    existing = _CURRENT_TRACE.get()
    if (parent_context is None and existing is not None
            and not getattr(existing, "closed", False)):
        # Never nest a second root under an active one; the caller wants the
        # active trace (explicit `use_trace` handles deliberate hand-offs).
        return existing
    trace = Trace(operation, trace_id=trace_id, attributes=attributes,
                  store=store if store is not None else default_store(),
                  parent_context=parent_context, kind=kind,
                  continuation_reason=continuation_reason, baggage=baggage)
    return trace.start()


def finish_trace(trace: Any, *, status: str | None = None,
                 error: dict[str, Any] | None = None) -> None:
    if trace is None or not getattr(trace, "recording", False):
        return
    trace.finish(status=status, error=error)


@contextmanager
def use_trace(trace: Any) -> Iterator[Any]:
    """Activate an existing trace for a block (used by stream generators that
    execute outside the request's context)."""
    if trace is None or not getattr(trace, "recording", False):
        yield trace
        return
    t_token = _CURRENT_TRACE.set(trace)
    s_token = _CURRENT_SPAN.set(trace.root)
    try:
        yield trace
    finally:
        _CURRENT_SPAN.reset(s_token)
        _CURRENT_TRACE.reset(t_token)


# ------------------------------------------------- module-level helpers used
# by the gateway: begin an attempt span and end it explicitly (the retry loop
# needs the span's duration to cover the call and not the backoff sleep).

def begin_span(kind: str, name: str, *,
               attributes: dict[str, Any] | None = None,
               parent: Any = _DEFAULT_PARENT) -> Any:
    tr = current_trace()
    if tr is None or not getattr(tr, "recording", False):
        return None
    if parent is _DEFAULT_PARENT:
        return tr.start_span(kind, name, attributes=attributes)
    return tr.start_span(kind, name, attributes=attributes, parent=parent)


def end_span(span: Any, *, status: str = STATUS_OK,
             error: dict[str, Any] | None = None, **attrs: Any) -> None:
    if span is None or span.closed:
        return
    span.end(status=status, error=error, **attrs)


def leave_span(span: Any) -> None:
    if span is None:
        return
    tr = current_trace()
    if tr is not None and getattr(tr, "recording", False):
        tr.leave(span)


def add_final_answer_event(trace: Any, *, ans: str, citations: int = 0,
                           refusal: bool = False, **extra: Any) -> None:
    """Record the answer as a *safe* event: hash, size, citation count, status —
    never the prose. Used by routes that own no pipeline of their own."""
    if trace is None or not getattr(trace, "recording", False):
        return
    trace.add_event("final_answer", answer_sha256=sha256_text(ans or ""),
                    answer_chars=len(ans or ""), citations=int(citations),
                    refused=bool(refusal), **extra)


# ==========================================================================
# Day 24 §6 — propagation core, span reporting, and the assembler.
#
# Day 23's tree is only as wide as the process that built it: the trace id is a
# Python value that dies at the socket. Day 24 makes identity survive the wire
# and admits — in the record itself — where the topology went dark.
# ==========================================================================

# W3C Trace Context: `version-trace_id(32)-parent_id(16)-flags`, lowercase hex.
# The format is a *contract*: fixed widths, lowercase, non-zero ids, a version.
_TRACEPARENT_RE = re.compile(
    r"^([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")
_ZERO_TRACE = "0" * 32
_ZERO_SPAN = "0" * 16
TRACEPARENT = "traceparent"
BAGGAGE = "baggage"

# Baggage is untrusted, bounded, non-secret, and never authoritative. Only these
# keys ride along, each value is length-capped, and the whole set is capped — a
# forged inbound header is data to validate, never authority to obey.
BAGGAGE_ALLOW = ("tenant", "surface", "request_kind")
BAGGAGE_MAX_VALUE = 64
BAGGAGE_MAX_TOTAL = 256


@dataclass(frozen=True)
class TraceContext:
    """One span's place on the wire: the trace it belongs to and its own id.

    `trace_id` is adopted on inbound (never re-minted for a continuation);
    `span_id` is always freshly minted for a child (local or remote). The two
    ids are different sizes and different lifetimes for a reason: the trace id
    names one distributed trace, the span id names one operation inside it.
    """

    trace_id: str
    span_id: str
    flags: str = "01"

    @property
    def sampled(self) -> bool:
        try:
            return bool(int(self.flags, 16) & 1)
        except (TypeError, ValueError):
            return False

    def child(self, span_id: str | None = None) -> "TraceContext":
        """A fresh span id under the same trace (local *or* remote — identical)."""
        return TraceContext(trace_id=self.trace_id,
                            span_id=span_id or new_span_id(), flags=self.flags)

    # `remote_child` is `child` with a name that documents intent at the call site.
    remote_child = child

    def as_traceparent(self) -> str:
        return f"00-{self.trace_id}-{self.span_id}-{self.flags}"

    def as_dict(self) -> dict[str, Any]:
        return {"trace_id": self.trace_id, "span_id": self.span_id,
                "flags": self.flags, "sampled": self.sampled}

    @classmethod
    def new_root(cls, *, sampled: bool = True) -> "TraceContext":
        return cls(trace_id=new_trace_id(), span_id=new_span_id(),
                   flags="01" if sampled else "00")

    @classmethod
    def parse(cls, text: Any) -> tuple["TraceContext | None", str]:
        """`(context, reason)`. A header that cannot be adopted names *why*."""
        if text is None:
            return None, REASON_NO_HEADER
        raw = str(text).strip()
        if not raw:
            return None, REASON_NO_HEADER
        m = _TRACEPARENT_RE.match(raw)
        if not m:
            # Wrong width, non-hex, uppercase, or the wrong number of parts.
            return None, REASON_MALFORMED
        version, trace_id, span_id, flags = m.groups()
        if version != "00":
            # A future version is well-formed but this process does not speak it;
            # we mint a fresh root rather than guess at its semantics.
            return None, REASON_VERSION_UNSUPPORTED
        if trace_id == _ZERO_TRACE or span_id == _ZERO_SPAN:
            return None, REASON_ALL_ZERO
        return cls(trace_id=trace_id, span_id=span_id, flags=flags), REASON_CONTINUED


@dataclass
class ExtractResult:
    context: "TraceContext | None"
    reason: str
    raw: str | None = None
    baggage: dict[str, str] = field(default_factory=dict)

    @property
    def continued(self) -> bool:
        return self.context is not None


def _header_lookup(carrier: Mapping[str, Any] | None, name: str) -> Any:
    """Case/underscore-tolerant header lookup (`http_traceparent` → `traceparent`)."""
    for key, value in (carrier or {}).items():
        norm = str(key).strip().lower().replace("_", "-")
        if norm == name or norm.endswith("-" + name):
            return value
    return None


def extract_baggage(carrier: Mapping[str, Any] | None) -> dict[str, str]:
    """Parse the allow-listed, bounded, non-secret baggage; drop everything else.

    A key not on the allow-list is dropped; an over-long value is truncated;
    the total is capped. Baggage may never select a store, tenant, index, or
    permission — this function returns *data*, and callers must treat it so.
    """
    raw = _header_lookup(carrier, BAGGAGE)
    if not raw:
        return {}
    out: dict[str, str] = {}
    total = 0
    for part in str(raw).split(","):
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        key = key.strip().lower()
        value = value.strip()
        if key not in BAGGAGE_ALLOW or not value:
            continue
        if _is_secret_key(key):
            continue
        value = value[:BAGGAGE_MAX_VALUE]
        total += len(key) + len(value)
        if total > BAGGAGE_MAX_TOTAL:
            break
        out[key] = value
    return out


def format_baggage(baggage: Mapping[str, str] | None) -> str:
    if not baggage:
        return ""
    parts = []
    for key in sorted(baggage):
        if key in BAGGAGE_ALLOW and not _is_secret_key(key):
            parts.append(f"{key}={str(baggage[key])[:BAGGAGE_MAX_VALUE]}")
    return ",".join(parts)


def extract(carrier: Mapping[str, Any] | None) -> ExtractResult:
    """Parse inbound headers: adopt a valid trace, or name why we did not."""
    raw = _header_lookup(carrier, TRACEPARENT)
    ctx, reason = TraceContext.parse(raw)
    return ExtractResult(context=ctx, reason=reason,
                         raw=(None if raw is None else str(raw)),
                         baggage=extract_baggage(carrier))


def inject(carrier: dict[str, str], context: "TraceContext",
           baggage: Mapping[str, str] | None = None) -> dict[str, str]:
    """Write the current context (and bounded baggage) into an outbound carrier."""
    if not propagation_enabled() or context is None:
        return carrier
    carrier[TRACEPARENT] = context.as_traceparent()
    bag = format_baggage(baggage)
    if bag:
        carrier[BAGGAGE] = bag
    return carrier


def outbound_headers() -> dict[str, str]:
    """The headers an outbound call must carry. `{}` when propagation is off.

    Forgetting to inject is how one distributed trace silently becomes two
    healthy local traces; this is the single choke point every client edge uses.
    """
    if not propagation_enabled():
        return {}
    ctx = current_context()
    if ctx is None:
        return {}
    tr = current_trace()
    baggage = getattr(tr, "baggage", None) if tr is not None else None
    return inject({}, ctx, baggage)


def start_server_trace(operation: str, carrier: Mapping[str, Any] | None, *,
                       attributes: dict[str, Any] | None = None,
                       store: "TraceStore | None" = None,
                       component: str | None = None) -> tuple[Any, ExtractResult]:
    """The inbound edge in one call: extract → continue-or-mint → `server` root.

    A malformed header never fails the request; it mints a fresh root whose
    `continuation.reason` says what was wrong (`malformed`, `all_zero`, …).
    """
    result = extract(carrier)
    attrs = dict(attributes or {})
    if component is not None:
        attrs.setdefault("component", component)
    trace = start_trace(operation, attributes=attrs, store=store,
                        parent_context=(result.context if result.continued else None),
                        kind=SPAN_SERVER, continuation_reason=result.reason,
                        baggage=result.baggage)
    return trace, result


# ----------------------------------------------------- propagation switches

_PROPAGATION = ContextVar("cc_ai_propagation", default=None)
_PROPAGATION_ENABLED = os.getenv("TRACE_PROPAGATION", "1").strip().lower() not in {
    "0", "off", "false", "no"}


def propagation_enabled() -> bool:
    override = _PROPAGATION.get()
    return _PROPAGATION_ENABLED if override is None else bool(override)


def set_propagation(on: bool) -> None:
    global _PROPAGATION_ENABLED
    _PROPAGATION_ENABLED = bool(on)


@contextmanager
def propagation(on: bool) -> Iterator[None]:
    token = _PROPAGATION.set(bool(on))
    try:
        yield
    finally:
        _PROPAGATION.reset(token)


# ------------------------------------------------------- span-close hooks

_SPAN_HOOKS: list[Callable[[Span], None]] = []
_TRACE_HOOKS: list[Callable[[Any], None]] = []


def register_span_hook(fn: Callable[[Span], None]) -> None:
    """Register a callback fired on every span close (metrics use this).

    Fail-open by construction: a hook that raises is swallowed, because a metric
    must never be able to break the operation it observes (D9).
    """
    _SPAN_HOOKS.append(fn)


def register_trace_hook(fn: Callable[[Any], None]) -> None:
    """Register a callback fired when a root trace closes (ledger totals)."""
    _TRACE_HOOKS.append(fn)


def clear_span_hooks() -> None:
    _SPAN_HOOKS.clear()


def clear_trace_hooks() -> None:
    _TRACE_HOOKS.clear()


def _fire_span_hooks(span: Span) -> None:
    for fn in list(_SPAN_HOOKS):
        try:
            fn(span)
        except Exception:                                  # noqa: BLE001
            pass


def _fire_trace_hooks(trace: Any) -> None:
    for fn in list(_TRACE_HOOKS):
        try:
            fn(trace)
        except Exception:                                  # noqa: BLE001
            pass


# ------------------------------------------------------------- SpanReport
# The unit that crosses a wire: one closed span's serialized record plus where
# it came from. Deliberately the *same* span schema Day 23 serves, so assembly
# produces one artifact and an inspector has one lookup.

@dataclass
class SpanReport:
    trace_id: str
    span: dict[str, Any]
    origin: str = ""
    reported_at: float = 0.0

    def __post_init__(self) -> None:
        if not self.reported_at:
            self.reported_at = time.time()

    @property
    def span_id(self) -> str:
        return self.span.get("span_id") or ""

    @property
    def parent_span_id(self) -> str | None:
        return self.span.get("parent_span_id")

    @property
    def name(self) -> str:
        return self.span.get("name") or ""

    @property
    def component(self) -> str:
        return self.span.get("component") or self.origin or ""

    @property
    def duration_ms(self) -> float | None:
        return self.span.get("duration_ms")

    @classmethod
    def from_span(cls, span: Span, *, origin: str = "") -> "SpanReport":
        return cls(trace_id=span.trace_id, span=span.as_dict(), origin=origin)

    def as_dict(self) -> dict[str, Any]:
        return {"trace_id": self.trace_id, "span": self.span,
                "origin": self.origin, "reported_at": self.reported_at}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SpanReport":
        return cls(trace_id=str(payload.get("trace_id") or ""),
                   span=dict(payload.get("span") or {}),
                   origin=str(payload.get("origin") or ""),
                   reported_at=float(payload.get("reported_at") or 0.0))


# ---------------------------------------------------------------- Topology
# Hand-written per component: which child spans it is *expected* to produce.
# That is what makes "the worker died" distinguishable from "the worker was
# never asked", and it is how a gap gets a name instead of a shrug.

@dataclass
class Topology:
    expected: list[tuple[str, str]] = field(default_factory=list)

    def expect(self, component: str, name: str) -> "Topology":
        pair = (component, name)
        if pair not in self.expected:
            self.expected.append(pair)
        return self

    def as_list(self) -> list[dict[str, str]]:
        return [{"component": c, "name": n} for c, n in self.expected]


# ------------------------------------------------------------ TraceAssembler
# Assembly is idempotent on `span_id`, order-free, and loss-aware: duplicates are
# counted (never double-counted), a report that arrives after assembly is marked
# late, and an expected-but-absent span is materialized as a named gap.

class TraceAssembler:
    def __init__(self, trace_id: str, *, topology: Topology | None = None,
                 store: "TraceStore | None" = None, operation: str = "") -> None:
        self.schema_version = SCHEMA_VERSION
        self.trace_id = trace_id
        self.operation = operation
        self.topology = topology or Topology()
        self.spans: "OrderedDict[str, dict]" = OrderedDict()
        self.duplicates: "Counter[str]" = Counter()
        self.late: list[str] = []
        self.delivered = 0
        self.reports = 0
        self.store = store
        self.attributes: dict[str, Any] = {}
        self.status = STATUS_OK
        self.started_at: float | None = None
        self.ended_at: float | None = None
        self._assembled = False

    # ---------------------------------------------------------- ingestion

    def expect(self, component: str, name: str) -> "TraceAssembler":
        self.topology.expect(component, name)
        return self

    def ingest(self, report: SpanReport) -> str:
        """Merge one report. Returns `delivered` | `duplicate` | `late`."""
        self.reports += 1
        sid = report.span_id
        if not sid:
            return "malformed"
        if sid in self.spans:
            self.duplicates[sid] += 1
            self._persist()
            return "duplicate"
        self.spans[sid] = dict(report.span)
        self._note_bounds(report.span)
        if self._assembled:
            self.late.append(sid)
            self._persist()
            return "late"
        self.delivered += 1
        self._persist()
        return "delivered"

    def absorb(self, trace: Any) -> "TraceAssembler":
        """Fold a *local* `Trace`'s spans in, so one tree serves both processes.

        The local spans and the reported spans are the same schema, so absorbing
        is just ingestion — there is no second artifact and no schema fork.
        """
        origin = (trace.attributes or {}).get("component") or "local"
        for span in trace.spans:
            self.ingest(SpanReport.from_span(span, origin=origin))
        self.attributes.update(trace.attributes or {})
        self.operation = self.operation or trace.operation
        if trace.status != STATUS_OK:
            self.status = trace.status
        self._persist()
        return self

    def _note_bounds(self, span: Mapping[str, Any]) -> None:
        started = span.get("started_at")
        ended = span.get("ended_at")
        if started is not None and (self.started_at is None or started < self.started_at):
            self.started_at = started
        if ended is not None and (self.ended_at is None or ended > self.ended_at):
            self.ended_at = ended

    def _persist(self) -> None:
        if self.store is not None:
            self.store.put(self)

    def finalize(self) -> "TraceAssembler":
        """Mark assembly complete; reports after this are *late*, not new."""
        self._assembled = True
        self._persist()
        return self

    # --------------------------------------------------------- completeness

    def root(self) -> dict[str, Any] | None:
        ids = set(self.spans)
        for span in self.spans.values():
            if span.get("parent_span_id") is None:
                return span
        # A trace assembled only from reports is rooted at a remote child whose
        # parent is not in this process; that span is still the local root.
        for span in self.spans.values():
            if span.get("parent_span_id") not in ids:
                return span
        return next(iter(self.spans.values()), None)

    def components_seen(self) -> list[str]:
        seen = {s.get("component") for s in self.spans.values() if s.get("component")}
        seen |= {s.get("attributes", {}).get("component")
                 for s in self.spans.values()
                 if s.get("attributes", {}).get("component")}
        return sorted(str(c) for c in seen if c)

    def gaps(self) -> list[dict[str, Any]]:
        """Expected-but-absent spans, each naming the component and the reason."""
        present = {(s.get("component") or s.get("attributes", {}).get("component"),
                    s.get("name")) for s in self.spans.values()}
        names_present = {s.get("name") for s in self.spans.values()}
        out: list[dict[str, Any]] = []
        for component, name in self.topology.expected:
            if (component, name) not in present and name not in names_present:
                out.append({"component": component, "span": name,
                            "reason": "never_arrived", "expected": True})
        return out

    # `missing` is the day's vocabulary; `gaps` is the same list under one name.
    missing = gaps

    def complete(self) -> bool:
        if self.gaps():
            return False
        # A span that never closed is an unfinished story, not a complete one.
        return all(s.get("duration_ms") is not None for s in self.spans.values())

    def duration_ms(self) -> float | None:
        root = self.root()
        return root.get("duration_ms") if root else None

    def totals(self) -> dict[str, Any]:
        return {"records": 0, "attempts": 0, "ok": 0, "failed": 0,
                "input_tokens": 0, "output_tokens": 0, "tokens": 0,
                "cost_usd": 0.0, "price_status": "known"}

    def events(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for span in self.spans.values():
            for ev in span.get("events") or []:
                out.append({"span_id": span.get("span_id"), **ev})
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "trace_id": self.trace_id,
            "operation": self.operation,
            "assembled": True,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            # The end-to-end number is the root/critical path, never Σ hop times.
            "duration_ms": self.duration_ms(),
            "status": self.status,
            "attributes": sanitize(self.attributes),
            "totals": self.totals(),
            "spans": list(self.spans.values()),
            "events": self.events(),
            "complete": self.complete(),
            "span_count": len(self.spans),
            "gap_count": len(self.gaps()),
            "gaps": self.gaps(),
            "components_seen": self.components_seen(),
            "duplicates": {sid: n for sid, n in self.duplicates.items()},
            "late": list(self.late),
            "reports": self.reports,
        }


# ---------------------------------------------------------------- Collector
# Bounded, thread-safe, fail-open. It is a *local* collector: an in-process
# submit path plus an optional loopback-only HTTP ingest. No broker, no vendor.

class Collector:
    def __init__(self, store: "TraceStore | None" = None, *,
                 max_traces: int = 256) -> None:
        self.store = store if store is not None else default_store()
        self.max_traces = max(1, int(max_traces))
        self._lock = threading.Lock()
        self._assemblers: "OrderedDict[str, TraceAssembler]" = OrderedDict()
        self.submitted = 0
        self.failures = 0

    def assembler(self, trace_id: str, *,
                  topology: Topology | None = None) -> TraceAssembler:
        with self._lock:
            asm = self._assemblers.get(trace_id)
            if asm is None:
                asm = TraceAssembler(trace_id, topology=topology, store=self.store)
                self._assemblers[trace_id] = asm
                self._evict()
            elif topology is not None:
                for pair in topology.expected:
                    asm.topology.expect(*pair)
            return asm

    def _evict(self) -> None:
        while len(self._assemblers) > self.max_traces:
            self._assemblers.popitem(last=False)

    def submit(self, report: SpanReport) -> str | None:
        """Ingest one report. **Fail-open**: any failure is swallowed and counted."""
        try:
            self.submitted += 1
            return self.assembler(report.trace_id).ingest(report)
        except Exception:                                  # noqa: BLE001
            self.failures += 1
            return None

    def absorb(self, trace: Any) -> TraceAssembler | None:
        try:
            asm = self.assembler(trace.trace_id)
            asm.absorb(trace)
            return asm
        except Exception:                                  # noqa: BLE001
            self.failures += 1
            return None

    def get(self, trace_id: str) -> TraceAssembler | None:
        with self._lock:
            return self._assemblers.get(trace_id)


_DEFAULT_COLLECTOR = Collector()


def default_collector() -> Collector:
    return _DEFAULT_COLLECTOR


def set_default_collector(collector: Collector) -> None:
    global _DEFAULT_COLLECTOR
    _DEFAULT_COLLECTOR = collector


def submit_report(report: SpanReport) -> str | None:
    """Fail-open module-level submit — the path a worker's report takes home.

    Wrapped here as well as in `Collector.submit` so an injected/custom collector
    that raises still cannot reach the caller (D9's "collector that raises" row).
    """
    try:
        return default_collector().submit(report)
    except Exception:                                      # noqa: BLE001
        return None


def ingest_payload(payload: Mapping[str, Any]) -> bool:
    """The HTTP ingest route's body handler: parse one report and merge it."""
    try:
        report = SpanReport.from_dict(payload)
    except Exception:                                      # noqa: BLE001
        return False
    return submit_report(report) is not None


def finalize_request(trace: Any) -> "TraceAssembler | None":
    """If this request crossed a boundary, fold its local tree into the assembler.

    A request that never dispatched anything has no assembler, so this is a
    no-op and `GET /traces/{id}` keeps serving the same local `Trace` object it
    always did (Days 12–23 unchanged). A request that did dispatch gets one
    merged tree — same schema, same lookup, same redaction path.
    """
    if trace is None or not getattr(trace, "recording", False):
        return None
    collector = default_collector()
    asm = collector.get(trace.trace_id)
    if asm is None:
        return None
    asm.absorb(trace)
    asm.finalize()
    return asm


# ------------------------------------------------------- inbound edge helper

def response_trace_headers(trace: Any) -> dict[str, str]:
    """The response's additive identity headers (body + header, Day 24 §7).

    `X-Trace-Id` is Day 23's field; `traceparent` lets a downstream/client close
    the loop. Both are additive: the answer body is byte-unchanged (O9/D9).
    """
    if trace is None or not getattr(trace, "recording", False):
        return {}
    headers = {"X-Trace-Id": trace.trace_id}
    if propagation_enabled():
        ctx = current_context()
        if ctx is not None:
            headers[TRACEPARENT] = ctx.as_traceparent()
    return headers
