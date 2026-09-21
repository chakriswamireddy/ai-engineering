# Concept: Model Gateway

> Day 4 of the AI-layer apprenticeship. The reusable model interface that
> stops the AI OS from being coupled to one provider.

## Problem

The AI OS was coupled to one raw `chat()` call to one provider (`ai_os/llm_client.py`,
Day 3). Retries, fallback and cost didn't exist as concepts — they were
"we'll handle it later." If we wanted to add a second provider, or survive a
rate limit without dropping the user's request, nothing was in place.

## The two axes

- A **model** is a named variant of a family: `gpt-4o-mini`, `claude-3-5-haiku-latest`.
  It defines quality, cost, latency. It is just a string.
- A **provider** is the API you call: OpenAI, Anthropic, or any
  OpenAI-compatible server (vLLM, Ollama, a local llama.cpp).

The abstraction belongs at the **provider** level. The thing that changes your
wire format, auth header and error handling is the provider; the model is a
config string the provider puts in a request field. Two models on OpenAI is a
one-line config change even without a gateway.

> Rule: **API-compatibility reduces provider count, not model count.**
> The same wire format ≠ the same model underneath.

## What a provider actually differs by

| Concern | OpenAI | Anthropic |
|---|---|---|
| Auth | `Authorization: Bearer <key>` | `x-api-key` + `anthropic-version` |
| System prompt | inside `messages` | top-level `system` parameter |
| `max_tokens` | optional, has a default | **required**, no default |
| Usage fields | `prompt_tokens` / `completion_tokens` | `input_tokens` / `output_tokens` |
| Error body | JSON always | JSON usually, non-JSON sometimes |
| Rate limit | HTTP 429, may carry `Retry-After` | same |

Everything above a provider sees none of this. Providers translate their wire
formats into the shared contract, or the contract providers translate *into*
it — one direction, the interface is the only vocabulary.

## The contract (`ai_os/contract.py`)

```python
class Usage:      input_tokens, output_tokens, total_tokens   # provider gives 2, compute the 3rd
class Completion: text, model, finish_reason, usage, raw

class ProviderError(Exception):        # .retryable decides retry policy
    RateLimitError    (retryable=True)  # add .retry_after
    ProviderTimeout   (retryable=True)
    NetworkError      (retryable=True)
    InvalidKeyError   (retryable=False)
    BadRequestError   (retryable=False)
```

Interface:

```python
class Provider:
    def chat(self, messages, *, temperature=0.0, max_tokens=512) -> Completion
    def stream(self, messages, *, temperature=0.0, max_tokens=512) -> Iterator[dict]
```

- **Interface** owns: method names, argument names, the dataclasses, the
  error taxonomy.
- **Implementation** owns: URLs, headers, request JSON shape, usage-field
  names, error-body parsing, `Retry-After` naming.

Adding a third provider = one class + one env var name. No application code
changes.

## The gateway (`ai_os/gateway.py`)

Holds an **ordered provider list** (primary → fallback). For each call it walks
retries inside a provider, then the fallback chain.

- **Retry** = same provider, same request. Only *transient* errors: `429`,
  `5xx`, timeouts, network blips. Exponential backoff `base × 2^(n-1)` plus
  jitter to avoid thundering-herd lockstep. When the server sends
  `Retry-After`, that wins over our formula — the server knows its own
  capacity.
- **Never retried** = semantic errors (`401`, `400`, `404`, `422`). A retry
  would reproduce the identical failure.
- **Fallback** = a different provider when the primary is exhausted *or* hit
  a non-retryable error. Switches price/quality tier; order matters.
- **Total budget** (`LLM_TOTAL_BUDGET_S`) caps the whole chain's wall-clock
  time — the slow case is no longer "timeout × attempts × providers".

The rule in one line: **retry only errors an identical retry can plausibly
live through.**

## The ledger (`ai_os/ledger.py`)

- Price table per model, USD per 1M tokens `(input, output)`. Unknown model
  prices `$0` by design (a conscious tradeoff over a crash).
- One `CallRecord` per **attempt** — success *and* failure — so retry cost is
  visible, not mysterious. Per-day and per-model aggregates feed Day 23
  observability.
- The provider owns *naming*; the ledger owns *money*. It never sees a
  provider field name.

## Configuration (`ai_os/config.py`)

"Which model, in what order" is runtime configuration, not code:

```bash
LLM_PROVIDERS=openai:gpt-4o-mini,anthropic:claude-3-5-haiku-latest
OPENAI_API_KEY=...        ANTHROPIC_API_KEY=...
OPENAI_BASE_URL=...       ANTHROPIC_BASE_URL=...
LLM_MAX_ATTEMPTS=3        LLM_TIMEOUT_S=60
LLM_BASE_DELAY_S=1.0      LLM_MAX_DELAY_S=30.0
LLM_TOTAL_BUDGET_S=0      # seconds for whole chain; 0 = unlimited
```

Keys come from env only. Omitted keys are a startup error (fail fast, not a
runtime 401).

## How we proved it (§7 deterministic testing)

No real API key and no real network: `tools/mock_gateway.py` serves both
provider wire formats on local ports and replays a *script* of outcomes per
request. `tools/gateway_smoke.py` resets scripts, runs the gateway, and asserts
on the ledger + stub hit counts.

Verified, all green:

- `200` → 1 call, usage normalized `12/24/36`, cost = price table.
- `429, 200` → honored `Retry-After`, succeeded on attempt 2.
- `500, 500, 200` → 3 attempts, backoff sleeps between them.
- `500 × 5`, max 3 → capped at 3 hits, fell back to Anthropic.
- `401` → 0 retries (`InvalidKeyError`); on the primary it falls through to
  the fallback provider.
- `400` → 0 retries (`BadRequestError`).
- `hang` → timed out 3×, then fell back.
- `malformed` (200 + garbage) → parse `NetworkError`, retried, then fallback.
- `cut` (mid-SSE drop) → partial text delivered, then `NetworkError`.
- `total budget` → `ProviderTimeout` naming the budget.

Cost example from a real run: one failed `500` chain costs the failed attempts
`$0`, the winning `claude-3-5-haiku-latest` call cost `$0.000088` — and the
ledger shows it.

## When a gateway is worth building

A gateway is ceremony when there's one provider. It earns its keep the moment:

1. the same call may need a different provider (fallback on outage, cheapest first);
2. rate limits and 5xx handling must be centralized (retry policy is a policy, not a `while` loop);
3. cost must be itemized per call and per day (the ledger).

## Open questions (carried to Day 20+)

- Per-provider circuit breaker: after N consecutive failures, stop calling a
  provider until a cooldown window passes.
- Is a 200-with-garbage *parse* error transient (retry) or a bug hiding retries
  (don't)? Current code treats it as `NetworkError` (retryable), but it could
  argue to be a `BadRequestError`.
- Should `finish_reason == "length"` be surfaced automatically (silent
  truncation at `max_tokens`)?

## Where this lives

- Implementation: `ai-layer/ai_os/*` (stdlib only — no SDKs).
- Daily notes: `saleor-platform/docs/day-plan/day-4-notes.md`
- Next: Day 5 — embeddings, vectors, cosine similarity, hand-built nearest-neighbor search.