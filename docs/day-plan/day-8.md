# Day 8 — Retrieval Improvements

## Objective

Improve retrieval quality. Day 7 produced a failure report whose `Proposed fix` column is this day's work plan: hybrid keyword + semantic search for the lexical gap, query rewriting for ambiguity, multi-query decomposition for the multi-hop questions, metadata filtering to shrink the search space. **Today does not build new features — it fixes the retriever, re-runs the same nine questions, and hands Day 9 a comparison it can improve on.** The deliverable is evidence of improvement, not the feeling of it (Rule 4).

## Duties Outline

### 1. Learn: What "Improved Retrieval" Really Means (45 min)

- [ ] Accept the premise Day 7 earned: the naive pipeline fails in *predictable patterns* (lexical gap, ambiguity, multi-hop, near-duplicates, out-of-corpus) — and each pattern has a *retriever-side* fix. Nothing in Day 8 touches the LLM.
- [ ] Define the target precisely: same questions, same expected doc sets, strictly better `recall@K`, `MRR`, and duplicate ratio — never "the answers looked better".
- [ ] Separate the two halves of "better": **recall** (did the right chunk enter top-K?) and **precision/rank** (did it win a top slot?). Hybrid search mostly buys the first; you are building towards the second without reranking (that is Day 9).
- [ ] Learn why lexical and semantic search are complements: the vector space captures meaning but averages senses and misses rare tokens; keyword scoring captures exact tokens but has no notion of paraphrase. Fusing them means a chunk wins on *either* evidence.
- [ ] Pre-commit the fusion design: a tunable `alpha` that interpolates normalized lexical and semantic scores, and an explicit rule for how alpha is chosen (not by vibes — by which value makes Day 7's fixtures pass with a margin).

### 2. Learn: The Four Moves — Hybrid, Rewrite, Multi-Query, Filter (45 min)

- [ ] **Hybrid keyword + semantic search.** BM25-style token scoring plus cosine, fused per chunk. Hand-rolled and stdlib-only (no `rank-bm25`): a token-overlap IDF score over the query's tokens kills the lexical gap — "cancel an order" finally matches `orderCancel`.
- [ ] **Query rewriting.** One user string, many intents. A rewrite step converts the *user's words* into the *corpus's words* ("cart is stuck" → "checkout requires shipping method") either deterministically (rule table) or by the gateway (`PENDING`). The rewritten query is what gets embedded.
- [ ] **Multi-query decomposition.** A question that needs two documents embeds as *one* averaged vector and loses half its intent. Decompose the question into sub-questions, retrieve each, fuse the ranked lists (max-score merge), dedup.
- [ ] **Metadata filtering.** Chunks already carry `doc_id`/`source`/`heading`/`position` (Day 6 §2). Filtering narrows the search space before scoring: e.g. restrict to a section, or (later) use the rewrite step to emit a section when the query names one.
- [ ] Understand the cost of each move: rewrite = one extra embed (cheap); multi-query = one extra embed **per sub-question** (each sub-question's embedding is a separate gateway call — ledger lines); hybrid = an extra tokenizer pass over the corpus (sub-millisecond at 1,000 chunks). These are the numbers Day 8 records.

### 3. Design: The Retrieval Layer Contract (30 min)

- [ ] Keep the seam Day 6 left open: `store.retrieve(qvec, k)` is the only call `/rag` touches. Day 8's changes stay *behind* that seam so `/rag` and Day 9 never care which retriever was used.
- [ ] Design a single `retrieve(question, k, mode=...)` that owns the full flow: rewrite → decompose → embed → (filter) → score/fuse → top-K, returning the same `[(score, chunk)]` shape `build_context` already consumes.
- [ ] Score fusion must produce **comparable units**: cosine is [−1, 1], lexical counts are unbounded. Define normalization before any weighted sum; decide how an out-of-corpus query maps through the fused score (lexical ≈ 0 when no token matches — which *preserves* Day 7's gate rather than defeating it).
- [ ] Config, not constants: `alpha`, metadata filter, and a max-sub-question cap live in one place. Every knob is a number Day 8's comparison can sweep.
- [ ] Regression contract: Day 7's `fixtures.py` (`expected ⊆ retrieved`, `must_not`, `must_refuse`, `max_dupes`) must stay green on the improved retriever *and* fail on the baseline the way the report said they would.

### 4. Build: Hybrid Keyword + Semantic Search (1-1.5 hours)

- [ ] Implement a token scorer over the store: tokenize (lowercase, split on non-word chars, drop stopwords), count per chunk, get IDF per token across the corpus, score each chunk as the sum of IDF of its matched query tokens.
- [ ] Normalize the lexical score across the corpus (divide by the max score seen) so it lands in the same band as cosine.
- [ ] Fuse: `fused = alpha * semantic + (1 − alpha) * lexical`, single pass, deterministic. Verify against Day 7's fixtures at `alpha ∈ {0, 0.25, 0.5, 0.75, 1}` and record which value clears every fixture.
- [ ] Re-run the lexical-gap families first (Day 7 C1, E2): the whole point of the move is that "cancel an order" stops depending on the embedder's mercy.
- [ ] Keep the out-of-corpus gate intact: a query sharing zero lexical tokens and low cosine must still refuse, not acquire a bogus lexical boost.

### 5. Build: Metadata Filtering (45 min)

- [ ] Expose a `filter` on retrieval: `retrieve(..., filter={'doc_prefix': 'developer/checkout'})` — narrows candidate chunks before scoring.
- [ ] Wire the cheap, deterministic source for a filter: a rule table mapping user phrasing to a section prefix ("checkout", "order", "apps", "payments").
- [ ] Verify filtering never *loses* Day 7's expected docs when the filter is right, and that a wrong filter degrades gracefully (falls back to unfiltered, annotated as a filter-miss).
- [ ] Note exactly where a real classifier/LLM extraction would plug in (`PENDING`), but do not ship the `PENDING` half today.

### 6. Build: Query Rewriting + Multi-Query Decomposition (1 hour)

- [ ] Build `rewrite(question)` — deterministic rule table first ("cart stuck" / "cancel order" / "fix checkout" → corpus vocabulary), with the gateway-based rewrite as a `PENDING` extension point that hands back a rewritten string.
- [ ] Build `decompose(question)` — split on coordination ("and", "what changes after", "then") into sub-questions, cap at a configurable N (default 3) to bound cost.
- [ ] Multi-query retrieval: embed each sub-question, retrieve top-K per sub-query, merge by max-score per chunk id, dedup chunk ids, re-score, return top-K. Record how many extra embed calls a multi-doc question now costs.
- [ ] Verify the shape of the fix with Day 7's Family B (B1: "how does a checkout become an order, and what changes after?"): the second sub-query exists *precisely* to drag the completion/order page into top-K.

### 7. Test: Re-run Day 7's Nine Questions (1-1.5 hours)

- [ ] Re-run all nine questions (A1, B1, B2, C1, C2, D1, D2, E1, E2) against the improved retriever with the *identical* trace record from Day 7 §4: top-K with ids/headings/scores, expected set, duplicate ratio, context utilization.
- [ ] Record a column-by-column diff vs the baseline: which questions now retrieve their expected doc, at which rank, and which still don't.
- [ ] Where a row is *still* broken, re-attribute it with Day 7's isolation tests — if the expected doc now arrives but the answer is still wrong, the failure has moved from retrieval to context (that is Day 9's backlog, and the report must say so).
- [ ] Confirm E1/E2 still refuse: the improved retriever must not introduce a lexical false-positive that feeds the out-of-corpus question.
- [ ] Sweep `alpha` and record the metric row per setting so the alpha choice is evidence, not taste.

### 8. Measure: Baseline vs Improved (45 min)

- [ ] Enforce Rule 4 in one table: | question | baseline recall@K | improved recall@K | baseline MRR | improved MRR | fixed-by | — one row per question, every number a real run's.
- [ ] Report which failure class dropped off (lexical gap gone?) and which survived (near-duplicates only get *worse* with hybrid scoring — they share tokens — so D1/D2 may still need Day 9's reranking).
- [ ] Record latency and token cost delta: hybrid = tokenizer + fused score, multi-query = +N embed calls. Improvement that doubles cost is a trade, not a win.
- [ ] Also add the negatives: *did not fix* rows, with the owning day attached. Day 9's backlog is that column.

### 9. Documentation (20 min)

- [ ] Write `docs/concepts/retrieval.md` — hybrid scoring, rewriting, decomposition, filtering, and the fusion formula with `alpha`.
- [ ] Write `docs/evaluations/retrieval-day8.md` — the baseline-vs-improved table plus the `alpha` sweep.
- [ ] Fill the Day 8 Daily Learning Record (Problem / Concept / Understanding / ... / Tomorrow).
- [ ] Record today's token economics: what each retrieval strategy now costs per request (embed calls, tokenizer pass) versus Day 6's single embed.

## Deliverable

An improved retriever and the comparison that proves it. Baseline (Day 6) versus improved (Day 8), on the same nine questions:

```text
Day 7 fixtures  ──▶  improved retriever ──▶ trace record (same shape as Day 7)
   (expected sets)        │
                          ├─ rewrite(question)          rule table + PENDING gateway
                          ├─ decompose(question)        max 3 sub-questions
                          ├─ embed per sub-query        gateway (ledger per call)
                          ├─ filter by section          if rule table matches
                          └─ fuse: alpha·sem + (1−α)·lex
                                   │
                                   ▼
                             top-K ──▶ build_context ──▶ /rag (unchanged)
                                   │
                                   ▼
              Baseline vs Improved:
              Question | base recall@K | new recall@K | base MRR | new MRR | fixed-by / owner
```

Days 9 (`docs/failures/rag-naive.md` + this comparison's *unfixed* rows) get a backlog; Day 21 gets the harness that will call this a benchmark.

## Exit Check — answer in your own words

- What failure class does hybrid search fix, and what does it *not* fix? Why does it make near-duplicates worse?
- Why must lexical and semantic scores be normalized before fusing, and what happens if one dominates?
- When is query rewriting mandatory, and when is it wasted work?
- What does a second sub-query cost, and how do you decide the decomposition cap?
- What did "improved" mean today, in numbers, and which row refused to improve?