# Agent Planning — a Plan Is the Loop's Agenda, Not Its Replacement

> Day 16 of the AI-layer apprenticeship. Days 6–15 built a machine that *reads*
> (6–10), *acts* (11), *drives itself* (12), *survives failure* (13), *computes*
> (14), and *defends itself* (15) — and every one of those is **reactive**: the loop
> chooses its next step only *after* reading the last observation. Day 16 adds the
> reflective counterpart the series was missing: **the agent writes an explicit
> agenda before it acts.** The curriculum's task is the archetype — *"Investigate
> why customer X could not complete checkout"* — and the deliverable is the
> **Planner + executor workflow**: a planner that turns a question into a
> **validated plan** (a DAG of tool calls), and an executor that walks it tracking
> **execution state**, writing **checkpoints**, and repairing, retrying, falling
> back, or **replanning** when reality disagrees with the plan.
> Implementation: `ai_os/planner.py`, `ai_os/executor.py`, `ai_os/state.py`
> (new), `ai_os/agent.py` (`run_agent(plan=…)`), `ai_os/trace.py` (plan columns),
> `tools/plan_smoke.py` (P1–P9).

The single idea: **the plan is the loop's agenda, not its replacement.** No tool,
no guard, and no loop step changes; the planner is a *prologue* that fixes the
sequence, the executor is Day 12's loop walked against that sequence, Day 13's
retry ladder gains one rung (**replan**), and Day 15's rejection classes decide
which rungs a failure may climb — a *repairable* failure climbs the ladder, a
*policy* wall is routed around or stopped on. And planning is not free, so the day
carries its own counterweight: **a one-step question must not be over-planned.**

## 1. Reaction vs reflection — what planning adds

Day 12's loop asked one question per step — *"given everything observed, what
next?"* — and Day 13 made that answer robust. It is a **reflex**: locally correct,
globally aimless. Nothing in a reflex loop holds the intent in view, so the loop can
search the docs, find nothing, search again, and never inspect the payment record —
because nothing told it the payment *matters*.

| | Reactive loop (Day 12) | Reflective planner (Day 16) |
|---|---|---|
| When it decides | after each observation | **before** any tool runs |
| What it holds | the last observation + transcript | an explicit **agenda** of steps + deps |
| Coverage | whichever the model thought of | **every required sub-question** |
| Order | whatever emerges | **dependencies respected** by construction |
| Cost | discovered as it is spent | **estimated before** the run |
| Failure | react per step | **repair / retry / fallback / replan** |

Three things planning buys, each of which becomes a fixture: **coverage** (P2 — a
multi-part question gets *all* its parts asked), **order** (P1 — *payment* depends
on *order state*, so the impossible ordering cannot even be expressed), and
**budget** (the estimate is taken *before* the spend, so an infeasible investigation
is refused rather than half-executed into a spent ledger).

### Planning is a configuration, not a new program

The lineage is the series' own. Day 11's `run_tool_answer` was the one-step case of
Day 12's `run_agent` — a config, not a different program. Day 16 inherits the same
structure: `run_agent(plan=…)` delegates to `executor.run_plan`, which walks the
plan's steps through the **same** atoms — one `chat_with_tools` turn per step, the
same registry, the same `execute_bounded` wall, the same error taxonomy and sibling
map. With `plan=None`, `run_agent` is Day 12 unchanged, byte for byte — which is
exactly why **a one-step plan is the identity case** and P9 can hold the line.

The honest cost sits on the same ledger: planning buys order and coverage with
**one extra chat** (the plan) and one new risk, **over-planning** — spending an
eight-step agenda on a question one query answers. Day 13's safety day measured its
own counterweight (a false-reject rate); Day 16 does the same, and P9 is that
measurement.

## 2. Decomposition — a plan is a DAG, not a list

The curriculum hands the day a linear eight-step list, and the first temptation is
to treat the list as the plan. It is not: it is one **linearization** of a
dependency graph. Day 10 recorded the *codebase's* dependency map
(`module → module`); Day 16 writes the *agent's* — but over **tool calls**:

```text
find_customer ─┐
find_checkout ─┼─→ (hypothesis) ─→ (verify) ─→ (respond)
order_state ───┤         ▲
payment ───────┘         │
docs ────────────────────┘
```

The arrows are *data* dependencies: `payment` needs the order located by
`order_state`; `verify` gates the response. A DAG buys two things a list cannot —
**order freedom** (independent steps could run in any order, or in parallel, a seam
named for later) and **dependency safety** (the executor may not run `payment`
before `order_state`, so an impossible ordering cannot even be expressed).

