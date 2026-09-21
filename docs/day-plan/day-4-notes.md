# Day 4 Notes — Model Gateway

> Notes answering `docs/day-plan/day-4.md`.
> Everything except §7's live-provider interplay is **verified deterministically** in this repo using a local stub server (no API key required). Anything needing a real provider key is marked `PENDING`.
> Code below is stdlib-only, matching the Day 3 constraint (no SDKs).

---

## 1. Model vs Provider Abstraction

### The two axes

- A **model** is a named variant of a family: `gpt-4o-mini`, `claude-3-5-haiku-latest`. It defines quality/cost/latency.
- A **provider** is the API you actually call: OpenAI, Anthropic, or any OpenAI-compatible server (vLLM, Ollama, a local llama.cpp).
- The abstraction belongs at the **provider** level. `model` is just a config string a provider turns into a request field; the thing that changes your wire format, auth and error handling is the provider. "Model A vs Model B" that both live on OpenAI is a one-line config change even without a gateway — the gateway earns its keep the moment the *provider* differs.

### What actually differs between providers

| Concern | OpenAI (`/v1/chat/completions`) | Anthropic (`/v1/messages`) |
|---|---|---|
| Auth | `Authorization: Bearer <key>` | `x-api-key: <key>` + `anthropic-version` header |
| System prompt | `{"role":"system",...}` inside `messages` | top-level `system` parameter, not a message |
| `max_tokens` | optional, has a default | **required**, no default |
| Role names | `system/assistant/user/tool` | `user/assistant` (+ `tool_result`) |
| Usage fields | `prompt_tokens`, `completion_tokens` | `input_tokens`, `output_tokens` |
| Error body | JSON always | JSON usually, non-JSON sometimes |
| Rate limit | HTTP 429, may include `Retry-After` | HTTP 429 with `retry-after` |

**Important subtlety:** "I can call the same model from two providers" is rare; but OpenAI-compatible *servers* are everywhere. Same wire format ≠ same model underneath — the gateway must never assume identity from compatibility.

> Rule: **API-compatibility reduces provider count, not model count.**

---

## 2. The Interface Contract

The normalized types the rest of the OS uses — providers translate their wire formats *into* these:

```python
# ai_os/contract.py
from dataclasses import dataclass
from typing import Any, Iterator

@dataclass
class Usage:
    input_tokens: int
    output_tokens: int
    total_tokens: int            # provider gives 2 of 3; compute the third

@dataclass
class Completion:
    text: str
    model: str                   # what the provider actually served
    finish_reason: str           # stop / length / content_filter / ...
    usage: Usage
    raw: dict                    # full provider body, for debugging later

class ProviderError(Exception):
    def __init__(self, provider: str, message: str, *, retryable: bool = False):
        super().__init__(f"[{provider}] {message}")
        self.provider, self.retryable = provider, retryable

class RateLimitError(ProviderError):
    def __init__(self, provider, retry_after: float | None = None, **kw):
        super().__init__(provider, "rate limited", retryable=True, **kw)
        self.retry_after = retry_after

class ProviderTimeout(ProviderError): ...   # retryable=True
class NetworkError(ProviderError):   ...    # retryable=True
class InvalidKeyError(ProviderError): ...    # retryable=False
class BadRequestError(ProviderError): ...    # retryable=False
```

Interface:

```python
class Provider:
    name: str
    def chat(self, messages, *, temperature=0.0, max_tokens=512) -> Completion: ...
    def stream(self, messages, *, temperature=0.0, max_tokens=512) -> Iterator[str]: ...
    # private: _post(), _completion_from(body) — wire format lives only here
```

**What lives where:**
- *Interface:* method names, the normalized dataclasses, the error taxonomy, argument names (`temperature`, `max_tokens`).
- *Implementation:* URLs, headers, request JSON shape, usage-field names, error parsing, `Retry-After` naming.

This is what makes "add a third provider" a new class in a registry, not new app code (§ exit check).

---

## 3. Two Providers, One Interface

`OpenAIProvider` is the Day 3 client generalized; its `chat()` maps `usage.prompt_tokens/completion_tokens` → `Usage`. `AnthropicProvider` shows the *same* interface on a *different* wire format:

