# RAG Failure Report — the naive baseline (`rag-naive`)

> Day 7's deliverable, and — per the day's own rules — **counter-evidence to
> Day 6's happy path**: `docs/day-plan/day-6-notes.md` described what the
> pipeline *should* do; this document records where it *lies*. It is the
> baseline Day 8 and Day 9 must beat, and each row is a unit of that
> improvement: a `Proposed fix` with an owning day, never a complaint with no
> next action.
>
> Status discipline, from `docs/day-plan/day-7-notes.md`:
>
> - **MECH** — proven deterministically, no model (this repo's `test_day7.py`).
> - **PENDING — the `⟨run⟩` cells.** No retrieval score or retrieved-doc
>   column is *filled by hand*. They are the run's to produce:
>   `python tools/rag_debug.py` fills them; until a real run, they are
>   `⟨run⟩`. An invented number here would make the report documentation of
>   nothing.
>
> There is no `docs/concepts/rag.md` in this repo yet — the happy-path claims
> it would hold are the fields this report indicts. Until it exists, the code
> being audited is `ai_os/rag.py` (retrieve/build_context/run_rag).

---

## 1. The layered model — where each wrong layer hides

RAG is **two independent systems — a search engine and a reader — glued by
one string** (`ai_os/rag.py:run_rag`). A wrong answer is one of these four,
indistinguishable from the outside, opposite fixes from the inside:

```
query ─┬─▶ [1] query encoding      did the question embed into the right region?
       ├─▶ [2] retrieval           did the right chunk win a top-K slot?
       ├─▶ [3] context construction did it survive the budget, undiluted?
       └─▶ [4] generation          did the model read it, or answer from priors?
```

The decision rule that makes this a *report* and not an argument:

```
Wrong / ungrounded answer
  ├─ expected doc ∉ top-K ───────────────▶ MISSING RETRIEVAL  → Day 8
  ├─ expected ∈ top-K, ∉ prompt ─────────▶ CONTEXT CONSTRUCTION → Day 9
  ├─ expected ∈ prompt, still wrong
  │     ├─ dupes/near-dupes crowd it ─────▶ CONTEXT QUALITY     → Day 9
  │     └─ oracle test wrong too ────────▶ GENERATION          → Day 20/21
  └─ no doc can answer ──────────────────▶ CORPUS GAP — refusing is correct
```

The tree never terminates in "the model is bad" until the oracle test has
failed. That ordering is the day's actual lesson.

## 2. The pre-registered question set (nine questions, families A–E)

Expected sources were written **before** the run — pre-registration is the
line between a report and curve-fitting (`ai_os/questions.yaml`). The
`Predicted` column is the hypothesis each question was chosen to indict;
the `⟨run⟩` columns decide it.

| ID | Family | Question | Expected docs | Predicted failure | Fix it would route to |
|---|---|---|---|---|---|
| A1 | A — control | What is a checkout in Saleor? | `quickstart/api#create-a-checkout` | none | — (if failed: ingest/chunk fault) |
| B1 | B — multi-doc | How does a checkout become an order, and what changes after? | checkout-create, checkoutComplete lifecycle, order lifecycle | missing retrieval (partial) | multi-query decomposition (Day 8) |
| B2 | B — multi-doc | What must be set before `checkoutComplete` succeeds, and what does it return? | api-guide + lifecycle completing | excessive context + partial | rerank + context ordering (Day 9) |
| C1 | C — ambiguous | How do apps work in Saleor? | apps-overview (App framework) | irrelevant retrieval (3 senses averaged) | query rewriting / metadata filter (Day 8) |
| C2 | C — ambiguous | What's the difference between an attribute and a field? | attributes overview + input types | wrong entity (rung 4) | query rewriting (Day 8) |
| D1 | D — similar | How do I complete a checkout? | api-guide completing | duplicated context | rerank + MMR diversity (Day 9) |
| D2 | D — similar | How do I create a checkout? | quickstart + api-guide session | duplicated context | context dedup (Day 9) |
| E1 | E — out-of-corpus | How much does Saleor Cloud cost? | *(none — must refuse)* | hallucination | similarity gate (ship today) |
| E2 | E — out-of-corpus | How do I integrate Saleor with Shopify? | *(none — must refuse)* | hallucination via near-miss | gate + threshold calibration |

## 3. The failure report — run-fillable rows

Run it: `python tools/rag_debug.py --stub` (offline mechanics) or
`python tools/rag_debug.py` (real store/gateway; chat columns skip cleanly
when the free tier is spent). Each row gains the five deliverable columns
(note: the offline stub already proves the mechanics columns and is green in
`test_day7.py`):

| ID | Retrieved documents | Expected documents | Failure reason | Proposed fix | Status |
|---|---|---|---|---|---|
| A1 | `⟨run⟩` | `quickstart/api#create-a-checkout` | — (control) | — | `PENDING` |
| B1 | `⟨run⟩` | create-a-checkout, completing-checkout, order lifecycle | ? | multi-query decomposition (Day 8) | `PENDING` |
| B2 | `⟨run⟩` | api-guide + lifecycle completing | ? | rerank + ordering (Day 9) | `PENDING` |
| C1 | `⟨run⟩` | apps-overview only (`must_not` app-store) | ? | rewrite query / metadata filter (Day 8) | `PENDING` |
| C2 | `⟨run⟩` | attributes overview + input types | ? | query rewriting (Day 8) | `PENDING` |
| D1 | `⟨run⟩` | api-guide completing | ? | rerank + MMR (Day 9) | `PENDING` |
| D2 | `⟨run⟩` | quickstart + api-guide session | ? | context dedup (Day 9) | `PENDING` |
| E1 | `⟨run⟩` | *(none — must refuse)* | ? | similarity gate | `PENDING` |
| E2 | `⟨run⟩` | *(none — must refuse)* | ? | gate + threshold τ | `PENDING` |

Expected doc membership, ranks, recall@K/MRR, duplicate ratio, context
utilization and the gate's arithmetic are **MECH** and already provable —
they are what `tools/rag_debug.py --stub` and `test_day7.py` assert right
now. What stays `PENDING` until a real embedder and a real chat call exists:
which doc actually ranks where, and whether the answer read it.

## 4. The two isolation tests (the only attribution tools)

```text
Test A — retrieval-only (no LLM):  `trace.retrieve_trace(gateway, store, q, expected=…)`
          does the expected doc rank inside top-K? pure function of embedder + index.

Test B — oracle substitution:      `oracle.oracle_test(gateway, store, q, expected_doc_ids)`
          inject the expected doc as the ONLY context; same question; perfect evidence.

| retrieval | oracle      | verdict                                |
|-----------|-------------|----------------------------------------|
| missing   | —           | retrieval failure      → Day 8
| present   | correct     | retrieval/context lost it → Day 9
| present   | wrong too   | generation failure     → Day 20/21     |
```

The two tests put every row of §3 in exactly one layer. Output is never
"the model is bad"; it is a layer with a citation to the test that proved it.

## 5. The out-of-corpus gate (the one *decision* Day 7 owns)

Naive RAG's retrieval `always` returns K neighbours — the honest refusal is
a prompt request, not a system property. `gate.should_refuse(results, τ)`
makes refusal a code decision, `MECH` by construction:

```python
refuse ⇔ (no results) or (top-1 cosine < τ)
```

- τ is **not chosen** — it is calibrated on the top-1 separation between
  Family A (known answerable) and Family E (known unanswerable). If the
  distributions overlap, no single τ works, and the overlap is itself the
  finding (`PENDING` — needs a real run).
- The trace records what the gate *would* have decided next to what the
  model *actually* did (`trace["gate_refused"]` vs `trace["refused"]`), so
  the report measures both refusal systems against each other.

## 6. Fixtures — the failures become permanent regression tests

Every row that fails becomes a model-free fixture (`ai_os/fixtures.py`,
backed by `ai_os/questions.yaml`): a question + expected doc_ids asserting
`expected ⊆ retrieved`, plus the `must_not` (C1's wrong sense) and
`must_refuse` (E1/E2) forms. "Day 8 improved the retriever" is *defined* as:
the same questions now keep these green. The fixture checks are `MECH`; the
semantic verdicts they can't make are exactly Day 20's golden dataset and
Day 21's retrieval benchmark.

## 7. The shortlist — what the report should decompose into

Deliberately short, ranked by expected impact ÷ effort. Fixes live on the
day that owns them; this is what "Day 8 starts" means.

| Priority | Fix | Class it fixes | Day |
|---|---|---|---|
| 1 | similarity gate (pagina `should_refuse` into `run_rag` before generation) | out-of-corpus answered anyway | 7 (shipping), 21 (measure) |
| 2 | hybrid keyword + semantic retrieval | lexical gap (`top1` uniformly low) | 8 |
| 3 | query rewriting / disambiguation | ambiguity (Family C, `top1 − topK` small) | 8 |
| 4 | multi-query decomposition | multi-hop questions (B1) | 8 (full: 16) |
| 5 | reranking + diversity (MMR) | near-duplicate crowding (D1) | 9 |
| 6 | context dedup + ordering | duplicated seam / lost-in-the-middle (D2, B2) | 9 |
| — | prompt/grounding work | generation failures under a correct oracle | 20/21 |

If this were twenty fixes, the report would have failed at ranking. It is
six, and the first is shippable today.

---

### Exit-check answers (Day 7's last task)

- **How do I decide retrieval vs generation without guessing?** Run the
  isolation pair: if the expected doc is absent from top-K → retrieval; if
  it is present but the oracle test is still wrong → generation. The oracle
  test is the only thing that separates "searched badly" from "reads badly".
- **Retrieved but not used?** The doc won a top-K slot and the context
  budget dropped it (or duplicates crowded it) — the context-construction
  layer owns that, and no amount of better retrieval fixes it.
- **Which class is not a bug?** Corpus gap. The correct system behaviour is
  refusal — which is why the deliverable ships a code-level gate, not
  another prompt line.
- **Why does low `top1` matter more than low top-K?** K neighbours always
  exist; top-1 is the strongest single signal of "is there a good match at
  all", and its gap to top-K tells you whether the winner was real or
  arbitrary. That is the gate's metric and the ambiguity detector.
- **What did this give Day 8 and Day 9?** A baseline (`⟨run⟩` rows), a
  decision tree that names the layer, an oracle that proves it, a gate that
  enforces "I don't know", and fixtures that measure the improvement —
  instead of "it seems better".