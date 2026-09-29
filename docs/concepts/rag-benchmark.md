# RAG Benchmark — Measuring Retrieval, Not Reporting It

> Day 21 of the AI-layer apprenticeship. Twenty days built a machine that *reads*
> (6–10), *acts* (11), *drives itself* (12), *survives failure* (13), *computes*
> (14–15), *thinks before acting* (16), *remembers* (17–18), *decides its own
> architecture* (19), and — since Day 20 — *judges its own work* (20). Day 20
> built the court and parked three semantic cells with an owner printed on each:
> **citation correctness, groundedness, answer relevance → Day 21.** The
> curriculum now names the day in two words: **"measure retrieval."** Its
> deliverable is the thing Days 7–20 kept promising as a machine: the **RAG
> benchmark.**
> Implementation: `ai_os/rag_bench.py` (the metric core), `ai_os/semantic_judge.py`
> (layer 2 of the judge), `ai_os/golden.yaml` (the new `relevant_also` /
> `human_review` / `semantic_fail` fields), `tools/bench_rag.py` (the runner +
> the R1–R9 fixture set).

The single idea: **measurement is only as honest as the relevance it
pre-registered, and answer quality is only as honest as the claims it can
trace.** Recall@K and MRR existed because *expected* was pre-registered at Day 7;
Precision@K never existed because *relevant* was never constrained — the golden
set's `relevant_also` is the missing premise that turns a judgment into
membership arithmetic. And the three semantic cells Day 20 parked reduce to one
discipline — **claim-coverage over the run's own evidence** — which is honest
only because the claims were pre-registered. A claim the answer asserts with no
line of evidence in the context the run actually showed is a hallucination *by
definition*, however fluent the prose.

## 1. The loaded objective — a report describes a run; a benchmark is a ship

The series has "evaluated retrieval" since Day 7 — hand-reading `trace.py`
tables, one question at a time. Day 21's word is **measure**, and the difference
is structural: a **report** describes a run; a **benchmark** is a fixed ship — a
fixed set of rows, a fixed set of metrics, a pinned baseline, and a
reproducibility contract — so Days 22–30 can ask *"did retrieval improve?"* and
receive a number instead of a re-read table. Day 20 made the *judgment*
re-runnable; Day 21 makes the *retrieval verdict* a machine's output.

The curriculum's four metrics are a census, and the census is the day's least
glamorous and most honest fact:

| metric | status before Day 21 | owner |
|---|---|---|
| **Recall@K** — fraction of pre-registered expected docs in top-K | **in code since Day 7** (`trace.expected_stats.recall_at_k`) | Day 7 |
| **MRR** — reciprocal of the first expected doc's rank | **in code since Day 7** (`best_rank`; ΣΔMRR since Day 8) | Day 8/9 |
| **Precision@K** — of the K slots, how many are *relevant* | **never computed** — *relevant* was never constrained | **Day 21** |
| **Retrieval relevance** — which retrieved slots actually matter | **free `rel()` since Day 9**, the paid cross-encoder `PENDING` | Day 9/21 |

Two baked, one genuinely empty, one free-but-unheadlined. The day is not "build
the RAG metrics"; it is "make the missing ones writable, and make the ones that
were only ever hand-quoted re-run automatically."

## 2. Precision@K — the metric the golden set had to exist to enable

Precision asks a question the ranking cannot answer — *"is slot 3 relevant?"* —
which is why every hand-grading day skipped it, and why Day 20's ban on manual
judgment made it *impossible* to compute honestly by hand. The golden set
supplies the missing premise: `relevant_also` — docs known to be **on-topic but
not required** — minted from corpus *structure* (never from retrieval output),
disjoint from `expected_evidence` by construction, and graded under the same
content hash. Then

```text
precision@K = |retrieved ∩ (expected ∪ relevant_also)| / K
```

is as `MECH` as recall. Two disciplines keep it honest:

- **The relevance set is pre-registered, not widened after the run.** A
  `relevant_also` widened to flatter precision changes the hash and trips the
  integrity refusal (V1) — the curve-fit, in its precision form.
- **Where relevance has no definition, the cell is `n/a` by construction** —
  E-family (nothing is relevant) and judge-hunting rows (relevance is the trap).
  `None`, never `0.0`: `0.0` would grade "refused to retrieve anything, pass," a
  category error.

