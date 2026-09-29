# Concept — Observability: One Request's Causal Story

> Day 23. "Understand what happens inside an AI request." The deliverable is
> the **traceable AI request**: one sanitized causal record joining the request,
> its model attempts, retrieval, documents, tool calls and results, the final
> answer, and the latency / tokens / cost / errors / model / prompt-version
> fields that explain it.

## The problem: evidence everywhere, causality nowhere

After Days 6–22 the repository owns receipts for almost every value:

| Value | Where it already lived |
|---|---|
| latency | `CallRecord.latency_ms` (per attempt); agent/RAG wall clocks |
| tokens & cost | every normalized gateway `CallRecord` |
| errors | the ledger, tool rows, and the gateway's typed exceptions |
| model & provider | requested model, attempted provider, actual response model |
| documents | RAG traces and tool evidence rows |
| tool calls & results | `run_agent`'s steps and ledgers |
| quality evidence | `ai_os/trace.py` (ranks, context verdicts, grounding) |

What none of them owned is the **join**: no single request identity, no
parent–child timing tree, no attempt lineage, no prompt identity, and no one
safe artifact connecting the RAG half to the agent half to the bill. A ledger
row says *a model attempt happened*; a retrieval trace says *docs were
retrieved*; the endpoint says *a response left* — but nothing proves they belong
to the same request, which attempt served it, which prompt produced it, or which
ledger rows paid for it.

**Observability is causal attribution, not timestamp collection.** The default
learning record's one line: *a pile of logs is not a trace — a trace is one
request's causally connected, clocked, ledger-reconciled story, safe enough to
inspect without turning observability into another data leak.*

## Trace · Span · Event

Three nouns, one unit — causality.

- A **trace** is one root request: an id, an operation, wall start/end,
  monotonic duration, a final status, safe attributes, aggregate totals, and a
  span tree. Not "logs near one request" — an explicit tree with one identity.
- A **span** is a timed operation inside it:

  ```text
  span_id · parent_span_id · kind · name
  started_at / ended_at · duration_ms · status
  attributes · error · events[]
  ```

  A model attempt, a retrieval, an agent step, and a tool execution are spans.
- An **event** is a discrete fact inside a span: a document retrieved, a tool
  proposed, a fallback happened, an answer returned.

```text
request (trace_id · operation)
├── model.embed
│     └── attempt
├── retrieval
│     ├── document event (rank · score · kept/dropped · chars · hash)
│     └── document event
└── generation / agent step
      ├── model.chat
      │     ├── attempt (provider · model · retry/fallback)
      │     └── attempt
      ├── tool-call event
      ├── tool-validation event
      └── tool-result event
final-answer event (status · answer hash/size · citations · termination)
```

### One request, one root

A root is created at the process boundary; every internal span descends from
it. A nested gateway call attaches to the **active** context (a scoped
`ContextVar` in `ai_os/observability.py`) — it never mints a second identity. A
helper that mints its own id produces four unrelated artifacts; orphan rejection
and a single root are what make the tree walkable.

### Two clocks, two questions

`started_at`/`ended_at` are UTC wall timestamps — *when* the request happened,
useful to humans and cross-system search. Every `duration_ms` comes from
`time.monotonic_ns()` — *how long* it took, immune to NTP correction, timezone
changes, and clock jumps. If duration came from wall time, a host-clock
correction could make a two-millisecond span negative.

### Nested duration is not additive

A model call contains HTTP time; an agent step contains model choice plus tool
execution; the root contains all of them. **Never** report `sum(span.duration_ms)`
as request latency — nested spans overlap and parallel tool calls overlap harder.
The **root's** duration is the request latency; each span owns its own duration.

### A small status vocabulary

`ok` · `error` · `timeout` · `cancelled` describe how an operation *ended*. Domain
outcomes are attributes: a retrieval refusal is an `ok` operation with
`refused=true`; a tool rejection is an instrumented call with a rejected
validation; a rate limit is `timeout` only if it actually timed out, else `error`
with `kind=rate_limit`. Prose is never parsed to learn whether the process
crashed. The error taxonomy (`provider_error`, `rate_limit`, `invalid_key`,
`bad_request`, `dimension_mismatch`, `tool_error`, `tool_rejection`,
`budget_exceeded`, `role_violation`, `internal`) rides alongside.

## Model identity is a lineage

"Model" is a chain of decisions and outcomes, not one field:

- the requested model from configuration;
- the provider attempted;
- the attempt number and fallback depth;
- the actual model reported by the successful response;
- the finish reason;
- whether the attempt succeeded, was retried, or handed off.

