# Day 23 Notes — AI Observability

> Notes answering `docs/day-plan/day-23.md`.
> Twenty-two days built a machine that reads (Days 6–10), acts (11–12), fails honestly (13), computes (14–15), plans (16), remembers (17–18), decides its own architecture (19), and learns to be judged (20–22). Days 20–22 put retrieval and agent behavior on fixed benches, but their verdicts still arrive through a causal gap: **what happened inside the request that produced this result?** The curriculum's Day 23 answer is **"Understand what happens inside an AI request,"** with the required spine **Request → Model → Retrieval → Documents → Tool calls → Tool results → Final answer** and the required fields **latency, tokens, cost, errors, model, prompt version**. The repo already owns scattered receipts: `trace.py` carries retrieval ranks, context decisions, and per-step evidence; `Ledger` records provider/model, tokens, cost, latency, success, and error for every attempt; the gateway knows retries and fallback; RAG separates retrieval from generation; and the agent returns its steps, calls, results, termination, and cumulative cost. What those records do not own is **causality** — no common request id, no parent-child timing tree, no attempt lineage, no prompt identity, and no one safe artifact joining RAG and agent execution to the bill.
> The single idea: **an AI request is observable only when every consequential step can be connected, timed, attributed, and inspected without guessing — so Day 23 builds one request identity, a nested span tree, explicit prompt references, and a ledger-reconciled metadata record, while leaving the answer path and every existing evaluation trace untouched.** Trace mechanics stay `MECH`: identity, parentage, monotonic duration, status, token/cost arithmetic, prompt hashes, lookup, and redaction are deterministic. Meaning does not sneak in: a trace can prove which answer was returned, not whether it was correct or whether a retrieved document supported it. The default is metadata before content — ids, names, counts, ranks, sizes, hashes, timings, costs, and error kinds — while raw prompts, documents, tool payloads, customer data, and answers stay out unless an explicit bounded diagnostic mode asks for them. The tracer never re-runs chat, retrieval, or tools to measure them, and O9 keeps it passive: tracing off and on leave the same answer, attempts, tools, tokens, and cost. Day 23 earns “traceable”; Day 24 earns “distributed.”
>
> **The one line the day would die defending: a pile of logs is not a trace — a trace is one request's causally connected, clocked, ledger-reconciled story, safe enough to inspect without turning observability into another data leak.**

---

## 1. The Loaded Question — “Understand What Happens Inside an AI Request”

Day 23's objective sounds softer than “build the agent” or “stop judging AI quality manually,” but it is stricter than both. **Understand what happens inside an AI request** does not ask for more timestamps around obvious calls. It asks for a causal account of one request: what entered, which model was actually used, which prompt version made each turn, what evidence was retrieved and shown, which tools were proposed and what they returned, where time went, what failed, what the final answer was, and what the whole request cost.

The existing system is not uninstrumented. It is **fragmented**:

- **Latency** exists per provider attempt in `CallRecord.latency_ms`; agent and RAG paths have partial wall-clock records.
- **Tokens and cost** exist on every normalized gateway call, including attempts the ledger chooses to count.
- **Errors** exist in the ledger and in tool/agent rows.
- **Model and provider** exist, with retries and fallback in `ModelGateway`.
- **Documents** exist in RAG traces and tool evidence.
- **Tool calls and results** exist in `run_agent`'s steps and ledgers.
- **Evaluation evidence** exists in `ai_os/trace.py`.

What is missing is not value coverage. It is **identity and parentage**. A ledger row says *a model attempt happened at time T*. A retrieval trace says *a query retrieved these docs*. An agent trace says *a loop made these choices*. The endpoint says *a response left*. Nothing yet proves those records belong to the same request, which attempt served it, which prompt produced it, or which exact ledger rows paid for it. The day is therefore a join, not a logging firehose.

Four invariants keep the join honest:

1. **One request, one root.** Every internal span descends from one trace id; a nested helper does not mint a disconnected identity.
2. **One ledger, one bill.** Token and cost totals read exact linked ledger records; the tracer never invents a second pricing function.
3. **Metadata before content.** A useful trace exists without raw prompts, documents, tool data, or answers.
4. **O9: tracing is passive.** Installing it changes no answer, retry, tool execution, token count, or cost.

