# Agent Benchmark — Measuring Behaviour, Not Watching It

> Day 22 of the AI-layer apprenticeship. Twenty-one days built a machine that
> *reads* (6–10), *acts* (11), *drives itself* (12), *survives failure* (13),
> *computes* (14–15), *thinks before acting* (16), *remembers* (17–18), *decides
> its own architecture* (19), *judges its own work* (20), and — since Day 21 —
> *measures its own retrieval* (21). Day 21 ran the retriever through Day 20's
> court and handed the same court to "the bench's sibling." The curriculum names
> that sibling in three words: **"measure agent behaviour."** Its deliverable is
> the thing Days 11–13 built and never once measured as a machine: the **agent
> benchmark.**
> Implementation: `ai_os/agent_bench.py` (the metric core), `ai_os/golden.yaml`
> (the new `necessary_calls` / `expected_steps` / `expected_termination` fields),
> `tools/bench_agent.py` (the runner + the AB1–AB9 fixture set).

The single idea: **measurement of the agent is only as honest as the call
envelope it pre-registered and the trace it reads** — so Day 22 mints
`necessary_calls` into the golden set to make *unnecessary calls* arithmetic,
defines *recovery* as progress-after-failure with fabrication as its
disqualifier, and grades every column as a **pure function of one `run_agent`
dict**, never re-executing the run to check it. An agent is the one object in
this series that *acts*, so its evidence is a transcript of choices, and the
judge's cardinal error would be to run it again: a second execution is a second
production run, a second bill, and a verdict on a run nobody graded.

## 1. The loaded objective — a report describes a run; a benchmark is a ship

The series has "evaluated the agent" since Day 12 — watching stub fixtures and
hand-quoting A1–A8. Day 22's word is **measure**, and the difference is Day 21's:
a **report** narrates a run; a **benchmark** is a fixed ship — fixed rows, fixed
metrics, a pinned baseline, a reproducibility contract — so Days 23–30 can ask
*"did the agent improve?"* and receive columns, not a re-read transcript. The
object forces one new honesty the retriever never needed: **a real agent run is
an outcome, not an identity.** The same golden row run twice can walk different
steps, so the A-report labels each row's **world** (`stub` vs `real`) and treats
a sample as a sample.

The curriculum's eight columns are a census, and the census decides the build:

| column | status before Day 22 | owner |
|---|---|---|
| **Task success** | `termination_reason` + `cited ⊆ retrieved` + Day 21's claim-coverage, all present | Day 12/21 |
| **Tool selection** | Day 11's per-step recall@1, hand-quoted since Day 12 §8 | Day 11/12 |
| **Argument correctness** | Day 11's registry (`args_valid`, names the field) | Day 11 |
| **Number of steps** | `tool_steps` + the exit | Day 12 |
| **Unnecessary calls** | **never defined** — needs a notion of *necessary* | **Day 22** |
| **Failure recovery** | rejections/refusals/`error_kind`s recorded since Day 13, never a rate | **Day 22** |
| **Cost** | the ledger's delta, billed per step since Day 4 | Day 4 |
| **Latency** | Day 13's `elapsed_ms`; the day adds *attribution* | Day 13/22 |

Six of eight exist as trace fields; the day is not "build the agent metrics" but
"print the six, define the two, and let the machine deliver the verdict."

## 2. The three columns that already lived in the trace

**Task success** is three memberships the machine can prove. The golden set's
`expected_answer.claims[] + must_not_assert[]` were built oracle-derivable at Day
20 and Day 21 graded them against the run's own evidence; Day 22 carries the lane
across the loop unchanged — claim-coverage over the transcript, a `must_not` hit
always a fail. The one genuinely-agile addition is the **termination clause**:
`termination_reason == expected_termination` is `MECH`, pre-registered per row,
and the loop's exit vocabulary (`answer`/`max_steps`/`budget`/`gave_up`/…) is
exactly the lexicon that makes Day 12's contract a column. An E-family row still
terminates `answer`; *what* it answers is the refusal, checked separately.

**Tool selection** is the day's loaded old promise, printed at last as **TSR@1** —
the fraction of steps whose chosen tools all lie in the row's pre-registered
`expected_tools`, by **membership, not a script**, because a correct agent may
legally walk a row in more than one order. Two sharp edges, both Day 13's: a step
choosing a tool outside the set is a **tool miss**, a required tool never called
is a **tool omission** (`tool_coverage = |expected_tools ∩ called| / |expected_tools|`),
and a miss's owner is the **schema description** the chooser read (F1's rule).
Where the row pre-registers no tools (E-family), `TSR@1` is `n/a`, never `0.0` —
`0.0` would grade "refused to choose, pass."

