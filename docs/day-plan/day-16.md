# Day 16 — Agent Planning

## Objective

Days 6–15 built the machinery of a working agent: retrieval over two corpora (Days 6–10), tools with declared schemas (Day 11), a loop that drives them step by step (Day 12), guards against failure (Day 13), a fourth corpus for computation (Day 14), and a safe executor that survives an adversary (Day 15). Every one of those is **reactive**: the loop chooses its next step *after* reading the last observation — Day 12 called it "the missing else branch", and Day 13 hardened the reaction with a timeout wall, a fallback ladder, and a stop-on-repeat. Today the agent learns the reflective counterpart: **decide before acting.** The curriculum's task is a concrete multi-step investigation — *"Investigate why customer X could not complete checkout"* — which decomposes into eight steps (find customer → find checkout → inspect order state → inspect payment → search relevant documentation → form hypothesis → verify evidence → respond). Day 16 builds the **planner + executor workflow**: a **planner** that turns a question into an explicit, ordered, *validated* plan (the learn bullets: planning, decomposition), and an **executor** that runs the plan while tracking **execution state**, writing **checkpoints**, and applying a **retry strategy** — repair, retry, fallback, **replan**. Nothing below the planner changes: a plan's steps are the *same* registered tools (Day 11), executed by the *same* loop (Day 12), under the *same* guards (Day 13); a Day 15 `policy` rejection is a **wall** the plan must *route around*, never a retry it absorbs. Decomposition is Day 10's dependency map turned into an execution order — the plan is a **DAG of tool calls**, and the curriculum's eight-step list is one linearization of it. The honesty invariant generalizes one more time: `cited ⊆ retrieved` (Day 6), `asserted ⊆ returned` (Day 14), and now **`conclusion ⊆ evidence`** — the plan's final answer draws only on observations the plan actually collected, and the *hypothesize → verify* steps (6→7) are where that is enforced. The day's counterweight is explicit, like Day 15's false-reject rate: **planning is not free**, and a one-step question inflated into an eight-step plan is a bug, so the planner must degrade to the Day 12 loop when one step suffices. What a long plan *needs* — memory that outlives the run — is **Day 17's**, and Day 16 keeps execution state ephemeral and names that seam. The deliverable is the **Planner + executor workflow**.

## Duties Outline

### 1. Learn: Planning — the Reflective Counterpart to the Reactive Loop (45 min)

- [ ] Name what planning adds: Day 12's loop is a **reaction loop** — observe, decide the next step, repeat — and it is *stateless about intent*: it holds the last observation, not a model of where it is going. Planning is the **reflective** layer above it: an explicit agenda, produced *before* any tool runs, saying which steps exist, in what order, and what each is for. The two are not rivals — the loop still runs; the planner writes its agenda.
- [ ] Accept the lineage: Day 11's `run_tool_answer` was the one-step case of Day 12's `run_agent`, a config and not a different program. Day 16's plan-then-execute is a *configuration* of the same loop — a **prologue** (the plan) that fixes the sequence the loop then walks. A one-step plan *is* the Day 12 loop; that identity is the no-regression line, exactly as `max_steps=1` was Day 11's.
- [ ] Learn the three things planning buys, each of which becomes a fixture: **coverage** (a multi-part question gets *all* its parts asked, not whichever the model thought of first), **order** (a dependency like *payment depends on order state* is respected by construction, not discovered by failing), and **budget** (cost is estimated *before* the run, so an infeasible investigation is replanned or refused, not half-executed into a spent ledger).
- [ ] Learn the cost and the hazard: planning adds one chat (the plan) and one new failure mode — **over-planning**, spending an eight-step agenda on a question one query answers. Name both now; §8's control fixture (P9) is what holds the line.
- [ ] Draw the honesty line: a plan is a **claim about the future** ("these steps will answer this"), so the trace must grade plan *adherence* — did execution follow the plan, and where it did not, *why* — not merely the final answer.

### 2. Learn: Decomposition — a Plan Is a DAG of Tool Calls (45 min)