The day's scope boundary is equally important. Cross-process correlation, distributed propagation, queues, workers, exporters, and production retention belong to Day 24 or later deployment work. Security policy belongs to Days 25–26. Quality verdicts belong to Days 20–22. Day 23 builds the smallest mechanism that can honestly make one in-process AI request traceable.

---

## 2. Trace, Span, Event — The Unit Is Causality

The smallest useful vocabulary has three nouns.

A **trace** is one root request. It owns the root id, operation, start/end wall timestamps, monotonic duration, final status, safe request attributes, and aggregate totals. It is not “all logs emitted near one request”; it is an explicit tree with one identity.

A **span** is a timed operation inside the trace:

```text
trace_id
span_id
parent_span_id
kind
name
started_at / ended_at
duration_ms
status
attributes
error
```

A span answers “what operation ran, under which parent, for how long, and how did it end?” A model attempt, retrieval, agent step, and tool execution are spans. A request root is a span. An event answers a discrete fact inside a span: document selected, tool proposed, fallback occurred, answer returned. A trace is the sum of that structure, not a list of unrelated lines.

Parentage is the mechanism that makes causality inspectable:

```text
request
├── model.embed attempt
├── retrieval
│   └── document events
└── generation / agent step
    ├── model.chat attempt
    ├── tool-call event
    ├── tool-result event
    ├── next model attempt
    └── final-answer event
```

A retry does not become a new request. It is another attempt span under the same logical model call. A fallback names the next provider/model while preserving the previous failure. Parallel tool calls are siblings under a step. A deferred or capped call is an event saying it was not executed; inventing an execution span for it would falsify the trace.

Time needs two clocks. A UTC wall timestamp answers **when** the request happened and is useful for humans and cross-system search. A monotonic clock answers **how long** it took and must own every duration. If duration came from wall time, an NTP correction could make a two-millisecond span negative. Trace records therefore store wall start/end metadata but derive `duration_ms` from `time.monotonic_ns()`.

Nested duration is not additive. A root contains retrieval and generation; a generation contains attempts; an agent step contains model choice and tool execution. Summing every span double-counts nested time and can overstate total latency, especially under parallel tool calls. The root wall-clock duration is the request latency; each span owns its own duration; a critical-path or self-time summary is useful only when its overlap semantics are explicit.

The status vocabulary stays small and structural: `ok`, `error`, `timeout`, `cancelled`. Domain outcomes remain attributes. A retrieval refusal is an `ok` operation with `refused=true`; a tool rejection is an `ok` instrumented call with a rejected validation result or an explicit tool error depending on the seam; a provider rate limit is a `timeout` only if it actually timed out, otherwise an `error` with `kind=rate_limit`. Prose should not have to be parsed to learn whether the process crashed.

---

## 3. Model Identity, Prompt Version, Money, and Failure

“Model” is not one field. It is a chain of decisions and outcomes:

- requested model from configuration;
- provider attempted;
- attempt number and fallback depth;
- actual model reported by the successful response;
- finish reason;
- whether the attempt succeeded, was retried, or handed off.

A trace containing only the final model cannot answer why a request became slower or more expensive. The primary may have timed out, returned a rate limit, rejected the request, or exhausted its retry budget. The attempt lineage is the cause.

A **prompt version** needs three parts: a stable semantic name, an explicit version, and the hash of the exact prompt or rendered message set. For example, `agent-reasoning-v1` says which human intent the template represents; its exact SHA-256 says which bytes ran. The caller supplies a `PromptRef`; the tracer does not inspect the first sentence and guess. A template change without a hash change is impossible to detect honestly, while a version string with no content identity can lie. Keeping both lets reports stay readable while preserving reproducibility.

Money comes from the ledger. Every model or embedding span links the exact `CallRecord` index/id. Root totals aggregate those linked rows under the ledger's existing policy, including failed attempts where the ledger includes them. The trace does not compute a second price from model name and token count. Two price tables will drift, and the first disagreement will make the observability system lie about the most quantitative field it owns. If a model is unknown to the pricing table, the existing policy prices it at zero by design, but the trace marks `price_status=unknown`; `$0.00` is a calculation, not proof of free inference.

Errors are causes with ownership. A safe record carries an error kind, provider/model, attempt, retryability, and a redacted message. It should also show what happened next: retry, fallback, refusal, guardrail stop, budget stop, or root failure. A trace that ends with `ProviderError: timeout` but does not show that the next attempt succeeded on a fallback is only half the story. Conversely, leaving descendants open after a root fails makes the tree lie.

