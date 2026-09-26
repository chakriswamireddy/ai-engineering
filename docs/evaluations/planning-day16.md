# Planning Day 16 — the plan set (P1…P9)

> Generated from the run of `python tools/plan_smoke.py` (offline: the Day-4
> scripted stub for the *chat* wire, the Day-7 doc corpus / Day-10 code corpus for
> *retrieval*, and the **seeded in-memory sqlite core subset** in `ai_os/data.py`
> for the *data* side). The stub world supplies a **stored plan** the way the
> fixture set supplies a question; the real world supplies it through
> `planner.plan()`.
> `MECH` = provable offline, here. `PENDING` = a real gateway is required. Two
> columns carry the day: **`conclusion ⊆ evidence` = `ok` on every answered row**
> (the fabricated twin the only `FAIL`, and the point of the fixture) and
> **`over-planned?` = `no` on P9** (the counterweight). A green table with a `yes`
> in either column is a failed day.

```
stores: 9 doc chunks (STUB) / 14 code symbols (STUB)   tools: 7 registered
seeded core subset (STUB, sqlite query_only)   τ_doc=0.05  τ_code=0.75
max_replans=1  max_repairs=1  tool_retries=1  tool_timeout_s=30  budget_chars=4000
```

## The fixtures, pre-registered before the run (§8)

| ID | What it proves | Input / injection | Expected |
|---|---|---|---|
| P1 | plan generation (curriculum) | "Investigate why customer X could not complete checkout" | valid, acyclic plan of **registered** tools; `payment ← order_state` present |
| P2 | decomposition completeness | same task | every required sub-question is a step (customer, checkout, order, payment, docs); no required dependency missing |
| P3 | execution state | any multi-step run | statuses `pending→running→done`; each observation recorded; a failed step recorded **as failed**, not dropped |
| P4 | checkpoints / resume | interrupt after step *k* | resume at *k+1* (completed steps not re-run); replay from 0 reproduces the evidence |
| P5 | replan-on-contradiction | step 2 finds **no checkout** | executor **replans** the remaining steps; answers nothing from the miss |
| P6 | retry ladder (repairable) | a step's tool returns `unknown_column` | bounded repair succeeds; plan continues; the **rung used** is recorded |
| P7 | policy wall | a step attempts a **policy-rejected** query (Day 15) | routes around or halts + surfaces; **not retried**; **nothing fabricated** |
| P8 | verification | hypothesis unsupported by collected evidence | run refuses to conclude; `conclusion ⊆ evidence` holds |
| P9 | **control** | a one-step question ("Fetch order 1") | **not over-planned**: one step, cost ≈ Day 12, no wasted plan chat |

The `MECH`/`PENDING` split is exactly Day 7's. **MECH** — P2, P3, P4, P6, P7, P9,
plus P1's *validation*, P5's *routing*, and P8's *check* — are pure functions of the
plan validator, the executor's state machine, the checkpoint format, and the retry
router over **stored** plans/observations: deterministic, offline, free (**68 checks,
0 fail**). **PENDING** — P1's and P5's *real-model* planning (does a real model
decompose the curriculum well, and replan sensibly) and P8's grounding under a real
model — need the real gateway and are **Day 21's** benchmark. The stub proves the
**workflow**; a real key proves the **planner**.

## §8 plan table (every cell a real run's)

| Task | Steps | Deps respected | Executed | Rungs | Policy walls | conclusion ⊆ evidence | over-planned? | Cost | wall_ms |
|---|---|---|---|---|---|---|---|---|---|
| P3 execution state | 2 | y | 2 | — | 0 | **ok** | no | $0.0000372 | 0.98 |
| P3b failed step recorded | 2 | y | 2 | — | 0 | **ok** | no | $0.0000372 | 0.86 |
| P4 resume after step k | 2 | y | **1** (k→k+1) | — | 0 | **ok** | no | $0.0000066 | 0.48 |
| P5 replan on contradiction | 3 | y | 3 | **replan** | 0 | **ok** | no | $0.0000438 | 2.27 |
| P6 repairable error | 2 | y | 2 | **repair** | 0 | **ok** | no | $0.0000372 | 2.01 |
| P6b rung bound (`max_repairs=0`) | 2 | y | 1 | **none** | 0 | **ok** | no | $0.0000306 | 0.71 |
| P7 policy-rejected step | 1 | y | 1 | **none** | **1** | **ok** | no | $0.0000306 | 0.15 |
| P8 unsupported hypothesis | 1 | y | 1 | — | 0 | **ok** | no | $0.0000306 | 0.27 |
| P8b **fabricated twin** | 1 | y | 1 | — | 0 | **FAIL** | no | $0.0000306 | 0.72 |
| P9 **control** (1-step Q) | **1** | — | 1 | — | 0 | **ok** | **no** | $0.0000306 | 0.14 |
| FC1 invalid plan | 1 | — | **0** | — | 0 | **ok** | no | $0.0000000 | 0 |
| FC2 infeasible plan | 5 | — | **0** | — | 0 | **ok** | no | $0.0000000 | 0 |

