# ai-layer — Day 4: The Model Gateway

A reusable model interface: `Application → ModelGateway → Provider → LLM`.
The gateway owns retries, exponential backoff + jitter, `Retry-After`,
ordered fallback between providers, a per-call time budget and the cost
ledger — while each provider only knows its own wire format.

```
Application
     |
     v
AI Layer (ModelGateway)        retries, fallback, budget, ledger
     |
  +--+--+
  |     |
OpenAI Anthropic              providers speak their own wire format
```

Same interface, two real providers, zero SDKs — raw HTTP via `urllib`.

## Files

| File | What it is |
|---|---|
| `ai_os/contract.py` | `Usage`, `Completion`, 5-class error taxonomy |
| `ai_os/providers/openai.py` | OpenAI wire format (`/v1/chat/completions`) |
| `ai_os/providers/anthropic.py` | Anthropic wire format (`/v1/messages`, `system` top-level) |
| `ai_os/gateway.py` | retries, backoff+jitter, `Retry-After`, budget, fallback chain |
| `ai_os/ledger.py` | token accounting + cost per model; one `CallRecord` per attempt |
| `ai_os/config.py` | `build_gateway_from_env()` — everything is env config |
| `ai_os/server.py` | FastAPI: `/chat`, `/chat/stream`, `/ledger`, `/health` |
| `tools/mock_gateway.py` | scripted dual-provider stub `:8700`/`:8701` for deterministic tests |
| `tools/gateway_smoke.py` | deliberate-failure suite (all §7 rows) + token economics |

## Run it for real

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
# fill .env: OPENAI_API_KEY / ANTHROPIC_API_KEY / LLM_PROVIDERS (see .env.example)
uvicorn ai_os.server:app --port 8023

curl -s localhost:8023/chat \
  -H 'content-type: application/json' \
  -d '{"messages":[{"role":"user","content":"What is Saleor?"}]}'
# → {message, model, finish_reason, usage, cost_usd, latency_ms, provider, attempts}
```

## Run it locally — no API key, deterministic failures

```bash
python tools/gateway_smoke.py     # spawns the stub itself, runs all 13 scenarios
```

The stub script serves both providers on `:8700` (OpenAI) and `:8701`
(Anthropic) and can be programmed to return 200 / 429 / 5xx / 401 / 400 /
timeouts / malformed JSON / mid-SSE cuts:

```bash
python tools/mock_gateway.py &    # both ports on 127.0.0.1:8700-8701
python tools/gateway_smoke.py
```

Smoke suite verifies (against the stub + the ledger, no real network):

| Stub script | Expected gateway behavior |
|---|---|
| `200` | 1 call, usage normalized, cost from price table |
| `429, 200` | honors `Retry-After`, succeeds on attempt 2 |
| `500, 500, 200` | exponential backoff, 3 attempts |
| `500 x5` (max 3) | stops at max_attempts, falls back to next provider |
| `401` | never retried — `InvalidKeyError` (0 retries, both providers) |
| `401` on primary | falls through to fallback provider |
| `400` | never retried — `BadRequestError` |
| `hang` (stub stalls) | retried until cap, then fallback |
| `malformed` (200 + garbage) | parse failure retried, then fallback |
| `cut` (mid-SSE drop) | partial text delivered, then `NetworkError` |
| total budget | `ProviderTimeout` naming the budget |

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

Omitted keys are a startup error in `build_gateway_from_env` (fail fast, not a
runtime 401 surprise).

## API

- `GET /health` → `{ok, providers[], max_attempts, timeout_s, ...}`
- `POST /chat` — `{messages, temperature, max_tokens}` → `{message, model, finish_reason, usage, cost_usd, latency_ms, provider, attempts}`
- `POST /chat/stream` — same body, SSE events; first chunk carries `ttft_ms`
- `GET /ledger` → aggregates: records, ok/failed, tokens, `cost_usd`, by_model, by_day

`usage` is the normalized `{input_tokens, output_tokens, total_tokens}` shape.
`/ledger` counts failed attempts too — retries are not free, and now visible.

## Notes

- Day 3 single-provider client still lives at `ai_os/llm_client.py` (regression-safe).
- Day 4 notes: `saleor-platform/docs/day-plan/day-4-notes.md`
- Concept doc: `saleor-platform/docs/concepts/model-gateway.md`
- Day 5 (next): embeddings, vectors, cosine similarity, hand-built nearest-neighbor search.