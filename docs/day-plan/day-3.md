# Day 3 — LLM Fundamentals

## Objective

Understand what happens when an application calls an LLM. Build the first piece of the AI OS: the AI Gateway → LLM → Response path.

## Duties Outline

### 1. Learn: Tokens & Tokenization (30 min)

- [ ] Define what a token is versus a word versus a character.
- [ ] Run the first-principles tokenization exercise (BPE-style merges, see notes).
- [ ] Understand: why do bytes get grouped into tokens? Why does vocabulary size matter?
- [ ] Rough rules of thumb: ~0.7-1.5 tokens per English word; how to estimate prompt cost.

### 2. Learn: Context, Inference & Limits (45 min)

- [ ] Context window: what is it, what counts against it (prompt + generated output).
- [ ] Inference: warm-up vs steady-state, KV cache, why latency grows with output length.
- [ ] Model limitations: no true memory, no tool use without scaffolding, hallucination, staleness.
- [ ] Temperature / sampling: what it actually changes (next-token distribution), what 0 vs 1 vs 2 feels like.
- [ ] Structured output: JSON mode / function calling / constrained decoding — why raw JSON fails at low temperature without scaffolding.

### 3. Learn: Cost & Latency (20 min)

- [ ] Token pricing model (input vs output tokens).
- [ ] Which knobs affect cost: prompt size, output length, retries, caching.
- [ ] Which knobs affect latency: model size, output length, streaming vs non-streaming, batching.

### 4. Build: The AI Gateway → LLM → Response Loop (2-3 hours)

- [ ] Create `docs/` + app skeleton for the AI OS (e.g. `ai_os/`).
- [ ] Write a minimal provider client (OpenAI-compatible) — no SDK, raw `httpx`/`requests`.
- [ ] Implement a tiny `chat()` function: `prompt + messages → POST /v1/chat/completions → response`.
- [ ] Add a streaming variant (SSE) and show the difference.
- [ ] Add temperature + max_tokens parameters.
- [ ] Read your API key from the environment (do NOT commit it).
- [ ] Expose it as a local endpoint (FastAPI/Flask): `POST /chat` returns `{message, usage, latency_ms, model}`.

### 5. Important Experiment: Context Experiments (45-60 min)

Build one question, ask it four ways:

- [ ] **No context** — raw factual question.
- [ ] **Incorrect context** — provide a plausible-but-wrong document and ask after it.
- [ ] **Correct context** — same prompt with the right document.
- [ ] **Excessive context** — same plus several irrelevant documents (GDPR-legal filler).

Record for each:

```text
Prompt size (tokens) | Latency | Cost | Answer | Confidence | Hallucination observed?
```

### 6. Debug: Break It Deliberately (45 min)

- [ ] Empty prompt / huge prompt (paste an entire book into input).
- [ ] Bad JSON / truncated JSON in the middle of a response.
- [ ] Network timeout (wrong port), 401 (bad key), 429 (rate limit).
- [ ] Non-streaming vs streaming with a long output — observe first-token latency.
- [ ] Log each failure: error type, status, retry behaviour.

### 7. Documentation (20 min)

- [ ] Write `docs/concepts/llm-fundamentals.md` summary.
- [ ] Fill the Day 3 Daily Learning Record (Problem/Concept/Understanding/.../Tomorrow).
- [ ] Record today's token economics: cost per day of the experiment runs.

## Deliverable

First working AI endpoint:

```text
User
 ↓ (POST /chat)
AI Gateway
 ↓ (POST /v1/chat/completions)
LLM
 ↓ (SSE / JSON)
Response
```

## Exit Check — answer in your own words

- What is a token and why does pricing depend on it?
- What changes when you raise temperature?
- Why does an LLM hallucinate even with correct context?
- Where in the request path does latency actually come from?
- What would it take to make the `chat()` output a valid JSON object every time?