# Multi-Agent Architecture — the Justification, Not the Count

> Day 19 of the AI-layer apprenticeship. Eighteen days built a machine that *reads*
> (6–10), *acts* (11), *drives itself* (12), *survives failure* (13), *computes*
> (14), *defends itself* (15), *thinks before acting* (16), *remembers what it did*
> (17), and *carries a lesson — only if the lesson earns it* (18). Day 19 is the
> curriculum's first question whose *correct answer may be "do nothing"*:
> **"Determine whether multiple agents are actually useful. Do not assume they
> are."** The deliverable is *either* a justified single-agent architecture *or* a
> justified multi-agent architecture, and the binding clause is **"the justification
> matters more than the number of agents."**
> Implementation: `ai_os/receipt.py` (the delegation contract), `ai_os/orchestrator.py`
> (the delegation loop), `ai_os/agent.py` (`make_agent(role)` + the role-context
> router), `ai_os/memory.py` (the shared-store write policy), `ai_os/trace.py` (the
> topology columns), `tools/architecture_smoke.py` (G1–G9).

The single idea: **an "agent" is a loop** (Day 12) **with tools** (Day 11), **a plan**
(Day 16), **a policy** (Day 15), **and memory** (17–18) — so the series' three
curriculum specialists are *already* the single loop's three tool groups, and "add
an agent" means **duplicate the loop**, not "add a tool group." Specialization has
four homes that cost zero loops — the tool schema (11), the privilege policy (15),
the plan (16), the memory (17–18) — and only a fifth candidate, *the loop itself*,
remains. Day 19 builds the comparison that decides whether that fifth duplication
earns its cost, and it does so with one rule stated up front: **the verdict must
emerge from a fixture table, on either side, and the justification — not the count —
is the deliverable.**

## 1. The loaded question — "multiple agents" vs "more code"

The curriculum's Day 19 is a rebuke, not a feature request. The apprenticeship's
Rule 6 lists *multi-agent systems* beside Kubernetes and microservices under *no
premature complexity*, and its §9 files *multi-agent hype* as something to use "only
if it solves an actual problem" — while the target-system diagram shows an
orchestrator and three specialists. The day lives in that tension.

The load-bearing word is **useful**, read the way Rule 4 reads every adjective: not
"clever," not "architecture-shaped." Useful means *on the measured axes — task
success, evidence grounding, cost, latency, failure containment — the split beats the
single agent it replaces, on tasks that actually occur, enough to pay for what
splitting costs.* A multi-agent system that cannot justify each loop against its
monolith **on the table** is not a "justified multi-agent architecture"; it is a Rule-6
violation wearing a diagram.

The first real move is to notice how much was already settled. The curriculum's three
candidate agents — **Knowledge**, **Data**, **Engineering** — are **tool groups and
privilege sets on one loop**:

| Curriculum "agent" | What it already is here | Built by |
|---|---|---|
| Knowledge | doc/code retrieval + the gate + rerank | Days 6–10 |
| Data | schema-grounded read-only SQL + Day 15 policy | Days 14–15 |
| Engineering | code questions (+ the run/inspect surface) | Days 10–11 |

The series has done "specialization" continuously without duplicating the loop: Day 11
specialized by *tool schema*, Day 15 by *role and policy*, Day 16 by *plan*. So the
diagram is a **hypothesis**, not a blueprint, and Day 19 runs it against the null.

Three words keep the argument from blurring:

- **role** — a tool set + a policy (Day 11 + Day 15). Exists today, on one loop, at
  **zero loop-cost**.
- **agent** — a role + its own loop (Day 12) + its own budget + its own memory + its
  own termination. The thing the hypothesis proposes to duplicate.
- **orchestrator** — a loop whose *tools are other loops*: a delegation surface that
  can fail and must be audited like any tool.

