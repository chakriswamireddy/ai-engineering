# Day 13 — Agent Failure Modes

## Objective

Day 12 built the loop and made it *terminate*: three legitimate exits (an answer turn with no `tool_calls`, `max_steps`, budget) so an uncapped spiral is impossible by construction. But **termination is not handling**. A loop that hits `max_steps` because the model re-issued the same wrong call nine times ended correctly and failed totally; a loop that spends its budget around a tool that hung for thirty seconds was bounded in *money* and blind in *time*; a loop handed a rejection it cannot parse, two observations that contradict each other, or a tool name that does not exist has **no defined behavior at all** — it either raises or pretends. Today the loop's negative space becomes the subject: the seven failure modes the curriculum names — **wrong tool selection, invalid arguments, infinite loops, missing information, contradictory tool results, hallucinated tools, tool failure** — become a **taxonomy with an owner and a guard each**, and the loop gains the mechanisms Day 12 deliberately deferred: **timeouts** (the wall clock, the one bound money never gave), **max-iteration hardening** (repeat/oscillation detection that diagnoses *before* the cap fires), and **error handling + fallback behavior** (a typed tool-error result and a bounded, non-fabricating fallback ladder). Everything Day 12 built — transcript-as-state, the three exits, the per-step trace, the self-correction bound — is exercised **as-is**; Day 13 wraps defenses around it and adds the failure column that names what went wrong. The deliverable is the **agent failure-handling layer**: every mode has a deterministic guard, a typed result, and a trace row, and no failure is ever answered by a fabrication.

## Duties Outline

### 1. Learn: The Seven Failure Modes as a Taxonomy (45 min)

