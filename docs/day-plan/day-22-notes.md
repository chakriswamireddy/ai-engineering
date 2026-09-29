# Day 22 Notes — Agent Evaluation

> Notes answering `docs/day-plan/day-22.md`.
> Twenty-one days built a machine that *reads* (Days 6–10), *acts* (11–12), *fails honestly* (13), *computes* (14–15), *plans* (16), *remembers* (17–18), *decides its own architecture* (19), *judges its own work* (20), and *measures its own retrieval* (21). Day 21 ran the retriever through the court Day 20 built and produced the RAG benchmark — recall@K, precision@K, MRR, retrieval relevance, the semantic tier reduced to claim-coverage, τ calibrated — all at 0 chats — and ended by handing the same court to "the bench's sibling." The curriculum now says what that means in three words: **"Measure agent behavior."** Its track list is the eight columns Day 20 parked an owner on (**task success, tool selection, tool argument correctness, number of steps, unnecessary calls, failure recovery, cost, latency**), and its deliverable is the **agent benchmark**. Day 22's opening census is its least glamorous and most honest fact: six of the eight columns already exist as trace fields — `run_agent` carries termination, `tool_steps`, per-step `calls` rows with `args_valid`, rejection counts, refusals, the ledger's chats/embeds/tokens/cost, and Day 13's `elapsed_ms` and `tool_timeouts` — while **unnecessary calls have never been defined and failure recovery has never been a rate**, the exact same ratio Day 21 found: the columns everybody could compute stayed hand-quoted, and the two nobody could compute are the two a pre-registered golden set finally makes writable.
> The single idea: **measurement of the agent is only as honest as the call envelope it pre-registered and the trace it reads — so Day 22 mints `necessary_calls` into the golden set to make unnecessary calls arithmetic, defines recovery as progress-after-failure with fabrication as its disqualifier, and grades every column as a pure function of one run dict, never re-executing the run to check it.** The arithmetic columns stay `MECH` (task-success memberships, TSR@1, `args_valid`, step counts, the U1/U2/U3 rules, the recovery rate, cost, latency — all zero new chats); the meaning cells are the semantic residue, parked with owners exactly the way Day 21 parked its own (claim truth re-used from Day 21, support-in-meaning re-used from Day 21, good-recovery and defensible-uncited calls new but owned). And the object itself forces a new honesty the retriever never needed: **a real-model agent run is an outcome, not an identity** — the same golden row can walk different steps on different runs — so the A-report labels each row's world (`stub` vs `real`) and treats a sample as a sample. The day's headline closes the oldest open `PENDING` in the agent line — Day 11's real-model tool choice, Day 12's "does a real model drive multi-step well" — measured on the same golden set the retriever stood on, and the age-old invariable survives translation: the stub proves the **bench**, a real key proves the **verdicts**.
>
> **The one line the day would die defending: a behavioral verdict that cannot re-run has no more authority than the vibe it replaced — and a tool call that added nothing the answer used is a wasted purchase, counted, not felt.**

---

## 1. The Loaded Question — "Measure Agent Behavior"

Day 22's objective is the shortest of the month — three words — and, read the way the apprenticeship reads every adjective, the cruelest: **measure agent behavior.** It does not say "describe an agent run" — the series has been doing that since Day 12, watching stub fixtures and hand-quoting A1–A8. It says *measure*, which is the difference between a report and a benchmark: a report narrates a run; a benchmark *is a fixed ship with a pinned baseline and a reproducibility contract*, so any future change can be asked Rule 4's question in columns instead of anecdotes: did the agent get better, by which metric, on which rows, at what cost, in what wall-clock? Day 21 made the retrieval verdict a machine's output on a fixed golden set; Day 22 makes the *behavior* verdict one.

The census is the day's least glamorous fact, and it determines the build:

- **Task success** — ingredients already exist: `termination_reason` (Day 12's contract), `cited ⊆ retrieved` (Day 6/12), and Day 21's claim-coverage over the run's own transcript (the `claims[]` the answer asserted, the `must_not_assert` that always fails). Not a build — a re-use.
- **Tool selection** — Day 11's §8 wrote the sentence *"the `tool chosen vs expected action` column is now a per-step recall@1"* and every day since quoted it into a report. Day 22 prints it as **TSR@1** (per-step membership in the row's `expected_tools`), with Day 13's F1 attribution attached: a miss is owned by the description the chooser read.
- **Tool argument correctness** — Day 11's registry decides it (`args_valid`, rejection-names-the-field) in every step row; Day 22 adds *payload membership* — an `arg_deviation` (valid shape, wrong payload) — and parks its semantics with the residue lane, because "syntactically valid, semantically wrong" is byte-identical in the trace to "correct."
- **Number of steps** — `tool_steps`, `termination_reason`, `gave_up`. A column, not a build.
- **Unnecessary calls** — genuinely empty. Nobody has ever defined it, because it needs a notion of *necessary* the transcript cannot supply by itself. **The build.**
- **Failure recovery** — recorded and never a rate: rejections, refusals, `error_kind`s, `tool_timeouts` all sat in the trace since Day 13, and nobody turned "the run kept going" into a number. **The build.**
- **Cost** — the ledger's delta, bills since Day 4. A column.
- **Latency** — `elapsed_ms`, `tool_timeouts`; the day's only addition is *attribution* (which step, which tool owned the wall-clock), and the full story of that number is Day 23's seam, named not built.

