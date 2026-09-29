# Evaluation Fundamentals — Stop Judging Manually, Not Judging

> Day 20 of the AI-layer apprenticeship. Nineteen days built a machine that
> *reads* (6–10), *acts* (11), *drives itself* (12), *survives failure* (13),
> *computes* (14), *defends itself* (15), *thinks before acting* (16),
> *remembers what it did* (17), *carries a lesson — only if the lesson earns it*
> (18), and *decides its own architecture* (19). Every one of those days was
> judged **by hand** — a human built the fixture, read the table, and said
> "green." Day 20's objective is the smallest sentence of the month: **"stop
> judging AI quality manually."** Its deliverable is the thing every day since
> Day 7 kept promising a future day would build: **the first evaluation
> harness.**
> Implementation: `ai_os/eval_dataset.py` (the five-field, hash-pinned golden
> row), `ai_os/golden.yaml` (the pre-registered set), `tools/eval_harness.py`
> (the router + the V1–V9 evaluation set), `ai_os/judge.py` (the layered
> grader), `tools/regress_all.py` (the Rule-2 regression gate).

The single idea: **judgment is not banned — *manual* judgment is.** A manual
grade is unrepeatable (the reader's attention moved), unweighted (one reader, one
reading), and unavailable to the system as a contract (nothing in the repo knew
what "good" was supposed to mean). Day 20 writes "good" down **before** the run —
in five fields, in YAML, in code — and builds a judge that grades the **trace**,
never re-answers the question, and only ever issues a verdict the pre-registered
expectation supports. The day's whole invariant is four words: **`judged ⊆
pre-registered`.**

## 1. The loaded objective — what "manually" bans

Read the curriculum the way the apprenticeship reads every adjective: it does
**not** say "stop judging AI." Judgment is owed — Rule 4 asks *how much better,
by which metric, on which dataset, at what cost, with what latency, what failure
cases remain* — but for nineteen days the judge answering Rule 4 has been a human
eyeball, and an eyeball is the one grader that cannot be **re-run**.

The four learn items are the pillars:

- **Golden datasets** — a fixed, pre-registered set of questions, each carrying
  its expected evidence, expected answer, expected tools, and failure category.
  The "battleship": every run is tested against the same ship, and an improvement
  is only real if it shows up as a diff on that ship.
- **Regression testing** — Rule 2's ratchet, made mechanical. The series has
  never allowed "better" to mean "different" (Day 8's "the same questions keep
  green", Day 9's `ΣΔMRR` with 0 leaks, Day 10's code 6/6 + doc nine 9/9). Day 20
  promotes that habit into one command.
- **Deterministic evaluation** — grades computed by set-membership and arithmetic
  over the trace: run-to-run identical, single-run (no sampling), free, and
  *self-proving* — a deterministic verdict carries its own reproducibility in its
  method.
- **LLM-as-judge limitations** — the constraining admission. A stochastic model
  grading a stochastic answer cannot issue a reproducible verdict, is
  fluency-bait, drifts across runs and providers, can be poisoned, and costs a
  chat per cell the free tier already answered.

## 2. The golden dataset is a hypothesis ledger, not a test-suite dump

Look at what Day 7 already stored per question: `predicted` (the hypothesis) and
`fix` (the layer that owns the failure if the hypothesis survives). A golden
entry is a **bet against the system** — "this question needs these docs, this
answer, these tools; if it fails, this layer owns the loss." Running the set is
scoring the bets: a failing row does not just say *no*, it says *which layer's
hypothesis died*.

The five fields, in the curriculum's order:

1. **Question** — the natural-language input, verbatim (`questions.yaml`,
   `code_questions.yaml`; cross-checked so the golden set can add fields but
   never rewrite what it inherits).
2. **Expected evidence** — the doc_ids / `module#Symbol` sets the corpus must
   surface. Day 7's law stands: pre-registered from corpus *structure*, never
   from retrieval output.
3. **Expected answer** — the field the series deliberately **refused to
   hand-write**.
4. **Expected tools** — the tool(s) the answer needs, from Day 11's registry /
   Day 19's role table.
