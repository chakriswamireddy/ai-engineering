# Day 9 Notes — Reranking and Context Construction

> Notes answering `docs/day-plan/day-9.md`.
> Day 9 owns the second half of retrieval quality that Day 8 explicitly deferred. Day 8 widened the retriever's *reach* (hybrid, rewrite, decompose, filter) and handed over an `Owner if open` column: near-duplicate crowding (D1), duplicated context (D2), wrong-entity ordering (B2/C2), lost-in-the-middle. Today's claim is narrow: rerank the candidates for **relevance**, enforce **diversity** (MMR), and let `build_context` **order + dedup + compress** under the budget. No corpus change, no embedder change, no gateway change — the labels `MECH` (arithmetic, no model) and `PENDING` (needs the real gateway) stay the honest grading system, and the whole day is graded by a Day 8 → Day 9 table on the same nine questions.
> The single idea: **Day 8 got the right chunks into top-K; Day 9 decides which of them wins, where it sits, and what survives the budget.**

---

## 1. "Found" and "Best" Are Two Different Judgements

Day 8's fused score was built to *find*: `α·cos + (1−α)·lex`, tuned to put the expected chunk in the top-K envelope. That is a **recall** engine's score. Whether the expected chunk then deserves **rank 1** is a different judgement, and the two are routinely in conflict — the conflict is exactly Family D:

- D1's near-duplicate twin shares *tokens* (so lexical loves it) *and* vector region (so cosine loves it). It out-scores the genuine how-to page. Day 8 predicted — honestly, in its own notes — that hybrid would make D1 *worse or equal*, because "improved" retrieval was a recall claim, not an ordering claim. Fixing that is Day 9's first job.
- C2's wrong-entity page (`checkout` reference) can legitimately out-score the right one (`checkoutComplete` flow) when the query vector lands between two senses. Nothing about the find-score fix that.

So a reranker must expose evidence the first pass *could not afford*: not more embedding quality, but a different, smaller, more targeted set of signals judged at the *candidate level*. First pass: one vector, whole corpus, cheap. Reranker: top-K only, but we can now spend per-candidate work that was impossible over 250 chunks.

The thing that keeps the day honest — the **cheater's question**: predict, before running, which *two* Day 8 rows a reranker is allowed to change, then check the trace. If rank order after "reranking" equals retrieval-score order, the reranker is a no-op wearing a name. So each run records `Δrank` per expected doc: pre-rerank rank vs post-rerank rank. No Δrank, no reranker.

The relevance-scoring family tree is worth naming because it sets the ceiling of ambition:

