# Day 5 — Embeddings

## Objective

Understand how text becomes a point in space. Build the piece that will power RAG (Day 6): embeddings, cosine similarity, and a first hand-built nearest-neighbor search before any vector database.

## Duties Outline

### 1. Learn: Vectors, Dimensions & Embeddings (45 min)

- [ ] Define a vector and what a "dimension" actually is (one axis of meaning).
- [ ] What an embedding is: text → fixed-length list of floats, no explicit rules — learned by a model.
- [ ] Why embeddings map *meaning* to *nearness*: semantically close texts → close points.
- [ ] What `text-embedding-3-small` (1536-dim) vs a 384-dim local model changes: fidelity vs size/cost.
- [ ] Embeddings are not a database feature — they're plain numbers you store and compare yourself.

### 2. First-Principles: Similarity From Scratch (45 min)

- [ ] Implement dot product and vector magnitude by hand (no numpy).
- [ ] Derive cosine similarity: `cos θ = (a·b) / (|a||b|)` — why it's scale-invariant.
- [ ] Euclidean vs cosine: when one "feels" better, and why cosine dominates in text.
- [ ] Verify on a tiny hand-made vector set: similarity must be 1.0 on itself, drop as vectors move apart.
- [ ] Before any database: **brute-force nearest-neighbor** — scan all vectors, keep the top-k. It's the baseline every vector DB is optimizing.

### 3. Implement: Embeddings Client (1-1.5 hours)

- [ ] Add `embed(inputs: list[str]) -> list[Embedding]` to the Day 4 `Provider` interface (or a new `EmbeddingProvider`).
- [ ] OpenAI-compatible wire format: `POST /v1/embeddings` → `{data: [{embedding: [...]}], usage: {...}}`.
- [ ] Normalize: `Embedding(text, vector, model, usage)` — reuse the Day 4 `Usage`/ledger.
- [ ] Pricing note: embeddings bill per token too; the Day 4 ledger should price them.

### 4. Build: Brute-Force Nearest-Neighbor Search (1.5 hours)

- [ ] `Index` = list of `(id, text, vector)` pairs, in memory, append-only.
- [ ] `search(query_vector, k)` → loop every vector, compute cosine, track top-k.
- [ ] Normalize vectors to unit length once (`a/|a|`), so cosine reduces to a plain dot product.
- [ ] Handle edge cases: empty index, `k > n`, zero-magnitude vector.

### 5. Build: Minimal Semantic Search Endpoint (1 hour)

- [ ] Seed the index with a small curated corpus (10-30 short documents).
- [ ] Expose `POST /search` → `{query, results: [{id, text, score}]}`.
- [ ] Show a result that keyword search would *miss* (synonyms, paraphrase) — that's the demo.

### 6. Test: Break It Deliberately (45-60 min)

With the stub server returning **scripted fake vectors** (no API key needed for the search half):

- [ ] Query not in the corpus (nearest neighbor retrieval, not exact match).
- [ ] Query that is a paraphrase of a stored doc — must rank it top.
- [ ] Empty query / empty index / `k` greater than corpus size.
- [ ] Embedding call fails (500 → retry via Day 4 gateway, 401 → no retry).
- [ ] Two identical texts → cosine = 1.0; two opposite-ish texts → near 0.
- [ ] Log each embedding call's tokens + cost through the ledger.

### 7. Documentation (20 min)

- [ ] Write `docs/concepts/embeddings.md` summary.
- [ ] Fill the Day 5 Daily Learning Record (Problem/Concept/Understanding/.../Tomorrow).
- [ ] Record today's token economics: tokens + cost of every embedding call.

## Deliverable

A minimal semantic search prototype — no vector database:

```text
Query
  ↓ (embed)
Vector
  ↓
Index (list of vectors, brute-force scan)
  ↓ (cosine top-k)
Ranked documents
```

## Exit Check — answer in your own words

- What is a dimension, physically, in an embedding vector?
- Why does cosine similarity survive vector scaling when Euclidean distance doesn't?
- How is "nearest-neighbor" different from "keyword match"?
- Where do embeddings come from, and why can't you write one by hand?
- What would change if you swapped the embedding model — would the index break?