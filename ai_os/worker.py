"""Day 24 §7 — the genuinely crossing component: a second process, one trace id.

Every other "distributed" hop in this repo is a call we *initiate* and cannot see
inside (a provider, a database). This module is different: it is a small service
we actually run, so it is instrumented end to end and its spans travel home. It
is what makes "distributed" real rather than notional.

```text
parent process                         worker process (this file)
  request trace ── inject traceparent ──▶ extract → continue (remote parent)
        ▲                                       │  do one job
        └──── report the span back ────────────┘  (same trace id, fresh span id)
```

Two rules it demonstrates:

- **continue, don't restart** — an inbound `traceparent` is adopted; the worker's
  root is a `server` span whose parent is the caller's span in the *other*
  process. A fresh trace id here would be the classic two-healthy-local-traces
  bug.
- **fail-open** — `dispatch()` returns `None` on any failure (worker down, hung,
  or unreachable) and the caller records a **gap**, never a fabricated success.
  A killed worker must leave `complete: false`, not a silent short tree.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping

from ai_os import observability as obs

COMPONENT = "worker"

# A tiny, static "Saleor API" the worker answers from. Deterministic and offline
# so the fixture proves propagation, not a network — the point is the *hop*, and
# a hop whose result is a hash is still a hop.
SALEOR_CATALOG: dict[str, dict[str, Any]] = {
    "1": {"id": "1", "name": "Blue T-Shirt", "slug": "blue-t-shirt", "price": 19.99},
    "2": {"id": "2", "name": "Coffee Mug", "slug": "coffee-mug", "price": 9.50},
    "3": {"id": "3", "name": "Desk Lamp", "slug": "desk-lamp", "price": 24.00},
}


def _run_job(job: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """One piece of work. Pure, offline, deterministic."""
    if job == "saleor_catalog":
        product_id = str(payload.get("id", "1"))
        row = SALEOR_CATALOG.get(product_id)
        return {"operation": "product", "id": product_id,
                "found": row is not None, "fields": len(row or {}),
                "name": (row or {}).get("name")}
    if job == "echo":
        return {"echo": str(payload.get("text", ""))[:64]}
    return {"error": f"unknown job {job!r}"}


def run_job(traceparent: str | None, job: str,
            payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Extract → continue → work → report. The worker's whole reason to exist.

    The returned dict is what `dispatch()` hands back to the parent: the trace id
    it continued, why it continued (or did not), the raw header it received, and
    the serialized span reports to merge.
    """
    payload = dict(payload or {})
    carrier = {obs.TRACEPARENT: traceparent} if traceparent else None
    ctx, reason = obs.TraceContext.parse(traceparent)
    tr = obs.start_trace(f"worker.{job}", attributes={"component": COMPONENT,
                                                      "job": job},
                         store=obs.TraceStore(max_traces=8),
                         parent_context=(ctx if ctx is not None else None),
                         kind=obs.SPAN_SERVER, continuation_reason=reason)
    result: dict[str, Any] = {}
    try:
        with tr.span(obs.KIND_TOOL, "saleor.graphql",
                     attributes={"component": COMPONENT, "operation": job},
                     span_kind=obs.SPAN_CLIENT, remote=True,
                     remote_endpoint="saleor", component=COMPONENT) as span:
            result = _run_job(job, payload)
            span.update(returned_fields=result.get("fields", 0),
                        found=result.get("found"))
            span.add_event("job_done", job=job)
        _emit("job_done", "worker job done", job=job,
              returned_fields=result.get("fields", 0), status="ok")
    finally:
        tr.finish()
    reports = [obs.SpanReport.from_span(s, origin=COMPONENT).as_dict()
               for s in tr.spans]
    return {
        "trace_id": tr.trace_id,
        "continued": ctx is not None,
        "reason": reason,
        "received_traceparent": traceparent,
        "server_span_id": tr.root.span_id if tr.root else None,
        "parent_span_id": (ctx.span_id if ctx is not None else None),
        "reports": reports,
        "logs": _trace_logs(tr.trace_id),
        "result": result,
    }


def _trace_logs(trace_id: str) -> list[dict[str, Any]]:
    """This worker's structured lines for one request (cross-process grep demo)."""
    try:
        from ai_os import structured_logging as slog
        ring = slog.get_ring()
        return ring.lines(trace_id=trace_id) if ring is not None else []
    except Exception:                                      # noqa: BLE001
        return []


def _emit(event: str, msg: str, **attrs: Any) -> None:
    try:
        from ai_os import structured_logging as slog
        slog.emit(COMPONENT, event, msg, **attrs)
    except Exception:                                      # noqa: BLE001
        pass


# ------------------------------------------------------------------- client

def dispatch(job: str, payload: Mapping[str, Any] | None = None, *,
             host: str = "127.0.0.1", port: int = 8710,
             timeout: float = 5.0) -> dict[str, Any] | None:
    """Call the worker, carrying the current context; `None` on any failure.

    Fail-open by contract: the caller must treat `None` as a **missing hop** and
    record a gap. Raising here would make telemetry a dependency with no user.

    The call is wrapped in a `client` span that measures *our* wait and records
    the result shape — the far side's internals stay unobserved (the honest
    client/server split D8 checks is never summed into one number).
    """
    tr = obs.current_trace()
    span = None
    if tr is not None and getattr(tr, "recording", False):
        span = tr.start_span(obs.KIND_TOOL, "worker.job",
                             span_kind=obs.SPAN_CLIENT, remote=True,
                             remote_endpoint=f"{host}:{port}", component="api",
                             attributes={"operation": job})
    body = json.dumps({"job": job, "payload": dict(payload or {})}).encode()
    req = urllib.request.Request(
        f"http://{host}:{port}/job", data=body, method="POST",
        headers={**obs.outbound_headers(), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            out = json.load(resp)
        if span is not None:
            span.update(reported=bool(out.get("reports")))
            obs.end_span(span, status=obs.STATUS_OK)
            tr.leave(span)
        return out
    except Exception as exc:                               # noqa: BLE001
        if span is not None:
            obs.end_span(span, status=obs.STATUS_ERROR,
                         error=obs.error_fact(exc))
            tr.leave(span)
        return None


# ------------------------------------------------------------------- server

class _Handler(BaseHTTPRequestHandler):
    def _json(self, status: int, body: dict[str, Any]) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def do_GET(self):                                      # noqa: N802
        if self.path == "/health":
            self._json(200, {"ok": True, "component": COMPONENT})
        else:
            self.send_error(404)

    def do_POST(self):                                     # noqa: N802
        if self.path.rstrip("/") != "/job":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            req = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            req = {}
        traceparent = self.headers.get(obs.TRACEPARENT) or self.headers.get(
            "Traceparent")
        out = run_job(traceparent, str(req.get("job") or "echo"),
                      req.get("payload") or {})
        self._json(200, out)

    def log_message(self, fmt: str, *args: Any) -> None:
        pass                                               # quiet stub-style logs


def serve(port: int = 8710, *, daemon: bool = True) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    httpd.daemon_threads = daemon
    return httpd


def main() -> None:
    ap = argparse.ArgumentParser(description="Day 24 worker service")
    ap.add_argument("--port", type=int, default=8710)
    args = ap.parse_args()
    try:
        from ai_os import structured_logging as slog
        slog.setup(level="info")
    except Exception:                                      # noqa: BLE001
        pass
    httpd = serve(args.port)
    print(f"worker service: 127.0.0.1:{args.port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