| Family | What it judges | Day 9's version | Status |
|---|---|---|---|
| Pointwise | each candidate solo | `rel(c)` from §4 signals | `MECH` — hand-rolled |
| Pointwise (semantic) | each candidate solo, from a model | cross-encoder `relevance(q, c)` | `PENDING` — one gateway chat per candidate |
| Pairwise | candidate A vs B | (skipped — needs pairwise training data) | not needed today |
| Listwise | the whole ordering | (skipped — that's an evaluator, not a builder) | Day 21's benchmark |

Day 9 builds the cheapest pointwise kind with the signals already in the store, and writes the cross-encoder as a seam so the upgrade is one function away.

---

## 2. Context Construction Has Three Jobs, and Day 6 Only Did One

Day 6's `build_context` had exactly one job: **fit chunks into `budget_chars`, rank-order, floor-guarded**. It is a budget enforcer. The other two jobs — **ordering** (does the position of evidence matter to the reader?) and **compression** (which chunks are redundant or droppable?) — were flagged, `PENDING`, and handed forward. Day 7 §6 named them (`lost-in-the-middle`, `duplicated seam`) and Day 8's notes explicitly parked them at Day 9's door.

### Ordering and lost-in-the-middle

The paper claim is that transformer attention is positional: an answer-document in the *middle* of a long context is read worse than the same answer at the start or the end. Day 6 §7 tested it at K=8 and marked it `PENDING`; Day 7 §6 recommended making it an experiment; Day 8 deferred it. Day 9 turns the paper claim into a **number in the table**:

```text
experiment: same question, same evidence set, expected chunk at slot 1..K
measure:    correctness (or answer-quality) per slot
if pos(s1) > pos(s5): ordering is a real knob → best_first=1 is the default
if flat:     ordering is cheap insurance → best_first stays, demoted to tie-break
```

Protocol is `MECH` (fixed slots, fixed model, mechanical sweep). The *verdict* — whether the answer is actually correct at each slot — is `PENDING` until a real chat run. But the protocol must be run and the number pre-committed today, so that slot-order is a *decision with evidence attached*, not a default on vibes. And a real finding even when flat: "ordering doesn't help at K=5" is itself signal for Day 21.

### Compression is the dangerous job

Budget truncation can silently re-break recall **inside the context layer**: retrieval did its job, the chunk is in top-K, and then a character-count drops it. Day 6's floor (keep what fits, rank-order) was correct as an *invariant* — the budget must never evict the top chunk — but wrong as a *policy*: rank-order is not relevance-order, and a middling-relevance 200-char chunk can starve a low-rank but answer-bearing 400-char chunk.

Compression therefore has to be **evidence-weighted, not character-counted**:

```python
# Day 9 policy
ordered = rerank(candidates, question)      # §4 rel(c)
deduped = dedup(ordered, top_kept_first=True)   # §5 — never drop the top chunk
chosen  = []
chars   = 0
for c in deduped:                            # budget is a floor + a relevance order
    if chars + len(c.text) <= budget or not chosen:
        chosen.append(c); chars += len(c.text)
    else:
        drop_record(c, reason="budget")      # Day 7 trace: utilization field
```

Every drop is reported, so `context utilization` from the Day 7 trace is honest: a compression that "wins" only by shrinking utilization without dropping the answer is the *good* kind.

### Dedup, with a guard

D2's duplicated context is two guides serving the same fact. The naive fix — "drop chunks similar to already-kept ones" — is a bug waiting to happen: two chunks sharing 60% of tokens can carry *different* facts (B1's checkout-create and checkout-completion pages are near-duplicates *by design*). Day 9's dedup rule therefore has an exception: **the top-relevance chunk is never dropped by similarity**. Dedup is Redundancy Judged Against the Answer, not mechanical text-similarity.

---

## 3. The Two Seams

Day 8's contract was: everything behind `retrieve`, `/rag` untouched, output shape `[(score, Chunk)]`. Day 9 keeps that *for retrieval* and adds one honest divergence. **Day 8 predicted `build_context` would survive unchanged; Day 9's design decision is that the seam survives but the policy inside it changes.** Saying it openly beats smuggling it.

Two seams:

```text
retrieve()  (Day 8: fuse, rewrite, decompose, filter)
    └▶ rerank(candidates, question, k)     # new seam — [(score, Chunk)] → [(score, Chunk)]
         └▶ MMR(λ) + dedup(sim_tau)        # operates on stored vectors + rel scores
             └▶ build_context(reranked, budget)  # Day 6 signature; Day 9 policy inside
                  └▶ /rag (0 lines changed)
```

- `rerank` is the *judgement* step: same shape in and out, so it can be inserted, swapped for the cross-encoder, or bypassed without touching anything else.
- `build_context` keeps its Day 6 *signature* (input, output, budget floor, `citation ⊆ retrieved` invariant) — the contract is the seam, not the internal truncation policy. Day 9 replaces rank-order truncation with relevance-order + dedup + reporting.
- Pipeline order is itself a knob: MMR before dedup or after? These interact (MMR's pairwise penalty and dedup's redundancy cut can double-remove chunks). The sweep records both.

The config surface — `w_fused, w_heading, w_rare, w_pos`, `λ`, `best_first`, `sim_tau` — is every number Day 9 is allowed to tune, and the whole thing marries to Day 8's α sweep. Rule 4's "by which metric, on which dataset, at what cost" needs each knob's row.

### Why MMR specifically, and its trap

MMR is the arithmetic that turns "top-5 is 4 twins" into "one twin + its diverse neighbors":

```python
def mmr(candidates, sim, rel, lam, k):
    picked, rest = candidates[0], candidates[1:]        # highest rel first (MECH)
    while len(picked) < k and rest:
        i = max(range(len(rest)), key=lambda i:
                lam * rel(rest[i]) - (1 - lam) * max(sim(rest[i], p) for p in picked))
        picked.append(rest.pop(i))
    return picked
```

