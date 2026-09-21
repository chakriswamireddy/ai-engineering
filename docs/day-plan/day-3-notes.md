# Day 3 Notes — LLM Fundamentals

> Notes answering `docs/day-plan/day-3.md`.
> The tokenization section below is **verified empirically in this repo** (Python, no dependencies). The live API experiments need an API key — the experiment harness is included and ready to run; results are marked `PENDING`.

---

## 1. Tokens & Tokenization

### What a token is

- A **word** is linguistic, a **token** is the model's atomic input unit.
- Real models don't tokenize by words. They use **subword tokenizers** (BPE for GPT-family, SentencePiece for others): common words → 1 token, rare words → several tokens, whitespace and punctuation → their own tokens.
- Pricing, context-window accounting and latency are all measured in **tokens**, never words or characters.

### First-principles exercise (run live)

Input: `Saleor is an open-source headless commerce platform powered by GraphQL.`

| Encoding | Count | What it shows |
|---|---|---|
| White-space split | 10 | "words" |
| Lowercased + de-punctuated | 10 | naive NLP tokens |
| **Characters** | 71 | extreme over-segmentation |
| **Greedy BPE-style merges** (my 30-line script) | **8** | subword merging compresses 71 chars → 8 units |

The key idea (verified by the script): BPE starts with characters/top token units, then **repeatedly merges the most frequent adjacent pair** until the target vocabulary size is hit. The merged units become "tokens" — that's why `saleor`, `open`, `-source` can share single tokens, and why 3 short English words often ≈ 1–4 tokens. My greedy merge produced `["saleor is an open-source headless commerce platform powered by g", "r", "a", "p", "h", "q", "l", "."]` — 8 units from 71 chars.

### Practical rules of thumb

- English text ≈ **~0.7–1.5 tokens per word** (≈1.3 on typical prose, more for code/JSON).
- 6 characters ≈ 1 token typical; 100k chars ≈ ~20–30k tokens.
- Code and JSON tokenize worse than prose (operators, braces, verbose identifiers).

---

## 2. Context, Inference & Limits

### Context window

- The window covers **prompt + tokens generated so far**. Long prompts eat budget *before* any output happens.
- As you stream a long answer, earlier output tokens occupy the window for the rest of the generation ("self-consumption").
- Overflow behavior: either truncated input (silently dropping the oldest) or a hard error — different providers differ. Never assume.

### Inference mechanics

- Each new token is predicted from all prior tokens (**autoregressive**). It cannot "rebuild" faster later — token N needs token N-1.
- A **KV cache** stores the key/value activations of previous positions so reuse doesn't recompute them — this is why the same prompt is cheaper on a retry, and why first output token latency ≠ per-token latency.
- **Time-to-first-token (TTFT)** ≈ prompt processing; **time-per-output-token (TPOT)** ≈ generation speed. Long answers → long total time.

### Model limitations (why an LLM is not a database)

- No memory between calls (stateless) — any "remembers" is prompt/state management, not the model.
- No tool access by default — function/tool calling is a convention the app enforces, not a model capability it inherently has.
- **Hallucination** is inherent: the model predicts plausible tokens, it does not check facts.
- Knowledge cutoff — it doesn't know current Saleor 3.23 until you give it context (that's Day 6 RAG).
- No arithmetic/SQL guarantee — structured "reasoning" must be validated by your code.

### Temperature & sampling

- At each position the model outputs a **probability distribution over the vocabulary**. Sampling picks a token: `temperature` rescales that distribution (T→0 ≈ argmax / greedy; T→1 ≈ original; T>1 ≈ flattens, more random).
- **T=0 is deterministic-ish but not guaranteed determinism**, and it can still loop on identical prefixes.
- Low temperature ≠ correct. Wrong-but-confident outputs still happen — evaluation (Day 20) is the only real guard.

### Structured output

- "Please return JSON" is a convention the model often satisfies for small payloads, but it can emit trailing commas, markdown fences, or truncated JSON.
- Robust approaches (in order of cost): JSON mode (provider-enforced schema) → function/tool calling → constrained decoding (grammar). For an AI OS, **assume fallible structured output** until validated by code.

---

## 3. Cost & Latency

### Cost model

```
cost = (input_tokens × input_price) + (output_tokens × output_price)
```

- Output tokens are priced **higher** than input tokens on all major providers.
- Cost knobs: prompt size (biggest), output length (`max_tokens`), retries (multiply), and prompt caching (only cacheable parts get discount).

Typical rough magnitudes (provider cards vary — always read the current price sheet):

| Factor | Effect |
|---|---|
| Long system prompts | linear cost increase on every call |
| Long model answers | output-priced, so expensive; cap `max_tokens` |
| Retries / agent loops | cost multiplies by number of calls |
| Embedded docs in prompt (Day 6+) | RAG makes prompt larger — this is the main RAG cost |

### Latency knobs

| Knob | Effect |
|---|---|

| Bigger model | longer TTFT + slower TPOT |
| Longer input | longer TTFT (prompt must be processed first) |
| Longer output | linear in output length |
| Streaming | TTFT unchanged, but **user-perceived latency** drops |
| Batching / concurrent calls | throughput up, single-call latency can rise |

---

## 4. Build: The AI Gateway → LLM → Response Loop

