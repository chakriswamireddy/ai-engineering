                                                                        # Day 13 Notes — Agent Failure Modes

> Notes answering `docs/day-plan/day-13.md`.
> Day 12 built the loop and gave it three exits, so an uncapped spiral became impossible *by construction* — but termination is not handling. A loop that ends at `max_steps` because the model repeated the same rejected call nine times ended correctly and failed completely; a loop whose budget was eaten by a tool that hung for thirty seconds was bounded in money and blind in time; a loop handed a contradiction, a rejection it cannot parse, or a tool name that does not exist had no defined behavior at all. Day 13 turns the loop's negative space into the subject: the seven failure modes the curriculum names — **wrong tool selection, invalid arguments, infinite loops, missing information, contradictory tool results, hallucinated tools, tool failure** — become a **taxonomy with an owner and a guard each**, and the loop gains the three mechanisms Day 12 deferred: **timeouts** (the wall clock money never bounded), **max-iteration hardening** (a repeat/oscillation *diagnosis* that fires before the cap), and **error handling + fallback behavior** (a typed tool-error result and a bounded, non-fabricating fallback ladder).
> The single idea: **a failure is only *handled* when the run (a) does not crash, (b) returns a typed result the model and the trace can both read, and (c) never answers the failure with an invented fact — so the day's headline metric is a column called `fabricated?` that must read `no` on every row.**

---

## 1. The Failure Taxonomy — Seven Modes, One Owner Each

Day 12 ended with a list of things the loop could not survive and assigned them to Day 13. Today that list becomes a **table**, written before any code, because a failure mode with no owner is a failure mode nobody fixes. The seven modes divide cleanly into two groups: five that Day 11–12 already *guarded* and only need names and tests, and two — **tool failure** and **contradiction** — that are genuinely new because nothing in the stack has a vocabulary for them yet.

