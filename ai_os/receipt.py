"""Day 19 §6 — the delegation contract: a receipt, and `delegated ⊆ returned`.

The only thing allowed to cross the orchestrator↔specialist boundary is a
**receipt** — the exact `kind=episodic` record Day 17 stores and Day 18 recalls,
promoted to the currency of an inter-loop handoff:

```text
{ task_id, principal, role, question_asked, evidence[], conclusion,
  provenance {run_id, step, source: "specialist:{role}"}, status: returned|refused|failed }
```

Nothing else crosses. A specialist's "finding" that is not a receipt is not a
finding, and the orchestrator **cites** a receipt; it never paraphrases one — a
paraphrase is the day's cardinal leak ("Chinese whispers"), the delegation-seam
version of Day 13's fabricated citation.

This module is `MECH`: set membership over returned evidence, plus the receipt
schema. The day's headline invariant is one function:

    check_delegated_subset_returned(answer, receipts)
        attributed ⊆ returned      (every id the answer names came from a receipt)

symmetric with `cited ⊆ retrieved` (Day 6), `asserted ⊆ returned` (Day 14),
`conclusion ⊆ evidence` (Day 16), `recalled ⊆ authored` (Day 17), and
`authored ⊆ grounded` (Day 18) — the fourth translation of the same rule, now
across the loop boundary.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from ai_os.agent import mentioned_ids

RETURNED = "returned"
REFUSED = "refused"
FAILED = "failed"
DELEGATION_STATUSES: tuple[str, ...] = (RETURNED, REFUSED, FAILED)

# The exit reasons that make an *empty* specialist run a failure rather than a
# return. A run that stopped on its own bound without evidence did not return a
# finding — the orchestrator must treat it as a failed delegation, not a result.
_FAILURE_EXITS = frozenset({"max_steps", "budget", "gave_up", "no_progress",
                            "oscillation", "tool_failure", "policy"})


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def max_delegations_from_env() -> int:
    return max(1, _env_int("MAX_DELEGATIONS", 3))


def handoff_timeout_from_env() -> float:
    try:
        return float(os.getenv("HANDOFF_TIMEOUT", "30").strip() or 30.0)
    except ValueError:
        return 30.0


def delegation_retry_from_env() -> int:
    return max(0, _env_int("DELEGATION_RETRY", 1))


@dataclass
class Receipt:
    """The unit of delegation — a specialist's returned episodic record.

    `status` is a return value, not an exception (Day 11's refusal-as-a-return-value,
    at the agent seam): `returned` carries evidence + conclusion; `refused` means
    the specialist's corpus/policy answered "no" and says why; `failed` means the
    loop crashed, timed out, or stopped without a finding. An orchestrator may
    retry a `failed` (bounded) or route around it; it may **never** fabricate a
    receipt it did not receive.
    """

    task_id: str
    principal: str
    role: str
    question_asked: str
    conclusion: str = ""
    evidence: list[str] = field(default_factory=list)
    status: str = RETURNED
    reason: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    cost: dict[str, Any] = field(default_factory=dict)
    steps: int = 0
    run_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "task_id": self.task_id,
            "principal": self.principal,
            "role": self.role,
            "question_asked": self.question_asked,
            "conclusion": self.conclusion,
            "evidence": list(self.evidence),
            "status": self.status,
            "provenance": dict(self.provenance),
            "cost": dict(self.cost),
            "steps": self.steps,
        }
        if self.reason:
            out["reason"] = self.reason
        if self.run_id:
            out["run_id"] = self.run_id
        return out

    def citable(self) -> bool:
        """Only a `returned` receipt may ground the aggregator's answer."""
        return self.status == RETURNED

    def evidence_ids(self) -> list[str]:
        return list(self.evidence) if self.citable() else []

    def attributable(self) -> bool:
        """A receipt with honest provenance — the write-path rule across the seam."""
        cb = self.provenance or {}
        return bool(cb.get("source")) and cb.get("source") in AUTHORED_RECEIPT_SOURCES

    @classmethod
    def from_run(cls, role: str, run: dict[str, Any], *, principal: str,
                 question_asked: str | None = None, run_id: str | None = None,
                 status: str | None = None, reason: str | None = None,
                 task_id: str | None = None) -> "Receipt":
        """Wrap a finished specialist run as a receipt (returned/refused/failed).

        The status is derived from the run's own verdicts, never asserted: a
        `policy` stop, an all-refusal run, or an empty run that hit a bound is
        *not* a returned finding.
        """
        evidence = list(run.get("evidence") or [])
        refusal = bool(run.get("refused"))
        termination = run.get("termination_reason")
        policy = int(run.get("policy_violations") or 0)
        if status is None:
            if policy:
                status, reason = REFUSED, reason or "role_violation"
            elif refusal:
                status, reason = REFUSED, reason or "no covering evidence"
            elif not evidence and termination in _FAILURE_EXITS:
                status, reason = FAILED, reason or f"termination:{termination}"
            else:
                status = RETURNED
        rid = run_id or run.get("run_id") or "run"
        return cls(
            task_id=task_id or run.get("task_id") or f"{role}:{rid}",
            principal=principal,
            role=role,
            question_asked=question_asked or str(run.get("question") or ""),
            conclusion=str(run.get("answer") or ""),
            evidence=evidence,
            status=status,
            reason=reason,
            provenance={"run_id": rid, "step": "end", "source": f"specialist:{role}"},
            cost=dict(run.get("ledger") or {}),
            steps=int(run.get("tool_steps") or 0),
            run_id=rid,
        )