**Argument correctness** is Day 11's registry read as a column: `args_valid` is
the rejection verdict (missing/unknown/type/enum) and the message names the field
— the thing self-correction corrects against (A2). Day 22's addition is *payload
membership*: a necessary call may pre-register **structured** args, and a valid
call that deviates is an **`arg_deviation`** — "correct shape, wrong target,"
flagged, its semantics owned by the residue lane. Free-text `query`/`sql` is
deliberately *not* in the envelope: a correct agent may phrase a search
differently, so the envelope is enforceable at the tool-and-argument level — the
plan's own honest limit, stated not hidden.

## 3. Unnecessary calls — the precision of the agent bench

Asking *"was that call necessary?"* needs a notion of *necessary* the transcript
cannot supply — which is why nobody hand-counted it, and why the golden set must
supply the premise. Each answerable row gains **`necessary_calls`** — the minimal
call set a correct answer must have made, minted from task *structure* (never
from a run's output), hash-pinned, rejectable if it leaks. A necessary call is
required, so it must appear in `expected_tools` (the validator enforces it), and
**`necessary ⊆ made`** is call-coverage. With the envelope in place, a call is
graded exactly where the trace makes it mechanical:

- **U1 — duplicate fingerprint, no Δ evidence.** Day 13's `no_progress`
  detector, promoted from guard to unit.
- **U2 — no-Δ evidence, no data record.** The call gathered nothing new.
- **U3 — never-cited.** Its evidence is cited by no later step and no final
  answer. It was purchased and never used.

Only **efficiency-eligible** calls are graded — `args_valid and result_ok and not
refused`. A refusal is the finding that produced a refusal *answer*, never a
wasted purchase; a rejection or an error is a failure the recovery column owns.
Precedence is U1 > U2 > U3 so a call counts once. And a U3 call whose evidence
plausibly shaped the answer's prose — or whose evidence text cannot be resolved
to prove otherwise — is **`undecided`**: recorded, owned, **never counted**. That
last clause is the whole discipline — the bench refuses to grade what it cannot
prove, exactly as Day 21 refused to grade an undeducible claim. Efficiency is
`1 − unnecessary/total`.

## 4. Failure recovery — the inverse column

The trace has recorded every failure since Day 13 — rejections (bad args),
refusals (the gate), `error_kind`s (`timeout`/`tool_error`), timeouts
(`tool_timeout_s`) — and nobody turned "the run kept going" into a number. Day
22's definition is as pre-registered as the envelope: a failure event is
**recovered** when a later step **progresses** (the evidence union grows) or the
run **terminates with a grounded answer**, and the column's gate is carried from
F1–F9's invariant — **a recovery that fabricates is not a recovery**
(`fabricated == false` is the precondition). `gave_up` is the honest zero.

The report carries `recovery_rate = recovered / failures` and the recovery's
**extra steps**, because "recovered in one extra step" and "recovered after
burning three" are different verdicts wearing the same `recovered` label. Whether
a recovery was a *good* pivot rather than a lucky detour is the semantic residue,
parked with the flagged lane, off by default.

## 5. Cost and latency — owned columns, priced not built

**Cost** is the ledger's delta (chats, embeds, tokens, USD — billed per step
since Day 4, worst case `max_steps × 2 + 1`), written per row and per world: the
stub world bills nothing (the in-process scripted gateway), the real world bills
what it bills, and the report keeps the **price of measurement** (the bench, 0
chats) separate from the **price of being measured** (the run) so nobody
confuses them. **Latency** is Day 13's `elapsed_ms` plus the day's one addition —
**attribution**: which step, and which tool within it, owned the wall-clock.
"The agent was slow" is not a verdict until it says who was slow; the *why* of
the number (model vs retrieval vs a hung tool) is **Day 23's** observability seam.

## 6. The A-bench fixtures (AB1–AB9) and the control

The properties of measuring *behaviour*, expectations written before the runs
(`python tools/bench_agent.py --fixtures`), all `MECH` (0 new chats):

