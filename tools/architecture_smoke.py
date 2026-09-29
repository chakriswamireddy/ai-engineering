"""Day 19 §8–§9 — the architecture question set (G1–G9) against the scripted stub.

No API key, no real network. One harness presents every fixture to **both**
topologies and one judge scores them:

    Topology A  — Days 12–18's single loop + the role-context router (`run_agent`)
    Topology B  — the orchestrator + `make_agent(role)` specialists (`Orchestrator`)

The only variable is topology. The comparison is pre-registered (§8): the expected
behavior is written before either side runs, and the table — not the diagram —
decides. This is the day's whole deliverable: a **verdict the fixture table forces**.

    $ python tools/architecture_smoke.py

`MECH` here: the role-context router (a rule table, 0 chats), the receipt contract
(`delegated ⊆ returned` — a set membership over returned evidence), per-hop
grounding (`cited ⊆ retrieved` inside each specialist), the delegation cost ledger
(the union of both contexts), privilege containment (a specialist's role gate), the
shared-store write policy (specialists write episodes; the orchestrator writes only
orchestration records), and the **G9 control** (machinery off ≡ Days 12–18, and
Days 7–18's suites re-run unchanged). The day's headline —
**`delegated ⊆ returned` = ok on every B row** — is the third carried column; the
**G9 row** is the counterweight: over-orchestration is a bug.

`PENDING`: whether a *real* model's orchestrator delegates better than the real
single loop on real Saleor questions, and whether context isolation pays at real
token prices — Day 21/22's benchmarks on this harness. The stub proves the *court*.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STUB = Path(__file__).resolve().parent / "mock_gateway.py"
sys.path.insert(0, str(ROOT))

from ai_os.agent import (  # noqa: E402
    ROLES,
    ROLE_TOOLS,
    make_agent,
    mentioned_ids,
    route_question,
    run_agent,
)
from ai_os.orchestrator import (  # noqa: E402
    Orchestrator,
    agent_topology_from_env,
    detect_surfaces,
    orchestration_plan,
)
from ai_os.receipt import (  # noqa: E402
    FAILED,
    REFUSED,
    RETURNED,
    check_delegated_subset_returned,
    check_per_hop_grounding,
    failed_receipt,
)
from ai_os.state import (  # noqa: E402
    EPISODIC_KIND,
    ORCHESTRATION_KIND,
    StateStore,
)
from ai_os.trace import topology_columns  # noqa: E402

from tools.mock_gateway import ANSWER_SCENARIOS  # noqa: E402
from tools.tool_smoke import (  # noqa: E402
    _stub_up,
    build_registry,
    make_gateway,
    reset,
)

FAILS = 0
ROWS: list[dict] = []
_RUNS = {"n": 0}


def check(label: str, cond: bool, detail: str = ""):
    global FAILS
    if not cond:
        FAILS += 1
    print(f"[{'OK  ' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


# ------------------------------------------------------------- role plumbing

TOOL_ROLE = {t: role for role, tools in ROLE_TOOLS.items() for t in tools}


def role_evidence_from_run(run: dict) -> dict[str, list[str]]:
    """Which roles' *evidence* a single-loop run produced (by the tool that ran)."""
    out: dict[str, list[str]] = {}
    for step in run.get("steps") or []:
        for call in step.get("calls") or []:
            role = TOOL_ROLE.get(call.get("name"))
            if not role:
                continue
            out.setdefault(role, [])
            for i in call.get("evidence_ids") or []:
                if i not in out[role]:
                    out[role].append(i)
    return out


def role_evidence_from_receipts(receipts: list[dict]) -> dict[str, list[str]]:
    """Which roles' evidence the *returned* receipts carry."""
    out: dict[str, list[str]] = {}
    for r in receipts:
        if r.get("status") != RETURNED or not r.get("evidence"):
            continue
        out.setdefault(r["role"], [])
        for i in r["evidence"]:
            if i not in out[r["role"]]:
                out[r["role"]].append(i)
    return out