- `sim` = cosine over the *stored* Day 5 vectors — the twins' mutual similarity is already sitting in the index. No new embeds on any rerank/MMR path. That is the cost story Day 8's ledger buys us: **Day 9 adds zero embed calls and zero chat calls** — pure arithmetic over what's stored. (The `PENDING` cross-encoder is the only place that changes.)
- The trap is B1: its two docs are *intentionally* similar, so λ aggressive enough to cull D1's twins may cull B1's legit second page. The correct λ is the one where B1 survives *and* D1's `max_dupes` clears. That's a constraint-satisfaction sweep, not a taste.

---

## 4. The Cheap, Deterministic Reranker

Hand-rolled, stdlib, one module: `rel(c)` is a weighted sum of signals, each a plain function of data the pipeline already paid for:

```python
def rel(c, query, lexicon):
    fused   = c.fused_score                          # Day 8's α-backed score, carried forward
    rare    = rare_token_hit(c, query, lexicon)      # highest-IDF query tokens in c.text? — Day 8 Lexicon
    heading = heading_hit(c, query, lexicon)         # highest-IDF query token in c.heading/doc_id?
    pos     = position_tiebreak(c)                    # late position = mild penalty, tie-break only
    return w_fused*fused + w_rare*rare + w_heading*heading + w_pos*pos
```

The logic of each weight:

- **fused** keeps "did retrieval like this chunk" in the mix — but it stops being the *only* voice. `w_fused` is deliberately not the whole story.
- **rare-token**: Day 8's Lexicon already computes IDF. A token like `checkoutComplete` or `refund` discriminates; `how`/`do`/`i` were already stopworded. Rare-token hit on *text* catches the page that talks specifically about the answer.
- **heading**: the chunk's metadata (`doc_id`/`heading`, stored since Day 6 §3) is the *wrong-entity fixer*. When the query's highest-IDF token appears in the chunk's heading, that is a section-level match that cosine alone averages away. C2's wrong-entity page loses to the reference page the moment `checkoutComplete` hits a heading.
- **position**: a tie-break, never a verdict. Two candidates scored equally re-rank by retrieval position; the score must never be explained by "it came first".

`PENDING` tier, written as a seam but not shipped today:

```python
def relevance_cross_encoder(query, chunk):   # PENDING — one gateway chat per candidate
    return gateway.chat([...score relevance of chunk for query...])   # ledger: N calls, real cost
```

Day 8's ledger shows why that stays `PENDING`: every per-candidate call is a real cost line, and the whole point of Day 8/9's arithmetic is that the deterministic tier is *free*. The seam exists because Day 20/21 will tell us whether the paid tier earns its ledger lines.

---

## 5. MMR Diversity + Context-Level Dedup

D1's fixture (`max_dupes`, Day 7) is the acceptance test for this section. MMR handles the *slot-level* crowding; dedup handles the *context-level* redundancy (D2) after selection. Both operate only on the top-K from `rerank` — D1's expected `doc_id` must still be present after the cull.

Dedup at context level, with the guard — never drop the top chunk:

```python
def dedup(ordered, sim_tau):
    kept = [ordered[0]]                          # the answer-bearing chunk is never similarity-dropped
    for c in ordered[1:]:
        if max(text_sim(c.text, k.text) for k in kept) > sim_tau:
            drop_record(c, reason="redundant")   # → Day 7 trace: duplicate ratio
        else:
            kept.append(c)
    return kept
```

`text_sim` is a string-level similarity (token-overlap / normalized char ngrams) — deliberately *not* the same representation as the retrieval vectors, because D2's redundancy is a *text* fact (two guides saying the same sentence), not a vector fact. Using the same vectors would double-count what MMR already penalized.

The honest finding to pre-record: MMR and dedup fight. MMR already deprioritizes twins within the pick loop; dedup then re-removes text-identical neighbors from the *chosen* list. Sweep order and λ/`sim_tau` together, and log which of the two did the work — a "fixed by MMR vs fixed by dedup" column in the eval table. That attribution is the difference between "we improved retrieval" and "we know which knob did it".

---

## 6. Ordering + Compression Under the Budget

The `build_context` policy — three rules, in order — plus the lost-in-the-middle experiment:

1. **dedup already ran** (§5) before any character is spent.
2. **drop lowest-relevance chunks that don't fit** — never the top chunk. The floor (Day 6's inviolable "top chunk always fits") is kept; the *ordering that decides who is top* changes from retrieval-score to rel-score.
3. **report every drop** so utilization (Day 7 §4 trace) is a field, not a guess.