- [ ] Learn decomposition as the reverse of Day 10's map: Day 10 recorded the codebase's **dependency graph** (`module A → module B`); today the agent *writes* a dependency graph of its own, but over **tool calls** (`step 4 inspect payment` depends on `step 3 inspect order state`; `step 6 hypothesis` depends on steps 1–5). The plan is a **DAG**, and the curriculum's eight-step list is one **linearization** of it.
- [ ] Learn what a step must carry: an `id`; the **tool** it calls (a *registered* tool — Day 11's registry is the plan's alphabet); **arguments** (schema-valid, validated like any tool call); its **dependencies** (the DAG edges); and its **expected evidence** (what observation would make it *done*). A step that names a nonexistent tool or a malformed arg is a **plan-validation** failure — the planner's analogue of Day 11's argument check.
- [ ] Confront the decomposition failure modes: **missing sub-question** (the plan never inspects the payment, so the hypothesis is unfounded), **wrong order** (it inspects the payment before the order state it depends on), **non-atomic step** (one step doing three things, so a failure hides which part broke), and **over-decomposition** (eight steps for a question one query answers).
- [ ] Pre-commit the grounding rule for plans: a plan is only *grounded* if every step names a tool that **exists** and an argument the tool's **schema accepts** — an ungrounded plan is rejected **before** execution, the plan-level version of Day 14's "grounded on the DB schema, not the GraphQL schema".
- [ ] Name the static-vs-dynamic seam: Day 16 **generates** the plan from the question (a dynamic plan the model writes, validated by code), not a hand-written template per question class — but a *template* is exactly what a lower rung of the Day 13 fallback ladder can supply when planning itself fails.

### 3. Design: The Planner + Executor Workflow (30 min)

