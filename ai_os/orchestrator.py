"""Day 19 §5 — the orchestrator: a loop whose *tools are other loops*.

Topology B, built from the pieces the series already proved. The orchestrator is
Day 12's loop with one tool — `delegate(role, question)` — pointed at three
`make_agent(role)` specialists. Each specialist is the existing loop with a
narrower registry (a tool-list slice) and its own budget/termination, and each
returns a **receipt** (`ai_os/receipt.py`), never prose. So B is not "three new
systems": it is **one seam** — the handoff — over the machine Days 12–18 built.

The seam's rules, each a Day 11–16 reflex translated one scale up:

- **A delegation is a tool call.** `delegate()` executes a specialist the way the
  loop executes a tool: bounded by `MAX_DELEGATIONS` (Day 12's `max_steps`),
  walled by `HANDOFF_TIMEOUT` (Day 13's timeout, between loops), and retried once
  on a *failed* return (`DELEGATION_RETRY = 1`, Day 4's gateway-retry discipline).
  A `refused` receipt is a legitimate return and is **not** retried — retrying "no"
  until it becomes "yes" is the delegation version of rephrasing past a policy wall.
- **Failure is a return value.** A specialist that crashes, times out, or refuses
  yields a `failed`/`refused` receipt with a reason; the orchestrator may retry
  (bounded), route around (a different role in the plan), or answer with the failure
  *named*. It can never fabricate a specialist run it never received (G8).
- **Aggregation is by citation.** The returned receipts enter the aggregator's
  prompt as records to *cite* (`receipt_block`), never to re-voice — so
  `delegated ⊆ returned` (`receipt.check_delegated_subset_returned`) holds: every id
  the answer names came from a returned receipt.

Everything here is `MECH` under the stub: the routing, the receipts, the ledger, and
the containment are deterministic functions of topologies, receipts, and roles. The
only model call the orchestrator adds is the aggregation turn; its cost is billed
next to the specialists' so `cost for equal grounding` is a measured column (G5),
and the duplicate-context truth is stated, not hidden.

```text
   orchestrator loop (1 tool: delegate(role, q))
        │  plan = orchestration_plan(question)   ← MECH rule table
        ├── delegate("data", q) ──────► data specialist loop ──► RECEIPT
        ├── delegate("knowledge", q) ─► knowledge loop ───────► RECEIPT
        └── aggregate(receipts)  ──► answer (cites receipts only)
             delegated ⊆ returned  — checked in code
```
"""
from __future__ import annotations

import os
import time
from typing import Any, Sequence

from ai_os.agent import ROLES, RoleAgent, _new_run_id, route_question
from ai_os.receipt import (
    RETURNED,
    Receipt,
    check_delegated_subset_returned,
    check_per_hop_grounding,
    delegation_retry_from_env,
    failed_receipt,
    handoff_timeout_from_env,
    max_delegations_from_env,
    receipt_block,
    receipts_subset_authored,
    returned_evidence,
)
from ai_os.state import STATE_OFF, STATE_ON

TOPOLOGY_ENV = "AGENT_TOPOLOGY"
ROLES_ENV = "SPECIALIST_ROLES"

TOPOLOGY_SINGLE = "single"
TOPOLOGY_ORCHESTRATOR = "orchestrator"
TOPOLOGIES: tuple[str, ...] = (TOPOLOGY_SINGLE, TOPOLOGY_ORCHESTRATOR)

ORCHESTRATOR_PROMPT = (
    "You are an orchestrator. You delegated parts of the question to specialist "
    "agents and hold their RECEIPTS below. Rules:\n"
    "1. Answer only from the receipts' evidence. Cite the doc_id / module#Symbol ids "
    "that appear in a RETURNED receipt; never invent an id.\n"
    "2. Cite a receipt; never re-voice it as your own prose (a paraphrase is a "
    "handoff fabrication).\n"
    "3. A refused or failed receipt is a named gap, not a finding: say plainly what "
    "was not returned rather than filling it in.\n"
    "4. If no receipt returned evidence, say so — do not commission a finding that "
    "was never made."
)