Six of eight exist. The day is not "build the agent metrics"; it is "print the six, define the two, and let the machine deliver the verdict." The benchmark is a machine that turns every golden row run into one reproducible row of numbers — the same promise Day 21 made about retrieval, now pointed at the thing that acts.

Three invariants hold the day up, all translations: **`judged ⊆ pre-registered`** extended to the new field — `necessary_calls` and the U-rules are written before any run and graded under the content hash, so efficiency cannot be retrodicted (the curve-fit, in call form); **the judge never re-executes** — an agent is the object this series built that *acts*, and the judge's cardinal error would be to run it again to "check" it: a second execution is a second production run, a second bill, and a verdict on a run the judge was never handed (Day 20's rule, aimed at the moving part); and **AB9** — bench off ≡ days-without, because a benchmark that improves a single agent run is a fixer in a lab coat (P9/S9/M8/G9/V9/R9, now on the loop surface).

---

## 2. The Three Columns That Already Lived in the Trace — Task Success, Tool Selection, Argument Correctness

**Task success** is the golden set's oldest promise read across a transcript. Day 20's `expected_answer.claims[] + must_not_assert[]` were built oracle-derivable and Day 21 graded them against the run's own evidence; Day 22 carries that lane across the loop unchanged — the answer is judged by which pre-registered claims it asserted and which forbidden assertions it refused to make. The one genuinely-agile addition is the **termination clause**, and it is the metric the E-family exists to keep honest: an out-of-corpus row succeeds only if the run refused, an answerable row only if it answered. `termination_reason == expected_termination` is `MECH`, written per row, and the five-term vocabulary Day 12 built (`answer`, `max_steps`, `budget`, `gave_up`) is exactly the lexicon that makes a contract a column. Task success is then three memberships — termination right, citations in the transcript, claims covered — each of which the machine can prove and none of which asks a human to read prose.

**Tool selection** is the day's loaded old promise. Day 11 measured recall@1 on a single tool turn and Day 12 harvested it per step into a table; both were hand-read. Day 22 prints **TSR@1** as the fraction of steps whose chosen tool is a member of the row's pre-registered `expected_tools` — membership, not a script, because a correct agent may legally walk a row in more than one order. The two sharp edges are Day 13's: a step choosing a tool *outside* the set is a **tool miss**, a required tool never called is a **tool omission** (`tool_coverage = |expected_tools ∩ called| / |expected_tools|`), and a miss's owner is still the **schema description** the model read before choosing (F1's attribution rule, kept verbatim — the metadata written for a chooser remains the first suspect on a bad step).

**Argument correctness** is Day 11's contract read as a column: `args_valid` is already the rejection verdict — missing required, unknown key, wrong type, wrong enum — and the message names the field, which is precisely what self-correction corrects against (Day 12's A2 proves the loop grows from the message). Day 22's addition is *payload membership*: for each `necessary_calls` entry the golden row pre-registers its expected arguments, and a call that parsed but deviates is an **argument deviation** — flagged, counted, its semantics handed to the residue lane, which owns the same "real but wrong" judgment Day 21's citation-support half already owns in the retrieval line. Correct shape and wrong target is byte-identical in the trace to correct — that is why the semantics are parked, and why the parked cell has an owner rather than a silent pass.

The edge cases are decisions, not gaps: **empty `expected_tools`** (E-family) → TSR@1 is `n/a`, never 0.0, because 0.0 would grade "refused to choose, pass"; **sequence** → order is graded only where the row pre-registers it (the stub's scripted scenarios; free real runs may not hold one); **a deferred call** (Day 11's per-step cap) counts as chosen-not-executed, recorded and never graded as a miss.

---

## 3. The Two Genuinely New Columns — Unnecessary Calls, Failure Recovery, and the Cost of Both

**Unnecessary calls** is the precision of the agent bench, and it was unwritable for the same reason precision@K was: the question "was that call necessary?" needs a notion of *necessary* the trace cannot derive by itself. Day 21 answered its version by making the golden set the relevance oracle; Day 22 answers this one the same way — **the golden set is the necessity oracle.** Each answerable row gains `necessary_calls`: the minimal call set a correct answer must have made, minted pre-run from task structure under the same discipline as `relevant_also`, hash-pinned, rejectable if it leaks. With the envelope in place, an unnecessary call is graded exactly where the trace makes it mechanical:

- **U1 — duplicate fingerprint.** Same tool, normalized args, no Δ — Day 13's `no_progress` detector, promoted from guard to unit.
- **U2 — no-Δ-evidence.** The call's evidence ids all already sit in the union of earlier steps, and it produced no data record. It gathered nothing; whether it should have is beside the point on this rule.
- **U3 — never-cited.** Its evidence is cited by no later step and no final answer. It was purchased and never used.

Efficiency = `1 − unnecessary/total`; `necessary ⊆ made` is call-coverage; and a call that *looks* unnecessary under U3 but plausibly shaped the answer's prose is **`undecided`** — recorded, owned, optionally explained by the flagged lane, never silently added to either column. That last clause is the whole discipline: the agent bench refuses to grade what it cannot prove, the same way Day 21 refused to grade an undeducible claim. A metric that grades "purchased and unused" as a waste is only honest because its *necessary* premise was pre-registered before any run — widening the envelope after the answers leaked is the curve-fit in its call form, caught by the hash exactly as Day 21's hash caught a widened relevance set.

