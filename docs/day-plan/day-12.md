# Day 12 — Agent Loop

## Objective

Day 11 built the tool layer: schemas the model can read, the five tools, validation-as-contract, refusal-as-a-return-value — and capped it at a **single tool turn** so termination existed before the loop did. Today that cap is lifted, and the single turn becomes a **loop**: user request → reason → choose a tool → execute → observe the result → reason again → choose the next action → final answer. The question a tool turn could answer was one call deep; today's agent answers questions that take *several* calls — a multi-step Saleor investigation whose evidence accumulates in the transcript turn by turn. Everything Day 11 built (registry, validation, refusal, the Day 4 gateway's tool wire) is exercised **as-is**; Day 12 adds only the loop that keeps asking instead of stopping, and the discipline — **state** (the transcript is the memory), **termination** (three legitimate exits, never a runaway), **grounding** (every final claim `cited ⊆ retrieved` across the whole transcript) — that keeps a loop an agent and not a budget fire. The failure *modes* (wrong tool, invalid args, infinite loops, contradictory results, hallucinated tools) are Day 13's taxonomy; Day 12 builds the loop that Day 13 will stress-test.

## Duties Outline

### 1. Learn: Agent State — the Loop's Memory (45 min)

- [ ] Accept the first premise: **agent state is the transcript, nothing else.** The user request, every assistant `tool_calls` turn, every `tool` observation, and the final answer — that list *is* the agent's memory. There is no hidden variable, no scratch state the loop carries silently; if a human can reproduce the run from the transcript, the agent's state is honest.
- [ ] Learn what an **observation** is: a tool result appended to the conversation as a `tool`-role message, in exactly the shape the harness has graded since Day 7 (top-K evidence or `{refused: true}` or `{ok: false, error: "..."}`). An observation is not prose appended after "so I found…"; it is the Day 11 evidence record, now arriving per step.
- [ ] Split the two records the loop keeps: **the transcript** is the mechanism the model reasons over; **the ledger** is the audit of money and steps. Day 4's ledger rows and Day 12's per-step trace rows are different tables — one bills, one debugs. Agent state is the transcript; the ledger is its shadow.
- [ ] Learn why selection repeats Day 11's lesson per step: the model picks a tool by reading the `description` at each step, so Day 11's tool-selection recall@1 is now measured **per step**, and the first suspect on a bad step is still the description — the metadata written for a chooser.
- [ ] Pre-commit the day's claim: a *multi-step* agent whose final answer cites nothing from the accumulated observations is the Day 11 lie ("a tool-using agent that ignores its result") stretched over more turns. Grounding is not per-answer; it is per-`cited ⊆ retrieved` over the whole transcript.

### 2. Learn: Termination — When a Loop May Stop (45 min)

