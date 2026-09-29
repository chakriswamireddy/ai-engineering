# Day 21 Notes — RAG Evaluation

> Notes answering `docs/day-plan/day-21.md`.
> Twenty days built a machine that *reads* (Days 6–10), *acts* (11–12), *fails honestly* (13), *computes* (14–15), *plans* (16), *remembers* (17–18), *decides its own architecture* (19), and *judges its own work* (20). Day 20 built the court and made the month's promise mechanical: the golden dataset, the harness, the deterministic judge, the regression gate, and three semantic cells — citation correctness, groundedness, answer relevance — parked with an owner printed on each: **Day 21.** The curriculum now says what sits behind that door in two words — **"measure retrieval"** — and hands the day the four metrics the series has been hand-computing or hand-wishing since Day 7 (**Recall@K, Precision@K, MRR, retrieval relevance**) plus the "also evaluate" line (**citation correctness, groundedness, answer relevance**), for one deliverable: **the RAG benchmark.** Day 21's opening census is its least glamorous and most honest fact: two of the four retrieval metrics were already in code (`ai_os/trace.py`'s `expected_stats` has computed recall@K and MRR since Day 7), one existed as a free signal that was never the headline (retrieval relevance = Day 9's `rel()` arithmetic), and one had never been computed by anyone because it *couldn't* be — **precision@K needs a notion of "relevant," and until the golden set pre-registered one, precision was a judgment asking for a grader, which is the exact thing Day 20 banned.**
> The single idea: **measurement of retrieval is only as honest as the relevance it pre-registered, and the answer's quality is only as honest as the claims it can trace — so Day 21 writes `relevant_also` into the golden set to make precision arithmetic, and re-reads answer quality as claim-coverage over the run's own evidence to make the semantic tier checkable.** The rank-and-membership metrics stay `MECH` (recall@K, MRR, and now precision@K are arithmetic over the trace, zero chats); the meaning metrics are claim-coverage's house — a claim the answer asserts without a line of evidence in the shown context is a hallucination by definition, and a `must_not_assert` hit is a coverage failure of class *forbidden*, the worst kind. The judge's paid lane is wired, budgeted (1 chat per *undecided* claim), off by default, and never flips a `MECH` verdict; the day *prices* semantics before it spends on them, which is Day 9's cross-encoder question wearing a judge's hat and finally given a bench. And the day's oldest debt is called: Day 7's τ — "to be calibrated, not trusted" — is calibrated on real scores, the Day-9 prediction of an inseparable fused-score overlap is confirmed or killed by a sweep, and the refusal verdict either gets its defensible threshold or officially moves to the semantic tier, exactly as Day 9 §7 pre-registered.
>
> **The one line the day would die defending: a semantic verdict is only a step above the deterministic floor if it can prove itself the same way — and an answer's claim that cannot be traced to the evidence the run actually showed is a hallucination by definition, no matter how confident the prose.**

---

## 1. The Loaded Question — "Measure Retrieval"

Day 21's objective is the shortest of the month after Day 20's and, read the way the apprenticeship reads every adjective, the cruelest: **measure retrieval.** It does not say "evaluate a retrieval run" — the series has been doing that since Day 7, hand-computing `trace.py` tables one question at a time and calling them reports. It says *measure*, which is the difference between a report and a benchmark: a report describes a run; a benchmark *is a fixed ship with a pinned baseline and a reproducibility contract*, so that any future change can be asked the one question Rule 4 has been asking in vain: *did it improve, by which metric, on which dataset, at what cost?* Day 9's real-corpus sweep was the closest the series came — pre-registered expectations, ΣΔMRR, a 0-leak guard, knob sweeps — but it was still a hand-written table produced by a script that a human had to read to extract the verdict. Day 20 built the grader that makes the verdict a machine's output; Day 21 builds the machine that *is* the retrieval verdict.

The curriculum's metrics list is the census, and the census is the day's least glamorous fact:

- **Recall@K** — the fraction of pre-registered expected docs that reached the top-K. In code since Day 7 (`expected_stats.recall_at_k`); Day 7's §4 decision tree is literally a recall classifier (`expected ∉ top-K → missing-retrieval`). The series has hand-quoted it for fourteen days; the bench will reproduce it as a column.
- **MRR** — the reciprocal of the first expected doc's rank. In code since Day 7 (`expected_stats.mrr`, `best_rank`); Day 8's ΣΔMRR and Day 9's "no leaks" guard made it the improvement metric of the retrieval years. The bench inherits the contract unmodified.
- **Precision@K** — the day's genuine hole. Nobody hand-computed it because it required a judgment ("of the K slots, which are relevant?") that the ranking cannot make and a human grader was not allowed to make (Day 20). Day 21's answer: **the golden set is the relevance oracle** — each answerable row gains a pre-registered `relevant_also` set (on-topic docs that need not be retrieved), and precision becomes `|retrieved ∩ (expected ∪ relevant_also)| / K`, membership arithmetic, `MECH`, free.
- **Retrieval relevance** — Day 9's `rel()` signal (heading + rare + cosine), the free pointwise relevance that reranking already spends on every candidate, pendant until today as the answer to a pricing question the day finally asks with a bench instead of a hope: does the free arithmetic beat the paid `relevance_cross_encoder` (1 chat/candidate) at saying which retrieved slots actually matter.