### What a step carries

A step is not a sentence; it is a **tool call plus metadata**, and each field is a
place a plan can be wrong:

| Field | Why it exists | The failure it prevents |
|---|---|---|
| `id` | referenced by dependencies | an ambiguous plan |
| `tool` | must be **registered** (Day 11) | a plan that can't be built (grounding) |
| `args` | schema-valid, like any tool call | a malformed call at runtime |
| `depends_on` | the DAG edges | out-of-order execution |
| `expected` | what observation would make it *done* | a step that "succeeds" without evidence |

The last two are the subtle ones. `depends_on` makes the *payment-after-order*
ordering structural rather than lucky; `expected` is what lets the executor
**compare reality to the plan** at each checkpoint (§4) and decide to replan.

### Grounding a plan — fail-closed

Day 14 grounded generation on the DB schema and treated a query against the GraphQL
schema as a **grounding** bug. Day 16 grounds the *plan* the same way:
`validate_plan` checks, cheap first, that every step names a **registered tool**,
that its **args pass the tool's schema**, that every **dependency id exists**, and
that the graph is **acyclic** — collecting *all* errors, not the first. An
unbuildable step is rejected **before execution** (FC1 in the evaluation: zero
steps, zero bills), with **one** informative retry carrying the validator's errors
— the plan-level analogue of Day 12's self-correction.

The planner generates **dynamically** (the model writes a plan for *this* question,
validated by code), not from a hand-written template per question class. But a
template is a legitimate *lower rung* of the Day 13 fallback ladder: when planning
itself fails, a canned per-class plan is a safe degradation — and the **entry rule**
(`needs_plan`) is the rung above it that decides whether to plan at all.

## 3. The planner + executor workflow — and where it lives

```text
question → [plan]     model writes steps + deps        (one chat, Day 4 gateway)
         → [validate] registered tools · schema args · acyclic · deps exist  (code, fail-closed)
         → [estimate] steps × chats ≤ budget → else replan / refuse          (Day 12's cap, plan-wide)
         → [execute]  topological order, one loop step per plan step         (Day 12's loop)
         │            ↳ observation appended, checkpoint written             (§4)
         │            ↳ expected-evidence check → replan if diverged         (§4)
         → [adapt]    repairable → repair·retry·fallback·replan (bounded)    (Day 13 ladder, §5)
         │            policy    → WALL: route around, or stop + surface       (Day 15 classes, §5)
         → [answer]   conclusion ⊆ evidence                                   (§6)
```

Every stage maps onto machinery that already exists: **plan** is one Day 4 chat;
**validate** is Day 11's registry applied to a plan; **estimate** is Day 12's budget
arithmetic lifted from `max_steps` to a plan; **execute** is Day 12's loop; **adapt**
is Day 13's ladder plus one rung; **answer** is Day 12's final turn, gated by
`conclusion ⊆ evidence`.

### Why the plan must stay visible

The same argument that kept Day 14's SQL on the table: a **hidden plan** cannot be
audited, graded, or repaired. If the plan is an artifact in the trace, a human can
see the investigation's shape, the trace can measure **plan adherence** (did
execution follow the plan, and where it did not, *why*), and a bad plan becomes a
fixable object rather than a mysterious outcome. `plan_adherence` grades the
*step's tool*, not the sequence, because a correct agent may legally walk a row in
more than one order.

### Two layers: the executor is a state machine, the loop is still underneath

The **executor** knows *which* step is current, what has completed, and whether the
remaining plan is still valid; the **loop** runs each step as it always did (tool
call → observation → next). This is the series' recurring "old seams made
declarative" move (Days 11–14): the plan adds no new way to call a tool, only a
*schedule* for the existing way.

## 4. Execution state and checkpoints — and the two different questions

Per step, a **status** (`pending → running → done | failed | skipped`), the
**observation** it produced, and whether that observation **diverged** from
`expected`. This is *task-shaped* working memory: not the conversation (Day 12's
transcript), not the plan (the agenda), but the **execution**.