| ID | What it proves | Expected |
|---|---|---|
| AB1 | task success | answerable rows answered; E-family only if refused; **F fails coverage, G fails `cited ⊆ retrieved`** — by Day 21's lanes, never re-invented |
| AB2 | tool selection | TSR@1 per step; an injected wrong-tool step counts the miss, owner `description`; a required tool never called is a coverage gap |
| AB3 | argument correctness | an injected bad-args step rejected, the message names the field; a valid-shaped payload deviation is flagged |
| AB4 | number of steps | `tool_steps` inside the envelope; a `gave_up`/`max_steps` run is an **honest fail**, never a quiet pass |
| AB5 | unnecessary calls | U1/U2/U3 each fire once; `necessary ⊆ made`; `undecided` recorded with an owner, **never counted** |
| AB6 | failure recovery | rejection→self-correct, refusal→pivot recover; **gave_up and a fabricating "recovery" are zeros** |
| AB7 | cost | stub = $0; the bench adds no call; the run's chats counted |
| AB8 | latency | per-step/tool attribution; a hung tool counts `tool_timeouts`, distinct from provider retries |
| AB9 | **control** | the bench installed and unused ≡ production, byte-for-byte — **the bench is not a fixer** |

**AB9 is the control** — P9/S9/M8/G9/V9/R9's reflex pointed at the loop that
acts: a bench that changes a single agent run is a fixer in a lab coat. AB1–AB9
are wired into the regression gate (`tools/regress_all.py`, now 18 rows).

## 7. The stub world and the real world

The bench runs in two labelled worlds, one harness:

- **the stub world** — Day 20's in-process scripted gateway, one competent turn
  per golden row: it *proves the mechanics* at **$0** (the AB1–AB9 fixtures run
  here, hostile rows first, Rule 3's way);
- **the real world** (`python tools/bench_agent.py --real`) — the `.env` gateway
  on the real stores: it *delivers the verdicts*. This closes the oldest open
  `PENDING` in the agent line — Day 11's real-model tool choice and Day 12's
  "does a real model drive multi-step well" — as columns. The code rows
  (CA1–CE1) run on the stub's mechanism proof until Day 10's quota-blocked
  `data/code_store.json` lands.

The A-bench **inherits Day 21's real-store debit** and refuses to blend it: an
answerable real row whose store leaked is a **retrieval debit** (Day 8's
backlog); the tool fetched the evidence and the agent ignored it is a
**description/agent debit**. `agent_bench.attribute_failure` computes the first
applicable owner, so a failing row names *where* it failed — the agent bench is
only worth building if it refuses to blur that line.

## Config (env-overridable)

| Key | Default | Meaning |
|---|---|---|
| `AGENT_BENCH_MODE` | `stub` | the world the bench measures (`stub` \| real) |
| `GOLDEN_SET` | `ai_os/golden.yaml` | the pre-registered dataset path |
| `EVAL_JUDGE` | `deterministic` | the judge lane (Day 20/21); off by default |

## What this is not

Stated plainly, because a green table implies more than it proves:

- **The bench's six columns are passthroughs; only two are new.** Unnecessary
  calls and failure recovery are the day's build; the rest were trace fields.
- **`necessary_calls` is itself a judgment** — minted from task structure and
  hash-pinned; it is enforceable only at the tool/argument level (a free-text
  query is never pre-registered).
- **The `undecided` bucket is structurally compulsory** — a call that plausibly
  shaped the prose cannot be proven unnecessary, so the paid residue lane is the
  same answer Day 21 reached about its undeducible claims.
- **A real-model column is a sample, not the truth** — the first row of a
  distribution Day 30's batch may average.
- **Observability** (why that latency, which model, which prompt) is **Day 23's**
  trace; **distributed correlation-id tracing** is **Day 24's**; **judge
  poisoning** (an agent tuned to pass this grader) is **Day 25's** seam.
- **A rotating held-out slice** (the golden set is the fixed battleship) is a
  day-30 concern, *named not built*.

## Re-verify, in one line

```bash
python tools/bench_agent.py --fixtures     # AB1–AB9, 0 new chats
python tools/bench_agent.py --stub         # the §9 A-report on the stub world
python tools/bench_agent.py --real         # the real gateway (the headline)
python tools/regress_all.py                # Rule 2's ratchet, bench included
```

See `docs/evaluations/bench-day22.md` for the §9 A-report and the AB1–AB9
fixture table. The controls that hold the line: **AB9** (the bench changes no
run), **`judged ⊆ pre-registered`** (a verdict only where the envelope supports
one), and **`fabricated = false`** (a recovery that fabricates is not a recovery).
