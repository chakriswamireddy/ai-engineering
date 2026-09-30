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
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from ai_os import observability as obs
from ai_os.config import build_gateway_from_env
from ai_os.contract import (
    ProviderError, RateLimitError, InvalidKeyError, BadRequestError, ProviderTimeout,
)
from ai_os.rag import (
    RagStore, DimensionMismatch, StoreVersionError,
    code_store_path_from_env, run_rag, store_path_from_env,
)

try:
    gateway = build_gateway_from_env()
    _build_error: str | None = None
except Exception as e:  # keep /health informative instead of a hard 500
    gateway = None
    _build_error = str(e)

app = FastAPI(title="AI OS — Day 4 Model Gateway", version="0.2.0")


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


@app.post("/chat")
def chat_endpoint(req: ChatRequest, request: Request):
    if gateway is None:
        return {"error": _build_error}
    # Day 24 §7 — the inbound edge. A valid `traceparent` is *continued* (same
    # trace id, fresh span id, remote parent); absent/malformed/all-zero/unknown-
    # version mints a fresh root with the reason recorded. A bad header never
    # fails the request, and it never selects a store, tenant, or permission.
    trace, extract = obs.start_server_trace("chat", _headers_of(request), attributes={
        "route": "/chat", "component": "api", "messages": len(req.messages),
        "input_chars": sum(len(m.get("content", "")) for m in req.messages),
        "continuation_reason": extract.reason})
    headers = obs.response_trace_headers(trace)
    try:
        completion = gateway.chat(
            req.messages, temperature=req.temperature, max_tokens=req.max_tokens)
    except ProviderError as e:
        obs.finish_trace(trace, status=obs.status_for_error(e),
                         error=obs.error_fact(e))
        return JSONResponse(_error_response(e), headers=headers)
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
            media_type="text/event-stream")

    # Day 23 §8 — the trace id rides in a response *header*, so the SSE chunk
    # bytes are unchanged and TTFT is untouched; the stream's spans (including
    # `ttft_ms`) still land in the trace. Day 24 continues the inbound context
    # and adds the response `traceparent`.
    trace, extract = obs.start_server_trace("chat", _headers_of(request), attributes={
        "route": "/chat/stream", "component": "api", "messages": len(req.messages),
        "continuation_reason": extract.reason})
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
    """Day 23 §7 — the sanitized trace for one request. 404 on an unknown id."""
    trace = obs.default_store().get(trace_id)
    if trace is None:
        return JSONResponse({"error": f"unknown trace {trace_id}"}, status_code=404)
    return trace.as_dict()


@app.get("/traces")
def traces_index() -> dict[str, Any]:
    return {"count": len(obs.default_store()), "ids": obs.default_store().ids()}


@app.get("/ledger")
def ledger_endpoint() -> dict[str, Any]:
    if gateway is None:
        return {"error": _build_error}
    return gateway.ledger.summary()


def _rag_store(corpus: str = "docs") -> RagStore | None:
    path = code_store_path_from_env() if corpus == "code" else store_path_from_env()
    try:
        return RagStore.load(path)
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
        return {"error": _build_error}
    store = _rag_store(req.corpus)
    if store is None:
        which = (code_store_path_from_env() if req.corpus == "code"
                 else store_path_from_env())
        return {"error": f"no RAG store at {which}",
                "hint": "run `python tools/build_rag_store.py` (docs) or "
                        "`python tools/build_code_store.py` (code) first"}
    trace, extract = obs.start_server_trace("rag", _headers_of(request), attributes={
        "route": "/rag", "component": "api", "corpus": req.corpus,
        "question_sha256": obs.sha256_text(req.question),
        "continuation_reason": extract.reason})
    headers = obs.response_trace_headers(trace)
    try:
        result = run_rag(gateway, store, req.question, k=req.k,
                         budget_chars=req.budget_chars,
                         max_tokens=req.max_tokens)
    except ProviderError as e:
        obs.finish_trace(trace, status=obs.status_for_error(e), error=obs.error_fact(e))
        return JSONResponse(_error_response(e), headers=headers)
    except DimensionMismatch as e:
        obs.finish_trace(trace, status=obs.STATUS_ERROR)
        return JSONResponse({"error": str(e)}, headers=headers)
    except ValueError as e:
        obs.finish_trace(trace, status=obs.STATUS_ERROR)
        return JSONResponse({"error": str(e)}, headers=headers)
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


def _ingest_enabled() -> bool:
    return os.getenv("TRACE_INGEST", "0").strip().lower() in {
        "1", "true", "yes", "on"}


@app.post("/ingest/span")
def ingest_span_endpoint(payload: dict):
    """Day 24 §6 — the loopback-only span report route, **off by default**.

    Unauthenticated ingest is a Day-26 boundary: enabling this binds a collector
    that accepts writes from anywhere without a credential, so it ships disabled
    and says so rather than pretending a local route is a service.
    """
    if not _ingest_enabled():
        return JSONResponse(
            {"error": "trace ingest disabled; set TRACE_INGEST=1 to enable "
                      "(unauthenticated ingest is a Day-26 boundary)"},
            status_code=404)
    ok = obs.ingest_payload(payload)
    return JSONResponse({"accepted": ok}, status_code=200 if ok else 400)