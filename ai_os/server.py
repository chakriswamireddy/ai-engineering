"""AI OS Day-4 deliverable: expose the ModelGateway over HTTP.

    $ LLM_PROVIDERS=openai:gpt-4o-mini,anthropic:claude-3-5-haiku-latest \
      OPENAI_API_KEY=... ANTHROPIC_API_KEY=... uvicorn ai_os.server:app

    POST /chat            -> {message, usage, latency_ms, model, finish_reason, provider, attempts}
    POST /chat/stream     -> SSE stream with ttft_ms on the first chunk
    GET  /ledger          -> token/cost accounting for every call
    GET  /health          -> provider chain + knobs

All knobs come from the env (see ai_os.config). Responses use the normalized
`Usage` shape: input_tokens / output_tokens / total_tokens.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from ai_os import observability as obs
from ai_os import identity as identity_mod
from ai_os.config import build_gateway_from_env
from ai_os.contract import (
    ProviderError, RateLimitError, InvalidKeyError, BadRequestError, ProviderTimeout,
)
from ai_os.health import liveness, readiness
from ai_os.rag import (
    RagStore, DimensionMismatch, StoreVersionError,
    code_store_path_from_env, run_rag, store_path_from_env,
)
from ai_os.state import (
    state_store_path_from_env,
    acquire_single_writer_lock,
    SecondWriter,
)

# Day 29 §5.4 — single-writer guard: acquire exclusive lock at boot.
# AI_OS_LOCK_STATE=0 disables the guard (off-switch, default on).
_lock_state = os.getenv("AI_OS_LOCK_STATE", "1").strip().lower() not in {"0", "false", "off", "no"}
_writer_lock = None
if _lock_state:
    state_path = state_store_path_from_env()
    if state_path:
        try:
            _writer_lock = acquire_single_writer_lock(state_path)
        except SecondWriter as e:
            # Refuse to start — the message names the holder and the fix
            raise SystemExit(str(e))

try:
    gateway = build_gateway_from_env()
    _build_error: str | None = None
except Exception as e:  # keep /health informative instead of a hard 500
    gateway = None
    _build_error = str(e)

app = FastAPI(title="AI OS — Day 4 Model Gateway", version="0.2.0")

# WS7 — attach the durable trace sink to the default store at boot, but only
# when `TRACE_SINK` is explicitly configured: importing the server in offline
# suites must not create sink files as a side effect.
if os.getenv("TRACE_SINK", "").strip():
    try:
        from ai_os.trace_sink import get_sink as _get_sink
        _boot_sink = _get_sink()
        if _boot_sink is not None:
            obs.default_store().sink = _boot_sink
    except Exception:                                       # noqa: BLE001
        pass                    # sink is fail-open; the local ring is the fact


# ==================================================== WS1 — request identity
# OIDC JWT → Principal{id, tenant, roles}. The middleware stamps every request
# (`request.state.principal/tenant/roles/auth`) and never refuses — probes and
# public reads stay reachable. `_AUTH_PREFIXES` is the guarded surface:
# anonymous + `ALLOW_ANON=0` is a 401 there, enforced both in the middleware
# (so WS2's `/agent` and `/sql` inherit it by existing under a prefix) and at
# each endpoint (so the guard is directly testable without a running server).
# WS2 (amended) — the guarded surface: everything that spends money, reads
# data, or exposes operational state. The probes (`/health`, `/healthz`,
# `/readyz`) stay open — an LB that cannot read them cannot route. Prefix
# match, so `/chat` covers `/chat/stream` and `/traces` covers `/traces/{id}`.
_AUTH_PREFIXES = (
    "/chat", "/rag", "/ledger",                  # money surface
    "/metrics", "/logs", "/traces", "/audit",    # operational surface
    "/attacks", "/approvals", "/ingest/span",    # WS2's original set
    "/agent", "/sql",
)


@app.middleware("http")
async def _identity_middleware(request: Request, call_next):
    import time as _time
    principal = identity_mod.request_principal(request)
    request.state.principal = principal
    request.state.tenant = principal.tenant
    request.state.roles = list(principal.roles)
    request.state.auth = principal.auth
    if request.url.path.startswith(_AUTH_PREFIXES):
        try:
            identity_mod.require_auth(request, route=request.url.path)
        except identity_mod.AuthError as exc:
            return JSONResponse({"error": exc.reason}, status_code=exc.status)
        # WS9 — per-tenant/principal quota (Redis when REDIS_URL is set,
        # memory fallback otherwise). Disabled by default (RATE_LIMIT=none),
        # so offline suites never see a 429 they did not ask for.
        try:
            from ai_os import rate as _rate
            _qm = _rate.quota_manager()
            if _qm.enabled:
                _decision = _qm.check(principal.tenant, principal.id)
                if not _decision.allowed:
                    return JSONResponse(
                        {"error": "rate limit exceeded", "retry_after_s": round(
                            _decision.retry_after, 2)},
                        status_code=429,
                        headers={"Retry-After": str(max(1, int(_decision.retry_after + 0.5)))})
        except Exception:                                       # noqa: BLE001
            pass                    # quotas are fail-open; never break a request
    # WS7 — per-route latency histogram, fired at response close so metrics
    # and traces agree about the window. Route templates only (never ids).
    _t0 = _time.perf_counter()
    try:
        response = await call_next(request)
        return response
    finally:
        try:
            from ai_os import metrics as _metrics
            route = request.url.path.rsplit("/", 1)[0] if request.url.path.startswith("/approvals/") else request.url.path
            _metrics.observe_route(route,
                                   (_time.perf_counter() - _t0) * 1000.0)
        except Exception:                                   # noqa: BLE001
            pass                # metrics are fail-open; never break a response


def _auth_guard(request: Request | None) -> JSONResponse | None:
    """Endpoint-level guard for the `_AUTH_PREFIXES` surface. Returns a 401
    response for anonymous callers when `ALLOW_ANON=0`, else `None`.

    `request=None` (direct library calls, offline suites) resolves as
    anonymous — identical to a headerless request."""
    if request is None:
        request = _bare_request()
    try:
        identity_mod.require_auth(request, route=request.url.path)
    except identity_mod.AuthError as exc:
        return JSONResponse({"error": exc.reason}, status_code=exc.status)
    return None


def _bare_request() -> Request:
    """A headerless request for direct (non-HTTP) endpoint calls."""
    return Request({"type": "http", "method": "GET", "path": "/",
                    "headers": []})


def _caller_tenant(request: Request | None) -> str | None:
    """The caller's tenant for read-scoping, or `None` for direct library calls.

    Library/offline callers predate tenancy and stay unscoped (pre-WS1
    behavior, byte-for-byte); every HTTP request — anonymous included — is
    scoped to its tenant, so cross-tenant denial holds at the seam real
    traffic crosses."""
    if request is None:
        return None
    tenant = getattr(getattr(request, "state", None), "tenant", None)
    return str(tenant or identity_mod.DEFAULT_TENANT)


def _headers_of(request: Request) -> dict[str, str]:
    """Lowercased inbound headers — what `obs.extract` expects."""
    return {k.lower(): v for k, v in request.headers.items()}


class ChatRequest(BaseModel):
    messages: list[dict[str, str]]
    temperature: float = 0.0
    max_tokens: int = 2048
    stream: bool = False


class RagRequest(BaseModel):
    question: str
    k: int | None = None
    budget_chars: int = 1200
    max_tokens: int = 2048
    corpus: str = "docs"          # "docs" (Day 6) | "code" (Day 10)


def _error_response(err: ProviderError) -> dict[str, Any]:
    status = 502
    if isinstance(err, RateLimitError):
        status = 429
    elif isinstance(err, InvalidKeyError):
        status = 401
    elif isinstance(err, BadRequestError):
        status = 400
    elif isinstance(err, ProviderTimeout):
        status = 504
    return {"error": str(err), "provider": err.provider, "retryable": err.retryable, "status": status}


def _error_json(err: ProviderError,
                headers: dict[str, str] | None = None) -> JSONResponse:
    """`_error_response` with its `status` promoted onto the HTTP status line.

    Without this a provider failure returns `{"status": 502}` under HTTP 200 —
    a client, a load balancer and an uptime check all read that as success."""
    body = _error_response(err)
    return JSONResponse(body, status_code=int(body.get("status", 502)),
                        headers=headers)


def _unavailable(detail: str) -> JSONResponse:
    """A server-side configuration fault: not 200 (it is not success) and not
    4xx (the caller cannot fix it)."""
    return JSONResponse({"error": detail}, status_code=503)


@app.get("/health")
def health() -> dict[str, Any]:
    if gateway is None:
        return {"ok": False, "error": _build_error}
    return {
        "ok": True,
        "providers": [
            {"name": p.name, "model": p.model, "base_url": p.base_url}
            for p in gateway.providers
        ],
        "max_attempts": gateway.max_attempts,
        "timeout_s": gateway.timeout_s,
        "base_delay_s": gateway.base_delay,
        "max_delay_s": gateway.max_delay,
        "total_budget_s": gateway.total_budget_s,
    }


# ====================================================== Day 29 — health probes
# Two additive routes: /healthz (liveness) and /readyz (readiness).
# The old /health stays byte-identical because something in the series may
# depend on it, and deleting an anti-pattern by adding a correct thing is the
# only way to do it without breaking a contract.
#
# server.py:35's import-time gateway build is NOT moved: hoisting it would be
# the right refactor, but it changes when a misconfiguration becomes visible,
# and the day refuses to move a failure's timing inside a day whose deliverable
# is a container. The comment at the seam says exactly that.

@app.get("/healthz")
def healthz() -> dict[str, Any]:
    """Liveness probe — touches nothing external. Returns 200 with empty checks."""
    status, body = liveness()
    return JSONResponse(body, status_code=status)


@app.get("/readyz")
def readyz() -> dict[str, Any]:
    """Readiness probe — checks gateway, store, database, state.

    Returns 200 if all pass, 503 if any fails. Body is a table of named checks.
    """
    # Use the module-level gateway if available, otherwise try to build
    def _gw_builder():
        return gateway
    # Store loader uses the identity-keyed cache (Day 28)
    def store_loader(corpus: str):
        path = code_store_path_from_env() if corpus == "code" else store_path_from_env()
        try:
            from ai_os.rag import load_store_cached
            return load_store_cached(path)
        except Exception:
            return None
    state_path = state_store_path_from_env()
    status, body = readiness(
        gateway_builder=_gw_builder,
        store_loader=store_loader,
        state_path=state_path,
    )
    return JSONResponse(body, status_code=status)


@app.post("/chat")
def chat_endpoint(req: ChatRequest, request: Request):
    if gateway is None:
        return _unavailable(_build_error)
    # Day 24 §7 — the inbound edge. A valid `traceparent` is *continued* (same
    # trace id, fresh span id, remote parent); absent/malformed/all-zero/unknown-
    # version mints a fresh root with the reason recorded. A bad header never
    # fails the request, and it never selects a store, tenant, or permission.
    trace, extract = obs.start_server_trace("chat", _headers_of(request), attributes={
        "route": "/chat", "component": "api", "messages": len(req.messages),
        "input_chars": sum(len(m.get("content", "")) for m in req.messages)})
    headers = obs.response_trace_headers(trace)
    try:
        completion = gateway.chat(
            req.messages, temperature=req.temperature, max_tokens=req.max_tokens)
    except ProviderError as e:
        obs.finish_trace(trace, status=obs.status_for_error(e),
                         error=obs.error_fact(e))
        return _error_json(e, headers)
    obs.add_final_answer_event(trace, ans=completion.text,
                               citations=0, refusal=False)
    obs.finish_trace(trace)
    out = {
        "message": completion.text,
        "model": completion.model,
        "finish_reason": completion.finish_reason,
        "usage": completion.usage.as_dict(),
        "cost_usd": round(gateway.ledger.cost(completion.model, completion.usage), 8),
        "latency_ms": gateway.last_latency_ms,
        "provider": gateway.last_provider,
        "attempts": gateway.last_attempts,
    }
    if getattr(trace, "recording", False):
        out["trace_id"] = trace.trace_id
        out["continuation_reason"] = extract.reason
    return JSONResponse(out, headers=headers)


@app.post("/chat/stream")
async def chat_stream_endpoint(req: ChatRequest, request: Request) -> StreamingResponse:
    if gateway is None:
        return StreamingResponse(
            iter([f"data: {json.dumps({'error': _build_error})}\n\n"]),
            media_type="text/event-stream", status_code=503)

    # Day 23 §8 — the trace id rides in a response *header*, so the SSE chunk
    # bytes are unchanged and TTFT is untouched; the stream's spans (including
    # `ttft_ms`) still land in the trace. Day 24 continues the inbound context
    # and adds the response `traceparent`.
    trace, extract = obs.start_server_trace("chat", _headers_of(request), attributes={
        "route": "/chat/stream", "component": "api", "messages": len(req.messages)})
    headers = obs.response_trace_headers(trace)

    def events():
        with obs.use_trace(trace):
            try:
                for item in gateway.stream(
                        req.messages, temperature=req.temperature,
                        max_tokens=req.max_tokens):
                    data = {k: v for k, v in item.items() if v not in (None, {})}
                    data["provider"] = gateway.last_provider or ""
                    yield f"data: {json.dumps(data)}\n\n"
                yield "data: [DONE]\n\n"
            except ProviderError as e:
                yield f"data: {json.dumps(_error_response(e))}\n\n"
            finally:
                if getattr(trace, "recording", False):
                    trace.add_event("response_sent", ok=True, stream=True)
                obs.finish_trace(trace)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers=headers)


@app.get("/traces/{trace_id}")
def trace_endpoint(trace_id: str):
    """Day 23 §7 — the sanitized trace for one request. 404 on an unknown id.

    WS7 — the durable sink is the fallback: a trace that aged out of the
    256 local ring (or was written before a restart) still resolves, so
    `GET /traces/{id}` works after restart and S6's window query has rows.
    """
    trace = obs.default_store().get(trace_id)
    if trace is None:
        try:
            from ai_os.trace_sink import get_sink
            sink = get_sink()
            doc = sink.get(trace_id) if sink is not None else None
        except Exception:                                   # noqa: BLE001
            doc = None
        if isinstance(doc, dict) and doc.get("trace_id"):
            return doc
        return JSONResponse({"error": f"unknown trace {trace_id}"}, status_code=404)
    return trace.as_dict()


@app.get("/traces")
def traces_index() -> dict[str, Any]:
    return {"count": len(obs.default_store()), "ids": obs.default_store().ids()}


@app.get("/ledger")
def ledger_endpoint():
    if gateway is None:
        return _unavailable(_build_error)
    return gateway.ledger.summary()


def _rag_store(corpus: str = "docs") -> RagStore | None:
    """Day 28 — identity-keyed cache. 262,182,307 bytes, 11,061 chunks,
    one parse per request → zero parses per request after first load.
    Key: sha256(file) + embedding_model + dimension."""
    path = code_store_path_from_env() if corpus == "code" else store_path_from_env()
    try:
        from ai_os.rag import load_store_cached
        return load_store_cached(path)
    except (FileNotFoundError, StoreVersionError, json.JSONDecodeError):
        return None


@app.post("/rag")
def rag_endpoint(req: RagRequest, request: Request):
    """Day 6 §6 — question → embed → retrieve → context → chat → answer.

    `citations` is the retrieved set itself (with scores), so every claim
    the model makes is traceable to a chunk — or it isn't RAG. Retrieval
    and generation keep separate cost lines: they are different bills.

    Day 10 adds `corpus` ("docs" | "code"): a second store behind the same
    endpoint, so a code citation is still `cited ⊆ retrieved`.

    Day 24 §7 — the inbound edge runs first: a valid `traceparent` continues
    the trace, so this request and everything it calls (embed, retrieval,
    provider) share one identity across the wire.
    """
    if gateway is None:
        return _unavailable(_build_error)
    store = _rag_store(req.corpus)
    if store is None:
        which = (code_store_path_from_env() if req.corpus == "code"
                 else store_path_from_env())
        return JSONResponse(
            {"error": f"no RAG store at {which}",
             "hint": "run `python tools/build_rag_store.py` (docs) or "
                     "`python tools/build_code_store.py` (code) first"},
            status_code=503)
    trace, extract = obs.start_server_trace("rag", _headers_of(request), attributes={
        "route": "/rag", "component": "api", "corpus": req.corpus,
        "question_sha256": obs.sha256_text(req.question)})
    headers = obs.response_trace_headers(trace)
    try:
        result = run_rag(gateway, store, req.question, k=req.k,
                         budget_chars=req.budget_chars,
                         max_tokens=req.max_tokens)
    except ProviderError as e:
        obs.finish_trace(trace, status=obs.status_for_error(e), error=obs.error_fact(e))
        return _error_json(e, headers)
    except DimensionMismatch as e:
        obs.finish_trace(trace, status=obs.STATUS_ERROR)
        return JSONResponse({"error": str(e)}, status_code=500, headers=headers)
    except ValueError as e:
        obs.finish_trace(trace, status=obs.STATUS_ERROR)
        return JSONResponse({"error": str(e)}, status_code=400, headers=headers)
    if getattr(trace, "recording", False):
        trace.add_event("response_sent", ok=True)
        result["continuation_reason"] = extract.reason
    obs.finish_trace(trace)
    return JSONResponse(result, headers=headers)


@app.get("/metrics")
def metrics_endpoint(format: str = "json"):
    """Day 24 §8 — the aggregated view (JSON snapshot, optional Prometheus text)."""
    from ai_os import metrics as metrics_mod
    if format == "prometheus":
        return PlainTextResponse(metrics_mod.registry().render_prometheus())
    return metrics_mod.registry().snapshot()


@app.get("/logs")
def logs_endpoint(trace_id: str | None = None, limit: int | None = None):
    """Day 24 §8 — one grep by `trace_id` returns one request's lines."""
    from ai_os import structured_logging as slog
    ring = slog.get_ring()
    if ring is None:
        return {"lines": [],
                "hint": "structured logging not set up (call structured_logging.setup())"}
    return {"lines": ring.lines(trace_id=trace_id, limit=limit)}