| # | Failure mode | What it is | Detected by | Owner / guard |
|---|---|---|---|---|
| F1 | wrong tool selection | the model reads a description, picks the wrong corpus (docs for a code question) | per-step recall@1 (Day 11's metric) | the **schema description** — written for a chooser |
| F2 | invalid arguments | missing `query`, wrong `kind` enum, unknown key, malformed JSON | `registry.validate_args` (Day 11) | **validation** + the **self-correction bound** (Day 12) |
| F3 | infinite loop | no legitimate exit is ever taken | `max_steps` / budget (Day 12) | the **exits**; Day 13 adds a **repeat/oscillation diagnosis** |
| F4 | oscillating loop | A→B→A→B: every step legal, the *sequence* is not | step **fingerprint** (Day 13) | the **loop's state** — new today |
| F5 | missing information | the corpus/field cannot answer the question | the Day 7 refusal gate | **refusal-as-a-value** (Day 11) as an observation (Day 12) |
| F6 | contradictory tool results | step 2's evidence conflicts with step 1's | trace-level **contradiction flag** (Day 13) | **reconciliation** — flagged, not adjudicated (`PENDING`) |
| F7 | hallucinated tools | the model emits a tool name that does not exist | the registry's unknown-tool check (Day 11, T5) | the **registry** |
| F8 | tool failure | the tool ran and broke — timeout, exception, downstream 5xx, bad payload | the **tool-call boundary** (Day 13) | the **ToolError taxonomy** — new today |

Two observations fall out of the table immediately. First, the guards are not new *mechanisms* so much as **new names on old seams** — which is exactly the pattern of every day since Day 7, and it is why the day stays small. Second, the taxonomy is missing a column, and the day is incomplete without it: **`fabricated?`**. A mode can be detected and still mishandled — the run that catches a timeout and answers with a plausible symbol *survived* and *lied*. Handling is not "did not crash"; it is "did not crash and did not invent".

The pre-commit, stated so the day can be graded: **for every fixture, the run returns a typed result, a `failure_kind`, and a `fabricated: false`.** The three are independent — the stub proves the first two mechanically, and the third is the invariant the whole day exists to hold.

### What "handled" means, precisely

Day 12's exits were *diagnoses* in one sense already: `termination_reason: max_steps` says the loop stopped because it hit a wall. Day 13's contribution is that the diagnosis becomes *specific*: `failure_kind: no_progress` says **why** it hit the wall. The difference matters because the two failures have different fixes — a model repeating its own rejected call is fixed by the prompt and the rejection message; a model that never got a usable observation is fixed by the tool. A generic `max_steps` sends both to the same place; a `failure_kind` sends each to its owner.

---

## 2. Three Kinds of "No" — Provider, Model, Tool

The loop now lives between three error families, and conflating them is the canonical agent bug. Day 4 built the first, Day 11 built the second, and Day 13 adds the third:

| Family | Example | Where it is caught | Recovery | Cost |
|---|---|---|---|---|
| **Provider error** (Day 4/7) | 500, 401, `Retry-After`, hang | inside one `chat_with_tools` call | retries, backoff+jitter, ordered fallback | `n` bills, one logical step |
| **Validation rejection** (Day 11) | missing `query`, wrong `kind`, unknown tool, malformed JSON | `registry.validate_args` | self-correction: re-issue with **fixed args** (new step, bounded) | 1 chat, 1 step |
| **Tool error** (Day 13) | timeout, exception, downstream 5xx, bad payload | the **tool-call boundary** | **retry the same call once**, then the **fallback ladder** | 0–1 call + fallback cost |

The distinction that earns the third family its own channel is visible in the *recovery*, not the surface. Both a schema rejection and a tool error render as `{ok: false, ...}` — the same shape Day 11 established — but:

- a **schema rejection** means *"you asked wrong"*, and the fix is a **different call**: fixed arguments, a corrected tool name. Re-issuing the identical call is Day 12's "same wrong thing, louder".
- a **tool error** means *"you asked right, the world broke"*, and the fix is the **same call again** (transient) or a **sibling tool** (down). Re-issuing with different args is nonsense; re-issuing with the same args is exactly right once.

So the vocabulary the loop carries is complete only when it names all three:

```python
class ProviderError(Exception): ...          # Day 4 — retried inside the gateway
@dataclass
class ToolResult:                            # Day 11
    ok: bool; args: dict | None; data: Any; error: str | None
    error_kind: str | None = None            # Day 13 extends: "invalid_args" | "unknown_tool"
@dataclass
class ToolError:                             # Day 13 — the new family
    error_kind: str                          # "timeout" | "tool_error" | "no_data" | "internal"
    message: str
    retryable: bool
```

Every one of them is an **observation**. None is an exception the loop must catch — because the loop catching exceptions is how a well-tested run becomes an untested one the moment a stub is swapped for a real resolver (Day 14).

### The attribution rule

Day 7's isolation discipline re-roots cleanly onto the loop, one owner per symptom:

- **wrong tool** at a step → the step's **description** owns it (Day 11's first suspect, re-rooted by Day 12).
- **bad arguments** → **validation** owns it.
- **nothing in the corpus** → the **gate** owns it (refusal is correct behavior, not a bug).
- **the tool broke** → the **executor** owns it (Day 13's new owner).
- **two observations disagree** → **reconciliation** owns it (a flag today; adjudication `PENDING`).
- **the loop is stuck** → the loop's **fingerprint** owns it (Day 13's other new owner).

Two of these owners did not exist before today. The value of naming them is that a `failure_kind` in the trace *points at a file* — the same property Day 7's verdict tree gave the RAG pipeline, now applied to the loop.

### The boundary with Day 4, stated once

A **timeout on the chat call** is a provider error — Day 4 already has a request timeout and retries it. A **timeout on a tool call** is a tool error — Day 13 bounds it. Same word, two layers, two owners; the trace must record them in different columns (`provider_attempts` vs `tool_timeouts`) or a debugging session will spend an hour in the wrong file.

---

## 3. Timeouts — the Wall Day 12 Lacked

Day 12 bounded two currencies and neither was time. `max_steps` bounds **count**; the ledger bounds **cost**. A tool that hangs — Day 4's stub already has a `hang` outcome, and Day 14's database resolvers will hang for real — blocks the loop with **no wall at all**, and a slow-but-eventually-successful call can blow the user's patience while every counter reads green. The time budget is the third currency, and it is the one that most directly maps to a human's experience of the agent.

The rule is narrow and absolute: **measure the wall around `registry.execute`, not around the step.**

