# Day 19 Notes — Multi-Agent Architecture

> Notes answering `docs/day-plan/day-19.md`.
> Eighteen days built a machine that *reads* (Days 6–10), *acts* (Day 11), *drives itself* (Day 12), *survives failure* (Day 13), *computes* (Day 14), *defends itself* (Day 15), *thinks before acting* (Day 16), *remembers what it did* (Day 17), and *carries a lesson — only if the lesson earns it* (Day 18). The curriculum's next question is the one it tells us not to ask, told to ask it properly: **"determine whether multiple agents are actually useful. Do not assume they are."** The comparison is **single agent vs specialized agents** (Knowledge / Data / Engineering), the deliverable is *either* a justified single-agent architecture *or* a justified multi-agent architecture, and the governing clause is the day's whole thesis: **the justification matters more than the number of agents.** Day 19 does not build a multi-agent system. It builds the **comparison that decides whether one is called for** — and it discovers, by the series' own logic, that the question was never "should we add agents": an agent is a loop with tools, a plan, a policy, and memory, and the curriculum's three "specialists" are already the single loop's three tool groups wearing Day 15's roles. Splitting therefore means *duplicating the loop per role* — and the day's verdict, whatever it is, must emerge from a fixture table, because the curriculum says the justification — not the count — is the deliverable.
> The single idea: **specialization is done by role and privilege, not by loop count — and the only honest claim a second loop can make is "context isolation" or "independent concurrency," measured, never asserted.** The day's machinery is two topologies built from the exact same components (Topology A = Days 12–18 plus a deterministic role-context router; Topology B = an orchestrator loop delegating to loops the factory already makes, sharing Day 18's store), one harness that presents every fixture to both, one judge that checks the series' invariants on each side — and one new invariant, the day's headline: **`delegated ⊆ returned`**, the requirement that nothing cross a loop boundary except a *receipt*, and nothing be claimed at a hop its bearer did not return. The routing, the delegation contract, the cost ledger, and the containment are `MECH` — offline and free; whether a *real* orchestrator beats a real single loop is the same real-gateway verdict the series has parked for Day 21/22 since Day 14. What Day 19 actually delivers is a **verdict line the fixture table forces**, and on the evidence the series already carries — Day 15's roles, Day 16's planner, Day 18's memory — the early hypothesis is that the justified architecture is *single*, with role-context routing, and that the day is small because correctness makes it small. The table gets the final say.

---

## 1. The Loaded Question — "Multiple Agents" vs "More Code"

The curriculum's Day 19 is the rare day whose objective is a *rebuke*: determine whether multiple agents are actually useful, **do not assume they are**. The apprenticeship wrote it twice over — Rule 6 lists *multi-agent systems* beside Kubernetes and microservices under *no premature complexity*, and §9 files *multi-agent hype* under what this month deliberately refuses to prioritize: "only use multi-agent architecture if it solves an actual problem." Day 19 is those two rules given a courtroom.

The load-bearing word is **useful**, and the day must read it as Rule 4 reads every adjective: not "clever," not "architecture-shaped," not "the diagram in the curriculum says so." Useful means: *on the measured axes — task success, evidence grounding, cost, latency, failure containment — the split beats the single agent it replaces, on tasks that actually occur, enough to pay for what splitting costs.* And the deliverable clause is the second half of the sentence: the justification matters more than the number of agents. A multi-agent system that cannot justify each loop against the monolith on the table is not a "justified multi-agent architecture"; it is a Rule-6 violation wearing a diagram.

The day's first real move is to notice how much of the debate was already settled. The curriculum's three candidate "agents" —

- **Knowledge** — Days 6–10: doc retrieval, code retrieval, reranked evidence,
- **Data** — Days 14–15: schema-grounded read-only SQL,
- **Engineering** — Day 11's git/run tools, Day 10's code questions —

are **tool groups and privilege sets on one loop**. The series has done "specialization" continuously without once duplicating the loop — Day 11 specialized by *tool schema*, Day 15 by *role and policy*, Day 16 by *plan*. So the curriculum's `Orchestrator → {Knowledge, Data, Engineering}` diagram is not a blueprint that arrives with authority; it is a **hypothesis** that duplicating the loop per role earns its cost, and Day 19's duty is to run that hypothesis against the null.

The vocabulary that keeps the argument from blurring:

- **role** — a tool set + a policy (Day 11 + Day 15). Exists today, on one loop, at zero loop-cost.
- **agent** — a role + its own loop (Day 12) + its own budget + its own memory + its own termination. The thing Day 19's hypothesis proposes to duplicate.
- **orchestrator** — a loop whose *tools are other loops*: a delegation surface that can fail and must be audited like any tool — Day 11's "schema as the model's interface," where the interface is another agent's exit.

