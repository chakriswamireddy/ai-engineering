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

import fcntl
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ai_os.planner import Plan

STEP_STATUSES = frozenset({"pending", "running", "done", "failed", "skipped",
                           # WS4 — paused on a human decision, resumable (not failed).
                           "awaiting_approval"})


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
    # WS4 — the approval request this step waits on (set with the
    # `awaiting_approval` status; read back on resume to find the decision).
    approval_request_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "status": self.status, "planned_tool": self.planned_tool,
            "tool": self.tool, "evidence": list(self.evidence), "error": self.error,
            "rung": self.rung, "diverged": self.diverged,
            "observation": self.observation,
            "approval_request_id": self.approval_request_id,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "StepRecord":
        return cls(id=str(raw["id"]), status=str(raw.get("status") or "pending"),
                   planned_tool=str(raw.get("planned_tool") or ""),
                   tool=str(raw.get("tool") or ""),
                   evidence=list(raw.get("evidence") or []),
                   error=raw.get("error"), rung=raw.get("rung"),
                   diverged=bool(raw.get("diverged")),
                   observation=raw.get("observation"),
                   approval_request_id=raw.get("approval_request_id"))


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


# ====================================================== Day 17 — the state store
"""Day 17 §3–§6 — the store that gives Day 16's checkpoint a home.

Day 16 built the checkpoint *format* and named the *store* as Day 17's seam. This
block crosses it. A `StateRecord` is `{kind, principal, key, payload, ttl,
expires_at, created_by (run_id + step + source), created_at, accessed_at, schema}`
and the `StateStore` is CRUD + the four decisions the day pre-commits:

1. **the schema is a contract** — `schema: 2`, versioned, so a future agent can
   read a past run;
2. **provenance is mandatory** — a write with no `created_by`, or one from a
   source the runtime did not author, is **rejected at write time** (fail-closed):
   the raw material of **`recalled ⊆ authored`**;
3. **the store is closed** — only authored sources write; a record planted in the
   file without provenance is *not admitted on load*, so an import is unreachable
   (an open memory channel is Day 25's injection, pre-installed);
4. **deletion is first-class** — `forget(principal, key)` removes the record **and
   its derived index**, expiry acts as absence, and `compact` physically reclaims.

Everything here is `MECH`: dict CRUD, TTL arithmetic, key membership. Zero chats,
zero embeds — state's premium is latency and disk, never money.
"""

# §6 — default TTLs are deliberately short: a long TTL is a conscious config, not
# the default. What *should* live long-term is decided by distillation (Day 18),
# not by a long TTL on a task record.
DEFAULT_STATE_TTL = 86400.0        # 24h
DEFAULT_STATE_MAX_RECORDS = 1000
DEFAULT_STATE_SCHEMA = 2

# The **authors** — the runtime components allowed to write. `source` is checked
# against this set, so "import"/"external"/"injected" are not authorable: the
# store is closed to writes it did not make (§3, decision 3).
#
# Day 18 adds `distiller`: the semantic shelf's writer is a *consumer* of the
# store, not a new store — the distiller mints `kind=semantic` records through
# this same closed write path, so its output is admitted only because it carries
# provenance (S7 untouched; a distilled claim the system did not author is still
# unreachable).
AUTHORED_SOURCES = frozenset(
    {"agent", "executor", "session", "trace", "run", "checkpoint", "distiller",
     # Day 19 — the orchestrator is an *author of orchestration records only*
     # (routing, delegations, verdicts), never of a specialist's episodes. Adding it
     # here does not loosen `recalled ⊆ authored`: it admits a writer whose writes
     # are a different `kind`, and the write policy (§7) forbids it the episodic shelf.
     "orchestrator",
     # Day 27 — the approval desk. The same argument Day 19 carried, one shelf over:
     # admitting an author does not loosen the grounding rules for any other shelf,
     # and the write policy below forbids it every shelf it did not ground — in fact
     # the *inverse* rule is the sharper one, because `approval` is the only kind a
     # **closed set of one author** may write. A record that says a person decided
     # something is the one record in this repository whose author may not be the
     # component that wanted the action, so `source` for `kind=approval` is checked
     # to be exactly `"approver"` (see `_check_provenance`).
     "approver"})

# Day 27 §8 — the approval shelf. `APPROVAL_KIND` records are the one kind whose
# author is *fixed*: only the approval desk writes them, so an `agent`- or
# `orchestrator`-authored approval is refused by the existing provenance check
# rather than by a comment. The key shape mirrors the other shelves'
# (`/approval/{request_id}`) so a reader can tell the shelf from the key.
APPROVAL_KIND = "approval"
APPROVAL_AUTHOR_SOURCE = "approver"

