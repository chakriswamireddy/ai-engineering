# RAG Benchmark Day 21 — the R-report (measure retrieval)

> Day 21's evaluation record. Retrieval cells are pure functions of the trace
> (`ai_os/rag_bench.py`); the semantic cells read the run's **own** context
> (`ai_os/semantic_judge.py`) and never re-answer. `n/a` is honored where
> relevance is undefined; `pending-human` is the corpus-can-be-wrong boundary
> (I1) by design. Cost: **0 chats** for the bench and the deterministic subnet;
> only the flagged per-claim lane bills. Concept: `docs/concepts/rag-benchmark.md`.

dataset hash: `a936c02e032b4c1edfbba5d25a38bbd9ee254203ab6fb9c57fb6ed687598909e` (pinned) · registered: 2026-09-28 · `EVAL_JUDGE: deterministic` · doc k=9, code k=14

Reproduce:

```bash
python tools/bench_rag.py --fixtures                                   # R1–R9, 0 chats
python tools/bench_rag.py --stub                                       # the §9 table below (stub)
python tools/bench_rag.py --real --source doc --store data/store.json --tau-sweep   # the real column
python tools/regress_all.py                                            # Rule 2's ratchet, bench included
```

## §9 — the R-report, row by row (every cell a real run's)

### (a) The stub store — the mechanism proof (Day-7 mini-corpus + Day-10 symbol tree)

| id | family | store | K | recall@K | precision@K | MRR | relevance | cited ⊆ retrieved | citation-support | groundedness | coverage | must_not | verdict | owner |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A1 | A — obvious (control) | doc | 9 | 100% | 0.333 | 0.333 | 0.611 | ok | ok | ok | 100% | ok | **pass** | n/a |
| B1 | B — multi-document (the seam) | doc | 9 | 100% | 0.556 | 0.333 | 0.495 | ok | ok | ok | 100% | ok | **pass** | n/a |
| B2 | B — multi-document (the seam) | doc | 9 | 100% | 0.444 | 1.000 | 0.445 | ok | ok | ok | 100% | ok | **pass** | n/a |
| C1 | C — ambiguous terminology | doc | 9 | 100% | 0.111 | 0.500 | 0.384 | ok | ok | ok | 100% | ok | **pass** | n/a |
| C2 | C — ambiguous terminology | doc | 9 | 100% | 0.222 | 1.000 | 0.278 | ok | ok | ok | 100% | ok | **pass** | n/a |
| D1 | D — similar documents | doc | 9 | 100% | **0.444** | 1.000 | 0.593 | ok | ok | ok | 100% | ok | **pass** | n/a |
| D2 | D — similar documents | doc | 9 | 100% | 0.444 | 0.500 | 0.610 | ok | ok | ok | 100% | ok | **pass** | n/a |
| E1 | E — out of corpus | doc | 9 | n/a | **n/a** | n/a | 0.261 | ok | n/a | n/a | n/a | ok | **n/a (refusal row)** | n/a |
| E2 | E — out of corpus (near-miss) | doc | 9 | n/a | **n/a** | n/a | 0.257 | ok | n/a | n/a | n/a | ok | **n/a (refusal row)** | n/a |
| CA1 | A — obvious (split-brain double-hit) | code | 14 | 100% | 0.214 | 0.100 | 0.337 | ok | ok | ok | 100% | ok | **pass** | n/a |
| CB1 | B — cross-module flow | code | 14 | 100% | 0.143 | 1.000 | 0.215 | ok | ok | ok | 100% | ok | **pass** | n/a |
| CB2 | B — cross-module flow | code | 14 | 100% | 0.214 | 1.000 | 0.104 | ok | ok | ok | 100% | ok | **pass** | n/a |
| CC1 | C — name collision | code | 14 | 100% | 0.143 | 1.000 | 0.211 | ok | ok | ok | 100% | ok | **pass** | n/a |
| CD1 | D — near-duplicate symbols | code | 14 | 100% | 0.143 | 1.000 | 0.206 | ok | ok | ok | 100% | ok | **pass** | n/a |
| CE1 | E — out of corpus | code | 14 | n/a | **n/a** | n/a | 0.106 | ok | n/a | n/a | n/a | ok | **n/a (refusal row)** | n/a |
| F | fluent but wrong | judge-hunting | 0 | n/a | n/a | n/a | n/a | ok | ok | ok | 100% | **FAIL** | **fail (rejected)** | generation |
| G | fabricated citation | judge-hunting | 0 | n/a | n/a | n/a | n/a | **FAIL** | ok | ok | 100% | ok | **fail (rejected)** | generation |
| H | citation-present, unsupported | judge-hunting | 0 | n/a | n/a | n/a | n/a | ok | ok | ok | 100% | **FAIL** | **fail (rejected)** | generation |
| I1 | corpus can be wrong (contradiction) | judge-hunting | 0 | n/a | n/a | n/a | n/a | ok | ok | ok | 100% | ok | **pending-human** | reader |
| J1 | claim leak (`must_not_assert`) | judge-hunting | 0 | n/a | n/a | n/a | n/a | ok | ok | ok | 100% | **FAIL** | **fail (rejected)** | generation |
| K1 | near-miss quote (citation support) | judge-hunting | 0 | n/a | n/a | n/a | n/a | ok | **FAIL** | ok | 100% | ok | **fail (rejected)** | **semantic judge** |