- [ ] Hold the Day 12 premise and extend it: Day 12 made failure *reachable and terminating*; Day 13 makes it *named and handled*. An exit that says `max_steps` is a diagnosis; an exit that says `max_steps: repeated the same rejected call 9×` is a treatment plan.
- [ ] Write the seven modes as a table **before building anything** — each with what it is, where it is detected, and who owns the guard:
  - **wrong tool selection** — the model reads a description and picks the wrong corpus (docs for a code question) → detected by per-step recall@1 (Day 11's metric); owner: the **schema description**.
  - **invalid arguments** — Day 11's registry rejection → detected by `validate_args`; owner: **validation** + the **self-correction bound** (Day 12).
  - **infinite loop** — no legitimate exit → detected by `max_steps`/budget (Day 12); owner: **the exits**; Day 13 adds the repeat/oscillation *diagnosis* that fires earlier.
  - **missing information** — the corpus/field cannot answer → the Day 7 refusal gate; owner: **refusal-as-a-value**.
  - **contradictory tool results** — step 2's evidence conflicts with step 1's → detected at the trace level (overlapping claims, conflicting values); owner: **reconciliation** (mostly `PENDING` semantics).
  - **hallucinated tools** — the model emits a tool name that does not exist → detected by the registry's unknown-tool check (Day 11, T5); owner: **the registry**.
  - **tool failure** — the tool itself throws, times out, or returns garbage → detected at the tool-call boundary; owner: **Day 13's error taxonomy** (new today).
- [ ] Learn the split the day must respect: five modes (wrong tool, invalid args, infinite, hallucinated, missing-info) have guards Day 11–12 already built and need only *naming + testing*; **two are genuinely new** — **tool failure** (an exception/timeout the loop has no vocabulary for) and **contradiction** (a semantic condition the harness can flag but not adjudicate).
- [ ] Pre-commit the day's claim: a failure is *handled* only if the run (a) does not crash, (b) returns a **typed** result the model and the trace can read, and (c) **never** answers the failure with an invented fact. A loop that survives by fabricating is the Day 8 warning wearing a guardrail's uniform.
- [ ] Accept the honest boundary up front: whether a *real* model spirals (F3/F4), hallucinates a tool (F7), or produces contradictory observations (F6) is `PENDING` until a real gateway; today proves the guards *fire* on scripted failures and the taxonomy *classifies* them. The stub scripts the failures; the harness proves the defenses.

### 2. Learn: Three Kinds of "No" — Provider, Model, Tool (45 min)

- [ ] Separate the three error families the loop now meets, because confusing them is the classic agent bug:
  1. **Provider errors** (Day 4/7): 5xx, 401, `Retry-After`, hang → handled *inside* one `chat_with_tools` call by retries/backoff/fallback; the loop never sees them. Money-shaped, invisible, already solved.
  2. **Validation rejections** (Day 11): missing `query`, wrong `kind`, malformed JSON, unknown tool → a **message to the model**; the fix is self-correction (a new step, bounded by `self_correct`).
  3. **Tool errors** (Day 13, new): the tool *ran* and failed — a timeout, an exception, a downstream 500, a bad payload. Neither the gateway's job nor the model's fault-as-argument: an **execution failure** that needs its own typed shape and its own fallback.
- [ ] See why the third family needs a new channel: Day 11's `ToolResult.failure(...)` was built for *schema* rejection ("you asked wrong"); a tool error is "you asked right, the world broke". Same `{ok: false}` surface, a different `error_kind`, and a different recovery — schema rejection re-issues *fixed args*; a tool error may retry the *same* args once, then fall back.
- [ ] Learn the attribution rule that keeps the taxonomy honest (Day 7's isolation, re-rooted): **wrong tool → description; bad args → validation; nothing in corpus → refusal; the tool broke → the executor; two observations disagree → reconciliation.** Say which, never vibes — and give each a column in the trace.
- [ ] Pre-commit the taxonomy's boundary with Day 4: a **timeout on the chat call** is a provider error (Day 4 retries it); a **timeout on a tool call** is a tool error (Day 13 handles it). Same word, two layers, two owners — the day must not let one absorb the other.

### 3. Design: The Failure-Handling Layer (30 min)

- [ ] Draw the layer before writing it: the Day 12 loop with a **guard ring** around each tool call and a **failure ledger** beside each step row:
  ```text
  step → [validate] → [execute: timeout wall] → [typed result] → [observe]
                        │                            │
                        ├─ repeat/oscillation detector ┤
                        │                            │
                     failure_kind ───────► fallback ladder (bounded, non-fabricating)
  ```
- [ ] Decide what a **typed failure result** is, in the same shape as Day 11's refusals and rejections: `{ok: false, error_kind: "timeout" | "tool_error" | "invalid_args" | "unknown_tool" | "no_data", error: "<human-readable>", retryable: bool}` — human-readable for the model, machine-readable for the trace.
- [ ] Fix where each guard lives so nothing is implicit: **timeout** at the tool-call boundary (config `tool_timeout_s`); **repeat/oscillation** in the loop's state fingerprint (config `repeat_window`); **error taxonomy** in the tool executor; **fallback** in the orchestrator, bounded by config; **contradiction** in the trace, flagged not adjudicated.
- [ ] Pre-commit the two invariants the layer may not break: **(a)** no guard may fabricate evidence — a timeout, a refusal, a contradiction, or a dead tool never licenses an invented `module#Symbol`/`doc_id`; **(b)** every guard is *observable* — `failure_kind`, `observed_refusals`, `tool_timeouts`, `no_progress`, `oscillation`, `gave_up` are trace fields, not debug prints.
- [ ] Decide the Day 14 seam: the real resolvers (product/order/customer) are where tool failures and timeouts first bite for real; Day 13 ships the *interface* (`error_kind`, timeout config, fallback ladder) and stubs the failures, exactly as Day 11 stubbed the data tools.

### 4. Build: Timeouts — the Wall Day 12 Lacked (1 hour)

- [ ] State the gap plainly: Day 12's `max_steps` and budget bound *count* and *cost*; neither bounds **time**. A tool that hangs (the stub already has a `hang` scenario; Day 14's resolvers will hang for real) blocks the loop with no wall, and a slow-but-successful call can blow the user's patience while every counter reads green.
- [ ] Wrap every tool call in a wall-clock budget: `tool_timeout_s` (config, default small), measured around `registry.execute`, not around the whole step. A call that exceeds it returns `{ok: false, error_kind: "timeout", retryable: true}` — an **observation**, never an exception that unwinds the loop.
- [ ] Keep the Day 4 lesson separate: the gateway's provider timeout is *inside* `chat_with_tools`; the tool timeout is around *execution*. Same wall-clock vocabulary, two layers — and the trace records `tool_timeouts` distinctly from a provider retry storm.
- [ ] Verify against the stub's `hang` outcome: a scripted hanging tool must yield a timeout observation in bounded wall-clock, with the run's `elapsed_ms` and `tool_timeouts` reported — the harness asserts the wall actually stopped it (a timeout that returns after the hang is a lie).
- [ ] Carry the `budget_chars` reflex to time: a per-step time budget as well as a money budget, so a run's worst case is bounded in **both** currencies and both are named in the trace.

### 5. Build: Loop Hardening — Repeat, Oscillation, Max-Iterations (1 hour)

- [ ] Understand what Day 12's `max_steps` does *not* say: it reports *that* the loop hit a wall, not *why*. A model repeating the identical rejected call is a different disease from a model oscillating A→B→A→B, and a different one from slow-but-genuine progress. Day 13's hardening **diagnoses before the cap**.
- [ ] Add the **state fingerprint**: per step, `hash(tool_name, normalized_args, observation_ids)` — cheap, deterministic, model-free. Two identical fingerprints = **no progress**; a short cycle (A,B,A,B within `repeat_window`) = **oscillation**. Both set a `failure_kind` and let the loop stop *early* with a *named* reason instead of silently re-deriving `max_steps`.
- [ ] Keep `max_steps` and budget exactly where Day 12 put them: **unchanged backstops**. The fingerprint is a *diagnosis* layered on top, not a replacement — a loop with a detector and no backstop is one refactor away from infinite; a loop with a backstop and no detector reports `max_steps` for every disease. Day 13 needs both.
- [ ] Decide the fingerprint's whitespace: normalize args before hashing (arg order, whitespace) so "the same call spelled differently" is still a repeat; do **not** normalize away the fix (`kind="mutations"` vs `kind="mutation"` are different calls and the second is the correction — Day 12's A2 must stay green).
- [ ] Record the diagnosis in the trace: `no_progress_at_step`, `oscillation_detected`, `distinct_step_fingerprints`; the run object carries `failure_kind` alongside `termination_reason` so "stopped by cap" and "stopped because it was looping" are distinguishable, the way Day 12 made "answer" distinguishable from "max_steps".