Two of the four in code, one free and hanging, one genuinely empty. The day is not "build the RAG metrics"; it is "make the two missing ones writable, and make the two that were only ever hand-quoted re-run automatically." The benchmark is a machine that turns every golden row into one reproducible row of numbers — the same promise Day 20 made about verdicts, now pointed at the retriever.

---

## 2. The Four Metrics — Two Inherited, One Defined-By-The-Golden-Set, One Priced

**Recall@K** is the series' oldest number: `expected ⊂ top-K`, pre-registered from corpus structure since Day 7's YAML and graded as membership ever since. The bench's only addition is that the *row-level verdict* — which expected doc leaked, at which rank, who owns the leak — becomes a column with an owner, per Day 7's isolation map, so "retrieval is the bottleneck" stops being a paragraph in a report and becomes a debit line. The real-store floor is already written and unforgiving: Day 9's real-corpus table shows 7 of 9 doc rows leaking at top-8, best expected docs sitting at fused ranks 16–30+; the bench's first real act is reproducing exactly those leaks and naming them.

**MRR** inherits the balance of a decade of hand-reports: ΣΔMRR as the improvement metric, *leaks* as the anti-regression guard, the sweep's contract ("allowed to move: the rows Day 8 owned; must hold: the controls; never regress a green row"). The bench does not invent a new MRR; it makes the old one a machine's output and pins the old baselines as the floor.

**Precision@K** is the day's theorem, and it is the reason the golden set had to exist *first*. Precision asks a question with no arithmetic answer in the trace ("is slot 3 relevant?"), which is why every hand-grading skipped it and why Day 20's ban on manual judgment made it *impossible* to compute by hand honestly. The golden set supplies the missing premise: `relevant_also`, pre-registered under the same discipline as `expected` (written from corpus structure, graded under the dataset hash). With it, precision is as `MECH` as recall — the metric nobody could compute becomes the metric the golden set exists to enable.

The pair is not two numbers; it is one verdict viewed twice, and **D1 is the day's proof**. "How do I complete a checkout?" was pre-registered in Day 7 with the hypothesis *duplicated context — 4 of 5 slots are near-dupes of one meaning*. Recall@K = 1.0 (the how-to page is retrieved), MRR = 1.0 (it ranks first) — both green, both blind. Precision@K — with the twin family minted into `relevant_also` — is the first number that has ever seen the pollution D has been pre-registering since Day 7: 4 of 5 retrieved slots duplicate one meaning, and a two-metric report that says "perfect" was not wrong, merely unarmed. A recall-and-MRR-perfect row at precision 0.2 is the entire argument for a benchmark that measures more than the two things that were easy to measure.

The edge cases are decisions, not gaps: **empty expected** → recall/MRR are `n/a`, never 0.0 (E-family; 0.0 would grade "didn't retrieve anything, pass"); **empty relevance definition** → precision is `n/a` by construction (E: nothing is relevant; F/G/H: relevance is the trap); **K varies with budget_chars** → metrics are only comparable at the same pinned K, so the bench pins `k` per corpus the way `k_from_budget` always did implicitly.

---

## 3. The Semantic Tier — Citation Correctness, Groundedness, Answer Relevance as Claim-Coverage

Day 20 parked three cells and printed an owner on each: citation correctness, groundedness, answer relevance, all `PENDING: Day 21`. Day 21's theorem is that all three are one discipline read three ways — **claim-coverage over the run's own evidence** — and that the discipline is only honest because the golden set pre-registered the claims.

**Citation correctness** splits into the mechanical and the meaningful. The mechanical half is Day 6's invariant and it never relaxes: `cited ⊆ retrieved` — every citation points at a doc the run actually retrieved, checked by set membership (V3b's G-row: fabricated citation, caught for free). The meaningful half is *support*: the cited doc actually supports the claim it is cited for. This is the new thing, and the K-row is its fixture — a citation that is genuinely in-context yet does not back the claim (a lifecycle page cited for a claim about apps) passes `cited ⊆ retrieved` and fails support. A citation can be real and wrong; the mechanical half by design cannot see it; the semantic tier exists because of it.

**Groundedness** is `claims ⊆ evidence`: every claimed fact traces to the context the run actually showed — Day 16's `conclusion ⊆ evidence`, decomposed per claim. The sharpening is the day's rule: **a claim with no line of evidence in the shown context is a hallucination by definition**, independent of fluency, confidence, or structure. The F-row (fluent but wrong: correct citations, unsupported conclusion) is the demonstration that prose buys nothing.

