# Day 12 Notes — Agent Loop

> Notes answering `docs/day-plan/day-12.md`.
> Day 11 built the tool layer and capped it at a single tool turn so termination existed before the loop did. Day 12 lifts the cap: the single turn becomes a loop — user request → reason → choose tool → execute → observe the result → reason again → choose the next action → final answer. What changes is underneath the loop: **agent state** is redefined (the transcript *is* the memory — there is no hidden variable), **observations** arrive as the same evidence record the harness has graded since Day 7 (now per step), **tool selection** is Day 11's recall@1 harvested on every iteration, and **termination** gains two new legitimate exits (`max_steps`, budget) beside the natural one (an answer turn with no `tool_calls`). Everything Day 11 built — registry, validation, refusal-as-a-value, the Day 4 gateway's tool wire — is exercised **as-is**; Day 12's additions are the loop that keeps asking, the guardrails that bound it, and the per-step trace that makes a multi-turn agent as auditable as a single Day 7 query.
> The single idea: **an agent is a loop with a memory (the transcript), a stopping rule (one of three exits), and a grounding contract (every final claim `cited ⊆ retrieved` over the accumulated observations) — and Day 12 is the day the harness starts grading the *sequence* instead of the single turn.**

---

## 1. Agent State — the Loop's Memory

Day 12's first lesson is almost a deletion: **there is no agent state beyond the transcript.** The user request, every assistant `tool_calls` turn, every `tool` observation, and the final answer — that immutable list *is* the memory the model reasons over. This matters for three reasons, all of them degradations of "state" as software people usually mean it:

| "State" temptation | Reality | Cost of the temptation |
|---|---|---|
| a hidden variable (a `self.plan`, a scratch dict) the loop mutates | the transcript is the only state the model can see; a scratch dict is state *the model cannot see* | a plan the agent holds invisibly is a plan nothing verifies or corrects |
| "efficient" reuse of a field | every step re-reads the transcript the way Day 7 re-ran retrieval | the redundancy is the point: forgetting is a string problem, not a cache problem |
| a finished final answer that "summarizes" | the final answer is a turn in the same transcript, grounded the same way | an answer that cites observations the transcript doesn't contain breaks `cited ⊆ retrieved` |

The audit is split into two records that must not be confused: **the transcript** is the mechanism the model reasons over; **the ledger** (Day 4) is the audit of money and steps. Day 4's ledger row bills one chat call; Day 12's per-step trace row debugs one decision. Two tables, two owners — a vault row and a trace row are both written per step, and neither is the other.

### What an observation is

An observation is a tool result appended to the conversation as a `tool`-role message — the same three shapes Day 11 shipped:

- `top-K evidence` (Day 8 list, Day 10 `module#Symbol` records, `max_chars`-clamped) — the shape the harness grades;
- `{refused: true}` — the Day 7 gate as a return value, now arriving mid-transcript;
- `{ok: false, error: "…"}` — a rejection naming the field, now arriving mid-transcript.

Nothing about an observation is prose. The prompt that precedes a step can say "here is what the tool returned", but the *observation* is the evidence record, verbatim, clamped by `budget_chars`. The moment an observation is reformatted into a summary is the moment the harness stops being able to check `cited ⊆ retrieved` on it.

### Selection repeats the Day 11 lesson, per step

The model picks a tool by reading its `description` at *every* step, so Day 11's hard-won rule — the description is retrieval-for-a-chooser, written with the same stakes as the chunk heading written for a scorer — now applies per iteration. Day 11's tool-selection recall@1 ("tool chosen vs expected tool") becomes Day 12's **per-step recall@1** ("tool chosen at step n vs expected action at step n"). And the first suspect on any bad step is still the description: a loop that repeatedly grabs `search_saleor_docs` for code questions is not a loop bug; it is a description bug, fixed by editing the metadata, exactly as Day 11 fixed T1.

---

## 2. Termination — When a Loop May Stop

