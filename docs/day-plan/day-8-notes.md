# Day 8 Notes — Retrieval Improvements

> Notes answering `docs/day-plan/day-8.md`.
> Day 8 implements the retrieval-side shortlist Day 7's failure report produced — hybrid keyword + semantic scoring, query rewriting, multi-query decomposition, metadata filtering — then re-runs the *same nine questions* and compares against the Day 6 baseline. The constraint that governed Days 3–7 holds: **stdlib-only, no SDKs, no `rank-bm25`**. So the "BM25-ish" scorer is hand-rolled, and that is the point — the lexical scorer is ~40 lines and every trade-off in it is owned. Everything symbolic (embedding *quality*, whether the rewrite helps, whether the answer is better) is `PENDING`; everything arithmetic (recall@K, MRR, score fusion, token cost) is `MECH` and verified.
> The single idea: **Day 7 moved failures from *opinion* to rows; Day 8 moves rows to fixes; Day 9 will move the unfixed rows to a backlog.**

---

## 1. What "Improved Retrieval" Really Means

The whole day is one discipline: define "better" *before* running anything. Rule 4 of the apprenticeship — *how much better, by which metric, on which dataset* — is not a slogan here, it is a program. Day 7 produced nine rows (`question | retrieved | expected | failure reason | proposed fix`); Day 8's contract is that each row's `fix` column was *retriever-side*, and each row's `expected` column is the ground truth an improvement gets measured against.

Two halves of "better", and they are not the same:

| Half | Metric | What it measures | What buys it |
|---|---|---|---|
| **Recall** — did the right chunk enter top-K? | recall@K, hit@K | the retriever's reach | hybrid search, multi-query, rewrite |
| **Precision/rank** — did it win a top slot? | MRR, order | the retriever's judgement | reranking, diversity — **Day 9** |

Day 8 deliberately owns only the first half *with confidence*. Hybrid search widens the reach; it does not magically order better (lexical scoring actively *harms* near-duplicate Diversity). Pretending otherwise would be how "it seems better" sneaks back in — the exact thing Rule 4 forbids.

A second, quieter idea: **an improved retriever must keep its own refusal gate intact.** E1/E2 (out-of-corpus) must still refuse. A fix that "improves retrieval" by feeding the out-of-corpus question a plausible lexical match would be a regression wearing an improvement's clothes.

---

## 2. The Four Moves

### Hybrid keyword + semantic search

The lexical gap (Day 7): *"how do I cancel an order"* shares no surface tokens with a chunk about `orderCancel`, so cosine — which lives on meaning, not tokens — routes the query to whatever its averaged region happens to contain. Keyword scoring flips the failure: *"cancel"* and *"order"* are exactly the tokens the `orderCancel` docs use. But keyword scoring alone has no paraphrase sense and no notion of meaning — *"why is my cart stuck"* matches nothing lexically. The moves are complements:

```text
chunk_score = α · cosine(expected, query)      # meaning — semantic, one embed
            + (1 − α) · lexical(query, chunk)  # tokens — IDF-overlap, no embed
```

Every design decision in that one line has a trap:

1. **Normalization is mandatory.** Cosine lives in [−1, 1]; token counts are unbounded. The whole corpus shares the IDF scale, but per-chunk raw counts differ wildly. Divide the lexical score by the corpus max so both live in ≈ [0, 1] *per query* — otherwise whichever scale is bigger silently becomes the only one that matters, and α becomes a lie.
2. **α is evidence, not taste.** Swept over `{0, 0.25, 0.5, 0.75, 1}`, with the metric rows recorded per setting. The winner is the value that makes Day 7's fixtures pass with a margin *and* keeps E1/E2 refusing. (Foreshadowed: from Day 21, this sweep is called *hyperparameter evaluation*, and it gets a harness.)

### Query rewriting

One user string, many intents. Rewriting converts the user's words into the corpus's words **before embedding**, so the embedder has a fair query *and* a fair target. Two tiers, and the honesty rule is which tier existed when the run happened:

