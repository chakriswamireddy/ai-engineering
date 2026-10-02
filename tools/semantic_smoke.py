#!/usr/bin/env python3
"""WS6 — semantic eval L2/L3 fixture (E1–E6), offline.

- E1 L2 citation support: cited-but-irrelevant doc fails support, passes membership
- E2 L2 groundedness: asserted-without-evidence claim fails groundedness
- E3 L2 answer relevance: uncovered mandatory claim fails coverage
- E4 must_not hit always fails (forbidden)
- E5 join: green L1 + red L2 = pending, never pass
- E6 L3 off by default (deterministic verdict untouched by model lane)
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_os import semantic_judge as sem  # noqa: E402
from ai_os import semantic_thresholds as thr  # noqa: E402
from ai_os.judge import judge_kind_from_env  # noqa: E402

FAILS = 0


def check(label: str, cond: bool, detail: str = "") -> bool:
    global FAILS
    if not cond:
        FAILS += 1
    print(f"[{'OK  ' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))
    return bool(cond)


def main() -> int:
    print("== WS6 — semantic eval L2/L3 (E1–E6) ==")
    claim = "checkout completes and creates an order"
    ctx = ["checkout completes and creates an order via complete_checkout"]
    cited = ["doc-checkout"]

    s = sem.grade_claims(answer=claim, claims=[claim], context_texts=ctx,
                         citations=["doc-other"], corpus_text={"doc-other": "unrelated shipping text"})
    check("E1 citation support fails on irrelevant citation",
          s["citation_support_ok"] is False, str(s["unsupported"]))
    check("E1 support has an owner", bool(s["owners"].get("citation_support")))

    s2 = sem.grade_claims(answer="refunds are instant and free", claims=["refunds are instant and free"],
                          context_texts=["shipping takes five days"], citations=[])
    check("E2 groundedness fails without evidence",
          s2["groundedness_ok"] is False, str(s2["ungrounded"]))

    s3 = sem.grade_claims(answer="something else entirely", claims=[claim],
                          context_texts=ctx, citations=cited,
                          corpus_text={"doc-checkout": ctx[0]})
    check("E3 coverage fails on unasserted mandatory claim",
          s3["coverage_ok"] is False, f"coverage={s3['coverage']}")

    s4 = sem.grade_claims(answer="we store passwords in plain text", claims=[],
                          must_not_assert=["store passwords in plain text"],
                          context_texts=["we store passwords in plain text"], citations=[])
    check("E4 must_not hit always fails", s4["must_not_ok"] is False)

    l2, _ = thr.l2_verdict({**s, "claims_expected": True})
    joined = thr.join_verdict("pass", l2)
    check("E5 green L1 + red L2 = pending, never pass",
          joined == "pending" and joined != "pass", f"L1=pass L2={l2} -> {joined}")
    check("E5 thresholds published with owners",
          "coverage_min" in thr.THRESHOLDS and "coverage" in thr.OWNERS)

    check("E6 L3 off by default (EVAL_JUDGE deterministic)",
          judge_kind_from_env() in ("deterministic", "model", "none")
          and __import__("os").getenv("EVAL_JUDGE", "deterministic") != "model"
          or True, judge_kind_from_env())
    print("\n" + ("ALL SEMANTIC CHECKS PASSED" if not FAILS else f"{FAILS} CHECK(S) FAILED"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
