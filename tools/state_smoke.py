"""Day 17 §8 — the state question set (S1–S9) against the scripted stub.

No API key, no real network. Reuses Day 11's offline registry (the Day-7 mini doc
corpus + the Day-10 code tree + the Day-14 seeded data store) so retrieval stays
byte-identical to the harness Days 7–16 grade, and drives the Day-17 **state store**
through the loop (`run_agent(state=…)`) and the executor (`run_plan(state_store=…)`):

    $ python tools/state_smoke.py

`MECH` here: the store (put/get/list_for/recall/delete/forget/expire/compact), the
**closed write path** (provenance mandatory — a write with no author, or a foreign
source, is rejected at write time), **TTL/expiry** (an expired record acts absent;
compaction reclaims it), **first-class deletion** (`forget` removes the record *and*
the derived index), the **serialization contract** (`put → dump → load → get` is
byte-identical), **resume across a process** (the file survives; a fresh store
continues at step *k+1*), the **context boundary** (a cross-owner read returns
nothing), and the **S9 off-switch** (state disabled ≡ Days 12–16, and the Days 7–16
suites re-run unchanged in subprocesses). The headline is **`recalled ⊆ authored`**
on every row that recalls; `PENDING`: whether a *real* model's relevance-scored
recall of episodic records, and its distillation of grounded semantic facts, makes
answers better — Day 18's system and Day 21's verdict on this store.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STUB = Path(__file__).resolve().parent / "mock_gateway.py"
sys.path.insert(0, str(ROOT))

from ai_os.agent import run_agent  # noqa: E402
from ai_os.executor import run_plan  # noqa: E402
from ai_os.planner import Plan  # noqa: E402
from ai_os.state import (  # noqa: E402
    CONVERSATION_KIND,
    STATE_OFF,
    STATE_ON,
    TASK_KIND,
    ExecutionState,
    ProvenanceError,
    StateStore,
    conversation_key,
    load_task_state,
    task_key,
    working_state,
)
from ai_os.trace import recalled_subset_authored, state_columns  # noqa: E402

from tools.tool_smoke import (  # noqa: E402
    DOC_TAU,
    CODE_TAU,
    _stub_up,
    build_registry,
    make_gateway,
    reset,
)

FAILS = 0
STATE_ROWS: list[dict] = []


def check(label: str, cond: bool, detail: str = ""):
    global FAILS
    if not cond:
        FAILS += 1
    print(f"[{'OK  ' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


def record(fid: str, *, kind: str, written: str, stored_as: str, recalled: str,
           provenance: str, ttl: str, expired: bool, deleted: bool, resume: bool,
           cost: float = 0.0, wall_ms: float = 0.0, owner: str = "—",
           ok: bool = True) -> None:
    STATE_ROWS.append({
        "id": fid, "kind": kind, "written": written, "stored_as": stored_as,
        "recalled": recalled, "provenance": provenance, "ttl": ttl,
        "expired": expired, "deleted": deleted, "resume": resume,
        "cost": cost, "wall_ms": wall_ms, "owner": owner, "ok": ok,
    })


class Clock:
    """Injectable wall-clock so TTL arithmetic is deterministic (no real sleeps).

    The base is *real* time, so a record's `expires_at` is honestly in the future
    for any other process reading the same file — the injected clock only moves
    forward deliberately (S5's TTL math), it never rewrites the epoch.
    """

    def __init__(self, start: float | None = None) -> None:
        self.now = time.time() if start is None else start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def tmp_store(clock: Clock, *, default_ttl=None, max_records=None,
              principal: str = "default") -> tuple[StateStore, str]:
    fd, path = tempfile.mkstemp(prefix="state-d17-", suffix=".json")
    os.close(fd)
    os.unlink(path)
    store = StateStore(path, default_ttl=default_ttl, max_records=max_records,
                       principal=principal, clock=clock)
    return store, path


# ------------------------------------------------------------------ fixtures
TWO_STEP = {
    "question": "Where is checkout handled, and what is order 1?",
    "steps": [
        {"id": "s1", "tool": "search_saleor_code",
         "args": {"query": "where is checkout completed", "k": 4},
         "expected": "the checkout mutation/service chunks"},
        {"id": "s2", "tool": "get_order", "args": {"id": "1"},
         "expected": "order 1's status and lines"},
    ],
}


def stored(raw: dict, *, source: str = "stored") -> Plan:
    return Plan.from_dict(raw, source=source)


class CountingRegistry:
    """Counts *execution* — the proof a resume does not re-run a completed step."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.executed: list[str] = []

    def execute(self, name, args):
        self.executed.append(name)
        return self.inner.execute(name, args)

    def __getattr__(self, key):
        return getattr(self.inner, key)


# ============================================================ S1 — working state
def s1_working_state(reg, gw):
    print("\n== S1 — working state: turn 1's plan position + hypothesis served at turn 2")
    clock = Clock()
    store, path = tmp_store(clock, principal="w1")
    plan = stored(TWO_STEP)
    st0 = ExecutionState.fresh(plan.question, plan)
    st0.hypothesis = "checkout likely blocked on the order's payment state"

    reset(openai=["tc_code"])
    turn1 = run_plan(plan, reg, gw, max_replans=0, state=st0, stop_after=1,
                     state_store=store, principal="w1", task_id="t1", run_id="r1")
    rec = store.get("w1", TASK_KIND, task_key("t1"))
    print(f"    turn 1: {turn1['termination_reason']} resume_from={turn1['resume_from']}")

    # Turn 2 reads the working shelf — plan position + hypothesis — not a re-plan.
    payload = load_task_state(store, "w1", "t1")
    ws = working_state(payload)
    check("turn 1 flushed the plan position to the store (one task record)",
          rec is not None and rec.key == "/task/t1", str(rec.key if rec else None))
    check("the plan is at step 2 tomorrow (`resume_from` served, not re-derived)",
          ws["resume_from"] == 2 and turn1["resume_from"] == 2, str(ws["resume_from"]))
    check("the running hypothesis survived the turn (working state, cold)",
          ws["hypothesis"] == st0.hypothesis, repr(ws["hypothesis"]))
    check("the completed step's status is on the shelf (s1 done)",
          any(s["id"] == "s1" and s["status"] == "done" for s in ws["steps"]),
          str([(s["id"], s["status"]) for s in ws["steps"]]))
    check("provenance reached the reader (`recalled ⊆ authored`)",
          rec.created_by["source"] == "executor" and rec.created_by["run_id"] == "r1",
          str(rec.created_by))
    check("the working shelf reads back as authored",
          recalled_subset_authored([rec]))
    record("S1", kind="task", written="plan pos + hypothesis", stored_as="record",
           recalled="y", provenance=rec.provenance(), ttl=f"{rec.ttl:g}",
           expired=False, deleted=False, resume=False,
           cost=turn1["ledger"]["cost_usd"], wall_ms=turn1["elapsed_ms"],
           ok=recalled_subset_authored([rec]))
    os.unlink(path)
    return turn1


# ======================================================= S2 — conversation state
def s2_conversation(reg, gw):
    print("\n== S2 — conversation state: the transcript persists per session/principal")
    clock = Clock()
    store, path = tmp_store(clock, principal="u1")

    reset(openai=["tc_order", "answer_d14_orders"])
    t1 = run_agent("Fetch order 1", reg, gw, state=store, principal="u1",
                   session_id="conv-1", run_id="r1")
    rec = store.get("u1", CONVERSATION_KIND, conversation_key("conv-1"))
    check("the exchange persisted as conversation state on a shelf",
          rec is not None and len(rec.payload["turns"]) == 2, str(rec.payload)[:80])
    check("the session is a key/header, not a hidden variable",
          rec.payload["session"] == "conv-1" and rec.created_by["source"] == "session",
          str(rec.created_by))

    reset(openai=["ok"])
    t2 = run_agent("what did I ask about earlier?", reg, gw, state=store,
                   principal="u1", session_id="conv-1", run_id="r2")
    check("turn 2 loaded the prior exchange (answered without re-asking)",
          t2["conversation_turns"] == 2, f"prior={t2['conversation_turns']}")
    check("turn 2 stayed attributed to the same conversation (same session key)",
          store.get("u1", CONVERSATION_KIND, conversation_key("conv-1"))
          .payload["turns"][0]["content"] == "Fetch order 1")
    check("a different session starts empty (no bleed)",
          run_agent("hi", reg, gw, state=store, principal="u1",
                    session_id="conv-2", run_id="r3")["conversation_turns"] == 0)
    check("a different principal cannot read the session (boundary)",
          store.get("u2", CONVERSATION_KIND, conversation_key("conv-1")) is None)
    rec2 = store.get("u1", CONVERSATION_KIND, conversation_key("conv-1"))
    record("S2", kind="session", written="transcript delta", stored_as="record",
           recalled="y", provenance=rec2.provenance(), ttl=f"{rec2.ttl:g}",
           expired=False, deleted=False, resume=False,
           cost=t1["ledger"]["cost_usd"], wall_ms=0.0, ok=recalled_subset_authored([rec2]))
    os.unlink(path)
    return t1


# ============================================================= S3 — task resume
def s3_resume(reg, gw):
    print("\n== S3 — task resume: interrupt after step k → resume at k+1 from the store")
    clock = Clock()
    plan = stored(TWO_STEP)

    # (a) a genuinely NEW process resumes from the file — the whole point of durable
    # state: the only thing the new process inherits is the serialized store.
    store, path = tmp_store(clock, principal="p1")
    reset(openai=["tc_code"])
    first = run_plan(plan, reg, gw, max_replans=0, stop_after=1,
                     state_store=store, principal="p1", task_id="t2", run_id="r9")
    check("the interruption is a resumption point, not a termination (paused)",
          first["termination_reason"] == "paused" and first["checkpoint_idx"] == 1,
          f"{first['termination_reason']} idx={first['checkpoint_idx']}")
    store.save()                                    # the file is the survivor
    reset(openai=["tc_order", "answer_multistep"])
    child = subprocess.run(
        [sys.executable, str(Path(__file__)), "--resume-child", path,
         json.dumps(TWO_STEP), "p1", "t2"],
        cwd=str(ROOT), capture_output=True, text=True)
    line = [ln for ln in child.stdout.splitlines() if ln.startswith("{")]
    out = json.loads(line[-1]) if line else {}
    check("a NEW process resumed from the file at k+1 and grounded its answer",
          child.returncode == 0 and out.get("resumed_from_store") is True
          and out.get("tool_steps") == 1 and out.get("conclusion") is True,
          json.dumps(out) if out else (child.stderr or child.stdout)[-200:])

    # (b) and in this process, from a *fresh* store object — same code path, only
    # the file carried over.
    store2, path2 = tmp_store(clock, principal="p1")
    reset(openai=["tc_code"])
    paused = run_plan(plan, reg, gw, max_replans=0, stop_after=1,
                      state_store=store2, principal="p1", task_id="t2", run_id="r9")
    counting = CountingRegistry(reg)
    fresh = StateStore(path2, clock=clock)
    reset(openai=["tc_order", "answer_multistep"])
    second = run_plan(plan, counting, gw, max_replans=0, state_store=fresh,
                      principal="p1", task_id="t2", run_id="r10",
                      resume_from_store=True)
    check("the fresh store recalled the task record (provenance reached the reader)",
          second["resumed_from_store"] is True, str(second["resumed_from_store"]))
    check("resume executed only the remaining step (the completed step is not re-run)",
          counting.executed == ["get_order"], str(counting.executed))
    check("resume continued at k+1 (one new step, two checkpoints total)",
          second["tool_steps"] == 1 and second["checkpoint_idx"] == 2,
          f"steps={second['tool_steps']} idx={second['checkpoint_idx']}")
    check("the resumed run kept the earlier step's evidence (the checkpoint carried it)",
          set(paused["evidence"]) <= set(second["evidence"]),
          str(sorted(set(paused['evidence']) - set(second['evidence']))))
    check("the resumed run reached a grounded answer", second["conclusion_subset_evidence"])
    rec = store2.get("p1", TASK_KIND, task_key("t2"))
    record("S3", kind="task", written="checkpoint k", stored_as="record",
           recalled="y", provenance=rec.provenance(), ttl=f"{rec.ttl:g}",
           expired=False, deleted=False, resume=True,
           cost=second["ledger"]["cost_usd"], wall_ms=second["elapsed_ms"],
           ok=recalled_subset_authored([rec]))
    os.unlink(path)
    os.unlink(path2)
    return second


# ====================================================== S4 — serialize / replay
def s4_serialize_replay(reg, gw):
    print("\n== S4 — serialize/replay: byte-identical round-trip; replay reproduces evidence")
    clock = Clock()
    store, path = tmp_store(clock, principal="p2")
    plan = stored(TWO_STEP)

    reset(openai=["tc_code", "tc_order", "answer_multistep"])
    run_plan(plan, reg, gw, max_replans=0, state_store=store, principal="p2",
             task_id="t3", run_id="r11")
    rt = store.round_trip("p2", TASK_KIND, task_key("t3"))
    check("put → dump → load → get returns a byte-identical payload",
          rt is not None and rt["identical"] is True, f"identical={rt and rt['identical']}")

    reset(openai=["tc_code", "tc_order", "answer_multistep"])
    replay = run_plan(plan, reg, gw, max_replans=0)
    reset(openai=["tc_code", "tc_order", "answer_multistep"])
    original = run_plan(plan, reg, gw, max_replans=0)
    check("a replay from step 0 reproduces the same evidence (deterministic)",
          set(replay["evidence"]) == set(original["evidence"]),
          str(sorted(set(replay['evidence']) ^ set(original['evidence']))))
    rec = store.get("p2", TASK_KIND, task_key("t3"))
    record("S4", kind="task", written="full run", stored_as="file",
           recalled="y", provenance=rec.provenance(), ttl=f"{rec.ttl:g}",
           expired=False, deleted=False, resume=True,
           wall_ms=replay["elapsed_ms"], ok=bool(rt and rt["identical"]))
    os.unlink(path)
    return replay


# ========================================================== S5 — retention
def s5_retention(reg, gw):
    print("\n== S5 — retention: expired acts absent; compaction reclaims; hostile TTL fires")
    clock = Clock()
    store, path = tmp_store(clock, default_ttl=10.0, principal="r1")
    store.put("r1", TASK_KIND, "/task/e1", {"v": 1},
              created_by={"run_id": "r", "step": "s1", "source": "executor"})
    check("premise: the record is present before expiry",
          store.get("r1", TASK_KIND, "/task/e1") is not None)
    clock.advance(11.0)
    check("an expired record acts ABSENT through get", store.get("r1", TASK_KIND, "/task/e1") is None)
    check("...and through list_for", store.list_for("r1", TASK_KIND) == [])
    check("...and through recall", store.recall("r1", TASK_KIND) == [])
    check("expiry is countable before compaction", store.expire() == 1, str(store.expire()))
    removed = store.compact()
    check("compaction physically reclaims the expired record",
          removed == 1 and len(store) == 0, f"removed={removed} len={len(store)}")

    # Hostile config: a tiny TTL must show the shelf emptying (eviction by time).
    hostile = Clock()
    hstore, hpath = tmp_store(hostile, default_ttl=1.0, principal="h1")
    for i in range(5):
        hostile.advance(0.5)
        hstore.put("h1", "working", f"/w/{i}", i,
                   created_by={"run_id": "r", "step": i, "source": "agent"})
    hostile.advance(10.0)
    check("hostile tiny-TTL: every record expires (the shelf empties)",
          hstore.expire() == 5 and len(hstore) == 5, f"expired={hstore.expire()}")
    hstore.compact()
    check("...and compaction leaves an empty store", len(hstore) == 0)

    # Hostile config: max_records bounds the store (oldest evicted).
    cstore, cpath = tmp_store(Clock(), max_records=2, principal="c1")
    cstore.put("c1", "working", "/w/0", 0,
               created_by={"run_id": "r", "step": 0, "source": "agent"})
    cstore.put("c1", "working", "/w/1", 1,
               created_by={"run_id": "r", "step": 1, "source": "agent"})
    cstore.put("c1", "working", "/w/2", 2,
               created_by={"run_id": "r", "step": 2, "source": "agent"})
    check("the size cap evicts the oldest record (`max_records`)",
          len(cstore) == 2 and cstore.get("c1", "working", "/w/0") is None,
          f"len={len(cstore)} evicted={cstore.evicted}")

    record("S5", kind="task", written="expired record", stored_as="(absent)",
           recalled="n", provenance="run/s1/executor", ttl="past", expired=True,
           deleted=False, resume=False, ok=True)
    os.unlink(path)
    os.unlink(hpath)
    os.unlink(cpath)


# =========================================================== S6 — deletion
def s6_deletion(reg, gw):
    print("\n== S6 — deletion: forget removes the record AND its derived aggregate")
    clock = Clock()
    store, path = tmp_store(clock, principal="d1")
    store.put("d1", TASK_KIND, "/task/x", {"v": 1},
              created_by={"run_id": "r", "step": "s1", "source": "executor"})
    store.put("d1", CONVERSATION_KIND, "/conversation/x", {"turns": []},
              created_by={"run_id": "r", "step": 1, "source": "session"})
    check("premise: the record recalls before deletion", len(store.recall("d1")) == 2)
    removed = store.forget("d1", "/task/x")
    check("forget removed the record", removed == 1 and store.get("d1", TASK_KIND, "/task/x") is None)
    check("...it is unreachable through get/list_for", store.list_for("d1", TASK_KIND) == [])
    check("...and unreachable through recall (the derived index was purged)",
          all(r.key != "/task/x" for r in store.recall("d1")),
          str([r.key for r in store.recall("d1")]))
    check("delete() confirms the record is gone (not an omission)",
          store.delete("d1", TASK_KIND, "/task/x") is False)
    check("the rest of the store is untouched", len(store) == 1, str(len(store)))
    trace = state_columns(enabled=True, principal="d1", records=store.recall("d1"),
                          deleted=True)
    check("deletion is visible in the trace, not silent", trace["deleted"] is True)
    record("S6", kind="session", written="forget()", stored_as="(gone)",
           recalled="n", provenance="run/1/session", ttl="—", expired=False,
           deleted=True, resume=False, ok=True)
    os.unlink(path)


# ========================================================== S7 — provenance
def s7_provenance(reg, gw):
    print("\n== S7 — provenance / recalled ⊆ authored: the write path is closed")
    clock = Clock()
    store, path = tmp_store(clock, principal="a1")

    try:
        store.put("a1", TASK_KIND, "/task/np", {"v": 1}, created_by=None)
        no_prov = False
    except ProvenanceError:
        no_prov = True
    check("a write with no created_by is REJECTED at write time (fail-closed)", no_prov)

    try:
        store.put("a1", TASK_KIND, "/task/imp", {"v": 1},
                  created_by={"run_id": "r", "step": 1, "source": "import"})
        foreign = False
    except ProvenanceError:
        foreign = True
    check("a write from a foreign source (an import) is rejected — the store is closed",
          foreign)

    # A record planted in the file with no provenance is not admitted → unreachable.
    store.put("a1", TASK_KIND, "/task/ok", {"v": 1},
              created_by={"run_id": "r", "step": 1, "source": "executor"})
    raw = json.loads(store.dump())
    raw["records"].append({"kind": TASK_KIND, "principal": "a1", "key": "/task/ghost",
                           "payload": {"fact": "injected"}, "created_at": 1.0})
    raw["records"].append({"kind": TASK_KIND, "principal": "a1", "key": "/task/ghost2",
                           "payload": {"fact": "imported"}, "created_at": 1.0,
                           "created_by": {"run_id": "z", "step": 1, "source": "import"}})
    fresh = StateStore(None, clock=clock, autosave=False)
    fresh._load_text(json.dumps(raw))
    check("a runtime-imported record is not admitted (unreachable through get)",
          fresh.get("a1", TASK_KIND, "/task/ghost") is None
          and fresh.get("a1", TASK_KIND, "/task/ghost2") is None,
          f"dropped={fresh.dropped}")
    check("...and not recallable/listable either",
          all(r.key != "/task/ghost" for r in fresh.list_for("a1", TASK_KIND))
          and all(r.key != "/task/ghost" for r in fresh.recall("a1")))
    check("the authored record is admitted and recallable (`recalled ⊆ authored`)",
          fresh.get("a1", TASK_KIND, "/task/ok") is not None
          and recalled_subset_authored(fresh.recall("a1")))
    rec = store.get("a1", TASK_KIND, "/task/ok")
    trace = state_columns(enabled=True, principal="a1", records=store.recall("a1"))
    check("every recalled record carries run/step/source provenance",
          bool(trace["provenance"]) and trace["recalled_subset_authored"] is True,
          str(trace["provenance"]))
    record("S7", kind="task", written="no created_by / import", stored_as="rejected",
           recalled="n", provenance="—", ttl="—", expired=False, deleted=False,
           resume=False, ok=trace["recalled_subset_authored"])
    os.unlink(path)


# =========================================================== S8 — boundary
def s8_boundary(reg, gw):
    print("\n== S8 — boundary: a cross-owner read returns nothing (no bleed)")
    clock = Clock()
    store, path = tmp_store(clock)
    store.put("ownerA", TASK_KIND, "/task/a", {"v": "A"},
              created_by={"run_id": "ra", "step": 1, "source": "executor"})
    store.put("ownerB", TASK_KIND, "/task/b", {"v": "B"},
              created_by={"run_id": "rb", "step": 1, "source": "executor"})
    check("owner A cannot get owner B's record", store.get("ownerA", TASK_KIND, "/task/b") is None)
    check("owner A's list_for excludes B's shelf",
          [r.key for r in store.list_for("ownerA", TASK_KIND)] == ["/task/a"])
    check("owner A's recall excludes B's records",
          all(r.principal == "ownerA" for r in store.recall("ownerA")))
    check("an unknown owner reads nothing at all",
          store.list_for("nobody") == [] and store.recall("nobody") == [])
    record("S8", kind="task", written="cross-owner read", stored_as="(none)",
           recalled="n", provenance="—", ttl="—", expired=False, deleted=False,
           resume=False, ok=True)
    os.unlink(path)


# =========================================================== S9 — control
def s9_control(reg, gw):
    print("\n== S9 — control: with state disabled the system is Days 12–16 unchanged")
    clock = Clock()
    store, path = tmp_store(clock)

    # A store that is off writes nothing and recalls nothing.
    off = StateStore(path, clock=clock, enabled=False)
    off.put("x", TASK_KIND, "/task/off", {"v": 1},
            created_by={"run_id": "r", "step": 1, "source": "executor"})
    check("a disabled store writes nothing (len 0, no file)",
          len(off) == 0 and off.get("x", TASK_KIND, "/task/off") is None
          and off.list_for("x") == [] and off.recall("x") == [])
    check("a disabled store reports the OFF switch",
          state_columns(enabled=False)["state_on_off"] == STATE_OFF)

    # The loop with no store is Days 12–16: same exits, no state columns changed.
    reset(openai=["tc_order", "answer_d14_orders"])
    off_run = run_agent("Fetch order 1", reg, gw)
    reset(openai=["tc_order", "answer_d14_orders"])
    baseline = run_agent("Fetch order 1", reg, gw)
    check("state off ≡ Days 12–16: identical answer/evidence/exit",
          off_run["answer"] == baseline["answer"]
          and set(off_run["evidence"]) == set(baseline["evidence"])
          and off_run["termination_reason"] == baseline["termination_reason"],
          f"{off_run['termination_reason']}")
    check("with no store the run reports state off (and nothing was persisted)",
          off_run["state_on_off"] == STATE_OFF and not os.path.exists(path),
          str(off_run["state_on_off"]))
    record("S9", kind="—", written="state off", stored_as="—", recalled="—",
           provenance="—", ttl="—", expired=False, deleted=False, resume=False,
           ok=off_run["state_on_off"] == STATE_OFF)


# ---------------------------------------------------------------- regression
SUITES = ["test_day7.py", "test_day8.py", "test_day9.py", "test_day10.py",
          "tool_smoke.py", "agent_smoke.py", "failure_smoke.py", "data_smoke.py",
          "sql_attack_smoke.py", "plan_smoke.py"]


def no_regression():
    print("\n== Day 17 — no regression: Days 7–16 re-run unchanged through the store")
    for name in SUITES:
        script = (ROOT / name) if name.startswith("test_") else (ROOT / "tools" / name)
        proc = subprocess.run([sys.executable, str(script)], cwd=str(ROOT),
                              capture_output=True, text=True)
        tail = ""
        for line in reversed(proc.stdout.splitlines()):
            if line.strip():
                tail = line.strip()
                break
        check(f"{name} still exits 0", proc.returncode == 0, tail)
        if proc.returncode != 0:
            print(proc.stdout[-2000:])


# ------------------------------------------------------------------- reporting
def render():
    print("\n" + "=" * 132)
    print("   DAY 17 — THE STORE AS A CONTRACT (§9 state table; recalled ⊆ authored on every row)")
    print("=" * 132)
    print(f"{'id':<6}{'kind':<13}{'state written':<22}{'stored as':<12}{'recalled':<10}"
          f"{'provenance':<22}{'ttl':<8}{'expired':<9}{'deleted':<9}{'resume':<8}"
          f"{'recalled⊆authored':<18}cost")
    print("-" * 132)
    for r in STATE_ROWS:
        print(f"{r['id']:<6}{str(r['kind']):<13}{r['written']:<22}{r['stored_as']:<12}"
              f"{r['recalled']:<10}{r['provenance']:<22}{r['ttl']:<8}"
              f"{'y' if r['expired'] else 'no':<9}{'y' if r['deleted'] else 'no':<9}"
              f"{'y' if r['resume'] else '—':<8}"
              f"{'ok' if r['ok'] else 'FAIL':<18}${r['cost']:.8f}")
    print("-" * 132)
    bad = [r["id"] for r in STATE_ROWS if not r["ok"]]
    print(f"recalled ⊆ authored = ok on every row that recalls: "
          f"{'YES' if not bad else 'NO — ' + ', '.join(bad)}")
    print("Every row is 0 chats / 0 embeds of *state*: the store is local I/O — one JSON "
          "append per checkpoint. The premium is latency and disk, not money.")
    print("The S9 row is the counterweight: state off ≡ Days 12–16 (Days 7–16's suites "
          "re-run unchanged below).")


# ============================================================ resume child (S3)
def resume_child(argv: list[str]) -> int:
    """A *new process* that resumes a task from the store file and prints its verdict."""
    path, plan_json, principal, task_id = argv
    reg, _gd, _gc, _ds, _cs = build_registry()
    gw = make_gateway()
    store = StateStore(path)
    plan = stored(json.loads(plan_json))
    res = run_plan(plan, reg, gw, max_replans=0, state_store=store,
                   principal=principal, task_id=task_id, run_id="r-child",
                   resume_from_store=True)
    print(json.dumps({"resumed_from_store": res["resumed_from_store"],
                      "tool_steps": res["tool_steps"],
                      "conclusion": res["conclusion_subset_evidence"],
                      "termination": res["termination_reason"]}))
    return 0


# --------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--resume-child", nargs=4, metavar=("STORE", "PLAN", "PRINCIPAL", "TASK"),
                    help="internal: resume a task from a store file in a new process")
    args = ap.parse_args()
    if args.resume_child:
        sys.exit(resume_child(args.resume_child))

    proc = None
    try:
        if not _stub_up():
            proc = subprocess.Popen([sys.executable, str(STUB)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(100):
                if _stub_up():
                    break
                time.sleep(0.1)
            if not _stub_up():
                raise SystemExit("stub did not come up")

        reg, gw_doc, gw_code, doc_store, code_store = build_registry()
        gw = make_gateway()
        print(f"stores: {len(doc_store)} doc chunks (STUB) / {len(code_store)} "
              f"code symbols (STUB)   τ_doc={DOC_TAU} τ_code={CODE_TAU}   "
              f"tools: {', '.join(reg.names())}")
        print(f"state: STATE_DEFAULT_TTL={os.getenv('STATE_DEFAULT_TTL', '86400 (default)')} "
              f"STATE_MAX_RECORDS={os.getenv('STATE_MAX_RECORDS', '1000 (default)')} "
              f"STATE_PRINCIPAL={os.getenv('STATE_PRINCIPAL', 'default')}")
        s1_working_state(reg, gw)
        s2_conversation(reg, gw)
        s3_resume(reg, gw)
        s4_serialize_replay(reg, gw)
        s5_retention(reg, gw)
        s6_deletion(reg, gw)
        s7_provenance(reg, gw)
        s8_boundary(reg, gw)
        s9_control(reg, gw)
        no_regression()
    finally:
        if proc is not None:
            proc.terminate()

    render()
    print("\n" + ("ALL DAY 17 (MECH) CHECKS PASSED" if not FAILS
                  else f"{FAILS} CHECK(S) FAILED"))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