`best_first=True` is the default presentation order because it is the cheap, dominant strategy under positional attention: the strongest evidence leads the context. The lost-in-the-middle sweep (§2) either confirms it (slot 1 beats slot 5) or measures how strong the effect is at this K — both are numbers.

Compression, done honestly, sounds backward: **utilization can go down while quality goes up.** Dropping four redundant-but-fits chunks is a win that lowers the char count. Day 9's eval table records utilization as a *reported* metric, not an *objective* — Rule 4 again: state what you're optimizing before you run.

The invariant contract:

```text
cited ⊆ retrieved      (Day 6)  — still green, by construction
top chunk never evicted (Day 6 floor) — kept
every drop has a reason+id (Day 7 trace) — new, and cheap
duplicate ratio ≤ Day 8's (Day 7 fixture) — the D1/D2 acceptance line
```

---

## 7. Test: Re-run the Nine Questions

Same nine, same expected set, same trace *shape* as Day 7 §4 — with one column added: **Δrank** (pre-rerank rank → post-rerank rank of the first expected doc). The Δrank column is the proof-of-existence for the whole day: a reranker that moves no row has no data.

Re-attribute the survivors with Day 7's isolation tests — this is where "moved" becomes a precise word:

- **expected doc now ranks 1, answer right** → closed. Row's `owner if open` becomes `—`.
- **expected doc ranks 1, answer wrong** → the failure has *moved to generation*. Day 7's oracle test decides: if oracle (perfect evidence) also fails → generation (Day 20/21); if oracle succeeds but the built context still loses → *context quality* — reranker order vs the model's actual reading (the lost-in-the-middle experiment is the probe). The report must say "moved", exactly as Day 8's notes demanded.
- **expected doc still missing from top-K** → retrieval owns it after all; it re-enters the Day 8 backlog, not Day 10's code-RAG backlog. Day 8's hybrid was *supposed* to fix it; if it didn't, that's Day 8's datum, not Day 9's aspiration.