5. **Failure category** — the expected failure shape if the entry is *supposed*
   to fail (out-of-corpus → refusal, ambiguous → wrong-entity, …), from Day 7's
   verdict tree and Day 13's `failure_kind` taxonomy.

### The expected answer is `claims[] + must_not_assert[]`, and it must be oracle-derivable

Authoring the model's voice by hand is the seductive form of the manual judgment
the day bans. So the expected answer is not a model-voice paragraph; it is the
**minimal set of facts the answer must assert** (`claims[]`) and the assertions
it must avoid (`must_not_assert[]`). And it is **oracle-derivable**: every
discriminative term of a claim must appear in the row's expected evidence, or the
row is **malformed** and the validator rejects it. A golden row whose "expected
answer" cannot be derived from its expected evidence is not an answer — it is a
**hope**. This is the offline, token-grounded analogue of Day 18's `authored ⊆
grounded`, and it keeps the set honest at the seam where most evaluation datasets
quietly rot: expectations written from the *hope of the answer* rather than the
*structure of the evidence*.

## 3. Pre-registration is the line between a report and curve-fitting

Day 7's `questions.yaml` said it in a header comment; Day 20 turns it into
`assert hash == pinned_hash`.

- The golden YAML is the **commitment**: the five fields, written before any run.
- Each row carries `registered_at`; the dataset carries a **content hash** over
  every graded field (evidence, answer, tools, failure category, judge-hunting
  probes).
- The harness **refuses to grade under a stale hash**. A row whose
  `expected_evidence` is patched after the run came up short, or whose
  `expected_answer` is typed after seeing the output, fails **V1/V8** — an
  integrity failure — before a single verdict exists.
- A run that started *before* its row's registration is **flagged** (V8): the
  expectation cannot have been written in response to an output that did not yet
  exist (`ai_os.eval_dataset.rows_registered_before`).

## 4. The judge grades the trace and never re-answers

Every honest number the series keeps already lives in the trace
(`ai_os/trace.py`): retrieved top-K, expected-set membership, recall@K, MRR,
citations, the fabrication flag, the failure kind, the ledger. So the judge
**reads that** — it never runs the model again. A judge that re-answers is a
second production call: it doubles the bill, re-bakes sampling noise into the
"grade," and — worst — grades a run the trace never saw. "The run that was
judged" must be identical to the run that happened, or the verdict is about an
answer nobody gave.

The judge is **layered**, determinism first:

```text
        the golden dataset (five fields, pre-registered, hash-pinned)
                              │
             ┌────────────────┴─────────────────┐
             ▼                                  ▼
   THE HARNESS (a router, not a grader)   THE REGRESSION GATE (tool/regress_all.py)
   presents each question to production   replays the fixture families; exactly as green
             │                                  │
             └────────────────┬─────────────────┘
                              ▼
              THE JUDGE — built to not re-answer:
  layer 1  deterministic (MECH, 0 chats): expected ⊆ retrieved · cited ⊆ retrieved
           · conclusion ⊆ evidence · tool == expected_tools · must_not · refusal == expected
           · failure == category · cost · latency
  layer 2  semantic — a SLOT, PENDING by default (Day 21 RAG / Day 22 agent)
  layer 3  LLM-as-judge — flagged, budgeted, OFF; explains, never decides
                              ▼
                    verdict rows → the eval report → Day 21/22's benchmarks