- **Deterministic tier (`MECH`)** — a rule table mapping observed user phrasing → canonical Saleor vocabulary:
  ```python
  REWRITES = {
      "cart is stuck": "checkout requires a shipping method and valid addresses",
      "cancel an order": "cancel orders with orderCancel after payment",
      "fix checkout": "checkout cannot be completed, common checkout errors",
  }
  ```
  Rules are cheap, testable, and betray the corpus's actual vocabulary — which is itself useful: `corpus/developer/` has `checkout/`, `order/`, `payments/` folders; a rule names them.
- **Gateway tier (`PENDING`)** — `rewrite(question)` via the Day 4 gateway: *"rephrase this Saleor support question in the vocabulary of Saleor documentation, output only the rewrite."* Costs one extra chat call per query — a ledger line, and a real one.

Deterministic-first is not a limitation. It is the version whose behaviour is a *test*, which is the version the fixtures can re-run.

### Multi-query decomposition

Day 7's Family B showed the deepest structural flaw: a question needing two documents embeds as **one** vector and lands *between* intents. B1 — *"how does a checkout become an order, and what changes after?"* — keeps the checkout-create docs and silently drops the completion/order docs. Decomposition makes the intent explicit:

```python
def decompose(question, cap=3):
    return [s.strip() for s in re.split(r"\b(?:and|then|what changes after|but)\b",
                                        question, flags=re.I) if s.strip()][:cap]
```

- Each sub-question is embedded separately → **one extra gateway `embed()` per sub-question** (`2` total for B1 — that is the cost, recorded in the ledger).
- Each sub-query retrieves top-K; the lists are **merged by max-score per chunk id, deduped, re-ranked** — a chunk that the second sub-query ranks #1 beats the first sub-query's #8, which is exactly the completion-page use case.
- The cap is a *budget* knob, exactly like K was in Day 6: `cap=3` bounds worst case at `3 × embed + 3 × scan`. Rule: never let decomposition multiply cost silently.

A boundary worth naming: today's decomposition is *lexical* (`and`/`then`). The real decomposition is *semantic* — a planner deciding "this question contains two intents" — and Day 16 builds that planner properly. Today builds the seam (`decompose()` is a `callable` the retriever consults) and ships the deterministic version.

### Metadata filtering

Chunks have carried `doc_id/source/heading/position` since Day 6 §2 — the citation layer. Filtering spends that metadata *at query time*: `retrieve(..., filter={"doc_prefix": "developer/checkout"})` narrows the scored set before any cosine or lexical math runs. Two honest limitations:

- The **source of the filter still needs to be derived.** Deterministic rule table: query says "checkout" → prefix `developer/checkout/`. This is query rewiring in disguise — a mapping from user phrasing to section.
- A **wrong filter degrades, it does not crash** — the natural Day 8 reaction is to drop the filter and let full-corpus scoring run, annotating the retry as a *filter-miss*. That annotated miss is itself a fixture: it tells Day 20's evaluators where section-classification would pay off.

Everything the filter does today, an LLM "extract section from question" call could do (`PENDING`) — with the ledger showing the price of the upgrade.

---

## 3. The Retrieval Layer Contract

The one retrofit the architecture actually requires. Day 6 left a deliberate seam: `/rag` calls `store.retrieve(qvec, k)` and nothing else — Day 9 never needs to know which retriever produced the top-K. Day 8 widens the seam to own the *query side* too:

```python
class Retriever:
    def retrieve(self, question: str, k: int, mode: str = "hybrid") -> list[tuple[float, Chunk]]:
        q = rewrite(question)                 # deterministic tier today; gateway PENDING
        subs = decompose(q)                   # [q] → up to cap sub-questions
        vecs = [gateway.embed([s])[0].vector for s in subs]   # ledger: one line per embed
        cand = filter_store(subs[0]) if rule_section(subs[0]) else self.store.chunks
        return fuse_rank(vecs, cand, alpha=self.alpha, k=k)
```

- The **output shape is unchanged** — `[(score, Chunk)]` — so `build_context` and `/rag` (Day 6 `rag.py`) compile untouched. That is the seam's contract, and it is the reason Day 9 can swap everywhere *behind* `retrieve` a third time without `/rag` knowing.
- Everything tunable lives in one config: `alpha`, `filter` rule table, `decompose_cap`. Every knob is a number the comparison §8 can sweep. A knob that cannot be swept is a hard-coded belief.
- **The regression contract is symmetric**: Day 7's `fixtures.py` stays green on the improved retriever, and *the baseline must fail them the way the report said it would*. If the baseline suddenly passes B1, the baseline differs from what Day 7 measured.