**D1 is the exhibit.** *"How do I complete a checkout?"* was pre-registered at
Day 7 with the hypothesis *"duplicated context — 4 of 5 slots are near-dupes of
one meaning."* On the stub, D1's recall@K is 1.0 and its MRR is 1.0 — the two
legacy metrics report *perfect* on a context the same row's hypothesis calls
polluted. Precision is the first number that sees the disease: a recall-and-MRR
perfect row whose precision is `4/9` on a 9-chunk store. A recall- and
MRR-perfect row with low precision is not a pass — it is the whole argument for a
metric the two easy ones cannot be.

**Retrieval relevance** is Day 9's free pointwise `rel()` (fused + rare +
heading + position) reported as the mean over the *kept* context. It is reused
wholesale from `ai_os/rerank.py` — a second implementation of the same seam
would be a second seam to drift. The paid `relevance_cross_encoder` (one chat per
candidate) stays shelved behind its `NotImplementedError` until this free column
shows a leak the free signal misses; Day 9 §7's pricing question finally has a
bench to answer it.

## 3. The semantic tier — claim-coverage over the run's own evidence

Day 20 parked three cells and printed an owner on each. Day 21's theorem is that
all three are one discipline read three ways, and that the discipline is honest
only because the golden set pre-registered the claims (`claims[] +
must_not_assert[]`, oracle-derivable since Day 20).

- **Citation correctness** splits in two. The **mechanical** half is Day 6's
  invariant and never relaxes: `cited ⊆ retrieved` — every citation points at a
  doc the run actually retrieved (V3b's G-row: a fabricated citation, caught by
  set membership). The **support** half is new: the cited doc must *state* the
  claim it is cited for. A citation can be **real and wrong** — the K1 row cites
  an in-context lifecycle page for a claim about `checkoutCreate`; it passes
  `cited ⊆ retrieved` and fails support.
- **Groundedness** is `claims ⊆ evidence`, Day 16's `conclusion ⊆ evidence`
  decomposed per claim: every asserted claim must trace to the context the run
  actually showed. The F-row (fluent but wrong) is the demonstration that prose
  buys nothing.
- **Answer relevance** is claim-coverage: the fraction of pre-registered
  `claims[]` the answer asserted, with a `must_not_assert[]` hit always a fail of
  class *forbidden* (the J1 row) — a category error, not a coverage miss.

The **deterministic subnet** decides what membership can decide: a claim is
**deducible** when, after the series' normalization (stopword-fold, stem match —
`eval_dataset.claim_derivable`), its content terms appear in the evidence. The
stem match carries a **length guard** (`_MAX_INFLECTION`), because a bare 4-char
stem collapses `checkout` and `checkoutCreate` onto one another — which would let
a page that only says "checkout" license a claim about `checkoutCreate`, i.e.
silently pass the K1/R5 and R6 fixtures the support half exists to catch. A claim
the subnet cannot decide is **undecided**: recorded with an owner, optionally
explained by the flagged lane, never silently passed.

The flagged lane (layer 3, behind `EVAL_JUDGE=model`, **off by default**) is one
chat per *undecided* claim, writing only to `semantic:` with `graded_by: model`,
and it may **explain** an undecided claim, never flip a `MECH` verdict. The day
*prices* semantics before it spends on them — Day 9's cross-encoder question
wearing a judge's hat.

## 4. τ calibration and the gate flip (R8)

τ has been `0.55` in both YAMLs since Day 7, self-described as *"to be
calibrated, not trusted."* `tools/bench_rag.py --tau-sweep` calibrates it on the
real store's fused top-1 scores — the answerable families (A/B/C/D) against the
E-family refusals (E1/E2/CE1) — and reports the separation
(`min answerable − max E`), the τ that refuses every E row and no answerable row,
and the leak it costs. Day 9 §7 pre-registered the worry: a fused-score τ cannot
separate E. On this store (11,061 chunks, `bge-base-en-v1.5`, k=9) the sweep
finds answerable top-1 `0.794 … 0.853` and E `0.775 / 0.783` — a separation of
**+0.011**: a defensible τ = `0.794` exists, with **0 leaks**, but the margin is
thin. The finding is recorded in both YAMLs (fitted τ + the real top-1 numbers),
and the **durable** refusal verdict stays the semantic tier: where the score gate
cannot make the call, an answer whose claims trace to no shown evidence is
refused by code, never fabricated.

## 5. The R-bench fixtures (R1–R9) and the control

The properties of measurement itself, expectations written before the runs
(`python tools/bench_rag.py --fixtures`), all `MECH` (0 chats):