# The three shelves' kinds (the lifetime axis, §2's table).
TASK_KIND = "task"
CONVERSATION_KIND = "conversation"
WORKING_KIND = "working"

# Day 18 — the memory shelves. Episodic = what happened (a receipt, recalled
# verbatim); semantic = what is generally true (a distilled lesson, `authored ⊆
# grounded`); preference = *data about the user*, cited as data, never a lesson.
EPISODIC_KIND = "episodic"
SEMANTIC_KIND = "semantic"
PREFERENCE_KIND = "preference"
# Day 19 — the orchestrator's own shelf: routing decisions, delegations, verdicts.
# Deliberately *not* an episode: a router that authors investigations would claim
# evidence it never personally grounded (§7, M7's boundary across roles).
ORCHESTRATION_KIND = "orchestration"

# §5 — the sample-size rule: a lesson from one run is an anecdote, never minted.
DEFAULT_MIN_SOURCES = 2

STATE_ON = "on"
STATE_OFF = "off"


class ProvenanceError(ValueError):
    """A state write with no author — the closed write path's fail-closed refusal."""


def task_key(task_id: str) -> str:
    return f"/task/{task_id}"


def conversation_key(session_id: str) -> str:
    return f"/conversation/{session_id}"


def working_key(task_id: str) -> str:
    return f"/working/{task_id}"


# Day 18 — the memory shelves' key shapes. `Recall` (memory.py) and `forget`'s
# re-grounding parse these prefixes, so a concept's `source_episodes` (task ids)
# map to episodic keys deterministically.
def episode_key(task_id: str) -> str:
    return f"/episodic/{task_id}"


def concept_key(concept_id: str) -> str:
    return f"/semantic/{concept_id}"


def preference_key(name: str) -> str:
    return f"/preference/{name}"


def orchestration_key(name: str) -> str:
    """The orchestrator's record key (routing/delegation/verdict — never an episode)."""
    return f"/orchestration/{name}"


def approval_key(request_id: str) -> str:
    """The approval record's key (`/approval/{request_id}`), Day 27 §8.

    One request, one key, one durable record — the address an incident review can
    be pointed at, and the shape `write_approval` writes without a second naming
    convention.
    """
    return f"/approval/{request_id}"


EPISODE_KEY_PREFIX = "/episodic/"


def episode_task_id(key: str) -> str:
    """The task id an episodic key names (`/episodic/{task_id}` → `{task_id}`)."""
    return str(key)[len(EPISODE_KEY_PREFIX):] if str(key).startswith(EPISODE_KEY_PREFIX) else ""


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def state_store_path_from_env() -> str | None:
    return os.getenv("STATE_STORE_PATH", "").strip() or None


def state_default_ttl_from_env() -> float:
    return _env_float("STATE_DEFAULT_TTL", DEFAULT_STATE_TTL)


def state_max_records_from_env() -> int:
    return max(1, _env_int("STATE_MAX_RECORDS", DEFAULT_STATE_MAX_RECORDS))


def state_principal_from_env() -> str:
    return os.getenv("STATE_PRINCIPAL", "").strip() or "default"