```python
def execute_bounded(registry, tc, *, tool_timeout_s, clock=time.monotonic):
    t0 = clock()
    try:
        result = registry.execute(tc.name, tc.arguments)      # validate happened before
    except Exception as exc:                                   # executor bug, not model fault
        return failure(tc.name, error_kind="tool_error", message=str(exc), retryable=True)
    dt = clock() - t0
    if dt > tool_timeout_s:                                    # the wall
        return failure(tc.name, error_kind="timeout", message=f"exceeded {tool_timeout_s}s",
                       retryable=True, elapsed_ms=dt * 1000)
    return result
```

Three properties keep it honest:

1. **A timeout is an observation.** The loop receives `{ok: false, error_kind: "timeout"}` exactly as it receives a refusal — nothing unwinds, nothing crashes, and the transcript stays the memory. This is Day 11's "rejection is a message" generalized to a fourth kind of "no".
2. **The wall is proven, not assumed.** A timeout that returns *after* the hang returns is a lie; the harness asserts `elapsed_ms` is bounded, using an **injected clock** so the mechanical suite is deterministic (no real sleeping in tests). F9's assertion is as much about the clock as about the tool.
3. **Two layers, two vocabularies.** The gateway's provider timeout lives inside `chat_with_tools`; the tool timeout lives at the boundary. Reporting both in one column would re-create the exact confusion §2 warns about.

Why the boundary and not the orchestrator: the orchestrator knows *a step* hung; the boundary knows *which call* hung and with *what args*. The trace wants the call — `{tool, args, error_kind: "timeout"}` is debuggable, `{step: 3, slow: true}` is not. The narrower the wall, the more precise the failure.

---

## 4. Loop Hardening — Repeat, Oscillation, Max-Iterations

`max_steps` reports *that* the loop hit a wall. It does not report *why*, and the "why" is the difference between three diseases with three cures:

- **repeat** — the identical call, re-issued (`search_saleor_code("checkout")` nine times). Cure: the prompt and the rejection message; the model is not reading its own history.
- **oscillation** — alternating calls, each legal (`A`, then `B`, then `A`…). Cure: the model has two hypotheses and no tie-breaker; the description or the evidence is ambiguous.
- **slow-but-genuine progress** — every step is new and the work simply exceeded the cap. Cure: none at this layer; this is Day 16's planner (decide *before* burning steps) or a larger budget.

Day 13's hardening is a **state fingerprint** that separates the first two from the third *before* the cap fires:

```python
def fingerprint(tc):
    return hash((tc.name, json.dumps(tc.arguments, sort_keys=True)))   # normalized args

# in the loop, per step:
sigs.append(fingerprint(tc))
if sigs.count(sigs[-1]) >= 2:                       # identical call re-issued
    failure_kind = "no_progress"; break
if len({sigs[-4:]}) <= 2 and len(sigs) >= 4:        # A,B,A,B within the window
    failure_kind = "oscillation"; break
```

The design decisions worth recording:

1. **The fingerprint is layered, never a replacement.** `max_steps` and budget stay exactly where Day 12 put them, untouched. A loop with a detector and no backstop is one refactor away from infinite; a loop with a backstop and no detector reports `max_steps` for every disease. Day 13 needs both, and the trace records both (`termination_reason` **and** `failure_kind`).
2. **Normalize args, but do not normalize away the fix.** Arg *order* and whitespace are noise and get sorted before hashing; `kind="mutations"` vs `kind="mutation"` are **different calls**, and the second is the correction — so Day 12's A2 (self-correction) must stay green. The fingerprint's job is to catch *identical* calls, and the fix makes a call non-identical by construction. F2 and A2 must coexist.
3. **The fingerprint costs nothing.** It is arithmetic over strings the loop already has — no embed, no chat, no API. The *saving* is real and pointed: a repeated-call spiral that used to burn `max_steps` steps now stops at step 2, so the worst-case `max_steps × 2 + 1` bill from Day 12 gets *lower* in exactly the runs that used to be most expensive.
4. **The diagnosis is output.** `no_progress_at_step`, `oscillation_detected`, and `distinct_step_fingerprints` are trace fields, because the point of the detector is to tell the user *which* disease they have — a loop that stops early and silently is only marginally better than one that hits the cap.

