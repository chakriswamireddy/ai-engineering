# Concept: Evidence Quality is a Layer of Its Own

> Day 9 of the AI-layer apprenticeship. Day 8 fixed the retriever's *reach* —
> getting the right chunk into top-K. Day 9 owns the second half: given the
> right candidates, decide **which one wins, where it sits, and what survives
> the budget**. The output deadline is unchanged (`/rag` and the gateway don't
> change a line); only the evidence they're handed changes.
> Implementation: `ai_os/rerank.py` + `ai_os/context.py`.

## Problem

Day 8's comparison closed with an `Owner if open` column — failures it fixed
retrieval*'s* reach for but could not touch, because they are not reach
problems:

| Day-8 row | Symptom | Failure class |
|---|---|---|
| D1 | near-duplicate twins own the top-5 slots | **diversity** — the find-score loves twins |
| D2 | two guides serve the same fact; context repeats it | **redundancy** — duplication survives into the context |
| B2/C2 | wrong page out-ranks the right one | **ordering** — one average score for many senses |
| (all) | the answer chunk sits mid-context | **presentation** — lost-in-the-middle, carried since Day 6 |

One idea reframes all four: **found ≠ best.** A retrieval score that is tuned
to *find* (bring the right chunk into top-K) is the wrong instrument to also
decide *rank 1*, because rank order is about the *relationship among
candidates* (redundancy, section semantics, position), while a find-score is
about one candidate's relationship to the query. Day 9 splits that judgement
out into its own layer.

## The two seams (what didn't change)

The contract Day 6–8 held — `[(score, Chunk)]` out of retrieval, `/rag`
untouched, `citation ⊆ retrieved` — stays. Two narrow seams are inserted
between them, and one documented divergence:

```text
retrieve()            (Day 8: fuse, rewrite, decompose, filter)
  └▶ rerank(cands, q, k)          # NEW seam — judge. [(score, Chunk)] → [(score, Chunk)]
       └▶ MMR(λ) + dedup(sim_tau) # diversity + redundancy over stored vectors & text
            └▶ build_context(reranked, budget)   # Day 6 signature; Day 9 policy inside
                 └▶ /rag          # 0 lines changed
```

- `rerank` is shaped identically to its input, so it can be inserted, swapped
  for a gateway cross-encoder, or bypassed without touching a caller.
- `build_context` keeps its Day 6 *signature* (inputs, outputs, budget floor,
  citation invariant) — the contract is the seam. The *policy inside* (rank-order
  truncation → relevance-order + dedup + reporting) is Day 9's change. Day 8
  predicted the function would survive untouched; Day 9 documents that the seam
  survives but the policy doesn't — the divergence is stated, not smuggled.
- Pipeline order (MMR before or after dedup?) is itself a knob, swept not assumed.

## The reranker: pointwise relevance, arithmetic not models

`rel(c)` is a weighted sum of signals, each a plain function of data the
pipeline *already paid for*:

```text
rel(c) = w_fused · fused(c)      # 0.5 — Day 8's α-fusion, now one voice among four
       + w_rare  · rare(c)       # 0.25 — highest-IDF query tokens in c.text (Day-8 Lexicon)
       + w_head  · heading(c)    # 0.20 — highest-IDF query token in the chunk's heading/doc_id
       + w_pos   · pos(c)        # 0.05 — late position is a mild tie-break, never a verdict
```

Why each signal, and why the first pass couldn't use it:

- **fused** — "did retrieval like this chunk" stays in the mix so the reranker
  is a *reordering*, not a brand new ranking. But the whole-corpus first pass
  computed one number per candidate; it cannot afford the targeted work below.
- **rare** — the Lexicon's IDF already separated the tokens that discriminate
  (`checkoutComplete`, `refund`) from the ones that don't (`how`, `do`). Two
  subtleties, both tested: tokens shorter than `MIN_DISCRIMINANT_LEN = 4` are
  never discriminators (the 3-char verb `set` must not out-IDF
  `checkoutComplete`), and matching is one-way (a long candidate term does not
  satisfy a short query term).
- **heading** — chunks have carried `doc_id/heading/position` since Day 6;
  Day 8 spent that at query time for *filtering*, Day 9 spends it for *judging*.
  When the query's highest-IDF token appears in a chunk's heading, that is a
  section-level match cosine averages away — the wrong-entity fixer (C2's
  `checkout` reference page loses to the `checkoutComplete` guide the moment
  `checkoutComplete` hits a heading).
- **position** — a tie-break only; the score must never be explainable by "it
  came first". Capped drop (~10% at end-of-document), monotone, never > the
  fused weight.

The `PENDING` tier is a seam that stays a seam: `relevance_cross_encoder`
would be pointwise-but-semantic, costs **one chat call per candidate**, and
raises `NotImplementedError` until the paid tier proves it outranks free `rel()`
on the same table. Day 9's entire rerank path costs the ledger nothing.

## MMR: diversity is a stored-vector computation

Maximal Marginal Relevance, greedy:

```text
pick 0 = argmax rel (seeds the set)
pick i = argmax_c [ λ·rel(c) − (1−λ)·max_{j∈kept} cos(v_c, v_j) ]
```

- The vectors are Day 5's stored index — twins' high mutual cosine is already
  sitting in the store. **No new embeds on any diversity path.**