@app.get("/attacks")
def attacks_endpoint(trace_id: str | None = None):
    """Day 25 §8 — the injection inspection surface.

    `GET /attacks?trace_id=…` returns the signals and classes the firewall found
    for one request (payloads **hashed** — the metadata-safe default intact), and
    `GET /attacks` returns the bounded local ring. Findings you cannot look up do
    not get tuned, so this is the small, boring surface the day's ladder is tuned
    through — deliberately no bigger than the trace and log rings beside it.
    """
    from ai_os import injection as inj
    rows = inj.attempts(trace_id)
    return {
        "trace_id": trace_id,
        "count": len(rows),
        "ring_size": inj.ring_size(),
        "attempts": rows,
        "note": ("signals carry class + field path + sha256 + offsets; the payload "
                 "is never stored — the metadata-safe default does not relax for "
                 "hostile input"),
    }


@app.get("/audit")
def audit_endpoint(request: Request = None,  # FastAPI injects; None = direct library call
                   principal: str | None = None,
                   tool: str | None = None,
                   decision: str | None = None) -> dict[str, Any]:
    """Day 26 §8 — the decision log, read-only.

    `GET /audit?principal=…&tool=…&decision=deny` returns the recorded decisions
    (allows included), filtered by the three fields that matter. There is nothing
    to leak because no value is ever stored — the entry carries the principal, the
    tool, the declared effect/data class, the decision and its code, an args hash
    + shape, a result shape, and a hash chain. Note the honest limit beside the
    surface: `server.py` had no agent route and no identity before today, so this
    is the first thing in the repo that is meaningfully per-caller — and it is
    per-caller only because someone put a string in a request. That is a Day 29
    fact wearing today's clothes.
    """
    from ai_os import audit as audit_mod
    log = audit_mod.default_audit_log()
    # WS1 — tenant-scoped reads: a caller sees its own tenant's entries only,
    # so `GET /audit?principal=<oidc-sub>` shows the resolved `sub` without
    # leaking another tenant's rows. Pre-WS1 entries carry tenant "default".
    tenant = _caller_tenant(request)
    rows = log.entries(principal=principal, tool=tool, decision=decision,
                       tenant=tenant)
    return {"count": len(rows), "total": len(log), "entries": rows}


