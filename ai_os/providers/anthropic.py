"""Day 4 §3 — AnthropicProvider.

The same `Provider` interface on a *different* wire format, proving the
abstraction earns its keep:
  - auth:        `x-api-key` header + `anthropic-version`
  - system:      top-level parameter, never a message role
  - max_tokens:  required (no default) — the gateway default covers callers
  - usage:       input_tokens / output_tokens
  - non-JSON error bodies are common
Stdlib only, like every provider in this project.
"""
from __future__ import annotations

import json
import time
from typing import Any, Iterator
from urllib import error as urlerror, request

from ai_os.contract import (
    Completion, ProviderError, InvalidKeyError,
    ProviderTimeout, NetworkError,
    usage_from_dict,
)
from ai_os.providers import register, parse_sse_line, raise_for_status, retry_after_from, try_parse_json_bytes


@register
class AnthropicProvider:
    name = "anthropic"
    DEFAULT_BASE_URL = "https://api.anthropic.com/v1"
    DEFAULT_MODEL = "claude-3-5-haiku-latest"
    ANTHROPIC_VERSION = "2023-06-01"

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL,
                 base_url: str = DEFAULT_BASE_URL, timeout_s: float = 60.0):
        if not api_key:
            raise InvalidKeyError(self.name, "no api_key supplied")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_s = float(timeout_s)

    # -- private: wire format lives only here --------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "content-type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": self.ANTHROPIC_VERSION,
            "User-Agent": "ai-layer/0.2 (anthropic provider)",
        }

    def _message_body(self, messages: list[dict[str, str]], *, temperature: float,
                      max_tokens: int, stream: bool) -> dict[str, Any]:
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        rest = [m for m in messages if m["role"] != "system"]
        body: dict[str, Any] = {
            "model": self.model,
            "messages": rest,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if system:
            body["system"] = system
        return body

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = {**payload, "model": self.model,
                "max_tokens": payload.get("max_tokens", 512)}
        req = request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(body).encode(),
            headers=self._headers(),
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout_s) as resp:
                try:
                    return json.load(resp)
                except json.JSONDecodeError as e:
                    raise NetworkError(self.name, f"malformed JSON in 200: {e}") from e
        except urlerror.HTTPError as e:
            parsed = try_parse_json_bytes(e)
            raise_for_status(self.name, e.code, parsed, retry_after_from(e.headers))
            return parsed
        except TimeoutError as e:
            raise ProviderTimeout(self.name, f"timed out after {self.timeout_s}s: {e}") from e
        except urlerror.URLError as e:
            raise NetworkError(self.name, f"network error: {e.reason}") from e

    # -- interface: normalized contract --------------------------------------

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.0,
             max_tokens: int = 512) -> Completion:
        data = self._post("/messages", self._message_body(
            messages, temperature=temperature, max_tokens=max_tokens, stream=False))
        content = data.get("content") or []
        text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
        return Completion(
            text=text,
            model=data.get("model", self.model),
            finish_reason=data.get("stop_reason") or "stop",
            usage=usage_from_dict(data.get("usage") or {}),
            raw=data,
        )

    def stream(self, messages: list[dict[str, str]], *, temperature: float = 0.0,
               max_tokens: int = 512) -> Iterator[dict[str, Any]]:
        body = {**self._message_body(messages, temperature=temperature,
                                     max_tokens=max_tokens, stream=True),
                "max_tokens": max_tokens}
        req = request.Request(
            f"{self.base_url}/messages",
            data=json.dumps(body).encode(),
            headers=self._headers(),
            method="POST",
        )
        t0 = time.perf_counter()
        ttft_emitted = False
        input_tokens = 0
        stop_reason: str | None = None
        seen_stop = False
        try:
            with request.urlopen(req, timeout=self.timeout_s) as resp:
                carry = ""
                for raw in resp:
                    line = raw.decode().strip()
                    if not line:
                        continue
                    payload, carry = parse_sse_line(line, carry)
                    if payload is None:
                        continue
                    try:
                        evt = json.loads(payload)
                    except json.JSONDecodeError as e:
                        raise NetworkError(self.name, f"malformed SSE event: {e}") from e
                    etype = evt.get("type")
                    if etype == "message_start":
                        msg = evt.get("message") or {}
                        input_tokens = ((msg.get("usage") or {}).get("input_tokens", 0)
                                        or (evt.get("usage") or {}).get("input_tokens", 0))
                    elif etype == "content_block_delta":
                        delta = evt.get("delta") or {}
                        item: dict[str, Any] = {
                            "delta": delta.get("text", ""),
                            "finish_reason": None,
                            "usage": {},
                            "model": self.model,
                        }
                        if not ttft_emitted:
                            item["ttft_ms"] = round((time.perf_counter() - t0) * 1000, 1)
                            ttft_emitted = True
                        yield item
                    elif etype == "message_delta":
                        delta = evt.get("delta") or {}
                        stop_reason = delta.get("stop_reason") or stop_reason
                        out = (evt.get("usage") or {}).get("output_tokens", 0)
                        yield {
                            "delta": "",
                            "finish_reason": stop_reason or "stop",
                            "usage": usage_from_dict(
                                {"input_tokens": input_tokens, "output_tokens": out}
                            ).__dict__,
                            "model": self.model,
                        }
                    elif etype == "message_stop":
                        seen_stop = True
                        return
                if not seen_stop:
                    raise NetworkError(self.name, "SSE stream ended before message_stop")
        except TimeoutError as e:
            raise ProviderTimeout(self.name, f"stream timed out after {self.timeout_s}s: {e}") from e
        except urlerror.HTTPError as e:
            parsed = try_parse_json_bytes(e)
            raise_for_status(self.name, e.code, parsed, retry_after_from(e.headers))
        except urlerror.URLError as e:
            raise NetworkError(self.name, f"network error: {e.reason}") from e