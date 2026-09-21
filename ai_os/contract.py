"""Day 4 §2 — the normalized contract between the AI OS and any provider.

Providers translate their *wire formats* into these types, and errors into
this taxonomy, so nothing above a provider ever sees a provider-specific
field name. That is what makes "add a third provider" a new class in a
registry instead of new application code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Usage:
    input_tokens: int
    output_tokens: int
    total_tokens: int

    def as_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
        }


def usage_from_dict(u: dict[str, Any]) -> Usage:
    """Normalize any provider's usage dict into the shared `Usage`.

    OpenAI names them prompt_tokens/completion_tokens; Anthropic names them
    input_tokens/output_tokens. The ledger only ever sees this shape.
    """
    inp = u.get("input_tokens")
    if inp is None:
        inp = u.get("prompt_tokens", 0)
    out = u.get("output_tokens")
    if out is None:
        out = u.get("completion_tokens", 0)
    total = u.get("total_tokens")
    if total is None:
        total = int(inp) + int(out)
    return Usage(input_tokens=int(inp), output_tokens=int(out), total_tokens=int(total))


@dataclass
class Completion:
    text: str
    model: str
    finish_reason: str
    usage: Usage
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Embedding:
    """Day 5 §3 — one text → one vector, reusing the Day 4 `Usage`.

    `usage` is the *request-level* accounting (embeddings bill per token of
    the whole batch); it is repeated on each member so callers never have to
    guess where the receipt lives.
    """

    text: str
    vector: list[float]
    model: str
    usage: Usage

    def __post_init__(self) -> None:
        self.vector = [float(x) for x in self.vector]


class ProviderError(Exception):
    """Base of the error taxonomy. `retryable` drives the gateway's retry
    policy — only transient errors (an identical retry can succeed) get one.
    """

    def __init__(self, provider: str, message: str, *, retryable: bool = False):
        super().__init__(f"[{provider}] {message}")
        self.provider = provider
        self.retryable = retryable


class RateLimitError(ProviderError):
    def __init__(self, provider: str, message: str = "rate limited",
                 *, retry_after: float | None = None, **kw):
        super().__init__(provider, message, retryable=True, **kw)
        self.retry_after = retry_after


class ProviderTimeout(ProviderError):
    def __init__(self, provider: str, message: str = "provider timed out", **kw):
        super().__init__(provider, message, retryable=True, **kw)


class NetworkError(ProviderError):
    def __init__(self, provider: str, message: str = "network error", **kw):
        super().__init__(provider, message, retryable=True, **kw)


class InvalidKeyError(ProviderError):
    def __init__(self, provider: str, message: str = "invalid or missing API key", **kw):
        super().__init__(provider, message, retryable=False, **kw)


class BadRequestError(ProviderError):
    def __init__(self, provider: str, message: str = "bad request", **kw):
        super().__init__(provider, message, retryable=False, **kw)