@app.get("/audit/verify")
def audit_verify_endpoint() -> dict[str, Any]:
    """Day 26 §8 — `{entries, chain_ok, first_broken_seq}`.

    `entries` and `chain_ok` are reported **separately** on purpose: a chain is
    tamper-evident for edits, reorders, and interior deletions (it breaks and
    names the first bad `seq`) and structurally blind to truncation of its own
    tail — nothing inside the process can notice a file that simply ends. The
    external anchor that closes that gap is Day 29's.
    """
    from ai_os import audit as audit_mod
    return audit_mod.default_audit_log().verify()


# ==================================================== Day 27 §8 — the approvals
# Three facts a reader six months from now needs, recorded **at the registration
# site** rather than in a changelog. Day 27 stated them; WS1 and WS4 amended
# two of them, and the amendments are dated here so the history survives:
#
#   1. [Day 27] this repository had no authentication, no session, no agent
#      route and no `principal` header — `principal` was a caller-supplied
#      string. Day 26 called that *authorization without authentication*.
#      AMENDED by WS1: the identity middleware verifies the JWT and sets
#      `request.state.principal`; the string fallback survives only behind
#      `ALLOW_ANON=1` for offline tests.
#   2. [Day 27] **there was deliberately no decision route.** `POST
#      /approvals/{id}/decide` did not exist: it would have placed the single
#      most valuable call behind the least protected surface. AMENDED by WS4 —
#      see "WS4 — decide + apply" below: the route exists now and re-earned
#      the attribute the absence provided for free (auth required, tenant
#      scoped, actor = authenticated principal, never a body field).
#   3. [Day 27] the owner of the authenticated surface was **Day 29**. Delivered
#      as WS1 (identity) + WS4 (decide/apply); Day 29's `?reveal=1` reading
#      surface remains as documented below.
#
# `?reveal=1` discloses argument **values**; with WS1's identity in front of the
# GET routes it is no longer unauthenticated — the note on the route below states
# which of the two it is at runtime (`ALLOW_ANON`), and the default is masked
# exactly as Day 26's metadata-safe rule requires.