Three columns carry the day: the **`MECH` verdict on every row** (byte-reproducible),
the **`n/a` cells honored** (E-family precision; `undefined ≠ 0.0`), and the
**semantic PENDINGs with owners**. **D1 is the exhibit**: recall 100%, MRR 1.000 —
the two legacy metrics call it perfect — and precision **0.444** is the first
number that sees the pollution the Day-7 hypothesis pre-registered.

### (b) The real doc store — the day's headline (11,061 Saleor chunks, `bge-base-en-v1.5`, k=9)

| id | K | recall@K | precision@K | MRR | relevance | cited ⊆ retrieved | support | grounded | coverage | verdict | owner |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A1 | 9 | **0%** | 0.000 | 0.000 | 0.644 | ok | FAIL | FAIL | 100% | **fail** | retrieval |
| B1 | 9 | 67% | 0.222 | 0.167 | 0.543 | ok | FAIL | FAIL | 100% | **fail** | retrieval |
| B2 | 9 | **0%** | 0.000 | 0.000 | 0.515 | ok | FAIL | FAIL | 100% | **fail** | retrieval |
| C1 | 9 | 100% | 0.111 | 0.333 | 0.627 | ok | FAIL | FAIL | 100% | **fail** | semantic judge |
| C2 | 9 | 50% | 0.111 | 1.000 | 0.564 | ok | FAIL | FAIL | 100% | **fail** | retrieval |
| D1 | 9 | 100% | 0.111 | 0.500 | 0.700 | ok | FAIL | FAIL | 100% | **fail** | semantic judge |
| D2 | 9 | **0%** | 0.000 | 0.000 | 0.622 | ok | FAIL | FAIL | 100% | **fail** | retrieval |
| E1 | 9 | n/a | n/a | n/a | 0.527 | ok | n/a | n/a | n/a | **n/a (refusal row)** | n/a |
| E2 | 9 | n/a | n/a | n/a | 0.494 | ok | n/a | n/a | n/a | **n/a (refusal row)** | n/a |
| F/G/H/I1/J1/K1 | 0 | — | n/a | — | — | as stub | K1 fails support | — | — | **rejected / pending-human** | judge |

The real column is not flattering, and that is the point: **the bench's first
real act indicts its own pipeline.** Only 3 of 7 answerable doc rows surface
≥50% of their expected docs at k=9 (C1, D1 at 100%; C2 at 50%; B1 at 67%), and
**4 of 7 surface none** (A1, B2, D2 — the envelope-bound leak Day 9 found). The
rows that do retrieve still fail *support/groundedness* on the scripted answer
because the kept context does not state the claim's terms — the semantic cells
are downstream of the retrieval miss, not an independent verdict (see the concept
doc's "What this is not"). The debit is recorded with an owner (**retrieval**,
Day 8's backlog), as a verdict rather than a paragraph.

> The judge-hunting rows are *fixtures*, graded against a fixed corpus so their
> verdicts are mode-independent (K1 fails support, I1 is `pending-human` on every
> store) — a fixture whose verdict depended on the store would not be a fixture.

## §7 — the τ calibration (the oldest `PENDING` in the retrieval line)

`tools/bench_rag.py --tau-sweep` measures the fused top-1 separation between the
answerable families (A/B/C/D) and the E-family refusals (E1/E2/CE1).

| store | answerable top-1 | E-family top-1 | separation (min A − max E) | fitted τ | leaks | finding |
|---|---|---|---|---|---|---|
| stub (demo, 9/14 chunks) | 0.932 … 0.996 | 0.619, 0.682 | **+0.250** | 0.932 | 0 | defensible τ (a demo, not a calibration) |
| **real doc** (11,061 chunks) | **0.794 … 0.853** *(0.7938, 0.8076, 0.8080, 0.8106, 0.8110, 0.8141, 0.8529)* | **0.775, 0.783** | **+0.011** | **0.794** | **0** | a defensible τ exists — but the margin is **thin** |
| real code | — | — | — | — | — | **PENDING** Day 10's store (quota-blocked) |

**The decision, on the day's own data** (recorded in `ai_os/questions.yaml` and
`ai_os/code_questions.yaml`): the fused-score gate **ships a fitted τ = 0.794** —
it refuses both E rows and no answerable row, 0 leaks — and the **durable**
refusal verdict stays the **semantic tier** (`refusal: semantic`): where the
score cannot make the call, an answer whose claims trace to no shown evidence is
refused by code, never fabricated. Day 9 §7 predicted an *inseparable overlap*
(E1/E2 at 0.854/0.874, inside the answerable band); on this store and embed model
the separation is instead a hairline **+0.011**. The honest reading is neither
"the gate works" nor "the gate fails" — it is *"the score gate is 0.011 wide and
two E samples deep; treat it as the fast path, not the guarantee."* The code
corpus's real separation is unmeasurable until Day 10's store lands; the code
rows run on the stub mechanism proof, and the same six rows re-run the day it
does.

