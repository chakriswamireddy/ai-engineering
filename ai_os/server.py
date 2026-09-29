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
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
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
def chat_endpoint(req: ChatRequest) -> dict[str, Any]:
    if gateway is None:
        return {"error": _build_error}
    # Day 23 §7 — the route mints the request root; the gateway's attempt spans
    # attach to it. `trace_id` is the one additive field on the response.
    trace = obs.start_trace("chat", attributes={
        "route": "/chat", "messages": len(req.messages),
        "input_chars": sum(len(m.get("content", "")) for m in req.messages)})
    try:
        completion = gateway.chat(
            req.messages, temperature=req.temperature, max_tokens=req.max_tokens)
    except ProviderError as e:
        obs.finish_trace(trace, status=obs.status_for_error(e),
                         error=obs.error_fact(e))
        return _error_response(e)
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
    return out


@app.post("/chat/stream")
async def chat_stream_endpoint(req: ChatRequest) -> StreamingResponse:
    if gateway is None:
        return StreamingResponse(
            iter([f"data: {json.dumps({'error': _build_error})}\n\n"]),
            media_type="text/event-stream")

    # Day 23 §8 — the trace id rides in a response *header*, so the SSE chunk
    # bytes are unchanged and TTFT is untouched; the stream's spans (including
    # `ttft_ms`) still land in the trace.
    trace = obs.start_trace("chat", attributes={
        "route": "/chat/stream", "messages": len(req.messages)})
    headers = {"X-Trace-Id": trace.trace_id} if getattr(trace, "recording", False) else {}

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
def rag_endpoint(req: RagRequest) -> dict[str, Any]:
    """Day 6 §6 — question → embed → retrieve → context → chat → answer.

    `citations` is the retrieved set itself (with scores), so every claim
    the model makes is traceable to a chunk — or it isn't RAG. Retrieval
    and generation keep separate cost lines: they are different bills.

    Day 10 adds `corpus` ("docs" | "code"): a second store behind the same
    endpoint, so a code citation is still `cited ⊆ retrieved`.
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
    try:
        result = run_rag(gateway, store, req.question, k=req.k,
                         budget_chars=req.budget_chars,
                         max_tokens=req.max_tokens)
    except ProviderError as e:
        return _error_response(e)
    except DimensionMismatch as e:
        return {"error": str(e)}
    except ValueError as e:
        return {"error": str(e)}
    return result