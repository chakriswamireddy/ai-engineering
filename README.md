# ai-layer — Days 4–6: Gateway → Search → RAG

A reusable AI layer built in three layers:

1. **Day 4 — The Model Gateway**: `Application → ModelGateway → Provider → LLM`.
   The gateway owns retries, exponential backoff + jitter, `Retry-After`,
   ordered fallback between providers, a per-call time budget and the cost
   ledger — while each provider only knows its own wire format.
2. **Day 5 — Vector Search**: brute-force `dot / cosine` nearest neighbour
   search over `(id, text, vector)` documents — no library, hand-rolled.
3. **Day 6 — First RAG Pipeline**: docs → parse → chunk → embed → store →
   retrieve → context → "answer" from the gateway, with citations and a
   refusal path when the documentation cannot cover the question.

```
Application
     |
     v
AI Layer (ModelGateway)        retries, fallback, budget, ledger
     |
  +--+--+
  |     |
OpenAI Anthropic              chat (+ OpenAI embeddings)
     |
RAG loop                      docs → chunk → embed → store → retrieve → prompt
```

Same interface across chat and embeddings, two real providers, zero SDKs —
raw HTTP via `urllib`.

## Files

| File | What it is |
|---|---|
| `ai_os/contract.py` | `Usage`, `Completion`, `Embedding`, 5-class error taxonomy |
| `ai_os/providers/openai.py` | OpenAI wire format (`/v1/chat/completions`, `/v1/embeddings`) |
| `ai_os/providers/anthropic.py` | Anthropic wire format (`/v1/messages`, `system` top-level); no embeddings |
| `ai_os/gateway.py` | chat **and** `embed()` — retries, backoff+jitter, `Retry-After`, budget, fallback chain, ledger |
| `ai_os/ledger.py` | token accounting + cost per model; one `CallRecord` per attempt |
| `ai_os/config.py` | `build_gateway_from_env()` — everything is env config |
| `ai_os/search.py` | day 5: `Index`, document scoring, `dot`/`normalize` |
| `ai_os/chunk.py` | day 6: `chunk_text()` with overlap + tail grace + guards |
| `ai_os/ingest.py` | day 6: fetch/parse Saleor `llms.txt` docs → `Document`s |
| `ai_os/rag.py` | day 6: `RagStore` (persistent, dimension-locked), retrieval, `build_context` (budget floor), `run_rag` |
| `ai_os/server.py` | FastAPI: `/chat`, `/chat/stream`, `/rag`, `/ledger`, `/health` |
| `tools/mock_gateway.py` | scripted dual-provider stub `:8700`/`:8701` (chat **and** embeddings) |
| `tools/gateway_smoke.py` | day 4 deliberate-failure suite + token economics |
| `tools/build_rag_store.py` | embed a `corpus/` of markdown → `data/store.json` |
| `tools/rag_smoke.py` | day 6 deliberate-failure suite for the RAG loop |
| `corpus/`, `data/store.json` | 6 real Saleor doc pages, committed store (250 chunks, stub vectors) |
| `test_search.py`, `test_chunk.py`, `test_rag.py` | deterministic unit suites (no network) |

## Run it for real

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
# fill .env: OPENAI_API_KEY / ANTHROPIC_API_KEY / LLM_PROVIDERS (see .env.example)
uvicorn ai_os.server:app --port 8023
```

Chat — replies via the gateway:

```bash
curl -s localhost:8023/chat \
  -H 'content-type: application/json' \
  -d '{"messages":[{"role":"user","content":"What is Saleor?"}]}'
# → {message, model, finish_reason, usage, cost_usd, latency_ms, provider, attempts}
```

RAG — grounded answer over `data/store.json`:

```bash
curl -s localhost:8023/rag \
  -H 'content-type: application/json' \
  -d '{"question":"How do I create a checkout?"}'