- [ ] Name the three legitimate exits before writing any code: **(1)** the model's answer turn carries no `tool_calls` — it answered from the evidence in hand; **(2)** `max_steps` is reached — the loop stops and says *so*, naming Day 13, instead of inventing a final step; **(3)** the step budget is spent — the ledger's cumulative cost exceeds the run cap, and the loop surfaces what evidence was gathered and that the budget ended it.
- [ ] Understand that termination is a **contract with the user**, not a code detail: every loop that ends must be able to say *why* it ended, and a transcript that dies at `max_steps` is a *failed-agent* transcript, not a quiet special case. The `termination_reason` field in the trace is part of the answer.
- [ ] Learn the Day 13 boundary: an **infinite loop** is a loop that never hits a legitimate exit — the taxonomy for *why* (repeating the same wrong tool, oscillating between two tools) is Day 13's; Day 12's job is to make sure the exits exist and are reachable, so "infinite" is impossible by construction, not by luck.
- [ ] See retries in their two different costumes today: **provider retries** (Day 4's backoff+jitter, `Retry-After`, fallback) happen *inside* one chat call and are invisible to the loop; **loop self-correction** (a rejected tool call re-issued with fixed args) is a *new step*, an observation, a bill — and it is bounded (see §6). Confusing the two is how a loop burns budget repeating "same wrong thing".
- [ ] Pre-commit: a refusal is an observation, not a retry signal. `search_saleor_code` returning `{refused: true}` is evidence the question is out-of-corpus; re-issuing the same call is the Day 11 "refusal fabricated into an answer" bug wearing a loop.

### 3. Design: The Loop (30 min)

- [ ] Write the shape down before any code: `run_agent(request, registry, gateway, *, max_steps, budget_chars, self_correct=…)` — Day 11's `run_tool_answer` with the cap lifted into `max_steps` and the "append observation, ask again" branch replacing the "terminate after one turn" branch. The registry and the stub are exercised **as-is**; a loop is not a reason to touch them.
- [ ] Decide the decision box: each iteration sends the full transcript + tool schemas; if the completion has `tool_calls` and `steps < max_steps` → validate, execute, append observations, loop; if not → final answer. Any `tool_calls` at `max_steps` are *recorded, not executed* — the trace row says `truncated: true`.
- [ ] Design for **parallel calls in a step**: a step may emit several `tool_calls` (Day 11 forbade two; the loop allows them) — each is validated and executed, and *each* becomes its own `tool` observation. Parallelism is context, not magic: the bills are `k` chats' worth of embedding on top of one chat that carried `k` tool requests.
- [ ] Choose where refusal routes: a step whose tools all refused is an observation row (`observed_refusals > 0`) and the *next* reasoning turn sees it — the model may pivot to another tool, ask the user, or answer "not covered". Pivoting is legal; restating is not.
- [ ] Pre-commit the day's budget claim: worst case is **`max_steps` × (1 chat + 1 embed) + 1 final chat** — bounded by config, reported per run, and never exceeded silently.

### 4. Build: The Loop — `run_agent` (1-1.5 hours)

- [ ] Generalize Day 11 honestly: `run_tool_answer` becomes the one-step case of `run_agent(max_steps=1)`. The Day 11 two-turn behavior is a config, not a different program — so T1…T7 stay green by construction and the loop inherits the single-turn tests as its no-regression line.
- [ ] Implement the transcript-as-state: one immutable-growing `messages` list; every step appends `assistant(tool_calls=...)` then one `tool` message per executed call. The final answer appends an `assistant` text turn. No mutation of earlier rows, ever — history is the memory.
- [ ] Record per-step trace rows: `step | tool_calls | each ⇒ {name, args_parsed, args_valid, result_ok, refused, cited, retrieved} | tokens | cost | ledger_ref`. Day 7's trace columns, serialized per step, so a step is as auditable as a Day 7 query.
- [ ] Wire the empty-content learning from Day 11 into the loop's stop check: "no tool_calls in the answer turn" is read off the **already-corrected** completion parser — a plain-text answer turn with empty content is still the Day 6 retryable glitch, not an exit.
- [ ] Extend the stub (`tools/mock_gateway.py`) with scripted **multi-step** scenarios — fix-then-answer, pivot-after-refusal, parallel calls, never-stop (to prove the cap) — so the whole mechanical suite runs offline, deterministically, at zero cost.

### 5. Build: Multi-Step Investigation — Walking Day 10's Map (45 min)

- [ ] Compose the step that Day 11 could not take: "What happens after `CheckoutComplete` submits?" → step 1 `search_saleor_code` for the mutation → step 2 read the chunk's `imports`/`deps` (Day 10 recorded the map) → step 3 `search_saleor_code` for the service it calls → step 4, if the trail reaches a customer/order, `get_order(customer's order)` → final answer citing every step's evidence. **Finding the map was Day 10; walking it is today.**
- [ ] Mix the corpora across steps: docs (`search_saleor_docs`) to ground a concept, code to locate it, (stubbed) data to ground a fact — the Day 30 scenario 4/5 skeleton (customer investigation, failing-test diagnosis) running on Day 12's loop.
- [ ] Record the seam to Day 16: Day 12's loop is **reactive** (decide at each step from the transcript); Day 16's planner-decider is **reflective** (Day 8's decomposition, done by a model before acting). Both loop; the seam is *where the decision is made*. Day 12 ships reactive and names the planner.
- [ ] Keep grounding per-step: the `module#Symbol`/[`doc_id`] citation of step *n*'s answer must be a member of step *n*'s observation — `cited ⊆ retrieved` holds per step, so it holds over the transcript.

