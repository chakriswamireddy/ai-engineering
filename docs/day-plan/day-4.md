# Day 4 — Model Gateway

## Objective

Stop coupling the AI OS to one model provider. Turn the Day 3 raw `chat()` into a reusable model interface where "Model A vs Model B" is configuration, not code.

## Duties Outline

### 1. Learn: Model vs Provider Abstraction (30 min)

- [ ] Define the two axes: a **model** (a named variant, e.g. `gpt-4o-mini`) vs a **provider** (the API you call, e.g. OpenAI, Anthropic).
- [ ] Why the abstraction lives at the *provider* level, not the *model* level.
- [ ] What actually differs between providers: endpoint, auth header, request shape (where `system` goes), token field names, error conventions, structured-output mechanism.
- [ ] Note which providers are OpenAI-compatible (vLLM, Ollama, local servers) — those need no new provider, just a base URL.

### 2. Design: The Interface Contract (45 min)

- [ ] Define `Usage` and `Completion` dataclasses — normalized across providers.
- [ ] Define the `Provider` interface: `chat()`, `stream()`.
- [ ] Define the error taxonomy: `RateLimitError`, `ProviderTimeout`, `NetworkError`, `InvalidKeyError`, `BadRequestError` — each tagged retryable or not.
- [ ] Decide what lives in the interface (contract) vs the implementation (wire format).

### 3. Implement: Two Providers (2-3 hours)

- [ ] Generalize the Day 3 OpenAI client into an `OpenAIProvider`.
- [ ] Add a second, genuinely different provider (`AnthropicProvider`) — same interface, different wire format.
- [ ] Normalize `usage` into `(input_tokens, output_tokens, total_tokens)`.
- [ ] Keep it stdlib-only (or `httpx`); no SDKs.

### 4. Build: Gateway Core — Timeouts, Retries, Backoff (1.5 hours)

- [ ] Timeout per request + a total budget for the call.
- [ ] Retry policy: which errors retry, which must not.
- [ ] Exponential backoff + jitter; honor `Retry-After` when present.
- [ ] Cap attempts per provider.
- [ ] Fallback chain: ordered list primary → fallback providers.

### 5. Build: Token Accounting & Cost (45 min)

- [ ] Normalize usage across providers (§3).
- [ ] Price table per model (input/output per 1M tokens).
- [ ] `cost = in/1e6 × in_price + out/1e6 × out_price`.
- [ ] Ledger: one `CallRecord` per call (provider, model, tokens, latency, cost, ok/error); per-day and per-model totals.

### 6. Configuration (30 min)

- [ ] All knobs via env: API keys, base URLs, model names, primary/fallback order, max attempts, timeout, base delay.
- [ ] No keys in code; prices live only in the ledger's config table.

### 7. Test: Break It Deliberately (45-60 min)

Use a **local stub server** (deterministic, no API key needed). Point providers at it and script responses:

- [ ] 200 ok, 429 then 200, 500 × 2 then 200, 401, 400, timeout (pause the stub), malformed JSON, mid-stream SSE cut.
- [ ] Verify: which errors got retried, delays between attempts, which error bubbled to the app, fallback swap.
- [ ] Log each failure: error type, status, attempts, elapsed.

### 8. Documentation (20 min)

- [ ] Write `docs/concepts/model-gateway.md` summary.
- [ ] Fill the Day 4 Daily Learning Record (Problem/Concept/Understanding/.../Tomorrow).
- [ ] Record today's token economics: tokens + cost for all calls, including the failures.

## Deliverable

A reusable model interface:

```text
Application
     |
     v
AI Gateway
     |
 +---+---+
 |       |
Model A Model B
```

## Exit Check — answer in your own words

- What belongs in the provider interface, and what stays in the implementation?
- Which errors must be retried, and which must never be?
- How is fallback different from retry?
- Where exactly is cost computed — and what does each number depend on?
- What would break if you added a third provider tomorrow?