Privacy is part of correctness. The default safe attributes are:

- ids and names;
- enums and booleans;
- counts and sizes;
- hashes;
- ranks and scores;
- model/provider names;
- token, cost, and timing values;
- bounded redacted error facts.

The default excludes raw user messages, full system prompts, retrieved document text, full tool arguments/data, customer records, authorization headers, API keys, and provider response bodies. A raw diagnostic mode may capture bounded content locally, but it must be explicit, size-capped, redacted, and unnecessary for the O1–O9 mechanics. The deliverable is traceability, not an accidental data lake.

---

## 4. RAG and Agent Events — Two Existing Traces Become Causal

The RAG path becomes a causal tree without changing a retrieval or generation decision:

```text
request
├── model.embed attempt
├── retrieval
│   ├── document event: retrieved rank 1
│   ├── document event: retrieved rank 2, dropped by budget
│   └── gate/refusal event
├── model.chat attempt
└── final answer: refused/citations/answer hash
```

The useful distinction is **retrieved versus shown**. A request can retrieve ten candidates and fit two into context. The trace must expose both counts, document ids, ranks, scores, keep/drop reasons, and content sizes/hashes. Raw text is unnecessary to diagnose crowding, budget loss, dimension mismatch, or an unexpected corpus. The existing Day-7 trace remains the evaluation artifact — recall@K, MRR, duplicate ratios, verdict — and the observability tree references the same document ids rather than replacing those fields.

The agent path uses the same grammar:

```text
request
├── step 1
│   ├── model.chat_with_tools attempt
│   ├── tool-call event: search_saleor_code
│   ├── validation event
│   ├── tool-result event: evidence ids/counts
│   └── model result: next turn / answer
├── step 2 ...
└── final answer: termination, citations, grounding flags
```

The important seam is the **proposal**, not a later scan. At `registry.validate_args` and `registry.execute`, the tracer knows the tool name, whether arguments passed, what kind of result came back, and how long execution took. A final log that says “the agent called a tool” without the proposal, validation, result, and step parentage is a summary, not an explanation.

The final answer is likewise a safe event: status, answer hash/size, citation ids/count, refusal, termination reason, and fabrication flags. The response itself already exists at the caller. A metadata trace can prove which bytes of a particular answer were returned (subject to the hash contract) without storing the prose. It can reconstruct the execution, but exact replay is a stronger and more privacy-sensitive claim. The day should not make that stronger claim by accident.

This also reveals the difference between observability and evaluation. If retrieval missed the expected document, a trace can show the document's rank and the gate's score. Only the RAG benchmark can decide whether that was a retrieval failure against a pre-registered oracle. If the agent chose a poor tool, a trace can show the choice, arguments, result, and next turn. Only the agent benchmark can grade whether the tool was unnecessary or the arguments semantically wrong. The trace supplies evidence; the bench issues the verdict.

---

## 5. Design: The Trace Contract

The design is deliberately small:

```text
TRACE
  schema_version
  trace_id
  operation
  started_at / ended_at
  duration_ms
  status
  safe_attributes
  totals
  spans[]
  events[]

SPAN
  trace_id
  span_id
  parent_span_id
  kind
  name
  started_at / ended_at
  duration_ms
  status
  attributes
  error
```

The decisions that matter:

**Decision 1 — operational trace and evaluation trace stay separate but correlated.** `ai_os/observability.py` owns request identity, timing, status, model lineage, and cost references. `ai_os/trace.py` keeps retrieval ranks, context verdicts, grounding, and agent-step evaluation. Shared document/evidence/trace ids join them. A single giant object would force operational retention to carry evaluation fixtures and force benchmark code to know about HTTP/provider details; the two records answer different questions.

**Decision 2 — wrap boundaries; never re-run them.** Gateway attempts, retrieval, validation, tool execution, and answer completion are instrumented where they happen. Measuring chat by calling chat again, retrieving by retrieving again, or executing a tool twice would create a second production run, second bill, and possibly different result. The evaluation benches already adopted “the judge never re-executes”; observability inherits the same law.

**Decision 3 — linked ledger records are billing truth.** A model span points to a ledger record; root totals aggregate those records. The trace does not reprice the call and does not attach one successful call's cost while forgetting failed retries. It also never adds nested span durations to produce latency.

