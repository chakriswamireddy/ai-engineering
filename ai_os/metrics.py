"""Day 24 §8 — metrics: how many, how slow, how expensive, how often broken.

The third view, and the one most often abused. A **counter** answers *how many*;
a **histogram** with fixed, declared buckets answers *how slow, in what shape*.
Neither can name an instance. That is not a limitation to work around — it is
the design: **metrics cannot answer "which request was slow."** They can tell
you it was 3% of requests, and then the trace is where you go.

Two rules make this trustworthy, and both are code, not conventions:

1. **Increments fire at span close.** `register_span_hook(record_span)` ties the
   registry to `observability.py`, so metrics and traces cannot disagree about
   the same window. A hand-incremented counter on a path with no span is the bug.
2. **Labels come from an enumerable allow-list.** `check_labels` rejects a
   `trace_id`, a user, a question, an SQL string, or an error message — adding a
   high-cardinality label is a visible diff, never an accident.

The registry is a *local development surface* (`GET /metrics`), not a production
metrics stack: durable shipping, a real collector, and scrape-based alerting are
deployment concerns, and calling an in-memory JSON snapshot "monitoring" would
be the Day-24 version of calling an in-memory trace store "durable telemetry".
"""
from __future__ import annotations

import os
import threading
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterable, Iterator, Mapping

from ai_os import observability as obs

# Latency buckets in milliseconds, fixed and declared: a reader can reason about
# the histogram later, which is impossible if the buckets are chosen at runtime.
DEFAULT_LATENCY_BUCKETS: tuple[float, ...] = (
    1, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000)

# The bounded label set. Every one is enumerable in the source.
ALLOWED_LABELS = frozenset({
    "component", "operation", "model", "provider", "status", "error_kind",
    "corpus", "span_kind", "kind", "name", "level", "job", "topology", "role",
    "route",
})

# Labels that are *never* allowed, named so the rejection is legible.
FORBIDDEN_LABELS = frozenset({
    "trace_id", "span_id", "parent_span_id", "user", "principal", "session_id",
    "question", "sql", "query", "error", "message", "doc_id", "url", "key",
    "prompt", "answer",
})


class LabelError(ValueError):
    pass


def check_labels(labels: Mapping[str, Any]) -> None:
    for key in labels:
        if key in FORBIDDEN_LABELS:
            raise LabelError(
                f"label {key!r} is a high-cardinality bug: it names an instance, "
                "and metrics cannot name an instance")
        if key not in ALLOWED_LABELS:
            raise LabelError(
                f"label {key!r} is not on the allow-list; adding one is a code "
                "change, not an accident")


def _key(labels: Mapping[str, Any]) -> tuple:
    return tuple(sorted((str(k), str(v)) for k, v in labels.items()))


# ------------------------------------------------------------------ series

class Counter:
    def __init__(self, name: str, labels: Mapping[str, Any]):
        self.name = name
        self.labels = dict(labels)
        self.value = 0.0

    def inc(self, amount: float = 1.0) -> "Counter":
        self.value += amount
        return self

    def as_dict(self) -> dict[str, Any]:
        return {"type": "counter", "name": self.name, "labels": self.labels,
                "value": self.value}


class Histogram:
    def __init__(self, name: str, labels: Mapping[str, Any],
                 buckets: Iterable[float] = DEFAULT_LATENCY_BUCKETS):
        self.name = name
        self.labels = dict(labels)
        self.buckets = tuple(float(b) for b in buckets)
        self.counts = [0] * (len(self.buckets) + 1)   # +inf is the last
        self.sum = 0.0
        self.count = 0

    def observe(self, value: float) -> "Histogram":
        self.sum += value
        self.count += 1
        for i, edge in enumerate(self.buckets):
            if value <= edge:
                self.counts[i] += 1
                return self
        self.counts[-1] += 1
        return self

    @property
    def mean(self) -> float:
        return self.sum / self.count if self.count else 0.0

    def bucket_counts(self) -> dict[str, int]:
        out = {f"{int(b) if float(b).is_integer() else b}": c
               for b, c in zip(self.buckets, self.counts)}
        out["+Inf"] = self.counts[-1]
        return out

    def as_dict(self) -> dict[str, Any]:
        return {"type": "histogram", "name": self.name, "labels": self.labels,
                "buckets": self.bucket_counts(), "sum": round(self.sum, 3),
                "count": self.count, "mean": round(self.mean, 3)}


# ----------------------------------------------------------------- registry