**Answer relevance** is claim-coverage: how much of the pre-registered `claims[]` did the answer assert, and did it assert any `must_not_assert`? A `must_not` hit is not a coverage miss — it is a coverage failure of class *forbidden*, a category error the answer must not make, and it always fails the row. This is the bridge Day 20's Notes asked about in its Questions section — *does the claims form expand into a claim-coverage metric, and is that the honest bridge from the deterministic floor to the semantic tier?* — and the day's answer is yes, with the seam made precise: the **deterministic subnet** covers claims that are *deducible* — contained in, or term-derivable from (normalization + Day 9's `text_sim`), the evidence the answer could have cited; the **residue** — paraphrase, inference, meaning-only support — is *undecided*, recorded, owned, optionally explained by the flagged lane, and never silently passed.

The judge's discipline is Day 20's intact across all three cells: deterministic first (`EVAL_JUDGE=deterministic`, 0 chats), the paid lane (bounded, 1 chat per undecided claim) off by default, and the machine may only *decide* where membership can — an undecided claim is parked with an owner, because a semantic verdict that cannot re-run is Day 20's drift problem wearing a fancier hat. V2's rule stands: the same trace graded twice is the same verdict row, byte for byte.

---

## 4. The RAG Benchmark — the Court Day 20 Built, the Stand Day 21 Provides

Day 19 called its harness "the exact machinery Day 20 formalizes into a benchmark." Day 20 formalized it into a court and handed the semantic verdicts to "a real run." Day 21 is that real run — the court's first sitting — and the design is deliberately unsurprising because the architecture was settled two days ago: the benchmark is **Day 20's harness plus a metrics layer plus a pinned baseline**, not a new engine in the answer path.

```text
   THE GOLDEN SET (five fields + relevant_also; hash-pinned; E-family refusals,
     judge-hunting F/G/H, new adversarial I1/contradiction, J1/claim-leak,
     K1/near-miss-quote) — written, and pinned, before any run
                                │
           ┌────────────────────┴─────────────────────┐
           ▼                                          ▼
  THE RETRIEVAL BENCH (R1–R4, MECH)       THE SEMANTIC JUDGE (R5–R7)
  runs each row on the chosen store        fills day 20's parked cells:
  (stub / data/store.json / code store)    citation support · groundedness
  recall@K · precision@K · MRR ·           · answer relevance = claim-coverage
  retrieval relevance (free rel() math)    over the run's OWN context
  + the priced cross-encoder lane (OFF)    + flagged 1-call-per-claim lane (OFF)
           └────────────────────┬─────────────────────┘
                                ▼
                THE R-REPORT — one row per golden entry:
     R1 recall@K · R2 precision@K · R3 MRR · R4 relevance · R5 cited ⊆ retrieved
     + citation-support · R6 groundedness · R7 coverage (+must_not)
     · R8 refusal (τ on real scores) · R9 control (bench off ≡ days-without)
                                ▼
         verdict rows + pinned baseline → Days 22–30 ask "did it improve?"
```

The design decisions:

**Decision 1 — precision and relevance are the build; the other two metrics already exist.** recall@K/MRR columns come from `trace.expected_stats` verbatim. The work is (a) minting `relevant_also` into golden.yaml under the same oracle discipline as `expected` — pre-registered from corpus structure, rejectable if it leaks, graded under the dataset hash — and (b) making retrieval relevance a column via Day 9's free `rel()` over the kept context, with the cross-encoder lane shelved behind its existing `NotImplementedError` until the free column demonstrates a leak the free signal misses. Day 9 §7's pricing question — *does paying for semantics beat the free rel() arithmetic?* — now has a bench to answer it instead of a note to defer it.

**Decision 2 — the semantic judge grades the run's own evidence, never a fresh answer.** Groundedness and claim-coverage read the context the trace actually built and the answer the run actually gave; a judge that regenerates to "check" is a second production call grading a run nobody gave (Day 20's rule, unchanged). The only billable cell is a claim the deterministic subnet cannot trace, budgeted at 1 chat, flagged, off by default, forbidden from flipping a `MECH` verdict.

**Decision 3 — the refusal row (R8) is calibration, not decoration.** τ is `0.55` in both YAMLs, self-described as "to be calibrated, not trusted." Day 9's real run already produced the first calibration data and it was ominous: E1/E2 fused top-1 = 0.854 / 0.874, *inside* the answerable family's ~0.7–0.9 range. Day 21's `--tau-sweep` either finds a defensible separation or confirms the pre-registered finding — *a fused-score τ cannot separate E on this corpus* — and then makes the gate-flip decision Day 7's own docstring promised: *only Day 21's evaluation flips the decision into the pipeline.*

**Decision 4 — the LOM verdict lives here.** Day 9 proved the ordering knob works (`MECH`, the slot sweep). Whether slot 1 beats slot 3 for the model's actual correctness was left `PENDING` until a graded chat run — the R7 answer-relevance column across a `best_first` sweep is that graded run, closing the oldest open semantic `PENDING` in the retrieval line.