### 6. Build: Error Handling + the Fallback Ladder (1 hour)

- [ ] Give tool execution an **error taxonomy** of its own — the third family from §2 — so the loop's vocabulary is complete: `ProviderError` (Day 4, inside chat), `ToolResult.failure` (Day 11, schema), and `ToolError` (Day 13, execution): `{error_kind: "timeout" | "tool_error" | "no_data" | "internal", retryable: bool}`. Every one is an observation; none is an exception the loop must catch.
- [ ] Build the **fallback ladder** — ordered, bounded, and each rung *honest*:
  1. **Retry once** on a `retryable` tool error (timeout, transient downstream) with the *same* args — not self-correction (args unchanged), execution retry, counted separately.
  2. **Fall back to a sibling tool** when a corpus tool is down: `search_saleor_code` failing → `search_saleor_docs` (and the reverse) — but only when the question's *intent* justifies it; a fallback that answers a code question with docs must *say* it did.
  3. **Degrade honestly**: answer strictly from already-gathered observations, naming the gap ("the code index was unavailable; this is from the docs").
  4. **Stop honestly**: `termination_reason: tool_failure` when nothing can proceed, surfacing whatever evidence existed.
- [ ] Enforce the ladder's one absolute rule: **a failed tool never licenses an invented result.** Falling back to a *different real* tool is legal; saving the turn with a plausible `module#Symbol` that no tool returned is the Day 8 fabrication bug with a nicer story. The trace's `fallback_used` and `observed_failures` columns separate "the agent degraded" from "the agent lied".
- [ ] Keep the ladder **bounded** by the same reflex Day 12 used for self-correction: `tool_retries` and `fallbacks` are configs with defaults, counted per run; an unbounded fallback ladder is an infinite loop in a different costume.

### 7. Build: Contradictory Results and Missing Information (45 min)