def _approval_desk():
    """The read-only desk, or `None` when approvals are switched off."""
    from ai_os import approval as approval_mod
    if not approval_mod.enabled():
        return None
    return approval_mod.ApprovalStore(approval_mod.state_store_for_approvals())


@app.get("/approvals")
def approvals_endpoint(request: Request = None,  # injected when served; None = library call
                       state: str | None = None,
                       tool: str | None = None,
                       principal: str | None = None, reveal: int = 0
                       ) -> dict[str, Any]:
    """Day 27 §8 — the queue, read-only: `{count, records}` with values masked.

    Nothing here needs a signature to read: the record's tool, state, digest,
    reversibility, signal *keys*, expiry and the parties involved are the facts an
    operator greps at 3 a.m. — and every argument value is replaced by its shape
    unless `?reveal=1` is passed.
    """
    from ai_os import approval as approval_mod
    denied = _auth_guard(request)
    if denied is not None:
        return denied
    desk = _approval_desk()
    if desk is None:
        return {"count": 0, "records": [],
                "note": "APPROVALS=none — the gate is off, so no request was opened"}
    tenant = _caller_tenant(request)
    rows = desk.all_records()
    if tenant is not None:
        rows = [r for r in rows if r.tenant == tenant]
    if state is not None:
        rows = [r for r in rows if r.state == state]
    if tool is not None:
        rows = [r for r in rows if r.tool == tool]
    if principal is not None:
        rows = [r for r in rows
                if principal in (r.requested_by, r.decided_by, r.applied_by)]
    return {"count": len(rows),
            "state": state, "tool": tool, "principal": principal,
            "reveal": bool(reveal),
            "records": [approval_mod.approval_view(r, reveal=bool(reveal))
                        for r in rows],
            "note": ("values masked by default; "
                     + ("?reveal=1 is unauthenticated (ALLOW_ANON=1), which is the "
                        "strongest argument for the identity work, not a feature"
                        if identity_mod.allow_anon()
                        else "?reveal=1 is auth-required and attributed to the "
                             "caller (ALLOW_ANON=0)"))}


