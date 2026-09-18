"""Day 4 §3 — OpenAIProvider.

The Day-3 client (`llm_client.py`) generalized: same wire format
(`POST /v1/chat/completions`, Bearer auth, prompt_tokens/completion_tokens),
now speaking the normalized contract and the error taxonomy. Stdlib only.
"""
from __future__ import annotations

import json
import time
from typing import Any, Iterator
from urllib import error as urlerror, request
from urllib.parse import urljoin

from ai_os.contract import (
    Completion, ProviderError, InvalidKeyError,
    ProviderTimeout, NetworkError,
    usage_from_dict,
)
from ai_os.providers import register
from ai_os.providers import parse_sse_line, raise_for_status, retry_after_from, try_parse_json_bytes


@register
class OpenAIProvider:
    name = "openai"
    DEFAULT_BASE_URL = "https://api.openai.com/v1"
    DEFAULT_MODEL = "gpt-4o-mini"

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
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": "ai-layer/0.2 (openai provider)",
        }

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        req = request.Request(
            urljoin(self.base_url + "/", path),
            data=json.dumps(body).encode(),
            headers=self._headers(),
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout_s) as resp:
                try:
                    return json.load(resp)
                except json.JSONDecodeError as e:
                    # 200 with garbage: a transient, retryable parse failure
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
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        data = self._post("chat/completions", body)
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        return Completion(
            text=message.get("content", ""),
            model=data.get("model", self.model),
            finish_reason=choice.get("finish_reason") or "stop",
            usage=usage_from_dict(data.get("usage") or {}),
            raw=data,
        )

    def stream(self, messages: list[dict[str, str]], *, temperature: float = 0.0,
               max_tokens: int = 512) -> Iterator[dict[str, Any]]:
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        req = request.Request(
            urljoin(self.base_url + "/", "chat/completions"),
            data=json.dumps(body).encode(),
            headers=self._headers(),
            method="POST",
        )
        t0 = time.perf_counter()
        ttft_emitted = False
        seen_done = False
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
                    if payload == "[DONE]":
                        seen_done = True
                        return
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError as e:
                        raise NetworkError(self.name, f"malformed SSE chunk: {e}") from e
                    choice = (chunk.get("choices") or [{}])[0]
                    delta = (choice.get("delta") or {}).get("content", "")
                    item: dict[str, Any] = {
                        "delta": delta,
                        "finish_reason": choice.get("finish_reason"),
                        "usage": usage_from_dict(chunk.get("usage") or {}).__dict__,
                        "model": chunk.get("model", self.model),
                    }
                    if not ttft_emitted:
                        item["ttft_ms"] = round((time.perf_counter() - t0) * 1000, 1)
                        ttft_emitted = True
                    yield item
                if not seen_done:
                    raise NetworkError(self.name, "SSE stream ended before [DONE]")
        except TimeoutError as e:
            raise ProviderTimeout(self.name, f"stream timed out after {self.timeout_s}s: {e}") from e
        except urlerror.HTTPError as e:
            parsed = try_parse_json_bytes(e)
            raise_for_status(self.name, e.code, parsed, retry_after_from(e.headers))
        except urlerror.URLError as e:
            raise NetworkError(self.name, f"network error: {e.reason}") from e