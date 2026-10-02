"""WS6 — semantic eval L2/L3: published thresholds, one owner per check.

L2 is deterministic (0 chats): citation support, groundedness, answer
relevance over the run's own evidence. L3 (LLM-judge) is OFF by default,
calibrated prompts, sampled runs only.

Join rule (scorecard): green L1 + red L2 = `pending`, never `pass`.
A semantic verdict never flips a deterministic row — it can only hold a
pass for human review.
"""
from __future__ import annotations

# Published thresholds (nightly `eval_harness.py --live --sample` must pass
# with these; changing one is a reviewed diff, not a tuning accident).
THRESHOLDS: dict[str, float] = {
    "coverage_min": 1.0,          # every pre-registered claim asserted
    "groundedness_min": 1.0,      # every asserted claim traced to context
    "citation_support_min": 1.0,  # every asserted claim cited by a doc stating it
    "forbidden_max": 0,           # no must_not_assert hit, ever
}

OWNERS: dict[str, str] = {
    "coverage": "generation — a pre-registered claim the answer never asserted",
    "groundedness": "generation — an asserted claim with no shown evidence",
    "citation_support": "semantic judge — the cited doc must support the claim",
    "must_not": "generation — a forbidden assertion (a category error)",
}

# L3: off by default; sampled runs only; calibrated prompt lives in
# semantic_judge.MODEL_CLAIM_PROMPT. Enabling is EVAL_JUDGE=model + --sample.
L3_DEFAULT = "off"
L3_SAMPLE_DEFAULT = 5


def l2_verdict(sem: dict) -> tuple[str, str | None]:
    """`pass`/`fail`/`n/a` for L2 + the owning check. Deterministic."""
    if not sem.get("graded"):
        return "n/a", None
    cov = sem.get("coverage")
    if sem.get("claims_expected") and cov is not None and cov < THRESHOLDS["coverage_min"]:
        return "fail", "coverage"
    if sem.get("groundedness_ok") is False:
        return "fail", "groundedness"
    if sem.get("citation_support_ok") is False:
        return "fail", "citation_support"
    if sem.get("must_not_ok") is False:
        return "fail", "must_not"
    return "pass", None


def join_verdict(l1: str, l2: str) -> str:
    """Scorecard join: green L1 + red L2 = pending, never pass."""
    if l1 == "pass" and l2 == "fail":
        return "pending"
    if l2 == "n/a":
        return l1
    if l1 == "fail" or l2 == "fail":
        return "fail"
    return l1