- [ ] Design the two-part workflow the curriculum names — **Planner + executor** — stating the split in one line: the **planner** produces and validates an explicit plan; the **executor** runs it, tracks execution state, writes checkpoints, and decides repair/retry/fallback/**replan** at each failure.
  ```text
  question → [plan]     write steps + deps        (model, one chat)
           → [validate] real tools · valid args · acyclic · deps ok   (code, fail-closed)
           → [estimate] budget check            (replan/refuse if infeasible)
           → [execute]  topological order: step → tool → observation → checkpoint
           → [adapt]    repairable → retry/replan · policy → wall (route around/stop)
           → [answer]   conclusion ⊆ evidence
  ```
- [ ] Decide **where the plan lives**: it is *data* — a `Plan` of `Step`s — not a new program. The executor is the Day 12 loop walked against a plan instead of against "what next?"; the registry and the tools are untouched. **The plan is the loop's agenda, not its replacement.**
- [ ] Design decision #1 — **the plan must be visible** (Day 14/15's reflex): it is an artifact in the trace, so a human can audit it, the trace can grade adherence, and a *bad plan* is a fixable object. A hidden plan cannot be graded or repaired — the same argument that kept Day 14's query on the table.
- [ ] Design decision #2 — **planning is optional, and its absence must be free.** A single-step plan must cost what Day 12 cost; the planner is entered only when the question needs it (or always, and it degrades to one step). The control fixture (P9) enforces this.
- [ ] Design decision #3 — **Day 15's rejection classes govern the plan.** A `repairable` failure is the planner/executor's business (repair, retry, or replan the remaining steps); a `policy` rejection is a **wall** — the plan is rewritten to route *around* it (or the run stops and surfaces it), never retried and never worked around. A plan that "fixes" its way past a denial is Day 15's attack loop wearing a planner.
- [ ] Pre-commit the three invariants: **(a)** no step runs before its dependencies are satisfied and the plan validates; **(b)** the final answer is **`conclusion ⊆ evidence`** (nothing asserted that the plan did not observe); **(c)** a one-step question is **not over-planned** (cost ≈ Day 12).

### 4. Build: The Planner — Question → a Validated Plan (1 hour)

- [ ] Build the **planner**: one chat (Day 4 gateway) that turns the question + the **tool registry's descriptions** (Day 11's descriptions, the chooser's metadata) into a plan. The model proposes; code disposes — the plan is *validated* before anything runs.
- [ ] Validate fail-closed, Day 11's way: every step names a **registered tool**; every step's **arguments** pass the tool's schema; the **dependency graph is acyclic**; every dependency id exists. An invalid plan is rejected (with *one* informative retry, the plan-level analogue of Day 12's self-correction) — never executed speculatively.
- [ ] Estimate cost **before** execution: `steps × expected chats` against the run budget (Day 12's `max_steps` generalized to a *plan* budget). An infeasible plan is **replanned** (a cheaper decomposition) or the run reports the question infeasible — it is never half-executed into a spent ledger.
- [ ] Use the **registry as the plan's alphabet**: the planner may only name tools the agent has (`get_customer`, `get_order`, the code/doc search, and the hardened `run_saleor_sql`) — which keeps the curriculum's eight steps *buildable* rather than aspirational, and makes an unbuildable plan a **grounding** failure (Day 14's lesson) rather than a model failure.
- [ ] Keep the honest `PENDING`: whether a *real* model writes a *good* plan for the curriculum's task — the right decomposition, the right tools, the right order — is a **semantic** cell (Day 21's benchmark on Day 16's planner). The stub supplies a stored plan and the harness grades everything downstream; a real key proves the *planning* half.

### 5. Build: The Executor — Execution State, Checkpoints, Resumption (1 hour)

- [ ] Build **execution state** as first-class: per step a **status** (`pending → running → done | failed | skipped`), the **observation** it produced (evidence), and the plan's running **hypothesis** (what the evidence so far suggests). This is the *task-shaped* working memory a multi-step investigation needs — and Day 16 keeps it **ephemeral** (in-run), naming Day 17 as the owner of anything that must outlive the process.
- [ ] Write a **checkpoint** after each step: the minimal state to **resume** (which steps completed, their evidence) or **replay** (deterministic re-execution from the start). Checkpoints are what make a plan *resumable* — the thing Day 12's loop, with no notion of "where am I", could not do — and what turns a mid-plan interruption into a recoverable event instead of a lost run.
- [ ] Distinguish the two stops people conflate: Day 12/13 answered "**when may the loop stop?**" (natural exit, cap, guard); Day 16 answers "**where is the plan, and can it continue?**" A checkpoint is not a termination, it is a *resumption point*. The trace carries both.
- [ ] Make the checkpoint the **re-plan gate**: after each step, compare the observation to the step's *expected evidence*; if they diverge (step 2 finds no checkout at all), the checkpoint is where the executor decides to **replan** the remaining steps instead of marching through a plan the evidence has already invalidated.
- [ ] Keep the seam honest: **execution state ≠ memory**. Day 16's state lives for the run; Day 17 introduces *working/conversation/task* state and, only if useful, *persistent* (episodic/semantic) memory. Say which is which; do not build Day 17's durable system today.

### 6. Build: Retry Strategy — Repair, Retry, Fallback, Replan (1 hour)

- [ ] Learn the four rungs, cheapest first, and keep them distinct — the day's retry-strategy grammar:
  | Rung | What changes | Cost | Owner |
  |---|---|---|---|
  | **repair** | the same step, **fixed arguments** | ~0 (re-issue) | Day 12/13 self-correction |
  | **retry** | the same step, same args, again | 1 tool call | Day 13's bounded retries |
  | **fallback** | a *different step/tool* for the same intent | 1 step | Day 13's fallback ladder |
  | **replan** | the *remaining plan* is rewritten | 1 plan chat | **Day 16 (new)** |
- [ ] Apply Day 15's classes to the rungs: a `repairable` rejection may climb the ladder (repair → retry → fallback → replan); a `policy` rejection may **not** — it is a wall, and the plan either routes *around* it structurally or the run stops. This is the concrete handoff Day 15 named for Day 16 ("a policy rejection is a hard wall the plan must route around").
- [ ] Bound every rung (Day 12/13's reflex): per-step retries capped, total replans capped (`max_replans`), the plan budget re-checked at every rung. A planner that replans forever is Day 13's oscillation at plan scale, and the same `no_progress` fingerprint catches it.
- [ ] Distinguish **replan-on-contradiction** (the evidence invalidated a step — a *good* replan) from **replan-as-thrash** (the planner keeps rewriting without new evidence — the oscillation the bound exists to stop). The trace records which.
- [ ] State the honest limit: replan is the one rung that regenerates a *model* artifact mid-run, so it is the plan's most expensive and least deterministic move; the stub proves the *routing* (when replan fires, and that a policy wall never triggers it), not the *quality* of a model's replan — Day 21.

### 7. Build: The Policy Wall and Verification in a Plan (45 min)

- [ ] Wire Day 15's **policy wall** into the executor: a `policy` rejection from any step **stops that line of the plan** — the executor either rewrites the remaining plan to avoid the forbidden query (a structural route-around) or halts and surfaces the violation. It is never fed to the retry ladder, and the run never substitutes a fabricated fact for the wall (Day 13's cardinal rule, now plan-deep).
- [ ] Wire **verification** as the plan's honesty gate: steps 6→7 (**form hypothesis → verify evidence**) are enforced, not decorative — the executor may not advance from *hypothesis* to *respond* while the hypothesis lacks supporting observations. This is where `conclusion ⊆ evidence` is checked.
- [ ] Enforce the invariant in the trace: parse the final answer's entities/claims and assert membership in the plan's collected **evidence** (or a stated aggregate of it) — the plan-level sibling of Day 7's `cited ⊆ retrieved` and Day 14's `asserted ⊆ returned`. A confident conclusion the plan never observed is the day's cardinal failure.
- [ ] Handle the honest dead ends explicitly: **plan exhausted, evidence insufficient** → the answer says "could not determine why; here is what was checked" (never an invented cause); **a step legitimately found `no_data`** (Day 13) → a finding, recorded, not a failure. A plan that cannot reach certainty must *say so*.
- [ ] Keep the plan **inspectable end-to-end**: checkpoint log + step statuses + evidence + hypotheses are all in the trace, so the *investigation* — not just its conclusion — is auditable. Day 14 kept the SQL visible for the same reason; Day 16 keeps the *plan* visible.

### 8. Test: The Plan Set (P1…P9) (1 hour)

- [ ] Pre-register the plan fixtures (**P1…P9** — *plan* fixtures: the workflow's properties plus a control) with expectations written **before** any run:
  - **P1** plan generation (curriculum task): "Investigate why customer X could not complete checkout" → a valid, acyclic plan of real tools covering the eight steps; the dependency *payment ← order state* present.
  - **P2** decomposition completeness: every required sub-question (customer, checkout, order, payment, docs) is a step; no required dependency missing.
  - **P3** execution state: statuses progress `pending→running→done`; each step's observation is recorded; a failed step is *recorded as failed*, not silently dropped.
  - **P4** checkpoints/resume: a run interrupted after step *k* **resumes at k+1** from the checkpoint (completed steps are not re-run), and a replay from step 0 reproduces the same evidence.
  - **P5** replan-on-contradiction: step 2 finds *no checkout* → the executor **replans** the remaining steps (investigate the cart/order instead) rather than answering from the miss.
  - **P6** retry ladder (repairable): a step's tool returns a repairable error (`unknown_column`) → bounded repair/retry succeeds; the plan continues; the *rung used* is recorded.
  - **P7** policy wall: a plan step attempts a **policy-rejected** query (Day 15) → the executor routes *around* it or halts and surfaces it; the step is **not** retried and the run fabricates nothing.
  - **P8** verification: a hypothesis unsupported by the plan's evidence → the run refuses to conclude (or keeps collecting) rather than answering; `conclusion ⊆ evidence` holds.
  - **P9** **control**: a one-step question ("Fetch order 1") → **not over-planned**: one step, cost ≈ Day 12, no plan chat wasted.
- [ ] Split the day honestly, Day 7's way: **P2–P4 and P6–P9 are `MECH`** — pure functions of the plan validator, the executor's state machine, the checkpoint store, and the retry router over *stored* plans/observations: deterministic, offline, free. **P1's and P5's *real-model* planning** (does a real model decompose the curriculum's task well, and replan sensibly) and **P8's grounding under a real model** are `PENDING` → Day 21. The stub proves the **workflow**; a real key proves the **planner**.
- [ ] Re-run Days 7–15's fixtures as the no-regression gate: the planner is **additive** — an agenda above the loop — so the doc nine, the code fixtures, T1…T7, A1…A8, F1…F9, Q1…Q8, and X1…X11 must stay exactly green. A planner that changes a single-step answer is a **P9** failure arriving through the front door; a planner that lets a policy rejection trigger a retry is a **P7**/Day 15 regression, which is worse.
- [ ] Apply Day 7's isolation, one owner per symptom: bad decomposition/order → **the planner** owns it; a dependency violated at runtime → **the executor** owns it; a wrong conclusion from right evidence → **the answer contract** (Day 13/14) owns it; a policy rejection retried → **the retry router** owns it (it ignored `rejection_class`); an over-planned trivial question → **the planner's entry rule** owns it (P9). One owner per symptom, in the trace.