The discipline Day 11 imposed with a cap is carried forward as the discipline Day 12 makes *structural*. An agent is a loop with three legitimate exits, and the trace must be able to say which one fired:

1. **The natural exit.** The model's answer turn carries no `tool_calls` — it answered from the evidence in hand. This is the only exit Day 11 knew (`run_tool_answer`'s otherwise-branch), and it survives unchanged in `run_agent`.
2. **`max_steps` reached.** The loop stops *and says so*, recording `termination_reason: max_steps` and naming Day 13 (whose taxonomy owns "why couldn't this model converge"); it does not invent a final step, and it does not pretend the transcript is complete.
3. **Budget spent.** The ledger's cumulative cost for the run trips the cap. The loop surfaces *already-gathered* evidence and reports `termination_reason: budget`. Money ends the loop the way it bytes anything in the OS — visibly, in the trace, never silently.

The pre-commit that makes these exits honest: **termination is a contract with the user.** A transcript that dies at `max_steps` is a failed-agent transcript — the answer must say so, not blur into a confident non-answer. `termination_reason` is output, not debug-only.

### The Day 13 boundary

An *infinite loop* (Day 13's first failure mode) is exactly a loop that never hits a legitimate exit. Day 12 does not build the taxonomy of *why* agents spiral (repeating the same wrong tool, oscillating between two tools, re-issuing the same rejected call). Day 12 builds the exits and makes them reachable on schedule, so that "infinite" is impossible **by construction**: `max_steps` and budget both fire no matter what the model emits. Day 13 then gets to study *compound* failure — loops whose steps are individually plausible but jointly stuck — without spending a day discovering that an uncapped loop eats money first.

### Retries wear two costumes

- **Provider retries** — Day 4's backoff+jitter, `Retry-After` handling, ordered fallback, per-attempt ledger rows — happen *inside* one `chat_with_tools` call. The loop never sees them; a black 5-second retry storm is one ledger row to the loop. Cost: `n` bills for one logical step.
- **Loop self-correction** — a rejected tool call re-issued with fixed arguments — is a *new step*, an observation, a bill, and a trace row. It is Day 11's "rejection is a message" paying its dividend: the model read the rejection, fixed the arg, re-asked. It is bounded (§6), because the difference between self-correction and "same wrong thing, louder" is only visible in the step sequence — and only a bound turns the diagnostic into a guarantee.

The one rule that carries over from Day 11 into both: **refusal is an observation, not a retry signal.** `search_saleor_code` returning `{refused: true}` is evidence the question is out-of-corpus; re-issuing the identical call is the Day 8 fabrication bug wearing a loop. The Day 7 gate object may not be "tried harder" into submission.

---

## 3. The Loop

```python
def run_agent(request, registry, gateway, *, max_steps, budget_chars, self_correct=2):
    transcript = [{"role": "user", "content": request}]
    trace, steps, spent = [], 0, Ledger()
    while steps < max_steps:
        completion = gateway.chat_with_tools(transcript, tools=registry.schemas())
        if not completion.tool_calls:                     # exit 1 — answered from evidence
            return final_answer(completion.text, trace=trace, termination="answer")
        observations, step_row = [], {}
        for tc in completion.tool_calls:                  # parallel calls, each its own observation
            vr = registry.validate_args(tc.name, tc.arguments)
            obs = vr if not vr.ok else registry.execute(tc.name, vr.args)
            observations.append(to_tool_role(tc.id, obs, budget_chars))
            step_row[tc.id] = (tc.name, tc.arguments, vr, obs, spent.add(...))
        transcript += [assistant_turn(completion.tool_calls)] + observations
        steps += 1; trace.append(step_row)
    return terminated(transcript, reason="max_steps", trace=trace)   # exit 2 (exit 3: budget)
```

The whole day is in the shape of this function. Three properties are deliberate:

1. **`run_agent(max_steps=1)` is Day 11.** The single-turn tool answer is a configuration of the loop, not a separate program. That is the no-regression guarantee spelled out: T1…T7 run unchanged, through the loop, at depth 1, and must reproduce Day 11 exactly — a loop that changes a single-tool answer is a bug wearing an agent. The `depth1` check pins it (`answer`, `turns: 2`, no `max_steps` note); a pre-fix regression injected a false "step limit" note into every single-tool answer, and the live run caught it (§8).
2. **Observations are appended, never summarized.** The transcript grows by one `assistant` turn (with `tool_calls`) and one `tool` message per executed call. Nothing is mutated, nothing is dropped; the model's "memory" is literally the accumulated conversation. Parallel `tool_calls` in one step each get their own observation — budget-aware parallelism, not a shortcut that loses a row.
3. **Termination is a return value.** Exits 1, 2 and 3 all return a `run` whose trace ends with a `termination_reason`. The final answer under exit 2 or 3 is *limited to already-gathered evidence* — the model is not asked to "complete" what the budget cut off, it is asked to say what the evidence shows and that the loop stopped.

### Parallel calls and their cost

A step may emit several `tool_calls` (Day 11 forbade two; the loop allows them). Each is validated, executed, and observed separately. The economics do not pretend to be special: one chat carried the `k` tool requests (one bill), each retrieval step embeds its query (k bills), and the observations all enter the one transcript. Parallelism is context — the model saw k results before reasoning again — not a discount.

---

## 4. Building the Loop

The build is deliberately Day 11's files, grown:

- **`ai_os/agent.py`** — `run_tool_answer` generalizes into `run_agent`; the tool registry (`ai_os/tools.py`) and the gateway's `chat_with_tools` get **zero line changes**. The loop is the missing `else` branch: instead of terminating after one observation-append, loop — and after the last allowed step, ask **once more** so the model can answer *naturally*; only a completion that still asks for tools at the cap is recorded-not-executed (`capped_tool_calls`). That is what keeps `run_agent(max_steps=1)` Day 11 exactly, and `depth1` guards it.
- **`ai_os/agent.py:REASONING_PROMPT`** — the reasoning-turn system prompt §6 specifies: observations are evidence for the *next* step, a refusal is a finding, a corrected retry is a different call. It was the missing piece behind non-composition (§7/§8): without it the model rephrases the question; with it, the live run walks the map. Content only — no code path changes; the stub ignores it, so offline stays byte-identical.
- **`ai_os/trace.py`** — column-per-step serialization: `step | tool_calls | each ⇒ {name, args_parsed, args_valid, result_ok, refused, cited, retrieved, ledger_ref} | tokens | cost`. Day 7's per-query trace becomes Day 12's per-step trace; a step is as auditable as a Day 7 query was, because the row shape is the same.
- **`tools/mock_gateway.py`** — scripted *multi-step* scenarios: fix-then-answer (bad args, then good), pivot-after-refusal (refuse code for a docs question, then the model uses docs), parallel (two calls one step), never-stop (the model keeps asking — proves the cap fires). The whole mechanical suite is deterministic, offline, and free: same Day 4 pattern, scripted to turn N, not turn 1.

The one subtle bug to design against (inherited from Day 11's edit discipline): the loop's exit-1 check must read "no `tool_calls` in the answer turn" off the **already-corrected** completion parser. A plain-text answer turn that arrives with empty `content` and no `tool_calls` is still the Day 6 transient glitch (retry), not an exit — Day 11 gave the empty-content rule a sibling, and Day 12 must not re-merge them by checking exit-1 on raw content.

---

## 5. Multi-Step Investigation — Walking Day 10's Map

Day 10 stored the map; Day 12 walks it. The canonical investigation:

```text
"What happens after CheckoutComplete submits?"
  step 1  search_saleor_code("CheckoutComplete", kind="mutation", module="saleor/graphql/checkout")
          → CheckoutComplete { imports: [checkout.complete_checkout], deps: [complete_checkout] }
  step 2  search_saleor_code("complete_checkout", kind="function", module="saleor/checkout")
          → complete_checkout { deps: [order], calls create_order_from_checkout }
  step 3  (orders are seeded) get_order(id=…)            ← the stubbed data seam, legitimately reached
  final   "CheckoutComplete (…) calls complete_checkout (…) which creates the order (…) —
           cited ⊆ retrieved over both steps"
```

Three properties make this Day 10's map come alive, not a toy:

1. **The second step is chosen by the first step's evidence.** The model did not know `complete_checkout`'s name before observation 1; it read the `imports`/`deps` field on the returned chunk (the map Day 10 recorded without walking) and turned it into a new query. That is the difference between an agent and a pipeline: step n+1 is *caused* by step n's observation.
2. **The corpora mix across steps.** Docs (`search_saleor_docs`) to ground a concept, code to locate it, stubbed data (`get_order`) to ground a fact. This is the skeleton Day 30's scenario 4 (customer investigation) and scenario 5 ("why is this test failing?") will flesh out with real resolvers (Day 14) and real models (Day 21).
3. **The seam to Day 16 is named, not hidden.** Day 12's loop is **reactive**: the decision is made at each step from the current transcript. Day 16's planner-decider is **reflective**: Day 8's decomposition done by a model *before* acting. Both loop; the seam is *where the decision is made*. Day 12 ships reactive and records the planner as Day 16's — because a budget game could not afford both, and the reactive loop already isolates (which tools, which order, which stopping rule) all the variables Day 16 will need.

Grounding is enforced per step and therefore over the transcript: step n's final-answer citations must belong to step n's observation — `cited ⊆ retrieved` at every hop, so the union of observations is the only source the final answer may cite.

---

## 6. Guardrails

A loop without guardrails is a function that calls `max_steps` "safety" and hopes. Three bear names:

1. **Self-correction is bounded.** A rejected call (bad args, unknown tool) may be re-issued at most `self_correct` times (a config, `default 2`). Beyond that the loop records `gave_up`, appends the last rejection as the observation, and the final answer may *name* the stuck point honestly. Two properties made possible only by Day 11's validation design: the re-issue is possible **because** rejection is a message naming the field (a raised exception would have killed the turn), and the stop is verifiable **because** the trace counts rejections per tool.
2. **Refusal is never reinterpreted.** A refused tool is not retried into the budget vault, and an all-refusals step does not license the final answer to restate. The trace's `observed_refusals` column is what lets the harness tell "the agent pivoted" (legal — it chose `search_saleor_docs` after code refused) from "the agent fabricated" (illegal — it asserted a `module#Symbol` that never arrived). The Day 8 warning file does not close on Day 12; it gains a loop-shaped row.
3. **Budget is a run-level cap, not a habit.** `budget_chars` clamps every observation (Day 11's `max_chars` discipline, per step), and the ledger's cumulative cost is checked at the top of each loop. When the cap trips: `termination_reason: budget` and a final answer strictly over already-gathered evidence. The reason the cap exists is the reason Day 3 logged "Retries / agent loops — cost multiplies by number of calls" as a risk: the loop multiplies Day 11's per-request shape by `steps`, and only a config named in the trace keeps that multiplication honest.

The prompt that precedes each reasoning turn is written to the same standard the schemas were: *tool results are evidence; a refusal is a finding, not an answer; if a call was rejected, fix the argument and retry — a corrected call is a different call; a repeated wrong call is a cost.* The grader can read the transcript and decide which happened, which is the only thing that makes "self-correction" a virtue instead of a shrug.

---

## 7. Test: The Agent Question Set

Expected facts written **before** the run, like every day since Day 7:

| ID | What it proves | Input (stub) | Expected |
|---|---|---|---|
| A1 | multi-step investigation | "What happens after CheckoutComplete submits?" | ≥2 `search_saleor_code` steps following `imports`; final answer cites symbols from ≥2 observations |
| A2 | self-correction | step 1 emits bad args (`kind="mutations"`) | step 2 re-issues with `kind="mutation"`; trace shows 1 rejection + 1 success |
| A3 | pivot-after-refusal | code tool refuses a docs question | step 2 uses `search_saleor_docs`; no fabricated symbol |
| A4 | parallel calls | stub emits two `tool_calls` one turn | both execute, both observed, both cited or acknowledged |
| A5 | termination on `max_steps` | stub keeps asking past the cap | `truncated: true`, `termination_reason: max_steps`, answer names the limit |
| A6 | grounding across steps | final answer's citations | `cited ⊆ ∪(step observations)` — the multi-turn invariant |
| A7 | zero-tool question | "Summarize checkout in one sentence" | first answer turn has no `tool_calls`; `tool_steps: 0` |
| A8 | parallel capacity guard | three tool calls in one step | all validated/executed/observed within budget; no dropped observations |

The `MECH`/`PENDING` split is exactly Day 7's. **MECH** — A2 mechanics, A3's pivot plumbing, A4, A5, A7, A8 — are pure functions of the registry, the stores, and scripted stub turns: deterministic, offline, green. **PENDING is now measured**: `tools/agent_smoke.py --live` runs A1/A6 against the real gateway, and the free `nvidia/nemotron-3-super-120b-a12b:free` model follows the map (`complete_checkout` → `create_order_from_checkout` → `process_payment`) once the §6 reasoning prompt is present. Day 21 owns the cross-model strength verdict. The stub proves **the loop carries** N turns; `--live` proves **the model drives** them.

Three obligations, unchanged in spirit:

1. **No regression on Day 11.** T1…T7 run unchanged through `run_agent(max_steps=1)` and must stay exactly as green — a loop that changes a single-tool answer is a bug wearing an agent. Behind the tools, Days 7–10's fixtures must also stay green: the loop added a driver, not a new retrieval path. The `depth1` check in `tools/agent_smoke.py` is the guard, and adding it caught a genuine regression: at `max_steps=1` the loop executed the one tool turn, called that the cap, and injected the "step limit" note into Day 11's answer — which a real model echoed verbatim (see the live run). The fix: the loop asks **once more** after the last allowed tool step, so `run_agent(max_steps=1)` takes the *natural* answer exit; only a completion that still carries `tool_calls` at the cap is recorded-not-executed (`capped_tool_calls`).
2. **Isolation discipline on any failure, attributed per step.** Right symbol missing from step n's observation → step n's *retrieval* owns it. Symbol in the observation but the next step ignored it → the **loop prompt** owns it (the agent lost the thread between steps) — a new suspect class, born today. Loop never terminates → `max_steps`/budget ownership. Wrong tool at a step → that step's *description* owns it. Say which, never vibes; the trace's per-step rows are what make the attribution possible.
3. **Parallel work is not dropped.** A8 exists because the cheapest way to "handle" multi-call steps is to execute the first and hope; the cap makes the harness assert all three observations entered the transcript.

---

## 8. Measure: The Loop as a Contract

Rule-4 per step, one table per run, every cell a real run's:

| Step | Expected action | Tool chosen | Args valid | result ⊆ retrieved | Gate correct | Cum. steps | Step cost | Cum. cost |
|---|---|---|---|---|---|---|---|---|
| 1 | `search_saleor_code` (mutation) | … | … | … | … | 1 | … | … |
| 2 | `search_saleor_code` (service) | … | … | … | … | 2 | … | … |
| 3 | `get_order` | … | … | … | — | 3 | … | … |
| — | final answer | — | — | `cited ⊆ ∪obs` | — | 3 | … | **total** |

The **`tool chosen` vs `expected action`** column is Day 11's recall@1 **harvested per step** — the metric the loop optimizes from Day 13 on, and the one column whose real-model cells stay `PENDING` under the stub (the stub's scripted turns are the "truth" the harness checks).

The cost shape, recorded twice so it cannot be misread: one question costs **`tool_steps` chat turns + `tool_steps` embeddings (one per retrieval step) + 1 final chat**. Day 11's fixed 3-bill shape multiplies by the number of steps — this is Day 3's warning arriving as a config. The worst case is `max_steps × 2 + 2` bills — one chat more than the §3 bound, because the loop asks once more after the last allowed step, which is the turn that makes `run_agent(max_steps=1)` *be* Day 11 instead of a capped single turn (§7). The run reports the actual, and the ledger per-step rows are the audit trail under the multiply.

The negatives column, with owners: the loop cannot yet *survive* its own failure modes — infinite/oscillating loops, contradictory observations, hallucinated tools, wrong-but-valid calls — **Day 13** owns those; the stubbed data tools still return no real Saleor data — **Day 14**; and whether a real model drives a genuinely good multi-step investigation is **Day 21**. Empty negatives would mean the day learned nothing; these have owners, not wishes.

Day 12's `PENDING` composition cell is measured, not just wished: `tools/agent_smoke.py --live` swaps the *chat* wire for the real gateway (retrieval stays on the stub stores) and measures, per run, whether the model follows an evidence-carried symbol to a new query. With the §6 reasoning prompt in place, the free `nvidia/nemotron-3-super-120b-a12b:free` model **does** walk the map — `step 2 ← complete_checkout`, `step 3 ← create_order_from_checkout`, `step 4 ← process_payment` — and answers with `cited ⊆ ∪ observations` and no fabrication. Without the prompt it rephrases the question; the prompt, not the loop, was the missing piece. Contract cells (valid args, `cited ⊆ ∪ observations`, a real exit) are hard-checked; composition cells are `measure`d, and a provider outage reports `[SKIP]`/exit 2 rather than a false FAIL. Limitation: the free-tier cap (~50 req/day) is easily exhausted (§8).

---

## 9. Documents

- `docs/concepts/agent-loop.md` — state-as-transcript, the observation turn, the three terminations, per-step grounding, the reactive-vs-reflective seam (Day 16).
- `docs/evaluations/agent-day12.md` — the §8 per-step tables (per-step recall@1, grounding-over-transcript, termination reasons), the A1…A8 pre-registered fixtures, the corrected `max_steps × 2 + 2` budget bound, the depth-1 (Day 11) no-regression line, the `--live` run (real-model composition, measured negative on the free model), and the T1…T7 re-run.
- PROGRESS rollup: Day 12 mechanics move to `DONE` on the stub (loop, transcript-state, three exits, parallel observations, self-correction bound, per-step trace verified offline); the real-model composition cell is now *measured* per run via `--live` (the free model does not follow the map) but stays `PARTIAL` until Day 21 rules on a stronger model.

---

## 10. Daily Learning Record

### Problem

The OS could act but not *investigate*. Day 11's deliverable was one tool turn: a question answered by exactly one call, with termination hard-coded as a cap. The problem is to lift the cap into a general loop — the same tool layer, driven repeatedly — and to keep the loop honest: state that is actually visible (the transcript, not a hidden plan), observations that are evidence in the graded shape, grounding that holds across turns, and termination that can always say why. Without the loop, multi-step questions ("what happens after checkouts complete?") are unanswerable; with a naive loop, cost multiplies, refusals get retried into the vault, and a confident final answer about nothing arrives on schedule.

### Concept

**An agent is a loop with a memory, a stopping rule, and a grounding contract — and each is a renamed discipline from earlier days.** The memory is **the transcript** (agent state = the messages; no hidden variable exists), so forgetting is a string problem, not a cache problem. The stopping rule is **one of three exits** — an answer turn with no `tool_calls`, `max_steps`, or budget — with `termination_reason` as part of the output, because termination is a contract with the user. The grounding contract is **per-step `cited ⊆ retrieved`**: each step's final-answer citations must belong to that step's observation, so the union of observations is the only source a final answer may cite. Tool selection repeats Day 11's recall@1 per step, and the description stays the first suspect. The loop inherits Day 11's two refusals-separated-from-retries rule (refusal is an observation, self-correction is a *new* step, both bounded), and it runs Day 12's added guardrails: a self-correction bound, a budget that is a run-level cap, and a reactive (not reflective) loop whose planner-seam is recorded for Day 16.

### Understanding

The unlock is that **Day 12 invents almost nothing — it generalizes the under-branch of Day 11 and adds guardrails.** `run_agent(max_steps=1)` *is* `run_tool_answer`; the registry, the stores, and the gateway's tool wire are untouched; the empty-content rule stays corrected-but-unmerged. The second understanding is that the new unit of honesty is the **step sequence**, not the answer: the per-step trace makes a two-step investigation as auditable as a Day 7 query, which is what lets attribution work ("the agent lost the thread between steps" is a loop-prompt bug that never existed for a single turn to have). The third, the honest one: the mechanical half (the loop carries N turns, the three exits fire, observations are never dropped, self-correction is countable) is green under the stub, while the *composition* — a real model following an import to a second query it was never told — is `PENDING` until a key has quota. Day 12 built the track the train runs on; whether a good engineer drives the train is Day 21's measure.

### Implementation

- `ai_os/agent.py` (extended) — `run_agent`: `run_tool_answer` generalized; transcript-as-state; per-step execution of parallel `tool_calls`; three termination reasons returned in the run object.
- `ai_os/trace.py` (extended) — per-step columns (args_valid / result_ok / refused / cited / retrieved / ledger_ref); `termination_reason`, `truncated`, `tool_steps`, `observed_refusals`, `gave_up`.
- `ai_os/tools.py` — **unchanged** (registry, validation, five tools); the loop consumes the seam, never edits it.
- `ai_os/gateway.py` — **unchanged**; the loop adds retry *steps*, never new retry *behavior* inside a call.
- `tools/mock_gateway.py` (extended) — scripted multi-step scenarios: fix-then-answer, pivot-after-refusal, parallel calls, never-stop (proves the cap).
- `tools/agent_smoke.py` (new) — offline suite for A1–A8 (mechanical half) + T1–T7 no-regression via `max_steps=1`.
- `docs/concepts/agent-loop.md`, `docs/evaluations/agent-day12.md`.

### Failure

1. **The hidden-state drift.** Somewhere in the loop a `self.plan` or "reasoning cache" appears because "the transcript is long". Any state the model cannot see is state the harness cannot verify — the first recorded failure mode is the temptation, caught by the rule nobody negotiates: state is the transcript.
2. **Stuck-at-one-step.** The loop "works" on A7 (single answer) and the tests pass, but A1 never happens — because the reasoning prompt doesn't invite a second call, or the stub wasn't scripted to keep going. A2/A5 are the guards: if self-correction and the cap never fire in tests, the loop was never actually looping.
3. **Refusal retried as a louder call.** The strongest failure is also the most reproducible: step 1 refuses, step 2 re-issues the identical query "still not found, retry". `observed_refusals` and the self-correction bound only *record* it; the fix is the prompt line "a refusal is a finding, not a retry signal" — and the harness asserting that a refused tool name never repeats.
4. **The parallel-call shortcut.** A step with three `tool_calls` executes the first and summarizes the rest to "save tokens". A8 exists because it is the cheapest bug to commit — every dropped observation is a silenced step the final answer can pretend it saw.
5. **Budget as an afterthought.** The cap is added late, so the first real runs of A1 silently double-triple the bill. The guardrail is named *before* the loop is built (§3's pre-commit) — the ledger per-step row makes the multiply visible the first time it happens, not after a campaign.

### Debugging

Everything Day 12 is `MECH` under the stub: step counting, rejection/re-issue, observation appends, budget clamping, the three exits, and per-step grounding (`cited ⊆ retrieved` per observation) are all deterministic arithmetic over stored schemas, stores, and scripted turns. The debug loop is Day 7's decision tree, re-rooted per step:

- **Step n returned the right symbol but the transcript drops it** → loop ownership (the observation-append or budget clamp dropped a row) — check the step-row, not the model.
- **Step n+1 ignored step n's evidence** → **loop-prompt ownership** (a new Family born today): the model had the thread and lost it; the prompt's "observations are evidence for the *next* step" line is the suspect, and it is editable.
- **The wrong tool at a step** → that step's *description* owns it (Day 11's first suspect, re-rooted).
- **The loop never terminates** → `max_steps`/budget ownership, provable by the never-stop stub scenario; no model behavior is even required to see it.
- **Right tool, wrong result inside the observation** → that step's retrieval owns it (Day 8's job, unchanged).

The trace says which, never the vibes; the per-step row is the unit of attribution, and it has an owner from the day it was designed.

### Evaluation

The §8 per-step tables in `docs/evaluations/agent-day12.md`: per-step tool-selection recall@1 (stub-truth now, real-model later), argument-validity rate per step, refusal handling (A3 must pivot, never restate), grounding-over-transcript (A6: final citations ⊆ union of observations), termination-reason distribution (how often the loop ends by answer vs by cap), and the cost row (steps × per-step bills, worst case `max_steps × 2 + 1`). The numbers a future observer can carry: whether the loop ever dropped an observation (it must not), whether refusal ever repeated (it must not), whether `cited ⊆ ∪obs` held on the transcript (it must), and whether a single-tool answer changed under the loop (it must not — T1…T7 stay green). The `PENDING` line, plainly: whether a real model *composes* — chooses the second tool from the first step's evidence, cites both observations to the end — is Day 21's judgment on Day 12's harness.

### Architecture

The claim of the day is the same claim as Days 10 and 11, one level up: **the system survived a capability change — from "answer in one tool turn" to "act until the transcript can answer" — and stayed small.** The loop is the `else` branch Day 11 deliberately did not take; the registry, stores, and gateway are untouched; money is a config (`max_steps`, self-correction bound) and a ledger, not a new subsystem. State became the transcript (a deletion, not an addition), observations stayed the graded evidence shape (a reuse, not a new type), and grounding became per-step (a generalization, not a new invariant). The reactive loop is a seam, not a destination: Day 13 stress-tests it, Day 14 completes the data tools it already reaches for, Day 16 swaps the decider inside it for the reflective planner, and Day 30's scenarios are already running on it — scenario 4 (customer investigation) and scenario 5 (failing-test diagnosis) are Day 12's A1 skeleton with real resolvers. **Day 12 added a loop, not a subsystem; the capped single turn of Day 11 is what bought the generalization.**

### Questions

- Is `max_steps` the right termination lever, or does a real agent's worth live in *quality-weighted* stopping — a reasoner that stops because the evidence is sufficient, not because the counter hit N? (Day 16 or 21 must decide, on Day 12's per-step evidence.)
- When the loop prompt starts shaping behavior ("use the observation"), at what point has the prompt memorized Day 30's scenarios instead of teaching a general skill — and does the harness have a test for prompt-specialization?
- Does per-step `cited ⊆ retrieved` catch the *important* failure (ignored evidence) or only the *cheapest* one (dropped citation)? When the model cites step 1's symbol while step 2 contradicts it, what does the harness's verdict column say?
- How should the loop bill tool *execution* — the embed is billed, the vector arithmetic is not; does a 3-step investigation hide real ms that Day 13's timeout work will need to see?
- The reactive loop burns a chat per step; the reflective planner (Day 16) would burn one to plan. At which step count does reflection become cheaper — and is that number reachable within Day 30's budget?

### Tomorrow

Day 13 — Agent Failure Modes: the loop's negative space becomes the subject. The taxonomy Day 12 bored: wrong tool selection, invalid arguments, **infinite/oscillating loops**, missing information, **contradictory tool results**, **hallucinated tools**, tool failure. Day 13 builds timeouts, max-iterations hardening, error handling, and fallback behavior on Day 12's loop as-is — the per-step trace and `termination_reason` are the instruments already mounted. Day 12's `observed_refusals`, `gave_up`, and the three exits are the ground Day 13's guards will grow out of; Day 14 completes the data tools the loop already reaches for; and Day 21 rules on whether the composition is worth anything. Day 12 was the day the harness learned to grade a *sequence*; Day 13 is where the sequence learns to defend itself.