E1/E2 stay the refusal gate's territory, and Day 9 must not accidentally reopen it: rerank *could* boost a plausible-but-wrong `shopify`-adjacent chunk's rel score. The gate (`should_refuse`) checks the *fused* score (Day 8's threshold), not the rerank score — deliberately, so that a reranker's opinion can never manufacture evidence for an out-of-corpus question. That boundary is a design decision and it is written down here precisely so nobody "improves" the gate later by letting the reranker vote.

Metrics `MECH` by construction: MRR, Δrank, duplicate ratio, `max_dupes`, utilization, λ/weight sweep rows. The semantic ones (answer correctness per slot, "answer right" in the moved tests) are `PENDING` — but the *protocol* is run today so Day 21 has the protocol and the expectations, not just an aspiration.

The sweep: `{λ, weights, best_first, sim_tau}` each get written into the eval doc *before* the run, per Day 7's pre-registration rule — expected before, never curve-fit after. (Foreshadowing: this is the same discipline that makes the α sweep in `docs/evaluations/retrieval-day8.md` reproducible.)

---

## 8. Measure: Day 8 vs Day 9

Rule 4 in one table — every number a real run's. The comparison is the deliverable, and the *headline rows are the ones Day 8 predicted would not improve*:

| Question | Day 8 MRR | Day 9 MRR | Δrank | Day 8 dup-ratio | Day 9 dup-ratio | Opening (Day 8's `owner if open`) | Status |
|---|---|---|---|---|---|---|---|
| A1 | pass | pass | 0 | | | — | must hold (control) |
| B1 | hit (multi-query) | ? | ? | | | multi-query (done Day 8) | hold: MMR must not cull its 2nd doc |
| B2 | ? | ? | ? | | | **Day 9 (rerank)** | allowed to move |
| C1 | hit | ? | ? | | | rewrite+hybrid (done D8) | hold |
| C2 | ? | ? | ? | | | **Day 9 (rerank)** | allowed to move |
| D1 | worse-or-equal | ? | ? | >1 | ≤1 | **Day 9 (MMR)** | the acceptance test |
| D2 | duplicated | ? | ? | high | low | **Day 9 (dedup)** | the acceptance test |
| E1 | refuse | refuse | — | | | gate | must refuse |
| E2 | refuse? | refuse? | — | | | gate + τ | must refuse |

The **δ column** is the honest core: a row's `owner if open` from Day 8 is either cleared (status `—`, with evidence), explicitly held, or *re-opened*. Day 9 succeeds in exactly the rows Day 8 said were Day 9's — and the table's negatives column stays, because a comparison that moved every row is a miracle or a lie.

Cost/latency, in the ledger's raw numbers:

| Instrument | Day 8 (per query) | Day 9 adds (per query) | Trade |
|---|---|---|---|
| embed calls | 1–3 (sub-queries) | **0** | MMR/dedup use stored vectors |
| chat calls | 1 (+rewrite `PENDING`) | **0** | rel() is arithmetic |
| tokenizer pass | one, cached | one over top-K rel() | microseconds |
| `PENDING` cross-encoder | — | +N chat calls | the seam it's written for |

That "**zero**" is Day 9's feature: an improvement that costs more embeds would be a trade, not a win (Day 8 §8's own rule), and Day 9 is the day where the improvement is free until you opt into the paid tier.

---

## 9. Documents

- `docs/concepts/reranking.md` — pointwise relevance scoring, the four rel() signals and their weights, MMR formula and λ, the three context-construction jobs (order, dedup, compress), the two seams, and the cross-encoder `PENDING`.
- `docs/evaluations/rerank-day9.md` — the §8 table plus the `{λ, weights, best_first, sim_tau}` sweep, per-question, each row done-and-failed-state attributed (MMR did it / dedup did it / it moved to generation / it returned to Day 8).
- PROGRESS rollup: Day 9 status moves to `DONE` for the arithmetic (rerank/MMR/dedup/order verified fixtures) and `PARTIAL` until a real gateway run confirms the ranking actually serves better answers.

---

## 10. Daily Learning Record

### Problem

Day 8's comparison closed with `owner if open` rows: near-duplicate crowding (D1), duplicated context (D2), wrong-entity ordering (B2/C2), and the lost-in-the-middle flag carried since Day 6 §7. The problem is not "retrieve better" — Day 8 did that and said so. The problem is the *second half*: given the right chunks in top-K, why do the wrong ones still win the context, hold the wrong rank, and duplicate each other — and can a hand-rolled, per-request-free reranker + context builder fix it in numbers, not vibes?

### Concept

**Evidence quality is a distinct system from retrieval quality.** Three ideas: (1) *found ≠ best* — the fused retrieval score optimizes recall; relevance (which chunk should be rank 1 and quoted) is a second judgement, judged at candidate level with signals (heading hit, rare-token hit, position) the whole-corpus pass can't spend. (2) *MMR balances relevance against redundancy* — `λ·rel(c) − (1−λ)·max(sim to kept)` is the arithmetic that stops near-duplicate twins from owning top-K, using stored vectors for free. (3) *Context construction has three jobs* — budget enforcement (Day 6), ordering (lost-in-the-middle: does slot matter? measure it), and compression (dedup + relevance-weighted drops, with a guard that the top chunk is never similarity-dropped). Compression is dangerous because it can silently re-break recall inside the context layer — that is why every drop is recorded in the Day 7 trace.

### Understanding

The deepest unlock: **retrieval score and evidence order are two different claims, and conflating them is the root of D1/D2.** A find-score tuned for recall is asked to also *depend on order* — impossible, because ordering is about the *relationship among candidates* (redundancy, section semantics, position), not the relationship of one candidate to the query. Once that's clear, the fixes are cheap *because they are arithmetic over what's stored*: MMR needs the Day 5 vectors, rare-token hits need the Day 8 Lexicon, heading hits need Day 6 metadata. Day 9 is the first day where the deliverable improves without spending a new embed — and the `PENDING` cross-encoder (the expensive, real, semantic reranker) exists so that Day 21 can answer whether paying for semantics is worth it.

### Implementation

- `ai_os/rerank.py` — `rel(c, question, lexicon)` pointwise scorer: `w_fused·fused + w_rare·rare + w_heading·heading + w_pos·pos`; `Δrank` tracking; `PENDING` `relevance_cross_encoder` seam.
- `ai_os/context.py` (build_context v2) — policy: dedup → relevance-weighted budget fill (top chunk never evicted) → `best_first` ordering; every drop recorded in the trace.
- `mmr` — greedy diversity picker over stored vectors + rel scores; λ config.
- `fixtures` — Day 8's nine sets stay green; D1 `max_dupes` and D2 duplicate-ratio are Day 9's acceptance lines; E1/E2 keep refusing.
- `docs/evaluations/rerank-day9.md` — Day 8 → Day 9 table + `{λ, weights, best_first, sim_tau}` sweep, pre-registered.

### Failure

The MMR-vs-dedup interaction is the day's first expected failure: MMR deprioritizes twins inside the pick, then dedup re-removes text-identical survivors in the context — double-removal is a real possibility, and it's not a bug, it's two mechanisms sharing one intent. The guard (`top chunk never similarity-dropped`) is what keeps it from costing B1 its second doc. The second expected failure: rerank *can* lift a plausible-but-wrong chunk (E2's shopify-adjacent page) on rare-token or heading hits. The refusal gate deliberately reads the *fused* score, not the rerank score, so a reranker's opinion can never manufacture an answer to an unanswerable question — that boundary is a decision, recorded here so nobody "improves" the gate by letting the reranker vote.