**Failure recovery** is the inverse column, and its definition is as pre-registered as the envelope: a failure event — rejection (Day 11), refusal (Day 7/13), `error_kind` (`timeout`/`tool_error`, Day 13), timeout (`tool_timeout_s`, Day 13) — is **recovered** when a later step **progresses**: the evidence union grows, or the run terminates in a grounded answer. And the day's hard precondition, carried from F1–F9's `fabricated = no` and promoted to a column's gate: **a recovery that fabricates is not a recovery.** Falling back to a different real tool is legal; saving the turn with a plausible symbol no tool returned is the Day-8 fabrication bug with a recovery's uniform off. The report carries `recovery_rate = recovered / failures`, the honest `gave_up` zero, and the *cost* of recovery — the extra steps and tokens a recovery spent — next to the rate, because "recovered in one extra step" and "recovered after burning three steps" are different verdicts wearing the same `recovered` label. The semantic residue — was this recovery a *good* one, a sensible pivot rather than a lucky detour — is parked with the flagged lane, off by default.

**Cost and latency** are the columns the series already owns, priced not built. Cost is the ledger's delta (chats, embeds, tokens, USD — billed per step since Day 4, worst case `max_steps × 2 + 1`), written per row and per world: the stub world bills nothing, the real world bills what it bills, and the report keeps the two rows separate so nobody confuses the price of measurement with the price of being measured. Latency is Day 13's `elapsed_ms` plus `tool_timeouts`, with the day's one addition — **attribution**: which step, and which tool within it, owned the wall-clock. "The agent was slow" is not a verdict until it says who was slow; the *why* of the number — model latency, retrieval latency, a hung tool — is the observability seam Day 23 opens, and Day 22 says so plainly rather than pretending its number is the whole story.

---

## 4. Design: The A-Bench — Day 21's Court, the Agent on the Stand

Day 21's notes called the R-bench "the bench's sibling, on the same golden set." Day 22 is that sibling: the design is deliberately unsurprising because the architecture was settled two days ago — the agent benchmark is **Day 21's harness plus a behavior layer plus a pinned baseline**, not a new loop in the answer path.

```text
   THE GOLDEN SET (six fields + relevant_also + necessary_calls; hash-pinned;
     E-family refusals, judge-hunting F/G/H, adversarial I1/J1/K1) — written before any run
                                │
           ┌────────────────────┴─────────────────────┐
           ▼                                          ▼
  THE AGENT BENCH (AB1–AB8, MECH)             THE RESIDUE (PENDING, owned)
  run_agent on each row, each world           Day 21's claim-coverage re-used for
  (stub script / real store / code store)     task-success meaning · support-in-meaning
  TSR@1 · args · steps · unnecessary          · good-recovery judgment · defensible-
  calls (U1/U2/U3 + necessary ⊆ made)         uncited · LOM-band verdicts — the flagged
  · recovery rate · cost · latency            lane, off by default, never flips MECH
           └────────────────────┬─────────────────────┘
                                ▼
                THE A-REPORT — one row per golden entry:
     AB1 task success · AB2 TSR@1 · AB3 args · AB4 steps · AB5 unnecessary calls
     · AB6 recovery · AB7 cost · AB8 latency · AB9 control (bench off ≡ days-without)
                                ▼
         verdict rows + pinned baselines → Days 23–30 ask "did the agent improve?"
```

The design decisions:

**Decision 1 — six of the eight metrics exist; unnecessary calls and recovery are the build.** TSR@1, args, steps, cost, and latency are pure reads of `run_agent`'s dict. The work is minting `necessary_calls` into the golden YAML under the same oracle discipline as `relevant_also` — pre-registered from task structure, rejectable if it leaks, graded under the hash — and defining the U-rules and the recovery definition *before* any run so efficiency and recovery cannot be retrodicted. Day 21's `relevant_also` discipline, wearing a call ledger.

**Decision 2 — the judge reads the trace and never re-executes the run.** Every column is a pure function of the run dict (`steps`, `tool_ledger`, `ledger`, `termination_reason`, `fabricated`, `elapsed_ms`); the bench adds no chat call anywhere. This is Day 20's "must not re-answer" translated for the object that acts — and the translation is stricter: re-running an agent to check it doesn't just bill a second time, it *changes* what there is to check, because a run is an outcome, not an identity. The only billable cell in the day is the residue lane, off by default, and it never flips a `MECH` verdict.

**Decision 3 — two worlds, one harness.** The **stub world** proves the mechanics: scripted multi-step scenarios make AB2–AB8 reproducible offline at 0 tokens, exactly as A1–A8 and F1–F9 did, with the new adversarial scripts — injected wrong-tool, injected bad-args, refusal-then-pivot, no-progress repeat, hang, deferral — run through the bench before any real key touches it (Rule 3, the hostile rows first). The **real-model world** delivers the verdicts: the same golden rows through the real gateway on the chosen store, the report labeling the world each row came from. The code-store rows (CA1–CE1) run on the stub's mechanism proof until the real Saleor code store lands — Day 10's `PENDING`, the rows written so they re-run the day it does.

