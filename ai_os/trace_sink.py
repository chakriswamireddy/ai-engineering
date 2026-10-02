"""WS7 — trace sink, retention, metrics backend seam.

The Day-23 `TraceStore` is an in-process `OrderedDict` (cap 256): a restart
loses every trace, and S6's "why did latency increase" needs a 7-day window
no ring buffer can answer. This module adds the durable sink behind the same
interface:

- `TraceSink` — `put(trace_id, doc) / get(trace_id) / purge_before(cutoff)`.
- `FileSink` — JSONL file (`TRACE_SINK_PATH`, default `data/traces.jsonl`):
  survives restarts, no dependency, the offline default.
- `PostgresSink` — TimescaleDB/Postgres table (`TRACE_SINK_DSN`): the staging
  sink. Requires `psycopg`; without it (or without a DSN) construction raises
  a named error and the caller keeps the file sink — fail-open, never crash.
- `retention_days()` — 30d raw / 90d aggregates policy (`TRACE_RETENTION_DAYS`,
  default 30). `purge_before` is the retention job; the eviction test becomes
  a retention test.

`TRACE_SINK=postgres|file|off` selects the backend (default `file`).
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Protocol


def sink_kind_from_env() -> str:
    return os.getenv("TRACE_SINK", "file").strip().lower() or "file"


def sink_path_from_env() -> str:
    return os.getenv("TRACE_SINK_PATH", "data/traces.jsonl").strip() or "data/traces.jsonl"


def retention_days_from_env() -> int:
    try:
        return max(1, int(os.getenv("TRACE_RETENTION_DAYS", "30").strip() or "30"))
    except ValueError:
        return 30


class TraceSink(Protocol):
    def put(self, trace_id: str, doc: dict[str, Any]) -> None: ...
    def get(self, trace_id: str) -> dict[str, Any] | None: ...
    def purge_before(self, cutoff_epoch: float) -> int: ...


class FileSink:
    """Durable JSONL sink: one line per trace `{trace_id, stored_at, doc}`.

    Survives restarts (the WS7 accept: `GET /traces/{id}` works after
    restart). Bounded only by retention, not by the 256 local cap.
    """

    def __init__(self, path: str | None = None):
        self.path = path or sink_path_from_env()
        self._lock = threading.Lock()
        parent = os.path.dirname(os.path.abspath(self.path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        Path(self.path).touch(exist_ok=True)

    def put(self, trace_id: str, doc: dict[str, Any]) -> None:
        line = json.dumps({"trace_id": trace_id, "stored_at": time.time(),
                           "doc": doc}, default=str)
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def get(self, trace_id: str) -> dict[str, Any] | None:
        found: dict[str, Any] | None = None
        try:
            with open(self.path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if row.get("trace_id") == trace_id:
                        found = row.get("doc")
        except FileNotFoundError:
            return None
        return found

    def purge_before(self, cutoff_epoch: float) -> int:
        removed = 0
        kept: list[str] = []
        try:
            with open(self.path, encoding="utf-8") as fh:
                lines = fh.readlines()
        except FileNotFoundError:
            return 0
        for line in lines:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if float(row.get("stored_at", 0.0)) < cutoff_epoch:
                removed += 1
            else:
                kept.append(line if line.endswith("\n") else line + "\n")
        with self._lock:
            with open(self.path, "w", encoding="utf-8") as fh:
                fh.writelines(kept)
        return removed


class PostgresSink:
    """TimescaleDB/Postgres sink (`TRACE_SINK_DSN`).

    Table `ai_traces(trace_id PK, stored_at, doc JSONB)`. Requires `psycopg`
    (v3); without the driver or DSN the constructor raises a named error so
    the caller keeps the file sink — the sink selection is fail-open.
    """

    DDL = ("CREATE TABLE IF NOT EXISTS ai_traces "
           "(trace_id TEXT PRIMARY KEY, stored_at DOUBLE PRECISION, doc JSONB)")

    def __init__(self, dsn: str | None = None):
        dsn = (dsn or os.getenv("TRACE_SINK_DSN", "").strip())
        if not dsn:
            raise RuntimeError("PostgresSink needs TRACE_SINK_DSN (no DSN configured)")
        try:
            import psycopg  # type: ignore
        except ImportError as exc:
            raise RuntimeError("PostgresSink needs `psycopg` (pip install psycopg[binary])") from exc
        self._psycopg = psycopg
        self.dsn = dsn
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(self.DDL)
            conn.commit()

    def put(self, trace_id: str, doc: dict[str, Any]) -> None:
        with self._psycopg.connect(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO ai_traces(trace_id, stored_at, doc) VALUES (%s, %s, %s) "
                    "ON CONFLICT (trace_id) DO UPDATE SET stored_at=EXCLUDED.stored_at, doc=EXCLUDED.doc",
                    (trace_id, time.time(), json.dumps(doc, default=str)))
            conn.commit()

    def get(self, trace_id: str) -> dict[str, Any] | None:
        with self._psycopg.connect(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT doc FROM ai_traces WHERE trace_id=%s", (trace_id,))
                row = cur.fetchone()
        if not row:
            return None
        doc = row[0]
        return doc if isinstance(doc, dict) else json.loads(doc)

    def purge_before(self, cutoff_epoch: float) -> int:
        with self._psycopg.connect(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM ai_traces WHERE stored_at < %s", (cutoff_epoch,))
                removed = cur.rowcount or 0
            conn.commit()
        return int(removed)


def build_sink_from_env() -> TraceSink | None:
    """`TRACE_SINK=file|postgres|off`. `off`/unknown-without-DSN → None.

    The postgres branch fails open to the file sink on *any* construction
    error — driver missing, DSN wrong, database not up yet. The contract is
    "never crash the app over a sink", and a boot-time `OperationalError`
    (compose can start us before Postgres accepts connections) is exactly the
    case the file sink exists for. The failure is logged, not silent.
    """
    kind = sink_kind_from_env()
    if kind in ("off", "none", "0", "false", "no"):
        return None
    if kind in ("postgres", "postgresql", "timescale", "timescaledb"):
        try:
            return PostgresSink()
        except Exception as exc:                             # noqa: BLE001
            import logging
            logging.getLogger("ai_os.trace_sink").warning(
                "postgres sink unavailable (%s) — falling back to file sink", exc)
            return FileSink()  # fail-open: durable file beats no sink
    return FileSink()


_SINK: Any = None
_SINK_BUILT = False


def get_sink() -> TraceSink | None:
    """Process-wide sink singleton, built once from env (WS7)."""
    global _SINK, _SINK_BUILT
    if not _SINK_BUILT:
        _SINK = build_sink_from_env()
        _SINK_BUILT = True
    return _SINK


def reset_sink() -> None:
    """Test seam: drop the singleton so the next `get_sink` rebuilds."""
    global _SINK, _SINK_BUILT
    _SINK, _SINK_BUILT = None, False


def run_retention(sink: TraceSink | None = None,
                  days: int | None = None) -> dict[str, Any]:
    """The retention job: drop raw traces older than `days` (default 30).

    Aggregates (metrics snapshots) live 90d — they are rollups, not rows, and
    are kept by the metrics backend (Prometheus retention), not here.
    """
    sink = sink if sink is not None else get_sink()
    if sink is None:
        return {"purged": 0, "sink": "off"}
    days = retention_days_from_env() if days is None else max(1, int(days))
    cutoff = time.time() - days * 86400.0
    purged = sink.purge_before(cutoff)
    return {"purged": int(purged), "days": days, "cutoff": cutoff}
