"""Day 4 §5 — token accounting and cost ledger.

The provider owns *naming* (wire format), the ledger owns *money*. It only
ever sees the normalized `Usage`; unknown models price at $0 by design
(a new model silently costing nothing is a conscious tradeoff over a crash).
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from ai_os.contract import Usage

MODEL_PRICES: dict[str, tuple[float, float]] = {
    # USD per 1M tokens: (input, output)
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "claude-3-5-haiku-latest": (0.80, 4.00),
    "claude-3-5-sonnet-latest": (3.00, 15.00),
    # Day 6 — embeddings bill twice: the corpus embed is a per-token cost
    # (output_tokens = 0), so the ledger prices it like any input token.
    "text-embedding-3-small": (0.02, 0.0),
    "text-embedding-3-large": (0.13, 0.0),
}


def cost_of(model: str, usage: Usage) -> float:
    in_price, out_price = MODEL_PRICES.get(model, (0.0, 0.0))
    return usage.input_tokens / 1e6 * in_price + usage.output_tokens / 1e6 * out_price


@dataclass
class CallRecord:
    ts: float
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: float
    ok: bool
    error: str = ""
    # Day 23 — additive provenance, not accounting. A trace id/span id make the
    # row citable by the request's trace tree; `operation`/`attempt` and the
    # prompt identity let a reader walk from a bill line back to the exact turn.
    # Defaults keep every pre-Day-23 record byte-for-byte unchanged.
    trace_id: str = ""
    span_id: str = ""
    operation: str = ""
    attempt: int = 0
    prompt_name: str = ""
    prompt_version: str = ""
    prompt_sha256: str = ""
    price_status: str = "known"     # `unknown` = model absent from the table


class Ledger:
    """Append-only log of every call — failures included, so retry cost is
    visible instead of mysterious."""

    def __init__(self, prices: dict[str, tuple[float, float]] | None = None):
        self.prices = dict(MODEL_PRICES if prices is None else prices)
        self.records: list[CallRecord] = []

    def cost(self, model: str, usage: Usage) -> float:
        in_price, out_price = self.prices.get(model, (0.0, 0.0))
        return usage.input_tokens / 1e6 * in_price + usage.output_tokens / 1e6 * out_price

    def record(
        self,
        *,
        provider: str,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        latency_ms: float = 0.0,
        ok: bool = True,
        error: str = "",
        trace_id: str = "",
        span_id: str = "",
        operation: str = "",
        attempt: int = 0,
        prompt_ref=None,
    ) -> CallRecord:
        usage = Usage(input_tokens, output_tokens, input_tokens + output_tokens)
        prompt_name = prompt_version = prompt_sha256 = ""
        if prompt_ref is not None:
            prompt_name = getattr(prompt_ref, "name", "") or ""
            prompt_version = getattr(prompt_ref, "version", "") or ""
            prompt_sha256 = getattr(prompt_ref, "sha256", "") or ""
        rec = CallRecord(
            ts=time.time(),
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=self.cost(model, usage) if ok else 0.0,
            latency_ms=round(latency_ms, 1),
            ok=ok,
            error=error,
            trace_id=trace_id,
            span_id=span_id,
            operation=operation,
            attempt=int(attempt),
            prompt_name=prompt_name,
            prompt_version=prompt_version,
            prompt_sha256=prompt_sha256,
            # Day 23 — an unknown model prices at $0 by design; the trace marks
            # it, so `$0.00` is never mistaken for proof of free inference.
            price_status="known" if model in self.prices else "unknown",
        )
        self.records.append(rec)
        return rec

    def total_cost(self) -> float:
        return sum(r.cost_usd for r in self.records)

    def totals_by_model(self) -> dict[str, dict]:
        acc: dict[str, dict] = defaultdict(
            lambda: {"calls": 0, "ok": 0, "tokens": 0, "cost_usd": 0.0}
        )
        for r in self.records:
            acc[r.model]["calls"] += 1
            acc[r.model]["ok"] += 1 if r.ok else 0
            acc[r.model]["tokens"] += r.input_tokens + r.output_tokens
            acc[r.model]["cost_usd"] += r.cost_usd
        return {m: dict(v) for m, v in acc.items()}

    def totals_by_day(self) -> dict[str, dict]:
        acc: dict[str, dict] = defaultdict(
            lambda: {"calls": 0, "ok": 0, "tokens": 0, "cost_usd": 0.0}
        )
        for r in self.records:
            day = datetime.fromtimestamp(r.ts).date().isoformat()
            acc[day]["calls"] += 1
            acc[day]["ok"] += 1 if r.ok else 0
            acc[day]["tokens"] += r.input_tokens + r.output_tokens
            acc[day]["cost_usd"] += r.cost_usd
        return {d: dict(v) for d, v in acc.items()}

    def failures(self) -> list[CallRecord]:
        return [r for r in self.records if not r.ok]

    def summary(self) -> dict:
        return {
            "records": len(self.records),
            "ok": sum(1 for r in self.records if r.ok),
            "failed": len(self.failures()),
            "tokens": sum(r.input_tokens + r.output_tokens for r in self.records),
            "cost_usd": round(self.total_cost(), 8),
            "by_model": self.totals_by_model(),
            "by_day": self.totals_by_day(),
        }