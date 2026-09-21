# Day 7 — RAG Debugging

## Objective

Understand why naive RAG fails. Day 6 built retrieve-then-generate and left its semantics marked `PENDING`; today stops trusting the pipeline, instruments it, attacks it with five families of questions, and produces a failure report that tells Day 8 and Day 9 exactly what to fix. **Today builds no features — it produces evidence.**

## Duties Outline

### 1. Learn: Why Naive RAG Fails (45 min)

- [ ] Reframe the day: Day 6 built the pipeline; Day 7 asks **where** it breaks. "The LLM was bad" is the last hypothesis to test, never the first.
- [ ] Name the four layers a wrong answer can come from — **query encoding**, **retrieval**, **context construction**, **generation** — and commit to attributing every failure to exactly one.
- [ ] Accept that failure is *expected*: a fixed 200/40 chunker, one embedded query, one embedding space, top-K with no reranking is the **naive** baseline — the plan calls it naive on purpose.
- [ ] The two ways a query misses a document: **lexical gap** (user says "cancel an order", the corpus says `orderCancel` / fulfillment) and **semantic gap** (user says "why is my cart stuck", the corpus says "checkout completion requires a shipping method").
- [ ] Why retrieval failure and generation failure produce **identical-looking output** — and therefore why they must be separated by experiment, not by opinion.

### 2. Learn: The Failure Taxonomy (45 min)

- [ ] Learn the graded ladder of retrieval outcomes: correct doc at rank 1 → correct doc buried → right topic, wrong section → right topic, **wrong entity** (`checkout` vs `checkoutComplete`) → topically adjacent noise → nothing relevant exists.
- [ ] The five classes to investigate, exactly as the plan lists them: **irrelevant retrieval**, **missing retrieval**, **excessive context**, **duplicated context**, **hallucination**.
- [ ] Map each class to the layer that owns it: missing/irrelevant → retrieval; excessive/duplicated → context construction; hallucination/false-refusal → generation; unanswerable → **corpus gap** (not a bug).
- [ ] Understand why "the answer was wrong" is a symptom, not a category — the category is *which layer* caused it.
- [ ] Get precise with the metrics vocabulary: hit@K, recall@K, precision@K, MRR, duplicate ratio, context utilization, top-1 score gap. (Formalized on Day 21; used *by hand* today.)

### 3. Design: The Failure Report & Debug Harness (45-60 min)

- [ ] Adopt the plan's deliverable format — `Question | Retrieved documents | Expected documents | Failure reason | Proposed fix` — one row per question, and **no row without a reason and a fix**.
- [ ] Decide the instrumentation *before* the questions. Per query, log: (a) question, (b) top-K with `doc_id`/`heading`/score, (c) expected doc set, (d) is expected ∈ retrieved, and at what rank, (e) context char count + duplicate count, (f) answer + citations, (g) verdict.
- [ ] Design the **oracle substitution** test: bypass retrieval entirely and inject the *expected* document into the prompt. Same question, perfect evidence — if it still fails, the fault is generation; if it fixes, the fault is retrieval.
- [ ] Design the **retrieval-only** test: no LLM at all. Does the expected chunk appear in top-K? Deterministic, runs without a chat model.
- [ ] Pre-commit the thresholds: what top-1 similarity counts as "no good match" (the out-of-corpus gate), and what duplicate ratio counts as context pollution.

### 4. Build: The Question Set (1-1.5 hours)

- [ ] **Family A — obvious:** the answer sits in one page ("What is a checkout in Saleor?"). These must pass; if they don't, nothing downstream is measurable.
- [ ] **Family B — multi-document:** the answer is assembled from 2+ pages ("How does a checkout become an order, and what changes after?"). Stresses top-K breadth and the overlap seam.
- [ ] **Family C — ambiguous terminology:** one word, several meanings in the corpus ("How do apps work?", "channels", "attribute vs field", "product vs variant"). Stresses whether one query vector can pick a sense.
- [ ] **Family D — similar documents:** near-duplicate pages (`checkout` vs `checkoutComplete`, overlapping guide pages). Stresses ranking stability and result diversity.
- [ ] **Family E — out of corpus:** no page can answer ("How much does Saleor Cloud cost?"). Stresses the refusal path — the model must not invent.
- [ ] For every question write the **expected source down before running it**. Expected-after-the-fact is curve-fitting, not a report.

### 5. Run: The Experiments (1.5 hours)

