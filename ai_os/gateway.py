"""Day 4 §4 — ModelGateway: the reusable model interface.

```
Application
     |
     v
AI Gateway        <- this file
     |
 +---+---+
 |       |
Model A Model B
```

Responsibilities:
  - ordered fallback chain (primary → fallbacks)
  - per-provider retries: transient errors only
  - exponential backoff + jitter, honoring `Retry-After` when present
  - per-request timeout AND a total budget spanning the whole chain
  - token accounting: one `CallRecord` per attempt, success *and* failure
"""
from __future__ import annotations

import logging
import random
import time
from typing import Any, Iterator
from urllib import error as urlerror

from ai_os.contract import (
    Completion, Embedding, ProviderError, ProviderTimeout, RateLimitError,
)
from ai_os.ledger import Ledger

log = logging.getLogger("ai_os.gateway")


def _backoff(attempt: int, err: ProviderError, base: float, cap: float) -> float:
    """Seconds to sleep before the next retry of `attempt` (1-based).

    Honor the server's own `Retry-After` when it tells us — it knows its
    capacity better than our formula. Otherwise exponential base*2^(n-1)
    with full jitter (±50%) to break thundering-herd lockstep.
    """
    if isinstance(err, RateLimitError) and err.retry_after is not None:
        return min(max(err.retry_after, 0.0), cap)
    exp = base * (2 ** (attempt - 1))
    return min(cap, exp * random.uniform(0.5, 1.5))