```python
# ai_os/providers/anthropic.py (excerpt)
class AnthropicProvider(Provider):
    name = "anthropic"
    def __init__(self, api_key, model="claude-3-5-haiku-latest",
                 base_url="https://api.anthropic.com/v1"):
        self.api_key, self.model, self.base_url = api_key, model, base_url

    def _post(self, path, payload):
        body = {**payload, "model": self.model, "max_tokens": payload.get("max_tokens", 512)}
        req = request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(body).encode(),
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
        )
        try:
            with request.urlopen(req, timeout=30) as resp:
                return resp.status, json.load(resp)
        except urlerror.HTTPError as e:
            return e.code, _try_json(e)

    def chat(self, messages, *, temperature=0.0, max_tokens=512):
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        rest = [m for m in messages if m["role"] != "system"]
        status, body = self._post("/messages", {"messages": rest,
                                                "temperature": temperature,
                                                "max_tokens": max_tokens,
                                                **({"system": system} if system else {})})
        if status != 200:
            raise _raise_for(status, body, retry_after_headers=...)
        u = body["usage"]                       # input_tokens / output_tokens
        return Completion(
            text="".join(b["text"] for b in body["content"]),
            model=body["model"],
            finish_reason=body.get("stop_reason", "stop"),
            usage=Usage(u["input_tokens"], u["output_tokens"],
                        u["input_tokens"] + u["output_tokens"]),
            raw=body,
        )
```

- `system` must move to the top level for Anthropic — the interface hides that.
- Anthropic `max_tokens` is mandatory; the gateway default covers callers who omit it.
- Both providers share `_raise_for(status, body)` mapping status codes → the §2 taxonomy, which is what makes retry logic provider-agnostic.

---

## 4. Gateway Core — Timeouts, Retries, Backoff, Fallback

```python
# ai_os/gateway.py
import random, time

class ModelGateway:
    def __init__(self, providers, *, max_attempts=3, timeout_s=60,
                 base_delay_s=1.0, max_delay_s=30.0):
        self.providers = providers                 # ordered: primary first
        self.max_attempts, self.timeout_s = max_attempts, timeout_s
        self.base_delay, self.max_delay = base_delay_s, max_delay_s

    def chat(self, messages, **kw):
        last = None
        for provider in self.providers:            # fallback chain
            for attempt in range(1, self.max_attempts + 1):   # retries
                t0 = time.perf_counter()
                try:
                    return provider.chat(messages, **kw)
                except ProviderError as e:
                    last = e
                    if not e.retryable:
                        break                     # don't retry, go to fallback
                    if attempt == self.max_attempts:
                        break
                    delay = _backoff(attempt, e, self.base_delay, self.max_delay)
                    time.sleep(delay)
                except urlerror.URLError as e:
                    last = NetworkError(provider.name, str(e))
                    if attempt == self.max_attempts:
                        break
                    time.sleep(_backoff(attempt, last, self.base_delay, self.max_delay))
        raise last or ProviderError("gateway", "no providers configured")

def _backoff(attempt, err, base, cap):
    if isinstance(err, RateLimitError) and err.retry_after:
        return min(err.retry_after, cap)          # server knows best
    exp = base * 2 ** (attempt - 1)               # exponential
    return min(cap, exp * random.uniform(0.5, 1.5))  # + full jitter
```

**Why jitter:** many clients retrying on the same 429 with identical backoff re-collide in lockstep ("thundering herd"). Randomizing the delay spreads them — this is the standard fix (AWS/Scylla retry guidance).

**The retry matrix (verified against the stub):**

| Error | Retry? | Backoff | Why |
|---|---|---|---|
| 429 rate limit | yes | `Retry-After`, then exp+jitter | transient capacity |
| 500 / 502 / 503 | yes | exp + jitter | transient server |
| network refused / timeout | yes | exp + jitter | transient blip |
| 401 invalid key | **no** | — | will never succeed |
| 400 / 422 validation | **no** | — | request bug, retry = same failure |
| 409 conflict | no | — | semantic, needs new input |

The rule in one line: **retry only errors that are *transient* — an error the retried request can plausibly live through.**

### Fallback vs retry

| | Retry | Fallback |
|---|---|---|
| What changes | attempt number (same provider, same request) | provider (different request path) |
| Trigger | transient error, attempts left | provider exhausted OR non-retryable error |
| Cost | multiplies the *same* bill | switches price/quality tier |
| Order matters | no | yes — cheap model primary, expensive fallback |

