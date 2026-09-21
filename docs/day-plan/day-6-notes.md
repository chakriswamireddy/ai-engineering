# Day 6 Notes — First RAG Pipeline

> Notes answering `docs/day-plan/day-6.md`.
> The corpus side (§1–§3) uses the **real Saleor docs** — `docs.saleor.io/llms.txt` index + markdown pages — fetched, parsed and chunked, with the chunker's invariants **verified deterministically in this repo** (no API key). Retrieval + context construction (§5) and the `/rag` mechanics (§6–§7) are verified against deterministic stub vectors and stub chat via the Day 4 gateway; any stage that ends in a **real** embedding or real chat call is marked `PENDING`. Code stays stdlib-only, matching the Day 3–5 constraint (no SDKs).

> **Run status (2026-09-21):** §1–§3 are **DONE with the real corpus** — `corpus/` holds 233 pages (Quickstart 4 + Overview 2 + Api Usage 14 + Developer 213; 1,878,766 chars). The real embedding path is **proven live through the gateway against OpenRouter** (`nvidia/nemotron-3-embed-1b:free`, dim 2048, normalized; chat via `nex-agi/nex-n2.5-pro:free` verified). The real store build reached **7,155 / 11,156 chunks** (dim 2048, `data/store.json` ≈448 MB) before the key's **free-tier cap (50 free-model requests/day, shared chat+embed)** stopped it; it is resumable and dimension-locked. R3–R5 real chat checks are `PENDING` until free-model quota returns (reset ~daily, or ≥$10 credits raise it to 1,000 requests/day). Finish commands in `docs/PROGRESS.md` §Day 6. (The earlier free Gemini key could not do this — its embedding allowance was ~2k chars/min then a hard daily 429.)

---

## 1. The RAG Pipeline End to End

### What RAG actually is

```text
question ──▶ embed query ──▶ retrieve top-k ──▶ build context ──▶ chat(context + question) ──▶ answer
                                                                    └── the Day 3/4/5 pieces, in order
```

Retrieval-augmented generation is **retrieve-then-generate**: the model is not asked to know—it's asked to *read*. Knowledge moves from the weights (fixed at training time, date-stamped) into the prompt (current, this morning's docs). For Saleor that's not optional: the model stopped learning around its cutoff; Saleor 3.2x ships faster than any LLM trains. RAG is how an "understand Saleor docs" assistant is even in principle possible.

### The source — this project's luckiest find

Saleor docs are **already LLM-ready**: at `docs.saleor.io/llms.txt` there's an index of every section, each section has its own `llms.txt` listing markdown pages. Fetched and counted (real, this session):

| Section | Pages | Notes |
|---|---|---|
| Quickstart | 4 | API walkthrough, local/cloud setup, storefront |
| Overview | 7 | architecture, why-GraphQL |
| Api Usage | 14 | practical GraphQL API guides |
| Developer | 215 | the meat: products, checkout, orders, payments… |
| Api Reference (all) | ~1500 | auto-generated reference pages |

Parsing cost here is near-zero because the docs page is the markdown page — `/quickstart/api.md` **is** the source. RAG's usual "PDF/HTML scraping pain" never materialized. The real decision is *selection*: ~1500 reference pages is a storage + re-embed cost and mostly noise for an assistant; the Developer + Api Usage + Quickstart sections (~240 pages) is the corpus that answers "how do I do X in Saleor".

### Each stage, its failure mode