AUTHORED_RECEIPT_SOURCES = frozenset(
    {f"specialist:{r}" for r in ("knowledge", "data", "engineering")})


def failed_receipt(role: str, question: str, *, principal: str,
                   reason: str, run_id: str, task_id: str | None = None) -> Receipt:
    """A delegation that never returned — the orchestrator's honest negative."""
    return Receipt(
        task_id=task_id or f"failed:{role}:{run_id}",
        principal=principal, role=role, question_asked=question,
        conclusion="", evidence=[], status=FAILED, reason=reason,
        provenance={"run_id": run_id, "step": "delegation",
                    "source": f"specialist:{role}"},
        cost={}, steps=0, run_id=run_id)


# ------------------------------------------------------------- the invariants

def returned_evidence(receipts: Sequence[Receipt]) -> list[str]:
    """The union of *returned* receipts' evidence — the only citable surface."""
    out: list[str] = []
    for receipt in receipts:
        for eid in receipt.evidence_ids():
            if eid not in out:
                out.append(eid)
    return out


def check_delegated_subset_returned(answer: str,
                                    receipts: Sequence[Receipt]) -> dict[str, Any]:
    """`delegated ⊆ returned` — the day's headline, checked in code.

    Every id the orchestrator's answer names must appear in a **returned** receipt's
    evidence. An id attributed but absent from every receipt is a **handoff
    fabrication** — the Chinese whisper, caught exactly as Day 13 caught a
    fabricated citation, now across a seam. A refused/failed receipt contributes
    nothing citable, so an answer that "remembers" what a crashed specialist found
    fails this check by construction.
    """
    returned = returned_evidence(receipts)
    attributed = mentioned_ids(answer)
    fabricated = [src for src in attributed if src not in set(returned)]
    return {
        "ok": not fabricated,
        "attributed": attributed,
        "returned": returned,
        "fabricated": fabricated,
        "returned_receipts": sum(1 for r in receipts if r.citable()),
        "receipts": len(list(receipts)),
    }


def check_per_hop_grounding(runs: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Each specialist's own `cited ⊆ retrieved`, and the total leaked at the seam.

    The orchestrator cannot see inside a specialist's loop (Day 18's closed store,
    one hop out), so the specialist must prove its own grounding; a hop that leaked
    evidence internally is a Day 6/7 failure *inside* the specialist, not a
    delegation failure — but the harness counts it here so a leak is locatable to
    the exact hop.
    """
    hops: list[dict[str, Any]] = []
    leaks = 0
    for run in runs:
        fabricated = list(run.get("fabricated") or [])
        leaks += len(fabricated)
        hops.append({
            "role": run.get("role"),
            "fabricated": fabricated,
            "cited_subset_retrieved": bool(run.get("cited_subset_retrieved", True)),
        })
    return {"ok": leaks == 0, "hops": hops, "leaks": leaks}


def receipts_subset_authored(receipts: Sequence[Receipt]) -> bool:
    """Every receipt carries honest provenance (whose loop returned it)."""
    return all(r.attributable() for r in receipts)


def receipt_block(receipts: Sequence[Receipt]) -> str | None:
    """The aggregator's prompt block: receipts served **as receipts**.

    Marks each returned receipt as a citable record and states the one rule the
    day enforces in code — cite the receipt, never re-voice it. A refused/failed
    receipt is shown as a *named negative*, never as evidence. Returns `None` when
    nothing returned, so an empty delegation changes the aggregator's prompt
    nothing (the G9 reflex).
    """
    # A returned receipt is usable even with no *id*-shaped evidence: a data
    # specialist returns rows (a finding), not doc ids, and its conclusion is still
    # citable. Only a refused/failed receipt is shown as a named negative.
    usable = [r for r in receipts if r.citable()]
    negatives = [r for r in receipts if not r.citable()]
    if not usable and not negatives:
        return None
    lines = [
        "Delegation receipts. Serve these as RECORDS you may CITE — never re-voice "
        "a receipt as your own prose, and cite only ids that appear below. A "
        "receipt that did not return is a named gap, not evidence."
    ]
    for r in usable:
        lines.append(f"- [{r.role}] task {r.task_id}: {r.conclusion} "
                     f"(evidence: {', '.join(r.evidence)})")
    for r in negatives:
        lines.append(f"- [{r.role}] task {r.task_id} {r.status.upper()}: "
                     f"{r.reason or 'no reason given'} (no evidence returned)")
    return "\n".join(lines)