The negatives are named with owners: real model *tool choice* (Day 11's `PENDING`) is Day 22's agent benchmark; observability of the graded pipeline is Day 23's trace; judge poisoning remains Day 25's seam; and the fully-golden prose answer stays *named, not built* — the claims form is the replacement, and claim-coverage is what fills it out.

---

## 5. Build: The Retrieval Bench

`ai_os/rag_bench.py` is the metric core, and its discipline is that **every metric is a pure function of a trace**: for each golden row, `trace.retrieve_trace` on the chosen store produces retrieved / kept / `expected_stats` at 0 chats, and the bench adds exactly two columns that trace already has the ingredients for — `precision_at_k` over `expected ∪ relevant_also`, and `retrieval_relevance` as the mean `rel()` over the kept context (Day 9's arithmetic, reused wholesale because a second implementation of the same seam would be a second seam to drift).

`ai_os/golden.yaml` grows honestly. The doc nine and code six keep their fields and gain `relevant_also` sets minted from corpus *structure* — D1's twin family, B1/B2's sibling lifecycle pages, C2's surrounding attribute-file pages — each pre-registered, each re-readable, each rejectable if it leaks into the required set. The judge-hunting F/G/H and the E-family refusals stay, and the precision cell learns the honest `n/a` where relevance has no definition. And the day adds its adversarial rows: **I1 the contradiction row** (a page in the corpus that genuinely contradicts itself — the *corpus-can-be-wrong* seam Day 20's Notes asked about, where every citation cell passes and the fact is still wrong), **J1 the claim-leak row** (a `must_not_assert` violation for coverage to catch), **K1 the near-miss quote row** (a citation in-context that does not support the claim — the support half of R5 made into a fixture).

`tools/bench_rag.py` is the runner: Day 20's env-switch discipline (`stub | data/store.json | code store`), `--k` pinned per corpus, `--baseline` writing Day 8/9's real-corpus numbers as the floor, and the R-report as its only output. The zero-human rule stands: expectations written before, verdicts written after, nobody reads a middle table to decide anything.

---

## 6. Build: The Semantic Judge

`ai_os/semantic_judge.py` is layer 2 of Day 20's `judge.py`, and it fills the three parked cells with one discipline. **Citation support**: does the cited doc state the claim's key terms — an evidence-containment probe by default (deterministic, free), the flagged lane as fallback. **Groundedness**: every claim contained-in or derivable-from the shown context. **Answer relevance**: coverage of `claims[]`, and a `must_not_assert[]` hit is always a fail.

The deterministic subnet is defined so the `MECH`/`PENDING` line cannot blur: a claim is **deducible** if, after normalization (stopword-fold, case-fold, term-Jaccard against the shown context — Day 9's `text_sim`, reused), it is contained or its key terms co-occur in the evidence the answer could have cited. Deducible is decided by arithmetic, byte-reproducible. Undeducible is *undecided* — recorded, owned, optionally explained by the flagged lane, never silently passed.

The flagged lane behind `EVAL_JUDGE` (default `deterministic`): bounded, budgeted, 1 chat per undecided claim, writing only to the `semantic:` column with `graded_by: model`, never flipping a `deterministic_verdict`, carrying the drift disclaimer (Day 20's V4, recalibrated per claim). The lane's first bill is a number in the report — the day *prices* the semantic tier before it spends on it, which is exactly how the cross-encoder question was always meant to be answered: with a receipt, not a promise.

The judge is run through its hostile rows first, Rule 3's way, so the happy rows earn nothing by comparison: the **F-row** (fluent but wrong) must fail groundedness; the **G-row** (fabricated citation) must fail `cited ⊆ retrieved` at Day 6's invariant; the **H-row** (citation-present, answer-unsupported) must fail coverage; the **K-row** must fail citation *support* while passing `cited ⊆ retrieved`. Four rows that prove the semantic tier catches what membership alone cannot, and that no amount of prose buys a pass.

---

## 7. Build: The τ Calibration and the Gate Flip

`tools/bench_rag.py --tau-sweep` is the answer to the oldest uncalibrated constant in the repo. Day 7 wrote `tau: 0.55` and called it "to be calibrated, not trusted"; Day 8 carried it for the fused score; Day 9 produced the first real data and the pre-registered worry was confirmed with numbers — E1/E2 fused top-1 at 0.854/0.874, overlapping the answerable family's top-1 range (~0.7–0.9), so the fixture τ = 0.55 silently passed both E rows. The sweep reports the separation (min-answerable − max-E), the τ that would maximize correct refusals, and the leak that τ costs.

The day's decision on its own data: **flip `should_refuse` into the pipeline only where real scores support a defensible τ.** Where the overlap is confirmed and no τ separates — as Day 9 predicted — the finding *is* the deliverable for the score-based gate, and the refusal verdict officially relocates to the semantic tier: an answer whose claims trace to no shown evidence is refused by code, never fabricated. Day 7's gate stays as the field arithmetic it always was; Day 21 confirms (or kills) the fused-score version and moves the final refusal call to the tier that can actually make it. The YAMLs close their oldest `PENDING`: `tau` becomes either a fitted number with its measured separation or an explicit `refusal: semantic` note carrying the real top-1 numbers — finally, in both `questions.yaml` and `code_questions.yaml`'s mirror.

---

## 8. Test: The R-Bench Fixtures (R1…R9) and the Control

Expected behavior written **before** the runs, one fixture per property plus the hostile ones and the control:

| ID | What it proves | Input / injection | Expected |
|---|---|---|---|
| R1 | recall@K | each answerable golden row against `top-K` (stub and real store) | `expected ⊆ top-K`; the real-store floor is Day 9's numbers — leaks are **reproduced and debited to retrieval**, never hidden |
| R2 | precision@K | rows with `relevant_also`; E-family and F/G/H rows with no relevance set | `(expected ∪ relevant_also) ∩ top-K / K` by membership; D1's 4-of-5-dupes hypothesis gets its number; `n/a` by construction where relevance is undefined |
| R3 | MRR | first-expected rank per row vs the pinned Day-8/9 baseline | ΣΔMRR with a **0-leak guard**; improvement ≠ different |
| R4 | retrieval relevance | mean `rel()` over kept context vs the flagged cross-encoder lane | free signal measured; if the paid lane runs, its Δ is **priced, not assumed**; off by default |
| R5 | citation correctness | `cited ⊆ retrieved` (MECH) and support-in-meaning (K-row) | membership passes; **the K-row fails support while passing membership** — the tier's reason to exist |
| R6 | groundedness | every claim vs the shown context | `claims ⊆ evidence`; the F-row's unsupported conclusion is a fail **by definition** |
| R7 | answer relevance | claim coverage over the golden set's `claims[]`/`must_not_assert[]` | coverage scored, `must_not` hit always fails; J1 is designed to fail it; the LOM sweep grades slot 1 vs 3 |
| R8 | τ calibration | E1/E2/CE1 fused top-1 vs answerable top-1 on the real store | a separation **or the documented finding**; the gate-flip decision lands in the YAMLs |
| R9 | **control** | the bench installed and unused | R-report byte-identical to the same rows' production traces; no fixture moved — **the bench doesn't fix answers** |

The `MECH`/`PENDING` split is Day 7's, exact as ever: **R1–R4, R5's membership half, R6's deducible subnet, R7's coverage arithmetic, R8's sweep, and R9 are all `MECH`** — offline, free, reproducible, the whole of the day's deliberative payload. **The `PENDING` cells are the semantic residue** — support-in-meaning, undeducible claims, the LOM real verdict — each with an owner (the flagged lane, off by default). The no-regression line runs Day 20's own way: `regress_all.py` with the bench **off** (baseline — the analyzer adds nothing) and **on** (the golden rows' R-report must agree with production traces, R9); a row the bench cannot reproduce is a **bench bug to fix**, never a fixture to weaken.

---

## 9. Measure: The R-Report and the Price of Semantics

Rule-4 as a table, one row per golden entry, every cell a real run's:

| ID | store | K | recall@K | precision@K | MRR | relevance | cited ⊆ retrieved | support | grounded | coverage | verdict | owner | tokens | latency |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A1…C2 | stub | … | ok | ok (relevant_also) | … | … | ok | ok | ok | ok | **pass** | — | 0-chat | … |
| A1…C2 | real | … | **LEAK (7 of 9 rows)** | n/a–low | 0.000–0.500 | low | ok on kept | … | … | … | **fail → retrieval (Day 8)** | retrieval | 0-chat | … |
| D1 | real/stub | … | 1.0 | **0.2 (the 4-of-5 prediction)** | 1.0 | crowded | ok | ok | ok | ok | **fail → context (Day 9)** | context | 0-chat | … |
| E1/E2/CE1 | real | … | n/a | **n/a** | n/a | — | — | — | — | — | **refusal verdict (R8)** | gate/τ → semantic | 0-chat | … |
| F/G/H | adversarial | … | — | n/a | — | — | **caught** | caught | **caught** | **caught** | **pass (rejected)** | judge | 0-chat | … |
| I1 | contradiction | … | ok | ok | ok | ok | ok | ok | ok | ok | **PENDING-human** | reader | 0-chat | … |
| K1 | near-miss quote | … | ok | ok | ok | ok | ok | **fails support** | ok | ok | **fail → support half** | semantic judge | 1-call (flagged) | … |
| R9 | control | … | — | — | — | — | — | — | — | — | **bench off ≡ days-without** | bench | 0-chat | — |

Three columns carry the day: **the `MECH` verdict on every row** (reproducible — the same trace graded twice is the same row), **the `n/a` cells honored** (E-family precision; undefined ≠ 0.0, because 0.0 would grade "refused to retrieve, pass"), and **the `PENDING`s with owners** (undecided claims, support-in-meaning, LOM). The real-store column is the day's headline and it is not flattering: the bench's first real act reproduces Day 9's leaks — the retriever on this corpus, at this K, does not surface seven of the nine expected docs — and the bench says it as a verdict with an owner (retrieval, Day 8's backlog) instead of a paragraph. That is the point: **the RAG benchmark is only worth building if it is willing to indict its own pipeline**, and the first real run does.

The cost row is honest in the series' ledger: the retrieval bench and the deterministic half of the semantic judge are **0 chats, 0 embeds**; the flagged lane bills **1 chat per undecided claim**, off by default; and the cross-encoder lane, if ever run, bills its price into the R4 column rather than the margin. The day *prices* semantics before deciding whether to pay for them — Rule 4's "at what cost," answered by column.

---

## 10. Documents

- `docs/concepts/evaluation.md` (Day 20's, standing) + `docs/concepts/rag-benchmark.md` (the day's concept): what "measure retrieval" means, precision's dependency on a pre-registered relevance set and its honest `n/a`, the D1 case (recall- and MRR-perfect yet polluted), claim-coverage as the honest bridge from the deterministic floor to the semantic tier, the price of semantics quoted before paid, and the τ-calibration finding on real scores.
- `docs/evaluations/bench-day21.md` — the §9 R-bench table and the R1…R9 fixture table with the R9 control row, both stores' rows, the τ-sweep numbers, the Day-8/9 baseline pins, the cost rows, and the `PENDING` line owned by Day 22/23/25.
- PROGRESS rollup: the retrieval bench and the deterministic semantic subnet move to `DONE` under the stub (R1–R4 + R5's membership half + R6/R7's deducible subnet + R8's sweep mechanics + R9, all verified offline); the *real-store* retrieval rows and the semantic residue stay `PARTIAL`/`PENDING` until a real key supplies the verdicts — the same white-court-until-real-key rule that has held every day since Day 6.

---

## 11. Daily Learning Record

### Problem

Nineteen days of retrieval work and a day-20 court that graded everything the trace could prove left two genuine holes standing exactly where the curriculum's Day 21 points: **retrieval had never been measured as a benchmark** — recall@K and MRR were `trace.py` fields hand-quoted into hand-written tables since Day 7, precision@K had never been computed by anyone because it needed a notion of *relevant* that only a pre-registered golden set can supply, and retrieval relevance had a free arithmetic since Day 9 and a price since Day 9 §7 but no bench to decide between them. And Day 20 peeled open the other half: the semantic cells — citation correctness, groundedness, answer relevance — were parked with the owner *"Day 21"* printed on each, and the only honest way to fill them was *not* a model's impression but **claim-coverage over the run's own evidence**, built on Day 20's pre-registered `claims[] + must_not_assert[]`. The deliverable is the **RAG benchmark** — and the discipline is that a benchmark that only re-reports what the two easy metrics already say is a report wearing a benchmark's uniform, so the day must make the two missing metrics writable, put the semantic tier on a determinism it can prove, and answer the oldest uncalibrated constant in the repo (τ) with its own real numbers.

### Concept

**Measurement is only as honest as the relevance it pre-registered; answer quality is only as honest as the claims it can trace.** The two legacy metrics (recall@K, MRR) existed because *expected* was pre-registered since Day 7; precision@K was unwritable because *relevant* was never pre-registered — Day 21's `relevant_also` set (on-topic docs that need not be retrieved, minted under the golden set's hash) turns precision from a judgment into membership arithmetic, and the honest `n/a` where relevance has no definition. Retrieval relevance inherits Day 9's free `rel()` signal and prices the paid cross-encoder lane instead of assuming it. The semantic tier is one discipline read three ways — **citation correctness** (cited ⊆ retrieved mechanically, support-in-meaning semantically), **groundedness** (every claim ⊆ shown evidence; a claim with no traced evidence is a hallucination by definition), **answer relevance** (pre-registered claim coverage; a `must_not` hit is a coverage failure of class *forbidden*) — with the deterministic subnet deciding the deducible claims and the flagged, budgeted, off-by-default lane pricing the undeducible residue without ever flipping a `MECH` verdict. τ is calibrated on real scores; where the E-family's fused top-1 refuses to separate from the answerable family's (as Day 9's data already predicted), the score-based gate is retired as the sole refusal signal and the refusal verdict moves to the semantic tier — Day 7's gate flip, executed by a bench. Everything is `MECH` and free where membership decides; everything `PENDING` has an owner, and the R9 control keeps the whole machine invisible — the bench doesn't fix answers.