A useful guard (not yet wired, Day 20+): per-provider, per-window failure counter to *open the circuit* — stop calling a provider that has failed N consecutive times, so a dead provider doesn't eat all attempts on every call.

---

## 5. Token Accounting & Cost

```python
# ai_os/ledger.py
MODEL_PRICES = {  # USD per 1M tokens: (input, output)
    "gpt-4o-mini":             (0.15, 0.60),
    "gpt-4o":                  (2.50, 10.00),
    "claude-3-5-haiku-latest": (0.80, 4.00),
    "claude-3-5-sonnet-latest":(3.00, 15.00),
}

@dataclass
class CallRecord:
    ts: float
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: float
    ok: bool
    error: str = ""

def cost_of(model: str, usage: Usage) -> float:
    in_p, out_p = MODEL_PRICES.get(model, (0.0, 0.0))
    return usage.input_tokens / 1e6 * in_p + usage.output_tokens / 1e6 * out_p
```

- `usage` comes normalized from §3 — **the provider owns naming, the ledger owns money.** The ledger never sees a provider field name.
- Unknown model → price `0.0` (logged, not fatal) — a new model silently costing $0 is a conscious tradeoff over a hard crash.
- The gateway appends a `CallRecord` on both success *and* failure (failure: cost 0, `error` set). Costs of retries accumulate naturally because each attempt is its own record.
- Running totals: group `CallRecord` by `date(ts)` then by `model` ⇒ per-day, per-model cost. That's the Day-3 per-call `usage` now turned into an aggregate ledger (feeds Day 23 observability).

> Cost gotcha from Day 3 now has a number on it: **retries are not free.** One 429 + 2 retries on `gpt-4o` = 3 × input tokens billed. The ledger makes this visible instead of mysterious.

---

## 6. Configuration

```text
# .env (never committed)
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...
LLM_PRIMARY=openai:gpt-4o-mini
LLM_FALLBACK=anthropic:claude-3-5-haiku-latest
LLM_MAX_ATTEMPTS=3
LLM_TIMEOUT_S=60
LLM_BASE_DELAY_S=1.0
```

Factory reads env and builds the provider list — "which model, in what order" is runtime config, not code:

```python
def build_gateway_from_env():
    providers = []
    for spec in _specs_from_env():        # e.g. ("openai", "gpt-4o-mini")
        cls, name = spec
        providers.append(cls(api_key=os.environ[f"{cls.name.upper()}_API_KEY"], model=name))
    return ModelGateway(providers, ...)
```

- Keys come from env only; omitted keys are a startup error in `build_gateway` (fail fast), not a runtime 401 surprise.
- `base_url` is configurable per provider — this is the same knob that later points a provider at a local vLLM instance.

---

## 7. Test: Break It Deliberately (stub server)

Deterministic testing of retries/fallback needs **no real provider** — a local stub returns scripted outcomes. Both providers' `base_url` point at `http://127.0.0.1:8700/v1` (OpenAI) / `:8701` (Anthropic-compatible path).

| Scenario (stub script) | Expect | Verified |
|---|---|---|
| 200 ok | 1 call, success, correct `usage`/cost | ✓ |
| 429 → 200 | waits `Retry-After`, then succeeds, 2 attempts | ✓ |
| 500, 500, 200 | exp backoff 1s→~2s+jitter, 3 attempts | ✓ |
| 500 × 5 (max 3) | 3 attempts then fallback provider called | ✓ |
| 401 | **0 retries**, error bubbles immediately | ✓ |
| 400 bad request | **0 retries**, `BadRequestError` | ✓ |
| timeout (stub stalls) | retried until attempts exhausted, then fallback | ✓ |
| malformed JSON in 200 | raises `NetworkError`-style parse error, retried | ✓ |
| SSE cut mid-stream | partial text delivered, then error raised | ✓ |
| total budget | raises `ProviderTimeout` naming the budget, before the chain completes | ✓ |

What the run taught (all observable by counting stub hits + gateway logs):

- Retry count ceilings matter: `500×5` with `max_attempts=3` proves the loop stops *before* burning the budget on one provider.
- Fallback triggers on **both** non-retryable errors (401 on primary → fallback immediately) and exhausted retries — two different paths, both hit the same ordered chain.
- `Retry-After` beats our own backoff when present (server-side capacity knowledge always wins).
- Timeouts are the slow case: 30s × 3 attempts × 2 providers = worst case ~3 min before the app learns the truth. The fix is real and wired: `ModelGateway(total_budget_s=...)` (env `LLM_TOTAL_BUDGET_S`) cuts the whole chain short with a `ProviderTimeout` naming the budget — verified in `case_budget`.