- Why MMR is the *specific* D1 fix: the twins penalize each other the moment
  one is kept; the how-to page, distinct from both, survives the pick.
- The trap, recorded: B1's two docs are similar *by design* (checkout-create +
  checkout-completion). λ aggressive enough to cull D1's twins can cull B1's
  legitimate second doc. The correct λ is a constraint-satisfaction result —
  the `--sweep` row where B1 stays green *and* the twins cull — not a taste.
  On the stub, that is **λ = 0.7** (λ ≤ 0.6 leaks B1's quickstart page).

## Context construction: three jobs, not one

Day 6's `build_context` did one job: fit chunks into the budget, rank-order,
floor-guarded. The other two jobs were flagged, `PENDING`, and handed forward.
Day 9 implements all three:

1. **Ordering.** Reranked+MMR order is the evidence's *claim*; presentation
   order is the model's *attention*. Default `best_first=True` (strongest
   evidence leads). The lost-in-the-middle protocol (same question, same
   evidence, answer chunk slid slot 1→K) measures whether slot order changes
   correctness — protocol `MECH` (mechanical sweep over fixed slots) with the
   semantic verdict `PENDING` until a real chat run. Ordering isn't a nicety,
   it's a variable being measured.
2. **Dedup.** Context-level redundancy is a *text* fact (two guides saying the
   same sentence), so `text_sim` uses token Jaccard over the selected set —
   deliberately **not** the retrieval vectors, which would double-count what
   MMR already penalized. One guard makes it a judgement, not a deletion bug:
   **the top-relevance chunk is never similarity-dropped** — a near-duplicate
   that *is* the answer survives.
3. **Compression.** Budget truncation can silently re-break recall *inside the
   context layer* — retrieval did its job and a character count threw the
   answer away. So compression is evidence-weighted, not character-counted:
   walk the deduped rerank order, keep what fits, and **drop lowest-relevance
   chunks first — never the budget-protected top chunk** — recording every drop
   with `reason:"budget"` so context utilization stays a traced field. (Day 6's
   rank-order truncation becomes its special case and is retired as the policy.)

Compression done honestly sounds backwards: **utilization can fall while
quality rises** — dropping four redundant-but-fits chunks is a win. The eval
table treats utilization as a reported metric, not an objective.

The invariants that cannot bend:

```text
cited ⊆ retrieved                        (Day 6) — by construction
top chunk never evicted (Day 6 floor)    — kept
every drop has a reason + doc id         (Day 7 trace) — new, cheap
duplicate ratio ≤ Day 8's                (Day 7 fixture) — the D1/D2 acceptance line
```

## The refusal gate reads the fused score, on purpose

Rerank *can* lift a plausible-but-wrong chunk (the E-family's out-of-corpus
near-miss) via rare-token or heading hits. The gate therefore reads the
**fused** score — Day 8's threshold — never the rerank score. A reranker's
opinion can never manufacture an answer to an unanswerable question. That
boundary is a decision, written down so nobody "improves" the gate later by
letting the reranker vote.

## What "improved" meant, in numbers

On the same nine pre-registered questions, rerank+MMR+dedup+budget as pure
arithmetic over stored artifacts:

- **Two rows moved** (the pre-registered allowed set B2/D2): B2 MRR 0.500→1.000
  (rare+heading vote lifts completing-checkout), D2 0.500→1.000 (heading
  signal spends the create-a-checkout section id).
- **Everything else held, nothing leaked**: A1 the control, B1's three docs
  (MMR did *not* cull the legit second page at λ=0.7 — the recorded trap),
  C1/C2 held, D1's how-to page stayed rank 1 with the twin behind it. 9/9
  fixtures, 0 context leaks. ΣΔMRR +1.000 on the stub.
- **E1/E2 still refuse** (0/2 at the stub τ) and the gate's input is
  byte-identical to Day 8's — rerank reorders, it does not invent.
- **Zero new embeds, zero chat calls.** rerank+MMR+dedup+budget ≈ 0.57 ms/query
  on the 9-chunk stub — an improvement that costs the ledger nothing until you
  opt into the `PENDING` cross-encoder (+N chat calls per query, its price
  recorded next to the numbers).
- Honest stub-specific negatives, carried to the real-corpus backlog: D1's
  near-duplicate ratio does not drop on the stub (no how-to-vs-twins structure
  exists in 9 chunks — the cull is proved by `test_day9.py`'s hand-vector
  fixture); C2 holds on the stub (its wrong entity lives only in the richer
  corpus — the heading mechanism is unit-tested against a crafted page). Both
  need `python tools/retrieval_day9.py --store data/store.json`.

## Cost ledger

| Strategy | Extra cost vs Day 8 | Notes |
|---|---|---|
| Rerank (4 signals) | a tokenizer pass over top-K | sub-ms; Lexicon built once at construct |
| MMR(λ) | cosine over stored vectors | no new embeds |
| Dedup(sim_tau) | token-Jaccard over the chosen set | text-level by design |
| Compression | one sorted walk | every drop reported |
| Cross-encoder | +N chat calls (N = candidates) | `PENDING` seam; must beat free rel() before it ships |

An improvement that doubles cost is a trade, not a win. Day 9's is the day the
retrieval layer got better without paying for it; the paid tier has to earn its
ledger lines from Day 21.