NO_RETURN_ANSWER = (
    "I decomposed the question and delegated, but no specialist returned evidence "
    "for it, so I cannot answer from receipts. Nothing was fabricated to fill the gap."
)


def agent_topology_from_env() -> str:
    raw = os.getenv(TOPOLOGY_ENV, TOPOLOGY_SINGLE).strip().lower()
    return raw if raw in TOPOLOGIES else TOPOLOGY_SINGLE


def specialist_roles_from_env() -> list[str]:
    raw = os.getenv(ROLES_ENV, "").strip()
    if not raw:
        return list(ROLES)
    wanted = [r.strip() for r in raw.split(",") if r.strip()]
    return [r for r in wanted if r in ROLES] or list(ROLES)


# --------------------------------------------------------------- the plan
# The delegation DAG *is* a plan (Day 16): a deterministic rule-table decomposition
# of a question into (role, sub-question) nodes, `MECH`, 0 chats, overridable.

_ROLE_TOKENS: dict[str, tuple[str, ...]] = {
    "knowledge": ("documentation", "docs", "guide", "quickstart", "concept"),
    "engineering": ("source code", "codebase", "code", "function", "class",
                    "symbol", "module", "mutation", "implemented", "where is"),
    "data": ("sql", "database", "schema", "table", "column", "revenue", "rows"),
}


def detect_surfaces(question: str) -> list[str]:
    """Which roles' surfaces a question names, in a stable order.

    A two-surface question (docs *and* code) is the honest G1 case: a single routed
    loop enters one surface, while a delegation can cover both. Detection reads the
    router's rule table per role — the *first-match* router picks one surface, and
    this scan recovers the others a plan must cover.
    """
    q = f" {(question or '').lower()} "
    found = [role for role in ROLES
             if any(t in q for t in _ROLE_TOKENS.get(role, ()))]
    if not found:
        routed = route_question(question)["routed_to"]
        found = [routed] if routed else []
    return found


def orchestration_plan(question: str, *, roles: Sequence[str] | None = None,
                       forced: str | None = None) -> dict[str, Any]:
    """A question → the delegations it warrants (deterministic, `MECH`).

    One delegation per named surface, capped by the allowed roles; an unrouted,
    single-surface question is a single delegation (or none if no role covers it).
    This is the rule-table half of "the orchestrator plans first" — the DAG a model
    would propose, computed instead so the routing is transparent and free.
    """
    allowed = list(roles or ROLES)
    routed = route_question(question, forced=forced)
    surfaces = [r for r in detect_surfaces(question) if r in allowed]
    if not surfaces and routed["routed_to"] in allowed:
        surfaces = [routed["routed_to"]]
    delegations = [{"role": r, "question": question} for r in surfaces]
    return {"routed_to": routed["routed_to"], "signal": routed["signal"],
            "delegations": delegations, "surfaces": surfaces}


# --------------------------------------------------------------- the loop

