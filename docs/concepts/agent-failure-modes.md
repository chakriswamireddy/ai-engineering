# Concept: A Failure Is Handled Only When It Names the Disease and Never Fabricates

> Day 13 of the AI-layer apprenticeship. Day 12 made the loop *terminate* — three
> legitimate exits (`answer`, `max_steps`, budget) plus the narrower `gave_up` — so
> an uncapped spiral is impossible **by construction**. But termination is not
> handling: a loop that stops at `max_steps` because the model repeated the same
> rejected call nine times ended correctly and failed completely. Day 13 turns the
> loop's negative space into the subject: the seven failure modes the curriculum
> names become a **taxonomy with an owner and a guard each**, and the loop gains the
> three mechanisms Day 12 deferred — **a wall-clock timeout** at the tool-call
> boundary, **a state fingerprint** that diagnoses repeat/oscillation *before* the
> cap, and **a bounded, non-fabricating fallback ladder**. Implementation:
> `ai_os/tools.py` (`ToolError`, the `error_kind` taxonomy), `ai_os/agent.py`
> (`execute_bounded`, the fingerprint, the ladder, `_derive_failure_kind`),
> `ai_os/trace.py` (`fingerprint`/`detect_*`/`contradiction_*`), `tools/mock_gateway.py`
> (`tc_d13_*`), `tools/failure_smoke.py` (F1–F9).

## Problem

Day 12's exits were diagnoses in one sense already: `termination_reason: max_steps`
says the loop stopped because it hit a wall. What it cannot say is **why**, and the
why is the difference between three diseases with three cures:

- **repeat** — the identical call, re-issued (`search_saleor_code("checkout")` nine
  times). Cure: the prompt and the rejection message; the model is not reading its
  own history.
- **oscillation** — alternating calls, each legal (`A`, then `B`, then `A`…). Cure:
  the model has two hypotheses and no tie-breaker.
