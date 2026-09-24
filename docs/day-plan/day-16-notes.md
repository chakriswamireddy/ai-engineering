# Day 16 Notes — Agent Planning

> Notes answering `docs/day-plan/day-16.md`.
> Fifteen days built a machine that *reads* (Days 6–10), *acts* (Day 11), *drives itself* (Day 12), *survives failure* (Day 13), *computes* (Day 14), and *defends itself* (Day 15). Every one of those is **reactive**: the loop chooses its next step only *after* reading the last observation, holding no model of where it is going — Day 12's loop is a reflex, and Day 13 hung guards on a reflex. Day 16 adds the faculty the whole series has been missing, **planning**: the agent writes an explicit agenda *before* it acts, then executes it. The curriculum's task is the archetype — *"Investigate why customer X could not complete checkout"*, which decomposes into eight steps (find customer → find checkout → inspect order state → inspect payment → search docs → hypothesize → verify → respond) — and the deliverable is a **Planner + executor workflow**: a planner that turns a question into a **validated plan** (a DAG of tool calls), and an executor that walks it tracking **execution state**, writing **checkpoints**, and repairing, retrying, falling back, or **replanning** when reality disagrees with the plan. The honesty invariant is `cited ⊆ retrieved`'s third translation: **`conclusion ⊆ evidence`** — the final answer draws only on observations the plan actually collected.
> The single idea: **the plan is the loop's agenda, not its replacement.** No tool, no guard, and no loop step changes; the planner is a *prologue* that fixes the sequence, the executor is Day 12's loop walked against that sequence, Day 13's retry ladder gains one rung (**replan**), and Day 15's rejection classes decide which rung a failure may climb — a *repairable* failure climbs the ladder, a *policy* wall is routed around or stopped on. Planning is not free, so the day carries its own counterweight: a one-step question must not be over-planned.

---

## 1. Reaction vs Reflection — What Planning Adds

Day 12's loop asked one question per step — *"given everything observed, what next?"* — and Day 13 made that answer robust. It is a **reflex**: locally correct, globally aimless. Nothing in a reflex loop holds the intent ("find out why the checkout failed") in view, so the loop can search the docs, find nothing, search again, and never inspect the payment record — because nothing told it the payment *matters*.

Planning adds the missing faculty: a **model of the whole task, formed before acting**.

| | Reactive loop (Day 12) | Reflective planner (Day 16) |
|---|---|---|
| When it decides | after each observation | **before** any tool runs |
| What it holds | the last observation + transcript | an explicit **agenda** of steps + deps |
| Coverage | whichever the model thought of | **every required sub-question** |
| Order | whatever emerges | **dependencies respected** by construction |
| Cost | discovered as it is spent | **estimated before** the run |
| Failure | react per step | **repair / retry / fallback / replan** |

Three concrete things planning buys, each of which becomes a fixture later:

1. **Coverage.** A multi-part question ("why couldn't X check out?") needs *all* of customer, checkout, order, and payment inspected; a reflex can stop after the first plausible find. The plan enumerates the parts (P2).
2. **Order.** *Inspect payment* depends on *inspect order state* — you cannot read a payment record you have not located. The plan encodes the dependency (P1/P2); the reflex discovers the ordering only by failing.
3. **Budget.** Planning puts a cost estimate *before* the spend: `steps × expected chats` against the run budget (Day 12's `max_steps`, generalized). An investigation that cannot fit is **replanned cheaper or refused** — not half-executed into a spent ledger.

### Planning is a configuration, not a new program

The lineage is the series' own. Day 11's `run_tool_answer` was the one-step case of Day 12's `run_agent` — a config, not a different program. Day 16 inherits the same structure: a **one-step plan** *is* the Day 12 loop; `run_agent` still exists and still runs the steps. What the day adds is a **prologue** (the plan) that fixes the sequence the loop then walks, and an **executor** that tracks where in the sequence it is. This is why the day can claim to stay small — the plan changes the *order* of loop steps, never their *nature*.

The honest cost sits on the same ledger: planning buys order and coverage with **one extra chat** (the plan) and one new risk, **over-planning** — spending an eight-step agenda on a question one query answers. Day 13's safety day learned to measure its own counterweight (a false-reject rate); Day 16 does the same, and P9 is that measurement.

---

## 2. Decomposition — a Plan Is a DAG, Not a List

The curriculum hands the day a linear eight-step list, and the first temptation is to treat the list as the plan. It is not: it is one **linearization** of a dependency graph. Day 10 recorded the *codebase's* dependency map (`module → module`); Day 16 writes the *agent's* — but over **tool calls**:

```text
1 find customer ─┐
2 find checkout ─┼─→ 6 form hypothesis ─→ 7 verify evidence ─→ 8 respond
3 order state ───┤         ▲
4 payment ───────┘         │
5 search docs ─────────────┘
```

The arrows are *data* dependencies: step 4 needs the order located by step 3; steps 1–5 all feed the hypothesis; verification (7) gates the response (8). A DAG buys two things a list cannot — **order freedom** (steps 1, 2, 3, 5 are independent and could run in any order, or in parallel, a seam named for later) and **dependency safety** (the executor may not run 4 before 3, so an impossible ordering cannot even be expressed).

### What a step carries

A step is not a sentence; it is a **tool call plus metadata**, and each field is a place a plan can be wrong:

| Field | Why it exists | The failure it prevents |
|---|---|---|
| `id` | referenced by dependencies | an ambiguous plan |
| `tool` | must be **registered** (Day 11) | a plan that can't be built (grounding) |
| `args` | schema-valid, like any tool call | a malformed call at runtime |
| `depends_on` | the DAG edges | out-of-order execution |
| `expected` | what observation would make it *done* | a step that "succeeds" without evidence |

The last two are the subtle ones. `depends_on` is what makes the *payment-after-order* ordering structural rather than lucky; `expected` is what lets the executor **compare reality to the plan** at each checkpoint (§4) and decide to replan.

### Decomposition failure modes

Naming the ways decomposition breaks is half the design work, because each becomes a fixture or a rule:

- **missing sub-question** — the plan never inspects the payment, so the hypothesis is unfounded (P2). The *coverage* failure.
- **wrong order** — the plan inspects the payment before the order state it depends on (P1/P2). The DAG makes this *unrepresentable* rather than merely wrong.
- **non-atomic step** — one step "checks the order and payment and docs", so a failure hides which part broke; the retry ladder cannot repair what it cannot isolate (§5).
- **over-decomposition** — eight steps for a question one query answers (P9). The *comfortable* failure: it looks thorough.

### Grounding a plan

Day 14 grounded generation on the DB schema and treated a query against the GraphQL schema as a **grounding** bug. Day 16 grounds the *plan* the same way: a step is valid only if it names a **registered tool** with **schema-valid arguments**. An unbuildable step is rejected **before execution** — cheap checks first, exactly Day 14's ordering.

The planner generates **dynamically** (the model writes a plan for *this* question, validated by code), not from a hand-written template per question class. But a template is a legitimate *lower rung* of the fallback ladder: when planning itself fails or is unavailable, a canned per-class plan is a safe degradation (§5).

---

## 3. The Planner + Executor Workflow — and Where It Lives

The curriculum names two components; the day's design work is placing them without inventing a second agent.

```text
question → [plan]     model writes steps + deps        (one chat, Day 4 gateway)
         → [validate] registered tools · schema args · acyclic · deps exist  (code, fail-closed)
         → [estimate] steps × chats ≤ budget → else replan / refuse          (Day 12's cap, plan-wide)
         → [execute]  topological order, one loop step per plan step         (Day 12's loop)
         │            ↳ observation appended, checkpoint written             (§4)
         │            ↳ expected-evidence check → replan if diverged         (§4)
         → [adapt]    repairable → repair·retry·fallback·replan (bounded)    (Day 13 ladder, §5)
         │            policy    → WALL: route around, or stop + surface       (Day 15 classes, §7)
         → [answer]   conclusion ⊆ evidence                                   (§6)
```

Every stage maps onto machinery that already exists:

- **plan** is one chat through Day 4's gateway (the planner's only new call);
- **validate** is Day 11's registry + arg validation, applied to a plan instead of a single call;
- **estimate** is Day 12's budget arithmetic, lifted from `max_steps` to a plan;
- **execute** is Day 12's loop — the plan fixes *which* step is next, the loop still runs it;
- **adapt** is Day 13's retry/fallback ladder, plus one new rung (**replan**);
- **answer** is Day 12's final turn, gated by `conclusion ⊆ evidence`.