| ID | What it proves | Expected |
|---|---|---|
| R1 | recall@K | every answerable golden row's `expected ⊆ top-K`; leaks **reproduced and debited**, never hidden |
| R2 | precision@K | a number on rows with `relevant_also`; `n/a` by construction on E-family and judge-hunting rows |
| R3 | MRR | the bench's MRR ≡ the trace's MRR (inherited, not new) |
| R4 | retrieval relevance | the free `rel()` measured for every kept context; the paid lane still raises |
| R5 | citation correctness | G fails membership; **K1 fails support while passing membership** (the tier's reason to exist) |
| R6 | groundedness | a claim in the shown context passes; the same claim with no line of evidence **fails** |
| R7 | answer relevance | J1's `must_not_assert` hit fails; A1 passes; the F-row is rejected |
| R8 | τ calibration | the answerable-vs-E separation and the gate-flip decision |
| R9 | **control** | the bench installed and unused ≡ production, byte-for-byte — **the bench is not a fixer** |

**R9 is the control** — V9's reflex on the benchmark surface: a bench that
improves answers is *in* the answer path and is a bug. R1–R9 run on the stub and
are wired into the regression gate (`tools/regress_all.py`).

## 6. Cost — the price of semantics, quoted before paid

The retrieval bench and the deterministic half of the semantic judge are **0
chats, 0 embeds** (one query embed per row) — metrics over traces, membership
over sets, coverage over claims. The only billable lines are the flagged
per-claim lane (`EVAL_JUDGE=model`, 1 chat per *undecided* claim, off by default)
and the shelved cross-encoder (`NotImplementedError`). The day prices semantics
before deciding whether to pay for them.

## Config (env-overridable)

| Key | Default | Meaning |
|---|---|---|
| `BENCH_MODE` | `stub` | the store the bench measures (`stub` \| real) |
| `GOLDEN_SET` | `ai_os/golden.yaml` | the pre-registered dataset path |
| `EVAL_JUDGE` | `deterministic` | the judge lane: `deterministic` \| `model` \| `none` |

## What this is not

Stated plainly, because a green table implies more than it proves:

- **On a real store the semantic cells grade a *scripted* answer.** The scripted
  answer exists only to give the deterministic subnet something to grade; a real
  model's answer is the Day-21 real-key handoff. Where a real row's retrieval
  misses its expected docs, the row fails with owner **retrieval** — the semantic
  cells are downstream of the retrieval miss, not an independent verdict.
- **Support-in-meaning** (does the cited doc *mean* what the claim means) is the
  flagged lane's; the deterministic half is a term-containment probe.
- **The undecidable claims** are owned by the flagged lane, never silently passed.
- **The LOM verdict** (does slot 1 beat slot 3 for a real model) is R7 across a
  `best_first` sweep — `PENDING` a real graded run.
- **Agent behaviour** — steps, tool-selection, recovery, cost, latency — is
  **Day 22's** agent benchmark on the same golden set.
- **Judge poisoning** (answers tuned to pass the grader) is **Day 25's** seam.
- **A rotating held-out slice** (the golden set is the fixed battleship) is a
  day-30 concern, *named not built*.

## Negatives (honest, with owners)

1. **The real code corpus** — the code rows run on the stub's mechanism proof
   until Day 10's real Saleor store lands (Cloudflare-quota-blocked); the same six
   rows re-run the day it does.
2. **The τ margin is 0.011** — the fitted gate ships, but the durable refusal is
   the semantic tier. `PENDING` a larger E sample.
3. **Real answer quality** — citation correctness in meaning, groundedness
   beyond membership, whether recall makes real answers better — is the real-key
   handoff, not a stub artefact.
4. **Precision's relevance set is itself a judgment** — minted from corpus
   structure and hash-pinned; a structural constraint (`relevant_also ⊆ one hop
   from expected in the corpus graph`) is a recorded seam, not built.
5. **I1 never resolves mechanically** — corpus-can-be-wrong is `PENDING-human` by
   design.

## Re-verify, in one line

```bash
python tools/bench_rag.py --fixtures                       # R1–R9, 0 chats
python tools/bench_rag.py --stub                           # the R-report (stub)
python tools/bench_rag.py --stub --tau-sweep               # the stub τ separation
python tools/bench_rag.py --real --source doc --store data/store.json --tau-sweep  # real doc store
python tools/regress_all.py                                # Rule 2's ratchet, bench included
```

**R9's control** and **`judged ⊆ pre-registered`** hold the line: a bench that
changes no answer, and a verdict only where the pre-registration supports it. See
`docs/evaluations/bench-day21.md` for the §9 R-report (both stores) and the R1–R9
fixture table.