- **slow-but-genuine progress** — every step new, the work simply exceeded the cap.
  Cure: none at this layer (Day 16's planner, or a larger budget).

A generic `max_steps` sends all three to the same place. Worse, the two currencies
Day 12 bounded were **count** and **cost**; neither is **time**. A tool that hangs
blocks the loop with no wall at all, and a slow-but-eventually-successful call can
blow the user's patience while every counter reads green. And a loop handed a
contradiction, a rejection it cannot parse, or a tool name that does not exist had
**no defined behavior** — it either raised or pretended.

The pre-commit, stated so the day can be graded: **for every fixture, the run returns
a typed result, a `failure_kind`, and a `fabricated: false`.** A mode can be detected
and still mishandled — the run that catches a timeout and answers with a plausible
symbol *survived* and *lied*. Handling is not "did not crash"; it is "did not crash
and did not invent".

## The seven modes, one owner each

Written before any code, because a failure mode with no owner is a failure mode
nobody fixes. Five were already *guarded* by Days 11–12 and only need names and
tests; two — **tool failure** and **contradiction** — are genuinely new.

| # | Failure mode | Detected by | Owner / guard |
|---|---|---|---|
| F1 | wrong tool selection | per-step recall@1 (Day 11) | the **schema description** — written for a chooser |
| F2 | invalid arguments | `registry.validate_args` (Day 11) | **validation** + the **self-correction bound** (Day 12) |
| F3 | infinite loop | `max_steps`/budget (Day 12) | **the exits**; Day 13 adds a **repeat diagnosis** |
| F4 | oscillating loop | step **fingerprint** (Day 13) | the **loop's state** — new today |
| F5 | missing information | the Day 7 refusal gate | **refusal-as-a-value** (Day 11) as an observation (Day 12) |
| F6 | contradictory tool results | trace-level **contradiction flag** (Day 13) | **reconciliation** — flagged, not adjudicated (`PENDING`) |
| F7 | hallucinated tools | the registry's unknown-tool check (Day 11, T5) | the **registry** |
| F8 | tool failure | the **tool-call boundary** (Day 13) | the **`ToolError` taxonomy** — new today |
| F9 | timeout | the **wall clock** at the boundary | the **executor** — new today |

Two observations fall out immediately. First, the guards are not new *mechanisms* so
much as **new names on old seams** — which is why the day stays small. Second, the
taxonomy is incomplete without a column: **`fabricated?`**. The three assertions are
independent — the stub proves the first two mechanically; the third is the invariant
the whole day exists to hold.

## Three kinds of "no" — provider, model, tool

The loop lives between three error families, and conflating them is the canonical
agent bug. Day 4 built the first, Day 11 the second, Day 13 the third:

| Family | Example | Caught in | Recovery | Cost |
|---|---|---|---|---|
| **Provider error** (Day 4/7) | 500, 401, `Retry-After`, hang | inside one `chat_with_tools` call | retries, backoff+jitter, ordered fallback | `n` bills, one logical step |
| **Validation rejection** (Day 11) | missing `query`, wrong `kind`, unknown tool, malformed JSON | `registry.validate_args` | self-correction: re-issue with **fixed args** (new step, bounded) | 1 chat, 1 step |
| **Tool error** (Day 13) | timeout, exception, downstream 5xx, bad payload | the **tool-call boundary** | **retry the same call once**, then the **fallback ladder** | 0–1 call + fallback cost |

The distinction that earns the third family its own channel is visible in the
*recovery*, not the surface. Both a schema rejection and a tool error render as
`{ok: false, …}` — the same shape Day 11 established — but a **schema rejection**
means *"you asked wrong"* (the fix is a **different** call), while a **tool error**
means *"you asked right, the world broke"* (the fix is the **same** call again, or a
sibling tool). Re-issuing the identical wrong call is Day 12's "same wrong thing,
louder".

The vocabulary is complete only when it names all three. Day 11's `ToolResult.failure`
was built for schema rejection; Day 13 adds the execution family:

```python
@dataclass
class ToolError:                            # Day 13 — the new family
    error_kind: str                          # "timeout" | "tool_error" | "no_data" | "internal"
    message: str
    retryable: bool
```

In the implementation the family is carried on `ToolResult` itself (`error_kind`,
`retryable`, `fallback_from`), with the constants split into two frozen sets that
encode the recovery rule:

```python
ERROR_INVALID_ARGS = "invalid_args"     # Day 11 — schema rejection (self-correct)
ERROR_UNKNOWN_TOOL = "unknown_tool"     # Day 11 — registry rejection (self-correct)
ERROR_TIMEOUT      = "timeout"          # Day 13 — the wall clock
ERROR_TOOL         = "tool_error"       # Day 13 — the tool raised / downstream 5xx
ERROR_NO_DATA      = "no_data"          # Day 13 — the resolver found nothing
ERROR_INTERNAL     = "internal"         # Day 13 — the harness itself is broken

REJECTION_KINDS  = {ERROR_INVALID_ARGS, ERROR_UNKNOWN_TOOL}    # model fixes it
TOOL_ERROR_KINDS = {ERROR_TIMEOUT, ERROR_TOOL, ERROR_INTERNAL} # the ladder owns it
```

Every one is an **observation**. None is an exception the loop must catch — because a
loop that catches exceptions is how a well-tested run becomes an untested one the
moment a stub is swapped for a real resolver (Day 14). `no_data` is deliberately *not*
a `TOOL_ERROR_KIND`: an empty result is a *finding* to degrade from, not a broken tool
to retry.

### The attribution rule

Day 7's isolation discipline re-roots onto the loop, one owner per symptom — and a
`failure_kind` in the trace *points at a file*:

- **wrong tool** at a step → the step's **description** owns it (`tool_miss`)
- **bad arguments** → **validation** owns it (`invalid_args`)
- **nothing in the corpus** → the **gate** owns it (`refused` — refusal is correct)
- **the tool broke** → the **executor** owns it (`tool_error` / `timeout`)
- **two observations disagree** → **reconciliation** owns it (`contradiction`)
- **the loop is stuck** → the loop's **fingerprint** owns it (`no_progress` / `oscillation`)

### The boundary with Day 4

A **timeout on the chat call** is a provider error — Day 4 already has a request
timeout and retries it. A **timeout on a tool call** is a tool error — Day 13 bounds
it. Same word, two layers, two owners; the trace must record them in different columns
or a debugging session will spend an hour in the wrong file.

## The timeout wall

`max_steps` bounds *count*; the ledger bounds *cost*. Neither bounds **time**. The
rule is narrow and absolute: **measure the wall around `registry.execute`, not around
the step** — because the orchestrator knows *a step* hung; the boundary knows *which
call* hung and with *what args*. `{tool, args, error_kind: "timeout"}` is debuggable;
`{step: 3, slow: true}` is not.

```python
def execute_bounded(registry, name, args, *, tool_timeout_s, clock=time.monotonic):
    t0 = clock()
    try:
        result = registry.execute(name, args)          # validate happened before
    except Exception as exc:                            # executor bug, not model fault
        return ToolResult.tool_error(name, error_kind="tool_error",
                                     message=..., retryable=True), (clock() - t0) * 1000
    elapsed_ms = (clock() - t0) * 1000
    if tool_timeout_s is not None and elapsed_ms > tool_timeout_s * 1000:
        return ToolResult.tool_error(name, error_kind="timeout",
                                     message=..., retryable=True), elapsed_ms
    return result, elapsed_ms
```

Three properties keep it honest:

1. **A timeout is an observation.** The loop receives `{ok: false, error_kind:
   "timeout"}` exactly as it receives a refusal — nothing unwinds, nothing crashes,
   and the transcript stays the memory. Day 11's "rejection is a message" generalized
   to a fourth kind of "no".
2. **The wall is proven, not assumed.** The clock is **injectable** (`clock=
   time.monotonic`), so `elapsed_ms` is asserted bounded with no real sleeping — F9's
   dialect uses a `FakeClock` that reads `0` then `1000`, so the call *measures* a
   1000 s wall deterministically. A timeout that returns *after* the hang returns is a
   lie; the assertion is as much about the clock as about the tool.
3. **Two layers, two vocabularies.** The gateway's provider timeout lives inside
   `chat_with_tools`; the tool timeout lives at the boundary. Reporting both in one
   column re-creates the exact confusion §2 warns about.

## Loop hardening — repeat, oscillation, max-iterations

Day 13's hardening is a **state fingerprint** that separates the first two diseases
from genuine slow progress *before* the cap fires. It is arithmetic over strings the
loop already holds — zero embeds, zero chats:

```python
def fingerprint(call):
    key = json.dumps([call.name, normalized_args(call)], sort_keys=True, default=str)
    return hashlib.sha1(key.encode()).hexdigest()[:12]   # stable across processes

def detect_repeat(signatures):                    # A, A — identical call, adjacent
    return len(signatures) >= 2 and signatures[-1] == signatures[-2]

def detect_oscillation(signatures, window):       # ≤2 distinct calls in a window
    return window >= 2 and len(signatures) >= window and len(set(signatures[-window:])) <= 2
```

The design decisions worth recording:

1. **The fingerprint is layered, never a replacement.** `max_steps` and budget stay
   exactly where Day 12 put them, untouched. A loop with a detector and no backstop is
   one refactor away from infinite; a loop with a backstop and no detector reports
   `max_steps` for every disease. Day 13 needs both, and the trace records both
   (`termination_reason` **and** `failure_kind`).
2. **Normalize args, but do not normalize away the fix.** `normalized_args` JSON-
   canonicalizes key order (whitespace/order is noise) but never the *value*, so
   `kind="mutations"` vs `kind="mutation"` are **different calls** and the second is
   the correction — Day 12's A2 (self-correction) must stay green. F2 and A2 are the
   paired test that proves it.
3. **The fingerprint costs nothing.** It is arithmetic over strings — no embed, no
   chat, no API — and the *saving* is pointed: a repeated-call spiral that used to
   burn `max_steps` steps now stops at step 2. The worst-case `max_steps × 2 + 1` bill
   from Day 12 gets *lower* in exactly the runs that used to be most expensive.
4. **The diagnosis is output.** `no_progress_at_step`, `oscillation_detected`, and
   `distinct_step_fingerprints` are trace fields, because the point of the detector is
   to tell the user *which* disease they have — a loop that stops early and silently is
   only marginally better than one that hits the cap.
5. **The exit is named.** `AGENT_TERMINATIONS` gains `no_progress`, `oscillation`, and
   `tool_failure`, and `TERMINATION_NOTE` gives each a forced-final-turn instruction
   that still forbids fabrication.

The honest limitation: the fingerprint cannot see slow-but-genuine progress, which
still ends at `max_steps` (A5 uses four *distinct* calls precisely so the cap, not the
detector, is under test). That is the seam to Day 16's planner, which decides *before*
acting.

## The fallback ladder

When a tool fails, "handle it" has four meanings; the day orders and bounds them so
the word does not become a synonym for "guess":

```text
tool error
   ↓  rung 1 — retry once, SAME args (retryable only; not for refusal/schema rejection)
   ↓  rung 2 — sibling tool (code tool down → docs tool), only if the intent allows it
   ↓  rung 3 — degrade honestly: answer from already-gathered observations, NAMING the gap
   ↓  rung 4 — stop honestly: termination_reason = "tool_failure", surface the evidence
```

Four rules make it a guard instead of a wish:

1. **Rung 1 is *not* self-correction.** Retrying with the same args is *execution*
   retry (transient failure); re-issuing with fixed args is *self-correction* (Day 12).
   They are counted in different columns (`tool_retries` vs rejections) and bounded by
   different configs (`tool_retries` vs `self_correct`), because a transient 502 and a
   missing `query` are not the same animal.
2. **Rung 2 must *say* it fell back.** The sibling map is `{search_saleor_code ⇄
   search_saleor_docs}`; the substituted result carries `fallback_from`, and the run
   records `fallback_used: [{step, from, to, ok}]`. A silent fallback is a confident
   answer from the wrong corpus — F1's disease through the back door.
3. **Rung 3 never fabricates.** "Degrade" means *use only observations that exist*,
   not *invent the observation you wanted*. The already-gathered evidence is the floor;
   a plausible `module#Symbol` no tool returned is the Day 8 fabrication bug with a
   nicer story.
4. **The ladder is bounded.** `tool_retries` and `fallbacks` are configs with defaults,
   counted per run. An unbounded ladder is an infinite loop in a different costume —
   the same discipline Day 12 applied to self-correction, applied to recovery.

The absolute rule, enforced by the trace's `fabricated` column: **falling back to a
*different real* tool is legal; inventing evidence is not.** The trace separates the
two because `fallback_used` and `observed_failures` are recorded *and* the final
answer's citations are checked against the union of observations.

## Contradiction and missing information

### Missing information

Its guard already exists — the work is making it a *guarded terminal*. An all-refusals
step may be followed by **one** pivot to a sibling tool; if the pivot also refuses, the
final answer is the gate's terse "not covered" and nothing else. Day 7's refusal became
a return value (Day 11) and an observation (Day 12); Day 13 gives it an owner
(`refused`) and a stopping rule. The failure mode it guards: a model handed a refusal
that *answers anyway*, hedging ("Saleor does not appear to have a Shopify handler") —
helpful-sounding fabrication. The trace's `refusal_restated` cell makes it visible.

### Contradiction — flag it, do not adjudicate it

When two observations bear on the same claim with conflicting *values*, the trace
records a `contradiction` row:

```python
{"contradiction": True,
 "field": "order_created_by_checkout",          # what the two disagree about
 "observations": [obs_id_1, obs_id_2],
 "values": [True, False]}
```

Adjudicating *which* is right is semantic and `PENDING` — no mechanical rule can, and
pretending otherwise is the day's most tempting lie. **Detecting and surfacing** it is
`MECH`: `contradiction_rows` groups claims by `field`, dedups by JSON-canonical value,
and fires when a field has more than one distinct value. The answer contract grows a
*completeness* clause: `contradiction_coverage(rows, citations)` requires every
contentious observation to appear in the answer's citations — silently picking a side
is the failure mode.

The distinction the detector must respect: **contradiction ≠ complementarity.** Day
10's split-brain (a `CheckoutComplete` mutation and a `complete_checkout` service both
answering "where is checkout") is two correct hits in two layers, and a detector that
fires on it is a false positive that will train everyone to ignore the column. The
rule is narrow — conflicting **values** for the same claim `field`, not overlapping
**topics** — and F6 carries two negative twins (two sources that agree; the split-brain)
so the guard is proven not trigger-happy.