**Decision 4 — metadata-safe is the default product.** The trace answers which request, model, prompt, evidence, tool, error, duration, and cost without requiring raw payload capture. This makes inspection safer and the fixtures deterministic. Raw mode is diagnostic, bounded, and optional.

**Decision 5 — local before distributed.** A root id is created at the current process boundary and propagated through in-process scoped context. A bounded thread-safe in-memory store and `GET /traces/{trace_id}` make the trace inspectable now. Day 24 owns cross-component propagation, distributed trace formats, and end-to-end topology. Durable storage/export belongs to deployment work, not hidden inside today's local mechanism.

---

## 6. Build: The Trace Core and Prompt References

`ai_os/observability.py` is the mechanism-first implementation: a trace context/recorder, a span with explicit lifecycle, event recording, monotonic timing, typed status/error handling, totals, serialization, and a bounded `TraceStore`. No OpenTelemetry SDK is required to understand it. The module should be dependency-free enough to run in the existing Python application and tests.

The span lifecycle is small but strict:

- create root once;
- start child under a live parent;
- end each child exactly once;
- close root after all expected work or the failure boundary;
- reject events on closed spans;
- reject orphan children;
- preserve completed ancestor state when a descendant fails;
- serialize to plain JSON-safe structures.

The context must be scoped, not a mutable global singleton. A context variable is appropriate for nested synchronous calls; an explicit trace object remains the API. Standalone gateway/RAG/agent calls still need a root, so the helper can create one or the caller can supply one without hidden cross-request leakage.

`PromptRef(name, version, sha256)` is the prompt provenance primitive. The RAG system prompt, agent reasoning prompt, and termination notes receive stable semantic versions and exact-content hashes. The prompt text remains where it currently lives unless a small central registry makes the version relationship safer; behavior must not change merely because the string moved. A rendered-message digest can be added where the exact prompt plus inputs need one stable identity, but credentials and raw content still do not enter the default trace.

`Ledger.CallRecord` gains trace and span provenance plus operation/attempt/prompt fields where available. This is an additive extension: existing aggregates, failure visibility, and append-only behavior remain. The ledger remains responsible for money; the trace merely cites and aggregates it.

The trace store is deliberately local. It needs a maximum trace count, deterministic oldest-first eviction, thread-safe reads/writes, and an unknown-id path. It does not need a database, background exporter, sampling engine, or distributed backend today. If a later day chooses a real telemetry backend, the schema and local algorithm should survive the change.

---

## 7. Build: Instrument the Existing Seams

The gateway is the model's attempt boundary. `chat`, `chat_with_tools`, `stream`, and `embed` each wrap every actual provider attempt in a child span linked to the ledger record. The trace records requested/actual model, provider, attempt and fallback depth, finish reason, usage, cost, latency, retryability, and safe error facts. Streaming records time-to-first-event and total duration while yielding chunks unchanged. The tracer never buffers an entire stream merely to know it ended.

RAG instrumentation follows the existing pipeline. A request root contains the embedding/retrieval work, document events, generation attempt, and final answer. Explicit query/document/answer hashes and sizes make the record useful without copying content. The existing Day-7 `retrieve_trace` and `run_trace` fields remain available; shared trace/document ids connect the operational and evaluation views. RAG decisions — k, budget, gate, refusal, citation set — remain exactly where they are.

Agent instrumentation follows the loop. Each step gets a span. The model's tool turn gets a model-attempt child. Every proposed call gets a call event with a safe argument shape; validation gets its own status; execution gets a result event with evidence/data counts, refusal/rejection/error kind, and duration. Parallel calls are siblings. Deferred and capped calls say “proposed, not executed” and never receive fake result spans. The forced final turn after max steps, budget, or gave-up is a real model attempt and must be traced like any other. Final grounding and termination populate the final event.

The server returns `trace_id` for existing `/chat`, `/rag`, and `/chat/stream` responses and exposes `GET /traces/{trace_id}` for the sanitized record. Direct `run_agent` calls return/attach the same id through their result/context. There is no need to invent a broad agent API just to make tracing possible; the direct runner is enough for the current seam, while Day 24 can map the real end-to-end component topology.

Nested calls attach to the active root through scoped context. A gateway call with no request context gets its own root or an explicit “unscoped call” policy; it never attaches to whichever request happened to be active on another thread.

