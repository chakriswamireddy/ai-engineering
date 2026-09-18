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
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ai_os.config import build_gateway_from_env
from ai_os.contract import (
    ProviderError, RateLimitError, InvalidKeyError, BadRequestError, ProviderTimeout,
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
    try:
        completion = gateway.chat(
            req.messages, temperature=req.temperature, max_tokens=req.max_tokens)
    except ProviderError as e:
        return _error_response(e)
    return {
        "message": completion.text,
        "model": completion.model,
        "finish_reason": completion.finish_reason,
        "usage": completion.usage.as_dict(),
        "cost_usd": round(gateway.ledger.cost(completion.model, completion.usage), 8),
        "latency_ms": gateway.last_latency_ms,
        "provider": gateway.last_provider,
        "attempts": gateway.last_attempts,
    }


@app.post("/chat/stream")
async def chat_stream_endpoint(req: ChatRequest) -> StreamingResponse:
    if gateway is None:
        return StreamingResponse(
            iter([f"data: {json.dumps({'error': _build_error})}\n\n"]),
            media_type="text/event-stream")

    def events():
        try:
            for item in gateway.stream(
                    req.messages, temperature=req.temperature, max_tokens=req.max_tokens):
                data = {k: v for k, v in item.items() if v not in (None, {})}
                data["provider"] = gateway.last_provider or ""
                yield f"data: {json.dumps(data)}\n\n"
            yield "data: [DONE]\n\n"
        except ProviderError as e:
            yield f"data: {json.dumps(_error_response(e))}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


@app.get("/ledger")
def ledger_endpoint() -> dict[str, Any]:
    if gateway is None:
        return {"error": _build_error}
    return gateway.ledger.summary()