The `PENDING` line: Day 13 proves the harness *flags* a contradiction and *requires*
both sides cited; whether a real model *resolves* one well, or papers over it
fluently, is Day 21's judgment — like every semantic cell since Day 7.

## `failure_kind` — the taxonomy's payoff

`termination_reason` says *that* the loop stopped; `failure_kind` says *why*. Both are
returned, so "stopped by cap" and "stopped because it was looping" are distinguishable
— the way Day 12 made "answer" distinguishable from `max_steps`. `_derive_failure_kind`
resolves one name from the run's evidence, in priority order:

```text
termination == no_progress            → "no_progress"
termination == oscillation            → "oscillation"
termination == tool_failure           → "timeout" | "tool_error"
gave_up                               → last rejection's error_kind ("unknown_tool" | "invalid_args")
all executed calls refused             → "refused"
a rejection occurred                  → its error_kind
a tool failure occurred               → "timeout" | "tool_error"
a refusal was followed by a different success → "tool_miss"   (§2's wrong-tool attribution)
a contradiction was flagged           → "contradiction"
otherwise                             → None   (a healthy run carries no disease)
```

A healthy run — A5's legitimate cap, Day 12's A1 – A8 — returns `failure_kind: None`
and stays byte-identical. That is the no-regression contract: the guards wrap the loop,
and a hardening layer that changes a passing run is a bug wearing a safety vest.