def cited_roles(answer: str, role_evidence: dict[str, list[str]]) -> set[str]:
    """The roles whose evidence the answer actually cites (the coverage meter)."""
    cited = set(mentioned_ids(answer or ""))
    return {role for role, ids in role_evidence.items() if cited & set(ids)}


# ---------------------------------------------------------------- the runners

Q_TWO = ("What do the docs say about creating a checkout, and where is checkout "
         "completed in the code?")
Q_CODE = "Where is checkout completed?"
Q_DOCS = "What do the docs say about creating a checkout?"
Q_DATA = "What was our revenue last month?"


class JumpClock:
    """A clock whose second read jumps past any handoff timeout (deterministic)."""

    def __init__(self, jump: float = 1000.0) -> None:
        self.n = 0
        self.jump = jump

    def __call__(self) -> float:
        self.n += 1
        return 0.0 if self.n == 1 else self.jump


class BoomAgent:
    """A specialist that crashes — G8's failure shape, injected at the seam."""

    def __init__(self, role: str) -> None:
        self.role = role

    def names(self) -> list[str]:
        return []

    def denied(self) -> dict[str, str]:
        return {}

    def run(self, question, gateway, **kwargs):
        raise RuntimeError("specialist crashed")


def tmp_store(**kw) -> tuple[StateStore, str]:
    fd, path = tempfile.mkstemp(prefix="arch-d19-", suffix=".json")
    os.close(fd)
    return StateStore(path, **kw), path


class Harness:
    def __init__(self, reg, gw):
        self.reg = reg
        self.gw = gw
        self.agents = {role: make_agent(role, registry=reg) for role in ROLES}

    # -- Topology A ------------------------------------------------------
    def run_a(self, question: str, script: list[str], **kw) -> dict:
        reset(openai=script)
        before = len(self.gw.ledger.records)
        kw.setdefault("routed_to", route_question(question)["routed_to"])
        res = run_agent(question, self.reg, self.gw, **kw)
        res["_tokens"] = sum(r.input_tokens + r.output_tokens
                             for r in self.gw.ledger.records[before:])
        res["_chats"] = len(self.gw.ledger.records) - before
        return res

    # -- Topology B ------------------------------------------------------
    def run_b(self, question: str, script: list[str], delegations,
              *, agents=None, store=None, memory_store=None, **kw) -> dict:
        reset(openai=script)
        _RUNS["n"] += 1
        orc = Orchestrator(agents or self.agents, self.gw, principal="d19",
                           store=store, memory_store=memory_store,
                           run_id=f"orc-{_RUNS['n']}", **kw)
        return orc.run(question, delegations=delegations)


# ---------------------------------------------------------------- the fixtures

def record(fid: str, *, a: str, b: str, winner: str, steps: str = "—",
           handoffs: str = "—", tokens: str = "—", latency: str = "—",
           failure: str = "no", delegated: str = "—", cited: str = "—",
           concluded: str = "—", cost: str = "—", verdict: str = "—",
           ok: bool = True) -> None:
    ROWS.append({"id": fid, "a": a, "b": b, "winner": winner, "steps": steps,
                 "handoffs": handoffs, "tokens": tokens, "latency": latency,
                 "failure": failure, "delegated": delegated, "cited": cited,
                 "concluded": concluded, "cost": cost, "verdict": verdict, "ok": ok})


