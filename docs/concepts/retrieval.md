# Concept: Retrieval as a Layer

> Day 8 of the AI-layer apprenticeship. The retriever stops being "embed, scan,
> sort" and becomes a layer with its own configuration — its own knobs, its own
> failure classes, its own arithmetic. Day 7 proved the naive pipeline fails in
> four *predictable* patterns; Day 8 builds a retriever that answers each one.
> Implementation: `ai_os/lexicon.py` + `ai_os/retrieval.py`.

## Problem

Day 6's retriever was one function: embed the query, cosine-scan the store,
take top-K. Day 7's failure report showed that shape fails in *patterns*, and
each pattern is a *retriever-side* fix:

| Day-7 failure | Example | What broke | The Day-8 move |
|---|---|---|---|
| Lexical gap | "how do I cancel an order" never meets `orderCancel` | cosine lives on meaning, the *tokens* are the answer | **hybrid** keyword + semantic |
| Ambiguity | "how do apps work?" averages the senses of *app* | one vector for many intents | **rewrite** + **metadata filter** |
| Multi-document | "checkout becomes an order, and what changes after?" drops half | one vector lands *between* intents | **multi-query** decomposition |
| Out-of-corpus | "how much does Saleor Cloud cost?" | K neighbours returned anyway | keep the Day-7 **gate** intact |

The discipline: every move is a fix for a *named* failure class, carries a
*measured* cost, and is proven on the same nine pre-registered questions —
"better" means strictly better `recall@K`/`MRR`/duplicate-ratio on a re-run,
never "the answers looked better".

## The seam (what didn't change)

Day 6 left one seam: `/rag` calls `store.retrieve(qvec, k)`. Day 8 widens it
to own the *query side* too, but the output is the same `[(score, Chunk)]`
shape `build_context` consumes — so `/rag`, the gateway, and Day 9's reranker
never know which retriever produced the top-K:

```python
retriever = Retriever(gateway, store, alpha=0.5)
results = retriever.retrieve("How do I cancel an order?", k=5)
# [(score, Chunk)] — identical shape to store.retrieve(...)
```

`retriever.retrieve(..., mode="semantic")` reproduces Day 6 verbatim (α=1, no
moves), which is what makes baseline-vs-improved an apples-to-apples
comparison instead of two functions pretending to be one.

## The fusion formula

```text
chunk_score = α · cosine(qvec, chunk.vector)   # one embed, meaning
            + (1 − α) · lexical(query, chunk)  # IDF-over-tokens, no embed
```

Two conditions make that one line honest:

1. **Both halves must be comparable units.** Cosine lives in [−1, 1]; a raw
   token-count sum is unbounded. The lexical scorer therefore normalizes by
   the corpus max *per query* into [0, 1] — otherwise whichever scale is
   bigger silently becomes the only scale and α is a lie.
2. **α is a swept knob, not a taste.** `tools/retrieval_day8.py --sweep` runs
   the nine questions at α ∈ {0, .25, .5, .75, 1} and records the metric row
   per setting. The winner on a real corpus is the value that clears every
   answerable fixture *and* keeps the E-family refusing.

## The lexical half (why stdlib)

No `rank-bm25` — the scorer is ~50 hand-written lines because the whole point
is that every trade-off is *owned and declared*:

- **IDF over set-membership, not counts.** A chunk saying "checkout" six times
  is not six times more relevant. Chunk term-sets and query token-sets both
  saturate, so repetition can't launder relevance.
- **Stopwords dropped before matching.** "How do I cancel an order" →
  `{cancel, order}`. `how/do/i` correlate with nothing.
- **Normalize by corpus-max per query.** A query with zero corpus-token
  overlap scores all zeros — the refusal path in lexical disguise. This is
  what keeps the improved retriever from manufacturing evidence for an
  out-of-corpus question.
- **IDF is built once at construction** (a single pass over the store), then
  scoring is pure per-query arithmetic — sub-millisecond at 10k chunks.

## The three query-side moves

**Rewrite** — one user string, many intents. A deterministic rule table maps
user phrasing to the corpus's mechanism words before embedding (`"why is my
cart stuck" → "checkout requires a shipping method and valid addresses"`). The
gateway rewrite tier (one chat call per query) is the declared `PENDING`
extension point; the rule table is the version whose behaviour is a test.

**Decompose** — split on coordination (`and`/`then`/`but`/`or`), embed each
sub-question, retrieve each, max-score-merge per chunk id, dedup. Cost: *one
extra embed per additional sub-question* — a real ledger line. The cap
(default 3) is a budget knob exactly like K was in Day 6. Today's split is
lexical; the semantic planner is Day 16's job.

**Filter** — chunks have carried `doc_id/source/heading/position` since Day 6;
filtering spends that metadata *at query time*. `rule_section` maps query
phrasing to a section, but only fires when the query names *exactly one*
section — an ambiguous query gets no filter rather than a filter flip. A
section's prefixes list every corpus folder where its docs live (checkout
spans `developer/checkout/` *and* `quickstart/api/`), so a right filter can't
lose an expected doc; a wrong/empty filter degrades to the full corpus and is
annotated as a *filter-miss*, never a crash.

## The gate survives

The Day-7 out-of-corpus gate (`should_refuse`) keeps working because the
fused score inherits a hard property: when the query shares no corpus tokens,
lexical = 0, so fused = α·cosine — the retriever cannot invent a lexical
match. The one honest price: **τ must be recalibrated onto the fused score**,
not the cosine (E-family top-1 is no longer a cosine). That calibration is
`PENDING` a real live-gateway run; the arithmetic is what the fixtures,
`test_day8.py`, and `tools/retrieval_day8.py --sweep` already prove.

## What "improved" meant, in numbers

The re-run of the nine questions flips exactly the row the taxonomy predicted:
**C1 (ambiguity) goes red→green** — the section filter excludes the wrong app
sense. `--no-rewrite` isolates that attribution: without the filter, C1 fails
again. Recall stays at 100% while MRR rises (B1's completion/order doc is now
ranked by its own sub-query; C1's apps page is now rank 1). Honest negatives:
**D1/D2 do not improve (shared tokens are what twins have — that is Day 9's
rerank/MMR)**, and E1/E2's gate sits uncalibrated (`PENDING`), both recorded
in `docs/evaluations/retrieval-day8*.md`.

## Cost (the ledger's view)

| Strategy | Extra cost vs Day 6 | Notes |
|---|---|---|
| Hybrid lexical half | a tokenizer pass, once, at construct | sub-ms scoring |
| Rewrite (deterministic) | none | rule table |
| Rewrite (gateway) | +1 chat + 0 embed | `PENDING` tier |
| Decompose (B1) | +1 embed | recorded, capped at 3 |
| Section filter | none | metadata already stored |

An improvement that doubles cost is a trade, not a win — the sweep's latency
and ledger rows are part of the evaluation, not decoration.