"""Day 7 §4/§6 — the deterministic out-of-corpus gate.

Naive RAG (`rag.retrieve`) *always* returns K neighbours, whether or not a
good one exists. So Day 6's entire defense against out-of-corpus questions
was a line in the system prompt — "if the context cannot answer, say
`not covered`". That is a *hope, not a check*: refusal is decided by a
generation call that has already been paid for.

`should_refuse` converts "the model should refuse" from a prompt request
into a system decision. It is `MECH` (fully provable without a model):

    refuse ⇔ retrieval is empty, or the best match is below τ.

τ is not guessable — it has to be *calibrated* on the top-1 score separation
between Family A (known answerable) and Family E (known unanswerable). If
those two distributions overlap, no single τ works, and that overlap is
itself the finding. Calibration is `PENDING` until a real run produces both;
until then the gate's arithmetic is what this module owns and tests.

Day 7 ships the gate as a *measurement*, not a feature: the pipeline still
runs as Day 6 built it, and `trace.py` records what the gate *would* have
decided next to what the model actually did. Only Day 21's evaluation flips
the decision into the pipeline.
"""
from __future__ import annotations

from typing import Any


def top_score(results: list[tuple[float, Any]]) -> float | None:
    """The best cosine of a `store.retrieve` list; None on empty retrieval."""
    if not results:
        return None
    return float(results[0][0])


def should_refuse(results: list[tuple[float, Any]], tau: float) -> bool:
    """MECH: refuse (skip generation) because no defensible match exists.

    `results` is a `store.retrieve` list of `(score, chunk)`. `tau` is the
    similarity threshold; a negative τ is a nonsensical calibration and is
    rejected loudly rather than silently refusing everything.
    """
    tau = float(tau)
    if tau < 0.0:
        raise ValueError(f"tau must be >= 0, got {tau}")
    if not results:
        return True
    return results[0][0] < tau


def score_gap(results: list[tuple[float, Any]],
              n: int | None = None) -> float | None:
    """top-1 minus top-K cosine — "was there a real winner?"

    A large gap says the top chunk dominated (plausible winner). A small gap
    (.e.g top1 − top5 ≈ 0) says the query's nearest region is ambiguous —
    the fingerprint of Family C (one vector averaged across senses) and of
    cluster collapse (a whole family of near-identical twins shares the top).
    """
    if not results:
        return None
    top = float(results[0][0])
    k = len(results) if n is None else min(n, len(results))
    floor = float(results[k - 1][0])
    return top - floor