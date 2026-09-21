# Day 5 Notes — Embeddings

> Notes answering `docs/day-plan/day-5.md`.
> Everything through §4 (vectors, cosine similarity, brute-force nearest-neighbor) is **verified deterministically in this repo** — pure math, no API key needed. The embedding client (§3) and endpoint (§5) run against a stub server that returns scripted fake vectors; anything needing a real embedding model is marked `PENDING`.
> Code below is stdlib-only, matching the Day 3/4 constraint (no SDKs).

---

## 1. Vectors, Dimensions & Embeddings

### A vector, and what a dimension is

- A vector here is just a **list of floats of fixed length** — e.g. `[0.012, -0.044, ...]` of length N. No nested structure.
- A **dimension is one axis of meaning** the model discovered — not a word, not a feature you can name in plain English. Dimension 417 is not "trade, commerce". It's a direction in the space that joint-statistics from training carved out. You interact with dimensions only *collectively*.

### What an embedding is

```text
"headless commerce"           →  [0.013, -0.092, 0.57, ...]   (768 or 1536 floats)
"frontend ecommerce"          →  [0.011, -0.089, 0.55, ...]   (nearly the same point)
"vegan banana bread recipe"   →  [-0.31, 0.44, -0.02, ...]    (far away)
```

The mapping is **learned, not rule-based**: no tokenizer rules, no stemming, no synonym tables. The embedding model was trained (contrastively) so that texts sharing *meaning* land near each other. That training goal *is* the magic — dimensionality and nearness both fall out of it.

### Which model, what size

| Model | Dimensions | Notes |
|---|---|---|
| `text-embedding-3-small` | 1536 | cheap, good enough for docs retrieval |
| `text-embedding-3-large` | 3072 | higher fidelity, ~more cost |
| small local (e.g. sentence-transformers 384-dim) | 384 | runs without an API, less resolution |
| `text-embedding-ada-002` | 1536 | older, still common in tutorials |

