# ai-layer — Days 4–12: Gateway → Search → RAG → Code RAG → Tool Calling → Agent Loop

A reusable AI layer built in layers:

1. **Day 4 — The Model Gateway**: `Application → ModelGateway → Provider → LLM`.
   The gateway owns retries, exponential backoff + jitter, `Retry-After`,
   ordered fallback between providers, a per-call time budget and the cost
   ledger — while each provider only knows its own wire format.
2. **Day 5 — Vector Search**: brute-force `dot / cosine` nearest neighbour
   search over `(id, text, vector)` documents — no library, hand-rolled.
3. **Day 6 — First RAG Pipeline**: docs → parse → chunk → embed → store →
   retrieve → context → "answer" from the gateway, with citations and a
   refusal path when the documentation cannot cover the question.
4. **Days 7–9 — Debug + improve**: failure taxonomy and a similarity gate (7),
   hybrid lexical+semantic retrieval (8), rerank + MMR + context construction (9).
5. **Day 10 — Code RAG**: a second corpus (Saleor's *source*) behind the same
   seams — AST symbol extraction, interface-vs-body embedding, and identifier
   tokenization — answering **"where is it implemented?"** with `module#Symbol`
   citations and the identical fixture harness.
6. **Day 11 — Tool Calling**: the seams become **declarative** — `ToolRegistry`
   (schemas + hand-rolled validation + dispatch), the five tools
   (`search_saleor_docs`, `search_saleor_code`, `get_product/order/customer`),
   and `run_tool_answer` (one tool turn → one grounded answer). Function calling
   rides the Day-4 gateway as **one pass-through argument**; the refusal gate
   becomes a **return value**; and `cited ⊆ retrieved` moves home to where the
   evidence is produced.
7. **Day 12 — Agent Loop**: the single turn becomes a **loop** — reason → choose →
   execute → observe → reason again → answer — so a question several calls deep
   ("what happens after `CheckoutComplete` submits?") is answerable. Agent state is
   the **transcript** (no hidden variable), observations are the graded evidence
   record **per step**, and termination gains two exits beside the natural one
   (`max_steps`, budget) plus `gave_up`. `run_tool_answer` survives as
   `run_agent(max_steps=1)`, so Day 11's tests stay green by construction.

```
Application
     |
     v
AI Layer (ModelGateway)        retries, fallback, budget, ledger
     |
  +--+--+
  |     |
OpenAI Anthropic              chat (+ OpenAI embeddings), tools passed through
     |
Tool layer                    schemas → model proposes → validate → execute
     |
RAG loop / code RAG           docs → chunk / symbols → retrieve → evidence
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
| `ai_os/rag.py` | day 6: `RagStore` (persistent, dimension-locked), retrieval, `build_context` (budget floor), `run_rag`; day 10: channels optional code metadata + `code_store_path_from_env` |
| `ai_os/ingest_code.py` | day 10: `SymbolExtractor` (stdlib `ast`) + `ingest_code` — the code ingestion seam |
| `ai_os/code_questions.yaml` | day 10: the pre-registered code fixtures (CA1–CE1), asserting `module#Symbol` sets |
| `ai_os/server.py` | FastAPI: `/chat`, `/chat/stream`, `/rag` (`corpus="docs"｜"code"`), `/ledger`, `/health` |
| `ai_os/tools.py` | day 11: `ToolRegistry` (schemas/validation/dispatch), `ToolResult`, the five tools, the hand-rolled JSON-Schema subset |
| `ai_os/agent.py` | day 11/12: `run_tool_answer` (one tool turn → grounded answer) + `run_agent` — the loop: transcript-as-state, three exits + `gave_up`, per-step observations; `cited ⊆ retrieved` over the accumulated transcript; `REASONING_PROMPT` is the reasoning-turn system prompt |
| `tools/mock_gateway.py` | scripted dual-provider stub `:8700`/`:8701` (chat, embeddings, **and tool_calls** scenarios) |
| `tools/tool_smoke.py` | day 11: T1–T7 offline suite (selection, validation matrix, refusal, cap, dialect parity, no-regression) |
| `tools/agent_smoke.py` | day 12: A1–A8 offline suite (loop, three exits + `gave_up`, depth-1 = Day 11, parallel observations, grounding over the transcript, cost shape, T1–T7 re-run); `--live` measures real-model composition against the real gateway |
| `tools/gateway_smoke.py` | day 4 deliberate-failure suite + token economics |
| `tools/build_rag_store.py` | embed a `corpus/` of markdown → `data/store.json` |
| `tools/build_code_store.py` | day 10: embed a Saleor source tree → `data/code_store.json` (interface-only, resumable) |
| `tools/code_debug.py` | day 10: offline stub code corpus + identifier-hash gateway |
| `tools/retrieval_day10.py` | day 10: Doc-RAG vs Code-RAG runner (one harness, two datasets) |
| `tools/rag_smoke.py` | day 6 deliberate-failure suite for the RAG loop |
| `corpus/`, `data/store.json` | 6 real Saleor doc pages, committed store (250 chunks, stub vectors) |
| `test_search.py`, `test_chunk.py`, `test_rag.py`, `test_day10.py` | deterministic unit suites (no network) |

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

Code RAG — the same endpoint, the *second* corpus, cited `module#Symbol:line`:

```bash
curl -s localhost:8023/rag \
  -H 'content-type: application/json' \
  -d '{"question":"Where is checkout completed?","corpus":"code"}'
# → citations[{doc_id:"saleor/checkout/complete_checkout.py#complete_checkout",
#              symbol, kind, line, score}, ...]
```

To build the stores for real (needs a working embeddings key in `.env`). Pin
`LLM_PROVIDERS` so a store never mixes embedding models — the code store's build
is resumable and, on Cloudflare's free tier (10k neurons/day ≈ 16k symbols), the
full Saleor tree simply takes more than one day:

```bash
python tools/build_rag_store.py  --corpus corpus               --out data/store.json
LLM_PROVIDERS=cloudflare: python tools/build_code_store.py --corpus corpus_code/saleor \
    --out data/code_store.json --batch 100                    # resume to finish
```

## Run it locally — no API key, deterministic failures

```bash
python tools/gateway_smoke.py     # day 4: spawns the stub, all 13 scenarios
python tools/rag_smoke.py         # day 6: retrieves, budgets, retries, fallbacks
python test_chunk.py && python test_search.py && python test_rag.py
python test_day7.py && python test_day8.py && python test_day9.py && python test_day10.py
python tools/rag_debug.py --stub        # day 7 offline mechanics
python tools/retrieval_day9.py --stub   # day 9 evidence pipeline (ΣΔMRR +1.000)
python tools/retrieval_day10.py --stub  # day 10 doc-vs-code (code 6/6, doc 9/9)
python tools/tool_smoke.py              # day 11 T1–T7 (tool calls, validation, cap)
python tools/agent_smoke.py             # day 12 A1–A8 (loop, exits + gave_up, depth-1, grounding over transcript)
```

The stub serves both providers on `:8700` (OpenAI) and `:8701` (Anthropic)
and can be programmed to return 200 / 429 / 5xx / 401 / 400 / timeouts /
malformed JSON / mid-SSE cuts / empty-content 200s — and now hand-rolled
embeddings whose vectors are sums of a small theme vocabulary **and scripted
`tool_calls` scenarios** (`tc_code`, `tc_bad_args`, `tc_unknown_tool`,
`tc_two`, `tc_malformed`, …) so the tool layer is testable with zero keys:

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

Day 11 tool suite (`tools/tool_smoke.py`, T1–T7, offline):

| Fixture | Verified behavior |
|---|---|
| T1 tool selection | `search_saleor_code`; evidence = `complete_checkout` + `CheckoutComplete` (split-brain double-hit), all ⊆ store |
| T2 selection by corpus | `search_saleor_docs`; every result ⊆ the doc store |
| T3 data tool | `get_order(id="1")` valid; real resolver returns order 1001 / UNFULFILLED, **no** `pending_resolver` (Day 14 fills the stub) |
| T4 argument matrix | missing / wrong-enum / unknown-key / malformed-JSON each rejected **by name**, no crash |
| T5 unknown tool | `get_invoice` → rejection result (never an exception) |
| T6 refusal | out-of-corpus → `refused`, empty evidence; a restated refusal is flagged; a fabricated `module#Symbol` is caught |
| T7 single-turn cap | two `tool_calls` → one executed, `loop_deferred: day12` recorded |
| dialect parity | the same turn over Anthropic's `tool_use`/`tool_result` wire |
| regression | doc nine 9/9, code six 6/6 through the tools (same `retrieve()`) |

Day 12 agent suite (`tools/agent_smoke.py`, A1–A8 + exits, offline):

| Fixture | Verified behavior |
|---|---|
| A1 multi-step | 2 `search_saleor_code` steps; step 2's query names the symbol step 1's evidence carried; answer cites from ≥2 observations |
| A2 self-correction | step 1 rejected (enum violation named), step 2 fixed → 1 rejection + 1 success |
| A3 pivot after refusal | code tool refuses → step 2 legally switches to `search_saleor_docs`; no symbol fabricated |
| A4 parallel calls | two `tool_calls` one step → both executed, both observed |
| A5 termination | `max_steps` → `truncated`, answer names the limit; `budget_usd` → exit 3 |
| A6 grounding | `cited ⊆ ∪(step observations)`; a `ghost#Symbol` outside every observation is caught |
| A7 zero-tool | no `tool_calls` → `tool_steps: 0`, one turn |
| A8 parallel capacity | three calls one step → all validated/executed/observed, none dropped |
| gave_up | `self_correct=1`, repeat the bad call → bound reached, answer names the stuck point |
| cost | `chats == tool_steps + 1`, `embeds == tool_steps`, retrieval gateway chat calls `0` |
| depth-1 | `run_agent(max_steps=1)` *is* Day 11 — natural `answer` exit, `turns: 2`, no `max_steps` note |
| regression | `tools/tool_smoke.py` re-run unchanged (T1–T7) |

Both suites also run **live** — `python tools/tool_smoke.py --live` (Day 11 tool
*choice*) and `python tools/agent_smoke.py --live` (Day 12 multi-step *composition*).
Live swaps only the *chat* wire for the real gateway from `.env`; retrieval stays on
the deterministic stub stores. The harness contract (valid args, `cited ⊆ ∪
observations`, a real exit) is hard-checked; the semantic cells are measured per run.
With the reasoning prompt in place the free `nvidia/nemotron-3-super-120b-a12b:free`
model **follows evidence-carried symbols** (`complete_checkout` →
`create_order_from_checkout` → `process_payment`); a provider outage reports `[SKIP]`
and exits 2 rather than a false FAIL.

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
| `TOOL_GATE_TAU` | `0.55` | the fused-score gate behind the retrieval tools |
| `TOOL_TOP_K` | `4` | default `k` when the model does not name one |
| `TOOL_MAX_TURNS` | `1` | day 11's per-step call cap (day 12's `run_tool_answer`) |
| `AGENT_MAX_STEPS` | `6` | day 12: the loop's step cap (exit 2) |
| `AGENT_BUDGET_CHARS` | `4000` | day 12: per-observation clamp (day 11's `max_chars`, per step) |
| `AGENT_SELF_CORRECT` | `2` | day 12: re-issues allowed after a rejection, per question |
| `AGENT_BUDGET_USD` | unset (`0`) | day 12: run-level cost cap → exit 3 |
| `SALEOR_READONLY_DB_URL` | unset | day 14: the `saleor_read_only` DSN; unset → the offline seeded sqlite stub |
| `SALEOR_SQL_ROW_LIMIT` | `100` | day 14: rows per data query (truncation is reported) |
| `SALEOR_SQL_SCHEMA_BUDGET` | `4000` | day 14: chars of schema card (budget-clamped, cut marked) |

Omitted keys are a startup error in `build_gateway_from_env` (fail fast, not a
runtime 401 surprise). Embedding model is passed per-provider, so the key lives
with the provider config it belongs to.

## API

- `GET /health` → `{ok, providers[], max_attempts, timeout_s, ...}`
- `POST /chat` — `{messages, temperature, max_tokens}` → `{message, model, finish_reason, usage, cost_usd, latency_ms, provider, attempts}`
- `POST /chat/stream` — same body, SSE events; first chunk carries `ttft_ms`
- `POST /rag` — `{question, k?, budget_chars?, corpus?}` → answer, `refused`, retrieval/generation cost split, citations (`corpus="docs"` default, `"code"` for symbols)
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
- Day 10 adds the code corpus behind the *same* seams: `ai_os/ingest_code.py`
  (stdlib `ast` symbol extraction), `tools/build_code_store.py` (interface-only,
  resumable), `tools/retrieval_day10.py` (doc-vs-code). See
  `docs/concepts/code-rag.md` and `docs/evaluations/code-day10.md`.
- Day 11 adds the action layer: `ai_os/tools.py` (schemas + validation +
  dispatch), `ai_os/agent.py` (`run_tool_answer`), the providers' tool wire,
  and `gateway.chat_with_tools` (one pass-through argument). See
  `docs/concepts/tool-calling.md` and `docs/evaluations/tool-day11.md`. A
  tool-turn chat is a chat call, so it bills like one; tool execution is free
  arithmetic. The real model's *choice* of tool is Day 21's measure.
- Day 12 adds the loop: `run_agent` generalizes `run_tool_answer` (which is now
  `run_agent(max_steps=1)`) — transcript-as-state, three exits + `gave_up`,
  parallel observations, and `cited ⊆ retrieved` over the accumulated transcript.
  The loop asks once more after the last allowed tool step, so `max_steps=1` takes
  Day 11's *natural* answer exit (never a false "step limit" note); a completion
  that still asks for tools at the cap is recorded, not executed. `REASONING_PROMPT`
  precedes every reasoning turn (observations are evidence for the *next* step). See
  `docs/concepts/agent-loop.md` and `docs/evaluations/agent-day12.md`. The real
  model's *composition* of a multi-step investigation is Day 21's measure, now run
  per request via `python tools/agent_smoke.py --live` — the free model walks the
  map (`complete_checkout` → `create_order_from_checkout` → `process_payment`).
- Day 14 adds the data corpus: `ai_os/data.py` (read-only seam — `SqliteDataStore`
  seeded stub / lazy `PostgresDataStore`; the schema card + business glossary; the
  `SELECT`/`WITH`-only validator seed; the parameterized resolvers), plus
  `run_saleor_sql` / `describe_saleor_schema` in the registry and the data columns
  in the trace. The pipeline **is** the loop: the model generates SQL as a tool
  argument, so it stays visible (repairable, gradable — Day 15's target), and the
  final answer's numbers must be in the result set (`asserted ⊆ returned`). See
  `docs/concepts/text-to-sql.md`, `docs/evaluations/data-day14.md`, and
  `python tools/data_smoke.py` (Q1–Q8: the two read-only guards, the schema card,
  measured truncation, and the invariant proven both ways). A data answer costs
  **0 embeds + 2 chats** — exact set logic, no vectors.