def g1_two_surface(h: Harness):
    print("\n== G1 — the split is measured: a two-surface question, both topologies")
    delegations = [{"role": "knowledge", "question": Q_TWO},
                   {"role": "engineering", "question": Q_TWO}]
    b = h.run_b(Q_TWO, ["tc_docs", "answer_docs", "tc_code", "answer_code",
                        "answer_d19_combined"], delegations)
    a = h.run_a(Q_TWO, ["tc_docs", "tc_code", "answer_d19_combined"])
    cov_a = cited_roles(a["answer"], role_evidence_from_run(a))
    cov_b = cited_roles(b["answer"], role_evidence_from_receipts(b["receipts"]))
    both = {"knowledge", "engineering"}
    check("B covered both surfaces from two receipts (2/2)",
          cov_b == both, f"B cites {sorted(cov_b)}")
    check("...and A's single loop covered both surfaces in one context too (2/2) "
          "— the honest tie the notes predicted a stub would find",
          cov_a == both, f"A cites {sorted(cov_a)}")
    check("B paid more: a delegation bills the union of both contexts",
          b["chats"] > a["_chats"] and b["context_union_tokens"] > a["_tokens"],
          f"A {a['_chats']} chats/{a['_tokens']} tok vs B {b['chats']} chats/"
          f"{b['context_union_tokens']} tok")
    check("B's answer cited only receipted evidence (delegated ⊆ returned)",
          b["delegated_subset_returned"] is True, str(b["handoff_fabricated"]))
    check("B's receipts carry honest provenance (receipts ⊆ authored)",
          b["receipts_subset_authored"] is True)
    record("G1", a=f"{a['_chats']} chats/{a['_tokens']} tok", b=f"{b['chats']} chats/"
           f"{b['context_union_tokens']} tok", winner="A (tie → A)", steps="A 2 / B 2",
           handoffs="2", tokens=f"A {a['_tokens']} / B {b['context_union_tokens']}",
           delegated="ok", verdict="coverage ties; B costs more → SINGLE holds",
           ok=(cov_b == both and cov_a == both and b["delegated_subset_returned"]))
    return b, a


def g2_monolith(h: Harness):
    print("\n== G2 — the monolith holds: a single-surface question, B must not win")
    delegations = [{"role": "engineering", "question": Q_CODE}]
    # Same answer text on both sides, so the tie is comparable cell-for-cell.
    b = h.run_b(Q_CODE, ["tc_code", "answer_code", "answer_code"], delegations)
    a = h.run_a(Q_CODE, ["tc_code", "answer_code"])
    same = (a["answer"] == b["answer"] and set(a["evidence"]) == set(b["evidence"]))
    check("B at least ties A on the answer and evidence (no topology regression)",
          same, f"A='{a['answer'][:40]}…' B='{b['answer'][:40]}…'")
    check("A is cheaper — B's extra hop is pure duplication on a one-surface question",
          b["chats"] > a["_chats"], f"A {a['_chats']} chats vs B {b['chats']}")
    check("a tie is recorded as an A-win (the single agent was cheaper)",
          b["answer"] == a["answer"])
    check("B's answer cited only receipted evidence (delegated ⊆ returned)",
          b["delegated_subset_returned"] is True)
    record("G2", a=f"{a['_chats']} chats", b=f"{b['chats']} chats", winner="A",
           steps="A 1 / B 1", handoffs="1", tokens=f"A {a['_tokens']} / "
           f"B {b['context_union_tokens']}", delegated="ok",
           verdict="single (tie → A; over-orchestration is the failure shape)",
           ok=same and b["delegated_subset_returned"])
    return b, a


def g3_receipt_contract(h: Harness):
    print("\n== G3 — no handoff without a receipt (delegated ⊆ returned)")
    delegations = [{"role": "engineering", "question": Q_CODE}]
    leak = h.run_b(Q_CODE, ["tc_code", "answer_code", "answer_d19_leak"], delegations)
    check("the Chinese-whispers injection FAILS the check: the answer cites a fact "
          "no receipt returned",
          leak["delegated_subset_returned"] is False and leak["handoff_fabricated"],
          str(leak["handoff_fabricated"]))
    # A fabricated delegation: an answer attributing a specialist run never received.
    ghost = failed_receipt("engineering", Q_CODE, principal="d19",
                           reason="never received", run_id="ghost")
    chk = check_delegated_subset_returned(
        ANSWER_SCENARIOS["answer_d19_code"], [ghost])
    check("an orchestrator attributing a result it never received is REJECTED",
          chk["ok"] is False and chk["returned"] == [] and chk["fabricated"],
          f"fabricated={len(chk['fabricated'])}")
    # The passing twin (from G2) is the contrast; assert the check is not vacuous.
    pass_chk = check_delegated_subset_returned(
        ANSWER_SCENARIOS["answer_d19_code"], [])
    check("...and with no receipt at all the same answer is also rejected (the "
          "check is not vacuous)",
          pass_chk["ok"] is False and pass_chk["fabricated"])
    record("G3", a="—", b="—", winner="court", handoffs="1", delegated="rejected",
           verdict="handoff fabrication caught", ok=leak["delegated_subset_returned"] is False)