### Why the plan must stay visible

The same argument that kept Day 14's SQL on the table: a **hidden plan** cannot be audited, graded, or repaired. If the plan is an artifact in the trace, a human can see the investigation's shape, the trace can measure **plan adherence** (did execution follow the plan, and where it did not, *why*), and a bad plan becomes a fixable object rather than a mysterious outcome. The plan is the day's central object the way the query was Day 14's.

### Two layers: the executor is a state machine, the loop is still underneath

The **executor** knows *which* step is current, what has completed, and whether the remaining plan is still valid; the **loop** runs each step as it always did (tool call → observation → next). This is the series' recurring "old seams made declarative" move (Days 11–14): the plan adds no new way to call a tool, only a *schedule* for the existing way.

---

## 4. Execution State and Checkpoints — the Plan's Working Memory

A multi-step plan must remember *where it is* — the thing Day 12's transcript-only loop never tracked.

### Execution state

Per step, a **status** (`pending → running → done | failed | skipped`), the **observation** it produced, and the plan's running **hypothesis** (what the accumulated evidence suggests). This is *task-shaped* working memory: not the conversation (Day 12's transcript), not the plan (the agenda), but the **execution** — which rungs fired, which steps are done, what is known so far.

### Checkpoints

A **checkpoint** written after each step is the minimal state needed to either **resume** (continue at step *k+1* without re-running 1…*k*) or **replay** (deterministically re-execute from the start and reach the same evidence). Checkpoints give the plan two properties Day 12's loop could not have:

- **resumability** — a mid-plan interruption (a crash, a budget pause, a human pause) is recoverable rather than a lost run (P4);
- **auditability** — the checkpoint log *is* the investigation's provenance, step by step.

### "Where is the plan?" is not "may the loop stop?"

These are the two questions people conflate, and the day separates them cleanly:

| Question | Day | Answer |
|---|---|---|
| *when may the loop stop?* | 12/13 | natural exit · `max_steps` · a guard (repeat / oscillation / timeout) |
| *where is the plan, and can it continue?* | 16 | the checkpoint: step *k* done, evidence held, plan still valid? |

A checkpoint is not a termination; it is a **resumption point**. The trace carries both the termination reason (Day 12/13) and the checkpoint index (Day 16).

### The re-plan gate

The checkpoint is also where the plan meets reality. After each step, the executor compares the observation to the step's `expected` evidence. When they diverge — step 2 ("find checkout") finds *no checkout at all* — it **replans the remaining steps** (investigate the cart or the order instead) rather than marching through a plan the evidence has already invalidated (P5). This is planning's answer to Day 13's fallback: not "try the step differently," but "the *shape* of the task has changed."

### Execution state is not memory