A retry is **another attempt span under one logical `model.chat` span** — not a
new request and not a sibling with no story. A fallback names the next
provider/model while preserving the previous failure. A trace that stores only
the final model cannot answer why a request became slower or more expensive: the
primary may have timed out, returned a rate limit, rejected the request, or
exhausted its retry budget. The attempt lineage is the cause.

## Prompt version needs semantics *and* bytes

A prompt identity is three facts: a stable semantic name, an explicit version,
and the exact-content SHA-256.

```python
PromptRef.of("rag-answer", "v1", SYSTEM_PROMPT)
# -> name="rag-answer", version="v1", sha256=<hash of the exact template>
```

The caller supplies the `PromptRef`; the tracer never inspects the first
sentence and guesses. A template edit without a hash change is undetectable; a
version string with no content identity can lie. Keeping both makes reports
readable (`rag-answer@v1`) and replay-honest (the hash pins the bytes).

## Money comes from the ledger

Every model/embedding span links the exact `CallRecord`; the root's totals
aggregate those rows under the ledger's existing policy — **including failed
attempts**, where the ledger counts them. The trace never reprices a call from a
model name and a token count: two price tables will drift, and the first
disagreement makes the observability system lie about its most quantitative
field. An unknown model still prices at `$0` by design, but the trace marks
`price_status=unknown` — `$0.00` is a calculation, never proof of free inference.

## Errors are causes with owners

A safe error carries a kind, provider/model, attempt, retryability, and a
redacted message, and the trace shows **what happened next**: retry, fallback,
refusal, guardrail stop, budget stop, or root failure. A trace that ends at
`ProviderError: timeout` without showing that the next attempt succeeded on a
fallback is half the story; leaving descendants open after a root fails makes the
tree lie. A correct out-of-corpus refusal is not a system error — it is an `ok`
operation with `refused=true`.

## Metadata before content

The default safe record is **ids, names, enums, booleans, counts, sizes, hashes,
ranks, scores, model names, timings, costs, and bounded redacted error facts**.
It excludes raw user messages, full system prompts, retrieved document text, tool
arguments/data, customer records, authorization headers, API keys, and provider
response bodies. A raw diagnostic mode exists — it is explicit, size-capped, and
still redacts secrets — but it is unnecessary for the O1–O9 mechanics. The
deliverable is traceability, not an accidental data lake:

```text
reduces privacy/storage risk   ←── metadata-safe default ──→   hides exact text
```

## Where tracing must stop

The tracer **wraps boundaries; it never re-runs them**. Measuring chat by calling
chat again, retrieving by retrieving again, or executing a tool twice creates a
second production run, a second bill, and possibly a different result — exactly
the error the evaluation benches forbade since Day 20. Instrumentation is
**passive**: the O9 control asserts tracing off ≡ tracing on, byte-for-byte on
every observable output except the additive trace id.

## The boundary: observability vs evaluation vs distributed tracing

- **Operational trace** (`ai_os/observability.py`) owns identity, time, status,
  model lineage, and cost references.
- **Evaluation trace** (`ai_os/trace.py`) keeps recall@K, MRR, context verdicts,
  grounding, and per-step evidence. Shared document/evidence/trace ids **join**
  the two; they answer different questions, so one is not collapsed into the
  other.
- **Distributed tracing** is Day 24: cross-process correlation, propagation, and
  end-to-end topology over User → AI API → Agent → RAG → store → Saleor/PostgreSQL.
- **Durable storage, exporters, sampling, and retention policy** are deployment
  work — a local in-memory store is not production telemetry.

The trace can prove which bytes of an answer were returned (subject to the hash
contract) without storing the prose. It reconstructs *causal execution and
provenance* — it is **not exact replay**, which is a stronger, more
privacy-sensitive claim. Calling a metadata trace "replay" blurs traceability
with determinism, the distinction the benches have enforced since Day 20.

## The implementation in one screen

| Concern | Where |
|---|---|
| `Trace` / `Span` / `Event`, monotonic timing, status vocab | `ai_os/observability.py` |
| scoped context + bounded oldest-first `TraceStore` | `ai_os/observability.py` |
| redaction by key + content hashing, raw cap | `ai_os/observability.py` (`sanitize`) |
| attempt spans, ledger linking, stream TTFT | `ai_os/gateway.py` |
| request/retrieval/document/generation/answer + `RAG_PROMPT_REF` | `ai_os/rag.py` |
| step/tool-call/validation/result/final + prompt refs | `ai_os/agent.py` |
| `trace_id` + `GET /traces/{id}` | `ai_os/server.py` |
| O1–O9 + O9 control | `tools/trace_smoke.py` |

**O9 is the most important fixture.** Tracing off must leave the same answer,
provider/attempt sequence, tool results, token counts, and cost; only the trace
and its id are additive. A changed answer under instrumentation is an
instrumentation bug — never a reason to move a baseline.