## Config knobs (Day 13)

| Param | Env | Default | Meaning |
|---|---|---|---|
| `tool_timeout_s` | `AGENT_TOOL_TIMEOUT_S` | `30.0` | the wall around one tool call (time, not money) |
| `repeat_window` | `AGENT_REPEAT_WINDOW` | `4` | the fingerprint's cycle window (`A,B,A,B`) |
| `tool_retries` | `AGENT_TOOL_RETRIES` | `1` | rung 1: same-arg execution retries, per call |
| `fallbacks` | `AGENT_FALLBACKS` | `1` | rung 2: sibling-tool substitutions, per run |

Each is a config with a default and is counted per run, because an unbounded guard is
an infinite loop in costume. The existing Day 12 knobs (`max_steps`, `budget_chars`,
`self_correct`, `budget_usd`, `max_tool_calls_per_step`) are unchanged.

## Cost shape (two currencies, both named)

| Event | Calls | Currency that moved |
|---|---|---|
| a **timeout** | 0 | `elapsed_ms` (a wall, not a bill) |
| the **fingerprint** | 0 | arithmetic over strings |
| a **repeat/oscillation stop** | 0 | *saves* the steps the cap would have burned |
| the **fallback ladder** (per descent) | 1 embed + 1 chat | calls (a sibling tool is a real retrieval + turn) |