And the two failure shapes that will define the fixture set: **over-orchestration** (a delegation decision that adds cost and risk to a task the single loop already answers — Day 16's `over-planned?` counterweight promoted to the architecture level) and **the handoff leak** (a specialist's finding mis-stated when it crosses the boundary — the day's "Chinese whispers," a Day 13 fabrication mode living on the delegation seam).

---

## 2. Rule 6 and the Burden of Proof — Single Is the Default

If Day 19 is a courtroom, the null hypothesis is not "nothing." The null is the **cheapest proven thing**: the single loop Days 12–18 already run, already carrying all three tool groups, all three Day 15 roles, and Day 18's memory. Multi-agent carries the burden of proof, and Rule 6 names the standard: no premature complexity.

Three costs of a second loop, every one of which becomes a measured column:

1. **Context duplication.** The orchestrator's context does not shrink when it delegates — it *grows* by the receipt it must hold to ground its answer — and the specialist's context is separate, so the same facts are billed twice. Delegation does not *replace* context; it *ships a copy of it*.
2. **A new failure surface.** A handoff can fail like a tool call (Day 13's whole taxonomy), plus three shapes only delegation creates: routing to the wrong specialist, a specialist that returns ungrounded evidence, and an orchestrator that fabricates a delegation it never made. Day 13 proved the loop needs guards *inside*; Day 19 must decide if guards *between* loops buy anything.
3. **Shared-state coordination.** Two loops touching Day 17/18's store — or two stores, or one closing the other's file — is a concurrency problem the single loop never had. Day 17 explicitly deferred multi-process synchronization as *named, not built*; Day 19 inherits that note early.

Against those costs, exactly two honest reasons specialization might still pay:

1. **Context isolation.** A task mixing data-heavy and knowledge-heavy work can crowd a single context until one surface's evidence is squeezed out — Day 9's budget floor applied to the whole loop, not just the context builder. A specialist keeps each surface clean. But the measurable claim is *total tokens for equal grounding*, never "looks cleaner."
2. **Independent concurrency.** Two specialists investigating in parallel genuinely cut wall-clock *if* their work is independent *and* the orchestrator's aggregation does not serialize it back. Day 16's plan-as-DAG is the honest test: parallelizable branches, measured end-to-end.

Everything else the multi-agent pitch usually offers — "separation of duties," "scalability," "one agent per concern" — was already delivered by role-policies and tool groups at zero loop-cost. That is the standard. The counterweight is the same control the series has kept since Day 16's P9, Day 17's S9, Day 18's M8: **whatever Day 19 concludes, a topology that changes a memory-free, delegation-free answer is a bug.** Over-orchestration is measured the way over-memory was measured: as a no-change row.

Two pre-commitments follow. **Measure before building the topology** — the harness (a single fixture router that can present any task to either topology) is the day's first build, not the orchestrator, not the specialists; agents are treatments, the harness is the experiment. And **the day ends with a verdict line** — `justified architecture = single` or `justified architecture = multi (scoped to: …)` — because the curriculum's deliverable is *justified*, and a day that ends without the verdict line failed, while a day that ends with a verdict the table cannot support failed worse.

---

## 3. The Comparison — Two Topologies, Same Fixtures, One Judge

Day 19 is designed as an **A/B with one fixture router**, so the only variable is topology:

```text
                    the day's question set (Days 12–18 families + orchestration fixtures)
                                         │
                     ┌───────────────────┴────────────────────┐
                     ▼                                        ▼
         Topology A — the single loop                Topology B — orchestrator + specialists
   (Days 12–18 as-built, + role-context routing)   (Knowledge / Data / Engineering loops
   one context · one budget · one planner ·          under an orchestrator; role-scoped tools,
   Day 15 policy by tool group · Day 18 memory)      per-loop budgets, shared Day 18 store)
                     │                                        │
                     └───────────────────┬────────────────────┘
                                         ▼
                          the judge: identical exit checks
       (expected evidence ⊆ retrieved · cited ⊆ retrieved · conclusion ⊆ evidence
        · delegated ⊆ returned · cost · latency · steps · failures)
                          →  the verdict row, per fixture
```

**Decision 1 — the single loop is Topology A, and it is not "no architecture."** Days 12–18 plus the one upgrade the comparison genuinely owes it: **role-context routing** — a deterministic layer (Day 8's `rule_section` machinery, re-pointed at tool groups) that picks the entry surface (`knowledge | data | engineering`) for a question before the loop starts. That is the honest half of specialization: route the *context*, keep the *loop*. It is `MECH` — a rule-table decision, zero chats — and it is the single-agent answer's entire claim to "specialist-grade context": the specialist's *surface* without the specialist's *overhead*.

**Decision 2 — Topology B is the curriculum's diagram, built as honestly as the comparison demands.** The orchestrator's tools are three specialist loops, each exactly Topology A's tool group and policy, each a full Day 12 loop with its own budget and termination, each — the day's first real joint — writing to and reading from **Day 18's shared store**. The curriculum proposes agents sharing the memory Day 18 made worth sharing; B is that proposition, and the alternative (N private stores) is a comparison row (G6), not a default.

**Decision 3 — the delegation contract is a receipt, not prose.** The only thing allowed across the orchestrator↔specialist boundary is a **receipt**: a result carrying the specialist's `task_id`, its evidence, its conclusion, and its provenance — the exact episodic record Day 18 defines. The orchestrator *cites the receipt*; it never paraphrases it. A paraphrase is the day's cardinal leak, rejected by construction, the same way Day 18 refused to let a receipt read as a lesson. The receipt makes the day's headline invariant, **`delegated ⊆ returned`**, checkable in code: every fact the orchestrator's answer attributes to a specialist must appear in that specialist's returned evidence.

**Decision 4 — failure is a return value of the delegation, and only the delegation.** When a specialist refuses, times out, or crashes (Day 13 at loop granularity), the orchestrator receives a **failed-delegation receipt**: it may retry once (Day 4's retry discipline), route around (Day 16's replan at the delegation level), or answer with the failure named. It cannot fabricate a specialist run. This is Day 11's "refusal-as-a-return-value," promoted to the agent seam, and the trace shows `attempted → returned | failed(reason)` — never a hole.

The day's invariants, pre-committed:

1. **`delegated ⊆ returned`** — the day's headline; an orchestrator's answer cites only receipted evidence from its specialists. A fourth translation of `cited ⊆ retrieved`, across the loop boundary.
2. **Ballots, then verdicts** — every fixture is measured on both topologies before any conclusion; a fixture only one topology can run is a routing bug, not a win.
3. **The control** — machinery off ≡ Days 12–18; memory and orchestration both switchable, both measured (G9).
4. **No loop without a ledger** — every agent, real or candidate, books its chats and tokens to the per-fixture row, because the entire question is *cost for equal grounding*.

---

## 4. Topology A — the Single Loop, Plus Role-Context Routing

The baseline is already implemented; Day 19's discipline is to **treat it as implemented** and add only the one seam the comparison needs. A benchmark run of Days 12–18's fixture set, through the new A-B harness with routing off, *is* Topology A's unit test — and it must be green **before a single line of Topology B exists**, or the comparison is comparing against a moving floor.

The role-context router is the whole A-side addition, and it is deliberately boring: a deterministic rule layer (Day 8's `rule_section` machinery — the same token-gap family table that fixed C1 — re-pointed at tool groups) that classifies a question into one of the three surfaces and seeds the loop's first context accordingly. It costs zero chats, is transparent (a `routed_to` field in the trace), and is overridable (a question that explicitly names a surface wins), mirroring Day 8's debuggable filter. The honest claim it encodes: **the specialist's *surface* is real and cheap; the specialist's *loop* is the part that must earn its airfare.**

The comparison is pre-registered against a *written-ahead* A row — success, steps, chats, tokens, latency, `cited ⊆ retrieved`, `conclusion ⊆ evidence` — recorded before B exists, so the verdict cannot be rigged by measuring B first. Day 7's "expected before any run" discipline, applied at the architecture level.

And the day admits its own smallest-correct-change reflex (Day 16's `over-planned?`, promoted): **if the table says single, the deliverable is the two-artifact set** — the router plus the verdict document — and the day is small because correctness makes it small. Rule 2's "build before abstracting" has a corollary the day honors: *don't abstract at all until the measurement says the abstraction is real.*

---

## 5. Topology B — the Orchestrator and the Specialists

B is built from the pieces the series already proved, and that is the architectural honesty of the whole exercise. The three specialists are **parameterized single loops**, not new programs by hand: `make_agent(role)` = a Day 12 loop bound to `role`'s tool group — Knowledge to doc/code retrieval + rerank, Data to schema + read-only SQL with Day 15's policy embedded, Engineering to git/run/test — with its own budget, termination, and policy. A specialist is the existing loop with a tool-list slice. B is therefore not "three new systems"; it is *one seam* (orchestration) over the existing one.

The orchestrator is likewise a loop whose tool list is `[delegate(role, question)]`, and it reuses every guard the series built: it plans first (Day 16 — the delegation DAG *is* a plan), routes each plan node by the §4 rule-table, receives receipts, and aggregates by citation; its own loop respects Day 12's transcript-state rule, Day 13's guards (bounded delegations, timeouts, a one-rung retry ladder whose extra rung is *reroute*), and Day 6's citation contract applied to receipts.

Bounded delegation is policy, exactly as the series parameterizes everything: `MAX_DELEGATIONS` per question (Day 12's `max_steps` at orchestration level), `HANDOFF_TIMEOUT` per delegation (Day 13's timeout, between loops), `DELEGATION_RETRY = 1` (Day 4's gateway-retry discipline for a manual seam). A chain exceeding the bound terminates with a **named failing receipt** — Day 13's non-termination fixture, reproduced between loops.

Groundedness stays per-specialist and end-to-end: each specialist answers only from *its own* retrieval (its internal `cited ⊆ retrieved` rows), and the receipt it returns is its Day 18-style episodic record — evidence, conclusion, provenance — not a summary sentence. The trace carries both the specialist's internal grounding rows and the orchestrator's `delegated ⊆ returned` row, so a leak is locatable to the exact hop.

And the ledger keeps the number most likely to kill B's excuse: the cost of a delegation is the **union of both contexts** (`orchestrator + specialist`), because delegation duplicates rather than replaces context (G5).

---

## 6. The Delegation Contract — Receipts Across the Boundary

The receipt is the day's unit of delegation, and it is nothing the series has not already made: `{ task_id, principal, role, question_asked, evidence[], conclusion, provenance {run_id, step, source: "specialist:{role}"}, status: returned|refused|failed }` — the `kind=episodic` record of Day 17, promoted to the currency of an inter-loop handoff. Nothing else crosses the boundary; a specialist's "finding" that is not a receipt is not a finding.

`delegated ⊆ returned` is enforced as a code check, the day's fourth honesty invariant walking in lockstep with `cited ⊆ retrieved`, `conclusion ⊆ evidence`, `asserted ⊆ returned`, `recalled ⊆ authored`, and `authored ⊆ grounded`. After the orchestrator answers, every fact its trace attributes to a specialist (`attributed_to: {role, task_id}`) is checked against that specialist's receipt evidence; a fact attributed but absent from the receipt is a **handoff fabrication**, flagged in red — the Chinese whisper caught exactly as Day 13 caught a fabricated citation, now across a seam.

Aggregation is by **citation, not absorption** — Day 6's rule at the loop seam: the orchestrator merges receipts into its context as blocks to cite, exactly as Day 18's recall served episodes *as episodes*. It may combine two specialists' evidence to support one conclusion; it never re-voices a specialist's finding as un-cited prose. M7's "a receipt is never a lesson" is inherited across the boundary.

The orchestrator's refusal stays honest: a question that routes to no role, or to a role with nothing covering it, is refused like Day 7 refused. "I decomposed, delegated, and nothing returned evidence" is a valid terminated answer; "I commissioned a specialist who must have found something" is the cardinal lie (G8).

And the day records its honest limit in Day 18's voice: **the orchestrator cannot see inside a specialist's loop.** It holds the receipt, not the transcript. For a contested conclusion it has exactly Day 18's re-distillation move — delegate again with a pointed question — or it surfaces the disagreement. A delegation receipt is Day 17's closed store, not an open window; "you cannot see inside" sits beside "you cannot un-distill" as the day's honesty notes.

---

## 7. Shared Memory and Policy Across Roles

Day 18's store is the sharing substrate, and the day resists building anything new on it: specialists read `.recall(question, principal)` against the shared store (keying untouched, cross-principal reads still empty), and the distiller runs over a specialist's finished task records exactly as it ran over the single loop's — because the receipt a specialist returns *is* the episodic record the store already holds. The curriculum's "agents sharing memory" therefore costs **zero new memory machinery**: §5's `make_agent` plus Day 18's store, and one genuinely new policy — *who may write*.

The write policy is the day's one real security decision, settled Day 15's way (privileges beat parsers). Default: every specialist writes episodic records only under its own `principal` + `role` provenance, exactly as the single loop did. The orchestrator may read all its specialists' records — it is the coordinator — but **the orchestrator writes only orchestration records (routing, delegations, verdicts), never specialist-episodes.** A router that authors investigations claims evidence it never personally grounded; M7's boundary holds across roles.

The sharing regimes are compared as measured rows, not prose: Topology B shared store vs B private per-role stores vs Topology A with Day 18 memory — three columns answering Day 18's own closing question, *"is a single agent with real memory strictly cheaper than N agents with N private stores?"* The expectation is shared > private (a private store is N copies of the distiller and N shelf-sized recall pools), but the expectation is a hypothesis until the row is filled (G6).

Day 17/18's retention invariants apply unchanged across roles: receipts expire and delete like any episodic record; `forget` re-grounds every concept that cites a deleted receipt (M5) even when the concept lives on the shared shelf and the receipt was authored by one specialist; cross-principal reads stay empty (M6); and M8's control (memory off ≡ the days without) is re-run through B's loops, because a shared store must not change a memory-free specialist's answer either.

The `PENDING` sails with the fleet: the routing, the contract, the ledger, and the containment are `MECH` — deterministic functions of topologies, receipts, and roles, offline and free. Whether a **real model's orchestrator actually delegates better than the single loop answers** on real Saleor questions is the real-gateway question, parked where the series has parked it since Day 14 — **Day 21/22's benchmarks on Day 19's harness**, the same days that own Day 18's distiller and Day 16's planner. Day 19 ships the court and the verdict line; the real-verdict column stays `PENDING`.

---

## 8. Test: The Architecture Set (G1…G9), and the Court

Expected behavior written **before** either topology runs, one fixture per property plus a control:

| ID | What it proves | Input / injection | Expected |
|---|---|---|---|
| G1 | the specialist wins | a two-surface task (data-heavy + doc-concept, e.g. revenue + `total_net`) where one context crowds one surface | B beats A on the measured row (total tokens for equal grounding, or latency when truly parallel); the win is **scoped** to exactly the split that won |
| G2 | the monolith holds | the Days 12–18 single-surface fixtures, unchanged | B does **not** beat A on success, and the cost row shows the duplication; a tie is recorded as a **B loss**; over-orchestration on a one-step question is the fixture's failure shape |
| G3 | no handoff without a receipt | a cited receipt (pass); a **Chinese-whispers injection** — a receipt paraphrased into the answer, or attributed to a run never received | `delegated ⊆ returned` fires; the fabrication is rejected |
| G4 | privilege containment | a delegation that tries an out-of-role tool; Day 15's X-set re-run through both topologies | refused at the specialist's policy gate; no escalation via a crafted delegation question |
| G5 | the cost ledger is the judge | both topologies on the same question set | per-fixture tokens/latency with B billed as *union of both contexts* per delegation; B "wins" only when its total beats A's on the stated axis |
| G6 | the memory reducer | one question set, four configurations | single+memory vs B-shared vs B-private vs B-no-memory — measured, not asserted |
| G7 | per-hop grounding | receipts carrying evidence into the orchestrator's context | every specialist `cited ⊆ retrieved` internally; leaked hops across the seam **== 0** |
| G8 | failure containment | a specialist refuses / times out / crashes | a **failed-delegation receipt** or named fallback; never a fabricated specialist run; other delegations survive |
| G9 | **control** | all machinery **off** (orchestration off, routing neutral, store empty) | Days 12–18 fixtures run **unchanged, exactly as green** (A…F…Q…T…X…P…S…M) — over-orchestration is a bug, Rule 6 measured |

The `MECH`/`PENDING` split is Day 7's, exact as always. **G1, G2, G3's check, G4, G5, G6's routing+ledger, G7, G8, G9 are `MECH`** — deterministic functions of the harness, the router, the delegation contract, the shared-store keying, and stored payloads: offline, free, reproducible, and the whole of the day's deliberative payload. **The real verdicts are `PENDING`**: whether a *real* model's orchestrator delegates better than the single loop on real Saleor questions, whether real recall beats fresh investigation through a specialist's split context, and whether B's cost row stays below A's at real token prices — all **Day 21/22's** benchmarks on Day 19's harness. The stub proves the **court**; a real key proves the **verdict**.

Three obligations, unchanged in spirit:

1. **No regression through both topologies.** Days 7–18's fixtures are the gate. Days 12–18 through A is the baseline's unit test (must be green *before* B exists); the same set through B carries the day's real question — B must **at least tie** every fixture A answers, and a fixture B fails that A answers is a **topology failure to fix**, not a fixture to weaken.
2. **The day's counterweight.** The G9 no-change row answers Rule 6's economy with a measurement, not a vibes: adding loops is *supposed* to leave single-loop answers untouched, because a topology that changes a memory-free, delegation-free answer is a bug arriving through the front door.
3. **Isolation, one owner per symptom.** A handoff that leaks evidence → the **receipt contract** (`delegated ⊆ returned`). A specialist that answers ungrounded → that specialist's **own `cited ⊆ retrieved` owner** (Day 6/7). Over-orchestration → the **router** (it delegated where the rule-table said single-path). A cross-role privilege escape → the **specialist's policy gate** (Day 15). A cross-principal store read → **Day 18's keying**. A topology that changed a memory-free answer → the **G9 control**. Say which, never vibes.

---

## 9. Measure: Architecture as a Contract, Two Topologies, One Ledger

Rule-4 as an architecture table, one row per fixture, both topologies, every cell a real run's:

| Fixture | Topology A | Topology B | Winner | Steps | Handoffs | Tokens (A / B-union) | Latency | Failure? | delegated ⊆ returned | Cited ⊆ retrieved | concluded ⊆ evidence | Cost | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| G1 specialist win | … | … | B (scoped) | … | 1–2 | … | … | no | **ok** | ok | ok | … | split |
| G2 monolith holds | … | … | **A** (tie → A) | … | 0–1 | … | … | no | **ok** | ok | ok | … | single |
| G3 receipt contract | — | … | court | … | 1 | … | … | injection | **rejected** | ok | ok | … | contract |
| G4 containment | … | … | both | … | 1 | … | … | refused@gate | **ok** | — | — | … | role policy |
| G5 cost ledger | … | … | cheaper | … | … | union billed | … | no | **ok** | ok | ok | … | B miss |
| G6 memory reducer | +mem | shared/priv/none | measured | … | … | … | … | no | **ok** | ok | ok | … | answer |
| G7 per-hop grounding | … | … | both | … | ≥1 | … | … | leaks==0 | **ok** | ok | ok | … | ok |
| G8 containment | — | … | court | … | 1 | … | … | failed@hop | returned | — | ok | … | ok |
| G9 control | off | off | — | … | 0 | 0 | … | no | — | — | — | 0-chat | **machinery off ≡ Days 12–18** |

Three columns carry the day: **`delegated ⊆ returned = ok`** on every B row (the headline — the reader can't be mailed a fact no specialist returned, and the orchestrator can't mail one either), **B ≥ A enforced on the G2-family rows, with a tie recorded as an A-win** (the cost truth — "clean architecture" is not a column), and **G9's row** — machinery off ≡ Days 12–18 — the counterweight on Rule 6, measured exactly the way P9, S9, and M8 measured over-planning, over-persistence, and over-memory. The **Cost** column is honest in the series' ledger: Topology A adds **0 chats** (a rule-table router), B's *mechanics* add **0 chats** (routing, receipts, ledger, containment are deterministic), and B's *operation* bills what delegation genuinely bills — the specialist's own loop chats **plus the duplicated orchestrator context** — which is exactly why G5's rows are the day's first *architecture* ledger, at zero new spend.

The negatives column, with owners: whether a real orchestrator beats a real single loop is **Day 21/22**; whether shared memory changes the answer at scale is **Day 21** (Day 18's distiller and recall own it); *multi-agent observability* — one trace following one question through three loops — is **Day 24's** distributed trace, named now; *agent-to-agent negotiation or hand-off of running state* is **named, not built** (receipts are deliberately stateless — the only seam); and *parallel specialists running concurrently mid-question* is **the day's scoped-if-won prize** — only G1's measured parallel win could justify it, and even then it is a fixture row, not an entitlement. Empty negatives mean the day learned nothing; these have owners and verdecides.

---

## 10. Documents

- `docs/concepts/multi-agent.md` — the decision discipline: an agent is a loop (roles ≠ agents), the single-agent default and Rule 6's burden of proof, the three real costs of a second loop (context duplication, a new failure surface, shared-state coordination), the only two honest reasons to split (context isolation, independent concurrency), role-context routing as the honest half-split, the orchestrator-as-a-loop design, **`delegated ⊆ returned`** as the delegation contract, and the "justified ≠ many" verdict rule.
- `docs/evaluations/architecture-day19.md` — the §9 architecture table with `delegated ⊆ returned = ok` on every B row, the G1…G9 pre-registered fixtures and the G9 control, the topology configs and defaults (`AGENT_TOPOLOGY`, `SPECIALIST_ROLES`, `MAX_DELEGATIONS`, `HANDOFF_TIMEOUT`, `DELEGATION_RETRY`, `ROLE_CONTEXT_ROUTING`), the no-regression lines (Days 7–18 through both topologies, machinery on *and* off), the A-vs-B cost rows, and the real-verdict `PENDING`.
- PROGRESS rollup: Day 19 mechanics move to `DONE` under the stub (the A/B harness, the role-context router, `make_agent(role)`, the receipt contract with `delegated ⊆ returned`, the delegation cost ledger, failure containment, the shared-store write policy, and the G9 off-switch — all verified offline); the *verdict* cells stay `PARTIAL`/`PENDING` until a real gateway run answers "does an orchestrator beat the single loop" on real Saleor questions — Day 21/22's first move.

---

## 11. Daily Learning Record

### Problem

Eighteen days produced an agent that is competent, honest, grounded, planned, and — since Day 18 — capable of carrying a lesson from one task to the next. The curriculum now poses the first question whose *correct answer may be "do nothing"*: **"determine whether multiple agents are actually useful; do not assume they are."** The problem is not to build a multi-agent system — the problem is that the series' own apprentice document *warns* against one (Rule 6 names multi-agent systems as premature complexity; §9 files multi-agent hype as something only to use if it solves an actual problem) while its architecture diagram of the target system shows an orchestrator and three specialists. The genuine difficulty: **an agent is a loop with tools, a plan, a policy, and memory — and the curriculum's three "specialists" are already the single loop's three tool groups under Day 15's roles.** So Day 19 must decide whether duplicating the loop per role earns its cost, with "measured on the axes that matter" and "the justification matters more than the number of agents" as the binding criteria — and it must produce a *verdict the fixture table forces*, on either side, without inventing complexity or refusing a legitimately earned one.

### Concept

**Multiple agents are a measured decision, not an architecture trend — and the measurement's first fact is that an agent is a loop, so "add an agent" means "duplicate the loop," which costs context duplication, a new failure surface, and shared-state coordination.** The day's machinery is a **two-topology comparison over one harness**: **Topology A** (Days 12–18, the null hypothesis, plus a deterministic **role-context router** that seeds the right tool surface — the honest half of specialization: specialize the *context*, keep the *loop*), and **Topology B** (the curriculum's diagram built honestly: an **orchestrator** loop whose tools are three `make_agent(role)` specialists sharing **Day 18's store**). The contract that makes B safe is the day's headline invariant, **`delegated ⊆ returned`**: nothing crosses a loop boundary except a **receipt** (task record, evidence, conclusion, provenance), and an orchestrator's answer cites only receipted evidence — a handoff paraphrase is a *handoff fabrication*, caught by code like Day 13 caught a fabricated citation. Failure is a return value of the delegation (retry once, reroute, or name it — never invent it); cost is the union of both contexts per delegation. The routing, contract, ledger, and containment are **`MECH`**, offline and free; the real-model verdict is **Day 21/22's** bench on the harness. The deliverable is a **verdict line the fixture table forces** — justified single or justified-scoped multi — with G9 as the counterweight: machinery off ≡ Days 12–18, because over-orchestration is a bug measured exactly the way over-memory was.

### Understanding

The unlock is Day 19's inversion of the obvious: **the curriculum's diagram is a hypothesis about where specialization lives, and the series has been testing it since Day 11 without adding a single loop.** Specialization has four homes — the tool schema (Day 11), the privilege/policy (Day 15), the plan (Day 16), the memory (Days 17–18) — and only a fifth candidate, *the loop itself*, remains. The re-readings landed hardest in order: **(1) an "architecture" is justified by what it does to the fixture table, not by looking like the end-state diagram** — the diagram shows an orchestrator and three agents, but it is the *justification* clause that is the deliverable, and the diagram is what the justification must overcome, not what it inherits. **(2) Delegation is a tool call, and the tool-call discipline is complete** — Day 11's schema-as-interface, Day 11's refusal-as-return-value, Day 14's assert-then-return, Day 17's provenance, Day 18's receipt-not-lesson: every guard the single loop needs, the seam between loops needs a translation of, and `delegated ⊆ returned` is four old invariants wearing one new jacket. **(3) The cost that kills most multi-agent pitches is context, not model quality** — delegation duplicates the orchestrator's context instead of replacing it, and until a fixture row shows the union cheaper than the monolith, the argument is decoration. **(4) Memory changes the question from "should two agents talk" to "should two loops read the same shelf"** — Day 18 made sharing cheap, which is exactly why the decision is now genuinely open and measurable, and why Day 18's suspicion (one agent with memory beats N agents without) is a row to fill, not a line to assert. And the day's own sobriety: **all of the deliberative machinery is free** — the court costs 0 chats; the only thing that could cost real money is a real verdict on real questions, which is Day 21/22's purchase, not Day 19's.

### Implementation

- `ai_os/agent.py` — `make_agent(role)` (the existing Day 12 loop parameterized by tool group + Day 15 policy: knowledge / data / engineering), and the optional **role-context router** (day-8-style rule table → `routed_to`, `MECH`, 0 chats, transparent + overridable).
- `ai_os/orchestrator.py` (new) — the delegation loop: `delegate(role, question)` returns a **receipt**; `MAX_DELEGATIONS`, `HANDOFF_TIMEOUT`, `DELEGATION_RETRY = 1`; failed delegations return `failed(reason)` receipts; dispatch follows Day 16's plan-DAG.
- `ai_os/receipt.py` (new) — the contract: receipt struct + `check_delegated_subset_returned(orchestrator_answer, receipts)` (the handoff-fabrication check), and `check_per_hop_grounding(specialist_trace)`.
- `ai_os/memory.py` (extended, minimally) — `make_agent`'s specialists share Day 18's store with `principal`+`role` provenance; the orchestrator writes orchestration records only; distiller reads across a role's records unchanged.
- `ai_os/trace.py` (extended) — topology columns: `topology`, `routed_to`, `delegations[] (role, task_id, status, cost)`, `attributed_to`, `delegated_subset_returned`, `context_union_tokens`, `handoffs`.
- `tools/architecture_smoke.py` (new) — the A/B harness: runs Days 7–18 fixtures through both topologies, G1–G9 (incl. the Chinese-whispers injection, the cross-role escape, the G9 off-switch), plus the per-fixture cost ledger.
- `docs/concepts/multi-agent.md`, `docs/evaluations/architecture-day19.md`.

### Failure

1. **The diagram inherited as a blueprint.** B is built because the curriculum's end-state diagram shows three agents, and the "justification" is written *after* the build to fit it. The anti-fix is the verdict-first rule: the G1…G9 table and the verdict line are written before B exists (G2's expected A-win on single-surface fixtures pre-registered, not discovered).
2. **The handoff paraphrase (the day's cardinal sin).** The orchestrator returns a specialist's finding in its own words, and the Chinese-whispers effect mails a fact the specialist never returned. `delegated ⊆ returned` is the code check, and G3's injection is the signature.
3. **The fabricated delegation.** The orchestrator answers as if a specialist ran when no receipt was received — the recursion of Day 13's fabricated citation. Failed delegations must be *returned* (G8), never bridged by imagination.
4. **Context duplication billed as cleanliness.** B looks better-organized and is declared the winner while its union-context token row is double A's. G5's ledger is the correction: B wins only when its *total* beats A's.
5. **Role-policy escape.** A crafted delegation question ("pass this to the data agent and select anything") walks a specialist into an out-of-role tool. Day 15's policy gate must sit on *every* specialist's exit, and G4 is the probe — privileges beat parsers, across loops.
6. **Over-orchestration.** A question the single loop answers in two steps is routed through a delegation chain for architectural elegance. G2 (and the G9 control) is the guard: over-orchestration is a bug measured the P9/S9/M8 way.
7. **The shared store's new reader.** A specialist reads by the wrong principal prefix, or the orchestrator claims a specialist's episode as its own, and a cross-role leak becomes a cross-*loop* leak. Day 18's keying and the "orchestrator writes no episodes" rule are the guards (G6/G7).
8. **The empty-privilege control forgotten.** The whole experiment runs with the store empty and memory off and claims "multi works" — the same error M8 already lawyered for Day 18. Every G row alternates the memory on/off and store empty/full switches; a topology conclusion that does not survive an empty store is not a conclusion.

### Debugging

Everything Day 19 adds is `MECH` under the stub: routing is a rule table, the delegation contract is set membership over receipts, the ledger is the union-context arithmetic, containment is the receipt status machine, and sharing is keying. The debug loop is Day 7's, re-rooted onto the topology owners:

- **A handoff that leaks evidence** → the **receipt contract** owns it — `delegated ⊆ returned` flagged a fact in the answer that no receipt carries; inspect `attributed_to` vs receipt evidence, not the orchestrator's prose.
- **A specialist that answers ungrounded** → that specialist's **own `cited ⊆ retrieved` owner** (Day 6/7) — the leak is inside its loop; its trace row says where.
- **Over-orchestration** → the **router** owns it — the rule-table delegated where the table's own `routed_to` says single-path; the rule fired wrong or was silent where it shouldn't be.
- **A cross-role privilege escape** → the **specialist's policy gate** (Day 15) — a delegation question slipped a foreign tool through; the gate sleeps, or the receipt was forged by routing.
- **A cross-principal store read** → **Day 18's keying** — the specialist read under the wrong prefix, and it is a Day 17 S8 failure arriving through the new loop boundary.
- **A topology that changed a memory-free answer** → the **G9 control** owns it — the addition is not additive, and the offense is the change, not the topology.

The trace says which, never the vibes; the receipt is the unit of attribution across loops, and each symptom has an owner from the day it was designed.

### Evaluation

The §9 architecture table in `docs/evaluations/architecture-day19.md`: per-fixture both-topology rows with `winner`, `steps`, `handoffs`, `tokens (A / B-union)`, `latency`, `failure?`, and the four columns that carry the day — **`delegated ⊆ returned = ok`** on every B row (the headline: the reader can't be mailed a fact no specialist returned, and the orchestrator can't mail one either), **`cited ⊆ retrieved` and `conclusion ⊆ evidence`** untouched inside every loop, and **the G9 row** (machinery off ≡ Days 12–18). The numbers a future observer can carry: whether any C2 answer cited a fact no delegation returned (never), whether any specialist answered outside its role's tools (never), whether any delegation was manufactured rather than received (never), whether any topology changed a memory-free answer (never), and — the day's whole question — **which topology each fixture's cost row picks**, with ties lawyered to A (the cheaper loop), so the verdict line at the top of the document reads what the table forces, not what the diagram prefers. The `PENDING` line, plainly: whether a *real* orchestrator delegates better than the real single loop on real Saleor questions is **Day 21/22's** judgment on Day 19's harness — a court is only as good as the verdict it will eventually bear.

### Architecture

The claim of the day is the series' claim, one scale further: **the system asked whether it should split into subsystems, and the answer was *measured, not assumed* — because the split's machinery was already built, and a loop is a loop is a loop.** No new agent program exists (the specialists are `make_agent(role)`, the orchestrator is Day 12's loop with one tool), no new store exists (the sharing substrate is Day 18's, keying untouched), no new honesty contract — `delegated ⊆ returned` is `cited ⊆ retrieved` translated across the seam, the fourth in the line the series has kept since Day 6. The routing and the contract and the ledger are free, and the OFF switch (G9) proves the whole addition optional and *silently* optional — a topology that changes a memory-free answer is a bug, the final, complete form of Day 16's `over-planned?`, Day 17's S9, and Day 18's M8, now standing on Rule 6. The seams are named and owned: the real verdict is **Day 21/22's**; multi-agent observability is **Day 24's** trace; agent-to-agent state hand-off is named, not built (receipts are deliberately stateless); parallel specialists are the scoped-if-won prize, gated on G1's measured row. **Day 11 gave the loop its hands, Day 12 its reflex, Day 13 its guards, Day 14 its math, Day 15 its privileges, Day 16 its agenda, Day 17 its memory, Day 18 its lessons — and Day 19 asked whether the loop should be many, and measured the answer instead of assuming it. The justification — not the number of agents — is the deliverable, and the number is whatever the table says it is. Day 20 inherits a system whose architecture is *decided*, and the decision was made for $0.**

### Questions

- The day's honest verdict will almost certainly be *single* under a stub and *scoped-split* for a few G1-class tasks — does "one agent with role-context routing + Day 18 memory" have a name in the literature (route to tool-group context), and is the Day 19 framing — "specialize the context, not the loop" — the same claim the tool-agent and MCP ecosystems make, one abstraction layer up?
- Context isolation is one of the two honest reasons to split; but is a *single* loop with Day 9's budget floor and Day 10's role-filtered retrieval already capable of the same isolation, making the G1 "win" an artifact of a too-small context budget rather than a genuine property of splitting? Day 21's bench should re-run G1 against a larger-budget A.
- If B's cost row wins on a parallel task under a stub, is that a *latency* claim worth trusting, or an artifact of the stub's zero-cost specialists? The real orchestration gate (Day 21/22) must measure wall-clock through a real chat pair before "parallel pays" is ever written.
- Shared memory makes the multi-agent question *substrate-first* (who may read whom), not *topology-first*. Is the real Day 21 question therefore "Day 18's store + role-write policy," with the loop count as the last variable to sweep — and what would make a *distributed trace* (Day 24) the natural next consumer of the receipts the day minted?
- Does `delegated ⊆ returned` hold when the specialist is *another model provider* or a *human* (Day 27's loop)? The receipt is provenance already; generalizing it to a human-approval receipt sounds like Day 27's seam wearing Day 19's jacket — is that the intended continuity?

### Tomorrow

Day 20 — Evaluation Fundamentals: the curriculum stops trusting vibes about the system it just decided — *"stop judging AI quality manually"* — and Day 20 builds the **evaluation harness**: a golden dataset (question, expected evidence, expected answer, expected tools, failure category), regression testing, deterministic evaluation, and the honest limits of LLM-as-judge. Day 18's distiller, Day 16's planner, and Day 19's orchestrator are *waiting* to be judged; Day 20 is where the judge arrives, and Day 21 will take the stand. Day 19 hands over the verdict the fixture table forced and — more usefully — the **court itself**: a harness that can present any question to any topology and score the answer against pre-registered expectations, the exact machinery Day 20 formalizes into a benchmark. Day 12 was the reflex, Day 16 the agenda, Day 17 the memory, Day 18 the lesson, Day 19 the decision about who carries them — and Day 20 is where the system learns to **grade its own work**, so that Day 21 can grade the work honestly.