### Understanding

The re-readings hit in order: **(1) Two of the four metrics were already code** — `expected_stats()` has computed recall@K and MRR since Day 7, and Day 7's §4 decision tree is literally a recall classifier; the day's build was never "build the metrics," it was "make the missing ones writable." **(2) Precision was never hand-collected because it *could not* be** — it needs a relevance judgment the ranking can't make and a human grader Day 20 banned; the golden set's `relevant_also` is not a nice-to-have, it is the missing premise that turns a judgment into arithmetic. **(3) The two legacy metrics are load-bearing but blind, and D1 is the exhibit** — recall 1.0 / MRR 1.0 on the row Day 7 pre-registered as 4-of-5-dupes; the pair reports perfect on a polluted context, which is the entire argument for precision. **(4) Retrieval relevance has been paid for since Day 9** — the free `rel()` arithmetic already spends on every candidate; the only open question was whether the paid cross-encoder beats it, and that is a question for a bench, not a note. **(5) The semantic tier Day 20 parked reduces to claim-coverage** — the claims form was built oracle-derivable and waiting for a grader; grading "did the answer assert the pre-registered claims and refuse the forbidden ones, both traceable to shown evidence" is the deterministic floor's natural continuation, not a leap into vibes. **(6) τ's finding was pre-registered before Day 21 existed** — Day 9's real numbers (E top-1 0.854/0.874 inside the answerable range) were the first data point for a sweep Day 21 was always going to have to run, and the overlap was always the likely verdict; the day executes the pre-registered outcome rather than discovering it.