@app.get("/approvals/{request_id}")
# `request` trails (not leads) so direct library calls
# (`approval_endpoint("id")`, Day 27 style) keep working with no Request.
def approval_endpoint(request_id: str, reveal: int = 0,
                      request: Request = None):  # injected; None = library call
    """Day 27 §8 — one record, read-only, 404 on an unknown id.

    The full canonical rendering is deliberately *not* served here: this route
    returns the record (values masked unless `reveal=1`), and the human-readable
    rendering with the digest on its first line belongs to the CLI, where the reader
    is the approver rather than an unauthenticated canvas of arbitrary callers.
    """
    from ai_os import approval as approval_mod
    denied = _auth_guard(request)
    if denied is not None:
        return denied
    desk = _approval_desk()
    if desk is None:
        return JSONResponse({"error": "APPROVALS=none — no desk to read"},
                            status_code=404)
    rec = desk.get(request_id)
    if rec is None:
        return JSONResponse({"error": f"unknown approval {request_id}"},
                            status_code=404)
    tenant = _caller_tenant(request)
    if tenant is not None and rec.tenant != tenant:
        return JSONResponse({"error": f"unknown approval {request_id}"},
                            status_code=404)
    return approval_mod.approval_view(rec, reveal=bool(reveal))


# ==================================================== WS4 — decide + apply
# Day 27 deliberately omitted these routes ("the least protected surface");
# WS1's identity earns them back: both require auth, both scope to the
# caller's tenant, and the actor is the authenticated principal — never a
# body field (a caller-supplied decider would be Day 26's confused deputy
# wearing a workflow).