A **checkpoint** written after each step is the minimal state needed to either
**resume** (continue at step *k+1* without re-running 1…*k*) or **replay**
(deterministically re-execute from the start and reach the same evidence). It gives
the plan two properties Day 12's loop could not have: **resumability** (P4) and
**auditability** (the checkpoint log *is* the investigation's provenance).

### "Where is the plan?" is not "may the loop stop?"

| Question | Day | Answer |
|---|---|---|
| *when may the loop stop?* | 12/13 | natural exit · `max_steps` · a guard (repeat / oscillation / timeout) |
| *where is the plan, and can it continue?* | 16 | the checkpoint: step *k* done, evidence held, plan still valid? |

A checkpoint is not a termination; it is a **resumption point**. P4 proves both
directions: an interrupted run returns `termination_reason: "paused"` with one
checkpoint, and the resumed run executes **only the remaining step** (a counting
registry shows exactly one call), carrying the earlier step's evidence; a replay
from step 0 reproduces the same evidence union.

### The re-plan gate

The checkpoint is also where the plan meets reality. After each step the executor
compares the observation to the step's `expected`. When they diverge — step 2 ("find
the checkout") returns *no rows at all* — it **replans the remaining steps** rather
than marching through a plan the evidence has already invalidated (P5). An empty
observation is still recorded as `done` (Day 13's "`no_data` is a finding, not a
failure") but flagged `diverged`; the *status* and the *judgment* are separate, and
that separation is what lets a legitimate absence be evidence while an unexpected
one still triggers a replan.

### Execution state is not memory

The seam, stated precisely because Day 17 is next: Day 16's execution state is
**ephemeral — it lives for the run**. `ExecutionState.as_dict()`/`from_dict()` prove
the *format* round-trips (which is how resume works), and nothing more. What must
outlive the process — *working* / *conversation* / *task* state, or a *persistent*
memory (episodic/semantic) — is **Day 17's** (and Day 18's). Day 16 builds the
checkpoint and deliberately does not build the store.

## 5. The retry ladder — repair · retry · fallback · replan, under Day 15's classes

Four rungs, cheapest first, and the distinctions matter because they cost
differently and route differently:

| Rung | What changes | Cost | Owner | Trigger |
|---|---|---|---|---|
| **repair** | the same step, **fixed arguments** | ~0 (re-issue)⁴ | Day 12/13 self-correction | a `repairable` rejection with a clear fix |
| **retry** | the same step, same args, again | 1 tool call | Day 13 bounded retries | a transient error (`timeout`, flaky) |
| **fallback** | a *different* tool for the same intent | 1 step | Day 13 fallback ladder | the tool can't serve the intent |
| **replan** | the **remaining plan** is rewritten | 1 plan chat⁴ | **Day 16 (new)** | the evidence invalidated the plan's shape |

⁴ In the stub world both model rungs are supplied *stored* (a pre-registered fix /
remainder), so they cost 0 chats; in the real world each is one chat. The
evaluation's cost table says which world each row is.

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

A plan that "fixes" its way past a policy denial is Day 15's **attack loop wearing a
planner** — a self-correcting/replanning system that helps the model probe for a
bypass has handed the adversary a fuzzer at a higher level. So the *same*
`rejection_class` governs the *plan* layer, one abstraction up. P7 asserts the
routing directly: the wall fires, **no rung is recorded**, `replans == 0`, one tool
step, and nothing fabricated.

Every rung is bounded: `max_repairs`, `max_retries`, one disclosed fallback, and
`max_replans`. A planner that replans without new evidence is Day 13's *oscillation
at plan scale*; the contradiction-driven replan (good) and the thrash replan (bad)
are distinguished by whether the step's evidence moved. The honest limit: replan is
the one rung that regenerates a **model** artifact mid-run, so the stub proves the
*routing* — when replan fires, that a policy wall never triggers it, that the bound
holds — never the *quality* of a model's replan (Day 21).

## 6. Verification — `conclusion ⊆ evidence`

The eighth step ("respond") is where a plan's investigation becomes a claim, and the
day's invariant is that the claim may draw only on the **evidence the plan
collected**. It is the third translation of the same rule:

| Day | Invariant | Evidence type |
|---|---|---|
| 6 | `cited ⊆ retrieved` | prose chunks |
| 14 | `asserted ⊆ returned` | result rows |
| **16** | **`conclusion ⊆ evidence`** | **the plan's observations** |

The mechanism is the **6→7 gate**: a failed or diverging step stops the run *before*
`respond`, so the executor cannot advance from a hypothesis it could not support to
a confident answer. Enforcement in the trace is mechanical: parse the final answer's
entities/numbers and assert membership in the plan's collected evidence (or a stated
aggregate of it) — a set-membership test over stored observations, not a judgment
call.

