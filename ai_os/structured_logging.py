"""Day 24 §8 — structured logging: one JSON object per line, one identity.

Three views answer three questions, and collapsing them is the mistake:

- **logs** — *what happened at this instant, in this order, with these fields*;
  the right home for rare, high-cardinality, human-readable detail;
- **traces** — *what happened to this one request, causally* (`observability.py`);
- **metrics** — *how many, how slow, how expensive, how often broken* (`metrics.py`).

A log line with no `trace_id` is a receipt. A metric labeled with a `trace_id`
is a cardinality bug. This module makes the first sentence true: it binds ids
from the **same** context variable Day 23 already uses for spans, so a line
lands in the tree without a second plumbing mechanism.

The contract is a fixed spine:

```text
ts · level · logger · component · event · msg
trace_id · span_id · parent_span_id · duration_ms · status · (safe attributes…)
```

No f-string prose a machine must later parse; free text goes in `msg` with the
structured facts beside it. Redaction runs **before** serialization and is the
same rule the trace uses — no authorization headers, API keys, SQL text, row
contents, prompts, document text, or customer records in either view. The
`ai_os.gateway` logger is the first citizen to migrate: it used to emit prose
with no ids at all, and now carries the spine.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from typing import Any, Iterable

from ai_os import observability as obs

# The spine keys, in order. Every emitted line has all of them (absent values are
# `None`, never missing) so a downstream reader can rely on the shape.
SPINE = ("ts", "level", "logger", "component", "event", "msg",
         "trace_id", "span_id", "parent_span_id", "duration_ms", "status")

# Attribute keys never written, by name or by substring — the trace's rule,
# reused verbatim so logs and traces cannot disagree about what is unsafe.
_FORBIDDEN_SUBSTR = ("authorization", "api_key", "apikey", "secret", "password",
                     "bearer", "sql", "prompt", "document", "doc_text", "row",
                     "content", "connection_string", "dsn")

DEFAULT_RING_SIZE = 512
_LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING,
           "error": logging.ERROR, "critical": logging.CRITICAL}


def _enabled() -> bool:
    return os.getenv("TRACE_LOGGING", "1").strip().lower() not in {
        "0", "off", "false", "no"}


def _forbidden(key: Any) -> bool:
    k = str(key).lower()
    if obs._is_secret_key(k):
        return True
    return any(s in k for s in _FORBIDDEN_SUBSTR)


def redact(attrs: dict[str, Any]) -> dict[str, Any]:
    """Drop forbidden keys, bound the rest — the single redaction point."""
    out: dict[str, Any] = {}
    for key, value in (attrs or {}).items():
        if _forbidden(key):
            continue
        out[str(key)] = obs._safe_value(value)
    return out


def _context_ids() -> dict[str, Any]:
    tr = obs.current_trace()
    span = obs.current_span()
    if tr is None:
        return {"trace_id": None, "span_id": None, "parent_span_id": None}
    return {
        "trace_id": tr.trace_id,
        "span_id": getattr(span, "span_id", None),
        "parent_span_id": getattr(span, "parent_span_id", None),
    }


def build_line(record: logging.LogRecord) -> dict[str, Any]:
    """A LogRecord → the fixed-spine JSON object (ids from the live context)."""
    ctx = _context_ids()
    extra = {k: v for k, v in record.__dict__.items()
             if k not in logging.LogRecord("", 0, "", 0, "", (), None).__dict__
             and not k.startswith("_")}
    component = extra.pop("component", getattr(record, "component", None)) or record.name
    event = extra.pop("event", None)
    duration_ms = extra.pop("duration_ms", None)
    status = extra.pop("status", None)
    line: dict[str, Any] = {
        "ts": round(record.created, 6),
        "level": record.levelname.lower(),
        "logger": record.name,
        "component": component,
        "event": event,
        "msg": record.getMessage(),
        "trace_id": ctx["trace_id"],
        "span_id": ctx["span_id"],
        "parent_span_id": ctx["parent_span_id"],
        "duration_ms": duration_ms,
        "status": status,
    }
    line.update(redact(extra))
    return line


class JSONFormatter(logging.Formatter):
    """One JSON object per line, spine first."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        try:
            return json.dumps(build_line(record), separators=(",", ":"),
                              default=str)
        except Exception:                                  # noqa: BLE001
            # A formatter must never raise into the caller's log call.
            return json.dumps({"ts": record.created, "level": "error",
                               "logger": record.name, "event": "format_error",
                               "msg": str(record.msg)[:120]})