class DecideRequest(BaseModel):
    decision: str                       # approve | reject
    reason: str | None = None
    digest: str | None = None           # optional: what the decider saw


_REFUSAL_STATUS = {
    "approval_duplicate": 409,
    "approval_invalid_state": 409,
    "approval_expired": 410,
    "approval_role_not_permitted": 403,
    "approval_self_approved": 403,
    "approval_digest_mismatch": 409,
    "approval_run_limit": 409,
}


def _refusal_response(request_id: str, exc: Exception) -> JSONResponse:
    code = getattr(exc, "code", None) or "approval_refused"
    status = _REFUSAL_STATUS.get(str(code), 409)
    return JSONResponse({"error": str(exc), "code": code,
                         "request_id": request_id},
                        status_code=status)


def _route_actor(request: Request | None) -> str:
    state_principal = getattr(getattr(request, "state", None), "principal", None)
    pid = getattr(state_principal, "id", None)
    if pid:
        return str(pid)
    return "anonymous"


@app.post("/approvals/{request_id}/decide")
def approval_decide_endpoint(request_id: str, body: DecideRequest,
                             request: Request = None):
    """WS4 — decide an approval request. Auth required, idempotent, attributed.

    Replaying the decided outcome returns the record (200); deciding the other
    way conflicts (409). An optional `digest` lets the decider confirm what
    they reviewed — a mismatch is a 409 before anything is written.
    """
    from ai_os import approval as approval_mod
    denied = _auth_guard(request)
    if denied is not None:
        return denied
    desk = _approval_desk()
    if desk is None:
        return JSONResponse({"error": "APPROVALS=none — no desk to decide"},
                            status_code=404)
    rec = desk.get(request_id)
    if rec is None:
        return JSONResponse({"error": f"unknown approval {request_id}"},
                            status_code=404)
    tenant = _caller_tenant(request)
    if tenant is not None and rec.tenant != tenant:
        return JSONResponse({"error": f"unknown approval {request_id}"},
                            status_code=404)
    want = (body.decision or "").strip().lower()
    if want not in ("approve", "reject"):
        return JSONResponse({"error": "decision must be 'approve' or 'reject'"},
                            status_code=400)
    target = (approval_mod.STATE_APPROVED if want == "approve"
              else approval_mod.STATE_REJECTED)
    if rec.state == target:
        return approval_mod.approval_view(rec, reveal=False)
    if rec.state in approval_mod.DECIDED_STATES:
        return JSONResponse({"error": f"request {request_id} is already "
                                      f"{rec.state}; a decision is final",
                             "code": "approval_duplicate",
                             "request_id": request_id},
                            status_code=409)
    if (body.digest or "").strip() and body.digest.strip() != rec.digest:
        return JSONResponse({"error": "digest mismatch: the reviewed action is "
                                      "not the requested action",
                             "code": "approval_digest_mismatch",
                             "request_id": request_id},
                            status_code=409)
    try:
        decided = desk.decide(request_id, decided_by=_route_actor(request),
                              approve=(want == "approve"),
                              reason=body.reason)
    except approval_mod.ApprovalRefusal as exc:
        # Refusals first: ApprovalRefusal subclasses ValueError, so a plain
        # `except ValueError` would flatten every coded refusal into a 400.
        return _refusal_response(request_id, exc)
    except ValueError as exc:
        return JSONResponse({"error": str(exc), "request_id": request_id},
                            status_code=400)
    except Exception as exc:                                    # noqa: BLE001
        return _refusal_response(request_id, exc)
    return approval_mod.approval_view(decided, reveal=False)