class Registry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: "OrderedDict[tuple, Counter]" = OrderedDict()
        self._histograms: "OrderedDict[tuple, Histogram]" = OrderedDict()

    def counter(self, name: str, **labels: Any) -> Counter:
        check_labels(labels)
        key = (name, _key(labels))
        with self._lock:
            series = self._counters.get(key)
            if series is None:
                series = Counter(name, labels)
                self._counters[key] = series
            return series

    def histogram(self, name: str, *, buckets: Iterable[float] | None = None,
                  **labels: Any) -> Histogram:
        check_labels(labels)
        key = (name, _key(labels))
        with self._lock:
            series = self._histograms.get(key)
            if series is None:
                series = Histogram(name, labels,
                                   buckets if buckets is not None
                                   else DEFAULT_LATENCY_BUCKETS)
                self._histograms[key] = series
            return series

    # -------------------------------------------------------- derivation

    def record_span(self, span: obs.Span) -> None:
        self.counter("spans_total", span_kind=span.span_kind, kind=span.kind,
                     status=span.status).inc()
        if span.error:
            self.counter("span_errors_total", kind=span.kind,
                         error_kind=span.error.get("kind", "unknown")).inc()
        if span.span_kind == obs.SPAN_SERVER:
            self.counter("server_spans_total", component=span.component or "unknown").inc()
        if span.duration_ms is not None:
            self.histogram("span_duration_ms", kind=span.kind,
                           span_kind=span.span_kind).observe(span.duration_ms)

    def record_trace(self, trace: Any) -> None:
        totals = trace.totals()
        self.counter("traces_total", operation=trace.operation,
                     status=trace.status).inc()
        if totals.get("tokens"):
            self.counter("ledger_tokens_total",
                         operation=trace.operation).inc(totals["tokens"])
        if totals.get("cost_usd"):
            self.counter("ledger_cost_usd_total",
                         operation=trace.operation).inc(totals["cost_usd"])

    # ------------------------------------------------------ introspection

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            counters = [c.as_dict() for c in self._counters.values()]
            histograms = [h.as_dict() for h in self._histograms.values()]
        return {
            "counters": counters,
            "histograms": histograms,
            "series": len(counters) + len(histograms),
            "note": ("a local development surface, not a production metrics "
                     "stack; metrics cannot name an instance — use the trace"),
        }

    def counter_total(self, name: str) -> float:
        with self._lock:
            return sum(c.value for c in self._counters.values() if c.name == name)

    def histogram_count(self, name: str) -> int:
        with self._lock:
            return sum(h.count for h in self._histograms.values() if h.name == name)

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._histograms.clear()

    def render_prometheus(self) -> str:
        """The documented optional text exposition (no vendor dependency)."""
        lines: list[str] = []
        snap = self.snapshot()
        for series in snap["counters"]:
            labels = ",".join(f'{k}="{v}"' for k, v in series["labels"].items())
            lines.append(f'{series["name"]}{{{labels}}} {series["value"]}')
        for series in snap["histograms"]:
            labels = ",".join(f'{k}="{v}"' for k, v in series["labels"].items())
            lines.append(f'{series["name"]}_sum{{{labels}}} {series["sum"]}')
            lines.append(f'{series["name"]}_count{{{labels}}} {series["count"]}')
        return "\n".join(lines)


_REGISTRY = Registry()

_METRICS = ContextVar("cc_ai_metrics", default=None)
_METRICS_ENABLED = os.getenv("TRACE_METRICS", "1").strip().lower() not in {
    "0", "off", "false", "no"}


def metrics_enabled() -> bool:
    override = _METRICS.get()
    return _METRICS_ENABLED if override is None else bool(override)


def set_metrics(on: bool) -> None:
    global _METRICS_ENABLED
    _METRICS_ENABLED = bool(on)


@contextmanager
def metrics(on: bool) -> Iterator[None]:
    token = _METRICS.set(bool(on))
    try:
        yield
    finally:
        _METRICS.reset(token)


def registry() -> Registry:
    return _REGISTRY


def reset() -> None:
    _REGISTRY.reset()


# The span-close increment point. `record_span` and `record_trace` are the two
# hooks; both are fail-open inside `observability._fire_*_hooks`.
def _on_span(span: obs.Span) -> None:
    if metrics_enabled():
        _REGISTRY.record_span(span)


def _on_trace(trace: Any) -> None:
    if metrics_enabled():
        _REGISTRY.record_trace(trace)


obs.register_span_hook(_on_span)
obs.register_trace_hook(_on_trace)


def derive_from_trace(trace: Any) -> dict[str, Any]:
    """Span totals for one trace — what the registry must agree with (D7)."""
    totals = trace.totals()
    return {
        "spans": len(trace.spans),
        "errors": sum(1 for s in trace.spans if s.error),
        "tokens": totals.get("tokens", 0),
        "cost_usd": totals.get("cost_usd", 0.0),
    }