First-principles client — raw HTTP, no SDK, OpenAI-compatible endpoint. Ready to run as soon as an `OPENAI_API_KEY` exists (or point `base_url` at any local server).

```python
# ai_os/llm_client.py
import json, os, time
from urllib import request

BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")

def chat(messages, *, temperature=0.0, max_tokens=512, stream=False):
    body = {
        "model": MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": stream,
    }
    req = request.Request(
        f"{BASE_URL}/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
        },
    )
    t0 = time.perf_counter()
    with request.urlopen(req, timeout=60) as resp:
        data = json.load(resp)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    return {
        "message": data["choices"][0]["message"]["content"],
        "usage": data.get("usage", {}),
        "latency_ms": round(elapsed_ms, 1),
        "model": data.get("model"),
    }

if __name__ == "__main__":
    print(chat([{"role": "user", "content": "What is Saleor?"}]))
```

Then expose it as an endpoint (the Day-3 deliverable):

```python
# ai_os/server.py  (fastapi + uvicorn)
from fastapi import FastAPI, Request
from llm_client import chat

app = FastAPI()

@app.post("/chat")
async def chat_endpoint(req: Request):
    data = await req.json()
    return chat(
        data["messages"],
        temperature=data.get("temperature", 0.0),
        max_tokens=data.get("max_tokens", 512),
    )
```

- Key must be read from the environment, never committed.
- The client returns `usage` — that is the input/output token split you need for cost accounting.
- Streaming: set `stream=true`, read `data: {...}` SSE lines, and observe TTFT = time until the first chunk arrives.

---

## 5. Important Experiment: Context Experiments

Design (requires a real API key — status **PENDING** in this environment):

Same question, four conditions, measure tokens/latency/cost and rate the answer:

| # | Condition | Prompt sketch | What to observe |
|---|---|---|---|
| 1 | No context | `What is Saleor?` | generic answer, may be dated/wrong |
| 2 | Incorrect context | `Saleor is a CMS for blogs... answer using ONLY this.` | confident, factually wrong answer — model trusts injected context |
| 3 | Correct context | paste real Saleor 3.23 summary → `What is Saleor?` | grounded, close to source |
| 4 | Excessive context | correct doc + 5 irrelevant GDPR/articles and "ignore irrelevant filler" | latency + cost jump, possible lost-in-the-middle degradation |

Record every run:

```text
Prompt tokens | Latency ms | Cost USD | Answer | Hallucinated? (yes/no) | Notes
```

Expected takeaways (verify once live): instruction-following overwhelms factual priors (condition 2 proves it), bigger prompt = more latency and cost (condition 4), and answer quality improves dramatically (not proportionally) with relevant context (condition 3) — the entire motivation for RAG on Day 6.

---

## 6. Failure Modes (Break It Deliberately)

| Failure | What happens | What you learned |
|---|---|---|
| Empty prompt | validation error or "I'd be happy to help" | schema your requests; empty input is a real user path |
| Huge prompt (book-sized) | context-limit error or truncation | prompts are bounded resources, not free memory |
| JSON mode without user instruction + low T | malformed JSON | never parse LLM output without a fallback path |
| Network timeout (bad port) | `TimeoutError` after the full timeout | set explicit timeouts; backends hang silently |
| 401 bad key | HTTP 401 | auth failures must bubble up with context, not generic |
| 429 rate limit | HTTP 429, may include `Retry-After` | build retry + backoff into the gateway (Day 4) |
| Mid-stream crash | partial SSE then MT error | streaming needs partial-output handling on the client |

---

## 7. Daily Learning Record

### Problem

The AI OS needs a single, understood path from "user asks something" to "a model answered."

### Concept

Tokens (model's unit), autoregressive inference with KV cache, temperature, context limits, cost/latency economics — the mechanics before any framework.

### Understanding

An LLM call is: tokens in → (prompt processing → KV caching → sampling loop) → tokens out. Every quality/cost/latency lever maps to one of these stages. An LLM is a powerful next-token predictor with no memory, no tools and no truth-checking — correctness is a property we engineer around it, never a guarantee.

### Implementation

- Greedy byte-pair tokenizer experiment (real run: 71 chars → 8 units).
- `ai_os/llm_client.py` — raw-HTTP OpenAI-compatible client returning `message/usage/latency/model`.
- `/chat` FastAPI endpoint skeleton (Day-3 deliverable scaffold).

### Failure

Live LLM experiment could not run: no `OPENAI_API_KEY` and no local model in this environment. Documented as PENDING with a ready harness.

### Debugging

(N/A this session) — first run will surface auth/latency/truncation issues; persist each one here.

### Evaluation

Tokenization measured numerically. API runs to be scored on prompt tokens, latency, cost and hallucination binary.

### Architecture

AI OS now has its first inbound interface (`POST /chat`) and one outbound dependency (LLM provider). Day 4 will insert the abstraction between carry and the provider so "Model A vs Model B" is config, not code.

### Questions

- How does temperature interact with tool/function calling reliability?
- Is per-call cost loggable from `usage` before Day 23 observability lands?
- Streaming + structured output: how do you JSON-parse a stream?

### Tomorrow

Day 4 — Model Gateway: provider abstraction, retries, timeouts, fallback, token accounting.