**Decision 4 — the A-bench inherits Day 21's real-store debit.** The retriever behind `search_saleor_docs` still leaks (7 of 9 doc rows at top-8 — pinned on Day 9, reproduced on Day 21), so the agent may choose the *right* tool and still lose because the tool's store leaked. The A-report's attribution is Day 7's isolation, carried verbatim: the tool never fetched the evidence → **retrieval debit (Day 8's backlog)**; the tool fetched it and the agent ignored it → **agent/description debit**; the report says which, never a blend. The agent bench is only worth building if it refuses to blur that line — the RAG bench indicted its own retriever on day 21, and the A-bench must not whitewash the same leaks through the tools.

The negatives are named with owners: agent-to-agent hand-off and topology (Day 19's B) stays gated on G1's measured row; observability is Day 23's trace; prompt versioning Day 23's; distributed correlation-id tracing Day 24's; judge poisoning (an agent tuned to pass this grader) stays Day 25's seam; and the fully-golden raw transcript stays *named, not built* — the claims-and-calls form is the replacement, and the A-bench grades exactly that form.

---

## 5. Build: The Agent Bench Core

`ai_os/agent_bench.py` is the metric core, and its discipline is Day 21's verbatim: **every column is a pure function of one run dict.** For each golden row, one `run_agent` on the chosen world yields the dict, and the bench reads everything from it — nothing new asked of the model, nothing re-executed, no seam added that the trace cannot already prove.

The `MECH` columns are mostly passthroughs with a header: **AB1** termination-match (`termination_reason == expected_termination`) plus the Day 21 claim-coverage passthrough over the transcript; **AB2** `tsr_at_1` (per-step membership), `tool_coverage`, `tool_omissions`; **AB3** `args_valid` fraction and `arg_deviations` against the `necessary_calls` payloads; **AB4** `tool_steps` against the `expected_steps` envelope with termination quality; **AB7** the ledger delta's four numbers; **AB8** `elapsed_ms` with per-step and per-tool attribution and `tool_timeouts`.

The new arithmetic is the day's two columns. **AB5** scans the steps for U1 (a fingerprint repeat with no Δ — Day 13's normalized-fingerprint re-used as the detector), U2 (evidence ⊆ the union of earlier steps ∪ no data record), and U3 (evidence never cited by a later step or the answer); it counts `unnecessary/total`, checks `necessary ⊆ made`, and writes the U3-but-plausibly-prose-shaping calls into an `undecided` bucket that is recorded and owned, never counted. **AB6** walks each failure event (rejection/refusal/`error_kind`/timeout) and looks ahead: recovered iff the evidence union grows afterward or the run ends grounded, with `fabricated == false` as the column's gate; it reports `recovery_rate`, `gave_up`, and the recovery's extra-steps and extra-tokens so the price of recovering sits beside the fact of recovering.

The free-tier line holds: the whole `MECH` layer costs **0 chats, 0 embeds** — arithmetic over one dict. The only billable line remains Day 21's flagged residue lane, off by default, and its first run exists to price semantics, not to spend on them.

---

## 6. Build: The Stub World — Scripted Scenarios the Bench Can Grade Offline

`tools/mock_gateway.py` grows the bench's scripted **agent scenarios**, golden-row-driven so the stub world is a fixed ship: the correct-sequence script (the row's own `expected_tools` walked in order), the **injected wrong-tool step** (step 2 chooses docs for a code row — AB2 must count the miss and AB6 must see the pivot), the **injected bad-args step** (AB3 counts the rejection and AB6 sees the self-correction), the **refusal + pivot** script (AB1's E-family case, and the refusal-not-an-unnecessary-call distinction), the **no-progress repeat** script (AB5's U1, Day 13's fingerprint promoted to a measured unit), the **hang script** (AB8's timeout and its tool attribution), and the **deferred-call script** (Day 11's per-step cap — recorded, not graded).

The no-regression law is absolute and re-stated: Day 12's A1–A8 and Day 13's F1–F9 must stay **byte-for-byte green** through the new scenarios, because the stub's additions are additive — a bench that changes a passing loop run is a bug wearing a benchmark. And the day's two new columns are proven offline before any real key touches them, Rule 3's way: the no-progress script must yield `unnecessary_calls = repeat count` with `fabricated = false`, and the refusal-then-pivot must yield `recovery_rate = 1` at zero new chats — the stub is where the arithmetic is shown honest, the real world is where the verdict is earned.

---

## 7. Build: The Real-Model World

`tools/bench_agent.py` is the runner, Day 21's `bench_rag.py` translated: the env-switch discipline (`stub | data/store.json | code store`), `--baseline` pinning Day 12/13's stub behavior (A1–A8, F1–F9) as the floor, and the A-report as its only output — zero human screentime, V7 standing. The golden rows run through the real gateway on the chosen store, and the real-model section of the A-report answers the day's headline — the question Days 11–12 kept parking: **will a real model pick the expected tool per step, correct its arguments, stay inside the pre-registered step envelope, avoid unnecessary calls, and recover?** The row's world is labeled so no one mistakes a mechanism proof for a verdict; the sample is labeled a sample, because a real agent run is an outcome, not an identity, and the report is honest about counting one.

The ledger stays honest: the stub world costs 0 chats, the real world bills exactly what `run_agent` billed, and the cost row sits beside the latency row — Rule 4's "at what cost, with what latency" answered by column. The *story* of a latency number — model vs retrieval vs a hung tool — is written plainly as Day 23's, the seam named rather than faked.

---

## 8. Test: The AB-Fixtures and the Control

Expected behavior written **before** the runs, one fixture per property plus the hostile ones and the control:

| ID | What it proves | Input / injection | Expected |
|---|---|---|---|
| AB1 | task success | `termination_reason == expected_termination` per row; F/G/H through Day 21's re-used lanes | E-family only if refused; F fails coverage, G fails `cited ⊆ retrieved` — **by Day 21's lanes, never re-invented** |
| AB2 | tool selection | each step against `expected_tools` membership; an injected wrong-tool step on a code row | TSR@1 counts the miss; `tool_coverage` counts the omission; the miss's owner is the **description** (Day 13's F1) |
| AB3 | argument correctness | injected bad-args (missing/unknown/type/enum) and an `arg_deviation` (valid shape, wrong payload) | `args_valid` names the field; deviations *flagged*, semantics owned by the residue lane |
| AB4 | number of steps | `tool_steps` vs the pre-registered `expected_steps` envelope; `gave_up`/`max_steps` runs | inside envelope passes; a cap-terminated run is an honest fail, never a silent pass |
| AB5 | unnecessary calls | no-progress repeat, no-Δ-evidence, never-cited scripts; the golden `necessary_calls` envelope | U1/U2/U3 counted; `necessary ⊆ made`; `undecided` recorded with an owner, **never counted** |
| AB6 | failure recovery | rejection→self-correction, refusal→pivot, timeout→retry-or-degrade; a fabricating "recovery" | each yields recovered *progress*; the fabricating one is **not** a recovery (`fabricated == false` is the gate); `gave_up` is the honest zero |
| AB7 | cost | stub world vs real world, per row and per run id | stub = 0 chats/embeds; real = the ledger's delta; never averaged across worlds |
| AB8 | latency | `elapsed_ms` with step/tool attribution; a scripted hang | number reported, tool attributed; `tool_timeouts` distinct from provider retries (Day 13's layered rule); the story is Day 23's |
| AB9 | **control** | the bench installed and unused | A-report byte-identical to the same rows' production traces; no fixture moved — **the bench doesn't fix the agent** |

The `MECH`/`PENDING` split is Day 7's, exact as ever: **AB1's termination half, AB2–AB8's arithmetic, and AB9 are all `MECH`** — pure functions of the run dict and the golden sets, offline, free under the stub, byte-reproducible (the same trace graded twice is the same row). **The `PENDING` cells are the semantic residue** — claim truth (Day 21's lane, re-used, never re-invented), support-in-meaning (Day 21's K-row half), argument-semantics and good-recovery judgment (new, owned by the flagged lane, off by default). The no-regression line runs Day 20's own way: `regress_all.py` with the bench **off** (baseline — the analyzer adds nothing) and **on** (the golden rows' A-report must agree with production traces, AB9); a row the bench cannot reproduce is a **bench bug to fix**, never a fixture to weaken — Day 19's refusal, retargeted at the loop that acts.

---

## 9. Measure: The A-Report and the Price of the Agent

Rule-4 as a table, one row per golden entry, every cell a real run's:

| ID | world | termination | coverage | TSR@1 | tool_cov | args_valid | steps | unnecessary | recovery | cost (chats/$) | latency (ms) | verdict | owner |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A1…C2 | stub | answer | ok | 1.0 | 1.0 | 1.0 | in-env | 0 | — | 0 / $0.00 | … | **pass** | — |
| A1…C2 | real | answer | … | **miss on 2** | … | … | … | **U1 ×1** | … | n chats / $δ | … | **fail → description (Day 13 F1)** | description | 
| B1/B2/D1 | real | answer | … | ok | ok | ok | in-env | 0 | ok | n / $δ | … | **pass, but store leaked** | retrieval (Day 8) | 
| E1/E2/CE1 | real | **refused** | — | n/a | n/a | — | 1 | — | — | 1 / $δ | … | **pass (refused)** | gate → semantic | 
| F/G/H | adversarial | answer | **F fails** | … | … | … | … | … | … | 0 / $0.00 | … | **pass (rejected)** | judge | 
| A-multi | real | **max_steps** | partial | … | … | **miss** | **over-env** | … | **gave_up** | n / $δ | … | **fail → agent** | agent prompt | 
| AB9 | control | — | — | — | — | — | — | — | — | — | — | **bench off ≡ days-without** | bench | 

Three columns carry the day: **the `MECH` verdict on every row** (reproducible — the same trace graded twice is the same row), **the `n/a` cells honored** (E-family TSR@1; undefined ≠ 0.0, because 0.0 would grade "refused to choose, pass"), and **the `PENDING`s with owners** (claim truth, support-in-meaning, good-recovery, defensible-uncited). And the A-report's attribution is the day's whole honesty in one row: an answerable real row whose *store* leaked is a **retrieval debit**, reported as such, while the row that ran four steps to call nothing but its own transcript is an **agent debit** — the bench says which, and it refuses to let the agent's failures hide under the retriever's or the retriever's under the agent's.

The cost rows are honest in the series' ledger: the bench itself is **0 chats, 0 embeds**; the stub world bills nothing; the real world bills its ledger, per row, per run id; and the flagged residue lane bills 1 chat per undecided claim when the account is willing to pay for semantics. The day *prices* both the measurement and the thing measured in separate rows, because Rule 4's "at what cost" has two subjects here and the report refuses to blur them.

---

## 10. Documents

- `docs/concepts/agent-benchmark.md` — what "measure agent behavior" means, the `necessary_calls` envelope that makes unnecessary calls arithmetic (and its honest `undecided`), recovery as progress-after-failure with `fabricated` as its precondition, TSR@1 as Day 11's overdue column, why the judge must never re-execute, and the two-world (stub/real) honesty.
- `docs/evaluations/bench-day22.md` — the §9 A-bench table and the AB1…AB9 fixture table with the AB9 control row, both worlds' rows, the pinned Day-12/13 baselines, the cost rows, the retrieval-debit attribution (Day 9/21's 7-of-9 leaks inherited through the tools), and the `PENDING` line owned by Day 23/24/25.
- PROGRESS rollup: the agent bench's `MECH` layer moves to `DONE` under the stub (AB1–AB8's arithmetic + AB9, verified offline at 0 chats); the *real-model* columns and the semantic residue stay `PARTIAL`/`PENDING` until a real key supplies the verdicts — the same white-court-until-real-key rule that has held every day since Day 6.

