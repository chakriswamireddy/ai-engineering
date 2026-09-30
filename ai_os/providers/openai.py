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

from ai_os import observability as obs
from ai_os.contract import (
    Completion, Embedding, ProviderError, InvalidKeyError,
    ProviderTimeout, NetworkError, ToolCall, Usage,
    usage_from_dict,
)
from ai_os.providers import register
from ai_os.providers import parse_sse_line, raise_for_status, retry_after_from, try_parse_json_bytes


def _encode_tool_calls(calls: list[Any]) -> list[dict[str, Any]]:
    """Neutral `ToolCall`s (or raw dicts) → OpenAI's assistant `tool_calls`."""
    out: list[dict[str, Any]] = []
    for tc in calls:
        if isinstance(tc, ToolCall):
            out.append({"id": tc.id or "call_0", "type": "function",
                        "function": {"name": tc.name, "arguments": tc.arguments}})
        else:
            out.append(tc)
    return out


def _encode_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The Day-11 half of OpenAI's wire: `tool_calls` and the `tool` role.

    Plain user/system messages pass through untouched; an assistant turn that
    carries tool calls is re-encoded into OpenAI's nested shape, and a `tool`
    result message becomes the matching `role:"tool"` + `tool_call_id` row.
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            out.append({"role": "assistant",
                        "content": m.get("content") or None,
                        "tool_calls": _encode_tool_calls(m["tool_calls"])})
        elif role == "tool":
            msg = {"role": "tool",
                   "tool_call_id": m.get("tool_call_id") or m.get("id") or "call_0",
                   "content": m.get("content") or ""}
            if m.get("name"):
                msg["name"] = m["name"]
            out.append(msg)
        else:
            out.append({"role": role, "content": m.get("content", "")})
    return out


def _tool_calls_from_message(message: dict[str, Any]) -> list[ToolCall]:
    """`message.tool_calls[]` → neutral `ToolCall`s.

    `function.arguments` is a JSON **string** on this wire; a provider that
    already handed us a dict (some OpenAI-compatible endpoints do) is
    re-serialized rather than trusted, so the validator always sees a string.
    """
    out: list[ToolCall] = []
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function") or {}
        args = fn.get("arguments")
        if not isinstance(args, str):
            args = json.dumps(args if args is not None else {})
        out.append(ToolCall(name=fn.get("name") or "", arguments=args,
                            id=tc.get("id") or ""))
    return out


@register
class OpenAIProvider:
    name = "openai"
    DEFAULT_BASE_URL = "https://api.openai.com/v1"
    DEFAULT_MODEL = "gpt-4o-mini"
    DEFAULT_EMBED_MODEL = "text-embedding-3-small"

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL,
                 base_url: str = DEFAULT_BASE_URL, timeout_s: float = 60.0,
                 embedding_model: str = DEFAULT_EMBED_MODEL):
        if not api_key:
            raise InvalidKeyError(self.name, "no api_key supplied")
        self.api_key = api_key
        self.model = model
        self.embedding_model = embedding_model
        self.base_url = base_url.rstrip("/")
        self.timeout_s = float(timeout_s)

    # -- private: wire format lives only here --------------------------------

    def _headers(self) -> dict[str, str]:
        # Day 24 — the outbound edge injects the current trace context. Empty
        # when propagation is off (or no trace is active), so a pre-Day-24
        # request sends exactly the headers it always did (D9).
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": "ai-layer/0.2 (openai provider)",
            **obs.outbound_headers(),
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

    def chat(self, messages: list[dict[str, Any]], *, temperature: float = 0.0,
             max_tokens: int = 512,
             tools: list[dict[str, Any]] | None = None) -> Completion:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": _encode_messages(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if tools:
            # Day 11: the schema list is one opaque body field. The provider
            # does not validate, inspect, or partially forward it.
            body["tools"] = tools
        data = self._post("chat/completions", body)
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        text = message.get("content") or ""
        tool_calls = _tool_calls_from_message(message)
        if not text.strip() and not tool_calls:
            # Day 6's rule, with its Day 11 sibling. A 200 that carries nothing
            # (seen on OpenAI-compatible endpoints under throttling) is still a
            # transient glitch — but a 200 whose `content` is null *because the
            # model asked for a tool* is a success, not a glitch. The two cases
            # are separate rows in the suite so "fixing" one never re-breaks
            # the other; the Day-6 check is extended, never weakened.
            raise ProviderError(self.name, "empty completion (no content returned)",
                                retryable=True)
        return Completion(
            text=text,
            model=data.get("model", self.model),
            finish_reason=choice.get("finish_reason") or "stop",
            usage=usage_from_dict(data.get("usage") or {}),
            raw=data,
            tool_calls=tool_calls or None,
        )

    # -- interface: embeddings (Day 5 §3) -----------------------------------

    def embed(self, inputs: list[str]) -> list[Embedding]:
        """Day 5 §3 — text → vectors, the same wire format the client wraps.

        `POST /v1/embeddings` with `{"model", "input"}` → `{data: [{index,
        object, embedding}], usage}`. `usage.prompt_tokens` normalizes into
        the Day 4 `Usage` shape (output_tokens = 0) so the ledger prices an
        embed call like any input-token bill.
        """
        data = self._post("embeddings", {"model": self.embedding_model, "input": inputs})
        usage = usage_from_dict(data.get("usage") or {})
        # Normalize usage: embeddings report prompt_tokens as the whole batch's
        # input; `usage_from_dict` already maps prompt_tokens → input_tokens.
        rows = [r for r in data.get("data") or [] if isinstance(r.get("embedding"), list)]
        if not rows:
            raise NetworkError(self.name, "embedding response had no data rows")
        # Some OpenAI-compatible endpoints (e.g. Gemini/Palm) omit `index`;
        # fall back to response order rather than crashing on a missing key.
        def _index(r: dict[str, Any] | tuple, fallback: int) -> int:
            idx = r.get("index")
            try:
                return int(idx)
            except (TypeError, ValueError):
                return fallback

        rows = sorted(enumerate(rows), key=lambda ir: _index(ir[1], ir[0]))
        return [
            Embedding(
                text=inputs[i] if i < len(inputs) else inputs[0],
                vector=[float(x) for x in r["embedding"]],
                model=data.get("model", self.embedding_model),
                usage=Usage(usage.input_tokens, 0, usage.total_tokens),
            )
            for i, r in rows
        ]

    def stream(self, messages: list[dict[str, Any]], *, temperature: float = 0.0,
               max_tokens: int = 512,
               tools: list[dict[str, Any]] | None = None) -> Iterator[dict[str, Any]]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": _encode_messages(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if tools:
            # Day 11 — streamed tool-call *deltas* are the `PENDING` margin of
            # the real wire; the field rides along so the request is well-formed.
            body["tools"] = tools
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