### Implementation

- `ai_os/rag_bench.py` (new) — the metric core: recall@K/MRR via `trace.expected_stats`; `precision_at_k` over `expected ∪ relevant_also`; `retrieval_relevance` as mean `rel()` over kept context; every metric a pure function of a trace, 0 chats.
- `ai_os/golden.yaml` (extends Day 20's) — `relevant_also` sets minted from corpus structure for the answerable rows; honest `n/a` on E-family and F/G/H; the day's adversarial rows **I1 (contradiction), J1 (claim-leak), K1 (near-miss quote)**.
- `ai_os/semantic_judge.py` (new) — layer 2 of `judge.py`: citation support, groundedness, claim-coverage with the deterministic subnet (normalization + `text_sim`), the flagged per-claim lane behind `EVAL_JUDGE`, `deterministic_verdict` never flippable.
- `tools/bench_rag.py` (new) — the runner: `stub | real store | code store`, `--k` pin, `--baseline` writing Day-8/9 pins, `--tau-sweep`, R-report as the only output.
- `gate.py`/YAMLs — `tau` calibrated or declared `refusal: semantic` with real numbers; the gate-flip decision recorded in both `questions.yaml` and `code_questions.yaml`.
- `docs/concepts/rag-benchmark.md`, `docs/evaluations/bench-day21.md`.

### Failure

1. **Precision measured without a relevance set.** Someone computes `hits/K` against the required set alone and calls it precision — which penalizes expected docs for being retrieved alongside relevant (not required) noise, and reports D1's twins as errors they are not. Anti-fix: `relevant_also` or `n/a`, never a bare proportion pretending to be precision.
2. **Precision manufactured for rows with no relevance definition.** E-family and judge-hunting rows get a contrived relevance set so the column looks full; precision 0.9 on "nothing is relevant" is a metric lying about its own definition. Anti-fix: `n/a` by construction, and a hash-pinned relevance set that cannot be widened post-run (the curve-fit, in its precision form).
3. **Recall/MRR reported as improvement without the leak guard.** ΣΔMRR up, a green row red, and the report calls it better. Anti-fix: Day 9's 0-leak guard is a column, not a comment.
4. **The undecidable claim silently passed.** Claim-coverage reports 100% because the residue was skipped instead of parked. Anti-fix: *undecided* is a verdict state with an owner; the flagged lane may explain, never pass by omission.
5. **The semantic cell graded on a fresh generation.** The judge re-answers to "check" the run — double bill, baked-in noise, a verdict on an answer nobody gave. Anti-fix: Day 20's rule intact; cells read the trace's own context.
6. **τ flip forced on inseparable scores.** The gate is shipped with a τ that "works" by refusing everything (or nothing), converting a documented overlap into silent category errors. Anti-fix: the sweep's separation is the gate's precondition; where it fails, the refusal verdict relocates to the semantic tier and the YAML says so with numbers.
7. **The bench tunes to the known battleship.** The golden set is the training set; regression-hardening R1–R7 rows becomes rule-fitting until the bench only grades what it already fixed. Anti-fix: the rotating held-out slice is named, not built (a Day-30 concern), and the adversarial I1/J1/K1 rows are rebuilt, not reused.
8. **The R9 offense.** The bench improves answers — a judge in the loop, an emboldened retriever, a graded run running hotter than production. Anti-fix: byte-identical control, the V9 reflex on the benchmark surface.
9. **The LOM verdict parked again.** Best-first ordering stays a knob nobody grades. Anti-fix: R7 across the `best_first` sweep closes the oldest retrieval `PENDING`, on the bench's first real run.

### Debugging

Everything Day 21 adds is `MECH` under the stub: metrics over traces, membership over sets, coverage over claims, τ over arithmetic. The debug loop is Day 7's, re-rooted onto the bench:

- **A real-store row that "regresses"** → check the `K` first, not the pipeline — recall/precision are only comparable at the same pinned K; `k_from_budget` differences are a bench config bug, never a retrieval result.
- **A precision column that looks too good** → check the relevance set — was `relevant_also` widened after the run (the hash catches it, V1-style), or does the row have no defensible relevance and is pretending?
- **A row with recall 1.0 / MRR 1.0 that is still failing** → that is the D1 exhibit, not a bug — the bench found the pollution the two legacy metrics cannot see; the owner is context (Day 9), and the debug step is reading which of the 4/5 slots are near-duplicates.
- **A K-row that passes `cited ⊆ retrieved` and fails the row** → the support half owns it — the citation is real and wrong; the bench just proved the semantic tier's existence with one row.
- **A claim-coverage miss on a deducible claim** → the normalization (stopword-fold, `text_sim`) owns it — if the claim's terms co-occur in the shown context, the subnet failed; if they don't, the claim was genuinely undeducible and belongs to the flagged lane, not the bug book.
- **A τ sweep that finds a "separation"** → sanity-check it against the leak cost — a τ that refuses every answerable row at the 96th percentile of E is a τ that "works" by crafting a new failure; the flip precondition is separation *with* tolerable leaks.
- **An R-report that differs across identical runs** → the bench owns it (V2's rule): the deterministic subnet must be byte-reproducible; anything else is a bug in the bench, not a property of retrieval.

The trace says which, never the vibes; the metrics are deterministic; and every symptom has an owner from the day it was designed.

### Evaluation

The §9 tables in `docs/evaluations/bench-day21.md` — per golden-row verdicts with recall@K, precision@K, MRR, relevance, `cited ⊆ retrieved`, citation support, groundedness, coverage, `must_not`, owner, tokens, latency, and the R1…R9 fixture table. The numbers a future observer can carry: how many of the nine doc rows the real store actually retrieves at pinned K (Day 9 said 2 of 9 — the bench's first real act is confirming or correcting that debit), whether D1's pre-registered 4-of-5-dupes hypothesis produced the precision number it always predicted, whether any row's verdict changed when the bench re-ran (never — R9), whether any prior fixture moved (never — the regression gate), and whether the E-family refusal verdict finally has a defensible signal or a recorded finding that it must move to the semantic tier. The `PENDING` line, plainly: support-in-meaning, the undeducible claims, and the LOM real verdict are the semantic residue — owned by the flagged lane, off by default — and *agent* behavior (steps, tool selection, argument correctness, recovery, cost, latency) is **Day 22's** benchmark, standing on the same golden set the way Day 21 stood on Day 20's court.

### Architecture

The claim of the day is the series' claim, one layer further: **the system learned to measure what it built, and it did it by making the golden set the relevance oracle, not by building a new retriever.** No new engine exists — the metrics read `trace.py`'s fields, precision reads a set Day 20's discipline already knew how to mint, the semantic judge reads Day 20's pre-registered claims against the run's own evidence, and the honest `n/a` is a measurement choice the old hand-reports never had the authority to make. The bench is layered the way the court was: `MECH` first (recall@K, MRR, precision@K, the free `rel()` signal — each a pure function of the trace), semantics parked where membership cannot decide (support-in-meaning, undeducible claims, LOM), and the paid lane budgeted, off by default, and priced before spent. The two metrics that always existed are joined by the two that never could: precision, whose existence a pre-registered relevance set finally admits, and retrieval relevance, whose price the free arithmetic already pays. And the oldest promise in the retrieval line is finally kept — Day 7's τ is calibrated on real numbers, the pre-registered overlap verdict is executed rather than discovered, and the refusal decision either finds its defensible threshold or honestly moves to the tier that can make it. The seam is named: real *agent* behavior → **Day 22**; observability of the graded pipeline → **Day 23**; judge poisoning → **Day 25**; the fully-golden prose answer → *named, not built*. **Day 7 pre-registered nine questions and graded them by hand; Days 8–19 improved against them by hand; Day 20 built the court; and Day 21 is the first day the machine's grade is the answer instead of a human's — two metrics inherited, one minted by the golden set, one priced by the free arithmetic, the semantic tier reduced to traceable claims, and the refusal gate's oldest question settled by its own real numbers, all for $0, with the bench standing ready for Day 22 to ask the same hard questions of the agent.**

### Questions

- Claim-coverage's deterministic subnet works by containment and normalization — but a paraphrase that the evidence fully supports yet shares <30% tokens stays permanently undeducible, which means the paid lane is not optional for real generations. Is the flagged lane then *compulsory* for honest coverage (a cost the day priced but did not assume), or do we accept under-covered recall-of-truth as the price of staying free?
- Precision's `relevant_also` is itself a judgment — who mints it, and how is the set prevented from being *widened to flatter precision* (the golden-analog of curve-fitting)? Is the hash-pinned relevance set enough, or does the day need a structural constraint — `relevant_also ⊆ one hop from expected in the corpus graph` — to make inflation structurally impossible?
- The I1 (corpus-can-be-wrong) row grades grounded and relevant, and is still wrong. Is that the exact boundary where evaluation must finally call the reader human — or is a second-order metric (citation confidence × evidence reliability) the honest next experiment the day should have run?
- The LOM verdict measures whether slot 1 beats slot 3 in the R7 column — but ordering is a knob, not a fact, and one golden family's answer could overfit the `best_first` choice. When does the answer-relevance column stop revealing the pipeline and start confirming its own past?
- If score-only τ fails and refusal moves to the semantic tier, refusal no longer costs zero — a refused question bills its claim check while a gate-refused one bills nothing. Is a hallucination *prevented* worth a billed call, and should the semantic refusal lane default to on for exactly the failure it prevents, breaking Day 20's "all semantics off by default" rule for once?

### Tomorrow

Day 22 — Agent Evaluation: the bench's sibling, on the same golden set. The curriculum turns *measure agent behavior* into the columns Day 21's report already drew around them — **task success, tool selection, tool-argument correctness, number of steps, unnecessary calls, failure recovery, cost, latency** — the agent benchmark standing exactly where the RAG benchmark stood today, using the same pre-registered rows, the same verdict culture, the same `MECH`/`PENDING` split. Day 21 hands over the bench machinery, the pinned baselines, and the hard-won habit of letting the machine deliver the verdict; Day 22 asks of the agent what today asked of the retriever — not "does it seem better" but *measured*, on a fixed ship, at a known cost. Day 11 built the tools, Day 12 the loop, Day 13 the failure modes, Day 14–15 the data and its walls, Day 16 the plans, Day 17–18 the memory, Day 19 the architecture, Day 20 the court, Day 21 the retrieval verdict — and Day 22 is when the agent takes the stand. **The bench is built, the baselines are pinned, and the agent's verdicts belong to tomorrow.**