---

## 8. Test: The O-Fixtures and the Control

Expected behavior is written before implementation:

| ID | What it proves | Input / injection | Expected |
|---|---|---|---|
| O1 | root spine | one chat, RAG, or direct-agent request | one root; every child has a valid parent path and same trace id; monotonic root duration; no sum-of-spans latency |
| O2 | RAG spine | successful docs RAG with a kept and a dropped candidate | request → embed → retrieval → document events → chat → answer; raw text absent; ids/counts/ranks/sizes/hashes present |
| O3 | agent spine | multi-step script, parallel calls, one deferred call | every proposal/result is linked to its step; parallel siblings do not become sequential parents; deferred says not executed; termination reaches final event |
| O4 | model lineage | 429 retry then success; provider fallback | sibling attempts under one logical model call; each provider/model named; actual model and finish reason present |
| O5 | money | successful chat/embed plus failed attempt | trace tokens/cost equal exact linked ledger delta; retries follow ledger policy; unknown price is flagged |
| O6 | failure closure | rate limit, timeout, invalid key, bad request, dimension mismatch, tool error, budget stop | correct typed errors; completed ancestors retained; no open span marked `ok`; fallback/degradation visible |
| O7 | prompt provenance | RAG, reasoning, termination prompt calls | explicit name/version/hash on each model span; exact template change changes hash; no prompt body by default |
| O8 | inspection/redaction | lookup, unknown id, store overflow, raw-debug attempt, stream | complete sanitized tree; clean 404; oldest-first bounded eviction; secrets/headers excluded; raw fields capped; stream bytes/TTFT unchanged |
| O9 | **control** | tracer installed and unused/off | same answer, provider/attempt order, tool results, tokens/cost, and existing fixtures; only trace/id fields are additive |

The `MECH`/`PENDING` split remains honest. **Identity, parent paths, monotonic duration relationships, ledger arithmetic, prompt hashes, JSON serialization, lookup, retention, and redaction are `MECH`.** The selected balance of attributes, usefulness to an operator, and future sampling/retention policy are design judgments. Provider-specific full-fidelity payload capture, durable storage, exporters, and distributed correlation are explicitly outside today's proof.

The control is the most important row. Existing gateway, RAG, tool, and agent tests run with tracing off and on. A changed final answer, tool result, attempt sequence, token count, or cost means the instrumentation changed behavior. Fix the tracer; do not move a baseline. Instrumentation must never become an optimization or fallback policy wearing a telemetry coat.

---

## 9. Measure: The Trace Report and the Price of Looking

The measurement is two layers. First, a compact request table:

| trace_id | operation | model path | prompt versions | docs | tools | steps | attempts | tokens | cost | latency | errors | status |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `…` | rag | primary → fallback | rag-answer-v1 | 4/7 shown | — | — | 2 | … | … | … | rate_limit→ok | ok |
| `…` | agent | primary | reasoning-v1 + termination-v1 | — | 2/2 | 2 | 3 | … | … | … | one rejection | ok |
| `…` | rag | none | rag-answer-v1 | 0 | — | — | 1 | … | … | … | dimension mismatch | error |

Second, the actual span trees for four hostile paths: successful RAG, successful agent, retry/fallback, and failed/hung request. The report answers:

- Which provider/model actually answered?
- Which prompt version/hash made each model turn?
- Which documents were retrieved, kept, and dropped?
- Which tool was proposed, validated, executed, or not executed?
- Which attempt paid each ledger row?
- Which span owns the request's critical latency?
- What failed, where, and what recovery happened?

Tracing itself costs **0 chats and 0 embeds**. It adds local CPU, memory, serialization, and retention overhead, which should be measured rather than hidden. Model cost and token totals must be identical to the untraced control. If trace collection makes the request slower enough to change provider retries or timeouts, O9 fails even when the report looks useful.

The day does not claim exact replay. A safe trace reconstructs causal execution and provenance; it does not reconstruct hidden content or guarantee that a stochastic model will produce the same answer when run again. Calling that “replay” would blur traceability with determinism, exactly the distinction the evaluation benches have been enforcing since Day 20.

---

## 10. Documents