The honest limitation: the fingerprint cannot see slow-but-genuine progress, which still ends at `max_steps`. That is not a bug to fix today — it is the seam to Day 16, where a planner decides *before* acting instead of a reactive loop discovering it cannot stop.

---

## 5. The Fallback Ladder

When a tool fails, "handle it" has four possible meanings and the day must order them and bound them, so that the word does not become a synonym for "guess".

```text
tool error
   ↓  rung 1 — retry once, SAME args (retryable only; not for refusal, not for schema rejection)
   ↓  rung 2 — sibling tool (code tool down → docs tool), only if the question's intent allows it
   ↓  rung 3 — degrade honestly: answer from already-gathered observations, NAMING the gap
   ↓  rung 4 — stop honestly: termination_reason = "tool_failure", surface what evidence existed
```

Four rules make the ladder a guard instead of a wish:

1. **Rung 1 is *not* self-correction.** Retrying with the same args is *execution* retry (transient failure); re-issuing with fixed args is *self-correction* (Day 12). They are counted in different columns and bounded by different configs (`tool_retries` vs `self_correct`), because a transient 502 and a missing `query` are not the same animal and must not share a budget.
2. **Rung 2 must *say* it fell back.** Answering a code question with docs is legal *only* when the question's intent allows it, and the answer must disclose the substitution ("the code index was unavailable; this is from the documentation"). A silent fallback is a confident answer from the wrong corpus — F1's disease arriving through the back door.
3. **Rung 3 never fabricates.** "Degrade" means *use only observations that exist*, not *invent the observation you wanted*. The already-gathered evidence is the floor; a plausible `module#Symbol` that no tool returned is the Day 8 fabrication bug with a nicer story.
4. **The ladder is bounded.** `fallbacks` is a config with a default, counted per run. An unbounded ladder is an infinite loop in a different costume — the same discipline Day 12 applied to self-correction, applied to recovery.

The absolute rule, stated once and enforced by the trace's `fabricated?` column: **falling back to a *different real* tool is legal; inventing evidence is not.** The trace separates the two because `fallback_used` and `observed_failures` are recorded *and* the final answer's citations are checked against the union of observations — an answer that cites a symbol no observation contains is caught, whether or not a fallback happened.

---

## 6. Contradictory Results and Missing Information

These are the two honesty modes, and they are the ones most likely to be "handled" by a shrug.

### Missing information

Its guard already exists — the work today is making it a *guarded terminal* rather than an open branch. An all-refusals step (Day 12's `observed_refusals > 0`) may be followed by **one** pivot to a sibling tool; if the pivot also refuses, the final answer is the gate's terse "not covered" and nothing else. Day 7's refusal became a return value (Day 11) and an observation (Day 12); Day 13 gives it an owner and a stopping rule. The failure mode it guards is Day 8's warning wearing a loop: a model handed a refusal that *answers anyway*, hedging ("Saleor does not appear to have a Shopify handler") — which reads as helpful and is a fabrication with manners. The trace's refusal cell is what makes it visible.

### Contradictory results

This is the genuinely new one, and its design decision is the whole section: **flag it, do not adjudicate it.**

When two observations bear on the same claim with conflicting *values* — the same symbol resolved two ways, two chunks asserting opposite orderings — the trace records a `contradiction` row:

```python
{"contradiction": True,
 "observations": [obs_id_1, obs_id_2],
 "field": "order_created_by_checkout",     # what the two disagree about
 "values": [True, False]}
```

Adjudicating *which* is right is semantic and `PENDING` — no mechanical rule can, and pretending otherwise is the day's most tempting lie. But **detecting and surfacing** it is `MECH` and today's, and it extends the answer contract in a way that is checkable: when a contradiction is flagged, the final answer must cite **both** observations and state the conflict. Silently picking a side is the failure mode; `cited ⊆ retrieved` grows a *completeness* clause — the cited set must **cover** the contentious observations.

The distinction the detector must respect: **contradiction ≠ complementarity.** Day 10's split-brain (a `CheckoutComplete` mutation and a `complete_checkout` service both answering "where is checkout") is two correct hits in two layers, and a detector that fires on it is a false positive that will train everyone to ignore the column. The rule is narrow — conflicting **values** for the same claim, not overlapping **topics** — and the fixtures must include a split-brain non-firing case (F6's negative twin) so the guard is proven not to be trigger-happy.