### Debugging

Everything Day 9 builds is `MECH` and traceable: `rel()` is a weighted sum of four named signals, MMR is a greedy loop over stored vectors, dedup is string similarity with one guard. The debug loop: pick a fixture (D1), run pre-rerank ranks vs post-rerank, and read the Δrank line — if the how-to page didn't rise above its twin when the heading signal fired, the signal function (or its weight) is wrong, and the trace says which. B1's survival is the λ probe: culled second doc → λ too aggressive; that's a number, not a guess. The lost-in-the-middle sweep is a fixed protocol (slot 1..K, same question, same evidence) — run once, variance says whether the ordering policy *earns* `best_first`.

### Evaluation

The whole day is one Rule-4 table: `Day 8 → Day 9` for MRR, `Δrank`, and duplicate ratio, per question, every cell a real run's. The acceptance criteria are the rows Day 8 deliberately did *not* fix: D1's `max_dupes`, D2's duplicate ratio, B2/C2's wrong-entity rank. The evaluator it hands to Day 21: pre-registered expectations + sweep table, exactly the shape Day 8's α sweep established. The latent measure: **the day added zero embeds and zero chat calls per query** — cost stays flat while the evidence quality row moves.

### Architecture

`retrieve` (Day 8) and `/rag` (Day 6) are untouched. New: a `rerank` seam between them (same `[(score, Chunk)]` shape in and out — swappable for the cross-encoder), `MMR` and `dedup` operating on stored artifacts, and build_context's *policy* changed behind its unchanged signature. The architectural claim: **the plan's "Retriever → Reranker → Best evidence → Context builder" is three seams, not one blob** — judge, diversify/compress, and present, each independently measurable and independently testable, each free-of-new-models.

### Questions

- Is `best_first` the right presentation order at this K, or is the lost-in-the-middle effect (and once measured, the best order) corpus-length dependent? The sweep at slot 1..K answers the *existence*; Day 21 asks the *shape*.
- When a wrong-entity row (C2) is fixed, was it the heading signal, the rare-token signal, or MM-adjacent luck? `Δrank` + signal attribution per fixture is the only way to know which knob saved it — and which knob to tune next.
- Do MMR (slot-level) and dedup (context-level) double-remove, and is the guard enough, or should dedup run *before* MMR? The order sweep is the experiment; a correct answer today is a number, not a preference.
- Does handing the refusal gate only the fused score (not the rerank score) under-refuse — or is that the *correct* conservative stance until a real reranker exists? Worth stating loudly on Day 21.
- Is a real cross-encoder's relevance worth its N ledger lines? Day 9's arithmetic is free; the paid tier must prove it outranks free on the same table before it ships.

### Tomorrow

Day 10 — Code RAG: the corpus stops being prose docs and becomes Saleor source. Chunks become **files → functions → classes → symbols**, dependencies and imports become retrieval metadata, and "retrieval" now has to answer "where is checkout handled?" from code, not docs. Day 9's seams inherited: the reranker/MMR/context-builder carry over unchanged; the *ingestion* changes underneath them. The backlog Day 9's comparison still shows open — any retrieval-owned rows, the `PENDING` cross-encoder question, the `PENDING` lost-in-the-middle verdict — rides forward too, and Day 21's benchmark inherits today's table as its baseline.