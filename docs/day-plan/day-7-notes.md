# Day 7 Notes — RAG Debugging

> Notes answering `docs/day-plan/day-7.md`.
> Day 7 is the **first day that cannot be finished on stub vectors**: debugging RAG is entirely a question of *semantics*, and stub embeddings carry none. So this document is deliberately split into what is **mechanical** (harness, budget, duplicates, citation membership, threshold arithmetic — deterministic, no model) and what is **semantic** (which doc actually ranks where, whether the model actually reads it — `PENDING`, requires the real embedder and a real chat call).
> Every row of the failure report carries a status: `MECH` (provable without a model) or `PENDING` (needs one). No retrieval scores are invented here — the numbers are the *run's* to produce, and the job of these notes is to make that run mechanical and unambiguous.

---

## 1. Why Naive RAG Fails — The Layered Model

Day 6 built `question → embed → retrieve → build_context → chat → answer` and declared its semantics `PENDING`. Day 7 is the bill for that marker.

The single most important idea of the day:

```text
query ─┬─▶ [1] query encoding        did the question embed into the right region of the space?
       ├─▶ [2] retrieval             did the right chunk win a top-K slot?
       ├─▶ [3] context construction  did it survive the budget, undiluted?
       └─▶ [4] generation            did the model read it, or answer from priors anyway?
```

A wrong answer is never one thing. It is one of these four, and from the outside **they are indistinguishable**:

- A retrieval miss produces an answer from the wrong chunk — fluent, cited, wrong.
- A generation failure produces an answer that ignores a perfectly good context — fluently, confidently, wrong.

Same output, opposite causes, opposite fixes. That is precisely why naive RAG gets debugged by vibes, and why the plan's Day 7 is an *experiment* day rather than a *coding* day. The objective is not a better pipeline; it's the ability to say **"this was a retrieval problem"** with evidence.

### The two ways a query misses

| Gap | Example | Why it happens |
|---|---|---|
| **Lexical** | user asks "how do I cancel an order"; the corpus talks about `orderCancel` and fulfillment | sharing no surface tokens, nothing points the embedder at the right region |
| **Semantic** | user asks "why is my cart stuck"; the corpus says "checkout completion requires a shipping method" | the question's *framing* (symptom) differs from the corpus's framing (mechanism) |

Both are query-side failures that *look* like corpus failures. Neither is fixed by a bigger K.

> **Mental model that holds Day 7 together:** RAG is **two independent systems** — a search engine and a reader — glued together by one string. Day 6 glued them. Day 7's whole purpose is to find the seam and prove which side tore.

---

## 2. The Failure Taxonomy

### The graded ladder of retrieval outcomes

Retrieval never simply "works" or "fails". There is a ladder, and the dangerous rungs are in the middle:

| Rung | Outcome | Danger |
|---|---|---|
| 1 | correct doc, **rank 1** | success |
| 2 | correct doc, rank 2…K | survivable — but context ordering starts to matter (§6) |
| 3 | right topic, **wrong section** | answer is close but has a hole |
| 4 | right topic, **wrong entity** (`checkout` vs `checkoutComplete`) | **most dangerous** — plausible and wrong, with a real citation |
| 5 | topically adjacent, answers nothing | noise consumes the budget |
| 6 | nothing relevant exists | corpus gap — the *correct* behaviour is refusal |

Rung 4 is why "was a document retrieved?" is not the same question as "was the *right* document retrieved?", and why the fixture in §8 asserts on **expected doc IDs**, not on "non-empty results".

### The plan's five classes, mapped to layers

| Class | Layer | Symptom | Mechanically detectable? |
|---|---|---|---|
| **Irrelevant retrieval** | retrieval | top-K full of off-topic chunks, and the answer cites them | `PENDING` (needs semantic judgement), but score distribution hints |
| **Missing retrieval** | retrieval | expected doc is absent from top-K entirely | **`MECH`** — `expected ∈ retrieved` is a set test |
| **Excessive context** | context construction | low-rank chunks eat the budget; the answer dilutes | `MECH` for the char count; `PENDING` for the correctness effect |
| **Duplicated context** | context construction | overlap / near-duplicate chunks occupy slots | **`MECH`** — duplicate ratio |
| **Hallucination** | generation | answer asserts beyond the context; or refuses when it shouldn't | `MECH` for `cited ⊆ retrieved`; `PENDING` for claim-level grounding |