@dataclass
class StateRecord:
    """One record on a shelf — the unit of state and the unit of attribution.

    `created_by` is not metadata: it is the reason the record may be recalled at
    all (`recalled ⊆ authored`). `expires_at` is consulted *before* anything else
    on every read, because a stale record that still retrieves is not expired, it
    is lying. `accessed_at` makes decay visible (age vs neglect).
    """

    kind: str
    principal: str
    key: str
    payload: Any
    created_by: dict[str, Any]
    created_at: float
    ttl: float | None = None
    expires_at: float | None = None
    accessed_at: float | None = None
    schema: int = DEFAULT_STATE_SCHEMA
    # Day 18 §6 — semantic expiry's *meter* beside the clock. A concept is served
    # only while its `strength` clears its `recall_threshold`; below it the concept
    # is "alive but unserved" (the clock is the backstop, not the policy). Episodic
    # / task records leave both `None` and are unaffected.
    strength: float | None = None
    recall_threshold: float | None = None

    def is_expired(self, now: float) -> bool:
        return self.expires_at is not None and now >= self.expires_at

    def is_decayed(self) -> bool:
        """A relevance-expired semantic record: alive, but below its serve τ.

        The clock is consulted separately (`is_expired`); this is Day 18's added
        meter, and a record that trips it **acts absent on the recall path** while
        remaining visible to `list_for` (a principal may always see what it holds).
        """
        if self.strength is None or self.recall_threshold is None:
            return False
        return float(self.strength) < float(self.recall_threshold)

    def is_served(self, now: float) -> bool:
        return not self.is_expired(now) and not self.is_decayed()

    def provenance(self) -> str:
        cb = self.created_by or {}
        return f"{cb.get('run_id')}/{cb.get('step')}/{cb.get('source')}"

    def as_dict(self) -> dict[str, Any]:
        out = {
            "kind": self.kind, "principal": self.principal, "key": self.key,
            "payload": self.payload, "created_by": dict(self.created_by or {}),
            "created_at": self.created_at, "ttl": self.ttl,
            "expires_at": self.expires_at, "accessed_at": self.accessed_at,
            "schema": self.schema,
        }
        if self.strength is not None:
            out["strength"] = self.strength
        if self.recall_threshold is not None:
            out["recall_threshold"] = self.recall_threshold
        return out

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "StateRecord | None":
        """Rebuild a record, or return `None` when it carries no honest provenance.

        `None` is how a runtime-imported record becomes **unreachable**: a record
        present in the file but not written by an authored source is simply not
        admitted to the store, so `get`/`list_for`/`recall` can never return it.
        """
        if not isinstance(raw, dict):
            return None
        cb = raw.get("created_by")
        if (not isinstance(cb, dict) or not str(cb.get("run_id") or "").strip()
                or cb.get("source") not in AUTHORED_SOURCES):
            return None
        return cls(
            kind=str(raw.get("kind") or ""), principal=str(raw.get("principal") or ""),
            key=str(raw.get("key") or ""), payload=raw.get("payload"),
            created_by={"run_id": str(cb.get("run_id")),
                        "step": cb.get("step"), "source": cb.get("source")},
            created_at=float(raw.get("created_at") or 0.0),
            ttl=(None if raw.get("ttl") is None else float(raw["ttl"])),
            expires_at=(None if raw.get("expires_at") is None
                        else float(raw["expires_at"])),
            accessed_at=(None if raw.get("accessed_at") is None
                         else float(raw["accessed_at"])),
            schema=int(raw.get("schema") or DEFAULT_STATE_SCHEMA),
            strength=(None if raw.get("strength") is None
                      else float(raw["strength"])),
            recall_threshold=(None if raw.get("recall_threshold") is None
                              else float(raw["recall_threshold"])),
        )


# ====================================================== Day 29 §5.4 — single-writer guard
"""The single-writer guard is an exclusive lock taken at boot, refusing the second
process by name. It converts Day 28's "two writers lose records silently" from a
drawing into a refusal, which means the container can honestly be scaled to
exactly one.

`flock` is advisory (every writer must take it — the state store is the only
writer today, and the day is the only thing that can make that true) and it is
filesystem-dependent (works on local volume and bind mount; NFS semantics vary
and are not claimed). What it buys is enormous and specific: the refusal is the
deliverable, and the fixture asserts the defect first.
"""


class SecondWriter(RuntimeError):
    """Raised when a second process attempts to acquire the state store lock.

    The message names the holder's pid and path, and the fix:
    "two writers on one state file lose records; run one process, or give each
    its own STATE_STORE_PATH"
    """
    def __init__(self, holder_pid: int, path: str) -> None:
        self.holder_pid = holder_pid
        self.path = path
        super().__init__(
            f"second writer refused: pid {holder_pid} holds lock on {path}; "
            f"two writers on one state file lose records; "
            f"run one process, or give each its own STATE_STORE_PATH"
        )


class SingleWriter:
    """Exclusive lock on the state file path, taken at boot.

    Opens `<state>.lock`, takes `fcntl.flock(LOCK_EX | LOCK_NB)`, and on failure
    raises `SecondWriter` with the holder's pid and path. The lock is held for
    the process lifetime and released on exit.

    Wired into readiness()'s `state` check and into the process's start-up, so
    `uvicorn --workers 2` or a second replica on a shared volume fails at boot
    with a sentence instead of corrupting a file over the next hour.
    """

    def __init__(self, state_path: str | os.PathLike) -> None:
        self.path = str(state_path)
        self.lock_path = self.path + ".lock"
        self._lock_fd: int | None = None
        self._acquire()

    def _acquire(self) -> None:
        # Ensure parent directory exists
        os.makedirs(os.path.dirname(self.lock_path) or ".", exist_ok=True)
        # Open lock file (create if needed)
        self._lock_fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            # Non-blocking exclusive lock
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # Lock held by another process — read holder's pid from lock file
            holder_pid = self._read_holder_pid()
            os.close(self._lock_fd)
            self._lock_fd = None
            raise SecondWriter(holder_pid, self.path)

    def _read_holder_pid(self) -> int:
        """Best-effort read of holder's pid from lock file."""
        try:
            with open(self.lock_path, "r") as f:
                content = f.read().strip()
                if content.isdigit():
                    return int(content)
        except Exception:
            pass
        return -1

    def __enter__(self) -> "SingleWriter":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.release()

    def release(self) -> None:
        """Release the lock and close the file descriptor."""
        if self._lock_fd is not None:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                os.close(self._lock_fd)
            except Exception:
                pass
            self._lock_fd = None

    def __del__(self) -> None:
        self.release()