---

## 11. Daily Learning Record

### Problem

Twenty days of agent work and two days of court left one object unmeasured: **the agent itself.** Day 11 built the tools and hand-measured one turn of tool selection; Day 12 built the loop and hand-quoted per-step recall@1 into a table; Day 13 built the guardrails and hand-recorded failures — and nobody ever framed a machine that turns a run into a verdict. Day 20 parked the exact eight columns Day 22 now owns (task success, tool selection, tool-argument correctness, number of steps, unnecessary calls, failure recovery, cost, latency) and handed Day 21 the retriever to judge; Day 21 returned the R-bench and explicitly passed the same golden set to "the bench's sibling." Of the eight, six were already trace fields waiting for a header — and **unnecessary calls** had never been defined (it needs a notion of *necessary* only a pre-registered envelope can supply, the agent-twin of Day 21's `relevant_also`), and **failure recovery** had never been a rate (rejections, refusals, `error_kind`s, and timeouts sat in the trace since Day 13, never counted). The object adds a new problem the retriever never posed: a real-model run is an **outcome, not an identity**, so the benchmark must label its world and treat a sample as a sample — and the judge, aimed at something that acts, must never re-execute to check. The deliverable is the **agent benchmark** — with Day 11's oldest `PENDING` (real model tool choice) and Day 12's (does a real model drive multi-step well) both finally owed a measured answer.

### Concept

**Measurement of the agent is only as honest as the call envelope it pre-registered and the trace it reads.** The six columns that existed (task success = termination + `cited ⊆ retrieved` + Day 21's claim-coverage re-used; TSR@1 = Day 11's per-step recall, printed; args = Day 11's registry; steps = `tool_steps`; cost = the ledger; latency = `elapsed_ms`) needed only headers. The two that didn't exist needed premises: **unnecessary calls** became arithmetic via the `necessary_calls` envelope minted into the golden set under the same hash discipline as `relevant_also`, graded by the three U-rules (duplicate fingerprint, no-Δ-evidence, never-cited) with an `undecided` bucket that is owned, never counted; **failure recovery** became a rate via the definition *progress-after-failure, never a fabrication*, with `fabricated == false` as the column's gate and `gave_up` as the honest zero. The judge reads one run dict and never re-executes the run — an agent's second execution is a second production run, a second bill, and a verdict on a run the judge was never handed. The arithmetic is `MECH` and free; the semantic residue (claim truth re-used from Day 21, support-in-meaning re-used, good-recovery and defensible-uncited calls new) is parked with owners in the flagged lane, off by default; the stub proves the bench, a real key proves the verdicts; and AB9 keeps the whole machine invisible — the bench doesn't fix the agent.

### Understanding

The re-readings hit in order: **(1) The day was never "build the agent metrics"** — six of eight existed as trace fields, and Day 12's §8 had been hand-reporting per-step tool-selection recall since before Day 20 built the court; the build was printing the six and defining the two. **(2) Unnecessary calls is the precision of the agent bench, and it was unwritable for the same reason precision@K was** — it needs a notion of *necessary* the trace cannot derive, and the golden set's `necessary_calls` envelope (pre-registered, hash-pinned) is the missing premise that turns a judgment into membership, with the `undecided` bucket doing what Day 21's `n/a` did: refusing to grade what cannot be proved. **(3) "Recovery" had to mean progress, not survival** — "the run kept going" was already recorded; "the run kept going *and learned something*" is a rate, and a recovery that fabricates is the F-series bug wearing a recovery's uniform. **(4) The judge's rule sharpens when the object acts** — not re-answering becomes *never re-executing*, because a run is an outcome, not an identity, and the second execution changes what there is to check. **(5) Day 21's lanes are inherited, not re-invented** — claim truth, citation support, and the adversarial F/G/H rows all grade the same way they did yesterday; the A-bench adds behavior columns, not a second semantics. **(6) The real-store debit carries across the seam** — Day 21 indicted its own retriever (7 of 9 doc rows leaked); the A-bench must reproduce that indictment through the tools rather than let the agent's failures hide under a leak or the leak under the agent. **The day's headline is Day 11's oldest `PENDING`, finally owed an answer: will a real model pick the right tool, correct itself, stay efficient, and recover — and the answer is a number, not a re-read transcript.**

### Implementation

- `ai_os/agent_bench.py` (new) — the metric core: AB1–AB8 as pure functions of one `run_agent` dict; TSR@1/tool-coverage, `args_valid`+deviations, steps vs envelope, U1/U2/U3 + `necessary ⊆ made` with the `undecided` bucket, recovery-rate with the `fabricated` gate, ledger cost, attributed latency.
- `ai_os/golden.yaml` (extends Day 21's) — `necessary_calls` envelopes minted from task structure for the answerable rows; `expected_tools` per row; `expected_steps` and `expected_termination` per row; the honest `n/a` on E-family; nothing widened post-run (the hash).
- `tools/mock_gateway.py` (extends) — the scripted A-bench scenarios: correct-sequence, injected wrong-tool, injected bad-args, refusal+pivot, no-progress, hang, deferred-call.
- `tools/bench_agent.py` (new) — the runner: `stub | real store | code store`, `--baseline` pinning Day-12/13 stub behavior, the A-report as the only output.
- `docs/concepts/agent-benchmark.md`, `docs/evaluations/bench-day22.md`.

### Failure

1. **The judge re-executes to check.** A row fails and the bench "runs it again to see" — a second production run, a second bill, and a verdict on a run nobody graded. Anti-fix: the bench reads the dict it was given; re-running is the harness's job, not the judge's.
2. **Unnecessary calls graded without a necessary premise.** Someone counts "calls I wouldn't have made" and calls it U — a vibe dressed as a column, no hash, wide open to retrodiction. Anti-fix: `necessary_calls` pre-registered under the hash, U1/U2/U3 written before any run, and `undecided` for what cannot be proved.
3. **Recovery counted on survival alone.** The run "kept going," so `recovery_rate` reads 1.0 on a run that burned three steps re-issuing the same rejected call. Anti-fix: recovery means *progress* (evidence-union growth or a grounded answer), and the cost of recovery sits beside the rate.
4. **A recovery that fabricates passes the recovery column.** The loop "recovered" by emitting a plausible symbol no tool returned. Anti-fix: `fabricated == false` is the column's gate — a fabrication is a failure in recovery's uniform.
5. **`undecided` calls leaked into the counts.** Prose-shaping-but-uncited calls get graded U3 to flatter the efficiency number. Anti-fix: recorded, owned, never counted — Day 21's undeducible-claim honesty in call form.
6. **A sample treated as an identity.** One real-model run's verdict is printed as the agent's truth, no world, no run id. Anti-fix: the report labels world and run, and a sample stays a sample.
7. **The bench tunes to the known battleship.** The scripted scenarios become the training set and the U-rules memorize them. Anti-fix: the rotating held-out slice is named, not built (a Day-30 concern), and adversarial rows are rebuilt, not reused.
8. **The retrieval debit whitewashed.** The agent loses because the store leaked, and the report blames the agent anyway (or the agent fails and the report blames the store). Anti-fix: Day 7's isolation as a column — never fetched → retrieval; fetched and ignored → agent; the report says which.
9. **The AB9 offense.** The bench improves the agent — an emboldened prompt, a graded run running hotter than production. Anti-fix: the byte-identical control, the V9/R9 reflex on the loop surface.

### Debugging

Everything Day 22 adds is `MECH` under the stub: columns over one run dict, membership over sets, U-rules over evidence unions, recovery over failure events. The debug loop is Day 7's, re-rooted onto the agent:

- **A row that "regresses"** → check the world and the run id first, not the agent — a real and a stub verdict are different facts wearing the same column header; a rerun of a stochastic agent is a new outcome, not a regression.
- **Efficiency that looks too good** → check the U-detector, then the envelope — did `necessary_calls` shrink after the leaks (the hash catches it), or is a U3-but-prose-shaping call being counted as `undecided` but then quietly added back?
- **Recovery that reads 100%** → check the definition — did a no-progress repeat count as "recovered" (it must not; the evidence union never grew) or did a fabrication pass the gate (`fabricated == false` is the column's precondition)?
- **An answerable row that still fails in the real world** → the store may own it — did the tool ever retrieve the expected evidence (Day 21's 7-of-9 leak), and if so did the agent ignore it? Fetched-and-ignored → the description owns it; never-fetched → Day 8's backlog.
- **A termination verdict that looks wrong** → `termination_reason` is the contract — `answer` for an E-family row is a *fail* (it refused is the pass); make sure the row's `expected_termination` was written before the run, or the fixture owns the bug.
- **An A-report that differs across identical stub runs** → the bench owns it (V2's rule): the stub world is deterministic by script, so any variance is a bug in the bench, not a property of the agent.

The trace says which, never the vibes; the columns are deterministic where they claim to be; and every symptom has an owner from the day it was designed.

### Evaluation

The §9 tables in `docs/evaluations/bench-day22.md` — per golden-row verdicts with termination, coverage, TSR@1, tool coverage, arg validity, steps, unnecessary calls, recovery, cost, latency, owner, and the AB1…AB9 fixture table. The numbers a future observer can carry: how many real-model rows picked the expected tool per step (Day 11's overdue answer), whether the real agent stayed inside the pre-registered step envelope, whether any call was graded unnecessary and whether the `undecided` bucket stayed honest, whether the E-family refusing rows refused (and thereby passed), whether the retrieval-debit attribution reproduced Day 21's 7-of-9 leak through the tools, whether any row's verdict changed when the bench re-ran (never — AB9), and whether any prior fixture moved (never — the regression gate). The `PENDING` line, plainly: claim truth and support-in-meaning are Day 21's lanes, re-used and still owned by their flagged lane; good-recovery and defensible-uncited are Day 22's residue, owned and off by default; observability of the graded pipeline (why that latency, which model, which prompt) is **Day 23's** trace; judge poisoning is **Day 25's** seam; and distributed correlation-id tracing is **Day 24's**.

### Architecture

The claim of the day is the series' claim, one layer further: **the system learned to measure what it *does*, and it did it by making the golden set the necessity oracle and the run's trace the evidence — not by building a new agent.** No new loop exists; the columns read `run_agent`'s dict, unnecessary calls read a set Day 20's discipline already knew how to mint, recovery reads the failure events Day 13's guards already record, and the semantic residue is Day 21's court, re-used rather than rebuilt. The bench is layered the way the court was: `MECH` first (membership, set difference, arithmetic — each a pure function of one trace), semantics parked where the trace cannot decide (claim truth, good-recovery, defensible-uncited, LOM-band), and the paid lane budgeted, off by default, priced before spent. The six columns that always existed are joined by the two that never could: unnecessary calls, whose existence a pre-registered `necessary_calls` envelope finally admits, and failure recovery, whose number a transcript of failures and recoveries finally supports. And the oldest promise in the agent line is finally kept — Day 11 said real model tool choice is "Day 21's, standing on the transcript Day 12 made auditable," Day 21 handed it to the bench's sibling, and Day 22 measured it. The seams are named: observability of the graded pipeline → **Day 23**; distributed tracing → **Day 24**; judge poisoning → **Day 25**; real data resolvers → **Day 14**; the fully-golden raw transcript → *named, not built*. **Day 11 gave the loop its hands; Day 12 its reflex; Day 13 its guards; the days after gave it plans, memory, architecture, and a court; Day 21 put the retriever on the stand; and Day 22 put the agent there — six columns inherited from the trace it always left behind, two minted by the golden set it was always going to need, the semantic residue parked where Day 21 parked its own, AB9 keeping the whole machine invisible — with the loop that acts finally asked, and answered, in numbers instead of anecdotes.**

### Questions

- The `necessary_calls` envelope is minted from task *structure*, like `relevant_also` — but the agent's search queries are free text a correct agent might legally phrase differently. Is the envelope then only enforceable on the *tool-and-arguments* level, and does that silently exempt the genuinely-hard unnecessary case (a wrong-but-in-envelope query that retrieves nothing new)?
- U3 (never-cited) grades calls by the answer's citations — but Day 21's claim-coverage re-reads the answer as *claims*, so evidence that shaped prose without being cited is common in real runs. Is the `undecided` bucket then structurally compulsory — the same answer Day 21 reached about its paid lane — and should the residue lane default *on* for exactly U3, breaking "all semantics off by default" once?
- Recovery requires progress = "the evidence union grows." A pivot that abandons a dead end is correct behavior on zero new evidence if it *stops storing the wrong path* — so does recovery need a negative-Δ formulation (evidence the run *unlearned*) that the union metric cannot see, or is cost-the-extra-steps the honest second column for that case?
- The A-bench grades one real-model sample per row and labels it a sample. Does the honest distribution need N runs per row (price × N), or is the first sample a defensible Row-0 until Day 30's batch? The series has never had to grade a stochastic object's mean before — this is the first day the "single-run" reproducibility story needs a sibling.
- TSR@1 grades per-step membership in `expected_tools` — but a real agent reading descriptions is allowed to be *cleverer* than the golden set (a tool the row never pre-registered, chosen well). Does an excellent out-of-envelope choice get graded a miss forever, and is that the same overfitting-to-the-battleship failure the held-out slice is meant to cure, showing up one day early?

### Tomorrow

Day 23 — AI Observability: the pipeline the benches can now grade, opened from the inside. The curriculum turns *understand what happens inside an AI request* into the trace Day 22's latency row already suspects: **Request → Model → Retrieval → Documents → Tool calls → Tool results → Final answer**, tracked with **latency, tokens, cost, errors, model, prompt version** — the seam Day 22 named, the story its numbers could not yet tell. Days 20–22 built the court and put both the retriever and the agent on the stand; Day 23 builds the observability that says *why* a verdict is what it is instead of just that it is, and delivers the **traceable AI request** — the guarantee that a graded run can be replayed, attributed, and explained at every hop. Day 24 extends the trace across components with correlation IDs, Day 25 attacks it with prompt injection, and the benches Days 21–22 built finally have a black box to look through. **The courts are built, the verdicts are machine-issued, and the black boxes they measure belong to tomorrow.**