And the class the plan lists as a *question family* but not as a failure: **corpus gap**. It is not a bug. It becomes a bug only if the system **fails to detect it** — which is exactly what naive RAG does, because `retrieve` always returns K neighbors whether or not a good one exists.

---

## 3. The Five Question Families

Expected sources are written **before** running. That pre-registration is the line between a report and curve-fitting.

### Family A — obvious (the control)

| ID | Question | Expected source | Stresses | A failure means |
|---|---|---|---|---|
| A1 | "What is a checkout in Saleor?" | `quickstart/api.md` (Create a checkout) | pipeline sanity | ingest/chunk fault — stop and fix before measuring anything else |

Family A is the **baseline**. If A1 fails, no other result is interpretable: you are debugging the corpus, not the retrieval.

### Family B — multi-document (the seam)

| ID | Question | Expected source | Stresses | A failure looks like |
|---|---|---|---|---|
| B1 | "How does a checkout become an order, and what changes after?" | checkout-create + checkoutComplete + order pages | top-K breadth, overlap seam | answer covers creation, silently omits completion → recall@K too low, or all K slots collapse into one cluster |
| B2 | "What must be set before `checkoutComplete` succeeds, and what does it return?" | api-reference + checkout guide | a fact split across guide prose and reference | half the fact retrieved confidently |

B1 is the one Day 6's overlap was built for — the fact straddling a chunk boundary. Day 7 checks whether that protection actually paid off.

### Family C — ambiguous terminology (one query vector, several meanings)

| ID | Question | The ambiguity | A failure looks like |
|---|---|---|---|
| C1 | "How do apps work in Saleor?" | **App** (Saleor App framework, `appInstall`, `App` type) vs **Django app** (a code module) vs app-store | retrieved chunks split across meanings; the answer straddles them or picks the wrong one |
| C2 | "What's the difference between an attribute and a field?" | product **attribute** (business data) vs GraphQL **field** (schema) | two definitions retrieved, neither clearly the answer |

C1 is Day 6 §2's **average-of-meanings problem, now at query time**. Day 6 used it to justify chunking a document instead of embedding it whole; Day 7 shows the *identical* failure at the query: one vector for a word with three senses lands between all three.

### Family D — similar documents (ranking stability)

| ID | Question | The near-duplicate pair | A failure looks like |
|---|---|---|---|
| D1 | "How do I complete a checkout?" | `checkout` vs `checkoutComplete` reference pages | top-5 is 4 near-dupes of one meaning; the actual how-to never surfaces, and the ranking between the twins is arbitrary |
| D2 | a question whose answer is in `quickstart/api.md` and also in a developer guide | two guides covering the same flow | the same fact served twice; one slot wasted, budget diluted |

D1 tests **diversity**, which pure cosine top-K has no concept of: near-identical vectors produce near-identical scores, and ties resolve by whatever order the store happened to be in.

### Family E — out of corpus (the refusal path)

| ID | Question | Why nothing answers it | A failure looks like |
|---|---|---|---|
| E1 | "How much does Saleor Cloud cost?" | pricing is not in the docs corpus | an **invented** price, fluently stated |
| E2 | "How do I integrate Saleor with Shopify?" | adjacent docs exist (apps, integrations) but no Shopify page | a stretched integration answer — the *near-miss* refusal, harder than E1 |

E1 is easy to get right by luck. **E2 is the real test**: retrieval returns topically-close chunks with non-trivial scores, so the "refuse when there is nothing" rule has to fire *through* plausible-looking context. Naive RAG, which always returns K neighbors, has no mechanism for this at all.