def acquire_single_writer_lock(state_path: str | os.PathLike) -> SingleWriter:
    """Acquire the single-writer lock for the given state path.

    Returns the SingleWriter instance (must be kept alive for the process lifetime).
    Raises SecondWriter if another process holds the lock.

    This is the function called at process startup to enforce the one-writer rule.
    """
    return SingleWriter(state_path)


# ------------------------------------------------- WS9 — distributed writer lock
# One file lock cannot span replicas: two pods on two nodes each hold their own
# `flock` and both write. WS9 replaces the file lock with a Postgres advisory
# lock when `STATE_ADVISORY_DSN` is set (`pg_advisory_lock` on a fixed key —
# crash-safe: the lock dies with the session, so a killed replica cannot wedge
# the writer). Without a DSN (offline, single node) the file lock stands — the
# honest default, and the chaos drill's premise (kill 1 replica, zero data
# loss) holds because only the advisory holder writes.
ADVISORY_LOCK_KEY = 841731


def acquire_distributed_writer_lock(
    state_path: str | os.PathLike,
    *, dsn: str | None = None,
) -> Any:
    """WS9 — advisory lock when a DSN is configured, file lock otherwise.

    Returns a lock object with `.release()`; raises `SecondWriter` when the
    advisory lock is taken (same refusal, now replica-wide). Without `psycopg`
    or without a DSN, returns `acquire_single_writer_lock(state_path)` — the
    single-node behavior is byte-identical to Day 29.
    """
    import os as _os
    dsn = dsn or _os.getenv("STATE_ADVISORY_DSN", "").strip()
    if not dsn:
        return acquire_single_writer_lock(state_path)
    try:
        import psycopg  # type: ignore
    except ImportError:
        return acquire_single_writer_lock(state_path)  # no driver: file lock
    try:
        conn = psycopg.connect(dsn, autocommit=True)
    except Exception:
        return acquire_single_writer_lock(state_path)  # DB down: file lock
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
            row = cur.fetchone()
    except Exception:
        conn.close()
        return acquire_single_writer_lock(state_path)  # query failed: file lock
    if not row or not row[0]:
        holder = _advisory_holder(conn)
        conn.close()
        raise SecondWriter(holder, str(state_path))
    return _AdvisoryWriterLock(conn, str(state_path))


def _advisory_holder(conn: Any) -> int:
    return -1  # Postgres names no holder pid; the refusal names the key


class _AdvisoryWriterLock:
    """A held `pg_advisory_lock` session. Dies with the connection (crash-safe)."""

    def __init__(self, conn: Any, path: str) -> None:
        self._conn = conn
        self.path = path

    def release(self) -> None:
        try:
            with self._conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))
        except Exception:                                       # noqa: BLE001
            pass
        try:
            self._conn.close()
        except Exception:                                       # noqa: BLE001
            pass

    def __enter__(self) -> "_AdvisoryWriterLock":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.release()

    def __del__(self) -> None:
        self.release()