---

## 4. Hybrid Keyword + Semantic Search

### The lexical scorer (hand-rolled, stdlib-only)

The corpus is small enough that eager, per-chunk IDF is trivially buildable once at store load:

```python
STOP = {"a", "an", "the", "is", "are", "of", "for", "to", "in", "and", "how", "what", "do", "does", "i"}

def tokenize(text):                       # lower → split → drop stopwords
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOP]

class Lexicon:
    def __init__(self, chunks):
        docs = [set(tokenize(c.text)) for c in chunks]
        self.idf = {t: math.log(1 + len(docs) / (1 + sum(t in d for d in docs)))
                    for doc in docs for t in doc}      # one pass, built once

    def score(self, query_tokens):                     # sum of IDF for matched tokens
        return {i: sum(self.idf.get(t, 0.0) for t in query_tokens if t in idf_i)
                for i, idf_i in self.chunk_terms.items()}

    def normalized(self, query_tokens):                # divide by max → [0, 1]
        raw = self.score(query_tokens)
        m = max(raw.values(), default=0.0)
        return raw if m == 0.0 else {i: v / m for i, v in raw.items()}
```

Design choices, each defensible and each *declared* (this is Day 8's first-principles payment):

- **IDF over set-membership, not counts.** A chunk mentioning "checkout" six times doesn't become six times more relevant — Day 7's ambiguity problem was never solved by repetition. Saturation (cap the count at 1) is what keeps rare-token signal and stops a keyword-stuffed chunk from laundering its relevance.
- **Stopword removal before matching.** "How do I cancel an order" → `{cancel, order}`. Keeps the scorer on the semantically-charged tokens and away from `how/do/i` which correlate with nothing.
- **Normalization by corpus-max per query** — the number that makes the fused formula honest (§2). With two query tokens matching nothing, `normalized` returns all zeros and cosine is allowed to carry the answer alone — which is the refusal path in lexical disguise.

### The synthesis test

The move is only worth anything if it *specifically* flips the row Day 7 diagnosed as lexical gap. C1 ("How do apps work in Saleor?") and E2 (the near-miss refusal) are the first two runs:

- C1: does `{apps, work, saleor}` push the App-framework pages above the GraphQL-field pages the cosine regime preferred? Expected anyway, but *measured* anyway — a hypothesis confirmed is a row with a number, not a warm feeling.
- E2: `{integrate, saleor, shopify}` — `shopify` matches nothing in the corpus, `integrate` matches integrations pages. The fused score must either stay below τ or the refusal path fires. **The gate was Day 7's code-level win; hybrid scoring must not be what murders it.**

---

## 5. Metadata Filtering

### The rule table (deterministic source of `filter`)

```python
SECTIONS = {
    "developer/checkout": {"checkout", "cart", "payment method", "shipping method"},
    "developer/order":    {"order", "orders", "fulfill", "cancel an order"},
    "developer/payments": {"payment", "refund", "card", "gateway"},
    "developer/apps":     {"app", "app installation", "webhook"},
}

def rule_section(query: str) -> str | None:       # first section whose tokens appear
    q = set(tokenize(query))
    hits = [sec for sec, terms in SECTIONS.items() if q & terms]
    return hits[0] if len(hits) == 1 else None    # ambiguity → no filter, not a guess
```

The rule `len(hits) == 1` is the whole design: an ambiguous query (`hits == 2`) gets **no filter**, not a filter flip. Filtering must never be the mechanism that *loses* Day 7's expected docs when it fires, and must degrade gracefully — annotated as a filter-miss — when it fires wrongly.

### What it costs and what it buys

The `corpus/` is ~250 chunks in the current build, so filtering saves microseconds — **the point is the pattern, not the performance.** The pattern: Day 6 stored metadata but never spent it; Day 8 spends it. When the corpus grows (Day 10's code RAG, ~1500 reference pages) the pattern is already in place, and the vector store that eventually replaces brute-force (Day 20+) will take a `filter` before a scan because the seam exists.

---

## 6. Query Rewriting + Multi-Query Decomposition

### Rewrite — the "cart is stuck" test

The single most convincing demo of the day, and it needs no real model:

| User question | After rewrite (rule table) | Tokens the embedder now sees |
|---|---|---|
| "why is my cart stuck" | "checkout requires a shipping method and valid addresses" | checkout, shipping, addresses |
| "how do I cancel an order" | "cancel orders with orderCancel after payment" | orderCancel, payment |

Day 7's semantic-gap diagnosis: the question's *symptom* framing differs from the corpus's *mechanism* framing. Rewriting is the mechanical correction — it inserts the mechanism vocabulary the corpus actually speaks before embedding, so the semantics have a fair target. This is the deepest conceptual payoff of the day: **the "semantic" search was never the whole story — it is only as good as the words the embedder receives.**

### Decomposition — Family B in one run

B1, with `decompose_cap=3`:

- `s1 = "How does a checkout become an order"` → checkout-create docs win.
- `s2 = "what changes after an order is created"` → order lifecycle docs win (the half Day 6's single vector kept dropping).
- Merge by max-score, dedup `chunk_id`, top-K. The completion page appears — rank 1 *under its own sub-query* — instead of being crowded out by its siblings in one averaged vector.

That is not a better embedder. It is a **better belief about where the token budget should be spent**: the same idea as Day 6's chunking (don't average meanings) once more, at query time. Day 6 §2's average-of-meanings was a *chunk* problem; Day 7's C1 made it a *query* problem; Day 8's reply is the same move in the other direction — don't average intents, list them.

Cost recorded (the actual ledger lines):

| Strategy | Embed calls per query | Extra cost vs Day 6 |
|---|---|---|
| Day 6 cosine-only | 1 | — |
| + rewrite (deterministic) | 1 | none (rule table) |
| + rewrite (gateway, `PENDING`) | 1 chat + 1 embed | +1 chat call |
| + decompose (B1, cap 2) | 2 | +1 embed (~0 cost, real line) |
| + decompose (worst case, cap 3) | 3 | +2 embeds — cap exists to bound this |

---

## 7. Test: Re-run Day 7's Nine Questions

Same questions, same expected sets, same trace record shape — the only variable is the retriever. That is what makes the comparison a comparison.

### The metric machinery (all `MECH`, no model required)

```python
def recall_at_k(expected, retrieved_ids):      # Day 7 §7
    return len(expected & set(retrieved_ids)) / len(expected)

def mrr(expected, retrieved_ids):              # 1/rank of first expected hit
    for i, doc in enumerate(retrieved_ids, 1):
        if doc in expected:
            return 1.0 / i
    return 0.0
```

| Metric | Baseline (Day 6) | Improved (Day 8 hybrid + rewrite + decompose + filter) | Fixed by |
|---|---|---|---|
| recall@K across 9 | `〈baseline run〉` | `〈improved run〉` | — |
| MRR across 9 | `〈baseline run〉` | `〈improved run〉` | — |
| A1 | pass (control) | pass (control) | — |
| B1 | miss (completion page absent) | **hit** (sub-query 2 surfaces it) | multi-query |
| B2 | partial | partial? | reranking → **Day 9** |
| C1 | split across senses of *app* | hit (token `apps` + rule) | rewrite + hybrid |
| C2 | wrong-entity | hit? | rewrite → verify |
| D1 | 4/5 near-dupes of one meaning | **worse or equal** — hybrid rewards shared tokens | **reranking / MMR → Day 9** |
| D2 | duplicated context | possibly *more* duplication | **dedup → Day 9** |
| E1 | refused | still refuses (τ held) | gate intact |
| E2 | near-miss hallucination | refuses iff fused score < τ | gate + threshold |

The predicted-vs-actual table above is written *before* the run, in the Day 7 tradition (expected sources pre-registered, not curve-fit). The telling rows are the ones the diagnosis predicts will *not* improve:

- **D1/D2 get worse under hybrid scoring** — near-duplicate chunks share tokens, so lexical scoring *rewards* the twins. This is the honest, counter-intuitive finding Day 7's report predicted: hybrid fixes one class and is neutral-or-harmful on another, and the day's value is that the comparison *says so* instead of hiding it.
- **E1/E2's τ now sits on a fused score**, not a single cosine. Re-calibrating it is the price of the new retriever — a legitimate Day 8 task, done with Family A (answerable) vs Family E (unanswerable) separation, exactly as Day 7 §6 designed.

### The verdict discipline

Where a row is *still* broken, re-attribute with Day 7's isolation tests before touching anything:

- expected doc now in top-K, answer still wrong → failure **moved** from retrieval to context — that is Day 9's backlog, and the report must say "moved", or Day 9 will re-debug a closed case.
- expected doc still missing → retrieval still owns it; log whether score distribution is uniformly low (lexical gap survived) or high-with-wrong-winner (cluster collapse survived).

---

## 8. Measure: Baseline vs Improved

Rule 4 in a table — one row per question, every number a real run's. The deliverable:

| Question | Base recall@K | New recall@K | Base MRR | New MRR | Fixed by | Owner if open |
|---|---|---|---|---|---|---|
| A1 | | | | | — | — |
| B1 | | | | | multi-query | — |
| B2 | | | | | ? | Day 9 (rerank) |
| C1 | | | | | rewrite + hybrid | — |
| C2 | | | | | ? | Day 9 (rerank) |
| D1 | | | | | — (expected neutral/worse) | Day 9 (MMR) |
| D2 | | | | | — | Day 9 (dedup) |
| E1 | refuse | refuse | | | gate | — |
| E2 | refuse | refuse? | | | gate | Day 21 (τ) |

And the **negatives column is the deliverable's second half**: *did not fix* rows, with the owning day attached. Day 9's backlog is literally this column — near-duplicate crowding (D1), lost-in-the-middle, duplication (D2), reranking. An improvement report with an empty negatives column is either a miracle or a lie; the fixtures won't let Day 7's rows be a lie.

Latency + cost delta recorded separately (they are being traded off, not hidden):

| Cost axis | Day 6 | Day 8 | Trade |
|---|---|---|---|
| embed calls | 1 | 1–3 | +N per sub-query, capped |
| tokenizer pass | 0 | one over corpus, once | microseconds at ~250 chunks |
| fusion | — | `α·sem + (1−α)·lex` | arithmetic, sub-ms |
| chat calls | 1 | 1 (rewrite is deterministic today) | +1 if gateway rewrite ships |

---

## 9. Documents

- `docs/concepts/retrieval.md` — hybrid fusion formula, normalization necessity, the four moves, dropout/benign-clipping choices of the hand-rolled scorer, and *why stdlib* (the scorer's every trade is owned because there is no library hiding it).
- `docs/evaluations/retrieval-day8.md` — the §8 table plus the α sweep (`{0, 0.25, 0.5, 0.75, 1}` × nine questions × {recall@K, MRR, refuse-correct}).
- PROGRESS rollup: Day 8 status moves to `PARTIAL` (mechanisms `DONE`, semantic confirmation `PENDING` until a real gateway run).

---

## 10. Daily Learning Record

### Problem

Day 7 produced rows: nine questions, expected doc sets, failure reasons, proposed fixes. Day 8's problem is exactly the report's `Proposed fix` column — retrieval-side fixes: hybrid scoring for lexical gap, rewriting for ambiguity, decomposition for multi-hop, filtering for search-space. And a second, subtler problem: proving the fixes are fixes **in numbers**, on the same nine questions, with Rule 4 as audit.

### Concept

Retrieval is not one function — it is at least four, and they compose: **hybrid fusion** (normalized lexical + semantic: α·cosine + (1−α)·token-IDF), **query rewriting** (user vocabulary → corpus vocabulary, before embedding), **multi-query decomposition** (split intents, embed each, max-score merge), **metadata filtering** (spend the citation layer to shrink the scored set). The through-line: every one is a fix for a *specific* Day 7 failure class, and each carries a *specific* cost (an embed per sub-query, a tokenizer pass) that belongs in the ledger.

### Understanding

Two things clicked. First, the semantic-vector model's weakness was never the vectors — it is the *words fed to the embedder*. "Why is my cart stuck" has no chance in a semantic space until it has been translated to the corpus's mechanism words ("shipping method, addresses, checkout completion"). Retrieval quality is downstream of vocabulary matching, and vocabulary is a design decision, not luck. Second, improvement is a *directional* claim: hybrid search widens recall and provably harms near-duplicate diversity at the same time. The honest output of Day 8 is not "retrieval improved" but "these three rows improved, these two rows must move to Day 9, and here is the α where the trade-off is best."

### Implementation

- `ai_os/lexicon.py` — stdlib tokenizer + IDF, set-membership saturation, per-query max normalization; a `Lexicon` built once over the store.
- `ai_os/retrieval.py` — `Retriever`: `rewrite()` rule table, `decompose()` (coord-conjunction split, cap), section filter, `fuse_rank()` (α-weighted merge of cosine + lexical, max-score per chunk id across sub-queries, dedup).
- `ai_os/search.py` untouched — Day 6's cosine remains the semantic half, and the seams stay wherever Day 5 left them.
- `fixtures` re-run — Day 7's nine `(question, expected)` sets, green-in / green-out symmetric contract.
- `docs/evaluations/retrieval-day8.md` — the comparison table + α sweep.

### Failure

Progress, measured: the baseline and the Day 7 report align (B1 partial, C1 split, D1 duplicated). The interesting failure is **predicted**: hybrid scoring is expected to leave D1/D2 *unchanged or worse* — shared tokens are exactly what near-duplicates have. That is not a bug in the implementation; it is the taxonomy refusing to be flattened by a single knob, and it is the strongest argument for why Day 9 (rerank, MMR, dedup) is a *separate* day with *separate* mechanisms.

The second failure is the honest one to pre-record: the rewrite rule table is the corpus's vocabulary as *I currently know it*. Every rule I forgot is a lexical-gap-shaped hole no test will catch until a question trips it. That is why the gateway rewrite tier is `PENDING`, and why Day 20's evaluation set must deliberately include questions built to defeat the rule table.

### Debugging

The moves are `MECH` to their core — the scorer, the normalization, the fusion, the merge are raw arithmetic and string ops runnable under a stub gateway, no API key. The debugging loop uses known-correct sets (Day 5's trick): synthetic queries with token overlap to a known chunk, zero-overlap queries to verify the refusal path survives fusion, and the nine real fixtures as the full integration. Where a row moved (fail→pass), the trace record diff is the debug print: which sub-query pulled the chunk, at what fused rank.

### Evaluation

Full Rule-4 table: **recall@K, MRR, refuse-correctness per question, α sweep, latency, token cost** — everything arithmetic is green, everything semantic is `PENDING` (a real embedder must confirm the rewrite actually *serves* better answers, not just better ranks). The numbers an observer can carry forward: how many of the nine expected sets now clear top-K, which rows moved to Day 9, and the α that maximizes `{fixtures pass, refusal intact} `.

### Architecture

`/rag` is untouched — all of Day 8 lives behind the Day 6 seam (`retrieve`). The retriever goes from a single cosine scan to `Retriever{rewrite, decompose, filter, Lexicon, α}`, still returning `[(score, Chunk)]`. That is the day's architectural claim: **retrieval is now a layer with its own configuration, not a function**, and Day 9 will swap mechanisms inside it (rerank, MMR, dedup) without `/rag`, `build_context`, or the gateway changing a line.

### Questions

- Is α ≈ 0.5 right, or is the true optimum corpus-dependent in a way no single α captures? (Day 21 can sweep it *per family* and answer.)
- How honest is the hand-rolled scorer vs real BM25? The gap is the IDF/unigram assumptions — but the first-principles version is *owned*, and migration is a one-function swap behind the seam.
- Is decomposition's cap of 3 enough for questions with four real intents, or does the cap just move the failure from "dropped intent" to "squeezed sub-query"?
- The rule table forgets things. Does the gateway-rewrite tier earn its +1 chat call, and at what refusal-rate penalty?
- When a wrong-entity row (C2) is fixed by hybrid + rewrite, was it really ambiguity, or did lexical tokens simply outrank semantics? The two cures differ (Day 9 rerank vs Day 16 planner) and only the trace record says.

### Tomorrow

Day 9 — Reranking and Context Construction: take the `Owner if open` column of today's comparison — near-duplicate crowding (D1), duplicated context (D2), wrong-entity ordering, lost-in-the-middle — and run them through a reranker + context builder that orders and compresses the *evidence*, the retrieval-quality half Day 8 explicitly did not touch. Today's α sweep and fixture contract are Day 9's baseline; Day 9's reranker must keep Day 8's rows green and clear the ones that moved here.