"""First-principles OpenAI-compatible client — raw HTTP, stdlib only.

Day 3: the AI Gateway -> LLM -> Response loop.
Defaults to the Gemini API OpenAI-compatible endpoint (Google AI Studio),
using a free flash model. Any OpenAI-compatible server works by setting
LLM_BASE_URL (OpenRouter, Ollama, vLLM, ...).
API key comes from the env, never the repo — a local `.env` file is loaded
automatically if present.

    $ python -m ai_os.llm_client "What is Saleor?"
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Iterator
from urllib import error as urlerror, request
from urllib.parse import urljoin


def _load_dotenv(path: str = ".env") -> None:
    """Minimal dotenv loader: KEY=VALUE lines, real env wins, quirks tolerated."""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except FileNotFoundError:
        pass


_load_dotenv()

BASE_URL = os.getenv("LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai")
MODEL = os.getenv("LLM_MODEL", "gemini-3.6-flash")
API_KEY = os.getenv(
    "GEMINI_API_KEY"
) or os.getenv("OPENROUTER_API_KEY") or os.getenv("CMD_API_KEY") or os.getenv("OPENAI_API_KEY", "")
TIMEOUT = float(os.getenv("LLM_TIMEOUT", "60"))


class LLMError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _headers() -> dict[str, str]:
    if not API_KEY:
        raise LLMError(
            "GEMINI_API_KEY (or OPENROUTER_API_KEY) is not set. "
            "Refusing to call the provider unauthenticated."
        )
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}",
        "User-Agent": "ai-layer/0.1 (gemini openai-compat client)",
    }


def _post(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    req = request.Request(
        urljoin(BASE_URL.rstrip("/") + "/", "chat/completions"),
        data=json.dumps(body).encode(),
        headers=_headers(),
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, json.load(resp)
    except urlerror.HTTPError as e:
        return e.code, _try_json(e)
    except TimeoutError as e:
        raise LLMError(f"provider timed out after {TIMEOUT}s: {e}") from e
    except urlerror.URLError as e:
        raise LLMError(f"network error talking to {BASE_URL}: {e.reason}") from e


def _try_json(bytes_io: Any) -> dict[str, Any]:
    try:
        return json.load(bytes_io)
    except Exception:
        return {"error": {"message": "<non-JSON error body>"}}


def _check_status(status: int, data: Any) -> None:
    if 200 <= status < 300:
        return
    if isinstance(data, list):  # Gemini OpenAI-compat returns [{"error": {...}}]
        data = data[0] if data else {}
    msg = (data.get("error") or {}).get("message") or data.get("message") or data.get("error", "")
    if status == 401:
        raise LLMError(f"401: invalid API key — check GEMINI_API_KEY. {msg}", status)
    if status == 429:
        retry_after = data.get("headers", {}).get("Retry-After")
        raise LLMError(f"429: rate limited (Retry-After={retry_after}). {msg}", status)
    if status == 400:
        raise LLMError(f"400: bad request (check model/params). {msg}", status)
    raise LLMError(f"{status}: {msg}", status)


def parse_sse_line(line: str, carry: str) -> tuple[str | None, str]:
    if line.startswith("data:"):
        carry += line[len("data:"):].lstrip()
        if not carry.endswith("}"):
            return None, carry
        return carry, ""
    return None, carry


def chat(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.0,
    max_tokens: int = 2048,
    model: str | None = None,
) -> dict[str, Any]:
    body = {
        "model": model or MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    t0 = time.perf_counter()
    status, data = _post(body)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    _check_status(status, data)

    choice = data["choices"][0]
    return {
        "message": choice["message"]["content"],
        "usage": data.get("usage", {}),
        "latency_ms": round(elapsed_ms, 1),
        "model": data.get("model", model or MODEL),
        "finish_reason": choice.get("finish_reason"),
    }


def stream(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.0,
    max_tokens: int = 2048,
    model: str | None = None,
) -> Iterator[dict[str, Any]]:
    body = {
        "model": model or MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }
    req = request.Request(
        urljoin(BASE_URL.rstrip("/") + "/", "chat/completions"),
        data=json.dumps(body).encode(),
        headers=_headers(),
        method="POST",
    )
    t0 = time.perf_counter()
    ttft_emitted = False
    try:
        with request.urlopen(req, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                raise LLMError(f"stream failed with status {resp.status}", resp.status)
            carry = ""
            for raw in resp:
                line = raw.decode().strip()
                if not line:
                    continue
                payload, carry = parse_sse_line(line, carry)
                if payload is None:
                    continue
                if payload == "[DONE]":
                    return
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                choice = (chunk.get("choices") or [{}])[0]
                delta = (choice.get("delta") or {}).get("content", "")
                item: dict[str, Any] = {
                    "delta": delta,
                    "finish_reason": choice.get("finish_reason"),
                    "usage": chunk.get("usage") or {},
                    "model": chunk.get("model"),
                }
                if not ttft_emitted:
                    item["ttft_ms"] = round((time.perf_counter() - t0) * 1000, 1)
                    ttft_emitted = True
                yield item
    except TimeoutError as e:
        raise LLMError(f"stream timed out after {TIMEOUT}s: {e}") from e
    except urlerror.URLError as e:
        raise LLMError(f"network error talking to {BASE_URL}: {e.reason}") from e


if __name__ == "__main__":
    import sys

    q = sys.argv[1] if len(sys.argv) > 1 else "What is Saleor?"
    t0 = time.perf_counter()
    usage, ttft_ms, model = {}, None, None
    for chunk in stream([{"role": "user", "content": q}]):
        delta = chunk.get("delta", "")
        if delta:
            sys.stdout.write(delta)
            sys.stdout.flush()
        if "ttft_ms" in chunk:
            ttft_ms = chunk["ttft_ms"]
        if chunk.get("usage"):
            usage = chunk["usage"]
        if chunk.get("model"):
            model = chunk["model"]
    elapsed = round((time.perf_counter() - t0) * 1000, 1)
    print()
    print(
        f"\n--- model={model}  ttft={ttft_ms}ms  elapsed={elapsed}ms  "
        f"usage={usage} ---",
        file=sys.stderr,
    )