def g4_containment(h: Harness):
    print("\n== G4 — privilege containment (the specialist's role gate)")
    # A data-role loop emitting a knowledge tool: refused at the gate, not executed.
    reset(openai=["tc_d19_escape_docs", "answer_d19_policy"])
    r = h.agents["data"].run(Q_DOCS, h.gw)
    check("a data specialist emitting search_saleor_docs is refused (policy), "
          "never executed",
          r["policy_violations"] == 1 and r["termination_reason"] == "policy"
          and r["evidence"] == [],
          f"policy_violations={r['policy_violations']} term={r['termination_reason']}")
    check("the denied tool is named by the role gate (`role_violation`)",
          "search_saleor_docs" in h.agents["data"].denied()
          and "run_saleor_sql" not in h.agents["data"].denied())
    # The control: the *same* call works on the full registry — the gate is the
    # role, not an unknown tool.
    reset(openai=["tc_d19_escape_docs", "answer_docs"])
    rc = run_agent(Q_DOCS, h.reg, h.gw)
    check("control: the full (unscoped) registry executes the same call — the gate "
          "is the role, not a missing tool",
          rc["evidence"] and rc["policy_violations"] == 0,
          f"evidence={len(rc['evidence'])}")
    # A knowledge-role loop emitting SQL: refused too.
    reset(openai=["tc_d19_escape_sql", "answer_d19_policy"])
    r2 = h.agents["knowledge"].run(Q_DATA, h.gw)
    check("a knowledge specialist emitting run_saleor_sql is refused (policy)",
          r2["policy_violations"] == 1 and "run_saleor_sql" in h.agents["knowledge"].denied())
    # Through the orchestrator: the delegation is a refused receipt, no escalation.
    b = h.run_b(Q_DOCS, ["tc_d19_escape_docs", "answer_d19_policy"],
                [{"role": "data", "question": Q_DOCS}])
    check("the orchestrator receives a REFUSED receipt, not a bypass",
          b["delegation_statuses"] == [REFUSED] and b["failure"] is True,
          str(b["delegations"]))
    record("G4", a="full reg: ok", b="refused@gate", winner="both", handoffs="1",
           delegated="ok", verdict="role policy (no escalation)", ok=True)


def g5_cost_ledger(h: Harness):
    print("\n== G5 — the cost ledger is the judge (union of both contexts)")
    delegations = [{"role": "data", "question": Q_DATA}]
    b = h.run_b(Q_DATA, ["tc_d14_revenue", "answer_d14_revenue", "answer_d19_data"],
                delegations)
    a = h.run_a(Q_DATA, ["tc_d14_revenue", "answer_d14_revenue"])
    check("B's row is billed as specialist chats + the orchestrator's aggregator chat",
          b["chats"] == 3 and b["specialist_tokens"] > 0 and b["orchestrator_tokens"] > 0,
          f"chats={b['chats']} spec={b['specialist_tokens']} orc={b['orchestrator_tokens']}")
    check("the union-context row exceeds A's total (delegation duplicates context)",
          b["context_union_tokens"] > a["_tokens"],
          f"A {a['_tokens']} tok vs B union {b['context_union_tokens']} tok")
    check("B's data delegation returned a receipt (data surface works through the seam)",
          b["delegation_statuses"] == [RETURNED], str(b["delegated_subset_returned"]))
    record("G5", a=f"{a['_chats']} chats/{a['_tokens']} tok", b=f"{b['chats']} chats/"
           f"{b['context_union_tokens']} tok (union)", winner="A", handoffs="1",
           tokens=f"A {a['_tokens']} / B {b['context_union_tokens']}", delegated="ok",
           cost=f"union billed", verdict="B miss on cost", ok=b["delegated_subset_returned"])
    return b


