"""Day 4 §2 — the Provider interface, the type contract, and shared helpers.

What lives here (the interface): method names, argument names
(`temperature`, `max_tokens`), the error taxonomy, normalized types.

What lives in the implementations: URLs, headers, request JSON shape,
usage-field names, error-body parsing, Retry-After naming.
"""
from __future__ import annotations

import json
from typing import Any, Iterator, Protocol

from ai_os.contract import (
    Completion, Embedding, ProviderError,
    BadRequestError, InvalidKeyError, RateLimitError, ProviderTimeout,
)


class Provider(Protocol):
    name: str

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.0,
             max_tokens: int = 512) -> Completion: ...

    def stream(self, messages: list[dict[str, str]], *, temperature: float = 0.0,
               max_tokens: int = 512) -> Iterator[dict[str, Any]]: ...

    def embed(self, inputs: list[str]) -> list[Embedding]: ...


def parse_sse_line(line: str, carry: str) -> tuple[str | None, str]:
    """Fold one SSE `data:` line into the in-flight JSON payload.

    Payloads that span multiple `data:` lines are accumulated until they
    look complete (JSON ending in `}`). A standalone `[DONE]` marker is
    returned verbatim so the caller can detect a clean stream end.
    """
    if line.startswith("data:"):
        carry += line[len("data:"):].lstrip()
        if carry == "[DONE]" or carry.endswith("}"):
            return carry, ""
        return None, carry
    return None, carry


def try_parse_json_bytes(bytes_io: Any) -> dict[str, Any]:
    try:
        return json.load(bytes_io)
    except Exception:
        return {"error": {"message": "<non-JSON error body>"}}


def retry_after_from(headers: Any) -> float | None:
    if headers is None:
        return None
    raw = headers.get("Retry-After") or headers.get("retry-after")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def raise_for_status(provider: str, status: int, body: Any,
                     retry_after: float | None = None) -> None:
    """Map an HTTP status onto the error taxonomy.

    Transient (retryable): 429, 408, 5xx.
    Semantic (never retried): 401/403 auth, 400/404/422 request bugs.
    """
    if 200 <= status < 300:
        return
    if isinstance(body, list):
        body = body[0] if body else {}
    msg = (
        (body.get("error") or {}).get("message")
        or body.get("message")
        or body.get("error")
        or f"HTTP {status}"
    )
    if status in (401, 403):
        raise InvalidKeyError(provider, f"{status}: {msg}")
    if status == 429:
        raise RateLimitError(provider, f"429: {msg}", retry_after=retry_after)
    if status in (408, 504):
        raise ProviderTimeout(provider, f"{status}: {msg}")
    if status in (400, 404, 422):
        raise BadRequestError(provider, f"{status}: {msg}")
    if 500 <= status <= 599:
        raise ProviderError(provider, f"{status}: {msg}", retryable=True)
    raise ProviderError(provider, f"{status}: {msg}", retryable=False)


PROVIDER_REGISTRY: dict[str, type] = {}


def register(cls: type) -> type:
    PROVIDER_REGISTRY[cls.name] = cls  # type: ignore[attr-defined]
    return cls