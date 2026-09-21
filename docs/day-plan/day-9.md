# Day 9 — Reranking and Context Construction

## Objective

Improve the final evidence passed to the model. Day 8 fixed the retriever's *reach* (recall) and explicitly left the *order and composition* of the evidence untouched — its comparison ended with an `Owner if open` column: near-duplicate crowding (D1), duplicated context (D2), wrong-entity ordering (B2/C2), lost-in-the-middle. Today owns that column: a reranker that re-judges the candidates for **relevance** (not just closeness to the query vector), MMR diversity that stops near-duplicate twins from swallowing top-K, and a context builder that orders, dedups, and compresses under the budget. **Today changes no corpus, no embedder, no gateway — it decides which evidence wins and where it sits. The deliverable is Day 8 vs Day 9 numbers on the same nine questions.**

## Duties Outline

### 1. Learn: "Found" and "Best" Are Two Different Judgements (45 min)

- [ ] Accept the premise Day 8's comparison earned: the fused score (`α·cos + (1−α)·lex`) is *optimized to find* — to get the right chunk into top-K. Whether it should then sit at **rank 1** is a separate decision. D1 proves it: a near-duplicate twin can out-score the real how-to page because the twins share everything — tokens and vector region.
- [ ] Define the reranker: a second pass over the *candidates* that can afford signals the first pass couldn't — "the query's rare token is in this chunk's heading", borrowed from metadata the pipeline stored but never spent (Day 8 §5 showed the pattern with filtering).
- [ ] Learn the relevance-scoring families: **pointwise** (score each candidate), **pairwise** (compare pairs), **listwise** (judge the ordering). Day 9 hand-rolls the pointwise kind — cheap, deterministic; the gateway-backed cross-encoder is pointwise-but-semantic and stays `PENDING`.
- [ ] The cheater's question that keeps the day honest: *was the ranking a computed claim or a byproduct?* If rank order is just sort-by-retrieval-score, there is no reranker — today must change at least one row's order by evidence a first pass wouldn't have seen.
- [ ] Pre-commit the target: same questions, same expected doc sets, strictly better MRR and duplicate ratio than Day 8 — and the Δrank column that proves the reranker actually re-ranks.

### 2. Learn: Context Construction Has Three Jobs, and Day 6 Only Did One (45 min)

