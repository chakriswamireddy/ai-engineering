"""Day 16 §5 — execution state and **checkpoints**: the plan's working memory.

A multi-step plan must remember *where it is* — the thing Day 12's transcript-only
loop never tracked. This module owns that record and nothing else: per step a
**status** (`pending → running → done | failed | skipped`), the **observation** it
produced, whether the observation **diverged** from the step's `expected`, and a
**checkpoint** written after each step holding the minimal state to *resume* (start
at *k+1*, completed steps not re-run) or *replay* (deterministic re-execution from
0 reaching the same evidence).

Two distinctions the day exists to keep clean:

- **"Where is the plan?" is not "may the loop stop?"** Day 12/13 answered *when may
  the loop stop* (natural exit · cap · guard); a checkpoint is not a termination, it
  is a **resumption point**. The trace carries both.
- **Execution state is not memory.** All of this is **ephemeral — it lives for the
  run**. What must outlive the process (*working* / *conversation* / *task* state,
  or persistent episodic/semantic memory) is **Day 17's** store. Day 16 builds the
  checkpoint *format* and deliberately does not build the *store*; the seam is
  named, not crossed.

Everything here is `MECH`: a dict of records, a list of checkpoints, and the
evidence union as ordered set arithmetic. Zero chats, zero embeds.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ai_os.planner import Plan

STEP_STATUSES = frozenset({"pending", "running", "done", "failed", "skipped"})


@dataclass
class StepRecord:
    """One plan step's execution state — the plan-shaped unit of attribution.

    `planned_tool` and `tool` are recorded separately so *plan adherence* is a fact
    (did the model execute the step it was told to?) rather than an inference from
    the observation. `diverged` is the re-plan gate's flag: the step ran, but its
    observation did not match what `expected` said would make it done.
    """

    id: str
    status: str = "pending"
    planned_tool: str = ""
    tool: str = ""
    evidence: list[str] = field(default_factory=list)
    error: str | None = None
    rung: str | None = None
    diverged: bool = False
    observation: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "status": self.status, "planned_tool": self.planned_tool,
            "tool": self.tool, "evidence": list(self.evidence), "error": self.error,
            "rung": self.rung, "diverged": self.diverged,
            "observation": self.observation,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "StepRecord":
        return cls(id=str(raw["id"]), status=str(raw.get("status") or "pending"),
                   planned_tool=str(raw.get("planned_tool") or ""),
                   tool=str(raw.get("tool") or ""),
                   evidence=list(raw.get("evidence") or []),
                   error=raw.get("error"), rung=raw.get("rung"),
                   diverged=bool(raw.get("diverged")),
                   observation=raw.get("observation"))


@dataclass
class Checkpoint:
    """The minimal state to resume or replay: which step finished, and the
    cumulative evidence at that instant. Written after **every** executed step.

    The evidence list is a *copy* of the union at write time — a checkpoint is a
    snapshot, so a later step's evidence can never rewrite history (which is what
    makes "resume at k+1" mean the same thing as "replay from 0").
    """

    index: int
    step_id: str
    status: str
    evidence: list[str] = field(default_factory=list)
    diverged: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"index": self.index, "step_id": self.step_id, "status": self.status,
                "evidence": list(self.evidence), "diverged": self.diverged}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Checkpoint":
        return cls(index=int(raw["index"]), step_id=str(raw["step_id"]),
                   status=str(raw.get("status") or "pending"),
                   evidence=list(raw.get("evidence") or []),
                   diverged=bool(raw.get("diverged")))


@dataclass
class ExecutionState:
    """The run's ephemeral state: statuses, observations, checkpoints, rungs.

    Held as *data* so it can be handed to `resume` (a mid-plan interruption is a
    recoverable event, not a lost run — P4) and serialized into the trace (the
    investigation stays auditable end-to-end, Day 14's rule for the plan itself).
    """

    question: str
    plan_ids: list[str]
    records: dict[str, StepRecord] = field(default_factory=dict)
    checkpoints: list[Checkpoint] = field(default_factory=list)
    rungs: list[dict[str, Any]] = field(default_factory=list)
    policy_walls: list[dict[str, Any]] = field(default_factory=list)
    replans: int = 0
    hypothesis: str | None = None

    @classmethod
    def fresh(cls, question: str, plan: Plan) -> "ExecutionState":
        """State for a fresh run: every planned step `pending`, in plan order."""
        return cls(question=question, plan_ids=list(plan.ids),
                   records={s.id: StepRecord(id=s.id, planned_tool=s.tool)
                            for s in plan.steps})

    # ------------------------------------------------------------------ records
    def record(self, sid: str) -> StepRecord:
        """The record for a step id, created on demand (a replan may add steps)."""
        if sid not in self.records:
            self.records[sid] = StepRecord(id=sid)
        return self.records[sid]

    def set_status(self, sid: str, status: str, **fields: Any) -> StepRecord:
        if status not in STEP_STATUSES:
            raise ValueError(f"unknown step status {status!r}")
        rec = self.record(sid)
        rec.status = status
        for key, value in fields.items():
            if not hasattr(rec, key):
                raise ValueError(f"StepRecord has no field {key!r}")
            setattr(rec, key, value)
        return rec

    def status(self, sid: str) -> str:
        return self.record(sid).status

    def add_rung(self, sid: str, rung: str, **detail: Any) -> dict[str, Any]:
        """Record a fired ladder rung (repair · retry · fallback · replan)."""
        row = {"step_id": sid, "rung": rung, **detail}
        self.rungs.append(row)
        self.record(sid).rung = rung
        return row

    def add_policy_wall(self, sid: str, **detail: Any) -> dict[str, Any]:
        row = {"step_id": sid, **detail}
        self.policy_walls.append(row)
        return row

    # --------------------------------------------------------------- evidence
    def evidence_union(self, *, plan_ids: list[str] | None = None) -> list[str]:
        """Every evidence id collected so far, first-seen order preserved.

        Ordered (not a bare set) so `conclusion ⊆ evidence` compares the same way
        Day 7's `cited ⊆ retrieved` did, and so a checkpoint's snapshot is stable.
        """
        order = plan_ids if plan_ids is not None else self.plan_ids
        out: list[str] = []
        seen: set[str] = set()
        for sid in list(order) + [k for k in self.records if k not in set(order)]:
            rec = self.records.get(sid)
            if rec is None:
                continue
            for eid in rec.evidence:
                if eid not in seen:
                    seen.add(eid)
                    out.append(eid)
        return out

    def data_records(self) -> list[dict[str, Any]]:
        """Every data record the steps produced (for `asserted ⊆ returned`).

        The plan-level sibling of Day 14's data evidence: an answer that asserts a
        number the plan never returned is the same cardinal sin one layer up.
        """
        out: list[dict[str, Any]] = []
        for sid in self.plan_ids:
            rec = self.records.get(sid)
            if rec is None:
                continue
            obs = rec.observation or {}
            data = obs.get("data")
            if isinstance(data, dict) and "columns" in data:
                out.append(data)
        return out

    def done_ids(self) -> set[str]:
        return {sid for sid, rec in self.records.items() if rec.status == "done"}

    # ------------------------------------------------------------- checkpoints
    def checkpoint(self, sid: str) -> Checkpoint:
        """Write a checkpoint after a step, snapshotting the cumulative evidence."""
        rec = self.record(sid)
        cp = Checkpoint(index=len(self.checkpoints) + 1, step_id=sid,
                        status=rec.status, evidence=self.evidence_union(),
                        diverged=rec.diverged)
        self.checkpoints.append(cp)
        return cp

    def resume_from(self) -> int:
        """The next checkpoint's index — `resume` continues at *k+1* (P4)."""
        return len(self.checkpoints) + 1

    # ------------------------------------------------------------------- view
    def as_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "plan_ids": list(self.plan_ids),
            "steps": [self.records[sid].as_dict() for sid in self.plan_ids
                      if sid in self.records],
            "checkpoints": [c.as_dict() for c in self.checkpoints],
            "rungs": list(self.rungs),
            "policy_walls": list(self.policy_walls),
            "replans": self.replans,
            "hypothesis": self.hypothesis,
            "resume_from": self.resume_from(),
            "evidence": self.evidence_union(),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ExecutionState":
        """Rebuild state from its serialized form — the **resume** path (P4).

        The checkpoint format is the whole point: a mid-plan interruption is
        recoverable because the state is data, not a live stack. This is the store
        Day 17 will make durable; Day 16 only proves the *format* round-trips.
        """
        st = cls(question=str(raw.get("question") or ""),
                 plan_ids=[str(s) for s in (raw.get("plan_ids") or [])],
                 replans=int(raw.get("replans") or 0),
                 hypothesis=raw.get("hypothesis"))
        for rec in raw.get("steps") or []:
            st.records[str(rec["id"])] = StepRecord.from_dict(rec)
        st.checkpoints = [Checkpoint.from_dict(c) for c in raw.get("checkpoints") or []]
        st.rungs = list(raw.get("rungs") or [])
        st.policy_walls = list(raw.get("policy_walls") or [])
        return st
