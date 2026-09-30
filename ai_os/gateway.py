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
from urllib.parse import urlsplit

from ai_os import observability as obs
from ai_os.contract import (
    Completion, Embedding, ProviderError, ProviderTimeout, RateLimitError,
)
from ai_os.ledger import Ledger

log = logging.getLogger("ai_os.gateway")


def _endpoint(provider) -> str:
    """The remote system's *host only* — never a credential or a full URL.

    Day 24: a client span names the far side, and the name must be safe to keep
    forever. `https://api.openai.com/v1` becomes `api.openai.com`.
    """
    base = getattr(provider, "base_url", "") or ""
    try:
        host = urlsplit(base).hostname
    except ValueError:
        host = ""
    return host or getattr(provider, "name", "provider")


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
        self.last_fallback_depth: int = 0
        self.last_error: ProviderError | None = None
        self.last_embeddings: list[Embedding] | None = None
        self.last_embed_provider: str | None = None
        self.last_embed_latency_ms: float = 0.0

    # ------------------------------------------------------------------ chat

    # ------------------------------------------------------ Day 23 tracing
    # The tracer wraps the *real* boundary and never re-runs it. When tracing is
    # off (no active trace, or `AI_TRACING=0`) every method below takes the same
    # path it always did: same retries, same fallback, same ledger rows (O9).

    def _record(self, tr, span_id: str, operation: str, attempt: int,
                prompt_ref, **kw):
        rec = self.ledger.record(
            trace_id=(tr.trace_id if tr is not None else ""),
            span_id=span_id or "", operation=operation, attempt=attempt,
            prompt_ref=prompt_ref, **kw)
        if tr is not None and getattr(tr, "recording", False):
            tr.link(rec)
        return rec

    def _attempt(self, tr, lspan, operation: str, provider, model: str,
                 attempt: int, depth: int, prompt_ref, fn, success_attrs=None):
        """Run one provider call inside a `model.<op>.attempt` span.

        The span covers only the call (not the backoff sleep), so a retry is a
        sibling attempt with its own latency. Returns
        `(value, error, span_id, latency_ms)`; on error `value` is None.
        """
        span = None
        if tr is not None and getattr(tr, "recording", False) and lspan is not None:
            span = tr.start_span(
                obs.KIND_MODEL, f"model.{operation}.attempt", parent=lspan,
                span_kind=obs.SPAN_CLIENT, remote=True, component="gateway",
                remote_endpoint=_endpoint(provider),
                attributes={"operation": operation, "provider": provider.name,
                            "model": model, "attempt": attempt,
                            "fallback_depth": depth,
                            "prompt": obs.prompt_attrs(prompt_ref)})
        t0 = time.perf_counter()
        try:
            value = fn()
        except BaseException as exc:
            latency_ms = (time.perf_counter() - t0) * 1000
            if span is not None:
                span.update(latency_ms=round(latency_ms, 3),
                            error_kind=obs.error_kind_of(exc),
                            retryable=bool(getattr(exc, "retryable", False)))
                obs.end_span(span, status=obs.status_for_error(exc),
                             error=obs.error_fact(exc))
                tr.leave(span)
            return None, exc, "", latency_ms
        latency_ms = (time.perf_counter() - t0) * 1000
        if span is not None:
            if success_attrs is not None:
                span.update(**success_attrs(value))
            span.update(latency_ms=round(latency_ms, 3))
            obs.end_span(span, status=obs.STATUS_OK)
            tr.leave(span)
        return value, None, (span.span_id if span is not None else ""), latency_ms

    def _retry_event(self, lspan, attempt: int, delay: float, err) -> None:
        if lspan is not None:
            lspan.add_event("retry_scheduled", attempt=attempt,
                            delay_s=round(delay, 3), reason=type(err).__name__)

    def chat(self, messages, *, prompt_ref=None, operation: str = "chat",
             **kw) -> Completion:
        tr = obs.current_trace()
        if tr is None or not getattr(tr, "recording", False):
            return self._chat(messages, prompt_ref=prompt_ref, operation=operation,
                              tr=None, lspan=None, kw=kw)
        with tr.span(obs.KIND_MODEL, f"model.{operation}",
                     attributes={"operation": operation,
                                 "prompt": obs.prompt_attrs(prompt_ref)}) as lspan:
            return self._chat(messages, prompt_ref=prompt_ref, operation=operation,
                              tr=tr, lspan=lspan, kw=kw)

    def _chat(self, messages, *, prompt_ref, operation: str, tr, lspan, kw) -> Completion:
        deadline = (time.monotonic() + self.total_budget_s
                    if self.total_budget_s else None)
        last_err: ProviderError | None = None
        attempts_total = 0

        for depth, provider in enumerate(self.providers):      # fallback chain
            for attempt in range(1, self.max_attempts + 1):    # retries
                attempts_total += 1
                if deadline is not None and time.monotonic() >= deadline:
                    err = ProviderTimeout(
                        "gateway", f"total budget of {self.total_budget_s}s exceeded")
                    self._record(tr, "", operation, attempts_total, prompt_ref,
                                 provider=provider.name, model=provider.model,
                                 ok=False, error=f"budget: {last_err or err}")
                    self.last_error = err
                    raise err

                completion, raw_err, span_id, latency_ms = self._attempt(
                    tr, lspan, operation, provider,
                    getattr(provider, "model", ""), attempt, depth, prompt_ref,
                    lambda: provider.chat(messages, **kw),
                    success_attrs=lambda c: {
                        "actual_model": c.model, "finish_reason": c.finish_reason,
                        "input_tokens": c.usage.input_tokens,
                        "output_tokens": c.usage.output_tokens})
                if raw_err is not None:
                    perr = self._normalize(provider, raw_err)
                    if perr is None:
                        raise raw_err
                    last_err = perr
                    self.last_error = perr
                    self._record(tr, span_id, operation, attempts_total, prompt_ref,
                                 provider=provider.name,
                                 model=getattr(provider, "model", ""),
                                 latency_ms=latency_ms, ok=False, error=str(perr))
                    self._log_failure(provider, attempt, perr, latency_ms)
                    if not perr.retryable or attempt == self.max_attempts:
                        break                      # never retry semantic errors
                    delay = _backoff(attempt, perr, self.base_delay, self.max_delay)
                    self._retry_event(lspan, attempt, delay, perr)
                    log.info("retry scheduled", extra={
                        "component": "gateway", "event": "retry_scheduled",
                        "provider": provider.name, "attempt": attempt,
                        "max_attempts": self.max_attempts,
                        "error_kind": obs.error_kind_of(perr),
                        "delay_s": round(delay, 3)})
                    time.sleep(delay)
                    continue
                self._record(tr, span_id, operation, attempts_total, prompt_ref,
                             provider=provider.name, model=completion.model,
                             input_tokens=completion.usage.input_tokens,
                             output_tokens=completion.usage.output_tokens,
                             latency_ms=latency_ms, ok=True)
                self.last_result = completion
                self.last_provider = provider.name
                self.last_latency_ms = latency_ms
                self.last_attempts = attempts_total
                self.last_fallback_depth = depth
                self.last_error = None
                log.info("chat ok", extra={
                    "component": "gateway", "event": "chat_ok",
                    "provider": provider.name, "model": completion.model,
                    "attempt": attempts_total, "latency_ms": round(latency_ms, 1),
                    "status": "ok"})
                return completion
            if deadline is not None and time.monotonic() >= deadline:
                break

        raise last_err or ProviderError("gateway", "no providers configured")

    @staticmethod
    def _normalize(provider, exc) -> ProviderError | None:
        """Fold a raw provider exception into the Day-4 taxonomy (or None when
        it is not a provider failure and must propagate untouched)."""
        if isinstance(exc, ProviderError):
            return exc
        if isinstance(exc, urlerror.URLError):
            return ProviderError(provider.name, f"network error: {exc.reason}",
                                 retryable=True)
        return None

    # ---------------------------------------------------------------- stream

    def chat_with_tools(self, messages, tools, *, prompt_ref=None,
                        operation: str = "chat_with_tools", **kw) -> Completion:
        """Day 11 §4 — `chat()` with one extra argument passed straight through.

        That is the whole feature, and that is the point: a tool-turn chat call
        *is* a chat call, so retries, backoff+jitter, `Retry-After`, ordered
        fallback, the per-request budget, and the per-attempt ledger row all
        behave exactly as they do for any chat. The gateway never validates,
        inspects, or partially forwards `tools` — the provider owns the dialect
        it is written in, which is Day 4's bet paying out its first real
        dividend: tool calling arrives as a pass-through, not a subsystem.
        """
        return self.chat(messages, tools=tools, prompt_ref=prompt_ref,
                         operation=operation, **kw)

    def stream(self, messages, *, prompt_ref=None, operation: str = "stream",
               **kw) -> Iterator[dict[str, Any]]:
        tr = obs.current_trace()
        if tr is None or not getattr(tr, "recording", False):
            yield from self._stream(messages, prompt_ref=prompt_ref,
                                    operation=operation, tr=None, lspan=None, kw=kw)
            return
        with tr.span(obs.KIND_MODEL, f"model.{operation}",
                     attributes={"operation": operation,
                                 "prompt": obs.prompt_attrs(prompt_ref)}) as lspan:
            yield from self._stream(messages, prompt_ref=prompt_ref,
                                    operation=operation, tr=tr, lspan=lspan, kw=kw)

    def _stream(self, messages, *, prompt_ref, operation: str, tr, lspan, kw):
        deadline = (time.monotonic() + self.total_budget_s
                    if self.total_budget_s else None)
        last_err: ProviderError | None = None

        for depth, provider in enumerate(self.providers):
            for attempt in range(1, self.max_attempts + 1):
                if deadline is not None and time.monotonic() >= deadline:
                    err = ProviderTimeout(
                        "gateway", f"total budget of {self.total_budget_s}s exceeded")
                    self._record(tr, "", operation, attempt, prompt_ref,
                                 provider=provider.name,
                                 model=getattr(provider, "model", ""),
                                 ok=False, error=f"budget: {last_err or err}")
                    self.last_error = err
                    raise err

                span = None
                if tr is not None and lspan is not None:
                    span = tr.start_span(
                        obs.KIND_MODEL, f"model.{operation}.attempt", parent=lspan,
                        span_kind=obs.SPAN_CLIENT, remote=True, component="gateway",
                        remote_endpoint=_endpoint(provider),
                        attributes={"operation": operation, "provider": provider.name,
                                    "model": getattr(provider, "model", ""),
                                    "attempt": attempt, "fallback_depth": depth,
                                    "prompt": obs.prompt_attrs(prompt_ref)})
                t0 = time.perf_counter()
                delivered = 0
                ttft_ms: float | None = None
                last_chunk: dict[str, Any] | None = None
                try:
                    for chunk in provider.stream(messages, **kw):
                        if delivered == 0:
                            ttft_ms = (time.perf_counter() - t0) * 1000
                            if span is not None:
                                span.update(ttft_ms=round(ttft_ms, 3))
                        delivered += 1
                        last_chunk = chunk
                        yield chunk
                except BaseException as exc:
                    latency_ms = (time.perf_counter() - t0) * 1000
                    if span is not None:
                        span.update(delivered=delivered, latency_ms=round(latency_ms, 3),
                                    error_kind=obs.error_kind_of(exc),
                                    retryable=bool(getattr(exc, "retryable", False)))
                        obs.end_span(span, status=obs.status_for_error(exc),
                                     error=obs.error_fact(exc))
                        tr.leave(span)
                    perr = self._normalize(provider, exc)
                    if perr is None:
                        raise
                    last_err = perr
                    self.last_error = perr
                    self._record(tr, span.span_id if span is not None else "",
                                 operation, attempt, prompt_ref,
                                 provider=provider.name,
                                 model=getattr(provider, "model", ""),
                                 latency_ms=latency_ms, ok=False, error=str(perr))
                    self._log_failure(provider, attempt, perr, latency_ms)
                    # Any chunks already handed to the caller are theirs; a
                    # mid-stream cut can't be transparently retried.
                    if delivered > 0 or not perr.retryable:
                        raise perr
                    if attempt == self.max_attempts:
                        break            # exhausted this provider -> fallback
                    delay = _backoff(attempt, perr, self.base_delay, self.max_delay)
                    self._retry_event(lspan, attempt, delay, perr)
                    time.sleep(delay)
                else:
                    # clean SSE termination ([DONE] / message_stop) reached
                    latency_ms = (time.perf_counter() - t0) * 1000
                    last_usage = (last_chunk or {}).get("usage") or {}
                    model = (last_chunk or {}).get("model", getattr(provider, "model", ""))
                    if span is not None:
                        span.update(delivered=delivered, latency_ms=round(latency_ms, 3),
                                    actual_model=model,
                                    input_tokens=last_usage.get("input_tokens", 0),
                                    output_tokens=last_usage.get("output_tokens", 0))
                        obs.end_span(span, status=obs.STATUS_OK)
                        tr.leave(span)
                    self._record(tr, span.span_id if span is not None else "",
                                 operation, attempt, prompt_ref,
                                 provider=provider.name, model=model,
                                 input_tokens=last_usage.get("input_tokens", 0),
                                 output_tokens=last_usage.get("output_tokens", 0),
                                 latency_ms=latency_ms, ok=True)
                    self.last_provider = provider.name
                    self.last_latency_ms = latency_ms
                    self.last_fallback_depth = depth
                    return

        raise last_err or ProviderError("gateway", "no providers configured")

    # ---------------------------------------------------------------- embed

    def embed(self, inputs: list[str], *, prompt_ref=None,
              operation: str = "embed") -> list[Embedding]:
        """Day 5 §3 — `Provider.embed()` through the full Day 4 machinery.

        Identical policy to `chat()`: retry transient errors, never retry 401,
        exponential backoff honoring `Retry-After`, ordered fallback to the
        next provider, one `CallRecord` per attempt (success *and* failure).
        Providers without an `embed` capability are skipped in the chain.

        Day 23 wraps each attempt in a `model.embed.attempt` span under the
        request's retrieval/generation parent; the policy is unchanged (O9).
        """
        tr = obs.current_trace()
        if tr is None or not getattr(tr, "recording", False):
            return self._embed(inputs, prompt_ref=prompt_ref,
                               operation=operation, tr=None, lspan=None)
        with tr.span(obs.KIND_MODEL, f"model.{operation}",
                     attributes={"operation": operation,
                                 "prompt": obs.prompt_attrs(prompt_ref)}) as lspan:
            return self._embed(inputs, prompt_ref=prompt_ref,
                               operation=operation, tr=tr, lspan=lspan)

    def _embed(self, inputs, *, prompt_ref, operation, tr, lspan) -> list[Embedding]:
        deadline = (time.monotonic() + self.total_budget_s
                    if self.total_budget_s else None)
        last_err: ProviderError | None = None
        attempts_total = 0

        for depth, provider in enumerate(self.providers):
            embed_call = getattr(provider, "embed", None)
            if embed_call is None:
                continue                     # chat-only provider (e.g. Anthropic)
            for attempt in range(1, self.max_attempts + 1):
                attempts_total += 1
                if deadline is not None and time.monotonic() >= deadline:
                    err = ProviderTimeout(
                        "gateway", f"total budget of {self.total_budget_s}s exceeded")
                    self._record(tr, "", operation, attempts_total, prompt_ref,
                                 provider=provider.name,
                                 model=getattr(provider, "embedding_model", ""),
                                 ok=False, error=f"budget: {last_err or err}")
                    self.last_error = err
                    raise err

                embeddings, raw_err, span_id, latency_ms = self._attempt(
                    tr, lspan, operation, provider,
                    getattr(provider, "embedding_model", ""), attempt, depth,
                    prompt_ref, lambda: embed_call(inputs),
                    success_attrs=lambda embs: {
                        "actual_model": embs[0].model if embs else "",
                        "inputs": len(inputs),
                        "input_tokens": embs[0].usage.input_tokens if embs else 0})
                if raw_err is not None:
                    perr = self._normalize(provider, raw_err)
                    if perr is None:
                        raise raw_err
                    last_err = perr
                    self.last_error = perr
                    self._record(tr, span_id, operation, attempts_total, prompt_ref,
                                 provider=provider.name,
                                 model=getattr(provider, "embedding_model", ""),
                                 latency_ms=latency_ms, ok=False, error=str(perr))
                    self._log_failure(provider, attempt, perr, latency_ms)
                    if not perr.retryable or attempt == self.max_attempts:
                        break                  # 401/400 never retried
                    delay = _backoff(attempt, perr, self.base_delay, self.max_delay)
                    self._retry_event(lspan, attempt, delay, perr)
                    log.info("embed retry scheduled", extra={
                        "component": "gateway", "event": "retry_scheduled",
                        "provider": provider.name, "attempt": attempt,
                        "max_attempts": self.max_attempts,
                        "error_kind": obs.error_kind_of(perr),
                        "delay_s": round(delay, 3)})
                    time.sleep(delay)
                    continue
                usage = embeddings[0].usage if embeddings else None
                self._record(tr, span_id, operation, attempts_total, prompt_ref,
                             provider=provider.name, model=embeddings[0].model,
                             input_tokens=usage.input_tokens if usage else 0,
                             output_tokens=0, latency_ms=latency_ms, ok=True)
                self.last_embeddings = embeddings
                self.last_embed_provider = provider.name
                self.last_embed_latency_ms = latency_ms
                self.last_error = None
                log.info("embed ok", extra={
                    "component": "gateway", "event": "embed_ok",
                    "provider": provider.name, "model": embeddings[0].model,
                    "attempt": attempts_total, "inputs": len(inputs),
                    "latency_ms": round(latency_ms, 1), "status": "ok"})
                return embeddings
            if deadline is not None and time.monotonic() >= deadline:
                break

        raise last_err or ProviderError("gateway", "no provider supports embedding")

    # ------------------------------------------------------------------ misc

    def _log_failure(self, provider, attempt: int, err: ProviderError,
                     latency_ms: float) -> None:
        # Day 24 §8 — the first structured-log citizen: the same facts as before,
        # now on the fixed spine and bound to whatever trace is active.
        log.info("provider attempt failed", extra={
            "component": "gateway", "event": "provider_attempt_failed",
            "provider": provider.name, "attempt": attempt,
            "max_attempts": self.max_attempts,
            "error_kind": obs.error_kind_of(err),
            "retryable": bool(err.retryable),
            "latency_ms": round(latency_ms, 1), "status": "error"})