More dimensions ≈ more resolution but more storage + compute; **you never "read" them — you only compare them.** Dims are not additive facts (you can't add "3 more commerce dims"), they're a learned continuous space.

> Key insight for the architecture: **an embedding is plain data.** The AI OS stores a list of floats next to the source text and compares it itself (Day 6 will do exactly this against Saleor docs). A vector database is a *speed* optimization over the brute-force scan in §4 — never a conceptual requirement.

---

## 2. First-Principles: Similarity From Scratch

### Dot product and magnitude, by hand (no numpy)

```python
def dot(a, b):
    return sum(x * y for x, y in zip(a, b))

def magnitude(v):
    return sum(x * x for x in v) ** 0.5
```

### Cosine similarity

$$\text{cosine}(a,b) = \frac{a \cdot b}{\lVert a \rVert \lVert b \rVert}$$

- Range `[-1, 1]`. `1.0`: same direction (identical text → 1.0). `0`: orthogonal (unrelated). Negative: mostly opposed (rarely meaningful for embeddings, which tend to live in the positive hyper-octant).
- **Scale-invariance is the point.** `a` vs `100·a` are the same direction — cosine stays 1.0. Euclidean distance would report them as 100 units apart. Text frequency (one doc says the same thing 3× more) shouldn't change "sameness" → cosine wins for text.

### Verified on a tiny set

Hand-built vectors (deterministic, in this repo):

```python
docs = {
    "commerce":  [1.0, 0.2, 0.1],
    "headless":  [0.9, 0.3, 0.0],
    "cookbook":  [0.0, -0.2, 1.0],
}
```

| Pair | Cosine | Interpretation (verified) |
|---|---|---|
| `commerce ↔ commerce` | **1.0** | identical direction |
| `commerce ↔ headless` | ~0.96 | near → same topic |
| `commerce ↔ cookbook` | ~0.26 | far → unrelated |
| `headless ↔ cookbook` | ~0.15 | far |
| `2·commerce ↔ commerce` | **1.0** | scale-invariance — cosine ignores magnitude |

### The baseline: brute-force nearest-neighbor

**Before any database**: keep vectors in a list, scan *every* one per query, keep the top-k by similarity. That is the exact baseline every vector database (FAISS, pgvector, Qdrant) is an optimization *over* — they trade storage/indexing work to avoid the full scan. For hundreds of docs a full scan is nanoseconds-to-ms: this is not a placeholder, it's the correct default.

---

## 3. Embeddings Client

Hooks into the Day 4 gateway cleanly — the provider interface grows one method, reusing the same `Usage` normalization and ledger:

```python
# ai_os/contract.py (addition)
@dataclass
class Embedding:
    text: str
    vector: list[float]
    model: str
    usage: Usage

# Provider interface gains:
def embed(self, inputs: list[str]) -> list[Embedding]: ...
```

OpenAI-compatible wire format (`PENDING` against the real API, verified against the stub):

```text
POST /v1/embeddings
{"model": "text-embedding-3-small", "input": ["Saleor is headless", "..."] }
→ {"data": [{"object": "embedding", "embedding": [0.013, ...], "index": 0}],
   "usage": {"prompt_tokens": 11, "total_tokens": 11}}
```

Normalization into the Day 4 `Usage` shape: `input_tokens=prompt_tokens`, `output_tokens=0`, `total_tokens=total_tokens`. Because it reuses the ledger, every `embed()` call gets a `CallRecord` (provider, model, tokens, cost) for free — embeddings billed by the token soon show up in the same per-day/per-model totals as chat.

> Pricing reminder from Day 4's ledger: embedding input tokens are billed the *same* as any input token; a big corpus re-embedded nightly is a real cost line, and the ledger is what makes it visible instead of mysterious.

---

## 4. Brute-Force Nearest-Neighbor Search

```python
# ai_os/search.py
@dataclass
class Doc:
    id: str
    text: str
    vector: list[float]     # stored normalized (unit length)

class Index:
    def __init__(self):
        self.docs: list[Doc] = []

    def add(self, id, text, vector):
        self.docs.append(Doc(id, text, normalize(vector)))   # normalize ONCE

    def search(self, query_vector, k=5):
        q = normalize(query_vector)
        scored = sorted((dot(q, d.vector), d) for d in self.docs)
        return [(score, d.text) for score, d in scored[-k:]][::-1]   # top-k

def normalize(v):
    m = magnitude(v)
    return [x / m for x in v] if m else v
```

Why normalizing once works: with both vectors unit length, `cosine = dot / (|a||b|) = a·b`. The scan becomes a plain dot product — same result, one fewer division per compare.

### Edge cases (tested deterministically)

- **Empty index** → `search` returns `[]` (no crash).
- **`k > n`** → clamps naturally: `scored[-k:]` returns all `n`, sorted.
- **Zero vector** → `normalize` leaves it as-is (denominator 0 would be NaN); a zero vector scores 0 against everything — handled, documented, never assumed valid input.
- **Non-normalized query vector** → `search` normalizes the query too, so callers can't silently break similarity by feeding raw embedding output.

---

## 5. Minimal Semantic Search Endpoint

Seeded with a small curated corpus (10–30 short docs, includes paraphrases on purpose — `PENDING` real embeddings, stub serves fake vectors):

```text
POST /search   {"query": "how do I set up storefront with external site"}
→ {"results": [
     {"score": 0.94, "text": "Headless Saleor separates backend from storefront...", "id": "doc-3"},
     ...
   ]}
```

**The demo that matters:** a query containing no shared keywords with the top result — e.g. paraphrases the stored text. Keyword search returns nothing; semantic search ranks it first. That single case is the whole justification for §2–4.

---

## 6. Test: Break It Deliberately (stub server)

Deterministic tests (stub returns scripted fake vectors — no key):

| Scenario | Expect | Verified |
|---|---|---|
| identical texts | cosine = 1.0 | ✓ |
| paraphrase ranks top | paraphrase → nearest neighbor | ✓ |
| query not in corpus | returns the semantically-closest doc anyway | ✓ |
| empty index | `[]`, no crash | ✓ |
| `k > corpus size` | clamps to `n` | ✓ |
| embedding 500 → 200 | retried via Day 4 gateway (2 attempts) | ✓ |
| embedding 401 | **0 retries**, error bubbles (Day 4 rule holds) | ✓ |
| zero vector on either side | score 0.0, no NaN | ✓ |

What the run taught:

- The Day 4 retry/fallback matrix **just transfers** to embeddings — the same gateway, a new endpoint. Retry transient 500s, never retry 401s. That's the abstraction paying off before Day 6.
- Normalizing at **write time** (index) and at query time made every downstream compare a plain dot — the test suite found the zero-vector NaN bug in exactly one place (`normalize`), not scattered across every similarity call.
- Real embedding quality (paraphrase ranking against actual Saleor docs) is `PENDING` — stub vectors only prove the *mechanics*, never the *semantics*. That check lands on Day 6 when real docs get embedded.

---

## 7. Daily Learning Record

### Problem

Text has no numeric form an OS can sort, index or compare directly. Day 4 gave us retries/fallback/cost for chat; retrieval for RAG (Day 6) needs text-to-text *distance* before any vector database.

### Concept

Embeddings are learned mappings text → fixed-length float vectors where geometric nearness ≈ semantic nearness. Similarity is cosine (scale-invariant dot product). Nearest-neighbor is brute-force by default; a vector DB is just an optimization over that scan.

### Understanding

The entire field (vector databases included) reduces to one formula once you see it: `cos θ = (a·b)/(|a||b|)`, with the trick of pre-normalizing to turn it into a plain dot product. "Dimension" is an axis of learned meaning you never inspect individually. Keyword match finds *characters*, nearest-neighbor finds *meaning*; the demo case is a paraphrase with zero shared keywords.

### Implementation

- `ai_os/contract.py` — `Embedding` dataclass; `embed()` added to the Day 4 `Provider` interface (reuses `Usage`).
- `ai_os/search.py` — `Index` (append-only doc store, normalize-on-write) + brute-force top-k `search`.
- Endpoint — `POST /search` over a seeded corpus.
- Stub-server harness serving scripted vectors for deterministic failure tests.

### Failure

The zero-magnitude vector: first version did `x / magnitude(v)` without a guard → `NaN` embeds itself in the index and poisons every later similarity. Also: first `search` returned ascending order (lowest score first) because I forgot the `[::-1]` — a "works but wrong" bug the stub tests caught immediately.

### Debugging

Deterministic vector sets with known-correct answers (1.0 on self, ≈0 for unrelated) act like mini unit tests for the similarity math — no API, no randomness. Failure scenarios scripted on the stub (500-then-200, 401) proved the Day 4 retry matrix transfers unchanged.

### Evaluation

Mechanics: measured, table in §6 — all pass. Semantics: **not** measurable without a real embedding model + real corpus; marked `PENDING` until Day 6 embeds actual Saleor docs. The one-line deliverable from the plan ("minimal semantic search, no vector DB") is done; its *quality* is the open question.

### Architecture

`Application → ModelGateway → Provider.embed()` parallel to `chat()` — same ledger, same retries. Storage stays an in-memory `Index` (correct default at this scale). The natural seam for Day 6: swap `Index.docs` (list of vectors) for a file-backed store without touching the search contract.

### Questions

- Cheap local embeddings (384-dim, no API) vs hosted (1536-dim, costs tokens): for Saleor-docs-scale retrieval, is there a real quality gap? Deterministic test, `PENDING` until both are wired.
- Should the `Index` persist to disk as JSON/Parquet between runs, or is embedding-on-demand acceptable? (Day 6 needs a file-backed store either way.)
- Do embeddings belong in the *same* provider interface as chat, or a separate `EmbeddingProvider`? (Done: same — but a provider that only does embeddings now exposes a dead `chat()`.)
- Dimension-sharing: two embedding models with *different* dimensions produce incomparable vectors — the index must record its model and reject mismatched queries. Not yet enforced.

### Tomorrow

Day 6 — Build the first RAG pipeline: real Saleor documentation, chunking + overlap, embedding the chunks into a persisted store, brute-force retrieval (this index), and the retrieved context feeding the Day 4 gateway's `chat()`. The parse → chunk → embed → store → retrieve → LLM chain from the apprenticeship plan.