The day trades a little resilience-cost for a lot of tail-cost: the worst-case
`max_steps × 2 + 1` from Day 12 gets *lower* in the runs that used to be most
expensive, because the fingerprint stops early.

## Negatives (honest, with owners)

1. **The guards cannot adjudicate a contradiction** — only flag it and require both
   sides. Owner: **Day 21** (semantic).
2. **The stub scripts the failures.** Whether a *real* model spirals (F3/F4),
   hallucinates a tool (F7), or produces contradictory observations (F6) is `PENDING`
   until a real gateway. Owner: **Day 21**. The stub proves the guards *fire*.
3. **The stubbed tool failures cannot represent real latency** — DB timeouts, resolver
   errors. Owner: **Day 14** (the first real resolvers).
4. **The fingerprint cannot catch slow-but-genuine progress** that still ends at the
   cap. Owner: **Day 16** (the reflective planner, which decides *before* burning
   steps).
5. **The fabrication guard is still id-shaped.** Ids are recognised by shape; an
   answer that refers to evidence only in prose is not checked. Recorded, not hidden.

## Re-verify, in one line

```bash
python tools/failure_smoke.py     # F1–F9 + the fabricated? column + A1…A8 / T1…T7 re-run
```

`PENDING`: whether a real model *trips* F1/F5 or contradicts itself, and whether it
*recovers* or papers over — Day 21's judgment on Day 13's defenses. The frozen
invariant is the one column that never varies: `fabricated? = no` on every row.