## §8 — the R-bench fixtures (R1–R9): the properties of measurement

| id | what it proves | expected | observed | status |
|---|---|---|---|---|
| R1 | recall@K = `expected ⊆ top-K` | all answerable rows green | 12/12 rows expected ⊆ top-K | green |
| R2 | precision@K over `expected ∪ relevant_also`; `n/a` where undefined | a number with a relevance set; `n/a` without | D1 precision@9 = 0.444 | green |
| R3 | MRR = 1/first-expected-rank | bench ≡ trace | 5 rows agree | green |
| R4 | retrieval relevance = mean `rel()` over kept context | free signal measured; paid lane raises | 15 rows in [0,1]; paid lane raises | green |
| R5 | citation correctness: membership + support-in-meaning | G fails membership; **K1 fails support while passing membership** | G=False K1 support=False | green |
| R6 | groundedness: every asserted claim traces to shown evidence | grounded passes; ungrounded fails | good=True bad=False | green |
| R7 | answer relevance = coverage (a `must_not` hit always fails) | J1 fails; A1 passes; F rejected | J1 must_not=False A1 cov=1.000 | green |
| R8 | τ calibrated on real scores; the gate-flip decision | a separation, or the documented overlap | real sep=+0.011 → τ=0.794, 0 leaks | green |
| R9 | **control**: the bench is not a fixer | bench off ≡ production, byte-for-byte | identical; no store grows | green |

`MECH`/`PENDING`, Day 7's split exact as ever: **R1–R9 are all `MECH`** (0 chats)
— metrics over traces, membership over sets, coverage over claims, τ over
arithmetic. The no-regression line runs Day 20's way: `regress_all.py` with the
bench **off** (the golden rows, 21/21) and **on** (R1–R9) — **17 rows, 0 moved**.
A row the bench cannot reproduce is a **bench bug to fix**, never a fixture to
weaken.

## The pinned baseline (the floor for Days 22–30)

Recorded on the real doc store and written to `data/bench-baseline.json` by
`--baseline` (a local artifact; `data/` is not tracked): one row per golden id
with `recall@K · precision@K · MRR · retrieval_relevance`. Days 22–30 ask *"did
it improve?"* against these numbers. The Day-8/9 floor they descend from: Day 9's
real run showed **7 of 9 doc rows leaking** at top-8 (best expected docs at fused
ranks 16–30+) with C2 partially moved (0.2→0.5) and D2 regressed (0.2→0.0) — the
numbers the bench's real column above confirms and sharpens.

## Cost (the price of semantics, quoted before paid)

| lane | cost | state |
|---|---|---|
| retrieval bench + deterministic semantic subnet | **0 chats, 0 embeds** (one query embed/row) | always on |
| flagged per-claim lane (`EVAL_JUDGE=model`) | **1 chat per *undecided* claim** | off by default |
| cross-encoder relevance lane | N chats/candidate | `NotImplementedError`, off |

The benchmark itself is **0 tokens** — the only paid line is the flagged per-claim
column, priced before it is used.

## The `PENDING` line (owned, not wished)

| cell | owner |
|---|---|
| real answer quality (citation correctness in meaning, groundedness beyond membership) | the Day-21 real-key handoff |
| the undecidable claims (paraphrase/inference residue) | the flagged lane (`EVAL_JUDGE=model`), off by default |
| the LOM real verdict (does slot 1 beat slot 3) | R7 across a `best_first` sweep, Day-21 real run |
| the real code corpus + its τ calibration | Day 10's `data/code_store.json` (quota-blocked) |
| agent behaviour (steps, tool-selection, recovery, cost, latency) | **Day 22's** agent benchmark, same golden set |
| observability of the graded pipeline | **Day 23's** trace |
| judge poisoning (answers tuned to pass the grader) | **Day 25's** security seam |
| the rotating held-out slice | a day-30 concern, *named not built* |
