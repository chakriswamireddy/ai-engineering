# Day 6 — First RAG Pipeline

## Objective

Make the AI understand Saleor documentation. Chain every piece built so far — Day 3's `chat()`, Day 4's gateway, Day 5's embeddings and brute-force index — into the first end-to-end RAG pipeline: documents → parse → chunk → embed → store → retrieve → context → LLM.

## Duties Outline

### 1. Learn: The RAG Pipeline End to End (45 min)

- [ ] Define retrieval-augmented generation: **retrieve first, then generate with the retrieved text in the prompt** — never a vector database, never a magic retrieval step.
- [ ] Why RAG exists for *this* system: Sell me on it — the model's knowledge stops at its cutoff, Saleor 3.23 changes every month; the docs are current, the weights are not.
- [ ] Map each stage to its failure mode: parse drops structure, chunk cuts a thought in half, embed flattens nuance, store goes stale, retrieve misses the right doc, context overflows the window, LLM ignores the context anyway.
- [ ] Why the corpus is embedded **offline** (once, at ingest) but the query is embedded **per request**.
- [ ] Discover the source: Saleor docs serve LLM-ready markdown with per-section indexes (`docs.saleor.io/llms.txt`). Count pages per section (Quickstart 4, Developer 215, API reference ~1500+) and decide which sections justify embedding cost.

### 2. Learn: Chunking, Overlap & Metadata (45 min)

- [ ] Why a whole doc is a poor retrieval unit (one embedding = average of all its topics) and a one-sentence chunk is worse (loses the connective context that disambiguates).
- [ ] What **overlap** buys: information that sits on the cut boundary belongs to *both* neighbors; a sliding window duplicates the seam instead of losing it.
- [ ] The tension you're balancing: chunk size vs embedding resolution vs context budget vs retrieval quality.
- [ ] **Metadata** as the citation layer: source URL, heading, position — retrieval stops being a black box the moment every chunk can say where it came from.
- [ ] Top-K: retrieval returns K candidates, never one; K is a *context-budget* decision, not a quality knob.

### 3. Build: Parse & Chunk the Saleor Docs (1.5 hours)

- [ ] Fetch a section index (`/llms.txt`) + its pages as markdown — near-zero parsing, the docs are already LLM-shaped.
- [ ] Strip navigation/cruft, keep headings + prose + code blocks.
- [ ] Implement a `Chunker`: fixed-size window with configurable overlap, tail-grace so it never cuts a sentence.
- [ ] Verify invariants deterministically: every original word survives into some chunk, no empty chunks, adjacent chunks actually share the overlap, `overlap >= size` is rejected.
- [ ] Attach metadata to every chunk: `doc.id`, `source_url`, `heading`, `position`.

### 4. Build: Embed + Persist the Store (1 hour)

- [ ] Reuse the Day 5 `Provider.embed()` through the Day 4 gateway — retries, ledger, cost, all come free.
- [ ] Embed each chunk and write into the Day 5 `Index` normalize-on-write (the zero-vector bug from Day 5 must not come back).
- [ ] Persist the store to disk (JSON): store `version`, `embedding_model`, `dimension`, and chunks+vectors so boot ≠ rebuild.
- [ ] Enforce model/dimension consistency on load and on query — a mismatched query is an error, not a silent wrong answer.

### 5. Build: Retrieve & Construct Context (1 hour)

- [ ] The Day 5 `Index.search` becomes the retriever — now returning chunks with metadata, not just scores.
- [ ] Top-K retrieval with K derived from a **context budget** (`budget_chars` / average chunk length).
- [ ] Context constructor: join retrieved chunks in rank order, respect the budget floor, drop-by-duplicate where overlap pollutes.
- [ ] Show retrieval cost separately from generation cost — embedding tokens and chat tokens are different bills.

### 6. Build: RAG Chat Endpoint (1.5 hours)

- [ ] `POST /rag`: query → embed → retrieve top-k → build context → `gateway.chat()` with a system prompt + context + question → answer.
- [ ] Return **citations** (chunk metadata) alongside the answer — every claim is traceable to a retrieved chunk or it isn't RAG.
- [ ] System-prompt discipline: "answer from the context only; if the context is silent, say so" — no-fact = no-answer beats hallucination.
- [ ] Empty-retrieval path: no relevant chunks → a truthful "not in the corpus" response, never a made-up one.

### 7. Test: Break It Deliberately (45-60 min)

With the stub server scripted for **both** embeddings and chat (deterministic, no API key):

- [ ] Question whose answer is in the docs → grounded answer, cites a real chunk.
- [ ] Question *not* in the corpus → nearest neighbors retrieved anyway; model must say "not covered".
- [ ] Answer that spans two chunks (needs K > 1) — the seam case overlap protects.
- [ ] Empty corpus, empty query, K beyond corpus size, budget smaller than one chunk.
- [ ] Embed 500 → gateway retries; chat 500 → fallback provider; 401 → no retry (Day 4 rules hold).
- [ ] Citation trace: every chunk cited by the answer is actually in the retrieved set.
- [ ] Lost-in-the-middle: 8+ retrieved chunks, is the answer still correct on the 2nd vs 7th?

### 8. Documentation (20 min)

- [ ] Write `docs/concepts/rag.md` summary.
- [ ] Fill the Day 6 Daily Learning Record (Problem/Concept/Understanding/.../Tomorrow).
- [ ] Record today's token economics: retrieval (embedding) tokens vs generation tokens, with ledger numbers.

## Deliverable

A working Saleor RAG assistant:

```text
Ask a question
      ↓ (embed query — Day 5 provider via Day 4 gateway)
Vector
      ↓
Chunk store (parse → chunk → embed → persisted; Day 5 index)
      ↓ (brute-force top-k)
Retrieved chunks + metadata
      ↓ (rank order, budget floor)
Context builder
      ↓
Gateway.chat() with context     ← the Day 3/4 path
      ↓
Grounded answer + citations
```

## Exit Check — answer in your own words

- What actually grounds an answer in RAG — the retrieval, the generation, or both?
- Why chunk before embedding, and what does overlap buy you at a boundary?
- Where does the "context budget" come from, and what breaks if you exceed it?
- What does metadata add beyond plain text retrieval?
- How would you tell an answer that was *drawn from a chunk* apart from one the model *invented* — and which of those is RAG's fault?