And the two failure shapes the fixtures define: **over-orchestration** (a delegation
that adds cost and risk to a task the single loop already answers — Day 16's
`over-planned?` promoted to the architecture level) and the **handoff leak** (a
specialist's finding mis-stated when it crosses the boundary — the day's "Chinese
whispers," a Day 13 fabrication mode living on the delegation seam).

## 2. Rule 6 and the burden of proof — single is the default

The null hypothesis is not "nothing." It is the **cheapest proven thing**: the single
loop Days 12–18 already run, already carrying all three tool groups, all three Day 15
roles, and Day 18's memory. Multi-agent carries the burden of proof, and Rule 6 names
the standard: no premature complexity.

Three costs of a second loop, every one a measured column:

1. **Context duplication.** The orchestrator's context does not shrink when it
   delegates — it *grows* by the receipt it must hold to ground its answer — and the
   specialist's context is separate, so the same facts are billed twice. Delegation
   does not *replace* context; it *ships a copy of it* (`context_union_tokens`, G5).
2. **A new failure surface.** A handoff can fail like a tool call (Day 13's taxonomy),
   plus three shapes only delegation creates: routing to the wrong specialist, a
   specialist returning ungrounded evidence, and an orchestrator fabricating a
   delegation it never made (G3, G8).
3. **Shared-state coordination.** Two loops touching Day 17/18's store — or two
   stores, or one closing the other's file — is a concurrency problem the single loop
   never had (Day 17 named multi-process synchronization, not built).

Against those costs, exactly two honest reasons specialization might still pay:

1. **Context isolation.** A task mixing data-heavy and knowledge-heavy work can crowd
   a single context until one surface's evidence is squeezed out. The *measurable*
   claim is **total tokens for equal grounding**, never "looks cleaner."
2. **Independent concurrency.** Two specialists investigating in parallel genuinely cut
   wall-clock *if* their work is independent **and** the orchestrator's aggregation
   does not serialize it back. Day 16's plan-as-DAG is the honest test.

Everything else the multi-agent pitch usually offers — "separation of duties,"
"scalability," "one agent per concern" — was already delivered by role-policies and
tool groups at zero loop-cost. The counterweight is the reflex the series has kept
since Day 16's P9, Day 17's S9, and Day 18's M8: **whatever Day 19 concludes, a
topology that changes a memory-free, delegation-free answer is a bug** (G9).

Two pre-commitments follow. **Measure before building the topology** — the harness
(a single fixture router that can present any task to either topology) is the day's
first build, not the orchestrator. And **the day ends with a verdict line** —
`justified architecture = single` or `multi (scoped to: …)` — because the curriculum's
deliverable is *justified*, and a day that ends with a verdict the table cannot
support failed worse than one that ends without a verdict.

## 3. The comparison — two topologies, same fixtures, one judge

Day 19 is an **A/B with one fixture router**, so the only variable is topology:

```text
                 the day's question set (Days 12–18 families + orchestration fixtures)
                                      │
                  ┌───────────────────┴────────────────────┐
                  ▼                                        ▼
      Topology A — the single loop             Topology B — orchestrator + specialists
  (Days 12–18 as-built, + role-context      (Knowledge / Data / Engineering loops under
   routing; one context · one budget ·       an orchestrator; role-scoped tools, per-loop
   one planner · Day 15 policy · Day 18      budgets, shared Day 18 store)
   memory)  ← the NULL hypothesis                          │
                  │                                        │
                  └───────────────────┬────────────────────┘
                                      ▼
                       the judge: identical exit checks
   (expected evidence ⊆ retrieved · cited ⊆ retrieved · conclusion ⊆ evidence
    · delegated ⊆ returned · cost · latency · steps · failures)
                        →  the verdict row, per fixture
```

**Decision 1 — the single loop is Topology A, and it is not "no architecture."** It
is the curriculum's allowed deliverable *and* the null at once: Days 12–18 plus the
one upgrade the comparison genuinely owes it, **role-context routing** — a
deterministic layer (`ai_os.agent.route_question`, Day 8's rule-table reflex
re-pointed at tool groups) that picks the entry surface (`knowledge | data |
engineering`) for a question before the loop starts. That is the honest *half* of
specialization: **route the context, keep the loop.** It is `MECH` (a rule-table
decision, 0 chats), transparent (`routed_to` in the trace), and overridable (a
question naming a surface wins). With routing off, Topology A is byte-for-byte Days
12–18 (G9).

**Decision 2 — Topology B is the curriculum's diagram, built as honestly as the
comparison demands.** The orchestrator's tools are three specialist loops, each
exactly Topology A's tool group and policy — `make_agent(role)` is the existing Day 12
loop bound to a **narrower registry** (`ToolRegistry.scope`: keep this role's tools,
*deny* the rest with a typed `role_violation`). A specialist is the existing loop with
a tool-list slice, so B is not "three new systems"; it is **one seam** (the handoff)
over the established machine. The specialists read from and write to **Day 18's shared
store** (the alternative, N private stores, is G6's comparison row).

**Decision 3 — the delegation contract is a receipt, not prose** (the day's headline,
`delegated ⊆ returned`). The only thing allowed across the orchestrator↔specialist
boundary is a **receipt** (`ai_os/receipt.py`): `{task_id, principal, role,
question_asked, evidence[], conclusion, provenance{run_id, step, source:
"specialist:{role}"}, status: returned|refused|failed}` — the exact `kind=episodic`
record Day 17 stores and Day 18 recalls, promoted to the currency of a handoff. The
orchestrator **cites** a receipt; it never paraphrases one. `delegated ⊆ returned` is
checked in code: every id the answer names must appear in a **returned** receipt's
evidence; an id attributed but absent is a **handoff fabrication**, flagged like Day
13's fabricated citation, across the seam.

**Decision 4 — failure is a return value of the delegation, and only the
delegation.** When a specialist refuses, times out, or crashes (Day 13 at loop
granularity), the orchestrator receives a `failed`/`refused` receipt with a reason. It
may retry a *failed* hop once (`DELEGATION_RETRY = 1`, Day 4's discipline), route
around it, or answer with the failure named — it **cannot** fabricate a specialist run
(G8). A `refused` receipt is a legitimate answer and is **not** retried: retrying "no"
until it becomes "yes" is the delegation version of rephrasing past a policy wall.

The day's invariants:

1. **`delegated ⊆ returned`** — an orchestrator's answer cites only receipted evidence
   (`receipt.check_delegated_subset_returned`), the fourth translation of
   `cited ⊆ retrieved` across the loop boundary.
2. **Ballots, then verdicts** — every fixture is measured on both topologies before
   any conclusion; a fixture only one topology can run is a routing bug, not a win.
3. **The control** — machinery off ≡ Days 12–18 (G9).
4. **No loop without a ledger** — every agent books its chats/tokens to the row,
   because the whole question is *cost for equal grounding*.

## 4. Topology A — the single loop, plus role-context routing

The baseline is already implemented; Day 19's discipline is to **treat it as
implemented** and add only the one seam the comparison needs. A benchmark run of Days
12–18's fixture set through the A/B harness with routing off *is* Topology A's unit
test — and it must be green **before a line of Topology B runs** (it is, by
construction: the existing suites are the no-regression gate).

The role-context router is the whole A-side addition, and it is deliberately boring: a
deterministic rule layer (`route_question`) that classifies a question into one of the
three surfaces and records `routed_to`. It costs zero chats, is transparent (the
`signal` field names the token that fired), and is overridable (an explicit surface
name or a `forced=` argument wins), mirroring Day 8's debuggable filter. The honest
claim it encodes: **the specialist's *surface* is real and cheap; the specialist's
*loop* is the part that must earn its airfare.** Routing changes no answer — it only
records where the loop *entered* — which is exactly what the G9 row proves.

## 5. Topology B — the orchestrator and the specialists

B is built from pieces the series already proved, and that is the architectural
honesty of the exercise. `make_agent(role)` (in `ai_os/agent.py`) returns a `RoleAgent`
whose `.registry` is a **scope** of the full registry (this role's tools kept, the
others denied) and whose `.run()` is Day 12's `run_agent` with the role recorded. The
three roles:

```text
knowledge   → search_saleor_docs            (the documentation surface)
engineering → search_saleor_code            (the code surface — the stand-in for the
                                             curriculum's git/run/test, absent here)
data        → run_saleor_sql · describe_saleor_schema · get_product/order/customer
```

The groups are **disjoint on purpose**: an out-of-role call is a *policy* refusal at
the specialist's gate (G4), not an ambiguity. (The curriculum's knowledge group also
contains code retrieval; the toolset here splits documentation from source so the
two-surface fixture has two roles to route between — a faithful instantiation of
"specialize the surface.")

The orchestrator (`ai_os/orchestrator.py`) is a loop whose tool list is
`[delegate(role, question)]`. It **plans first** (Day 16 — the delegation DAG *is* a
plan, computed by the `orchestration_plan` rule table so the routing is transparent
and free), dispatches each node, receives receipts, and **aggregates by citation** (Day
6's rule at the seam): the returned receipts enter the aggregator's prompt as records
to cite (`receipt_block`), exactly as Day 18's recall served episodes *as* episodes.

Bounded delegation is policy, exactly as the series parameterizes everything:
`MAX_DELEGATIONS` per question (Day 12's `max_steps` at orchestration level),
`HANDOFF_TIMEOUT` per delegation (Day 13's timeout, between loops), and
`DELEGATION_RETRY = 1` (Day 4's gateway-retry discipline for a manual seam). The
orchestrator's own loop respects Day 12's transcript-state rule, Day 13's guards, and
Day 6's citation contract applied to receipts.

Groundedness stays per-specialist and end-to-end (G7): each specialist answers only
from its own retrieval (its internal `cited ⊆ retrieved`), and the receipt it returns
is its Day 18-style episodic record — evidence, conclusion, provenance — not a summary
sentence. The ledger keeps the number most likely to kill B's excuse: the cost of a
delegation is the **union of both contexts** (`specialist_tokens + orchestrator_tokens`
= `context_union_tokens`, G5), because delegation duplicates rather than replaces
context.

## 6. The delegation contract — receipts across the boundary

The receipt is the day's unit of delegation, and it is nothing the series has not
already made: the `kind=episodic` record of Day 17, promoted to the currency of an
inter-loop handoff. Nothing else crosses the boundary; a specialist's "finding" that is
not a receipt is not a finding.

`delegated ⊆ returned` is enforced as a code check
(`receipt.check_delegated_subset_returned`), in lockstep with `cited ⊆ retrieved`
(Day 6), `asserted ⊆ returned` (Day 14), `conclusion ⊆ evidence` (Day 16),
`recalled ⊆ authored` (Day 17), and `authored ⊆ grounded` (Day 18). After the
orchestrator answers, every id it names is checked against the returned receipts'
evidence; a fact attributed but absent is a **handoff fabrication**, flagged in red —
the Chinese whisper caught exactly as Day 13 caught a fabricated citation.

Aggregation is by **citation, not absorption** — Day 6's rule at the loop seam. The
orchestrator merges receipts into its context as blocks to cite; it may combine two
specialists' evidence to support one conclusion; it never re-voices a specialist's
finding as un-cited prose. M7's "a receipt is never a lesson" is inherited across the
boundary.

The orchestrator's refusal stays honest: a question that routes to no role, or to a
role with nothing covering it, is refused like Day 7 refused. "I decomposed,
delegated, and nothing returned evidence" is a valid terminated answer; "I commissioned
a specialist who must have found something" is the cardinal lie (G8).

And the day records its honest limit in Day 18's voice: **the orchestrator cannot see
inside a specialist's loop.** It holds the receipt, not the transcript. For a contested
conclusion it has Day 18's re-distillation move — delegate *again* with a pointed
question — or it surfaces the disagreement. A delegation receipt is Day 17's closed
store, not an open window; "you cannot see inside" sits beside "you cannot un-distill."

## 7. Shared memory and policy across roles

Day 18's store is the sharing substrate, and Day 19 adds no new memory machinery: a
specialist writes its finished run as an `kind=episodic` record under its own
`principal` **and role** (`remember_episode(..., role=...)`; the role rides in the
payload), and reads `.recall(question, principal)` against the shared store with Day
18's keying untouched. The curriculum's "agents sharing memory" therefore costs **zero
new memory machinery** — and one genuinely new policy: **who may write.**

The write policy is the day's one real security decision, settled Day 15's way
(privileges beat parsers). Default: every specialist writes episodic records only under
its own `principal` + `role` provenance. The orchestrator may read all its specialists'
records (it is the coordinator), but **the orchestrator writes only orchestration
records** (`kind=orchestration`, `source: "orchestrator"` — routing, delegations,
verdicts), **never specialist-episodes.** A router that authors investigations claims
evidence it never personally grounded — M7's boundary reproduced across roles. The
store's `AUTHORED_SOURCES` gained `orchestrator`, which does **not** weaken
`recalled ⊆ authored`: it admits a writer whose writes are a different `kind`.

The sharing regimes are compared as measured rows (G6), not prose: B shared store vs B
private per-role stores vs A with Day 18 memory — answering Day 18's own closing
question, *"is a single agent with real memory strictly cheaper than N agents with N
private stores?"* Day 17/18's retention invariants apply unchanged across roles:
receipts expire and delete like any episodic record, cross-principal reads stay empty,
and M8's control (memory off ≡ the days without) is re-run through B's loops — a
shared store must not change a memory-free specialist's answer either.

The `PENDING` sails with the fleet: the routing, the contract, the ledger, and the
containment are `MECH` — deterministic functions of topologies, receipts, and roles,
offline and free. Whether a **real model's orchestrator actually delegates better than
the single loop answers** on real Saleor questions is the real-gateway question →
**Day 21/22's** benchmarks on Day 19's harness.

## Config (env-overridable)

| Key | Default | Meaning |
|---|---|---|
| `AGENT_TOPOLOGY` | `single` | `single` (Topology A) or `orchestrator` (B); the day's switchboard |
| `SPECIALIST_ROLES` | all three | which roles the orchestrator may delegate to (comma list) |
| `MAX_DELEGATIONS` | `3` | per-question hop cap (Day 12's `max_steps` at orchestration level) |
| `HANDOFF_TIMEOUT` | `30` s | the wall around one delegation (Day 13's timeout, between loops) |
| `DELEGATION_RETRY` | `1` | retries for a **failed** hop (Day 4's discipline); a *refused* hop is never retried |
| `ROLE_CONTEXT_ROUTING` | `1` | the router's switch; `0` → neutral (`routed_to: None`, A ≡ Days 12–18) |

## What this is not

Stated plainly, because a green table implies more than it proves:

- **The split's win is named, not measured.** Under the stub, a single loop covered a
  two-surface question in one context at lower cost (G1) — so the table forces
  **single**, and the context-isolation/latency claims stay `PENDING` for Day 21/22 on
  a real model with a real context budget.
- **The specialists are stub-driven.** The stub scripts which tool each loop calls; the
  routing, the contract, the ledger, and the containment are `MECH`, but *whether a
  real orchestrator chooses good delegations* is a real-key question.
- **Parallel specialists are named, not built.** The orchestrator dispatches a linear
  plan; true concurrency of two specialists mid-question is the scoped-if-won prize,
  gated on G1's measured row — and even then a fixture, not an entitlement.
- **Agent-to-agent state hand-off is named, not built.** Receipts are deliberately
  stateless — the only seam.
- **Multi-agent observability is Day 24's.** One trace following one question through
  three loops is the distributed trace, named now.
- **`delegated ⊆ returned` is id-shaped.** It proves the answer cites only returned
  evidence; a paraphrase in prose that names no id is not caught (the same honest limit
  Day 12's fabrication guard carries).

## Negatives (honest, with owners)

1. **Real-model delegation quality** — does a real orchestrator beat the single loop on
   real questions? Owner: **Day 21/22's** benchmark on this harness. `PENDING`.
2. **Context isolation's real price** — the G1 win did not reproduce under the stub
   (A tied on coverage and cost less); a real model + real budget must decide. `PENDING`.
3. **Shared memory at scale** — does a shared store make real answers better than
   private stores? **Day 21** on Day 18's distiller/recall. `PENDING`.
4. **Parallel specialists** — named, not built; gated on G1.
5. **Agent-to-agent state hand-off** — named, not built (receipts are stateless).
6. **Multi-agent observability** — **Day 24's** distributed trace.

## Re-verify, in one line

```bash
python tools/architecture_smoke.py    # G1–G9 + the Days 7–18 no-regression re-run
```

**`delegated ⊆ returned = ok` on every B row**, with **G9** holding the line: machinery
off ≡ Days 12–18, so over-orchestration is a bug measured the way P9, S9, and M8
measured over-planning, over-persistence, and over-memory. The verdict the table
forced: **justified architecture = SINGLE.** See
`docs/evaluations/architecture-day19.md` for the §9 table.
