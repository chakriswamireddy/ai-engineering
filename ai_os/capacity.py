"""Day 28 — the bound registry, admission control, and Day 27 queue helpers.

One place where every bound is a TUPLE: name, on, unit, rate, burst, consequence.
A limit missing `on` or `consequence` is a ValueError naming the bound, because
a limit nobody can be refused by is a comment with a number in it.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from ai_os.approval import ApprovalStore


# -----------------------------------------------------------------------------
# The bound record — five required parts, no defaults, no partials.
# -----------------------------------------------------------------------------

CONSEQUENCE_CODES = frozenset({
    "rate_limited",
    "budget_exhausted",
    "queue_depth_exceeded",
    "server_busy",
})


@dataclass(frozen=True)
class Bound:
    name: str
    on: str
    unit: str
    rate: float
    burst: float
    consequence: str

    def __post_init__(self) -> None:
        if not self.on:
            raise ValueError(f"bound {self.name!r}: missing required field 'on'")
        if not self.consequence:
            raise ValueError(f"bound {self.name!r}: missing required field 'consequence'")
        if self.consequence not in CONSEQUENCE_CODES:
            raise ValueError(
                f"bound {self.name!r}: unknown consequence {self.consequence!r} "
                f"(known: {sorted(CONSEQUENCE_CODES)})"
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "on": self.on,
            "unit": self.unit,
            "rate": self.rate,
            "burst": self.burst,
            "consequence": self.consequence,
        }


# -----------------------------------------------------------------------------
# Token bucket — pure arithmetic over an injected clock.
# No thread, no lock, no async. A limiter that needs a lock depends on a
# deployment detail, and the diagram's Plate II would then have to draw it.
# -----------------------------------------------------------------------------

@dataclass
class TokenBucket:
    rate: float
    burst: float
    clock: Any = time.time
    _tokens: float = 0.0
    _last: float = 0.0

    def __post_init__(self) -> None:
        self._tokens = float(self.burst)
        self._last = float(self.clock())

    @property
    def tokens(self) -> float:
        return self._refill()[0]

    @property
    def retry_after(self) -> float:
        """Seconds until one token is available (0 if tokens >= 1)."""
        tokens, _ = self._refill()
        if tokens >= 1.0:
            return 0.0
        return (1.0 - tokens) / self.rate if self.rate > 0 else float("inf")

    def _refill(self) -> tuple[float, float]:
        now = self.clock()
        elapsed = max(0.0, now - self._last)
        self._tokens = min(self.burst, self._tokens + self.rate * elapsed)
        self._last = now
        return self._tokens, now

    def consume(self, cost: float = 1.0) -> tuple[bool, float]:
        """Try to consume `cost` tokens.
        Returns (ok, retry_after). On refusal, tokens are NOT debited.
        """
        tokens, _ = self._refill()
        if tokens >= cost:
            self._tokens = tokens - cost
            return True, 0.0
        # Refusal debits nothing — a partial debit would make a refusal reduce
        # the caller's own budget, rewarding repeated attempts.
        retry_after = (cost - tokens) / self.rate if self.rate > 0 else float("inf")
        return False, retry_after


# -----------------------------------------------------------------------------
# Admission decision — same shape as Day 26's Decision (allow, code, detail).
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class Decision:
    allow: bool
    code: str | None
    detail: dict[str, Any]

    @staticmethod
    def allowed() -> "Decision":
        return Decision(True, None, {})

    @staticmethod
    def refused(bound: Bound, retry_after: float, **extra: Any) -> "Decision":
        return Decision(
            False,
            bound.consequence,
            {"bound": bound.name, "retry_after": round(retry_after, 3), **extra},
        )


# -----------------------------------------------------------------------------
# Admission controller — the four questions in order: resolve, refill, consume,
# decide. A limit applied after the work is an audit, not a limit.
# -----------------------------------------------------------------------------

class AdmissionController:
    def __init__(self, bounds: list[Bound], clock: Any = time.time):
        self.bounds = {b.name: b for b in bounds}
        self.buckets: dict[str, TokenBucket] = {}
        self.clock = clock

    def _bucket(self, bound: Bound, key: str) -> TokenBucket:
        bucket_key = f"{bound.name}:{key}"
        if bucket_key not in self.buckets:
            self.buckets[bucket_key] = TokenBucket(bound.rate, bound.burst, self.clock)
        return self.buckets[bucket_key]

    def check(
        self,
        principal: str,
        bound_name: str,
        cost: float = 1.0,
    ) -> Decision:
        bound = self.bounds.get(bound_name)
        if bound is None:
            raise ValueError(f"unknown bound {bound_name!r}")

        bucket = self._bucket(bound, principal)
        ok, retry_after = bucket.consume(cost)
        if ok:
            return Decision.allowed()
        return Decision.refused(bound, retry_after)


# -----------------------------------------------------------------------------
# Day 27 queue helpers — depth and lease for approval records.
# -----------------------------------------------------------------------------

def queue_depth(store: ApprovalStore, principal: str) -> int:
    """Count `requested` records for a principal."""
    return sum(1 for r in store.list_for(principal) if r.state == "requested")


def lease(
    store: ApprovalStore,
    request_id: str,
    consumer: str,
    ttl: float,
) -> bool:
    """Write a lease onto an approval record. Refused if another consumer
    holds an unexpired lease. Returns True on success, False on conflict.
    """
    rec = store.get(request_id)
    if rec is None:
        return False
    now = time.time()
    existing = getattr(rec, "lease", None)
    if existing:
        holder = existing.get("consumer")
        expires = existing.get("expires_at", 0.0)
        if holder != consumer and expires > now:
            return False
    rec.lease = {"consumer": consumer, "expires_at": now + ttl}
    store.put(rec)
    return True


# -----------------------------------------------------------------------------
# Config loading — capacity.yaml beside policy.yaml and permissions.yaml.
# -----------------------------------------------------------------------------

def load_bounds(path: str = "capacity.yaml") -> list[Bound]:
    import yaml
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    bounds_data = data.get("bounds", {})
    return [Bound(name=name, **spec) for name, spec in bounds_data.items()]


def build_controller_from_env(clock: Any = time.time) -> AdmissionController | None:
    """Build an AdmissionController from CAPACITY_PATH, or return None if
    RATE_LIMIT=none (the N9 off-switch).
    """
    import os
    if os.getenv("RATE_LIMIT", "").strip().lower() in {"none", "0", "off", "false", "no"}:
        return None
    path = os.getenv("CAPACITY_PATH", "capacity.yaml").strip() or "capacity.yaml"
    bounds = load_bounds(path)
    return AdmissionController(bounds, clock)