### 9. Measure + Documentation (45 min + 20 min)

- [ ] Rule-4 as a plan table, one row per fixture, every cell a real run's: `task | steps | deps respected | steps executed | rungs used (repair/retry/fallback/replan) | policy walls | conclusion ⊆ evidence | over-planned? | cost | wall_ms` — the **`conclusion ⊆ evidence`** column is the day's headline and must read `ok` on every answered row, and **`over-planned?`** must read `no` on P9 (the counterweight — Day 15's `false-reject?` in new clothes).
- [ ] Record the cost shape honestly — planning is **not free**: a planned answer costs **1 plan chat + N step chats** versus Day 12's N, and the *bet* is that the plan **reduces N** by asking the right questions in the right order and avoiding a wandering loop. Put the plan cost beside Day 12's unplanned cost, so the trade-off is a number, not a promise.
- [ ] Keep the negatives column with owners: whether a real model writes a *good* plan (decomposition, order, sensible replans) is **Day 21**; durable state across a process/session is **Day 17**; parallel/sub-agent execution of independent DAG branches (steps 1–3, 5) is **named, not built**; and a plan that needs *persistent* memory to be useful is **Day 17/18**.
- [ ] Write `docs/concepts/planning.md` — reaction vs reflection, decomposition as a **tool-call DAG**, the planner+executor workflow (plan · validate · estimate · execute · adapt · answer), execution state and checkpoints (ephemeral; Day 17's persistent seam), the four-rung retry ladder under Day 15's rejection classes, the **policy wall** in a plan, and `conclusion ⊆ evidence`.
- [ ] Write `docs/evaluations/planning-day16.md` — the §8 plan table with the `conclusion ⊆ evidence = ok` and `over-planned? = no` invariants, the P1…P9 pre-registered fixtures and the P9 control, the plan-budget / rung / `max_replans` configs and defaults, the no-regression lines (Days 7–15 fixtures and X1…X11 through a planned executor), and the real-model-planning `PENDING`.
- [ ] Fill the Day 16 Daily Learning Record (Problem / Concept / Understanding / … / Tomorrow).
- [ ] Record token economics: the plan chat (once), the per-step chats, the replan chats (bounded), and the *saving* when the plan is good (fewer wasted steps than an unguided loop) — both numbers, so planning's bill is honest.

## Deliverable

The Planner + executor workflow — a question planned before it is acted on, executed with state and checkpoints, and answered only from collected evidence:

```text
Question ("Investigate why customer X could not complete checkout")
   ↓  Plan        model writes steps + deps (one chat)                 (visible artifact)
   ↓  Validate    registered tools · schema-valid args · acyclic       (fail-closed, Day 11)
   ↓  Estimate    steps × chats ≤ budget, else replan/refuse           (Day 12's cap, plan-wide)
   ↓  Execute     topological order:  step → tool → observation
   │                 ↳ checkpoint after each step                      (resumable)
   │                 ↳ expected-evidence check → replan if diverged    (the re-plan gate)
   ↓  Adapt       repairable → repair · retry · fallback · replan      (Day 13 ladder, bounded)
   │              policy    → WALL: route around · or stop + surface   (Day 15 classes)
   ↓  Answer      conclusion ⊆ evidence  (hypothesis verified, not asserted)   ← the invariant
   ▼
  "Customer X's payment was declined … (verified against order 412 + payment record + docs)"
   — and if the plan cannot establish it: "could not determine why; here is what was checked."
```

Days 6–15 answered *"what does the text say"*, *"where is it implemented"*, and *"how much / which rows"*. Day 16 answers a **multi-step investigation** — a plan decomposes the question, the executor walks it with state and checkpoints, failures climb a bounded retry ladder, a policy rejection is a wall and not a retry, and the conclusion is asserted only from evidence the plan collected. What a long plan needs to *remember across runs* — durable state and memory — is Day 17's; whether a real model writes the right plan is Day 21's measure.

## Exit Check — answer in your own words

- What does a plan add over Day 12's step-by-step loop, and why is a one-step plan the loop rather than a different program?
- Why is a plan a DAG of tool calls rather than a numbered list, and which earlier day's artifact is that shape borrowed from?
- What are checkpoints *for*, and how does "where is the plan?" differ from Day 12/13's "when may the loop stop?"?
- Name the four rungs of the retry ladder, and say which kind of failure must climb none of them — and why.
- What does `conclusion ⊆ evidence` forbid, and how does it relate to `cited ⊆ retrieved` and `asserted ⊆ returned`?