P1 and P2 are validation-only rows (no run) and are asserted in the fixtures table
above. The `Rungs` column is the retry strategy made legible: which rung fired, and
— for P7 — that **none** did, because a policy rejection is a *wall*, not a mistake.
`wall_ms` is the stub's tool time (sub-millisecond retrieval/DB arithmetic); the
*story* of a real latency number — model vs retrieval vs a hung tool — is **Day
23's** observability seam, named rather than faked.

## What the run actually produced

**P1 / P2 — the plan is validated and complete.** `D16_PLAN_GOOD` (five steps over
`get_customer`, `search_saleor_docs`, `get_order`, `run_saleor_sql`, `search_saleor_docs`)
validates: every tool is registered, every arg passes the schema, every dependency id
exists, and the graph is acyclic. `topological_order` places `order_state` before
`payment` and `find_customer` before `order_state`, so the curriculum's dependency is
**structural**. The coverage check finds all five sub-questions. The fail-closed twins
are the point: an **unregistered tool** (`get_invoice`) is a *grounding* failure caught
by code (`step 'find_customer' names unregistered tool 'get_invoice' (known: […])`),
a **cycle** (`a → b → a`) is rejected by the DAG check, and a **missing required arg**
is rejected with the field named. The model path is exercised too: a plan naming
`get_invoice` earns **exactly one informative retry** (two chats in the ledger) and is
then accepted once corrected; a plan that stays invalid comes back with `errors`
populated and is **never executed**.

**P3 / P3b — execution state, and the failed step that stays recorded.** Both steps
reach `done`, each observation is stored, and two checkpoints are written
(`resume_from: 3`). The failure twin refuses a step (`search_saleor_docs` on an
out-of-corpus query): the step is recorded `status: failed` **with its error** and
stays in the state — the investigation is auditable, and a step that failed is not a
step that vanished.

**P4 — checkpoints are resumption points, and a resume does not re-run.** An
interrupted run (`stop_after=1`) stops with `termination_reason: "paused"` and
**one** checkpoint, and answers nothing. Resuming from the serialized state
(`ExecutionState.from_dict`) executes **only the remaining step** — a counting
registry shows exactly `["get_order"]` — carries the first step's evidence forward
from the checkpoint, and reaches a grounded answer. A replay from step 0 reproduces
the same evidence union, which is what makes "the same trace graded twice is the same
row" true for a planned run.

**P5 — replan-on-contradiction.** Step 2's query returns **0 rows** (`no such order
window`): the observation is recorded as `done` (a finding, not a crash) but flagged
`diverged`, so the re-plan gate fires, the `replan` rung is recorded once, and a
**rewritten remainder** (step `s2b`: `get_order`) runs and answers. The empty step
contributed **no evidence**, so the answer cannot cite anything from the miss — the
plan was not marched through on an agenda reality had already invalidated.

**P6 / P6b — the repairable rung, and its bound.** A generated query with an unknown
column is rejected `repairable` (`unknown_column`); the **repair** rung supplies fixed
arguments for the *same* step, the call succeeds (`plan_repairs: 1`), the rung is
recorded, and the plan continues to step 2. The bound is proven the other way: with
`max_repairs=0` the same failure fires **no rung** and the plan stops honestly
(`plan_exhausted`) rather than looping on a fix it is not budgeted for.

**P7 — the policy wall, and no rung climbs it.** A plan step attempting
`DELETE FROM order_order` is refused as `policy`: `policy_violations: 1`,
`policy_walls: 1`, **`rungs: []`**, `replans: 0`, one tool step, and the answer
surfaces the refusal without looking for a workaround. The wall is never fed to
repair, retry, fallback, or replan — a self-correcting planner that helped a model
past a denial would be Day 15's attack loop wearing a planner.

**P8 / P8b — `conclusion ⊆ evidence`, proven both ways.** When a step's evidence is
insufficient and no replan is available, the run **refuses to conclude**:
`plan_exhausted`, an honest non-answer ("I could not determine why …; what was
checked …"), and the invariant holds *vacuously* because nothing is asserted. The
fabricated twin — an answer citing `saleor/ghost/checkout.py#CompleteCheckoutMagic`,
a symbol the plan never observed — is **caught** (`FAIL` on the headline column),
which is the row that gives the green rows their meaning.