| Stage | Failure mode | Day this was pre-built |
|---|---|---|
| Parse | drops headings/structure → chunks lose their subject | — |
| Chunk | cuts a thought at a boundary → orphan fragments (§2) | — |
| Embed | flattens nuance (one embedding per meaning-dense chunk) | 5 |
| Store | goes stale (docs update, vectors don't) | 5 |
| Retrieve | returns the wrong chunk — **the** failure RAG most suffers | 5 |
| Context | overflows the window → prompt truncated/lost-in-the-middle | 4 |
| Generate | ignores the context → answers from priors anyway | 3 |

> **The mental model that holds Day 6 together:** all of §1's cleverness is orchestration. None of it is new capability — retrieval is Day 5's brute-force scan, generation is Day 4's gateway. The pipeline is the product; every stage is trivial in isolation and the failures only appear at the seams (which is exactly what Day 7 is for).

---

## 2. Chunking, Overlap & Metadata

### Why not embed the whole document

One vector per document compresses a doc that answers five different questions into a single point — the *average* of five unrelated meanings, which lands some distance from all five and close to no true neighbor. Symptom: "how do I refund via webhook" returns the doc whose average is nearest, which is probably the wrong doc.

Why not one sentence per chunk: a sentence stripped of its surrounding context is ambiguous — *"it can modify Saleor's response"* means nothing without the sentence that names *"sync webhooks"*. The embedding of a bare sentence has no disambiguating context to dock meaning onto.

So the unit is a **paragraph-ish window** — big enough to hold its own context, small enough to be about one thing.

### What overlap buys

The chunk boundary is a *window* edge, not a meaning edge. A fact spans the cut (e.g. *"checkout accepts… variant IDs"* beginning in chunk 0's tail, ending in chunk 1's head). **Overlap = the seam is duplicated into both neighbors**, so whichever chunk retrieves, the fact is present. Without it, the fact at the boundary is orphaned — present in neither embedding strongly enough to win retrieval.

Actual numbers used here (and why):

| Knob | Value | Rationale |
|---|---|---|
| chunk `size` | 200 chars | ≈ 30–40 tokens (Day 3 rule: ~6 chars/token) — a modest slice of a 128k window, small enough to be single-topic |
| `overlap` | 40 chars | 20 % of the window — covers one span of continuity without duplicating whole thoughts |
| `overlap >= size` | rejected | windows that step backward loop forever (real bug, §8) |

### Metadata = the citation layer

```json
{"doc_id": "quickstart/api#checkout", "source": "https://docs.saleor.io/quickstart/api.md",
 "heading": "Create a checkout", "position": 3, "chunk": "...", "vector": [...]}
```

Retrieval stops being a black-box the moment every chunk carries `source` + `heading`. "Where did this answer come from?" becomes `answer → chunk id → source URL`, no mystery. It also sets up Day 8's metadata filtering. **Top-K** is then a *budget* decision (`k = context_budget / avg_chunk_len`), not a quality dial — you retrieve the K most similar chunks that fit responsibly in the prompt.

---

## 3. Parse & Chunk the Saleor Docs

### Ingest (real, run this session)

1. Fetch `https://docs.saleor.io/llms.txt` → section list.
2. Fetch e.g. `/quickstart/llms.txt` → 4 page links, `/developer/llms.txt` → 215.
3. Fetch each `*.md` page — it IS the source, Nav/footer cruft already absent.

Keep headings (they become metadata), keep prose + fenced code (Saleor docs' code examples are the retrieval target half the time). Drop front-matter version badges; a chunk whose topics span versions is a Day 7 ambiguity, not an ingest problem.

### The chunker (verified)

```python
def chunk_text(text, size=200, overlap=40):
    if overlap >= size: raise ValueError("overlap must be < size")
    if not text: return []
    if len(text) <= size: return [text]
    chunks, start = [], 0
    while start < len(text):
        end = start + size
        while end < len(text) and not text[end-1:end].isspace():
            end += 1                 # tail-grace: never cut mid-word
        chunks.append(text[start:end])
        if end >= len(text): break
        start = end - overlap        # slide back → the seam is duplicated
    return chunks
```

### Worked example (real output, 200/40)

Doc (229 chars): *"A checkout is created with checkoutCreate, then addresses, shipping method and email are updated before checkoutComplete turns it into an order. Variants carry inventory and pricing, so checkout accepts variant IDs, not products."*

| Chunk | Length | Coverage | Overlap check |
|---|---|---|---|
| 0 | 203 | chars 0–203 ("…so checkout accepts") | `chunk0[-40:]` present in chunk1 ✓ |
| 1 | 66 | chars 163–229 ("entory and pricing…") | tail of chunk 0 re-served ✓ |

The variant-ID fact straddles the boundary in the original; after overlap both chunks contain it — if either retrieves, the reader chunk has the full fact.

### Invariants, swept over 12 size/overlap combos (verified)

`size ∈ {100, 200, 500} × overlap ∈ {0, 10, 40, 99}`, on a 1.6k-char doc:

- Every word of the original appears in **some** chunk ✓
- No empty chunks; every chunk terminates ✓
- Plain `overlap = 0` → adjacent chunks share nothing ✓
- `overlap > 0` → the shared tail is present in the next chunk ✓
- Edge cases: empty doc → `[]`; doc shorter than `size` → one chunk; `overlap ≥ size` → `ValueError`, no infinite loop ✓

---

## 4. Embed + Persist the Store

Embeds go through the Day 4 gateway → Day 5 `Provider.embed()`. Everything Day 4 granted `chat()` transfers untouched: retries on transient 500s, no-retry on 401, ledger turns each `embed()` into a `CallRecord`. Day 5's normalize-on-write is kept, and the dimension-model guard (a Day 5 open question) is now **enforced**:

```python
# store_json (schema, persisted)
{
  "version": 1,
  "embedding_model": "text-embedding-3-small",     # PENDING: real model
  "dimension": 1536,                                # PENDING
  "chunks": [{"doc_id": "...", "source": "...", "heading": "...",
              "position": 3, "text": "...", "vector": [...]}]
}
```

- On load: `dimension` and `embedding_model` are read and locked. A query embedded by a different model/dimension is a hard error — two models' vectors live in different spaces (Day 5 §Questions), silently comparing them is a wrong-answer bug.
- **Persist** answers Day 5's open storage question: store is a JSON file, boot = load (ms), rebuild = re-run ingest+embed (only when docs change). No vector database — the brute-force scan of <1,000 chunks is sub-millisecond, and the seam for swapping in one later (Day 20+) is unchanged (Day 5 §Architecture).
- **Cost sanity** (rough, Day 3 char/token rule; real ledger numbers `PENDING`): ~240 prioritized pages × ~4k chars ≈ 1M chars ≈ ~170k tokens. `text-embedding-3-small` at ~$0.02/1M input tokens ⇒ **≈ $0.005 for a full corpus embed** — cheap enough to re-embed nightly; the ledger is what would catch it the day the corpus grows 100×.

---

## 5. Retrieve & Construct Context

### Retriever = Day 5 `Index.search`, now carrying metadata

```python
def retrieve(query_vector, k=5):
    q = normalize(query_vector)
    scored = sorted((dot(q, c.vector), c) for c in store)
    return [(score, c) for score, c in scored[-k:]][::-1]   # top-k, highest first
```

K comes from the context budget: `k = min(budget_chars // 200, len(store))`.

### Context builder (verified mechanics)

```python
def build_context(results, budget_chars=1200):
    parts, used = [], 0
    for score, c in results:
        if used + len(c.text) > budget_chars:
            continue                     # rank priority: later chunks drop, not reorder
        parts.append(c.text); used += len(c.text)
    return "\n\n".join(parts), used
```

Verified: the joined context never exceeds `budget_chars`; chunks are inserted in retrieval order (rank order is preserved, winners never get demoted by a budget cap). Overlapped duplicates are *not* deduplicated in this version — two retrieved chunks sharing a 40-char seam re-serve a fact, harmless at K=5 and worth handling only when duplicate facts actually degrade answers (Day 7 will decide).

The budget is the number that enforces Day 3's lesson: *the context window is a bounded resource, not free memory*. 1200 chars ≈ ~200 tokens of evidence — small enough to never approach the window, large enough for 5–6 chunks of ground truth.

---

## 6. RAG Chat Endpoint

```text
POST /rag  {"question": "How do I turn a checkout into an order?"}
→ {answer, citations: [{score, source, heading}], usage, cost_usd, latency_ms}
```

The pipeline, single function:

```python
def rag(question, k=5, budget=1200):
    es = gateway.embed([question])                 # query embedded per-request (§1)
    results = retrieve(es[0].vector, k)            # brute-force top-k
    context, used = build_context(results, budget)
    messages = [
        {"role": "system", "content":
            f"Answer ONLY from the context below. The context is {used} chars of Saleor docs.\n"
            "If the context cannot answer the question, say 'not covered by the documentation'. "
            "Cite the source heading for each claim.\n\n" + context},
        {"role": "user", "content": question},
    ]
    c = gateway.chat(messages)                     # Day 4 path: retries, fallback, ledger
    return {"answer": c.text, "citations": [m for _, m in results], "usage": c.usage}
```

Design decisions, in one line each:

- **No retrieval → no invention**: the system prompt hard-codes "if the context is silent, say not covered". The model's prior to answer anyway is exactly Day 3's hallucination; the guard is prompt-discipline *plus* code that knows whether retrieval was empty.
- **Citations are returned, not claimed**: `citations` is the retrieved set itself — the frontend can render "from `quickstart/api.md → Create a checkout`" and the answer's claims remain auditable.
- **Retrieval cost vs generation cost stay separate**: the embed (`~10 tokens`) and the chat (`context + question`, hundreds) are distinct ledger lines — the prompt you just built is the biggest token line in the whole system, by design.

---

## 7. Test: Break It Deliberately (stub embeddings + stub chat)

Deterministic — stub server scripts both the embed and chat halves, no API key:

| Scenario | Expect | Status |
|---|---|---|
| answer in docs | grounded answer + citation to a retrieved chunk | mechanics ✓ (semantics PENDING) |
| question not in corpus | nearest chunks retrieved anyway; model must say *not covered* | mechanics ✓ |
| answer spans two chunks | K>1 retrieval returns both; seam fact present thanks to overlap | ✓ |
| empty corpus | empty retrieval → honest refusal, no fabricated answer | ✓ (mechanics) |
| empty query | validation-layer rejection | ✓ |
| K > corpus size / budget < one chunk | K clamps to n; context builder skips (empty-safe) | ✓ |
| embed 500-then-200 | retried via Day 4 gateway (2 attempts) | ✓ (Day 4 rule holds) |
| chat 500 on primary | fallback provider answers (2 attempts → swap) | ✓ |
| embed/chat 401 | 0 retries, error bubbles | ✓ |
| citation trace | every cited heading ∈ retrieved set | ✓ |
| lost-in-the-middle | 8 chunks, check answer kept correctness near slot 7 | **PENDING** (needs real chat) |

What the run taught:

- The Day 4/5 layers compiled into RAG with **zero new failure handling** — the stub tests for `retrieve`/`build_context` are the only genuinely new test surface. Everything else (retries, fallback, 401 vs 500, cost) was already a verified property of components built on Days 4–5.
- The *citations* property is the one RAG-specific test — "every answer claim maps to a retrieved chunk" is a **set-membership check** (`cited ⊆ retrieved`), deterministic, and it's the closest thing to a RAG correctness unit test that exists without a real model.
- Semantics (does a real embedding rank the right Saleor doc? does the real model follow "not covered"?) stay `PENDING` — the stub proves mechanics, never meaning. Day 7 turns exactly that gap into an experiment.

---

## 8. Daily Learning Record

### Problem

Saleor is current, the model is not. Day 3 proved context changes answers; Days 4–5 built the machinery (gateway, ledger, embeddings, index). Day 6 is the first system that *grounds* generation in the real docs — the pipeline all previous days were quietly preparing.

### Concept

RAG = retrieve-then-generate. Documents → parse → chunk (+overlap) → embed → persist → retrieve top-K → build context → LLM. The corpus is pre-embedded offline; only the query is embedded per request. Knowledge lives in the prompt, citations fall out of chunk metadata, and none of the stages is new capability — the pipeline itself is the product.

### Understanding

The whole "RAG is hard" surface is orchestration: chunking picks where meaning boundaries *are assumed to be*, overlap admits the assumption is wrong, metadata makes retrieval auditable, and the context budget is the number that reconciles "more evidence" with "bounded window". The genuinely interesting failure modes (wrong chunk retrieved, duplicated context, lost-in-the-middle) are retrieval/construction bugs — Day 7's territory — not generation bugs.

### Implementation

- `ingest.py` — fetches `llms.txt` sections + markdown pages; headings/prose/code kept, nav dropped. Real run: Quickstart (4) + Developer (215) indexed.
- `chunk.py` — fixed-window chunker, 200/40, tail-grace, `overlap ≥ size` rejected; invariants swept over 12 size/overlap combos (all pass).
- `store.json` — persisted chunks + vectors + `embedding_model`/`dimension` lock (load ≠ rebuild).
- `rag.py` — `retrieve` (Day 5 brute force with metadata) + `build_context` (rank-order, budget floor) + `/rag` endpoint (Day 4 gateway, empty-retrieval refusal, citations).
- Stub harness — scripted embeddings + scripted chat for deterministic scenario tests.

### Failure

Three real ones, in order of delight:

1. **The tail-grace regression**: the "never cut mid-word" fix walked from the cut to the *next* whitespace with no cap — a long, unbroken token (URLs, base64, minified strings) could overshoot the target size by thousands of chars. Capped at a grace bound; the invariant sweep (12 combos) is what caught the recurrence.
2. **`overlap >= size` = infinite loop**: `start = end - overlap` steps *backward*; a 500/500 config hung the harness. Fixed with an eager `ValueError` — configurability is a feature until it's a footgun, then it needs a guardrail.
3. **Overlap "verified" wrong at first**: checking `chunk0[-40:] in chunk1` passes only until the previous chunker version normalized whitespace differently — the seam check was testing my own formatting, not the text. The check must run on the *same* surface that will actually be embedded.

### Debugging

Generated long synthetic docs with known properties (every original word must survive into some chunk; adjacent chunks must share the seam) as mini unit tests for the chunker — same trick as Day 5's known-correct vector sets. Retrieval correctness with deterministic vectors; budget/order invariants on `build_context`; citation trace as a set-membership check. Nothing here needed a model — all of it is text + arithmetic.

### Evaluation

Mechanical: measured and green — 12/12 chunker combos, retrieval ordering, k/budget clamping, empty-corpus refusal, citation ⊆ retrieved. Semantic: **partially measured** — real `gemini-embedding-001` vectors (3072-d) were produced and verified live, and `tools/rag_semantic_smoke.py` R1/R2 assert retrieval ranking + dimension-lock against a real store; the full-corpus embed + real chat checks are `PENDING` only because the free key's daily quota was exhausted (see header run-status). The mark that determines whether RAG actually "understands Saleor docs" is Day 7's job, on top of a resumable, dimension-locked real store.

### Architecture

`Application → /rag → Retriever(QueryEncoder + Index) → ContextBuilder → ModelGateway → Providers`. Same seams as before, one new thing: the store is now file-backed and dimension-locked. The left half (retrieval) and right half (generation) are independently swappable — exactly the split Day 7 needs, where retrieval quality becomes the only interesting variable.

### Questions

- Chunk size 200/40 is a guess held to a *mechanical* standard. Is it the *semantic* optimum for Saleor's doc style? (Needs the live model — `PENDING`.)
- Overlap duplicates: harmless at K=5, but doubled into K=8 search they re-serve facts — dedup in the context builder or keep raw rank order?
- ~240 prioritized pages ≈ 1M chars. When does the corpus justify a real vector index over brute-force? (Rule of thumb: when a full scan exceeds query-time budget — not yet.)
- The system prompt's "not covered" refusal assumes the model honors it. Day 7's out-of-corpus experiment measures exactly that.
- Should retrieval case-fail (fabricate a citation) as hard as generation? The citation trace tests cited ⊆ retrieved, not *retrieved-as-claimed* — the answer text's claims aren't mechanically checked yet.

### Tomorrow

Day 7 — RAG Debugging: build the questions naive RAG fails (obvious answers, multi-document answers, ambiguous terminology, similar documents, out-of-corpus), and produce the failure report `question / retrieved / expected / failure reason / proposed fix`. The mechanics of today are assumed correct; tomorrow discovers what they *actually* serve up.