The `PENDING` line, honestly: Day 13 proves the harness *flags* a contradiction and *requires* both sides cited; whether a real model *resolves* one well, or papers over it fluently, is a semantic judgment — Day 21's, like every semantic cell since Day 7.

---

## 7. Test: The Failure Question Set

Expected behavior written **before** the run, like every day since Day 7. One fixture per curriculum mode, plus the two new mechanisms:

| ID | Mode | Injected (stub) | Expected |
|---|---|---|---|
| F1 | wrong tool selection | model picks `search_saleor_docs` for a code question | miss attributed to **description**; loop **pivots** to the code tool (or records `tool_miss`); no answer from the wrong corpus |
| F2 | invalid arguments | missing `query` / wrong `kind` / unknown key / malformed JSON | `ToolResult.failure` names the field; self-corrects within bound, else `gave_up` |
| F3 | infinite loop | identical call re-emitted forever | **repeat detector** fires (`failure_kind: no_progress`) before `max_steps` |
| F4 | oscillation | alternating tool A / tool B | **cycle detector** fires within `repeat_window`; `failure_kind: oscillation` |
| F5 | missing information | "Debug the Shopify webhook handler" | all tools refuse; one pivot; final answer "not covered"; zero fabricated symbols |
| F6 | contradictory results | step 1 and step 2 return conflicting values | trace flags `contradiction`; final answer cites **both** observation ids |
| F7 | hallucinated tool | stub emits `get_invoice()` | registry unknown-tool rejection; **no** execution attempted; self-correct or `gave_up` |
| F8 | tool failure | tool raises / downstream error | `{error_kind: "tool_error"}` observation; retry-once; fallback ladder if it persists |
| F9 | timeout | stub's `hang` outcome | `tool_timeout_s` fires in bounded wall-clock; `{error_kind: "timeout"}`; run survives; `tool_timeouts` recorded |

The `MECH`/`PENDING` split is exactly Day 7's. **MECH** — F2, F3, F4, F6-*detection*, F7, F8, F9 — are pure functions of the registry, the stub's scripted failures, and an **injected clock**: deterministic, offline, green, and free. **PENDING** — F1's and F5's *real-model* behavior (will a real model pick wrong, will it stop on "not covered") and F6's *adjudication* — need the real gateway, and are Day 21's judgment on Day 13's guards. The stub proves the guards **fire**; a real key proves a real model can **trip** them.

Three obligations, unchanged in spirit:

1. **No regression on Days 11–12.** The guards wrap the loop; a healthy run must be byte-for-byte unchanged. Day 12's A1…A8 and Day 11's T1…T7 run through the *guarded* loop and must stay exactly as green — a hardening layer that changes a passing run is a bug wearing a safety vest.
2. **The neighbors must not collide.** A2 (self-correction) and F2 (invalid args) exercise the same rejection path from different sides — F2 must not break A2, and the fingerprint must treat the *fixed* call as non-identical. A5 (`max_steps` on a legitimate long run) and F3/F4 (early diagnosis) must be distinguishable — F3/F4 fire *before* the cap, A5 fires *at* it. These pairs are the day's sharpest regression risk and get their own rows.
3. **`fabricated?` is a first-class assertion, not a vibe.** Every F-row asserts the final answer's citations ⊆ the union of observations, and that no symbol/doc appears that no tool returned. The column's value is `no` across the table, or the day failed regardless of how many guards fired.

---

## 8. Measure: Failures as a Contract

Rule-4 as a failure table, one row per fixture, every cell a real run's:

| Mode | Injected | Detected? | failure_kind | Guard fired | Recovered? | Fallback used | fabricated? | Owner | elapsed_ms | Cost |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 wrong tool | … | … | `tool_miss` | description → pivot | … | — | **no** | description | … | … |
| F2 bad args | … | … | `invalid_args` | validation + self-correct | … | — | **no** | validation | … | … |
| F3 repeat | … | … | `no_progress` | fingerprint | … | — | **no** | loop state | … | 0 |
| F4 oscillation | … | … | `oscillation` | fingerprint | … | — | **no** | loop state | … | 0 |
| F5 missing info | … | … | `refused` | gate | … | one pivot | **no** | gate | … | … |
| F6 contradiction | … | … | `contradiction` | trace flag | … | — | **no** | reconciliation | … | … |
| F7 hallucinated tool | … | … | `unknown_tool` | registry | … | — | **no** | registry | … | 0 |
| F8 tool failure | … | … | `tool_error` | executor | … | ladder | **no** | executor | … | … |
| F9 timeout | … | … | `timeout` | wall clock | … | ladder | **no** | executor | … | 0 |