- `docs/concepts/observability.md` — trace vs span vs event, parentage, monotonic timing, attempt lineage, prompt refs, ledger reconciliation, privacy defaults, and the boundary between observability, evaluation, distributed tracing, and production telemetry.
- `docs/evaluations/trace-day23.md` — O1–O9 results, four safe trace trees, model/fallback lineage, latency/cost reconciliation, prompt hashes, redaction/retention results, O9 control, and the remaining `PENDING` lines.
- PROGRESS rollup — mark the trace core, gateway/RAG/agent instrumentation, lookup, and O1–O9 stub mechanics only when their deterministic verification is actually green. Keep raw capture, durable storage/export, production retention, and cross-process propagation `PARTIAL`/`PENDING`.

---

## 11. Daily Learning Record

### Problem

The system has evidence everywhere and causality nowhere. `trace.py` can explain retrieval ranks, context loss, grounding, tool steps, and termination. `Ledger` can explain a provider attempt's model, tokens, cost, latency, and error. `ModelGateway` knows retry/fallback. `run_rag` separates retrieval and generation; `run_agent` separates choices, calls, results, and exits. Yet no common request identity joins those records. A latency spike cannot yet be attributed across the full request, a model change cannot be tied to the exact prompt that made it, a tool rejection cannot be followed to the next decision, and a surprising bill cannot be reconciled from one safe artifact. The evaluation benches can say a result is good or bad; they cannot say exactly which request lineage produced it. The problem is not missing logging. It is missing **trace identity, parentage, prompt provenance, and ledger reconciliation** — and the risk of solving it naively is worse than the current gap: a giant raw-content log that leaks sensitive data, double-counts nested time, reprices calls, or changes the run it claims to observe.

### Concept

**Observability is causal attribution, not timestamp collection.** One request gets one root trace. Consequential operations become spans with explicit parents; discrete facts become events. A model retry is another attempt under one logical call; a fallback is another provider/model with its cause; a tool proposal precedes its validation and result; retrieved documents are distinguished from documents actually shown; the final answer is an event with status, provenance, citations, and a safe identity. Duration comes from a monotonic clock, while wall time says when. Tokens and cost come from exact linked ledger records, never a second pricing path. Prompts carry a stable semantic version and exact-content hash. The default record is metadata-safe. The tracer wraps existing boundaries and never re-executes them. Operational traces and evaluation traces stay separate but correlated. Local scope comes before distributed scope, and O9 guarantees that observation changes no answer, attempt, tool result, token count, or cost.

### Understanding

The re-readings hit in order: **(1) The fields are mostly not new; the join is.** The repository already knows most values. Day 23's work is to give them a common request identity and parentage so a reader can move from final answer back to causes and forward to errors and money. **(2) Logs answer lines; traces answer hierarchy.** A timestamped “model called” is useful only when it says which request, which logical call, which attempt, and which parent operation it belongs to. **(3) Two clocks answer two questions.** UTC wall time says when; monotonic time says how long. Mixing them makes durations vulnerable to host-clock correction. **(4) Nested time does not add.** A root request is not root duration plus every descendant; doing so double-counts model calls inside generation and calls inside steps, and breaks harder under parallel tools. **(5) Model identity is a lineage.** Requested model, attempted provider, actual model, retry, fallback, and finish reason explain cost/latency differences that a final-model label cannot. **(6) Prompt provenance needs semantics and bytes.** A version says which intended prompt family ran; a hash says the exact content. Neither is optional. **(7) The ledger owns money.** A second trace-side price calculator is a future disagreement waiting to happen; unknown price needs an explicit status, not false confidence. **(8) The existing RAG and agent traces should be linked, not swallowed.** They answer quality and behavior; observability answers execution. Shared ids are enough. **(9) Safe-by-default is part of correctness.** A trace that requires raw prompts, documents, customer data, and tool payloads to be useful is also a data-governance incident waiting to happen. **(10) Observability must be passive.** A tracer that calls the model again has created a second production run; a tracer that changes retries has contaminated the latency it measures. O9 is not ceremonial.

### Implementation

