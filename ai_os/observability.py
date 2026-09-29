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
import json
import os
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator

SCHEMA_VERSION = 1

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
KIND_REQUEST = "request"
KIND_MODEL = "model"
KIND_RETRIEVAL = "retrieval"
KIND_STEP = "agent_step"
KIND_TOOL = "tool"
KIND_ANSWER = "final_answer"

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

    def as_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "kind": self.kind,
            "name": self.name,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "attributes": sanitize(self.attributes),
            "error": self.error,
            "events": [e.as_dict() for e in self.events],
        }


# ---------------------------------------------------------------------- Trace

_DEFAULT_PARENT = object()


class Trace:
    """One root request: identity, wall clock, monotonic duration, span tree,
    linked ledger records, and safe totals."""

    def __init__(self, operation: str, *, trace_id: str | None = None,
                 attributes: dict[str, Any] | None = None,
                 store: "TraceStore | None" = None) -> None:
        self.schema_version = SCHEMA_VERSION
        self.trace_id = trace_id or new_trace_id()
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
        self._start_ns = time.monotonic_ns()
        self._seq = 0
        self._stack: list[Span] = []
        self._trace_token = None
        self._span_token = None

    # ----------------------------------------------------------- lifecycle

    def start(self) -> "Trace":
        self.root = self.start_span(KIND_REQUEST, self.operation, parent=None)
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
        return self

    def _next_id(self) -> str:
        self._seq += 1
        return f"{self.trace_id[:10]}-{self._seq:03d}"

    # -------------------------------------------------------------- spans

    def start_span(self, kind: str, name: str, *,
                   attributes: dict[str, Any] | None = None,
                   parent: Any = _DEFAULT_PARENT) -> Span:
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
             parent: Any = _DEFAULT_PARENT) -> Iterator[Span]:
        span = self.start_span(kind, name, attributes=attributes, parent=parent)
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
        return {
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

class TraceStore:
    """A bounded, thread-safe, oldest-first-evicting local store.

    Deliberately not a database or an exporter: Day 23 needs `GET
    /traces/{id}` to work in-process, and nothing more. A later day may swap in
    a real backend behind the same schema.
    """

    def __init__(self, max_traces: int = 256) -> None:
        self.max_traces = max(1, int(max_traces))
        self._lock = threading.Lock()
        self._traces: "OrderedDict[str, Trace]" = OrderedDict()
        self.evictions = 0

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

    def get(self, trace_id: str) -> "Trace | NullTrace | None":
        with self._lock:
            return self._traces.get(trace_id)

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
    return f"tr-{uuid.uuid4().hex[:16]}"


def current_trace() -> Any:
    tr = _CURRENT_TRACE.get()
    if tr is not None and getattr(tr, "closed", False):
        return None
    return tr


def current_span() -> Any:
    tr = current_trace()
    return tr.current_span() if tr is not None else None


def start_trace(operation: str, *, attributes: dict[str, Any] | None = None,
                store: "TraceStore | None" = None,
                trace_id: str | None = None) -> Any:
    """Create and activate a root trace, or return the `NullTrace` when tracing
    is off. The id is minted at the process boundary only — children attach."""
    if not tracing_enabled():
        return _NULL
    existing = _CURRENT_TRACE.get()
    if existing is not None and not getattr(existing, "closed", False):
        # Never nest a second root under an active one; the caller wants the
        # active trace (explicit `use_trace` handles deliberate hand-offs).
        return existing
    trace = Trace(operation, trace_id=trace_id, attributes=attributes,
                  store=store if store is not None else default_store())
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