- [ ] Run every question against Day 6's `/rag`, logging retrieval *and* answer.
- [ ] Classify each outcome: retrieval miss / retrieval noise / context pollution / generation failure / corpus gap.
- [ ] Record the score distribution per question — the **top-1 cosine** is the out-of-corpus signal, and the **top1 − top5 gap** says whether there was a real winner.
- [ ] Capture the failure *shape*: always the same doc family? Always the same K? Always the same rank? Repetition is a diagnosis.
- [ ] Check where overlap chunks reappear as duplicates in the built context, and whether they crowd out better chunks (Day 6 §5 assumed "harmless at K=5" — test it).

### 6. Debug: Attribute Each Failure (45-60 min)

- [ ] For every wrong answer, run both isolation tests from §3 — retrieval-only, then oracle substitution.
- [ ] Assign the cause with evidence, not intuition: expected doc absent from top-5 → retrieval; present at rank 4 but dropped by the budget → context construction; present in context and still answered from priors → generation.
- [ ] Separate **retrieval failure** from **grounding failure** explicitly: retrieval can be perfect and the answer still wrong — the case that proves RAG is two systems, not one.
- [ ] Distinguish a genuine model failure from an unanswerable question. A question the corpus cannot answer is a **corpus gap**, and refusing is the *correct* behaviour.

### 7. Propose Fixes — No Implementation (45 min)

- [ ] Write a **proposed fix per row**. A diagnosis with no next action is a complaint.
- [ ] Route each fix to the day that owns it: lexical gap → hybrid keyword+semantic (Day 8); ambiguity → query rewriting (Day 8); multi-hop → multi-query decomposition (Day 8/16); near-duplicates and lost-in-the-middle → reranking + context ordering (Day 9); duplicate seam → context dedup (Day 9); out-of-corpus → similarity gate + explicit refusal (shippable today, measured Day 21).
- [ ] Rank the fixes by expected impact vs effort — the report should end with a **shortlist**, not a backlog.
- [ ] Resist adding features today. The deliverable is the report, not the fix.

### 8. Test: Turn Failures Into Fixtures (45 min)

- [ ] Promote each confirmed failure into a **regression fixture**: `(question, expected_doc_ids)` asserting `expected ⊆ retrieved` — runs without an LLM.
- [ ] Keep the oracle test as a fixture too: `(question, oracle_context, expected_answer_property)` for the generation half.
- [ ] Verify the mechanical checks deterministically against the stub gateway — budget overflow, duplicate detection, citation ⊆ retrieved, threshold-gate arithmetic.
- [ ] Mark every check that is **semantic** and cannot be asserted on stub vectors as `PENDING` — those become Day 20/21's evaluation set.

### 9. Documentation (20 min)

- [ ] Write `docs/failures/rag-naive.md` — the filled failure report plus the shortlist.
- [ ] Cross-check it against `docs/concepts/rag.md` (Day 6): that document describes the happy path; this one is its counter-evidence.
- [ ] Fill the Day 7 Daily Learning Record (Problem / Concept / Understanding / ... / Tomorrow).
- [ ] Record today's token economics: the cost of *debugging* — each experiment is one retrieve plus one generate.

## Deliverable

The failure report, produced by this loop:

```text
Question set (A obvious · B multi-doc · C ambiguous · D similar · E out-of-corpus)
        ↓
Per question, capture the retrieval trace:
        Query ─▶ embed ─▶ top-K (doc_id, heading, score)
        ↓
Is the expected doc in top-K?           ── no ─▶ MISSING RETRIEVAL   ─▶ Day 8
        │ yes
        ▼
Did it survive the context budget?       ── no ─▶ CONTEXT FAILURE    ─▶ Day 9
        │ yes
        ▼
Oracle test: feed the expected doc alone
        │ still wrong
        ▼
GENERATION FAILURE ─▶ prompt / grounding ─▶ Day 20/21 evaluation
        │
No page can answer it ─▶ CORPUS GAP — refusing is correct behaviour

        Output ──▶
        Question | Retrieved documents | Expected documents | Failure reason | Proposed fix
```

## Exit Check — answer in your own words

- A wrong answer came back — how do you decide whether retrieval or generation is at fault, *without guessing*?
- What does it mean for the expected document to be *retrieved but not used*, and which layer owns that?
- Which failure class is not a bug at all, and what is the correct system behaviour for it?
- Why does a low top-1 similarity score matter more than a low top-K score?
- What does today's report give Day 8 and Day 9 that "it seems worse" never could?