class StateStore:
    """File-backed, bounded, expiring, provenance-enforced state (`MECH`).

    One store, one shelf per `(principal, kind)`. `put` needs an author; `get` and
    `list_for` consult expiry first; `recall` reads the same keys through the
    **derived index** deletion must also purge; `forget` removes a record and its
    aggregate; `compact` reclaims the expired. `enabled=False` makes every read
    return nothing and every write a no-op, which is the S9 off-switch.
    """

    def __init__(self, path: str | os.PathLike | None = None, *,
                 default_ttl: float | None = None,
                 max_records: int | None = None,
                 principal: str | None = None,
                 clock=time.time, enabled: bool = True,
                 autosave: bool = True) -> None:
        self.path = str(path) if path else None
        self.default_ttl = (DEFAULT_STATE_TTL if default_ttl is None
                            else float(default_ttl))
        self.max_records = (DEFAULT_STATE_MAX_RECORDS if max_records is None
                            else int(max_records))
        self.principal = principal or "default"
        self.clock = clock
        self.enabled = enabled
        self.autosave = autosave
        self.records: dict[tuple[str, str, str], StateRecord] = {}
        # The derived aggregate `forget` must also remove: (principal, kind) → keys,
        # in first-seen order. `recall` is served through it; deletion goes through
        # both, so a deleted memory cannot "sometimes reappear".
        self.index: dict[tuple[str, str], list[str]] = {}
        self.dropped: list[str] = []        # provenance-less records refused at load
        self.evicted: list[str] = []        # keys removed by the size cap
        self.compacted = 0                  # records physically reclaimed by TTL
        self.regrounded: list[dict[str, Any]] = []  # Day 18: concepts re-grounded by forget
        self.load()

    @classmethod
    def from_env(cls, **kw: Any) -> "StateStore":
        return cls(state_store_path_from_env(),
                   default_ttl=state_default_ttl_from_env(),
                   max_records=state_max_records_from_env(),
                   principal=state_principal_from_env(), **kw)

    # ------------------------------------------------------------------ keys
    @staticmethod
    def _id(principal: str, kind: str, key: str) -> tuple[str, str, str]:
        return (str(principal), str(kind), str(key))

    def __len__(self) -> int:
        return len(self.records)

    # ------------------------------------------------------------- write path
    def _check_provenance(self, created_by: Any, kind: str | None = None
                          ) -> dict[str, Any]:
        if not isinstance(created_by, dict):
            raise ProvenanceError(
                "a state write requires created_by {run_id, step, source}")
        run_id = str(created_by.get("run_id") or "").strip()
        source = str(created_by.get("source") or "").strip()
        if not run_id:
            raise ProvenanceError("a state write requires a non-empty run_id")
        if source not in AUTHORED_SOURCES:
            raise ProvenanceError(
                f"source {source!r} is not an authored source "
                f"(allowed: {sorted(AUTHORED_SOURCES)}) — the store is closed")
        # Day 27 §8 — the one *kind-specific* policy in the store, and it is
        # fail-closed in **both** directions: an `approval` record may be written by
        # exactly one author, and the approval desk may write exactly one kind. The
        # reason is not tidiness — it is that this is the only record in the
        # repository where a model's intent and a person's decision coexist, so
        # `created_by={"source": "agent"}` (or "orchestrator", or "executor") is
        # refused here rather than trusted to convention, and the desk's author
        # string cannot be borrowed to mint a task, episode or concept either. A
        # provenance policy that lives in a frozenset is only a comment until a
        # fixture proves the refusal; the H-set and the unit suite do exactly that.
        authored_approval = source == APPROVAL_AUTHOR_SOURCE
        if (str(kind) == APPROVAL_KIND) != authored_approval:
            if authored_approval:
                raise ProvenanceError(
                    f"source {APPROVAL_AUTHOR_SOURCE!r} may only author "
                    f"kind={APPROVAL_KIND!r} records, not {kind!r} — the approval "
                    "desk's author is not a general licence")
            raise ProvenanceError(
                f"kind={APPROVAL_KIND!r} may only be authored by "
                f"{APPROVAL_AUTHOR_SOURCE!r}, not {source!r} — the approval shelf is "
                "closed to every author that could have wanted the action")
        return {"run_id": run_id, "step": created_by.get("step"), "source": source}

    def put(self, principal: str, kind: str, key: str, payload: Any, *,
            created_by: Any, ttl: float | None | str = "inherit",
            strength: float | None = None,
            recall_threshold: float | None = None) -> StateRecord | None:
        """Write a record. Fail-closed on missing/foreign provenance (§3).

        `ttl="inherit"` (the default) uses the store default; `ttl=None`/`0` means
        no expiry. Returns the stored record, or `None` when the store is disabled.
        `strength`/`recall_threshold` are Day 18's semantic meter (left `None` for
        every non-semantic record).
        """
        if not self.enabled:
            return None
        prov = self._check_provenance(created_by, kind)
        now = self.clock()
        if ttl == "inherit":
            ttl = self.default_ttl
        ttl = None if ttl in (None, 0) else float(ttl)
        expires = None if ttl is None else now + ttl
        rec = StateRecord(kind=str(kind), principal=str(principal), key=str(key),
                          payload=payload, created_by=prov, created_at=now,
                          ttl=ttl, expires_at=expires, accessed_at=now,
                          strength=(None if strength is None else float(strength)),
                          recall_threshold=(None if recall_threshold is None
                                            else float(recall_threshold)))
        ident = self._id(principal, kind, key)
        self.records[ident] = rec
        self._index_add(principal, kind, key)
        self._enforce_cap()
        self._maybe_save()
        return rec

    # -------------------------------------------------------------- read path
    def get(self, principal: str, kind: str, key: str) -> StateRecord | None:
        """A record, or `None` — expiry is consulted **first** (§6, S5).

        An expired record acts *absent*: it does not exist to the reader. A record
        that could still be retrieved past `expires_at` is not expired, it is lying.
        """
        if not self.enabled:
            return None
        rec = self.records.get(self._id(principal, kind, key))
        if rec is None:
            return None
        now = self.clock()
        if rec.is_expired(now):
            return None
        rec.accessed_at = now
        self._maybe_save()
        return rec

    def touch(self, principal: str, kind: str, key: str) -> None:
        """Refresh `accessed_at` on a served record (Day 18's re-prompt signal).

        Decay is *age vs neglect*: a successful re-serving records that the memory
        earned its keep, so the recall path touches what it serves. Recency is not
        truth — this is the decay meter's input, not a refresh of a concept's
        *strength* (a concept is never refreshed by re-serving alone, §6).
        """
        rec = self.records.get(self._id(principal, kind, key))
        if rec is not None and not rec.is_expired(self.clock()):
            rec.accessed_at = self.clock()
            self._maybe_save()

    def list_for(self, principal: str, kind: str | None = None,
                 key_prefix: str = "") -> list[StateRecord]:
        """Live records for one principal, optionally narrowed by kind/prefix."""
        if not self.enabled:
            return []
        now = self.clock()
        out: list[StateRecord] = []
        for (p, k, key), rec in self.records.items():
            if p != str(principal):
                continue
            if kind is not None and k != str(kind):
                continue
            if key_prefix and not key.startswith(str(key_prefix)):
                continue
            if rec.is_expired(now):
                continue
            out.append(rec)
        return out

    def recall(self, principal: str, kind: str | None = None,
               key_prefix: str = "") -> list[StateRecord]:
        """The retrieval path — same records, served through the derived **index**.

        Kept separate from `list_for` precisely so `forget` can be proven to purge
        *both*: a deleted record that still recalls is the same bug as a fabricated
        citation, on a different shelf (S6).
        """
        if not self.enabled:
            return []
        now = self.clock()
        out: list[StateRecord] = []
        for (p, k), keys in self.index.items():
            if p != str(principal):
                continue
            if kind is not None and k != str(kind):
                continue
            for key in keys:
                if key_prefix and not key.startswith(str(key_prefix)):
                    continue
                rec = self.records.get((p, k, key))
                if rec is None or rec.is_expired(now):
                    continue
                out.append(rec)
        return out

    # ------------------------------------------------------------- deletion
    def delete(self, principal: str, kind: str, key: str) -> bool:
        """Remove one record **and its index entry** (deletion is not cosmetic)."""
        ident = self._id(principal, kind, key)
        if ident not in self.records:
            return False
        del self.records[ident]
        self._index_remove(principal, kind, key)
        self._maybe_save()
        return True

    def forget(self, principal: str, key: str | None = None,
               kind: str | None = None, *, min_sources: int | None = None) -> int:
        """§6 — a first-class retraction: record + derived aggregate, together.

        `forget(principal, key)` removes every record the principal holds under
        `key` (any kind) and purges the index, so the record is unreachable through
        `get`/`list_for`/`recall`. Returns how many were removed.

        Day 18 extends "derived aggregate" to **semantic memory**: every concept
        whose `source_episodes` cite a deleted episode is re-grounded (M5) — its
        source list shrinks, and a concept that no longer clears the sample-size
        rule (`≥ min_sources`) is retired with it. A concept already absorbed into
        a past answer cannot be un-remembered from that answer ("you cannot
        un-distill"), but *future* recall stops.
        """
        removed_keys: list[tuple[str, str]] = []
        removed = 0
        for ident, _rec in list(self.records.items()):
            p, k, rk = ident
            if p != str(principal):
                continue
            if kind is not None and k != str(kind):
                continue
            if key is not None and rk != str(key):
                continue
            del self.records[ident]
            self._index_remove(p, k, rk)
            removed_keys.append((k, rk))
            removed += 1
        if removed:
            self._reground(principal, removed_keys, min_sources)
            self._maybe_save()
        return removed

    def _reground(self, principal: str, removed_keys: list[tuple[str, str]],
                  min_sources: int | None) -> None:
        """Re-ground every concept that cites a just-deleted episode (M5).

        A concept is an *aggregate* of its source episodes; when a source is
        deleted the aggregate must be recomputed, not left citing a record that no
        longer exists. Two outcomes, both recorded in `self.regrounded`:
        retirement (now below `min_sources` — a lesson that lost its evidence is an
        anecdote again) and demotion (strength falls below the recall threshold, so
        the concept is alive but unserved).
        """
        gone = {episode_task_id(rk) for k, rk in removed_keys
                if k == EPISODIC_KIND}
        gone.discard("")
        if not gone:
            return
        ms = DEFAULT_MIN_SOURCES if min_sources is None else max(1, int(min_sources))
        for ident, rec in list(self.records.items()):
            p, k, rk = ident
            if p != str(principal) or k != SEMANTIC_KIND:
                continue
            payload = rec.payload if isinstance(rec.payload, dict) else {}
            sources = list(payload.get("source_episodes") or [])
            if not (set(sources) & gone):
                continue
            kept = [s for s in sources if s not in gone]
            considered = int(payload.get("episodes_considered") or len(sources) or 1)
            strength = (len(kept) / considered) if considered else 0.0
            entry = {"concept": rk, "removed_source": sorted(set(sources) & gone),
                     "remaining_sources": kept, "strength": round(strength, 4)}
            if len(kept) < ms:
                del self.records[ident]
                self._index_remove(p, k, rk)
                entry["action"] = "retired"
                entry["reason"] = "below sample size after deletion"
            else:
                payload["source_episodes"] = kept
                rec.payload = payload
                if rec.recall_threshold is not None:
                    rec.strength = strength
                entry["action"] = ("demoted" if rec.is_decayed() else "re-grounded")
                self.regrounded.append(entry)
                continue
            self.regrounded.append(entry)

    # ------------------------------------------------------------ retention
    def expire(self, now: float | None = None) -> int:
        """How many records are past `expires_at` (they already act absent)."""
        now = self.clock() if now is None else now
        return sum(1 for rec in self.records.values() if rec.is_expired(now))

    def compact(self, now: float | None = None) -> int:
        """Physically reclaim the expired — the read path already hides them."""
        now = self.clock() if now is None else now
        gone = [ident for ident, rec in self.records.items() if rec.is_expired(now)]
        for ident in gone:
            p, k, rk = ident
            del self.records[ident]
            self._index_remove(p, k, rk)
        self.compacted += len(gone)
        if gone:
            self._maybe_save()
        return len(gone)

    def _enforce_cap(self) -> None:
        """Bound the store (`max_records`): evict oldest-first (deterministic)."""
        if not self.max_records or self.max_records <= 0:
            return
        while len(self.records) > self.max_records:
            ident = min(self.records.items(),
                        key=lambda kv: (kv[1].created_at, kv[0]))[0]
            p, k, rk = ident
            del self.records[ident]
            self._index_remove(p, k, rk)
            self.evicted.append(rk)

    # ------------------------------------------------------------------ index
    def _index_add(self, principal: str, kind: str, key: str) -> None:
        bucket = self.index.setdefault((str(principal), str(kind)), [])
        if str(key) not in bucket:
            bucket.append(str(key))

    def _index_remove(self, principal: str, kind: str, key: str) -> None:
        bucket = self.index.get((str(principal), str(kind)))
        if bucket and str(key) in bucket:
            bucket.remove(str(key))
            if not bucket:
                self.index.pop((str(principal), str(kind)), None)

    # ----------------------------------------------------------- persistence
    def dump(self) -> str:
        """The serialized form — the only thing that survives the process (S4)."""
        return json.dumps(
            {"schema": DEFAULT_STATE_SCHEMA,
             "records": [r.as_dict() for r in self.records.values()]},
            sort_keys=True)

    def load(self) -> None:
        if not self.path or not os.path.exists(self.path):
            return
        try:
            raw = json.loads(Path(self.path).read_text() or "{}")
        except (OSError, json.JSONDecodeError):
            return
        self._load_records(raw.get("records") or [])

    def _load_text(self, text: str) -> None:
        try:
            raw = json.loads(text or "{}")
        except json.JSONDecodeError:
            return
        self._load_records(raw.get("records") or [])

    def _load_records(self, rows: list[Any]) -> None:
        for row in rows:
            rec = StateRecord.from_dict(row)
            if rec is None:
                # no provenance → not admitted → unreachable (the import guard)
                if isinstance(row, dict):
                    self.dropped.append(str(row.get("key")))
                continue
            ident = (rec.principal, rec.kind, rec.key)
            self.records[ident] = rec
            self._index_add(rec.principal, rec.kind, rec.key)

    def save(self) -> None:
        if not self.path:
            return
        path = Path(self.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(self.dump())
        os.replace(tmp, path)

    def _maybe_save(self) -> None:
        if self.autosave:
            self.save()

    # ------------------------------------------------------------- round-trip
    def round_trip(self, principal: str, kind: str, key: str) -> dict[str, Any] | None:
        """S4 — `put → dump → load → get` must return a byte-identical payload.

        Resume and replay both consume the *serialized* form, never the live
        object, because the live object dies with the process; the only survivor is
        the file. This is that contract, checked.
        """
        before = self.get(principal, kind, key)
        if before is None:
            return None
        fresh = StateStore(path=None, default_ttl=self.default_ttl,
                           max_records=self.max_records, principal=self.principal,
                           clock=self.clock, autosave=False)
        fresh._load_text(self.dump())
        after = fresh.get(principal, kind, key)
        a = json.dumps(before.payload, sort_keys=True, default=str)
        b = json.dumps(after.payload, sort_keys=True, default=str) if after else None
        return {"before": before.payload, "after": after.payload if after else None,
                "identical": after is not None and a == b}


# ---------------------------------------------------- working/task state helpers

def persist_task_state(store: StateStore, principal: str, task_id: str,
                       state: dict[str, Any], *, run_id: str, step: Any,
                       source: str = "executor") -> StateRecord | None:
    """Flush a checkpointed `ExecutionState` to its shelf (one write per step)."""
    return store.put(principal, TASK_KIND, task_key(task_id), state,
                     created_by={"run_id": run_id, "step": step, "source": source})


def load_task_state(store: StateStore, principal: str,
                    task_id: str) -> dict[str, Any] | None:
    """Load the task record a resume continues from — the checkpoint, cold."""
    rec = store.get(principal, TASK_KIND, task_key(task_id))
    return rec.payload if rec is not None else None


def working_state(payload: dict[str, Any] | None) -> dict[str, Any]:
    """The working shelf, read for its owner: plan position + running hypothesis.

    This is what S1 serves at turn 2 without re-derivation — "the plan is at step
    4, the hypothesis is payment-declined" is *true tomorrow*, so it is a lookup,
    not a re-plan.
    """
    payload = payload or {}
    return {
        "plan_ids": list(payload.get("plan_ids") or []),
        "steps": payload.get("steps") or [],
        "hypothesis": payload.get("hypothesis"),
        "resume_from": payload.get("resume_from"),
        "checkpoints": payload.get("checkpoints") or [],
    }


def persist_conversation(store: StateStore, principal: str, session_id: str,
                         turns: list[dict[str, Any]], *, run_id: str,
                         source: str = "session") -> StateRecord | None:
    """Persist a session's transcript delta — Day 12's transcript, on a shelf.

    The store **augments** the transcript, it does not replace it: the run is still
    reconstructible from transcript + store, and the session id is a *header on the
    state*, not a hidden variable (Day 12's rule survives the upgrade).
    """
    rec = store.get(principal, CONVERSATION_KIND, conversation_key(session_id))
    prior = []
    if rec and isinstance(rec.payload, dict):
        prior = list(rec.payload.get("turns") or [])
    payload = {"session": session_id, "turns": (prior + list(turns))[-40:]}
    return store.put(principal, CONVERSATION_KIND, conversation_key(session_id),
                     payload,
                     created_by={"run_id": run_id, "step": len(payload["turns"]),
                                 "source": source})


def load_conversation(store: StateStore, principal: str,
                      session_id: str) -> list[dict[str, Any]]:
    """Prior turns for a session, or `[]` — the back-reference's source."""
    rec = store.get(principal, CONVERSATION_KIND, conversation_key(session_id))
    if rec and isinstance(rec.payload, dict):
        return [t for t in (rec.payload.get("turns") or [])
                if isinstance(t, dict) and t.get("role") in ("user", "assistant")]
    return []


# ====================================================== Day 27 — the approval shelf

def write_approval(store: StateStore, principal: str, request_id: str,
                   payload: dict[str, Any], *, run_id: str | None,
                   ttl: float | None | str = "inherit") -> StateRecord | None:
    """Day 27 §5 — persist one approval record. Mirrors `write_orchestration`.

    Four lines of glue and one string in a frozenset, which is the whole of Day 27's
    persistence: durability, the TTL, the size cap, the derived index, compaction and
    the fail-closed author check are **inherited** from Day 17 rather than rebuilt.
    The refused alternative was a second JSON file with its own expiry pass, its own
    cap and its own provenance model — three small systems that drift.

    Two clocks, deliberately different, and this is where they are told apart: the
    store's TTL is the **retention of the artifact**, while the record's own
    `expires_at` is the **decision window** (`APPROVAL_TTL_S`). The write therefore
    inherits the store's TTL — a record that vanished at the decision window could
    not report `approval_expired`, and the refusal needs the record to still exist.

    `principal` is the shelf owner (`requested_by`): the question the shelf answers
    is "what is this principal waiting on". The **author** is always
    `"approver"` — the approval desk, not the caller — which is what the store's
    kind-specific policy checks.
    """
    return store.put(principal, APPROVAL_KIND, approval_key(request_id), payload,
                     created_by={"run_id": run_id or "approval",
                                 "step": str((payload or {}).get("state")
                                             or "unknown"),
                                 "source": APPROVAL_AUTHOR_SOURCE},
                     ttl=ttl)


def list_approvals(store: StateStore, principal: str) -> list[StateRecord]:
    """The principal's own approval shelf, live records only (`StateStore`'s rules).

    The approval desk reads the raw `records` mapping instead, because an *expired*
    request is a coded decision it must still be able to name; this helper is the
    ordinary, expiry-respecting read for callers that just want the queue.
    """
    return store.list_for(principal, APPROVAL_KIND)