- [ ] Day 6's `build_context` did **budget** (rank-order floor truncation) — one job. The other two: **ordering** (does evidence slot matter?) and **compression** (which chunks are redundant / can be dropped?). Day 7 flagged lost-in-the-middle `PENDING`; Day 8 handed it forward. Today it stops being a paper claim and becomes a measured variable.
- [ ] Lost-in-the-middle: attention over a long context is not uniform. Experiment: hold question + evidence fixed, slide the answer chunk from slot 1 to slot K, compare correctness. If position changes the outcome, ordering is an engineering decision, not a nicety. (Protocol `MECH`; the semantic verdict `PENDING` until a real chat run.)
- [ ] Say why compression is the *dangerous* job: budget-cutting a chunk that holds a unique fact silently re-breaks recall **in the context layer** — retrieval did its job, the answer just got thrown away. Compression must be evidence-weighted (drop lowest-relevance redundant chunks first), never character-counted, and every drop must be reported (Day 7's context-utilization trace).
- [ ] Dedup is Day 7's duplicated-context fix: the overlap seam and the near-duplicate pages (D2) occupy slots and dilute the budget. Caution: two chunks sharing 60% of tokens can carry different facts — dedup by *redundancy* needs a guard, or "compression" becomes the new deletion bug.
- [ ] Understand how rerank and context can fight: rerank says "this chunk matters most", the budget says "it doesn't fit". Day 9's design makes the budget consult the rerank score — never the reverse.

### 3. Design: The Two Seams, and What Stays Behind Them (30 min)

- [ ] Two seams, both intentionally narrow — the Day 6–8 contract holds (`/rag` and the gateway don't change a line):
  1. `rerank(candidates, question, k)` — between retrieval and context; `[(score, Chunk)] → [(score, Chunk)]`.
  2. `build_context(reranked, budget)` — ordering, dedup, compression; same signature Day 6 had.
- [ ] Refine a Day 8 claim honestly: Day 8 predicted `build_context` would stay untouched. Day 9's design decision is that the *seam* — inputs, outputs, budget invariant, `citation ⊆ retrieved` — is the contract, and the *policy inside* is Day 9's to change. Document that divergence instead of smuggling it.
- [ ] Fix the pipeline order: `retrieve (Day 8) → rerank → MMR/dedup → budget + order → /rag`. Whether MMR runs before or after dedup is itself a config choice — and a measurable one.
- [ ] Config, not constants: reranker signal weights, MMR `λ`, `best_first`, dedup `sim_tau`, compression rule. Every number Day 9's sweep can move.
- [ ] Regression contract (symmetric with Day 8): Day 8's green rows stay green; the `Owner if open` rows are where a change is *allowed* to land; E1/E2 must still refuse — a reranker that manufactures a false-positive for an out-of-corpus question is a regression wearing an improvement's clothes.

### 4. Build: The Cheap, Deterministic Reranker (1-1.5 hours)

- [ ] Hand-rolled pointwise reranker over the Day 8 fused top-K (stdlib only, Rule 1). Signals per candidate:
  - **fused score** — carry Day 8's α-fusion forward (the find-score stays relevant, it just stops being the only voice).
  - **heading signal** — the query's highest-IDF token appears in the chunk's `heading` (metadata, spent like Day 8's filter). Targets rung 3 (`right topic, wrong section`) and the wrong-entity rows (C2).
  - **rare-token signal** — IDF-weighted overlap from Day 8's `Lexicon`, concentrated on the tokens that discriminate (`checkoutComplete`, not `how`).
  - **position** — treat late `position` as a mild tie-break only, never a verdict.
- [ ] Combine into `rel(c) = w_fused·fused(c) + w_heading·heading(c) + w_rare·rare(c) + w_pos·pos(c)`, weights configurable, deterministic, testable.
- [ ] Verify a reorder *before* touching anything else: C2 (definition pages fall below the reference page once the heading fires) and D1 (the how-to page rises above its twin once rare-token + heading agree).
- [ ] Write the `PENDING` tier as a seam, do not ship it: a gateway-backed cross-encoder `relevance(query, chunk)` — one chat call per candidate, real, expensive, and placeable later.

### 5. Build: MMR Diversity + Context-Level Dedup (45 min)

- [ ] Maximal Marginal Relevance, greedy: `cand = argmax_c [ λ·rel(c) − (1−λ)·max_{j∈kept} cos(v_c, v_j) ]`, top-K at a time. The chunk vectors already exist (Day 5 index) — diversity enters as arithmetic on stored vectors, not new embeds.
- [ ] Why MMR is the *specific* D1 fix: twins carry high mutual cosine and get penalized; the how-to page, distinct from the twins, survives. State it as `MECH` — it is a pure function of stored vectors and rel scores.
- [ ] Context-level dedup: over the *selected* set, drop a chunk whose string similarity to a kept chunk exceeds `sim_tau` (D2) — *unless* it is the top-relevance chunk (the guard: a near-duplicate that *is* the answer is not dropped).
- [ ] Verify D1/D2 fixtures: `max_dupes` (Day 7) now enforced at the context layer while the expected `doc_id` is still present after MMR culls the twins.
- [ ] Verify the MMR trap: B1's two documents are similar *by design* (checkout-create + completion). If MMR culls B1's second doc, `λ` is too aggressive — record the λ where B1 still survives.

### 6. Build: Ordering + Compression Under the Budget (1 hour)

- [ ] Ordering: reranked+MMR order is the evidence's *claim*; presentation order is the model's *attention*. Default `best_first=True` — the highest-relevance chunk leads. The lost-in-the-middle experiment measures whether that default earns its keep.
- [ ] Compression, three rules in order: (1) dedup already ran; (2) drop lowest-relevance chunks that don't fit, never the budget-protected top chunk; (3) report every drop so utilization stays a trace field, not a guess. Day 6's rank-order truncation becomes a special case and is retired if the experiment shows the new rule wins.
- [ ] Keep the citation invariant green: `cited ⊆ retrieved`; a dropped-but-cited chunk is a *retrieval hit and context miss* — the Day 7 trace already distinguishes them.
- [ ] Run the lost-in-the-middle protocol (slot sweep 1→K, correctness ratio) and attach a number to the ordering knob. `MECH` for the protocol, `PENDING` for the semantic verdict.

### 7. Test: Re-run the Nine Questions (1-1.5 hours)

- [ ] Same nine questions, same trace record shape (Day 7 §4) — plus `Δrank` per expected doc: pre-rerank rank vs post-rerank rank. A reranker that doesn't change any row's rank has no evidence of existing.
- [ ] Column-by-column vs Day 8: which `Owner if open` rows landed and at what rank; which stayed open.
- [ ] Re-attribute survivors with Day 7's isolation tests: doc now ranks 1 and the answer is still wrong → generation read the right evidence and lost it (→ Day 20/21, the report must say "moved"); doc ranks 1 and the answer is right → closed; doc still missing → retrieval owned it after all, return it to the Day 8 backlog.
- [ ] Keep E1/E2 refusing: rerank + MMR must not manufacture evidence for "Saleor + Shopify". Day 8's warning — a fix that "improves" by feeding the out-of-corpus question a plausible match is a regression — is now the reranker's to answer.
- [ ] Sweep `{λ, weights, best_first, sim_tau}` and record per-setting metric rows. Knob choices are evidence, not taste (Day 8's α discipline, applied again).

### 8. Measure: Day 8 vs Day 9 (45 min)

- [ ] Rule 4 in one table: | question | Day 8 MRR | Day 9 MRR | Day 8 dup-ratio | Day 9 dup-ratio | Δrank | owner if open | — one row per question, every number a real run's.
- [ ] Headline rows are the ones Day 8 *predicted would not improve*: D1 (hybrid was neutral-or-worse; rerank+MMR is the counter-move) and D2 (dedup). If they didn't move, Day 9's claim fails honestly in the table.
- [ ] Latency + cost delta: rerank/MMR/dedup are arithmetic over stored vectors — record the microseconds and confirm **zero** new embed/chat calls vs Day 8. Put the `PENDING` cross-encoder's price (N chat calls) explicitly next to the numbers.
- [ ] Keep the negatives column — it is the backlog Day 10 (code RAG) inherits: what did not move, why, and who owns it.

### 9. Documentation (20 min)

- [ ] Write `docs/concepts/reranking.md` — pointwise rerank, MMR and λ, ordering/compression/dedup, the two seams.
- [ ] Write `docs/evaluations/rerank-day9.md` — the Day 8 vs Day 9 table plus the λ/weight sweep.
- [ ] Fill the Day 9 Daily Learning Record (Problem / Concept / Understanding / ... / Tomorrow).
- [ ] Record today's token economics: Day 9 adds zero per-request embeds and chat calls (arithmetic rerank); write the cross-encoder's `PENDING` price beside it.

## Deliverable

The evidence-quality half Day 8 explicitly deferred, on the same nine questions:

```text
Day 8 fused top-K ─▶ rerank(cheap signals) ─▶ MMR(λ) ─▶ build_context(budget: order+dedup+compress)
   (find)                 (judge)                (diverse)       (present)
        │                     │                     │                 │
        └── Δrank ∈ the trace record for every expected doc (proves the reranker exists)
             │
             ▼
     Day 8 vs Day 9, same nine questions:
     Question | D8 MRR | D9 MRR | Δrank | dup-ratio D8→D9 | owner if open
```

Day 8 found the evidence; Day 9 decides which evidence wins and where it sits. Whatever is still open goes into Day 10's backlog (`docs/failures/rag-naive.md` + this comparison's unfixed rows + a brand-new corpus: Saleor source code — where "chunk" stops meaning prose and starts meaning symbols).

## Exit Check — answer in your own words

- What does the reranker know that the first-pass score doesn't — and why couldn't the first pass just use it?
- Why is MMR the specific fix for D1, and what does it risk for B1?
- How does compression differ from truncation, and how do you stop it from dropping the answer?
- What experiment turns "lost-in-the-middle" from a paper claim into a number in your table?
- Which Day 8 rows were you allowed to move, and what did "improved" mean today, in the numbers?