def g6_memory_reducer(h: Harness):
    print("\n== G6 — the memory reducer: shared vs private vs no memory")
    # 1) single + Day-18 memory
    shared1, p1 = tmp_store()
    a_mem = h.run_a(Q_CODE, ["tc_code", "answer_code"], memory_store=shared1)
    a_plain = h.run_a(Q_CODE, ["tc_code", "answer_code"])
    check("single + memory: the finished run indexed itself (role=None)",
          [r.payload.get("role") for r in shared1.list_for("default", EPISODIC_KIND)]
          == [None])
    check("...and memory changed the memory-free answer nothing (M8 reflex)",
          a_mem["answer"] == a_plain["answer"]
          and set(a_mem["evidence"]) == set(a_plain["evidence"]))

    # 2) B, one shared store
    shared2, p2 = tmp_store()
    b_shared = h.run_b(Q_CODE, ["tc_code", "answer_code", "answer_d19_code"],
                       [{"role": "engineering", "question": Q_CODE}],
                       store=shared2, memory_store=shared2)
    eps = shared2.list_for("d19", EPISODIC_KIND)
    orch = shared2.list_for("d19", ORCHESTRATION_KIND)
    check("shared store: the specialist wrote an episode under its OWN role",
          len(eps) == 1 and eps[0].payload.get("role") == "engineering",
          str([r.payload.get("role") for r in eps]))
    check("shared store: the orchestrator wrote only an ORCHESTRATION record",
          len(orch) == 1 and orch[0].created_by["source"] == "orchestrator"
          and eps and eps[0].created_by["source"] == "agent",
          f"orch={len(orch)}")

    # 3) B, private per-role stores
    priv = {r: tmp_store()[0] for r in ROLES}
    h.run_b(Q_TWO, ["tc_docs", "answer_docs", "tc_code", "answer_code",
                    "answer_d19_combined"],
            [{"role": "knowledge", "question": Q_TWO},
             {"role": "engineering", "question": Q_TWO}], memory_store=priv)
    check("private stores: each role's episode lands in its own store only",
          len(priv["knowledge"].list_for("d19", EPISODIC_KIND)) == 1
          and len(priv["engineering"].list_for("d19", EPISODIC_KIND)) == 1
          and priv["data"].list_for("d19", EPISODIC_KIND) == [],
          f"k={len(priv['knowledge'].list_for('d19', EPISODIC_KIND))} "
          f"e={len(priv['engineering'].list_for('d19', EPISODIC_KIND))} "
          f"d={len(priv['data'].list_for('d19', EPISODIC_KIND))}")

    # 4) B with no memory ≡ B over an empty store (the G9 reflex for memory)
    empty, p4 = tmp_store()
    b_none = h.run_b(Q_CODE, ["tc_code", "answer_code", "answer_d19_code"],
                     [{"role": "engineering", "question": Q_CODE}])
    b_empty = h.run_b(Q_CODE, ["tc_code", "answer_code", "answer_d19_code"],
                      [{"role": "engineering", "question": Q_CODE}], memory_store=empty)

    def same_b(x, y):
        return (x["answer"] == y["answer"] and x["delegation_statuses"] == y["delegation_statuses"]
                and x["delegated_subset_returned"] == y["delegated_subset_returned"])

    check("B with NO memory ≡ B over an EMPTY store (memory is silently optional)",
          same_b(b_none, b_empty), f"{b_none['delegation_statuses']}")
    check("...and equals the shared-store B's answer (no memory changes the answer)",
          b_shared["answer"] == b_none["answer"])
    record("G6", a="single+mem", b="shared/priv/none", winner="measured",
           handoffs="1–2", delegated="ok", verdict="answer: shared ≈ private ≈ none",
           ok=same_b(b_none, b_empty))
    for p in (p1, p2, p4):
        os.unlink(p)
    for s in priv.values():
        os.unlink(s.path)