class Orchestrator:
    """The delegation loop. One tool — `delegate(role, question)` — over specialists."""

    def __init__(self, agents: dict[str, RoleAgent], gateway, *,
                 principal: str = "default", store=None, run_id: str | None = None,
                 roles: Sequence[str] | None = None,
                 memory_store=None,
                 max_delegations: int | None = None,
                 handoff_timeout_s: float | None = None,
                 delegation_retry: int | None = None,
                 clock=time.monotonic,
                 temperature: float = 0.0, max_tokens: int = 2048) -> None:
        self.agents = dict(agents)
        self.gateway = gateway
        self.principal = principal
        # `store` is the orchestrator's *own* shelf (orchestration records).
        # `memory_store` is the substrate the **specialists** recall from — a single
        # shared store (Day 18's, the default) or a per-role mapping (G6's private
        # comparison). Both are optional; with neither, nothing is read or written.
        self.store = store
        self.memory_store = memory_store if memory_store is not None else store
        self.run_id = run_id
        self.roles = list(roles or [r for r in ROLES if r in self.agents])
        self.max_delegations = (max_delegations_from_env() if max_delegations is None
                                else max(1, max_delegations))
        self.handoff_timeout_s = (handoff_timeout_from_env()
                                  if handoff_timeout_s is None else handoff_timeout_s)
        self.delegation_retry = (delegation_retry_from_env()
                                 if delegation_retry is None else max(0, delegation_retry))
        self.clock = clock
        self.temperature = temperature
        self.max_tokens = max_tokens

    # ------------------------------------------------------------ one hop

    def _ledger_slice(self, start: int) -> dict[str, Any]:
        recs = self.gateway.ledger.records[start:]
        return {"chats": len(recs),
                "tokens": sum(r.input_tokens + r.output_tokens for r in recs),
                "cost_usd": round(sum(r.cost_usd for r in recs), 8)}

    def delegate(self, role: str, question: str, *, principal: str,
                 run_id: str) -> tuple[Receipt, dict[str, Any] | None]:
        """Execute one specialist as a tool call — bounded, walled, retried once.

        Returns `(receipt, run_or_None)`. The run is kept for the per-hop grounding
        trace even when the receipt is `failed`, because the specialist's *internal*
        grounding is a different fact from whether it returned.
        """
        agent = self.agents.get(role)
        if agent is None:
            return failed_receipt(role, question, principal=principal,
                                  reason="no such specialist", run_id=run_id), None

        attempts = 1 + self.delegation_retry
        last: tuple[Receipt, dict[str, Any] | None] = (
            failed_receipt(role, question, principal=principal,
                           reason="not attempted", run_id=run_id), None)
        for _ in range(attempts):
            start = len(self.gateway.ledger.records)
            t0 = self.clock()
            mem = (self.memory_store.get(role) if isinstance(self.memory_store, dict)
                   else self.memory_store)
            # Each specialist gets its *own* run id (provenance is per loop), so two
            # roles never collide on an episodic key in the shared store.
            extra = ({"memory_store": mem, "principal": principal}
                     if mem is not None else {})
            try:
                run = agent.run(question, self.gateway,
                                temperature=self.temperature, max_tokens=self.max_tokens,
                                **extra)
            except Exception as exc:                       # a specialist bug is a return value
                receipt = failed_receipt(
                    role, question, principal=principal,
                    reason=f"error:{exc.__class__.__name__}", run_id=run_id)
                receipt.cost = self._ledger_slice(start)
                last = (receipt, None)
                continue
            elapsed = self.clock() - t0
            if self.handoff_timeout_s is not None and elapsed > self.handoff_timeout_s:
                receipt = failed_receipt(role, question, principal=principal,
                                         reason="timeout", run_id=run_id)
                receipt.cost = self._ledger_slice(start)
                receipt.steps = int(run.get("tool_steps") or 0)
                last = (receipt, run)
                continue                                   # a timed-out hop may be retried once
            receipt = Receipt.from_run(role, run, principal=principal,
                                       question_asked=question)
            receipt.cost = self._ledger_slice(start)
            if receipt.status == RETURNED:
                return receipt, run
            # A refused receipt is an *answer* ("no covering evidence") — return it,
            # never retry a refusal into a different one.
            return receipt, run
        return last

    # ------------------------------------------------------------ the question

    def run(self, question: str, *, delegations: Sequence[dict[str, Any]] | None = None,
            principal: str | None = None) -> dict[str, Any]:
        """Plan → delegate → aggregate by citation → check `delegated ⊆ returned`."""
        principal = principal or self.principal
        run_id = self.run_id or _new_run_id()
        if delegations is None:
            delegations = orchestration_plan(question, roles=self.roles)["delegations"]
        planned = list(delegations)
        capped = max(0, len(planned) - self.max_delegations)
        planned = planned[:self.max_delegations]

        ledger_start = len(self.gateway.ledger.records)
        receipts: list[Receipt] = []
        runs: list[dict[str, Any]] = []
        for node in planned:
            role = str(node.get("role"))
            sub_q = str(node.get("question") or question)
            receipt, run = self.delegate(role, sub_q, principal=principal, run_id=run_id)
            receipts.append(receipt)
            if run is not None:
                runs.append(run)

        # Aggregate by citation. With no returned receipt the orchestrator refuses
        # like Day 7 refused — it does not commission a finding.
        block = receipt_block(receipts)
        answer = NO_RETURN_ANSWER
        if block:
            completion = self.gateway.chat(
                [{"role": "system", "content": ORCHESTRATOR_PROMPT},
                 {"role": "system", "content": block},
                 {"role": "user", "content": question}],
                temperature=self.temperature, max_tokens=self.max_tokens)
            answer = completion.text

        orc_cost = self._ledger_slice(ledger_start + sum(
            int(r.cost.get("chats") or 0) for r in receipts))
        specialist_tokens = sum(int(r.cost.get("tokens") or 0) for r in receipts)
        subset = check_delegated_subset_returned(answer, receipts)
        per_hop = check_per_hop_grounding(runs)
        statuses = [r.status for r in receipts]
        returned = returned_evidence(receipts)
        total = self._ledger_slice(ledger_start)

        result: dict[str, Any] = {
            "question": question,
            "answer": answer,
            "topology": TOPOLOGY_ORCHESTRATOR,
            "roles": self.roles,
            "routed_to": (planned[0]["role"] if planned else None),
            "delegations_planned": len(planned) + capped,
            "delegations_capped": capped,
            "handoffs": len(receipts),
            "delegations": [
                {"role": r.role, "task_id": r.task_id, "status": r.status,
                 "reason": r.reason, "cost": dict(r.cost)}
                for r in receipts],
            "receipts": [r.as_dict() for r in receipts],
            "delegation_statuses": statuses,
            "evidence": list(returned),      # alias: the citable evidence surface
            "returned_evidence": returned,
            "attributed_to": subset["attributed"],
            "delegated_subset_returned": subset["ok"],
            "handoff_fabricated": subset["fabricated"],
            "per_hop_grounding": per_hop,
            "per_hop_leaks": per_hop["leaks"],
            "receipts_subset_authored": receipts_subset_authored(receipts),
            "failure": any(s != RETURNED for s in statuses),
            "refused": all(s != RETURNED for s in statuses) if statuses else True,
            # The cost ledger, honest about the duplication: a delegation bills the
            # specialist's own loop *plus* the orchestrator's aggregator context.
            "specialist_tokens": specialist_tokens,
            "orchestrator_tokens": orc_cost["tokens"],
            "context_union_tokens": specialist_tokens + orc_cost["tokens"],
            "chats": total["chats"],
            "tokens": total["tokens"],
            "cost_usd": total["cost_usd"],
            "run_id": run_id,
            "principal": principal,
        }
        result["store_on_off"] = STATE_ON if self.store is not None else STATE_OFF
        if self.store is not None:
            from ai_os.memory import write_orchestration
            write_orchestration(self.store, principal, f"verdict:{run_id}", {
                "question": question, "topology": TOPOLOGY_ORCHESTRATOR,
                "routed_to": result["routed_to"], "delegations": result["delegations"],
                "delegated_subset_returned": subset["ok"],
                "per_hop_leaks": per_hop["leaks"], "handoffs": len(receipts),
            }, run_id=run_id)
            result["orchestration_written"] = True
        else:
            result["orchestration_written"] = False
        return result

    def run_topology(self, question: str) -> dict[str, Any]:
        """Alias kept explicit: this is Topology B's entry point."""
        return self.run(question)