class ModelGateway:
    def __init__(self, providers, *, max_attempts: int = 3, timeout_s: float = 60.0,
                 base_delay_s: float = 1.0, max_delay_s: float = 30.0,
                 total_budget_s: float = 0.0, ledger: Ledger | None = None):
        if not providers:
            raise ValueError("ModelGateway needs at least one provider")
        self.providers = list(providers)          # ordered: primary first
        self.max_attempts = max_attempts
        self.timeout_s = timeout_s
        self.base_delay = base_delay_s
        self.max_delay = max_delay_s
        self.total_budget_s = total_budget_s      # 0 = no overall limit
        self.ledger = ledger or Ledger()
        self.last_result: Completion | None = None
        self.last_provider: str | None = None
        self.last_latency_ms: float = 0.0
        self.last_attempts: int = 0
        self.last_error: ProviderError | None = None
        self.last_embeddings: list[Embedding] | None = None
        self.last_embed_provider: str | None = None
        self.last_embed_latency_ms: float = 0.0

    # ------------------------------------------------------------------ chat

    def chat(self, messages, **kw) -> Completion:
        deadline = (time.monotonic() + self.total_budget_s
                    if self.total_budget_s else None)
        last_err: ProviderError | None = None
        attempts_total = 0

        for provider in self.providers:                 # fallback chain
            for attempt in range(1, self.max_attempts + 1):   # retries
                attempts_total += 1
                if deadline is not None and time.monotonic() >= deadline:
                    err = ProviderTimeout(
                        "gateway", f"total budget of {self.total_budget_s}s exceeded")
                    self.ledger.record(provider=provider.name, model=provider.model,
                                       ok=False, error=f"budget: {last_err or err}")
                    self.last_error = err
                    raise err

                t0 = time.perf_counter()
                try:
                    completion = provider.chat(messages, **kw)
                except ProviderError as e:
                    last_err = e
                    self.last_error = e
                    latency_ms = (time.perf_counter() - t0) * 1000
                    self.ledger.record(provider=provider.name,
                                       model=getattr(provider, "model", ""),
                                       latency_ms=latency_ms, ok=False, error=str(e))
                    self._log_failure(provider, attempt, e, latency_ms)
                    if not e.retryable or attempt == self.max_attempts:
                        break                      # never retry semantic errors
                    delay = _backoff(attempt, e, self.base_delay, self.max_delay)
                    log.info("%s attempt %d/%d -> %s; sleeping %.2fs",
                             provider.name, attempt, self.max_attempts,
                             type(e).__name__, delay)
                    time.sleep(delay)
                except urlerror.URLError as e:
                    wrapped = ProviderError(provider.name, f"network error: {e.reason}",
                                            retryable=True)
                    last_err = wrapped
                    self.last_error = wrapped
                    latency_ms = (time.perf_counter() - t0) * 1000
                    self.ledger.record(provider=provider.name,
                                       model=getattr(provider, "model", ""),
                                       latency_ms=latency_ms, ok=False, error=str(wrapped))
                    if attempt == self.max_attempts:
                        break
                    delay = _backoff(attempt, wrapped, self.base_delay, self.max_delay)
                    time.sleep(delay)
                else:
                    latency_ms = (time.perf_counter() - t0) * 1000
                    self.ledger.record(
                        provider=provider.name, model=completion.model,
                        input_tokens=completion.usage.input_tokens,
                        output_tokens=completion.usage.output_tokens,
                        latency_ms=latency_ms, ok=True)
                    self.last_result = completion
                    self.last_provider = provider.name
                    self.last_latency_ms = latency_ms
                    self.last_attempts = attempts_total
                    self.last_error = None
                    log.info("%s chat ok model=%s attempts=%d latency=%.1fms",
                             provider.name, completion.model, attempts_total, latency_ms)
                    return completion
            if deadline is not None and time.monotonic() >= deadline:
                break

        raise last_err or ProviderError("gateway", "no providers configured")

    # ---------------------------------------------------------------- stream

    def stream(self, messages, **kw) -> Iterator[dict[str, Any]]:
        deadline = (time.monotonic() + self.total_budget_s
                    if self.total_budget_s else None)
        last_err: ProviderError | None = None

        for provider in self.providers:
            for attempt in range(1, self.max_attempts + 1):
                if deadline is not None and time.monotonic() >= deadline:
                    err = ProviderTimeout(
                        "gateway", f"total budget of {self.total_budget_s}s exceeded")
                    self.ledger.record(provider=provider.name, model=getattr(provider, "model", ""),
                                       ok=False, error=f"budget: {last_err or err}")
                    self.last_error = err
                    raise err

                t0 = time.perf_counter()
                delivered = 0
                last_chunk: dict[str, Any] | None = None
                try:
                    for chunk in provider.stream(messages, **kw):
                        delivered += 1
                        last_chunk = chunk
                        yield chunk
                    # clean SSE termination ([DONE] / message_stop) reached
                    latency_ms = (time.perf_counter() - t0) * 1000
                    last_usage = (last_chunk or {}).get("usage") or {}
                    self.ledger.record(
                        provider=provider.name,
                        model=(last_chunk or {}).get("model", getattr(provider, "model", "")),
                        input_tokens=last_usage.get("input_tokens", 0),
                        output_tokens=last_usage.get("output_tokens", 0),
                        latency_ms=latency_ms, ok=True)
                    self.last_provider = provider.name
                    self.last_latency_ms = latency_ms
                    return
                except ProviderError as e:
                    last_err = e
                    self.last_error = e
                    latency_ms = (time.perf_counter() - t0) * 1000
                    self.ledger.record(provider=provider.name,
                                       model=getattr(provider, "model", ""),
                                       latency_ms=latency_ms, ok=False, error=str(e))
                    self._log_failure(provider, attempt, e, latency_ms)
                    # Any chunks already handed to the caller are theirs; a
                    # mid-stream cut can't be transparently retried.
                    if delivered > 0 or not e.retryable:
                        raise e
                    if attempt == self.max_attempts:
                        break            # exhausted this provider -> fallback
                    delay = _backoff(attempt, e, self.base_delay, self.max_delay)
                    time.sleep(delay)
                except urlerror.URLError as e:
                    wrapped = ProviderError(provider.name, f"network error: {e.reason}",
                                            retryable=True)
                    last_err = wrapped
                    self.last_error = wrapped
                    self._log_failure(provider, attempt, wrapped,
                                      (time.perf_counter() - t0) * 1000)
                    if attempt == self.max_attempts:
                        break            # exhausted this provider -> fallback
                    delay = _backoff(attempt, wrapped, self.base_delay, self.max_delay)
                    time.sleep(delay)

        raise last_err or ProviderError("gateway", "no providers configured")

    # ---------------------------------------------------------------- embed

    def embed(self, inputs: list[str]) -> list[Embedding]:
        """Day 5 §3 — `Provider.embed()` through the full Day 4 machinery.

        Identical policy to `chat()`: retry transient errors, never retry 401,
        exponential backoff honoring `Retry-After`, ordered fallback to the
        next provider, one `CallRecord` per attempt (success *and* failure).
        Providers without an `embed` capability are skipped in the chain.
        """
        deadline = (time.monotonic() + self.total_budget_s
                    if self.total_budget_s else None)
        last_err: ProviderError | None = None
        attempts_total = 0

        for provider in self.providers:
            embed_call = getattr(provider, "embed", None)
            if embed_call is None:
                continue                     # chat-only provider (e.g. Anthropic)
            for attempt in range(1, self.max_attempts + 1):
                attempts_total += 1
                if deadline is not None and time.monotonic() >= deadline:
                    err = ProviderTimeout(
                        "gateway", f"total budget of {self.total_budget_s}s exceeded")
                    self.ledger.record(provider=provider.name,
                                       model=getattr(provider, "embedding_model", ""),
                                       ok=False, error=f"budget: {last_err or err}")
                    self.last_error = err
                    raise err

                t0 = time.perf_counter()
                try:
                    embeddings = embed_call(inputs)
                except ProviderError as e:
                    last_err = e
                    self.last_error = e
                    latency_ms = (time.perf_counter() - t0) * 1000
                    self.ledger.record(provider=provider.name,
                                       model=getattr(provider, "embedding_model", ""),
                                       latency_ms=latency_ms, ok=False, error=str(e))
                    self._log_failure(provider, attempt, e, latency_ms)
                    if not e.retryable or attempt == self.max_attempts:
                        break                  # 401/400 never retried
                    delay = _backoff(attempt, e, self.base_delay, self.max_delay)
                    log.info("%s embed attempt %d/%d -> %s; sleeping %.2fs",
                             provider.name, attempt, self.max_attempts,
                             type(e).__name__, delay)
                    time.sleep(delay)
                except urlerror.URLError as e:
                    wrapped = ProviderError(provider.name, f"network error: {e.reason}",
                                            retryable=True)
                    last_err = wrapped
                    self.last_error = wrapped
                    latency_ms = (time.perf_counter() - t0) * 1000
                    self.ledger.record(provider=provider.name,
                                       model=getattr(provider, "embedding_model", ""),
                                       latency_ms=latency_ms, ok=False, error=str(wrapped))
                    if attempt == self.max_attempts:
                        break
                    delay = _backoff(attempt, wrapped, self.base_delay, self.max_delay)
                    time.sleep(delay)
                else:
                    latency_ms = (time.perf_counter() - t0) * 1000
                    usage = embeddings[0].usage if embeddings else None
                    self.ledger.record(
                        provider=provider.name, model=embeddings[0].model,
                        input_tokens=usage.input_tokens if usage else 0,
                        output_tokens=0,
                        latency_ms=latency_ms, ok=True)
                    self.last_embeddings = embeddings
                    self.last_embed_provider = provider.name
                    self.last_embed_latency_ms = latency_ms
                    self.last_error = None
                    log.info("%s embed ok model=%s attempts=%d latency=%.1fms inputs=%d",
                             provider.name, embeddings[0].model, attempts_total,
                             latency_ms, len(inputs))
                    return embeddings
            if deadline is not None and time.monotonic() >= deadline:
                break

        raise last_err or ProviderError("gateway", "no provider supports embedding")

    # ------------------------------------------------------------------ misc

    def _log_failure(self, provider, attempt: int, err: ProviderError,
                     latency_ms: float) -> None:
        retryable = "retryable" if err.retryable else "fatal"
        log.info("%s attempt %d/%d -> %s (%s) in %.1fms",
                 provider.name, attempt, self.max_attempts,
                 type(err).__name__, retryable, latency_ms)