# → {answer, refused, retrieval{k, candidates, model, cost_usd},
#    generation{...}, citations[{doc_id, heading, position, chunk_id, score}], cost_usd}
```

To build the store for real (needs a working embeddings key in `.env`):

```bash
python tools/build_rag_store.py --corpus corpus --out data/store.json
```

## Run it locally — no API key, deterministic failures

```bash
python tools/gateway_smoke.py     # day 4: spawns the stub, all 13 scenarios
python tools/rag_smoke.py         # day 6: retrieves, budgets, retries, fallbacks
python test_chunk.py && python test_search.py && python test_rag.py
```

The stub serves both providers on `:8700` (OpenAI) and `:8701` (Anthropic)
and can be programmed to return 200 / 429 / 5xx / 401 / 400 / timeouts /
malformed JSON / mid-SSE cuts — and now hand-rolled embeddings whose vectors
are sums of a small theme vocabulary:

```bash
python tools/mock_gateway.py &    # both ports on 127.0.0.1:8700-8701
python tools/rag_smoke.py
```

Day 6 smoke suite verifies (all mechanics, no real network):

| Scenario | Verified behavior |
|---|---|
| answer in docs | grounded answer, citations = retrieved chunks, separate retrieval cost |
| question not in corpus | nearest neighbours returned anyway (semantic refusal is Day 7) |
| answer spanning two chunks | overlap keeps the seam fact in both neighbours; K>1 retrieves both |
| empty corpus | honest refusal, no chat call at all |
| empty query | rejected up front |
| K beyond corpus / budget below one chunk | clamped / empty-safe refusal |
| embed 500 → 200 | retried through the gateway (Day 4 rule applied to embeddings) |
| embed 401 | never retried — `InvalidKeyError` |
| chat 500 via `/rag` | falls back to the anthropic provider |
| citation trace | cited ⊆ retrieved, with doc_id + score |
| ledger | retrieval billed under the embed model ≠ generation bills |
| identical texts | identical vectors; self-similarity 1.0, cross-theme lower |
| lost-in-the-middle | all 8 candidates reach the prompt in retrieval order |

## Config (via env, never committed)

| Var | Default | Meaning |
|---|---|---|
| `LLM_PROVIDERS` | `openai:gpt-4o-mini,anthropic:claude-3-5-haiku-latest` | ordered fallback chain `provider:model` |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | — | provider keys |
| `OPENAI_BASE_URL` / `ANTHROPIC_BASE_URL` | official APIs | point at vLLM/Ollama/stub |
| `LLM_MAX_ATTEMPTS` | `3` | retries per provider per call |
| `LLM_TIMEOUT_S` | `60` | per-request socket timeout |
| `LLM_BASE_DELAY_S` | `1.0` | first backoff (grows `×2` + jitter) |
| `LLM_MAX_DELAY_S` | `30.0` | backoff cap (`Retry-After` wins when present) |
| `LLM_TOTAL_BUDGET_S` | `0` | wall-clock budget for the whole chain; `0` = unlimited |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | OpenAI embeddings model (token-priced in the ledger) |
| `RAG_STORE_PATH` | `data/store.json` | where `POST /rag` loads the store from |

Omitted keys are a startup error in `build_gateway_from_env` (fail fast, not a
runtime 401 surprise). Embedding model is passed per-provider, so the key lives
with the provider config it belongs to.

## API

- `GET /health` → `{ok, providers[], max_attempts, timeout_s, ...}`
- `POST /chat` — `{messages, temperature, max_tokens}` → `{message, model, finish_reason, usage, cost_usd, latency_ms, provider, attempts}`
- `POST /chat/stream` — same body, SSE events; first chunk carries `ttft_ms`
- `POST /rag` — `{question, k?, budget_chars?}` → answer, `refused`, retrieval/generation cost split, citations
- `GET /ledger` → aggregates: records, ok/failed, tokens, `cost_usd`, by_model, by_day

`usage` is the normalized `{input_tokens, output_tokens, total_tokens}` shape.
`/ledger` counts failed attempts too — retries are not free, and now visible.

## Notes

- Day 3 single-provider client still lives at `ai_os/llm_client.py` (regression-safe).
- Day 5 hand-rolled search lives at `ai_os/search.py` — the RAG loop uses it.
- Day 5/Day 6 notes: `saleor-platform/docs/day-plan/day-5-notes.md`, `day-6.md`, `day-6-notes.md`
- Concept docs: `saleor-platform/docs/concepts/model-gateway.md` (+ rag / vector-search concepts).
- The committed `data/store.json` was built with **stub** vectors (dim 8) so tests
  are deterministic; rebuild it with a real key before trusting answers.
- Day 7 (next): semantic end-to-end evals with a real embedder + model, and the
  "not covered" honesty guarantee.