---

## 4. The Debug Harness — Instrumentation & Isolation Tests

### The trace record (one per query)

```jsonc
{
  "id": "B1",
  "question": "How does a checkout become an order, and what changes after?",
  "retrieved": [                             // from the Day 6 retrieve()
    {"doc_id": "quickstart/api#create-a-checkout", "heading": "Create a checkout",
     "score": 0.83, "rank": 1, "chars": 203}
    // … K entries
  ],
  "expected": ["quickstart/api#create-a-checkout", "developer/checkout#completing"],
  "expected_in_retrieved": [true, false],    // MECH
  "expected_best_rank": 1,                   // MECH → feeds MRR
  "context_chars": 1180,                     // MECH
  "context_budget": 1200,                    // MECH
  "duplicate_chunks": 1,                     // MECH
  "top1_minus_topK": 0.11,                   // MECH → was there a real winner?
  "answer": "…",                             // PENDING (real chat)
  "citations": ["quickstart/api#create-a-checkout"],  // MECH: cited ⊆ retrieved
  "refused": false,                          // MECH
  "verdict": "retrieval-miss"                // assigned in §6
}
```

Three of these fields (`expected_in_retrieved`, `duplicate_chunks`, `cited ⊆ retrieved`) make most of the report **provable without a model** — which matters, because it means the report stops depending on an API key and starts depending on arithmetic.

### The two isolation tests — the heart of the day

**Test 1 — retrieval-only (no LLM).** Ask only: is the expected doc in top-K, at what rank? This is a pure function of the embedder and the index. It produces the retrieval half of every verdict.

**Test 2 — oracle substitution.** Replace retrieval with the ground truth: inject the *expected* document as context and ask the same question.

```python
def oracle_test(question, expected_doc_ids, gateway, store):
    ctx = "\n\n".join(text_of(d) for d in expected_doc_ids)   # perfect evidence
    return gateway.chat([{"role": "system", "content": GROUNDED_SYSTEM + ctx},
                         {"role": "user",   "content": question}])
```

| Retrieval (Test 1) | Oracle (Test 2) | Verdict |
|---|---|---|
| expected **missing** | — | **retrieval failure** → Day 8 |
| expected **present**, answer wrong | oracle **correct** | **retrieval/context fault** — retrieval let it through but the pipeline lost it → Day 9 |
| expected **present**, answer wrong | oracle **wrong too** | **generation failure** — even perfect evidence fails → prompt/grounding → Day 20/21 |

The oracle test is the whole day in one function. It is the only way to separate "searched badly" from "reads badly", and it costs one extra chat call.

### The diagnostic decision tree

```text
Wrong or ungrounded answer
  │
  ├─ expected doc ∉ top-K ────────────────▶ MISSING RETRIEVAL            → Day 8
  │     ├─ but top-1 score is high        → embedder can't separate the twins
  │     └─ top-1 score is low everywhere  → lexical gap / out-of-corpus
  │
  ├─ expected ∈ top-K, not in context ────▶ CONTEXT CONSTRUCTION         → Day 9
  │                                          (dropped by the budget floor)
  │
  ├─ expected ∈ context, answer still wrong
  │     ├─ duplicates/near-dupes crowd it ─▶ CONTEXT QUALITY             → Day 9
  │     └─ oracle test wrong too ─────────▶ GENERATION                  → Day 20/21
  │
  └─ no doc answers the question ─────────▶ CORPUS GAP — refusal is correct
```

Note the tree never terminates in "the model is bad" until the oracle test has failed. That ordering is the day's actual lesson.

---

## 5. The Failure Report

### Predicted profile — `HYPOTHESIS`, to be confirmed or killed by the run

Written *before* running, from the taxonomy alone. Its value is that it can be wrong in a way that teaches.