The **`fabricated?`** column is the day's headline and the one that never varies: a `yes` anywhere invalidates the run no matter how clean the other columns are. The **`failure_kind`** column is the taxonomy's payoff — the day the trace can name the disease instead of the symptom.

The cost shape, recorded in **two currencies** so neither is hidden: a **timeout costs 0 calls** (a wall, not a bill — `elapsed_ms` is the number that moved); the **fingerprint costs 0 calls** (arithmetic over strings); a **repeat/oscillation stop *saves*** the steps the cap would have burned, so the worst-case `max_steps × 2 + 1` from Day 12 gets *lower* in the runs that used to be most expensive; and the **fallback ladder costs 1 embed + 1 chat** per descent (a sibling tool is a real retrieval plus a real turn). The day trades a little resilience-cost for a lot of tail-cost, and both are named in the trace.

The negatives column, with owners: the guards cannot **adjudicate** a contradiction or **judge** whether a real model spirals (**Day 21**); the stubbed tool failures cannot represent real DB timeouts or resolver errors (**Day 14**); and the fingerprint cannot catch slow-but-genuine progress that still ends at the cap — **Day 16**'s planner, which decides *before* burning steps, is the structural answer. Empty negatives would mean the day learned nothing; these have owners, not wishes.

---

## 9. Documents