class RingHandler(logging.Handler):
    """A bounded in-memory ring of emitted lines — a *local* lookup surface.

    Not log shipping, not a collector: `GET /logs?trace_id=…` needs the last N
    lines in-process, and nothing more. Oldest-first, thread-safe, fail-open.
    """

    def __init__(self, capacity: int = DEFAULT_RING_SIZE, level: int = logging.INFO):
        super().__init__(level=level)
        self.capacity = max(1, int(capacity))
        self._lines: deque[dict[str, Any]] = deque(maxlen=self.capacity)
        self._lock = threading.Lock()
        self.overflow = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = build_line(record)
        except Exception:                                  # noqa: BLE001
            return
        with self._lock:
            if len(self._lines) == self.capacity:
                self.overflow += 1
            self._lines.append(line)

    def lines(self, *, trace_id: str | None = None,
              limit: int | None = None) -> list[dict[str, Any]]:
        with self._lock:
            rows = list(self._lines)
        if trace_id is not None:
            rows = [r for r in rows if r.get("trace_id") == trace_id]
        if limit is not None:
            rows = rows[-limit:]
        return rows

    def clear(self) -> None:
        with self._lock:
            self._lines.clear()
            self.overflow = 0


_RING: RingHandler | None = None
_configured = False


def setup(*, level: str = "info", ring_size: int = DEFAULT_RING_SIZE,
          logger_name: str = "ai_os", stream: bool = True) -> RingHandler:
    """Install the JSON formatter + ring on the `ai_os` logger (idempotent)."""
    global _RING, _configured
    logger = logging.getLogger(logger_name)
    logger.setLevel(_LEVELS.get(level.lower(), logging.INFO))
    if _RING is None:
        _RING = RingHandler(capacity=ring_size,
                            level=_LEVELS.get(level.lower(), logging.INFO))
    if not _configured:
        if stream:
            handler = logging.StreamHandler()
            handler.setFormatter(JSONFormatter())
            logger.addHandler(handler)
        logger.addHandler(_RING)
        logger.propagate = False
        _configured = True
    return _RING


def get_ring() -> RingHandler | None:
    return _RING


def emit(component: str, event: str, msg: str = "", *, level: str = "info",
         logger_name: str = "ai_os", **attrs: Any) -> dict[str, Any]:
    """Emit one structured line and return it (used by fixtures and edges)."""
    line = {
        "ts": round(time.time(), 6), "level": level, "logger": logger_name,
        "component": component, "event": event, "msg": msg,
        **_context_ids(), "duration_ms": attrs.pop("duration_ms", None),
        "status": attrs.pop("status", None),
    }
    line.update(redact(attrs))
    logger = logging.getLogger(logger_name)
    if logger.isEnabledFor(_LEVELS.get(level.lower(), logging.INFO)):
        logger.log(_LEVELS.get(level.lower(), logging.INFO),
                   msg or event, extra={"component": component, "event": event,
                                        **redact(attrs)})
    return line


def scan_for_secrets(lines: Iterable[dict[str, Any]]) -> list[str]:
    """A redaction fixture helper: any forbidden key that slipped through."""
    found: list[str] = []
    for line in lines:
        for key in line:
            if _forbidden(key) and key not in SPINE:
                found.append(str(key))
    return found