| ID | Family | Predicted failure class | Predicted cause | Fix it would route to |
|---|---|---|---|---|
| A1 | obvious | none (control) | — | — |
| B1 | multi-doc | missing retrieval (partial) | one query, one region; completion page never retrieved | multi-query decomposition (Day 8) |
| B2 | multi-doc | excessive + partial | fact split guide/reference; budget keeps the prose | rerank + ordering (Day 9) |
| C1 | ambiguous | irrelevant retrieval | one vector averaged across 3 senses of *app* | query rewriting / metadata filter (Day 8) |
| C2 | ambiguous | wrong-entity (rung 4) | GraphQL *field* docs outrank product *attribute* docs | query rewriting (Day 8) |
| D1 | similar | duplicated context | 4 of 5 slots are near-dupes of one meaning | rerank + diversity (Day 9) |
| D2 | similar | duplicated context | same fact in two guides, both retrieved | context dedup (Day 9) |
| E1 | out-of-corpus | hallucination | K neighbors always returned; no gate | similarity gate (ship today) |
| E2 | out-of-corpus | hallucination via near-miss | adjacent docs score plausibly | gate + threshold calibration |

### The report format (the deliverable)

`Question | Retrieved documents | Expected documents | Failure reason | Proposed fix` — plus the status column that keeps the document honest about what was measured:

| ID | Retrieved documents | Expected documents | Failure reason | Proposed fix | Status |
|---|---|---|---|---|---|
| A1 | `〈run〉` | `quickstart/api#create-a-checkout` | — (control) | — | `PENDING` |
| B1 | `〈run〉` | checkout-create, checkoutComplete, order | ? | multi-query (Day 8) | `PENDING` |
| C1 | `〈run〉` | App-framework pages only | ? | rewrite query (Day 8) | `PENDING` |
| D1 | `〈run〉` | `checkoutComplete…#completing` | ? | rerank + MMR (Day 9) | `PENDING` |
| E1 | `〈run〉` | *(none — must refuse)* | ? | similarity gate | `PENDING` |
| E2 | `〈run〉` | *(none — must refuse)* | ? | gate + threshold τ | `PENDING` |

The `Retrieved documents` column is deliberately empty. It is the *run's* output; filling it from memory would produce a report that documents nothing. What today produces with certainty is the **rows, the decision rules, and the gate** — the columns a real run fills in one pass.

---

## 6. The Five Failure Modes, In Depth

### Missing retrieval

The expected chunk is not in top-K at all. This is the only failure mode fully provable without a model, and it is the one that most often gets misdiagnosed as hallucination — the model "made something up" because it was handed nothing true.

Two sub-causes, distinguished by the score distribution:
- **Lexical gap** — the query shares no surface form with the doc. Top scores are uniformly mediocre. → hybrid keyword+semantic retrieval (Day 8).
- **Cluster collapse** — the query's nearest region contains the right topic but the answer chunk loses the K race to its own siblings (Family D). Top scores are *high* but the winner is a twin. → reranking (Day 9).

Same symptom, opposite diagnosis, and only the score distribution tells them apart.

### Irrelevant retrieval

Top-K is full of chunks that share the query's topic but not its question. The tell is over-retrieval: many chunks with middling scores and no clear winner (`top1 − topK` small). Ambiguous queries (Family C) are the standard producer — one vector averaged across senses.

### Excessive context

Day 6 set `budget_chars = 1200` and dropped chunks that didn't fit, rank-order preserved. Day 7 asks whether "fits" was the right test. Two distinct problems lurk:

- **The budget drops the winner.** A rank-4 chunk can be dropped to make room for rank 1–3; the floor that protected the budget can evict the answer.
- **Lost-in-the-middle.** Day 6 §7 flagged this `PENDING` with 8 chunks. Today it becomes an experiment: hold the question and the other chunks fixed, move the *expected* evidence from slot 1 to slot 7, and compare correctness. If correctness drops, attention over the context is not uniform — and **context ordering becomes a real variable**, not a nicety. → Day 9.

### Duplicated context

Day 6 §5 asserted overlap duplicates were "harmless at K=5, and worth handling only when duplicate facts actually degrade answers." Day 7 is exactly the "only when" — and the honest answer is that it must be measured, not assumed:

```python
def duplicate_ratio(context_chunks):
    texts = [c.text for c in context_chunks]
    return 1 - len(set(texts)) / len(texts)      # MECH
```

Two sources of duplication, and they behave differently:
- **Overlap seam** (Day 6's chunker) — the same 40 chars in two adjacent chunks. Harmless *if* the shared span isn't the answer; slot-wasting if it is.
- **Near-duplicate pages** (Family D2) — different chunks, same fact. Here the duplication is real signal that the corpus itself is redundant, and the fix is dedup at the context layer (Day 9), not the chunker.

### Hallucination — and its mirror

Two opposite generation failures, and naive RAG ships with both:

1. **Over-answering (Family E).** The context is silent; the model answers anyway. Day 6's entire defense was a prompt line — *"if the context cannot answer, say 'not covered'"*. That is a **hope, not a check**. Day 7 replaces it with a deterministic gate:

```python
def should_refuse(results, tau):
    """MECH: refuse before spending a generation call."""
    return (not results) or (results[0][0] < tau)
```

τ is not guessable — it is *calibrated* by the score separation between Family A (known answerable) and Family E (known unanswerable). If A's top-1 scores and E's top-1 scores overlap, no single τ works, and that overlap is itself the finding. Either way, the gate converts "the model should refuse" into "the system cannot answer", which is a decision code makes, not one a prompt requests.

2. **False refusal (the mirror).** The context *does* contain the answer, and the model still says "not covered". Day 6 never tested this. It is a generation failure that looks like a corpus gap from the outside — which is precisely why the refusal must be checked **against whether the expected doc was in context**, never taken at face value.

> The three-line summary of the day: *missing* → retrieval; *irrelevant* → retrieval (usually ambiguity); *excessive / duplicated* → context construction; *hallucination* → generation, unless the context was empty, in which case it is missing retrieval wearing a disguise.

---

## 7. Measurement — What a Stub Can Prove, and What Needs the Model

### `MECH` — deterministic, no model required

| Check | Definition | Why it's model-free |
|---|---|---|
| hit@K | any expected doc in top-K | set membership |
| recall@K | `\|expected ∩ retrieved_K\| / \|expected\|` | set arithmetic |
| MRR | `1 / rank` of first expected doc (0 if absent) | index lookup |
| duplicate ratio | `1 − distinct/total` in the built context | string comparison |
| context utilization | `context_chars / budget_chars` | arithmetic |
| top-1 score | the max cosine | arithmetic |
| declared-unanswerable flag | whether retrieval *should* have refused | threshold arithmetic |
| `cited ⊆ retrieved` (Day 6 property) | every citation is in the retrieved set | set membership |
| budget never exceeded | Day 6 invariant | arithmetic |

### `PENDING` — needs the real embedder and/or real chat

| Check | Why it needs the model |
|---|---|
| precision@K (are the *other* chunks relevant?) | requires judging relevance of text |
| grounding (does the answer's content follow from the context?) | requires reading |
| ambiguity resolution (did the correct sense of *app* win?) | semantic |
| lost-in-the-middle effect | semantic |
| τ calibration (is Family A separable from Family E?) | needs real embedding scores |

A useful split to keep: **`MECH` checks make the report reproducible; `PENDING` checks make it meaningful.** Today's report leans hard on the first column so that it can be built at all — and pre-writes the second column so that the real run fills it in one pass.

> Foreshadowing: the `PENDING` column is, almost exactly, Day 20's evaluation dataset (`question`, `expected evidence`, `expected answer`, `failure category`) and Day 21's retrieval metrics (Recall@K, Precision@K, MRR). Day 7 hand-builds, for nine questions, what Day 20 will systematize.

---

## 8. Fix Routing — Day 7's Output Is Day 8/9's Backlog

The point of a failure report is that it decomposes into work. Each failure class routes to exactly one owner:

| Failure class (from the run) | Proposed fix | Owning day |
|---|---|---|
| lexical gap (uniformly mediocre scores) | hybrid keyword + semantic search | 8 |
| ambiguity / cluster collapse | query rewriting, disambiguation, metadata filter | 8 |
| multi-hop answer needs 2+ docs | multi-query decomposition | 8 (full: 16) |
| near-duplicates fill top-K | reranking + diversity (MMR) | 9 |
| lost-in-the-middle | context ordering / compression | 9 |
| duplicated seam in context | dedup in the context builder | 9 |
| out-of-corpus answered anyway | similarity gate before generation | 7 (ship), 21 (measure) |
| grounding failure under oracle | prompt/grounding work | 20/21 |

Two properties make this table useful rather than decorative:
- **It is derived from evidence** — the class is assigned by the §4 tree, so the fix is not a preference.
- **It is short.** If the report produces twenty fixes, the report failed at ranking. The deliverable ends with a **shortlist** (what to do first), not a backlog.

### Fixtures — the failures become tests

Each confirmed failure is promoted to a permanent, model-free regression:

```python
FIXTURES = [
    {"id": "B1", "expected": {"quickstart/api#create-a-checkout",
                              "developer/checkout#completing"}},
    {"id": "C1", "expected": {"developer/apps#overview"},      "must_not": {"developer/apps#django"}},
    {"id": "D1", "expected": {"checkoutComplete#completing"},  "max_dupes": 1},
    {"id": "E1", "must_refuse": True,                          "tau": TAU},
]

def check(fixture, retrieved):
    if fixture.get("must_refuse"):
        return should_refuse(retrieved, fixture["tau"])        # MECH
    ids = {r["doc_id"] for r in retrieved}
    assert fixture["expected"] <= ids                          # recall@K = 1
    assert fixture.get("must_not", set()) & ids == set()
    return True
```

Day 8's improved retriever must keep these green. That is the concrete meaning of "improved": the same questions now pass checks that today's pipeline fails — which is the only honest way to answer the plan's Rule 4 (*"how much better, and on which dataset?"*).

---

## 9. Daily Learning Record

### Problem

Day 6 built retrieve-then-generate and marked its semantics `PENDING`. The pipeline produces answers; some are wrong; **nothing in the system says which layer lied.** Every previous day's failure had a stack trace. RAG's failures have fluent prose. Day 7's problem is to turn that prose into evidence.

### Concept

Layered failure attribution. Query encoding, retrieval, context construction, generation — each owns a class of failure, and the classes produce identical-looking output. The decisive tools are the **retrieval-only test** (does the right chunk even rank?) and the **oracle substitution** (give the model perfect evidence and see if it still fails). Plus the taxonomy: irrelevant, missing, excessive, duplicated, hallucination, and the non-bug, corpus gap.

### Understanding

RAG is two independent systems — a search engine and a reader — glued by one string, and every wrong answer belongs to exactly one of them. The output tells you nothing about which; only substitution experiments do. The genuinely surprising part: Day 6's most confident assumptions are exactly what Day 7 finds unverified. "Overlap duplicates are harmless at K=5", "the prompt makes the model refuse out-of-corpus", "the budget keeps the best chunks" — all were reasoned plausibly and none were measured. A report is what you get when reasoning meets a question set.

### Implementation

- `trace.py` — the per-query record (§4): retrieved top-K with scores/ranks, expected set, `expected_in_retrieved`, duplicate count, utilization, citations, verdict.
- `questions.yaml` — the nine pre-registered questions across families A–E, **expected sources written before running**.
- `oracle.py` — the isolation test: inject expected docs as context, compare with and without retrieval.
- `gate.py` — `should_refuse(results, tau)`, the deterministic out-of-corpus check (Day 6's prompt hope → Day 7's code decision).
- `fixtures.py` — the promoted `(question, expected_doc_ids)` regression set, model-free.
- `docs/failures/rag-naive.md` — the filled failure report + the shortlist.

### Failure

Two kinds, and the second is the one worth recording:

1. **The pipeline's failures** — the rows of the report (`PENDING` on a real run): the multi-doc question answered from one doc, the ambiguous query splitting across senses, the near-duplicates crowding top-K, the out-of-corpus question answered with an invented price. Expected, and the point of the exercise.
2. **The *debugging* failures** — which the day exists to prevent:
   - The first instinct on every wrong answer is **"the model hallucinated"**. It is wrong the majority of the time and unfalsifiable. The oracle test exists because of this instinct.
   - Writing expected sources **after** seeing what was retrieved. That is curve-fitting: the report then documents the pipeline's behaviour as if it were the requirement, and every "failure" politely disappears.
   - Trusting "non-empty results" as success. Rung 4 of the ladder (`checkout` vs `checkoutComplete`) is a *real citation* attached to a wrong answer — the retrieval test must assert on expected IDs, never on count.

### Debugging

The two isolation tests do all the work: retrieval-only isolates layers 1–2, oracle substitution isolates layer 4, and the gap between them is layer 3. Classification then follows the §4 tree mechanically — expected absent → retrieval; expected present but dropped → context; expected in context and still wrong → generation. Ambiguity vs lexical gap is separated by the score distribution (`top1 − topK` large with wrong winner = cluster collapse; uniformly low = lexical gap). Everything `MECH` runs without an API key; everything semantic is queued for the real gateway.

### Evaluation

Today's output is **not a metric — it is a report**, and that is deliberate: Day 8 and Day 9 cannot be evaluated without a baseline to improve against, and the report *is* the baseline. `MECH` checks (recall@K, MRR, duplicate ratio, utilization, `cited ⊆ retrieved`, gate arithmetic) are runnable immediately and become the fixtures. Semantic metrics (precision@K, groundedness, ambiguity resolution, τ separation) stay `PENDING` and, nine questions later, become precisely Day 20's golden dataset and Day 21's RAG benchmark. The one number Day 7 can hand forward with certainty: how many of nine questions have their expected doc in top-K today.

### Architecture

No new components in the pipeline — Day 6's `ingest → chunk → embed → store → retrieve → build_context → chat` is unchanged. What Day 7 adds is an **observability seam**: the retrieval trace (§4), which makes the otherwise-invisible layer boundary between retrieval and generation *addressable*. One candidate runtime change: the similarity gate, which moves the refusal decision from the prompt (layer 4) to retrieval (layer 2) — a small code change with a large semantic consequence, since it makes "I don't know" a property of the system rather than a request to the model.

### Questions

- Is chunk 200/40 the semantic optimum? Day 6 held it to a *mechanical* standard (invariants pass); Day 7's report is the first evidence about retrieval *quality* — and might indict the chunker (rung 3/4 failures) rather than the retriever.
- Can one τ separate Family A from Family E? Or do their top-1 score distributions overlap, making "out-of-corpus" undecidable by similarity alone? (Calibration is `PENDING`.)
- Is Family C ambiguity a **query** problem (rewrite the query) or a **corpus** problem (the docs genuinely conflate *app*)? The fix differs, and the report should be able to tell them apart.
- Does the budget ever evict a higher-value chunk than it keeps? If yes, Day 6's rank-order floor is the wrong rule.
- Does context *order* change correctness (lost-in-the-middle), or is that a paper claim that doesn't reproduce at K=5? Still `PENDING` from Day 6 §7.
- Are the overlap duplicates actually harmful, or was Day 6's "harmless at K=5" right? The duplicate ratio is `MECH`; the *harm* is semantic.

### Tomorrow

Day 8 — Retrieval Improvements: implement the shortlist today's report produced. Hybrid keyword + semantic search for the lexical gap, query rewriting for ambiguity, multi-query decomposition for the multi-hop questions, metadata filtering to shrink the search space — then re-run the nine questions and compare against Day 7's baseline. Day 7 measured the failures; Day 8's fix list is whatever the failure report's `Proposed fix` column actually says, and nothing else.