- `ai_os/observability.py` (new) — trace context/recorder, span lifecycle, events, monotonic timing, status/error types, JSON schema, safe totals, prompt refs, and bounded thread-safe trace store.
- `ai_os/gateway.py` (extends) — attempt-level spans for chat, tool chat, stream, and embed; provider/model/attempt/fallback lineage; linked ledger records; TTFT for streams; no content change.
- `ai_os/ledger.py` (extends) — additive trace/span/operation/attempt/prompt provenance on `CallRecord`; existing accounting and summaries unchanged.
- `ai_os/rag.py` + `ai_os/trace.py` (extend) — RAG request/retrieval/document/generation/answer spans and events; explicit prompt refs; correlation with the existing evaluation trace.
- `ai_os/agent.py` (extends) — step, model-turn, tool-call, validation, tool-result, forced-final, and final-answer tracing; parallel/deferred/capped semantics preserved.
- `ai_os/server.py` (extends) — `trace_id` in chat/RAG/stream responses and `GET /traces/{trace_id}` over the bounded local store.
- `tools/trace_smoke.py` + `test_day23.py` — O1–O9 and the no-regression control, offline and deterministic.
- `docs/concepts/observability.md`, `docs/evaluations/trace-day23.md`.

### Failure

1. **A new id at every layer.** The request, gateway, RAG, and agent each mint a trace id, producing four unrelated artifacts. Anti-fix: one root at the boundary; scoped child context; orphan rejection.
2. **Trace-side pricing.** The trace sums tokens and applies its own model prices, disagreeing with the ledger. Anti-fix: link exact ledger records and aggregate them; mark unknown prices.
3. **Sum nested durations as request latency.** Retry/model/generation/root times are added as if independent. Anti-fix: root monotonic duration is the request; children are attribution.
4. **The version string lies.** A prompt changes but keeps `v1`, or `v2` labels unrelated bytes. Anti-fix: explicit `PromptRef` with exact-content hash.
5. **Raw payload by default.** Full prompts, docs, tool arguments, customer records, and headers enter the trace. Anti-fix: metadata-safe default, explicit bounded raw mode, secret redaction.
6. **The tracer re-runs the operation.** “Measure latency” calls chat/retrieval/tool again. Anti-fix: wrap the real boundary; second execution is a second production run.
7. **Failure strands open spans.** A child dies but ancestors remain `ok` or the root hangs. Anti-fix: typed status, exception closure, completed-ancestor preservation, O6.
8. **A refusal is logged as a system error.** A correct out-of-corpus refusal increments the error count. Anti-fix: status describes execution; `refused` is a domain attribute.
9. **Streaming is altered.** The tracer buffers chunks to measure the end, changing TTFT or byte delivery. Anti-fix: pass chunks through; measure around them.
10. **The trace improves or changes the run.** Logging prompts a fallback, alters timeout pressure, or accidentally triggers a call. Anti-fix: O9 byte/behavior control; trace off ≡ tracing unused.
11. **Traceability is called replay.** A metadata record is advertised as reconstructing hidden input or guaranteeing identical stochastic output. Anti-fix: claim causal reconstruction and provenance only.
12. **The local store is mistaken for production telemetry.** In-memory bounded traces are called durable, sampled, or distributed. Anti-fix: name the boundary; Day 24 and deployment work own the rest.

### Debugging

- **A span is missing** → walk parent ids from the leaf. If the parent is absent, context propagation failed; if the parent exists but the child does not, the operation was not instrumented or a second call was swallowed by deferred/capped semantics.
- **Root latency exceeds real wall time** → inspect how duration was derived. Wall-clock subtraction, clock jumps, or a synthetic test clock belongs in the bug book; production duration comes from monotonic time.
- **A trace's latency is far larger than the endpoint** → check whether the endpoint returned before background work ended, or whether the tracer started measuring at a different boundary. One request may legitimately have a post-response finalization span, but it must be named, not hidden.
- **Token/cost totals disagree with `/ledger`** → compare linked record indices first. A missing retry, duplicate aggregate, or trace-side price calculation owns the discrepancy; the ledger remains the reference.
- **The wrong prompt version appears** → inspect the explicit `PromptRef` passed to the call, not the rendered first line. Verify the template hash at source and the version registry; never infer.
- **A tool call appears twice** → distinguish proposed call, validation, execution, and result event. Parallel/deferred calls are separate semantics; a repeated id may be a legitimate retry or a genuine duplicate, and the fingerprint/arguments decide.
- **A document is “missing” from the trace** → distinguish retrieved candidates from kept context and inspect the drop event. Day 23 records both, while the RAG trace owns quality metrics.
- **A safe trace contains sensitive content** → quarantine the raw-debug path, fail the redaction fixture, and rotate/revoke any exposed credential. Observability code must fail closed on secrets.
- **The untraced and traced answers differ** → O9 owns the investigation. Disable tracing, compare provider/attempt/tool/ledger sequences, and fix the side effect in instrumentation; do not bless the new baseline.