- `docs/concepts/agent-failure-modes.md` — the seven-mode taxonomy with owners, the three kinds of "no" (provider / model / tool), the timeout wall at the tool-call boundary, the state fingerprint (repeat vs oscillation vs slow), the fallback ladder, and contradiction-as-a-flag.
- `docs/evaluations/failure-day13.md` — the §8 failure table with the `fabricated? = no` invariant, the F1…F9 pre-registered fixtures (and F6's split-brain negative twin), the `tool_timeout_s` / `repeat_window` / `tool_retries` / `fallbacks` configs and defaults, and the no-regression lines (A1…A8, T1…T7 through the guarded loop).
- PROGRESS rollup: Day 13 mechanics move to `DONE` on the stub (taxonomy, timeout wall, fingerprint, error taxonomy, fallback ladder, contradiction flag verified offline); the real-model cells (does a real agent spiral, hallucinate, or contradict — and does it recover) stay `PARTIAL` until a real gateway run.

---

## 10. Daily Learning Record

### Problem

Day 12's loop could *end* but not *handle*. Its three exits made a runaway impossible and told the user *that* the loop stopped — never *why*, and never what to do about a tool that hung, a name that does not exist, or two observations that disagree. The problem is to give every failure mode the curriculum names an owner and a guard, add the two bounds Day 12 lacked (**time** and a **diagnosis before the cap**), and make the run **never answer a failure with an invention**. Without the guards, a naive loop either crashes on the first real resolver error or — worse — survives by fabricating, which is the Day 8 warning wearing a guardrail's uniform.

### Concept

**A failure is handled only when the run does not crash, returns a typed result the model and the trace can both read, and never fabricates.** The seven curriculum modes split into five that Day 11–12 already guarded (wrong tool → description; bad args → validation + self-correction; infinite → exits + a new fingerprint; hallucinated tool → registry; missing info → the refusal gate) and two that are new: **tool failure** (a third error family — "you asked right, the world broke" — with its own `error_kind` and its own recovery: retry the *same* call, then fall back) and **contradiction** (flagged, not adjudicated — the answer must cite both sides, and adjudication stays `PENDING`). The three error families stay separate because their *recoveries* differ: provider errors retry inside the gateway, schema rejections re-issue with fixed args, tool errors retry the same args then climb the ladder. The loop gains two bounds it lacked — a **wall-clock timeout** at the tool-call boundary (time is the currency `max_steps` and the ledger never saw) and a **state fingerprint** that names repeat vs oscillation vs genuine slow progress *before* `max_steps` fires, layered on top of the Day 12 backstops rather than replacing them. The invariant that makes "handled" mean something is a trace column: `fabricated?` must read `no`.

### Understanding

The unlock is that **Day 13 is almost entirely *naming* and *bounding*, not building.** Five of seven guards already existed as Day 11–12 seams; the day's new mechanisms — the timeout wall, the fingerprint, the fallback ladder — are configurations and arithmetic, not subsystems. The second understanding is the taxonomy's real product: a `failure_kind` turns "the loop stopped" into "the loop stopped **because it repeated its own rejected call**", which points at a *file* by the same discipline Day 7's verdict tree pointed at a layer. The third, the honest one: the mechanical half (guards fire, taxonomy classifies, the wall holds on an injected clock, `fabricated? = no`) is green under the stub, while whether a *real* model ever trips F1/F5 or contradicts itself is `PENDING` until a real gateway — Day 13 builds the defense; whether the defense is *needed* is Day 21's measure. And the day's most dangerous mistake is not a missing guard but a guard that "fixes" a failure by inventing the result it wanted.

### Implementation

- `ai_os/agent.py` (extended) — `execute_bounded` (the timeout wall) and the state-fingerprint check (`no_progress` / `oscillation`) layered on `run_agent`; the fallback ladder in the orchestrator; `failure_kind` returned beside `termination_reason`.
- `ai_os/tools.py` (extended) — `ToolError` taxonomy (`timeout` / `tool_error` / `no_data` / `internal`, `retryable`); the executor returns typed failures, never raises into the loop.
- `ai_os/trace.py` (extended) — failure columns: `failure_kind`, `tool_timeouts`, `no_progress_at_step`, `oscillation_detected`, `distinct_step_fingerprints`, `contradiction`, `fallback_used`, `observed_failures`, `fabricated`.
- `tools/mock_gateway.py` (extended) — scripted failure scenarios: repeat, oscillate, unknown tool, contradictory results, and the existing `hang` outcome re-used for the timeout wall.
- `tools/failure_smoke.py` (new) — offline suite for F1–F9 with an injected clock, plus A1…A8 / T1…T7 no-regression through the guarded loop.
- `docs/concepts/agent-failure-modes.md`, `docs/evaluations/failure-day13.md`.

### Failure

1. **Fabricate-to-survive (the day's cardinal sin).** A timed-out or failed tool, and the agent answers from a plausible guess, because that reads better than "the tool failed". The `fabricated?` column exists because this is the *attractive* bug — the run stays green on every other column. The guard is the citation check against the union of observations, and it is non-negotiable.
2. **The timeout that is not a wall.** The wall is checked *after* `execute` returns, so a hung call blocks the loop anyway and the "timeout" fires only once the hang has already cost the wait. F9's injected-clock assertion is the guard: the wall must hold in *bounded* time, not merely be *recorded*.
3. **The detector that eats self-correction.** An over-eager fingerprint flags the *fixed* call (after a rejection) as a "repeat" and stops the loop the moment the model does the right thing — breaking Day 12's A2. The fingerprint must normalize spelling but never the fix, and F2/A2 are the paired test that proves it.
4. **The silent fallback.** The code tool is down, the agent quietly answers from docs, and the user never learns the corpus changed — F1's disease via the back door. Rung 2 must *disclose* the substitution, and the trace's `fallback_used` column is where the disclosure is checked.
5. **The trigger-happy contradiction flag.** Day 10's split-brain (mutation + service, two correct hits) is flagged as a conflict, the column fills with false positives, and everyone learns to ignore it. F6's negative twin — a split-brain that must *not* fire — is the guard against a detector nobody trusts.

### Debugging

Everything Day 13 adds is `MECH` under the stub: the timeout wall is a clock comparison (injected clock), the fingerprint is string arithmetic, the error taxonomy is a mapping, the fallback ladder is a bounded sequence, and the contradiction flag is a value comparison over recorded observations. The debug loop is Day 7's, re-rooted onto the loop's owners:

- **A step returned the right tool but the run stopped early** → the **fingerprint** owns it (an over-eager repeat/oscillation match) — read `distinct_step_fingerprints`, not the model.
- **The tool hung and the run was slow** → the **wall** owns it — check `elapsed_ms` against `tool_timeout_s`; a timeout recorded after the hang means the check is in the wrong place.
- **The wrong tool at a step** → that step's **description** owns it (Day 11's suspect, re-rooted).
- **Bad args survived validation** → **validation** owns it (a coercion leaked in — the Day 11 suppression bug).
- **Two observations disagree and the answer picked one** → **reconciliation** owns it (the `contradiction` flag did not fire, or the answer ignored it).
- **The tool broke and the answer invented a result** → the **executor + the citation check** own it — this is the cardinal failure, and the trace's `fabricated` column is the proof.

The trace says which, never the vibes; the failure column is the unit of attribution, and it has an owner from the day it was designed.

### Evaluation

The §8 failure table in `docs/evaluations/failure-day13.md`: detection rate per mode (did the guard fire?), `failure_kind` distribution, recovery rate (did the loop self-correct / fall back / stop honestly?), timeout wall-clock vs `tool_timeout_s` (did the wall hold?), and — the headline — `fabricated? = no` on **every** row. The numbers a future observer can carry: how often the fingerprint stopped a spiral before `max_steps` (a saving), how often the fallback ladder descended and whether it disclosed, whether the contradiction flag ever fired on a split-brain (a false positive), and whether any passing run changed under the guards (it must not). The `PENDING` line, plainly: whether a real model *trips* F1/F5 or contradicts itself, and whether it *recovers* or papers over, is Day 21's judgment on Day 13's defenses.

### Architecture

The claim of the day is the same claim as Days 10–12, one level up: **the system survived a capability change — from "a loop that ends" to "a loop that defends itself" — and stayed small.** Five of the seven guards were *renamed* Day 11–12 seams (the registry, the validation bound, the refusal gate, the exits, the description); the two new ones are arithmetic (the fingerprint) and a clock (the wall); the error taxonomy completes the three-family vocabulary without touching the gateway; and the fallback ladder is a bounded sequence in the orchestrator. The registry, the stores, the gateway, and the retriever are **untouched** — a failure-handling layer that modifies the thing it protects is a bug wearing a safety vest. The layering is the point: `max_steps` and budget remain the backstops, the fingerprint is a faster diagnosis on top, and the fallback ladder is recovery *above* both — so Day 16's planner can slot in as a better decider without moving a single guard, and Day 14's real resolvers inherit a boundary that already returns typed failures instead of raising. **Day 13 added defenses, not a subsystem; the seams Day 11–12 drew are what made the defenses cheap.**

### Questions

- Is `fabricated?` the right invariant — a boolean — or does the *degree* of fabrication (one invented symbol vs an entire invented answer) deserve a measure, and would that measure change the guard?
- The fingerprint stops repeat and oscillation but not slow-but-genuine progress; is the honest answer a bigger `max_steps`, or is Day 16's pre-acting planner the only real fix — and can the trace tell the two apart today?
- When a fallback answers a code question from docs, at what point has the agent served the user and at what point has it quietly answered the wrong question — and who decides (the user, the intent classifier, or the schema)?
- Does flagging a contradiction without adjudicating it *help* the user, or just hand them the hard part? Is "cite both sides" an answer or an abdication — and what would a *credit-assigned* verdict look like?
- The timeout wall bounds one tool call; should the loop also bound *total* wall-clock (a per-run time budget), and would that make the `elapsed_ms` column the headline instead of cost?

### Tomorrow

Day 14 — Data Agent: the third corpus stops being a fixture. `get_product/order/customer` have been seeded stubs tagged `pending_resolver: "day14"` since Day 11; Day 14 builds the real resolvers over Saleor's structured data — schema grounding, the read-only access path, and the first place the tool-call boundary (timeout wall, `ToolError`, fallback ladder) meets real latency, real errors, and real data. Day 13's typed tool failures are exactly what a real resolver will produce, and the fallback ladder is what keeps a slow database from stalling the loop. The contradiction flag and the `fabricated?` invariant carry forward because real data is where a wrong-but-plausible answer is most tempting; Day 12's loop and Day 13's guards run as-is, and Day 21 still owns the verdict on whether a real model drives them well. Day 13 was where the loop learned to defend itself; Day 14 is where it first has something real to defend.