- [ ] Handle **missing information** first because its guard already exists: an all-refusals step is Day 12's `observed_refusals`; the loop may pivot **once** to a sibling tool, and if the pivot also refuses, the final answer is the gate's terse "not covered" — *never* a manufactured match. Day 7's refusal became a return value (Day 11) and an observation (Day 12); Day 13 makes it a *guarded* terminal with a named owner.
- [ ] Handle **contradiction** as a *flag*, not a verdict: when two observations bear on the same claim with conflicting values (the same symbol resolved two ways, two chunks asserting opposite orderings), the trace records a `contradiction` row (the two observation ids + the conflicting field). Adjudicating *which* is right is semantic and `PENDING`; **detecting and surfacing** it is `MECH` and today's.
- [ ] Make the answer contract require the surface: when a contradiction is flagged, the final answer must cite **both** observations and state the conflict — silently picking one side is the failure mode, and the trace can now see whether both sources appear. This is `cited ⊆ retrieved` grown a *completeness* clause: the cited set must cover the contentious observations.
- [ ] Distinguish contradiction from *complementarity*: Day 10's split-brain (a mutation and a service both answer "where is checkout") is two correct hits, not a conflict — the detector must not fire on agreement-in-different-layers. Encode the distinction in the flag's rule (conflicting *values*, not overlapping *topics*).
- [ ] Write the honest `PENDING` line: Day 13 proves the harness *flags* a contradiction and *requires* both sides cited; it does not prove a real model *resolves* one well. That judgment, like every semantic cell since Day 7, is Day 21's.

### 8. Test: The Failure Question Set (1 hour)

- [ ] Pre-register the failure fixtures (F1…F9) with expectations written **before** any run — one per curriculum mode, plus the two new mechanisms:
  - **F1** wrong tool selection: stub has the model pick `search_saleor_docs` for a code question → the miss is attributed to the *description* and the loop **pivots** to the code tool on the next step (or the run reports `tool_miss`); no answer fabricated from the wrong corpus.
  - **F2** invalid arguments: stub emits missing `query` / wrong `kind` / unknown key / malformed JSON → Day 11 rejection names the field; the loop self-corrects within the Day 12 bound, else `gave_up`.
  - **F3** infinite loop: stub re-emits the identical call forever → the **repeat detector** fires (`no_progress`) before `max_steps`; `failure_kind: no_progress`.
  - **F4** oscillation: stub alternates tool A / tool B → the cycle detector fires within `repeat_window`; `failure_kind: oscillation`.
  - **F5** missing information: "Debug the Shopify webhook handler" → every tool refuses; one pivot allowed; final answer is "not covered", zero fabricated symbols.
  - **F6** contradictory results: step 1 and step 2 return conflicting values for the same claim → trace flags `contradiction`; final answer cites **both** observation ids.
  - **F7** hallucinated tool: stub emits `get_invoice()` → registry unknown-tool rejection; the loop self-corrects or `gave_up`; **no** execution is attempted.
  - **F8** tool failure: the tool raises / returns a downstream error → `{ok: false, error_kind: "tool_error"}` observation; retry-once on the retryable path; if it persists, the fallback ladder runs.
  - **F9** timeout: stub's `hang` scenario → `tool_timeout_s` fires in bounded wall-clock; `{error_kind: "timeout"}` observation; run does not crash; `tool_timeouts` recorded; `elapsed_ms` proves the wall held.
- [ ] Split the day honestly, Day 7's way: **F2/F3/F4/F6-detection/F7/F8/F9 are `MECH`** — pure functions of the registry, the stub's scripted failures, and the wall clock (with an injected clock for determinism). **F1's and F5's real-model behavior** (will a real model pick wrong, will it stop on "not covered") and **F6's adjudication** are `PENDING` until a real gateway. The stub proves the guards *fire*; a real key proves a real model can *trip* them.
- [ ] Re-run Day 12's A1…A8 and Day 11's T1…T7 as the no-regression gate: the guards are *around* the loop, so a healthy multi-step investigation must be byte-for-byte unchanged — a hardening layer that changes a passing run is a bug wearing a safety vest. Day 12's A2 (self-correction) and A5 (`max_steps`) are the *neighbors* of F2–F4: F2 must not break A2, and F3/F4 must not preempt A5's legitimate cap.
- [ ] Apply Day 7's isolation to a failure: wrong tool → description; bad args → validation; refusal → gate; tool broke → executor; contradiction → reconciliation; loop stuck → the loop's fingerprint. One owner per failure, named in the trace, never vibes.