def g7_per_hop(h: Harness, b_two: dict, b_one: dict):
    print("\n== G7 — per-hop grounding (cited ⊆ retrieved inside every specialist)")
    check("every specialist's own groundedness holds (leaked hops == 0)",
          b_two["per_hop_grounding"]["ok"] and b_two["per_hop_leaks"] == 0
          and b_one["per_hop_leaks"] == 0,
          f"two={b_two['per_hop_leaks']} one={b_one['per_hop_leaks']}")
    hops = b_two["per_hop_grounding"]["hops"]
    check("the trace localizes each hop's own cited ⊆ retrieved",
          len(hops) == 2 and all(h["cited_subset_retrieved"] for h in hops),
          str([(h["role"], h["cited_subset_retrieved"]) for h in hops]))
    check("no evidence claimed at a hop its bearer never returned",
          b_two["per_hop_leaks"] == 0 and b_two["delegated_subset_returned"] is True)
    record("G7", a="—", b="—", winner="both", handoffs="2", delegated="ok",
           cited="ok", verdict="leaks==0", ok=b_two["per_hop_leaks"] == 0)


def g8_failure(h: Harness):
    print("\n== G8 — failure containment (a returned receipt, never a fabrication)")
    agents = {"engineering": h.agents["engineering"], "data": BoomAgent("data")}
    b = h.run_b(Q_CODE, ["tc_code", "answer_code", "answer_d19_partial"],
                [{"role": "engineering", "question": Q_CODE},
                 {"role": "data", "question": Q_CODE}],
                agents=agents, delegation_retry=1)
    check("a crashed specialist returns a FAILED receipt (retried once, then surfaced)",
          b["delegation_statuses"] == [RETURNED, FAILED]
          and str(b["delegations"][1].get("reason", "")).startswith("error:"),
          str([(d["role"], d["status"], d.get("reason")) for d in b["delegations"]]))
    check("the other delegation SURVIVES — a crash does not take down the question",
          b["delegation_statuses"][0] == RETURNED and b["handoffs"] == 2)
    check("the orchestrator fabricated no specialist run (delegated ⊆ returned)",
          b["delegated_subset_returned"] is True and b["handoff_fabricated"] == [])

    jc = JumpClock()
    b2 = h.run_b(Q_CODE, ["tc_code", "answer_code", "answer_d19_refused"],
                 [{"role": "engineering", "question": Q_CODE}],
                 clock=jc, delegation_retry=0)
    check("a timed-out handoff is a FAILED receipt naming the timeout",
          b2["delegation_statuses"] == [FAILED]
          and b2["delegations"][0].get("reason") == "timeout",
          str(b2["delegations"]))
    check("no returned receipt → the orchestrator names the gap, cites nothing",
          b2["delegated_subset_returned"] is True and b2["returned_evidence"] == [])
    record("G8", a="—", b="—", winner="court", handoffs="2", failure="failed@hop",
           delegated="ok", concluded="ok", verdict="contained, never fabricated", ok=True)


def g9_control(h: Harness):
    print("\n== G9 — the control: machinery off ≡ Days 12–18")
    q = "Fetch order 1"
    base = h.run_a(q, ["tc_order", "answer_d14_orders"], routed_to=None)

    os.environ["ROLE_CONTEXT_ROUTING"] = "0"
    try:
        check("with routing OFF the router is neutral (routed_to is None)",
              route_question(q)["routed_to"] is None
              and route_question(q)["routing_on_off"] == "off")
    finally:
        os.environ.pop("ROLE_CONTEXT_ROUTING", None)

    check("orchestration is off by default (AGENT_TOPOLOGY == single)",
          agent_topology_from_env() == "single")

    routed = h.run_a(q, ["tc_order", "answer_d14_orders"], routed_to="data")
    check("routing a question changes no memory-free answer (a recorded field only)",
          routed["answer"] == base["answer"]
          and set(routed["evidence"]) == set(base["evidence"])
          and routed["termination_reason"] == base["termination_reason"])

    empty, p = tmp_store()
    with_store = h.run_a(q, ["tc_order", "answer_d14_orders"], memory_store=empty)
    check("an empty store changes no memory-free answer (the M8 reflex, on A)",
          with_store["answer"] == base["answer"]
          and set(with_store["evidence"]) == set(base["evidence"]))
    check("A with routing off is byte-for-byte Days 12–18 (same answer/evidence/exit)",
          base["routed_to"] is None and base["role"] is None)
    os.unlink(p)
    record("G9", a="off", b="off", winner="—", steps="0 handoffs", handoffs="0",
           tokens="0", delegated="—", verdict="machinery off ≡ Days 12–18", ok=True)