**P9 — the counterweight.** `needs_plan("Fetch order 1")` is `False` (*"single-intent
question — the Day 12 loop already answers it"*), so no plan chat is spent at all;
the one-step stored plan runs through `run_agent(plan=…)` with **1 step chat + 1
answer chat, 0 plan chats** — Day 11/12's exact shape — `over_planned: False`, and
the answer grounded in the step's record. The negative twin: a three-step plan for the
same single-lookup question is flagged `over_planned: True`.

**FC1 / FC2 — fail-closed, pre-spend.** An **invalid** plan (unregistered tool) and an
**infeasible** one (estimated cost over `budget_chats`) both execute **zero steps and
bill zero calls**; their terminals are `invalid_plan` / `infeasible` and the validator's
reasons ride in the run object as a fixable artifact.

## Layer mechanics (the day's own unit checks)

| Assertion | Result |
|---|---|
| `validate_plan`: unregistered tool · schema-invalid args (field named) · unknown dependency · self-dependency · **cycle** (path reported) · duplicate id | ok |
| `topological_order` is deterministic and dependency-safe (stable tie-break on plan order) | ok |
| `parse_plan` reads fenced or bare JSON and rejects a reply with no JSON | ok |
| `planner.plan`: one informative retry on a rejected plan (2 chats), then acceptance; a still-invalid plan returns `errors` and is not executed | ok |
| `needs_plan`: multi-intent/causal → plan; single-intent → no plan (the entry rule owns P9) | ok |
| `ExecutionState.as_dict()`/`from_dict()` round-trips (the resume path) | ok |
| the retry ladder's **rungs are bounded** (`max_repairs`, `max_replans`) and the fired rung is recorded per step | ok |
| a **policy** rejection fires no rung and never triggers a replan | ok |
| `conclusion_subset_evidence` checks ids **and** numbers (via `asserted ⊆ returned` over the plan's data records) | ok |
| `plan_adherence` grades the step's tool, not the sequence (order freedom preserved) | ok |

## No-regression line (Days 11–15 ride the planned executor)

| Line | Result |
|---|---|
| `tools/tool_smoke.py` (T1–T7) re-run in a subprocess | **exits 0 — `ALL DAY 11 (MECH) CHECKS PASSED`** |
| `tools/agent_smoke.py` (A1–A8) | **exits 0 — `ALL DAY 12 (MECH) CHECKS PASSED`** |
| `tools/failure_smoke.py` (F1–F9) | **exits 0 — `ALL DAY 13 (MECH) CHECKS PASSED`** |
| `tools/data_smoke.py` (Q1–Q8) | **exits 0 — `ALL DAY 14 (MECH) CHECKS PASSED`** |
| `tools/sql_attack_smoke.py` (X1–X11) | **exits 0 — `ALL DAY 15 (MECH) CHECKS PASSED`** |
| `test_day7..10.py`, `test_chunk/search/rag.py` | **all green** (run independently) |

The planner is **additive** — an agenda above the loop — and deliberately changes
**one** thing in existing code: `run_agent` gains an optional `plan` parameter that
delegates to `executor.run_plan`. With `plan=None` the Day-12 body is untouched, which
is why a planned one-step run reproduces Day 11/12's shape and why a planned executor
that changed a single-step answer would be a **P9** failure arriving through the front
door. `ai_os/trace.py` gains only new symbols (`PLAN_TERMINATIONS`, `plan_adherence`,
`conclusion_subset_evidence`, `plan_columns`) — no existing function or row shape moved.

## Cost shape (planning spends to save)

| Event | What it spends |
|---|---|
| the **`MECH` layer** (validation, state, checkpoints, adherence, the invariant) | **0 chats, 0 embeds** — arithmetic over one plan and one state |
| a **stored** N-step plan (stub world) | **N step chats + 1 answer chat**, **0 plan chats** — the plan is pre-registered |
| a **model** plan (real world) | **+1 plan chat** (quality `PENDING`, Day 21) and **+1 per replan** |
| a **repair / replan** rung (stub) | 0 chats (a stored fix/remainder); **+1 chat** each when model-backed |
| an **invalid / infeasible** plan | **0 chats, 0 steps** — refused before the spend |
| **P9**, the floor of the trade | **1 step chat + 1 answer chat, 0 plan chats** — exactly Day 12, because the entry rule declined to plan |

The bet the day records honestly: a planned answer costs **one extra chat** (the plan)
and must earn it back in **fewer wasted steps**. The stub cannot price the *saving*
(the plan is stored and the questions are scripted); the *premium* is measured, and
the *saving* is the real world's — Day 21's.

## Config knobs (Day 16)

| Key | Default | Meaning |
|---|---|---|
| `AGENT_MAX_REPLANS` | `1` | total replans per run (rung 4's bound) |
| `AGENT_MAX_REPAIRS` | `1` | repairs per step (rung 1's bound) |
| `AGENT_TOOL_RETRIES` | `1` | same-arg execution retries (Day 13's rung) |
| `AGENT_TOOL_TIMEOUT_S` | `30.0` | the wall around one tool call (Day 13) |
| `AGENT_BUDGET_CHARS` | `4000` | per-observation clamp (Day 11/12) |
| `budget_chats` (arg) | unset | the plan-wide chat budget → `infeasible` before any spend |

## Negatives / `PENDING` (honest, with owners)

1. **Real-model plan quality** — does a real model decompose the curriculum's task
   well, order it correctly, and replan sensibly? The stub supplies a stored plan, so
   this is **Day 21's** judgment on Day 16's planner. `PENDING`.
2. **Replan quality and thrash** — the stub proves the *routing* (the rung fires on
   divergence, a policy wall never triggers it, the bound holds); the *quality* of a
   model's replan, and a `no_progress` fingerprint at plan scale, are unmeasured.
   Owner: **Day 21**.
3. **Durable state and memory** — the checkpoint *format* round-trips and resume
   works in-run; the persistent **store** is **Day 17/18's** seam, named not built.
4. **Parallel DAG branches** — the day executes a topological *linearization*; steps
   1–3 and 5 could run concurrently, and what breaks in the checkpoint/evidence model
   when branches interleave is a recorded seam. *Named, not built.*
5. **The entry rule is a heuristic** — a long single-intent question can be
   over-planned; the rule is deliberately a clause/conjunction test rather than a chat
   (a plan chat to decide whether to chat would have lost already). Owner: **Day 21**.
6. **`conclusion ⊆ evidence` is id- and number-shaped** — prose that asserts a cause
   without an id or a figure is not caught, the same honest limit Day 12's fabrication
   guard carries. Owner: **Day 21** (semantics).

## Re-verify, in one line

```bash
python tools/plan_smoke.py    # P1–P9 + fail-closed + the Days 11–15 no-regression re-run
```

**`conclusion ⊆ evidence = ok` on every answered row** (the fabricated twin P8b the
only `FAIL`, and the point of the fixture) and **`over-planned? = no` on P9**. The
numbers a future observer can carry: whether an invalid plan ever executed (never —
zero steps, zero bills), whether a completed step was ever re-run on resume (never),
whether a policy rejection ever climbed a rung (never — `rungs: []`), whether a
one-step question ever paid for a plan chat (never), and whether the planner moved any
of Days 7–15's numbers (it did not). The `PENDING` line, plainly: **whether a real
model writes a good plan — the right decomposition, the right order, sensible
replans — is Day 21's judgment on Day 16's planner.**