The trace should answer the question directly: which parent, which attempt, which prompt, which evidence, which error, which ledger record, which milliseconds. A debugging loop that still needs intuition has not finished the trace.

### Evaluation

The O1–O9 table and four trace trees in `docs/evaluations/trace-day23.md` are the evidence. The numbers a future observer can carry: number of traces with complete parent paths; number of orphan/open spans (target 0); model attempts reconciled to ledger records (target 100%); token/cost deltas between traced and untraced runs (target 0); prompt refs with matching content hashes (target 100% of model calls); raw secrets found in default traces (target 0); trace lookups returning the full sanitized tree; oldest-first retention under overflow; stream bytes/TTFT unchanged under tracing; and existing gateway/RAG/tool/agent/evaluation fixtures unchanged with tracing on and off.

The `PENDING` line is plain: operational usefulness and attribute selection need real review; production retention/sampling policy needs a real operating model; durable storage/export and cross-process correlation are not built; provider-specific payload fidelity and exact replay are not promised. Day 24 owns end-to-end propagation across User → AI API → Agent → RAG → store → Saleor/PostgreSQL, not a rewrite of today's local span grammar.

### Architecture

The claim of the day is the series' claim at a new layer: **the system can now explain a request as a causal structure instead of a pile of receipts.** It does so without pretending observability is evaluation: operational trace owns identity, time, status, model lineage, and cost references; retrieval/agent traces own quality evidence; the ledger owns money. The architecture is deliberately local and dependency-free because Rule 6 still holds — no distributed system is introduced before the single-process need is real. Scoped context carries one root through nested calls; spans represent operations; events represent facts; explicit prompt refs preserve intended and exact identity; metadata-safe records keep inspection from becoming a privacy hole; O9 keeps observation from becoming intervention.

The existing seams gain meaning rather than replacement. The gateway's retry/fallback becomes model lineage. The RAG trace's candidate/context split becomes document events. The agent's transcript becomes step/call/result parentage. The ledger's attempt rows become the trace's receipts. The endpoint's response gains one id by which the whole thing can be found. This is the “build before abstracting” rule applied to observability: learn the span tree and clock from first principles before asking whether OpenTelemetry, a collector, or a vendor backend is the right future adapter.

### Questions

- A bounded in-memory store is enough for local inspection, but what retention and sampling policy should production use when traces are too numerous or too sensitive to keep in full — and should sampling happen at root admission, error-biased retention, or both?
- Prompt hashes prove exact content but do not prove which retrieved documents or tool results were included in the rendered context. Is a per-model-call input digest enough for useful provenance, or does exact replay require a separately governed fixture store rather than a larger trace?
- With parallel model/tool operations, root wall-clock duration and per-span duration are easy, but “self time” and “critical path” are harder. Which derived metric is worth the complexity, and which should remain intentionally absent?
- The ledger prices unknown models at zero by design. Should observability expose that as a warning, an error, or a separate `price_status` metric, especially when a production dashboard might otherwise interpret `$0` as free inference?
- Metadata-safe traces reduce privacy and storage risk but can hide the exact text needed to diagnose semantic prompt problems. What narrow, local-only raw-capture workflow is justified without making the default trace unsafe?
- A local trace id is not a distributed correlation contract. When Day 24 propagates context across API, agent, retrieval, and Saleor boundaries, should the schema version change, or can the same id/parent grammar remain backward-compatible?

### Tomorrow

Day 24 — Distributed AI Observability: the local tree crosses the component boundary. The curriculum turns **cross-component execution** into the chain **User → AI API → Agent → RAG → Vector DB → Saleor API → PostgreSQL → Response**, with **correlation IDs, distributed traces, structured logging, and metrics**, and the deliverable **end-to-end request trace**. Day 23 makes one in-process request explainable; Day 24 makes one request remain explainable when the work crosses services, stores, and external systems. The questions change from “which span inside this process?” to “which component continued this trace, what context crossed the wire, and where did the topology become incomplete?” The local schema is the grammar; correlation propagation is the next mechanism; production exporters and durable retention remain later architecture concerns. **The black box has a tree, a clock, and a bill; tomorrow the tree learns to cross doors.**
