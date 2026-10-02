"""WS9 — rate/abuse: Redis quotas per tenant/principal with Retry-After.

Day 28's `capacity.TokenBucket` is the mechanism (in-process, per-bucket);
this module is the *policy*: one bucket per `(tenant, principal)`, a shared
Redis backend when `REDIS_URL` is set (replicas share the quota — otherwise a
per-replica bucket multiplies the allowance by the replica count), and an
in-memory fallback when Redis is unreachable (fail-open for availability,
fail-closed for abuse: the fallback is per-process and documented as such).

Enforcement lives in `server.py` on the guarded surface (`_AUTH_PREFIXES`):
over-limit returns 429 with a `Retry-After` header. `RATE_LIMIT_RPS=0`/unset
(`RATE_LIMIT=none`) disables the middleware — the offline default, so suites
stay green without Redis.

The per-request cost ceiling is the sibling control: `AGENT_BUDGET_USD` kills
the loop with the `budget` termination (`ai_os/agent.py`) before money, not
just rate, is exhausted.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Any

from ai_os.capacity import TokenBucket


def rate_rps_from_env() -> float:
    raw = (os.getenv("RATE_LIMIT_RPS", "") or os.getenv("RATE_LIMIT", "")).strip().lower()
    if raw in ("", "none", "off", "0", "false", "no"):
        return 0.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 0.0


def rate_burst_from_env(rps: float) -> float:
    try:
        return max(1.0, float(os.getenv("RATE_LIMIT_BURST", "") or (2.0 * rps if rps else 1.0)))
    except ValueError:
        return max(1.0, 2.0 * rps if rps else 1.0)


@dataclass
class QuotaDecision:
    allowed: bool
    retry_after: float = 0.0
    backend: str = "memory"


class _MemoryQuotas:
    """Per-(tenant, principal) buckets. One instance per process."""

    def __init__(self, rps: float, burst: float, *, clock: Any = time.time) -> None:
        self.rps = rps
        self.burst = burst
        self.clock = clock
        self._lock = threading.Lock()
        self._buckets: dict[tuple[str, str], TokenBucket] = {}

    def check(self, tenant: str, principal: str) -> QuotaDecision:
        key = (tenant or "default", principal or "anonymous")
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = TokenBucket(rate=self.rps, burst=self.burst, clock=self.clock)
                self._buckets[key] = bucket
            ok, retry_after = bucket.consume(1.0)
        return QuotaDecision(allowed=ok, retry_after=retry_after, backend="memory")


class _RedisQuotas:
    """Sliding-window counter per (tenant, principal) in Redis/Valkey.

    `INCR` + `PEXPIRE` per request: no Lua, no dependency beyond a Redis URL.
    Unreachable Redis raises on first use — the caller (`QuotaManager`)
    catches it once and pins the memory fallback for the process lifetime.
    """

    def __init__(self, url: str, rps: float, burst: float) -> None:
        try:
            import redis  # type: ignore
        except ImportError as exc:
            raise RuntimeError("Redis quotas need the `redis` package") from exc
        self._redis = redis
        self.client = redis.Redis.from_url(url, socket_timeout=2.0)
        self.rps = rps
        self.burst = burst

    def check(self, tenant: str, principal: str) -> QuotaDecision:
        window_ms = max(1, int(1000.0 / self.rps)) if self.rps > 0 else 1000
        key = f"ai_quota:{tenant or 'default'}:{principal or 'anonymous'}"
        count = int(self.client.incr(key))
        if count == 1:
            self.client.pexpire(key, window_ms * int(self.burst))
        allowed = count <= int(self.burst)
        retry_after = 0.0 if allowed else window_ms / 1000.0
        return QuotaDecision(allowed=allowed, retry_after=retry_after, backend="redis")


class QuotaManager:
    """One quota decision per request: Redis when configured, memory otherwise."""

    def __init__(self, *, rps: float | None = None, burst: float | None = None,
                 clock: Any = time.time) -> None:
        self.rps = rate_rps_from_env() if rps is None else max(0.0, float(rps))
        self.burst = rate_burst_from_env(self.rps) if burst is None else burst
        self._memory = _MemoryQuotas(self.rps or 1.0, self.burst, clock=clock)
        self._redis: _RedisQuotas | None = None
        self._redis_dead = False
        url = os.getenv("REDIS_URL", "").strip()
        if url and self.rps > 0:
            try:
                self._redis = _RedisQuotas(url, self.rps, self.burst)
            except RuntimeError:
                self._redis = None  # no driver: memory fallback, documented

    @property
    def enabled(self) -> bool:
        return self.rps > 0

    def check(self, tenant: str, principal: str) -> QuotaDecision:
        if not self.enabled:
            return QuotaDecision(allowed=True, backend="off")
        if self._redis is not None and not self._redis_dead:
            try:
                return self._redis.check(tenant, principal)
            except Exception:                                   # noqa: BLE001
                self._redis_dead = True  # fail-open once, pin the fallback
        return self._memory.check(tenant, principal)


_MANAGER: QuotaManager | None = None


def quota_manager() -> QuotaManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = QuotaManager()
    return _MANAGER


def reset_quota_manager() -> None:
    global _MANAGER
    _MANAGER = None