Two honest outcomes the gate allows:

- **plan exhausted, evidence insufficient** → the answer says *"could not determine
  why; here is what was checked"* — a **finding**, not a failure, and never an
  invented cause (P8);
- **a step legitimately found `no_data`** → recorded as evidence; the honest absence
  of a record is itself evidence ("no payment record exists for this order").

The failure this guards is the plan-shaped version of every prior day's cardinal
sin: a fluent, confident causal story ("the payment was declined") that no step
observed. A plan makes that sin *easier* to commit — a multi-step narrative is
persuasive — which is exactly why the gate is structural rather than a reviewer's
judgment. P8 proves it both ways: the insufficient-evidence run refuses to conclude
(`conclusion ⊆ evidence = ok` *vacuously*, because it asserts nothing), and the
fabricated twin — an answer citing a symbol the plan never observed — is **caught**
(`FAIL` in the table, the only failing row, and the point of the fixture).

## What this is not

Stated plainly, because a green plan table implies more than it proves:

- **A valid plan is not a good plan.** Validation proves a plan is *buildable*
  (registered tools, valid args, acyclic); whether a real model writes the right
  *decomposition* in the right *order*, and replans sensibly, is **Day 21's**
  judgment. P1/P2 are the validator and the coverage check; the model's plan quality
  is `PENDING`.
- **The stub supplies the plan.** The offline suite walks *stored* plans the way the
  fixture set supplies questions, so the workflow is `MECH` and free; `planner.plan`
  and `planner.replan` are exercised against the scripted stub, and their real
  quality is not measured here.
- **Execution state is not memory.** The checkpoint *format* is Day 16's; the
  durable **store** is **Day 17/18's**.
- **Parallel DAG branches are named, not built.** Independent steps (1–3, 5) could
  run concurrently; the day executes a topological *linearization*, and what breaks
  in the checkpoint/evidence model when branches truly interleave is recorded as a
  seam.
- **The over-planning guard is a heuristic.** `needs_plan` is a clause/conjunction
  rule, not a model call — deliberately, because an entry rule that needed a chat to
  decide whether to chat would have lost before it started. Its false negatives (a
  long single-intent question) are cheaper than its false positives (a wasted plan).

## Config (env-overridable)

| Key | Default | Meaning |
|---|---|---|
| `AGENT_MAX_REPLANS` | `1` | total replans per run (rung 4's bound) |
| `AGENT_MAX_REPAIRS` | `1` | repairs per step (rung 1's bound) |
| `AGENT_TOOL_RETRIES` | `1` | same-arg execution retries (Day 13's rung) |
| `AGENT_TOOL_TIMEOUT_S` | `30.0` | the wall around one tool call (Day 13) |
| `AGENT_BUDGET_CHARS` | `4000` | per-observation clamp (Day 11/12) |
| `budget_chats` (arg) | unset | the plan-wide chat budget → `infeasible` pre-spend |

## Negatives (honest, with owners)

1. **Real-model plan quality** — decomposition, ordering, sensible replans — is
   unmeasured by the stub. Owner: **Day 21's** benchmark on this harness.
2. **Replan quality and thrash-detection** — the stub proves the routing and the
   bound, not that a model's replan is an improvement; a `no_progress` fingerprint
   at plan scale is a recorded seam. Owner: **Day 21**.
3. **Durable state / memory** — the checkpoint format is built, the store is not.
   Owner: **Day 17/18**.
4. **Parallel branch execution** — a linearization is executed; true concurrency and
   its checkpoint/evidence semantics are named, not built.
5. **The entry rule's heuristic** — long single-intent questions are over-planned;
   question-shaped heuristics are a known approximation. Owner: **Day 21**.
6. **`conclusion ⊆ evidence` is id- and number-shaped** — prose that quietly asserts
   a cause without an id or a figure is not caught, the same honest limit the Day 12
   fabrication guard carries. Owner: **Day 21** (semantics).

## Re-verify, in one line

```bash
python tools/plan_smoke.py    # P1–P9 + fail-closed + the Days 11–15 no-regression re-run
```

`conclusion ⊆ evidence = ok` on every answered row (the fabricated twin the only
`FAIL`, and the point of the fixture), and `over-planned? = no` on P9. The
`PENDING`, plainly: whether a **real** model writes a **good** plan — and replans
sensibly — is Day 21's judgment on Day 16's planner. See
`docs/evaluations/planning-day16.md` for the §8 plan table.