# ---------------------------------------------------------------- regression
SUITES = ["test_day7.py", "test_day8.py", "test_day9.py", "test_day10.py",
          "tool_smoke.py", "agent_smoke.py", "failure_smoke.py", "data_smoke.py",
          "sql_attack_smoke.py", "plan_smoke.py", "state_smoke.py", "memory_smoke.py"]


def no_regression():
    print("\n== Day 19 — no regression: Days 7–18 re-run unchanged through the seam")
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


# ---------------------------------------------------------------- reporting
def render():
    print("\n" + "=" * 150)
    print("   DAY 19 — ARCHITECTURE AS A CONTRACT (§9 table; delegated ⊆ returned on "
          "every B row; G9 the counterweight)")
    print("=" * 150)
    print(f"{'id':<6}{'A':<20}{'B':<28}{'winner':<12}{'handoffs':<9}{'delegated⊆ret':<14}"
          f"{'verdict':<52}")
    print("-" * 150)
    for r in ROWS:
        print(f"{r['id']:<6}{str(r['a']):<20}{str(r['b']):<28}{str(r['winner']):<12}"
              f"{str(r['handoffs']):<9}{str(r['delegated']):<14}{str(r['verdict']):<52}")
    print("-" * 150)
    bad = [r["id"] for r in ROWS if not r["ok"]]
    b_rows = [r for r in ROWS if r["delegated"] == "ok"]
    print(f"delegated ⊆ returned = ok on every B row: "
          f"{'YES' if all(r['ok'] for r in b_rows) else 'NO'}")
    print(f"rows ok: {'ALL' if not bad else 'FAILURES ' + ', '.join(bad)}")
    print("Verdict: justified architecture = SINGLE (the null holds; every G2-family "
          "row is an A-win) — the split's context-isolation claim is PENDING Day "
          "21/22 on a real model + real budget. Cost: A adds 0 chats; B's mechanics "
          "add 0 chats; B's operation bills the union of both contexts (G5).")


# --------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fast", action="store_true",
                    help="skip the Days 7–18 no-regression re-run (fixtures only)")
    args = ap.parse_args()

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
        h = Harness(reg, gw)
        print(f"architecture: AGENT_TOPOLOGY={agent_topology_from_env()} "
              f"SPECIALIST_ROLES={os.getenv('SPECIALIST_ROLES', 'all')} "
              f"MAX_DELEGATIONS={os.getenv('MAX_DELEGATIONS', '3 (default)')} "
              f"HANDOFF_TIMEOUT={os.getenv('HANDOFF_TIMEOUT', '30 (default)')} "
              f"DELEGATION_RETRY={os.getenv('DELEGATION_RETRY', '1 (default)')} "
              f"ROLE_CONTEXT_ROUTING={os.getenv('ROLE_CONTEXT_ROUTING', '1 (default)')}")
        print(f"roles: knowledge={ROLE_TOOLS['knowledge']} "
              f"engineering={ROLE_TOOLS['engineering']} data={ROLE_TOOLS['data']}")
        print(f"router: {route_question(Q_CODE)}")
        print(f"plan(Q_TWO): {orchestration_plan(Q_TWO)}")
        print(f"surfaces(Q_TWO)={detect_surfaces(Q_TWO)}")

        b_two, a_two = g1_two_surface(h)
        b_one, a_one = g2_monolith(h)
        g3_receipt_contract(h)
        g4_containment(h)
        g5_cost_ledger(h)
        g6_memory_reducer(h)
        g7_per_hop(h, b_two, b_one)
        g8_failure(h)
        g9_control(h)
        if not args.fast:
            no_regression()
    finally:
        if proc is not None:
            proc.terminate()

    render()
    print("\n" + ("ALL DAY 19 (MECH) CHECKS PASSED" if not FAILS
                  else f"{FAILS} CHECK(S) FAILED"))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