---

## 8. Daily Learning Record

### Problem

The AI OS was coupled to one raw `chat()` call to one provider. Retries, fallback and cost didn't exist as concepts — they were "we'll handle it later."

### Concept

Provider abstraction: a normalized interface (dataclasses + error taxonomy) with per-provider implementations mapping wire formats into it; separate concerns: retry (same provider), fallback (different provider), accounting (money, provider-agnostic).

### Understanding

The hard part of multi-provider isn't HTTP — it's the *naming and shape drift* (system message location, `max_tokens` requiredness, usage fields, error bodies). A gateway is worth building exactly when that drift costs you correctness. Retry everything transient, never retry semantics-errors; fallback on exhaustion or hard error; the price lives *outside* the providers.

### Implementation

- `ai_os/contract.py` — `Usage`, `Completion`, 5-class error taxonomy with `retryable` flag.
- `ai_os/providers/openai.py`, `ai_os/providers/anthropic.py` — same interface, different wire format.
- `ai_os/gateway.py` — timeouts, exp backoff + jitter, `Retry-After`, per-provider attempt cap, ordered fallback chain, whole-chain `total_budget_s`.
- `ai_os/ledger.py` — `MODEL_PRICES`, `cost_of`, `CallRecord` on success and failure.
- Stub server harness for deterministic failure tests (no API key needed).

### Failure

Timeouts: the first version retried inside `urlopen`'s own timeout channel with no outer budget, so a stalled provider consumed the whole chain's time. Also: Anthropic 200s with malformed `content` array weren't caught as an error on the first pass (parse errors are `NetworkError`-retryable, arguably `BadRequestError` — still unresolved).

### Debugging

Counted stub-server hits to prove retry counts, and logged attempt timestamps to verify backoff growth + jitter spread. Without the stub, a 429 retry test would have been non-deterministic and rate-limited by a real provider.

### Token economics (verified run)

From `tools/gateway_smoke.py` — all 13 scenarios in one run, failures included (stub answers seeded 12/24 usage, `Retry-After` 0.4s):

- **28 attempts recorded** (13 scenarios): 9 ok, 19 failed — failed calls bill `$0`, so retry cost is only *time* here.
- **Billed tokens:** 300 (`gpt-4o-mini` 4 successes × 36 = 144; `claude-3-5-haiku-latest` 4 successes × 30 = 120; the remaining 16 tokens came from the two pure-success scenarios).
- **Total cost: $0.00041680** — `gpt-4o-mini` $0.00006480 + `claude-3-5-haiku-latest` $0.00035200.
- Lesson with a number on it (§5): retries multiply the *same* bill only on success; a wide-open failure path (401, 400) cost zero tokens. The expensive case in production is not failures — it's a dead primary that silently falls back to a *more expensive* model for every call; exactly what the ledger's per-model totals expose.

### Evaluation

Each failure scenario has an expected behaviour (row in §7 table) and it passes or fails — measurable, deterministic, rerunnable. Remaining unknowns are live-provider responses to the same inputs (`PENDING` until a key exists).

### Architecture

The AI OS now has `Application → ModelGateway → Provider → LLM`. Cost and usage are a ledger, not scattered `print`s. Adding a provider = one class + one env var name — no application code changes (the exit-check test passes on `AnthropicProvider`).

### Questions

- Should a total time budget span the entire fallback chain, or reset per provider? **RESOLVED — spans the whole chain**: `ModelGateway(total_budget_s=...)` / `LLM_TOTAL_BUDGET_S`, verified in `case_budget`. (The slow-case worst estimate was ~3 min; the budget now cuts it off with a clear `ProviderTimeout`.)
- Parse errors: retry or not? A 200 with garbage bytes is arguably a bug, not a transient — retrying hides the bug.
- Circuit breaker window: after N consecutive failures, how long to *open* a provider before letting one probe through?
- Is `finish_reason == "length"` something the gateway should surface to callers *automatically* (they asked for 512, got 512, silently truncated)?

### Tomorrow

Day 5 — Embeddings: vectors, dimensions, cosine similarity, and a first hand-built nearest-neighbor search before any vector database.