### 6. Build: Guardrails — Retry Bounded, Refusal Honest, Budget Real (45 min)

- [ ] Bound self-correction: a rejected call (bad args, unknown tool) may be re-issued at most `self_correct` times per question; beyond that the loop records `gave_up`, appends the last rejection as the observation, and the final answer may *name* the stuck point honestly — Day 11's "rejection is a message" is what makes the re-issue *possible*; the bound is what makes it *affordable*.
- [ ] Never reinterpret refusal: a refused tool is not retried into the budget vault, and a step that was all-refusals does not encourage the final answer to restate. The trace's `observed_refusals` column exists so "pivot" and "fabricate" are distinguishable.
- [ ] Enforce the budget as a run-level cap, not a habit: `budget_chars` clamps every observation (Day 11's `max_chars` discipline) and the ledger's cumulative cost is checked each step. When the cap trips, the loop terminates with `termination_reason: budget` and a final answer limited to *already-gathered* evidence.
- [ ] Keep the model honest about observations: the reasoning-turn prompt states that tool results are evidence, refusal is a finding not an answer, and self-correction should re-issue *fixed* calls, not louder ones. This is the same "write the prompt for the grader" discipline Days 8–11 applied to schemas.

### 7. Test: The Agent Question Set (1 hour)

- [ ] Pre-register the agent fixtures (A1…A8) with expectations written **before** any run:
  - **A1** multi-step investigation: "What happens after `CheckoutComplete` submits?" → ≥2 `search_saleor_code` steps following `imports`; final answer cites symbols from ≥2 observations.
  - **A2** self-correction: stub emits bad args on step 1 → loop re-issues with fixed args on step 2 → answers; trace shows 1 rejection + 1 successful call.
  - **A3** pivot-after-refusal: stub refuses `search_saleor_code` for a docs question → step 2 uses `search_saleor_docs`; no fabricated symbol.
  - **A4** parallel calls: stub emits two `tool_calls` in one step → both execute, both observed, both cited or acknowledged.
  - **A5** termination on `max_steps`: stub stops answering after `max_steps` → trace rows `truncated: true`, `termination_reason: max_steps`, final answer names the limit.
  - **A6** grounding across steps: final answer's citations are a subset of the union of step observations — the multi-turn `cited ⊆ retrieved`.
  - **A7** zero-tool question: "Summarize checkout in one sentence" → no `tool_calls` in the first answer turn → final answer, trace `tool_steps: 0`.
  - **A8** parallel capacity guard: three tool calls in one step → all validated, executed, observed within budget — the loop does not drop observations to save tokens.
- [ ] Split the day honestly, Day 7's way: A2/A3-mechanics/A4/A5/A7/A8 are `MECH` (pure functions of the registry, stores, and scripted stub turns); A1's and A6's *real-model* multi-step choices — will a real model follow an import across two steps *without* being told to, and will it keep citing each step — are `PENDING` until the real gateway. The stub proves the loop carries turns; the model's driving is Day 21's benchmark on Day 12's harness.
- [ ] Run Day 11's T1…T7 unchanged via `max_steps=1` as the no-regression gate: a loop that changes a single-tool answer is a bug wearing an agent.
- [ ] Apply Day 7's isolation to every failure: right symbol missing from an observation → that step's retrieval owns it; symbol present, next step ignored it → the *loop's prompt* owns it (the agent lost the thread); loop never terminates → `max_steps`/budget ownership; wrong tool on a step → that step's description. Say which, never vibes.

### 8. Measure: The Loop as a Contract (45 min)

- [ ] Rule-4 per step, one table per run: `step | expected action | tool chosen | args valid | result ⊆ retrieved | gate correct | cumulative_steps | step_cost | cumulative_cost` — the `tool chosen vs expected action` column is now a **per-step** recall@1, Day 11's metric harvested on every iteration.
- [ ] Record the cost shape honestly: a question costs **`tool_steps` chat turns + `tool_steps` embeddings (one per retrieval step) + 1 final chat**. The loop multiplies Day 11's 3-bill shape by the number of steps — Day 3's warning ("cost multiplies by number of calls") arrives as a config, `max_steps`, and a ledger that bills each step like any call. Worst case is the §3 bound; the run reports the actual.
- [ ] Keep the negatives column with owners: what the loop cannot yet survive (infinite/oscillating loops, contradictory results, hallucinated tools, a wrong-but-valid call) — **Day 13** owns the taxonomy and the guards; what the stubbed data tools cannot provide (real Saleor data) — **Day 14**; whether a real model *drives* multi-step well — **Day 21**. Empty negatives mean the day learned nothing; these have owners.
- [ ] The `PENDING` semantic line: Day 12 proves the harness runs N tool turns end-to-end and enforces grounding on the accumulated transcript; it does not pretend a real model composes a good investigation. The *measure* of that composition is Day 21's; the *container* for it is today's.

### 9. Documentation (20 min)

- [ ] Write `docs/concepts/agent-loop.md` — state-as-transcript, the observation turn, the three terminations, per-step grounding, reactive-vs-reflective seam (Day 16).
- [ ] Write `docs/evaluations/agent-day12.md` — the §8 per-step tables, the A1…A8 pre-registered fixtures, the budget bound, the no-regression lines (T1…T7 plus Days 7–10 fixtures behind the tools).
- [ ] Fill the Day 12 Daily Learning Record (Problem / Concept / Understanding / … / Tomorrow).
- [ ] Record token economics: per-step chat + embedding, `max_chars` clamps per observation, cumulative ledger per run, worst-case `max_steps × 2 + 1` bills.

## Deliverable

The multi-step Saleor investigation agent — a question answered by several real tool calls, each step's evidence in the transcript, the final answer grounded in all of them:

```text
question ("What happens after CheckoutComplete submits?")
     ↓  step 1 — reason → choose
gateway.chat_with_tools(transcript=[user], tools=registry.schemas())
     ↓
model: tool_calls=[search_saleor_code("CheckoutComplete", kind="mutation", module="saleor/graphql/checkout")]
     ↓  validate (registry) → execute (Day 8 retriever over data/code_store.json, gate τ)
observation 1: CheckoutComplete → {imports: [checkout.complete_checkout], deps: [complete_checkout]}
     ↓  step 2 — observe → reason again → choose
model: tool_calls=[search_saleor_code("complete_checkout", kind="function", module="saleor/checkout")]
     ↓  validate → execute (gate τ)
observation 2: complete_checkout(…, order=…) → {deps: [order]}
     ↓  step 3 — reason → enough
model: tool_calls=[]  (answer from the two observations)
     ↓
final: "CheckoutComplete (…) calls complete_checkout (…), which creates the order (…) —
       cited ⊆ retrieved over both steps"   [trace: tool_steps=2, cost = 2 chats+2 embeds+1 chat]
```

Day 11 made the model ask once; Day 12 makes the model ask *until it knows enough to answer*. Termination (answer, `max_steps`, budget) is a config and a trace field; the failure modes that a reactive loop can still hit are Day 13's backlog; the real data the stubbed tools cannot touch is Day 14's; and the judgment that a real model drives multi-step well is Day 21's, standing on the transcript Day 12 made auditable.

## Exit Check — answer in your own words

- What exactly is agent state, and why must it be the transcript and nothing else?
- Name the three legitimate exits from a loop, and what each must say to the user.
- How does Day 11's rejection-as-a-message become Day 12's self-correction — and where is the bound that keeps it affordable?
- What is per-step tool-selection recall@1 measuring, and why is the description still the first suspect?
- A multi-step agent whose final answer cites nothing from its observations — which invariant does it break, and where is it enforced?