# The only tool the apply route executes: approved SQL through the WS3 path.
_APPLY_TOOL = "run_saleor_sql"
_PK_WHERE_RE = re.compile(r"where\s+id\s*=\s*('([^']*)'|(\d+))",
                          re.IGNORECASE)


def _pk_literal(sql: str) -> str | None:
    """`WHERE id = <literal>` from an approved write, or `None`."""
    match = _PK_WHERE_RE.search(sql or "")
    if not match:
        return None
    if match.group(2) is not None:
        return "'" + match.group(2).replace("'", "''") + "'"
    return str(match.group(3))


def _literal_of(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _reverse_sql(head: str, table: str, pre_rows: list[dict]) -> str | None:
    """The undo for a PK-addressed write from its pre-image, or `None` when
    not derivable (INSERT needs the new row's id — a documented residual)."""
    if not pre_rows:
        return None
    if head == "update":
        sets = ", ".join(f"{col} = {_literal_of(val)}"
                         for col, val in pre_rows[0].items() if col != "id")
        if not sets:
            return None
        where = f"id = {_literal_of(pre_rows[0].get('id'))}"
        return f"UPDATE {table} SET {sets} WHERE {where}"
    if head == "delete":
        cols = ", ".join(pre_rows[0].keys())
        vals = ", ".join(_literal_of(v) for v in pre_rows[0].values())
        return f"INSERT INTO {table} ({cols}) VALUES ({vals})"
    return None


@app.post("/approvals/{request_id}/apply")
def approval_apply_endpoint(request_id: str, request: Request = None):
    """WS4 — apply an approved request: validate → execute → record outcome.

    Idempotent: replaying an applied request returns its stored outcome
    without re-executing (double-application is the failure this guards).
    The mutation runs through `DataAgent.run_write` (WS3) with the approval
    id; a compensation record (`reverse_sql` / saga step) lands on the state
    shelf beside the outcome.
    """
    from ai_os import approval as approval_mod
    from ai_os.data import DataAgent, make_data_store
    from ai_os.sql_safety import load_policy
    denied = _auth_guard(request)
    if denied is not None:
        return denied
    desk = _approval_desk()
    if desk is None:
        return JSONResponse({"error": "APPROVALS=none — no desk to apply"},
                            status_code=404)
    rec = desk.get(request_id)
    if rec is None:
        return JSONResponse({"error": f"unknown approval {request_id}"},
                            status_code=404)
    tenant = _caller_tenant(request)
    if tenant is not None and rec.tenant != tenant:
        return JSONResponse({"error": f"unknown approval {request_id}"},
                            status_code=404)
    actor = _route_actor(request)
    if rec.state == approval_mod.STATE_APPLIED:
        return {"applied": True, "rows_changed": rec.applied_rows_changed,
                "compensated": False, "replayed": True,
                "record": approval_mod.approval_view(rec, reveal=False)}
    if rec.tool != _APPLY_TOOL:
        return JSONResponse({"error": f"tool {rec.tool!r} is not executable via "
                                      "apply (only approved SQL runs here)",
                             "code": "approval_invalid_state",
                             "request_id": request_id},
                            status_code=409)
    sql = rec.args.get("sql") if isinstance(rec.args, dict) else None
    if not isinstance(sql, str) or not sql.strip():
        return JSONResponse({"error": "the approved record carries no SQL to run",
                             "code": "approval_invalid_state",
                             "request_id": request_id},
                            status_code=409)
    try:
        applied = desk.apply(request_id, applied_by=actor)
    except Exception as exc:                                    # noqa: BLE001
        return _refusal_response(request_id, exc)
    policy = load_policy()
    agent = DataAgent(make_data_store(), policy=policy)
    head = (sql.strip().split(None, 1) + [""])[0].lower()
    # The target table comes from the same policy check the write itself must
    # pass — one parse, two uses — so the pre-image can only ever name a table
    # the write is allowed to touch.
    probe = policy.check_write(sql, approval_id=request_id,
                               granted_tables=policy.write_tables_allow)
    target = (probe.shape.tables[0] if probe.ok and probe.shape
              and probe.shape.tables else None)
    pre_rows: list[dict] = []
    pk = _pk_literal(sql)
    if pk is not None and target and head in ("update", "delete"):
        try:
            snap = agent.store.query(f"SELECT * FROM {target} WHERE id = {pk}")
            if snap.ok:
                pre_rows = [dict(zip(snap.columns, row)) for row in snap.rows]
        except Exception:                                       # noqa: BLE001
            pre_rows = []       # pre-image is best-effort; the write decides
    wrote = agent.run_write(sql, principal=actor, approval_id=request_id,
                            granted_tables=policy.write_tables_allow)
    if not wrote.ok:
        desk.record_outcome(request_id, rows_changed=0, recorded_by=actor)
        return JSONResponse({"applied": True, "rows_changed": 0,
                             "compensated": False,
                             "execution_error": wrote.error,
                             "error_kind": wrote.error_kind,
                             "record": approval_mod.approval_view(
                                 desk.get(request_id), reveal=False)},
                            status_code=502)
    desk.record_outcome(request_id, rows_changed=wrote.rows_changed,
                        recorded_by=actor)
    reverse = _reverse_sql(head, target or "", pre_rows)
    try:
        shelf = approval_mod.state_store_for_approvals()
        shelf.put(actor, "compensation", f"compensation/{request_id}", {
            "approval_id": request_id, "tool": applied.tool,
            "digest": applied.digest,
            "forward_sql_sha256": obs.sha256_text(sql),
            "reverse_sql": reverse,
            "reverse_derivable": reverse is not None,
            "rows_changed": wrote.rows_changed, "applied_by": actor,
            "note": ("undo captured from pre-image"
                     if reverse is not None else
                     "reverse not derivable (INSERT needs the new row id)"),
        }, created_by={"run_id": request_id, "step": "compensated",
                       "source": "executor"})
    except Exception:                                           # noqa: BLE001
        pass                    # the outcome above is the fact; the saga step
                                # is the companion, never the gate
    return {"applied": True, "rows_changed": wrote.rows_changed,
            "compensated": False,
            "record": approval_mod.approval_view(desk.get(request_id),
                                                 reveal=False)}


def _ingest_enabled() -> bool:
    return os.getenv("TRACE_INGEST", "0").strip().lower() in {
        "1", "true", "yes", "on"}


@app.post("/ingest/span")
def ingest_span_endpoint(request: Request, payload: dict):
    """Day 24 §6 — the loopback-only span report route, **off by default**.

    Unauthenticated ingest is a Day-26 boundary: enabling this binds a collector
    that accepts writes from anywhere without a credential, so it ships disabled
    and says so rather than pretending a local route is a service.
    """
    denied = _auth_guard(request)
    if denied is not None:
        return denied
    if not _ingest_enabled():
        return JSONResponse(
            {"error": "trace ingest disabled; set TRACE_INGEST=1 to enable "
                      "(unauthenticated ingest is a Day-26 boundary)"},
            status_code=404)
    ok = obs.ingest_payload(payload)
    return JSONResponse({"accepted": ok}, status_code=200 if ok else 400)