### 9. Measure + Documentation: Failures as a Contract (45 min + 20 min)

- [ ] Rule-4 as a failure table, one row per fixture, every cell a real run's: `failure mode | injected (stub) | detected? | failure_kind | guard fired | recovered? | fallback used | fabricated? | owner | elapsed_ms | cost` — the **`fabricated?`** column is the day's headline: it must read `no` for every row, forever.
- [ ] Record the two new costs honestly: a timeout bounds *time* (report `elapsed_ms` and `tool_timeouts`), the fingerprint costs *nothing* (arithmetic over strings), and the fallback ladder costs *calls* when it runs (a sibling tool is another embed + a chat turn). Put the fallback's per-run bill beside the timeout's wall-clock saving so both currencies are visible.
- [ ] Keep the negatives column with owners: what Day 13's guards cannot do (adjudicate a contradiction, judge whether a real model spirals — **Day 21**), what the stubbed tool failures cannot represent (real DB timeouts, real resolver errors — **Day 14**), and what the fingerprint cannot catch (slow-but-genuine progress that still ends at the cap — **Day 16**'s planner, which would decide *before* burning steps).
- [ ] Write `docs/concepts/agent-failure-modes.md` — the seven-mode taxonomy, the three kinds of "no", the timeout wall, the fingerprint, the fallback ladder, contradiction-flag-not-adjudicate.
- [ ] Write `docs/evaluations/failure-day13.md` — the §8 failure table, the F1…F9 pre-registered fixtures, the `fabricated? = no` invariant, the timeout/repeat/fallback configs and their defaults, and the no-regression lines (A1…A8 through the guarded loop).
- [ ] Fill the Day 13 Daily Learning Record (Problem / Concept / Understanding / … / Tomorrow).
- [ ] Record token economics: a tool timeout costs 0 calls (a wall, not a bill); a repeat/oscillation stop *saves* the steps the cap would have burned; the fallback ladder costs 1 embed + 1 chat when it descends — and the worst case `max_steps × 2 + 1` is now *lower* because the fingerprint stops early.

## Deliverable

The agent failure-handling layer — seven modes, seven guards, one trace column each, and no fabrication:

```text
question
   ↓   ┌──────────────────── Day 12 loop (unchanged) ─────────────────────┐
   │   │  reason → choose tool → validate → execute → observe → reason…    │
   │   └───────────────────────────────┬───────────────────────────────────┘
   │                                   │  each tool call wrapped:
   │             ┌─────────────────────┴─────────────────────┐
   │             │  validate (Day 11)  → schema rejection     │  "you asked wrong"
   │             │  execute  (Day 13)  → ToolError taxonomy   │  "you asked right, world broke"
   │             │     ├─ timeout wall  (tool_timeout_s)      │  → {error_kind:"timeout"}
   │             │     └─ exception/5xx → {error_kind:"tool_error"}
   │             │  observe             → top-K | refused | failure
   │             └─────────────────────┬─────────────────────┘
   │                                   │
   │        fingerprint: no_progress / oscillation ──► stop early, named
   │                                   │
   │        fallback ladder: retry → sibling tool → degrade → stop   (bounded, never fabricates)
   ▼
 run: {termination_reason, failure_kind, tool_timeouts, observed_refusals,
       no_progress_at_step, oscillation_detected, contradiction: [ids], fabricated: false}
```

Day 12 made the loop *end*; Day 13 makes it *defensible*. Every curriculum failure mode now has a guard, a typed result, and a trace row — and the one number the whole day exists to hold at zero is `fabricated`.

## Exit Check — answer in your own words

- What is the difference between a loop that *terminates* and a loop that *handles* — and which of Day 12's three exits was already a diagnosis?
- Why does the third error family (tool errors) need its own typed shape, and how does it differ from a Day 11 validation rejection in *recovery*?
- What does the time budget bound that `max_steps` and the money budget cannot — and why must the tool timeout live at the tool-call boundary, not inside the gateway?
- Why is repeat/oscillation detection layered *on top of* `max_steps` rather than replacing it?
- A tool times out and the agent answers anyway. Which invariant broke, which trace column catches it, and why is "fall back to a *sibling* tool" legal while "invent a symbol" is not?