```

**Layer 1** is every check that is set-membership or arithmetic over the trace —
`expected ⊆ retrieved` (+ recall@K / MRR, Day 8/9's metrics now issued by a
machine), `cited ⊆ retrieved` (Day 6), `conclusion ⊆ evidence` (Day 16's
invariant, read as *claim coverage* + `must_not_assert` avoidance), `tool ==
expected_tools` (Day 11/19), `must_not` avoidance, refusal correctness (the
E-family), failure-category match, cost and latency from the ledger. Every
failing check names **one owner** (Day 7's isolation map): evidence miss →
retrieval; citation leak → generation; wrong tool → routing; refusal miss →
gate/τ.

**Layer 2** is the semantic *slot* — citation correctness in meaning, answer
relevance, groundedness beyond set membership — declared `PENDING` with owners
(Day 21's RAG benchmark; Day 22 for behavior). Parking is a feature, not a stall:
the court is exactly as good as the verdicts it will eventually bear.

**Layer 3** is the optional model lane behind `EVAL_JUDGE`. It is wired and
**off**. It writes only to the `semantic:` column, and it **may explain a
verdict, never decide one**: `deterministic_verdict` is only ever set by layer 1,
and a "model flipped a pass to fail" is a `judged ⊆ pre-registered` violation the
harness flags red (V4).

## 5. `judged ⊆ pre-registered` — held by construction

The day's headline invariant is not a slogan; it is a property of the code. A
deterministic check reads **only its own pre-registered expectation**; when that
expectation is absent the check is reported `n/a` (never failed). A check with no
expectation to read — `expected_tools` empty, `failure_category` null, no
`claims` — cannot produce a verdict, because a verdict where the pre-registration
was silent is not a verdict. The V1 fixture then proves the other direction: a
row patched after registration is refused **before grading**.

## 6. Regression, determinism, and the honest limits of LLM-as-judge

**Regression testing** is Rule 2's ratchet in one command
(`tools/regress_all.py`): the retrieval fixture families (the doc nine A1…E2 and
the code six CA1…CE1, walked through the Day-8/9 retriever) plus every day suite
(`test_day7.py` … `test_day10.py`, `tools/*_smoke.py` — T1–T7 · A1–A8 · F1–F9 ·
Q1–Q8 · X1–X11 · P1–P9 · S1–S9 · M1–M9 · G1–G9) plus the golden rows, all required
to come back *exactly as green*. The split must not blur: **improvement is
measured on the golden set; preservation is measured on the regression gate.**
One without the other is not a Rule-4 result — an improvement that rolls back a
green row is a regression, and a regression-free change that moves no golden row
is a rearranged deck, not an improvement.

**Deterministic evaluation** is the day's *default* judge, and the operative word
is default. Determinism is not the limit of honest evaluation; it is the bedrock
under whatever semantic floor the series eventually pays for.

**LLM-as-judge limitations** are the constraining half of the day, named with
fixtures' worth of weight:

1. **Judge fabrication** — the model asserts "the answer omitted X" where X is
   neither a pre-registered expectation nor in the answer's evidence: a grade on
   a fact the expectation set never contained, i.e. `judged ⊆ pre-registered`
   violated wearing a model's confidence.
2. **Fluency bias** — a confident, well-structured wrong answer out-grades a shy
   correct one. A model judge admires prose; the deterministic judge reads
   membership (V3).
3. **Drift** — the same answer graded differently across runs or providers. A
   stochastic grader cannot issue a reproducible verdict, and a non-reproducible
   verdict cannot fund a benchmark (V4).
4. **Collusion / poisoning** — an answer tuned to satisfy the grader rather than
   the user. Day 25's security seam, named today because the moment the judge
   becomes the goal, the judge has been suborned.
5. **Cost** — one model-judge call per semantic cell is a per-question bill where
   the free deterministic tier already answered.

The day's rule, encoded in `judge.py`: **an LLM judge may explain a `pending`, it
may never decide one.**

## 7. The judge is not a fixer (V9)

The counterweight, in the series' own voice: the harness is **not in the answer
path**. V9 is the fixture where the evaluator, installed and unused, changes
nothing — a golden run's trace equals the same question's production trace
byte-for-byte, no store grows, and no prior fixture row moves. Over-evaluation is
a bug measured the way over-planning (P9), over-persistence (S9), over-memory
(M8), and over-orchestration (G9) were measured: **as a no-change row.**

## 8. The evaluation set (V1–V9)

The properties of grading itself, expectations written before the harness runs
(`python tools/eval_harness.py --fixtures`):

| ID | What it proves | Expected |
|---|---|---|
| V1 | golden-set integrity | every row valid + oracle-derivable; a patched row **refused** by the hash before grading |
| V2 | judge reproducibility | the same trace graded twice → **byte-identical** verdict rows |
| V3 | the judge cannot be fooled | F (fluent-wrong), G (fabricated citation), H (citation-present, unsupported), and a confident out-of-corpus row each **fail** their deterministic check |
| V4 | judge honesty under drift | identical deterministic verdict across model runs; the `semantic:` comment may differ and the difference is **recorded** |
| V5 | the regression gate | `regress_all.py` replays the families + suites + golden rows → **exactly as green** |
| V6 | golden-set coverage | doc nine, code six, judge-hunting F/G/H, and E-family refusals present with all five fields |
| V7 | measurement, not manual | a verdict for every row, report generated with **zero human screentime** |
| V8 | pre-registration before runs | rows registered before runs; a pre-registration run is **flagged** (anti-curve-fit) |
| V9 | **control** | harness installed and unused ≡ production, byte-for-byte — the judge is not a fixer |

`MECH`/`PENDING`, Day 7's split exact as ever: **V1–V9 are all `MECH`** —
deterministic functions of the dataset, the registration hash, the traces, and
the ledger. The semantic cells (citation correctness in meaning, answer
relevance, groundedness) and the model lane's `semantic:` column are `PENDING` by
construction, owned by Day 21/22.

## Config (env-overridable)

| Key | Default | Meaning |
|---|---|---|
| `EVAL_JUDGE` | `deterministic` | the judge lane: `deterministic` \| `model` \| `none` |
| `GOLDEN_SET` | `ai_os/golden.yaml` | the pre-registered dataset path |
| `HARNESS_MODE` | `stub` | `stub` (in-process scripted gateway) or `real` (`.env` gateway) |
| `EVAL_TOPOLOGY` | `single` | production topology the harness presents (Day 19's B is exercised by `architecture_smoke.py`) |
| `EVAL_REGRESS` | `full` | `--fixtures`' V5 gate: `full` runs the day suites; `fast` runs fixtures + golden rows only |

## What this is not

Stated plainly, because a green table implies more than it proves:

- **The answers are not judged, only the traces.** Answer relevance and citation
  correctness *in meaning* are **Day 21's** RAG benchmark; agent behavior —
  steps, tool-selection, unnecessary calls, failure recovery — is **Day 22's**.
  Day 20 builds the judge that *parks* those verdicts.
- **The model lane is a comment, not a vote.** It is off by default, budgeted at
  one chat per graded semantic cell, and can never flip `deterministic_verdict`
  (V4).
- **`conclusion ⊆ evidence` is term-shaped.** It proves mandatory claims are
  asserted and forbidden claims are not, by content terms — a prose assertion
  that paraphrases beyond its evidence without hitting a `must_not_assert`
  term is not caught (the same honest limit Day 12's fabrication guard carries).
- **A fully-golden expected answer is named, not built.** The model-voice prose
  nobody should hand-write is deliberately replaced by the claims-based minimal
  form.
- **The golden set can grow only by adding rows, never by patching them.** The
  hash pin makes a widening-after-the-fact an integrity failure, not a fix.

## Negatives (honest, with owners)

1. **Answer relevance / citation correctness in meaning** — **Day 21's** RAG
   benchmark on this judge. `PENDING`.
2. **Agent behavior** (steps, tool-selection, unnecessary calls, recovery, cost,
   latency) — **Day 22's** agent benchmark. `PENDING`.
3. **Observability of the very pipeline being graded** — **Day 23's** trace.
4. **Judge poisoning** (answers tuned to pass the grader) — **Day 25's** security
   seam, named now.
5. **Relevance-scored / rotating held-out slices** — the golden set is the fixed
   battleship; a rotating held-out slice is a day-30 concern, named not built.

## Re-verify, in one line

```bash
python tools/eval_harness.py --stub                 # the §9 golden-set table, offline
python tools/eval_harness.py --fixtures             # the §8 evaluation set (V1–V9)
python tools/regress_all.py                         # Rule 2's ratchet: every row exactly as green
```

**`judged ⊆ pre-registered`** and **V9's control** hold the line: a verdict only
where the pre-registration supports it, and an evaluator that changes no answer.
The court is built, the stamps are cast, and the semantic verdicts belong to
tomorrow. See `docs/evaluations/eval-day20.md` for the §9 table.