The seam, stated precisely because Day 17 is next: Day 16's execution state is **ephemeral — it lives for the run**. What must outlive the process — *working* state across turns, *conversation* state across sessions, *task* state across runs, or a *persistent* memory (episodic/semantic) — is **Day 17's** (and Day 18's). Day 16 builds the *checkpoint* (the format that *could* persist) and deliberately does not build the *store*. A plan that would be better with memory is a Day 17 argument, named and not built.

---

## 5. Retry Strategy — Repair, Retry, Fallback, Replan

The curriculum's "retry strategy" is where Day 16 cashes in Day 13's ladder and Day 15's classes. Four rungs, cheapest first — and the distinctions matter because they cost differently and route differently:

| Rung | What changes | Cost | Owner | Trigger |
|---|---|---|---|---|
| **repair** | same step, **fixed args** | ~0 (re-issue) | Day 12/13 self-correction | a `repairable` error with a clear fix |
| **retry** | same step, same args | 1 tool call | Day 13 bounded retries | a transient error (`timeout`, flaky) |
| **fallback** | **different step/tool**, same intent | 1 step | Day 13 fallback ladder | the tool can't serve the intent |
| **replan** | rewrite the **remaining plan** | 1 plan chat | **Day 16 (new)** | the evidence invalidated the plan's shape |

The ladder is bounded at every rung (Day 12/13's reflex): per-step retries capped, total **replans** capped (`max_replans`), the plan budget re-checked at each rung. A planner that replans without new evidence is **Day 13's oscillation at plan scale**, and the same `no_progress` fingerprint catches it — the *contradiction-driven* replan (good) and the *thrash* replan (bad) are distinguished by whether the fingerprint moved.

### Day 15's classes decide which rungs a failure may climb

This is the day's sharpest integration, and the handoff Day 15 explicitly named:

```text
failure ──→ rejection_class?
              ├─ repairable → climb the ladder: repair → retry → fallback → replan   (bounded)
              └─ policy     → NO rung. It is a WALL.
                              executor routes *around* it (rewrite the plan),
                              or stops and surfaces it; never retries,
                              never works around, never fabricates.        (Day 15 §6)
```

A plan that "fixes" its way past a policy denial is Day 15's **attack loop wearing a planner** — a self-correcting/replanning system that helps the model probe for a bypass has handed the adversary a fuzzer at a higher level. So the *same* `rejection_class` that Day 15 split for the query layer governs the *plan* layer, one abstraction up. This is the whole reason Day 15 bothered to separate repairable from policy: the distinction has to survive the climb from a single query to a plan.

### The honest limit

Replan is the only rung that regenerates a **model** artifact mid-run, so it is the plan's most expensive and least deterministic move. The stub proves the **routing** — when replan fires, that a policy wall never triggers it, that the bound holds — not the **quality** of a model's replan. That quality is a *semantic* cell (Day 21), stated as `PENDING` and never implied by a green table.

---

## 6. Verification — `conclusion ⊆ evidence`

The eighth step ("respond") is where a plan's investigation becomes a claim, and the day's invariant is that the claim may draw only on the **evidence the plan collected**. It is the third translation of the same rule:

| Day | Invariant | Evidence type |
|---|---|---|
| 6 | `cited ⊆ retrieved` | prose chunks |
| 14 | `asserted ⊆ returned` | result rows |
| **16** | **`conclusion ⊆ evidence`** | **the plan's observations** |

The mechanism is the **6→7 gate**: the executor enforces that a **hypothesis (step 6)** may not advance to **respond (step 8)** without **verification (step 7)** — i.e. without supporting observations. The DAG makes this enforceable structurally: verification is a step depending on hypothesis, and respond depends on verification. Enforcement in the trace is mechanical: parse the final answer's entities/claims and assert membership in the plan's collected evidence (or a stated aggregate of it) — a set-membership test over stored observations, not a judgment call.

Two honest outcomes the gate must allow:

- **plan exhausted, evidence insufficient** → the answer says *"could not determine why; here is what was checked"* — a **finding**, not a failure, and never an invented cause (P8);
- **a step legitimately found `no_data`** (Day 13) → recorded as evidence; the honest absence of a record is itself evidence ("no payment record exists for this order").

The failure this guards is the plan-shaped version of every prior day's cardinal sin: a fluent, confident causal story ("the payment was declined") that no step in the plan actually observed. A plan makes that sin *easier* to commit — the narrative of a multi-step investigation is persuasive — which is exactly why the gate is structural rather than a reviewer's judgment.

---

## 7. Test: The Plan Set (P1…P9)

Expected behavior written **before** the run, like every day since Day 7. One fixture per property, plus a control:

| ID | What it proves | Input / injection | Expected |
|---|---|---|---|
| P1 | plan generation (curriculum) | "Investigate why customer X could not complete checkout" | valid, acyclic plan of **registered** tools covering the eight steps; `payment ← order state` dependency present |
| P2 | decomposition completeness | same task | every required sub-question is a step (customer, checkout, order, payment, docs); no required dependency missing |
| P3 | execution state | any multi-step run | statuses progress `pending→running→done`; each observation recorded; a failed step is recorded **as failed**, not dropped |
| P4 | checkpoints / resume | interrupt after step *k* | resume at *k+1* (completed steps not re-run); replay from 0 reproduces the same evidence |
| P5 | replan-on-contradiction | step 2 finds **no checkout** | executor **replans** the remaining steps; answers nothing from the miss |
| P6 | retry ladder (repairable) | a step's tool returns `unknown_column` | bounded repair/retry succeeds; plan continues; the **rung used** is recorded |
| P7 | policy wall | a plan step attempts a **policy-rejected** query (Day 15) | executor routes **around** it or halts + surfaces; the step is **not** retried; **nothing fabricated** |
| P8 | verification | hypothesis unsupported by collected evidence | run refuses to conclude (or keeps collecting); `conclusion ⊆ evidence` holds |
| P9 | **control** | a one-step question ("Fetch order 1") | **not over-planned**: one step, cost ≈ Day 12, no wasted plan chat |

The `MECH`/`PENDING` split is exactly Day 7's. **MECH** — P2, P3, P4, P6, P7, P9 (plus P1's *validation*, P5's *routing*, and P8's *check*) — are pure functions of the plan validator, the executor's state machine, the checkpoint store, and the retry router over **stored** plans/observations: deterministic, offline, free. **PENDING** — P1's and P5's *real-model* planning (does a real model decompose well, and replan sensibly?) and P8's grounding under a *real* model — need the real gateway, and are **Day 21's** benchmark on Day 16's planner. The stub proves the **workflow**; a real key proves the **planner**.

Three obligations, unchanged in spirit:

1. **No regression on Days 7–15.** The planner is **additive** — an agenda above the loop. The doc nine, the code fixtures, T1…T7, A1…A8, F1…F9, Q1…Q8, and X1…X11 run unchanged and must stay exactly green. A planner that changes a *single-step* answer is a **P9** failure arriving through the front door; a planner that lets a policy rejection trigger a retry is a **P7**/Day 15 regression, which is worse.
2. **The day's own counterweight.** Like Day 15's `false-reject?`, the **`over-planned?`** column must read `no` on P9 — planning is not free, and a layer that inflates a one-step question is a bug wearing thoroughness. Measured on **both** sides: investigations planned well *and* trivial questions left alone.
3. **Isolation, one owner per symptom.** Bad decomposition/order → **the planner**. A dependency violated at runtime → **the executor**. Right evidence, wrong conclusion → **the answer contract** (Day 13/14). A policy rejection retried → **the retry router** (it ignored `rejection_class`). An over-planned trivial question → **the planner's entry rule**. Say which, never vibes.

---

## 8. Measure: The Planner as a Contract

Rule-4 as a plan table, one row per fixture, every cell a real run's:

| Task | Steps | Deps respected | Executed | Rungs (repair/retry/fallback/replan) | Policy walls | conclusion ⊆ evidence | over-planned? | Cost | wall_ms |
|---|---|---|---|---|---|---|---|---|---|
| P1 curriculum investigation | 8 | y | 8 | — | — | **ok** | no | … | … |
| P2 decomposition | … | **y** | … | — | — | **ok** | no | … | … |
| P3 execution state | … | y | … | — | — | **ok** | no | … | … |
| P4 resume after step k | … | y | k→… | — | — | **ok** | no | … | … |
| P5 replan on contradiction | … | y | … | **replan** | — | **ok** | no | … | … |
| P6 repairable error | … | y | … | **repair/retry** | — | **ok** | no | … | … |
| P7 policy-rejected step | … | y | … | **none** | **1** | **ok** | no | … | … |
| P8 unsupported hypothesis | … | y | … | — | — | **ok** | no | … | … |
| P9 **control** (1-step Q) | **1** | — | 1 | — | — | **ok** | **no** | ≈Day 12 | … |

Two columns carry the day: **`conclusion ⊆ evidence`** must read `ok` on every answered row — the headline invariant, exactly as `fabricated?` did on Day 13 and `asserted ⊆ returned` did on Day 14 — and **`over-planned?`** must read `no` on P9 (the counterweight). The **Rungs** column is where the retry strategy becomes legible: which rung fired, and — for P7 — that **none** did, because the failure was a *policy wall*, not a mistake. The **Policy walls** column is the Day 15 integration, and a `replan` appearing in it would be a routing bug.

The cost shape, recorded honestly because planning **spends to save**: a planned answer costs **1 plan chat + N step chats**, versus Day 12's N (unguided) — so planning's *premium* is one chat, and its *bet* is that the plan **reduces N** by asking the right questions in the right order and avoiding a wandering loop. Both numbers belong on the page: planning is not free, and it must earn its chat back in fewer wasted steps. P9 is the floor of that trade — where the plan has nothing to save and must not be paid for.

The negatives column, with owners: whether a real model writes a **good** plan (right decomposition, right order, sensible replans) is **Day 21**; durable state and memory across a process/session are **Day 17/18**; parallel/sub-agent execution of independent DAG branches (steps 1–3, 5 could run concurrently) is **named, not built**; and a plan that needs *persistent* memory to be useful is **Day 17/18**. Empty negatives mean the day learned nothing; these have owners.

---

## 9. Documents

- `docs/concepts/planning.md` — reaction vs reflection, decomposition as a **tool-call DAG**, the planner+executor workflow (plan · validate · estimate · execute · adapt · answer), execution state and **checkpoints** (ephemeral; Day 17's persistent seam), the four-rung retry ladder under Day 15's rejection classes, the **policy wall** in a plan, and `conclusion ⊆ evidence`.
- `docs/evaluations/planning-day16.md` — the §8 plan table with the `conclusion ⊆ evidence = ok` and `over-planned? = no` invariants, the P1…P9 pre-registered fixtures and the P9 control, the plan-budget / rung / `max_replans` configs and defaults, the no-regression lines (Days 7–15 fixtures and X1…X11 through a planned executor), and the real-model-planning `PENDING`.
- PROGRESS rollup: Day 16 mechanics move to `DONE` on the stub (plan validation, execution state, checkpoints/resume, the retry router incl. the policy wall, the over-planning guard, and `conclusion ⊆ evidence` verified offline); the real-model planning cells stay `PARTIAL`/`PENDING` until a real gateway run confirms the model writes grounded, well-ordered plans.

---

## 10. Daily Learning Record

### Problem

Fifteen days produced an agent that is **competent per step and aimless overall**. Day 12's loop decides the next action only *after* reading the last observation, so it holds no model of the task: nothing guarantees it asks *every* sub-question the curriculum's task requires (customer, checkout, order, payment, docs), nothing enforces that it inspects the payment *after* it has located the order, and nothing estimates the investigation's cost before spending it. The problem is to give the agent the missing **reflective** faculty — decide *before* acting — for a concrete multi-step investigation ("Investigate why customer X could not complete checkout"), while keeping the change *small*: the plan must reuse the Day 11 tools, the Day 12 loop, and the Day 13/15 guards unchanged, add exactly one new model call (the plan), one new state (execution state + checkpoints), and one new rung (replan) — and must not become a way to spend eight steps on a question one query answers.

### Concept

**The plan is the loop's agenda, not its replacement.** Planning is a **prologue** that fixes the sequence of steps — a **DAG of tool calls**, since dependencies like *payment ← order state* are edges, not list positions — and the executor is Day 12's loop walked against that sequence while tracking **execution state** (`pending→running→done|failed|skipped` + observations + the running hypothesis) and writing **checkpoints** after each step. Checkpoints are what make a plan **resumable** and **auditable**, and what separate *"where is the plan?"* (Day 16) from Day 12/13's *"when may the loop stop?"*. Decomposition is Day 10's dependency map turned on the agent's own tool calls; a plan is *grounded* only if every step names a **registered** tool with **schema-valid** args (an unbuildable plan is rejected before execution — the plan-level analogue of Day 14's schema grounding). The **retry strategy** is Day 13's ladder with one new rung — **repair → retry → fallback → replan**, all bounded — governed by **Day 15's rejection classes**: a `repairable` failure climbs the ladder; a `policy` rejection is a **wall** the plan routes around or stops on, never a retry, because a self-correcting/replanning system that helps a model past a denial is Day 15's attack loop one level up. And the honesty invariant generalizes a third time: **`conclusion ⊆ evidence`**, enforced by the 6→7 **hypothesize → verify** gate, so the final answer draws only on observations the plan actually collected. The day's counterweight, stated as a fixture (P9): **planning is not free**, and over-planning a one-step question is a bug.

### Understanding

The unlock is that **Day 16 changes *when* the loop decides, not *what* it does.** Every step of a plan is still a Day 11 tool call executed by the Day 12 loop under the Day 13 guards; the novelty is an agenda in front and a state machine around — a prologue and a checkpoint, not a new engine. The second understanding is that **planning's benefits and its costs are the same coin**: a plan buys coverage, order, and a pre-spend budget, and pays one chat plus a new failure mode (over-planning) — which is why P9 exists and why a plan must earn its chat back in fewer wasted steps. The third is the **day's most important integration**: Day 15 split repairable from policy *precisely so this day's planner could consume it* — the distinction has to survive the climb from one query to a whole plan, or the planner becomes the adversary's fuzzer. And the honest one: the mechanical half (plan validation, execution state, checkpoints/resume, the retry router incl. the policy wall, the over-planning guard, `conclusion ⊆ evidence`) is green under the stub, while whether a *real* model decomposes well, orders correctly, and replans sensibly is `PENDING` until a real gateway — Day 21's benchmark on Day 16's planner.

### Implementation

- `ai_os/planner.py` (new) — `Plan`, `Step` (`id`, `tool`, `args`, `depends_on`, `expected`), `plan(question, registry)` (one Day 4 chat → a DAG), `validate_plan()` (registered tools · schema-valid args · acyclic · deps exist — fail-closed), `estimate_cost(plan)`, `replan(plan, observation, failed_step)`.
- `ai_os/executor.py` (new, or folded into `agent.py`) — the state machine: walk the plan in topological order, run each step through the Day 12 loop, record **execution state**, write a **checkpoint** per step, compare observation to `expected` (the re-plan gate), and consult `rejection_class` to choose a rung (repairable → repair/retry/fallback/replan, bounded by `max_replans`) or raise the **policy wall** (route around / stop + surface).
- `ai_os/agent.py` (extended, minimally) — `run_agent` gains an optional plan: with none, it is Day 12 unchanged (a **one-step plan** is the identity case, P9's guarantee); the loop itself is untouched.
- `ai_os/state.py` (new) — execution state + the checkpoint record (ephemeral, in-run); the **persistent** store is **Day 17's** seam, named not built.
- `ai_os/trace.py` (extended) — plan columns: `plan_id`, `step_id`, `step_status`, `depends_on`, `rung`, `replanned`, `policy_wall`, `checkpoint_idx`, `conclusion_subset_evidence`, `over_planned`, alongside Day 13/14/15's `failure_kind`/`truncated`/`fabricated`/`rejection_class`.
- `tools/plan_smoke.py` (new) — offline suite for P1–P9 (plan + control), plus the Days 7–15 no-regression run (incl. X1…X11 through a planned executor).
- `docs/concepts/planning.md`, `docs/evaluations/planning-day16.md`.

### Failure

1. **The confident causal story (the day's cardinal sin).** The plan collects partial evidence and the answer asserts a cause ("the payment was declined") that no step observed — `conclusion ⊆ evidence` fails. A multi-step investigation's narrative is *more* persuasive than a single citation, which is why the 6→7 gate is structural.
2. **The over-planned trivial question.** Eight steps for "Fetch order 1" — thorough-looking, wasteful, and a **P9** failure. Planning's comfortable failure, caught only by the control.
3. **The policy rejection climbed the ladder.** A `policy` wall is fed to repair/retry/replan, the planner rewrites around the denial, and the run probes for a bypass — Day 15's attack loop wearing a planner. The `rejection_class` router is the guard; a `replan` in the P7 row is the signature.
4. **The invalid plan executed speculatively.** A step names a tool that does not exist or an arg the schema rejects, and the executor "tries anyway" — a **grounding** failure that should have been rejected *before* any call (the plan-level analogue of Day 14's wrong-schema bug).
5. **The ignored checkpoint.** The plan marches through a sequence the evidence invalidated at step 2 (no checkout exists), wasting the remaining steps and answering from a stale agenda — the executor failed to replan at the gate.
6. **Execution state treated as memory.** The day quietly persists state "because a plan needs it," trespassing on Day 17 and blurring the ephemeral/durable line the series keeps clean.
7. **Non-atomic steps.** A step that does three things fails, and the retry ladder cannot repair what it cannot isolate — a decomposition bug that *looks* like a retry-strategy bug.

### Debugging

Everything Day 16 adds is `MECH` under the stub: plan validation is set membership plus a cycle check, the executor is a small state machine, checkpoints are a list of records, `rejection_class` routing is a lookup, the over-planning guard is a step count, and `conclusion ⊆ evidence` is set membership over stored observations. The debug loop is Day 7's, re-rooted onto the planning owners:

- **A bad plan (coverage/order)** → **the planner** owns it — read the plan artifact, not the model's prose.
- **A dependency violated at runtime** → **the executor** owns it (topological order ignored, or a stale plan after a replan).
- **Right evidence, wrong conclusion** → **the answer contract** (Day 13/14) owns it — `conclusion ⊆ evidence` localizes it.
- **A policy rejection retried/replanned-around** → **the retry router** owns it (`rejection_class` ignored) — the Day 15 integration is broken.
- **An over-planned trivial question** → **the planner's entry rule** owns it (P9).
- **A step that should have been two** → **decomposition** owns it (non-atomic step) — the "failed repair" was diagnosing the wrong thing.

The trace says which, never the vibes; the step is the unit of attribution, and each has an owner from the day it was designed.

### Evaluation

The §8 plan table in `docs/evaluations/planning-day16.md`: plan validity rate (registered tools, valid args, acyclic), decomposition completeness (required sub-questions covered), dependency order at runtime, execution-state correctness (statuses + evidence, failures recorded), checkpoint resume/replay fidelity, retry-rung correctness (which rung fired, and that **no rung fired on a policy wall**), the **`conclusion ⊆ evidence = ok`** headline on every answered row, and **`over-planned? = no`** on P9. The numbers a future observer can carry: whether any answer asserted a cause the plan never observed (never), whether a policy rejection ever climbed the ladder (never), whether a one-step question ever paid for a plan (never), and whether planning moved any of Days 7–15's numbers (it must not). The `PENDING` line, plainly: whether a real model writes a *good* plan — the right decomposition, the right order, sensible replans — is Day 21's judgment on Day 16's planner.

### Architecture

The claim of the day is the series' claim, one faculty further: **the system took a new capability — thinking before acting — and stayed small.** The planner is one chat and one validated data structure; the executor is the Day 12 loop plus a state machine; the retry ladder is Day 13's plus one rung; the policy wall is Day 15's `rejection_class` consumed one level up; and the honesty invariant is `cited ⊆ retrieved` translated a third time. Nothing in the registry, the tools, the loop, or the corpora changed — the plan is an *agenda*, and agenda ≠ engine. The seams are named and owned: durable state and memory are **Day 17/18**; whether a real model plans well is **Day 21**; parallel DAG branches are named, not built; and the day's counterweight — over-planning is a bug — is a fixture, not a footnote. **Day 16 added a prologue and a state machine; the loop Day 12 built, the guards Day 13 hung, and the privilege Day 15 relocated are what made "thinking first" a small change instead of a second agent.**

### Questions

- Is a generated plan the right rung above the loop, or does a *library of per-class templates* decompose the curriculum's tasks better and cheaper — and where is the line between "the model plans" and "the model fills a plan"?
- A plan is a DAG, but the day executes a topological **linearization**; when do genuinely independent branches (steps 1–3, 5) deserve **parallel** execution, and what breaks in the checkpoint/evidence model when they do?
- Replan is the one rung that regenerates a model artifact mid-run: should it *also* be gated by evidence (replan only on a contradiction, never on a whim), and can a `no_progress` fingerprint reliably tell a contradiction from a thrash?
- Checkpoints make a run resumable — but *resuming* requires the tools to be re-runnable (idempotent) and the state to be reconstructible; which Day 11 tools actually satisfy that, and does a read-only executor get resumability for free while a mutating one never can?
- If the plan estimates cost up front and reality diverges (a step explodes in tokens), does the executor *replan under the remaining budget*, *pause* for a human, or *stop and report* — and is that a planning decision or a Day 13 budget decision?

### Tomorrow

Day 17 — Memory and State: a plan's execution state has to be *remembered*, and today it is remembered only for the run. Day 17 starts with the three states the curriculum names — **working state, conversation state, task state** — and introduces *persistent* memory (episodic, semantic) only if it is genuinely useful, delivering the **agent state/checkpoint system** that Day 16's in-run checkpoints are the format for. The day rests on Day 16's distinction: execution state (what the plan did) is ephemeral; *working/conversation/task* state is what must survive a turn, a session, or a run. Day 13's guards, Day 14's `asserted ⊆ returned`, Day 15's executor, and Day 16's planner all run as-is; Day 17 adds the store, not the shape. Day 16 was where the agent learned to *think before it acts*; Day 17 is where it learns to *remember what it did*.
