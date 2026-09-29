# Day 23 — AI Observability

## Objective

Twenty-two days built a machine that reads (Days 6–10), acts (11–12), survives failure (13), computes (14–15), plans (16), remembers (17–18), decides its own architecture (19), and learns how it will be judged (20–22). Days 20–22 put the RAG pipeline and the agent on two benches, but a verdict still arrives through a hole in the evidence: **what happened inside the request that produced it?** The curriculum now says its shortest objective yet: **"Understand what happens inside an AI request."** Its required chain is explicit — **Request → Model → Retrieval → Documents → Tool calls → Tool results → Final answer** — and its required fields are **latency, tokens, cost, errors, model, prompt version**. Its deliverable is **traceable AI requests.** The repo already owns pieces of that record in separate places: `ai_os/trace.py` carries retrieval ranks, context decisions, and per-step tool evidence; `ai_os/ledger.py` records one row per provider attempt with model, tokens, cost, latency, success, and error; `ModelGateway` knows retries, fallback, and actual provider; `run_rag` separates retrieval from generation; and `run_agent` returns the transcript, steps, termination, rejections, refusals, evidence, and cumulative cost. What none of those records owns is **causality**: no single request identity, no parent-child timing tree, no provider-attempt lineage, no prompt-version identity, and no sanitized trace that joins the RAG half to the agent half. The day is therefore not "add timing fields." The fields mostly exist. The genuine build is the **join** — one trace that says which request caused which call, which attempt served it, which documents entered context, which tools ran, where the time went, what the prompt version was, and which ledger rows paid for it.

Day 23's first principle is the oldest software truth the AI layer has been circling since Day 4: **an answer is not observable merely because its components log something; an answer is observable when every consequential step can be connected, timed, attributed, and inspected without guessing.** The existing logs are a pile of receipts: the ledger knows a model attempt happened, the RAG trace knows evidence was retrieved, the agent trace knows tools were chosen, and the endpoint knows a response left — but none can prove they belong to the same request. Day 23 mints a request identity at the boundary and gives every child a **span** with a parent, a kind, a start/end on a monotonic clock, a status, and safe attributes. The span tree is the causal record; events carry the discrete facts (document retrieved, tool proposed, tool rejected, prompt used); the ledger remains the single source of money; the existing RAG/agent traces remain the source of evaluation evidence. Day 23 does not collapse those records into one giant object. It links them, because operational time, quality evidence, and money are related but not the same question.

The day's second principle is **trace before, but do not distort, the run.** A tracer that adds a model call, rewrites a prompt, changes a retry policy, or makes the answer path wait on a network exporter is an observer that has joined the experiment. **O9** is therefore the control the series has used since the evaluation court: tracing off and tracing on must leave the same final answer, the same gateway attempt sequence, the same ledger economics, and every existing fixture unchanged; the only additive outputs are the trace and its id. Third, metadata is the default, not the fallback. A useful trace needs model, prompt version, document ids, tool names, evidence ids, sizes, hashes, timings, costs, and error kinds — not the user's credentials, provider headers, full retrieved text, raw tool payloads, or private customer records. Raw capture may exist only as an explicit, bounded diagnostic mode and still must redact secrets. The result is traceable in the honest sense: a sanitized trace can reconstruct **what happened, where, under which model and prompt, and at what expense**. It does not pretend to be a byte-for-byte replay of hidden data. Day 24 can then extend the idea across processes with correlation IDs and distributed traces; Day 23 earns the right to call one in-process AI request traceable first.

## Duties Outline

### 1. Learn: The Loaded Objective — "Understand What Happens Inside an AI Request" (45 min)

- [ ] Parse **"understand what happens inside an AI request"** as a causal requirement, not a logging requirement: a timestamp beside a chat does not explain a request; a trace must let a reader move from the final answer back through the model, retrieval, documents, tools, and results that caused it, then forward again to the error, latency, or bill that followed.
- [ ] Inventory the evidence already present before declaring a new subsystem: **latency** exists at provider-attempt level in `CallRecord.latency_ms` and at agent-run level in the Day-13 trace; **tokens and cost** exist in every ledger row; **errors** exist per attempt and in `CallRecord.error`; **model** exists as requested and actual model/provider; **documents** exist in retrieval traces and tool evidence; **tool calls/results** exist in `run_agent`'s steps. The missing field is not value coverage. It is **identity plus parentage**.
- [ ] Pre-commit the day's split, inherited from Day 7 and sharpened by Days 20–22: **trace mechanics are `MECH`** — identity, parent links, monotonic duration, status, attempt order, token/cost reconciliation, prompt refs, and redaction rules are deterministic; **meaning is not silently promoted** — a trace proves which answer was returned, not whether it was correct, whether a document supported a claim, or whether the agent chose well. Evaluation remains the benches' job; observability explains the run those benches grade.
- [ ] Pre-commit four invariants before any build: **(a) one request, one root trace** — every internal span descends from the same root; **(b) one ledger, one bill** — trace totals are read from ledger records, never independently repriced; **(c) metadata before content** — a safe trace works without raw prompts, documents, tool payloads, or answers; **(d) O9** — tracing is passive and changes no answer, retry, tool, token, or cost behavior.
- [ ] Name the negatives with owners: **cross-process correlation and distributed propagation** are Day 24's; **durable production telemetry storage and exporters** are Day 29's deployment concern; **sampling and retention policy for production** remain a named boundary; **security policy for tool permissions** is Days 25–26; **quality verdicts** stay Days 20–22. Day 23 builds the smallest honest local trace, not a prematurely distributed observability platform.

### 2. Learn: Trace, Span, Event — The Unit Is Causality (45 min)

- [ ] Learn the vocabulary precisely: a **trace** is one root request; a **span** is a timed operation with `trace_id`, `span_id`, `parent_span_id`, `kind`, `name`, start, end, duration, status, safe attributes, and optional error; an **event** is a timestamped fact inside a span (document selected, tool proposed, provider fallback occurred). Logs explain a line; spans explain a hierarchy.
- [ ] Learn why the clock is `time.monotonic_ns()` and wall time is metadata: duration must not move when the host clock changes, NTP-corrects, or crosses a timezone. Keep a UTC wall timestamp for when the request began, but derive every duration from the monotonic clock; serialize durations, not the process's raw counter.
- [ ] Learn parentage over repetition: a model attempt belongs to a generation span; generation and retrieval belong to the request root; a tool call belongs to its agent step; a tool result belongs to the call. A retry is a new attempt span under the same logical model-call span, not a second request and not a sibling with no story. Parallel tool calls are siblings; their durations may overlap.
- [ ] Learn that **nested duration is not additive**. A model call contains HTTP time and retry time; an agent step contains model choice plus tool execution; the root contains all of them. Never report `sum(span.duration_ms)` as request latency. Report the root's wall-clock duration, each span's own duration, and a critical-path or self-time view only when its definition is explicit.
- [ ] Learn the status vocabulary as a small contract: `ok`, `error`, `timeout`, `cancelled`; spans also record an error taxonomy (`provider_error`, `rate_limit`, `invalid_key`, `bad_request`, `dimension_mismatch`, `tool_rejection`, `tool_error`, `budget_exceeded`) without making prose parsing the primary classifier. A refusal is a successful observation with `refused=true`, not a process error.

### 3. Learn: Model Identity, Prompt Version, Money, and Failure (45 min)

- [ ] Learn **model identity as a chain, not a label**: requested model, provider attempted, actual returned model, attempt number, fallback depth, retry reason, finish reason, and whether the result succeeded. A trace that stores only the final model loses the reason a slower or more expensive provider was used.
- [ ] Learn **prompt version as three facts**: a stable semantic id (`agent-reasoning-v1`), the hash of the exact template/rendered messages used, and the prompt role/name. The caller supplies an explicit `PromptRef`; the tracer never guesses a version from text similarity. A prompt edit without a version/hash change is a trace bug; a version label without a reproducible hash is documentation, not provenance.
- [ ] Learn **money from the ledger**: token counts and cost come from the exact `CallRecord` attached to each model/embedding attempt, including failed attempts according to the ledger's existing policy. The root total is a sum/delta over linked records, not a second pricing function. Unknown-model zero pricing remains visibly `unknown_price` rather than silently becoming truthful `$0`.
- [ ] Learn **errors as causes, not final strings**: retain error kind, provider, attempt, retryable flag, and a redacted message; keep the full exception only in a local debug representation. Final failure closes the open spans, records which ancestors completed, and shows whether a fallback, refusal, degradation, or guardrail stop actually happened.
- [ ] Learn the privacy boundary: safe-by-default attributes are ids, names, enums, booleans, counts, byte/character sizes, hashes, ranks, scores, model names, and timings. Raw user text, prompt bodies, document text, tool arguments/data, final answers, authorization headers, API keys, and provider response bodies are excluded by default and size-capped in any explicit raw-capture mode.

### 4. Learn: RAG and Agent Events — The Two Existing Traces Become Causal (45 min)

- [ ] Learn the RAG path as spans plus document events: root request → `model.embed` attempt → `retrieval` (query hash, store/corpus, k, budget, candidate count, gate outcome) → document events (doc/chunk id, rank, score, heading/symbol, kept/dropped reason, content size/hash) → `model.chat` attempt → final-answer event (refused, citation count, answer hash/size). The existing Day-7 trace remains the quality record; Day 23 links its document ids into the causal tree.
- [ ] Learn the agent path as the same grammar: root request → step span → `model.chat_with_tools` attempt → tool-call event (name, safe argument shape, validation result) → tool-result event (`ok`, `refused`, rejection/error kind, evidence/data counts, latency) → next step → final-answer event (termination reason, steps, citations, fabrication flags). No tool call may be "observed" after the run by scanning logs; the tracer must know the call at the seam that validates it.
- [ ] Learn that the final answer is an **event**, not necessarily stored content. A trace can prove that answer `sha256:…` of length N was returned with citations C and termination T. Exact answer replay is a separate, privacy-sensitive capability and must not be implied by a metadata trace.
- [ ] Learn where the current seams are insufficient: `run_rag` computes cost blocks after the call and has no prompt ref; `run_agent` aggregates ledger deltas but not attempt lineage; `trace.py` contains evaluation fields but no common request tree; `server.py` returns provider data without trace identity. Day 23 extends these seams; it does not invent a parallel RAG or agent implementation.

### 5. Design: The Trace Contract — One Root, Nested Spans, Safe Events (30 min)

- [ ] Design the canonical record before touching callers:
  ```text
      TRACE (schema_version, trace_id, operation, started_at, ended_at, duration_ms,
             status, safe_attributes, totals)
         │
         ├── request event: route · input hash/size · config snapshot ids
         │
         ├── model.embed attempt span(s)
         ├── retrieval span
         │     └── document events: id · rank · score · kept/dropped · size/hash
         │
         ├── generation / agent-step span
         │     ├── model.chat attempt span(s)
         │     ├── tool-call event
         │     └── tool-result event
         │
         └── final-answer event: status · hash/size · citations · termination
  ```
- [ ] Design decision #1 — **operational trace and evaluation trace stay separate but correlated**: `observability.py` owns time, status, identity, and cost lineage; `trace.py` keeps recall/MRR/context/grounding/agent-step evaluation. Shared ids join them. A large single trace with every benchmark cell and every production latency would serve two masters and leak operational fields into evaluation.
- [ ] Design decision #2 — **the tracer wraps boundaries, it does not re-run them**: gateway attempts, retrieval calls, tool validation/execution, and answer completion are instrumented in place. A tracer that re-executes chat, retrieval, or tools to measure them is a second production run, exactly the error the evaluation benches forbade.
- [ ] Design decision #3 — **ledger records are the billing truth**: every model span stores the exact record index/id and the aggregate reads the linked rows. Traces never invent token counts, attach cost to a successful logical call while dropping failed retries, or sum nested spans as if their time were independent.
- [ ] Design decision #4 — **metadata-safe is the product**: the default trace must answer “which request, which model, which prompt version, which evidence, which tool, which error, how long, how much?” without raw payload capture. Raw diagnostic capture is explicit, bounded, redacted, and never required for tests O1–O9.
- [ ] Design decision #5 — **local before distributed**: the trace id is generated at the current process boundary and copied through in-process context. No queue, worker, database trace store, OpenTelemetry SDK, or cross-service propagation contract is introduced today; Day 24 owns those. A bounded in-memory store is enough to make `/traces/{trace_id}` inspectable in the local application.

### 6. Build: The Trace Core and Prompt References (1 hour)

- [ ] Build `ai_os/observability.py` with a JSON-serializable schema and first-principles objects: `TraceContext` / `TraceRecorder`, `Span`, safe event recording, monotonic timing, error/status classification, trace totals, and a bounded thread-safe `TraceStore` whose eviction policy and maximum size are explicit.
- [ ] Build explicit `PromptRef(name, version, sha256)` support. Register the current RAG system prompt, agent reasoning prompt, and termination prompts with stable semantic versions; hash the exact template (and, where useful, the rendered-message digest) at call time. Do not infer prompt identity from the first words of a string.
- [ ] Extend `ai_os/ledger.py` minimally so a `CallRecord` can carry `trace_id`, `span_id`, `operation` (`chat`/`embed`/`stream`), attempt number, and prompt reference when available. Preserve every existing summary and the append-only semantics; a trace id adds provenance, not accounting policy.
- [ ] Implement invariant validation: every child has a live parent, an ended span cannot accept events, the root closes exactly once, statuses are from the fixed vocabulary, and serialization fails visibly on unknown non-JSON attributes rather than converting an observability bug into a successful trace.
- [ ] Keep the core dependency-free and framework-free. The repository's policy is mechanism first; OpenTelemetry may be compared later, but Day 23's trace must be understandable without a tracing SDK.

### 7. Build: Instrument the Existing Seams — Gateway, RAG, Agent, Server (1 hour 20 min)

- [ ] Instrument `ModelGateway.chat`, `chat_with_tools`, `stream`, and `embed` at the **attempt** boundary. Wrap each provider call in a child span; link its `CallRecord`; record requested/actual model, provider, attempt/fallback depth, latency, tokens, cost, retryability, finish reason, and safe error facts. Streaming records time-to-first-event and total stream duration without making the tracer buffer the stream.
- [ ] Instrument `run_rag` around the existing pipeline without changing its decisions: request root → embedding/retrieval → document events → generation → final answer. Pass an explicit RAG prompt ref into the generation span; keep Day-7's `retrieve_trace` / `run_trace` evidence available under the same trace id.
- [ ] Instrument `run_agent` at the loop seams: each step, model/tool turn, validation, execution, result, forced final turn, and final grounding/termination. Parallel calls are sibling spans; deferred/capped calls are events, never fake executions; rejection/refusal/error remains the Day-11 contract; no new model or tool call may be introduced.
- [ ] Add a safe trace context propagation mechanism (explicit context plus scoped context variable, not a mutable global singleton) so nested gateway calls attach to the active request while standalone calls still receive their own root.
- [ ] Add trace identity to existing function results (`trace_id`) and HTTP responses from `/chat`, `/rag`, and `/chat/stream`. Add `GET /traces/{trace_id}` returning the sanitized trace, plus a small trace runner for direct `run_agent` calls that do not yet have an HTTP route. Do not build a durable store or exporter in this step.

### 8. Test: The O-Fixtures (O1…O9) and the Control (1 hour)

- [ ] Pre-register trace fixtures with expectations written before implementation:
    - **O1 root spine**: one `/chat`, `/rag`, or direct-agent request creates one root; every span/event has the same trace id and a valid parent path; root duration uses the monotonic clock and is not the sum of nested durations.
    - **O2 RAG spine**: request → embed attempt → retrieval → document events (kept and dropped) → chat attempt → final answer; query/body text is absent by default, while ids, counts, ranks, sizes, and hashes are present.
    - **O3 agent spine**: every proposed call becomes a tool-call event, every validation/execution becomes a tool-result event under the correct step; parallel calls are siblings; deferred/capped calls are recorded as not executed; termination and grounding fields reach the final event.
    - **O4 model lineage**: primary success needs one attempt; retry creates sibling attempts under one logical model call; fallback names each provider/model and the actual successful model; finish reason and error taxonomy survive.
    - **O5 money reconciliation**: trace token totals and cost equal the exact linked ledger delta for the request, including all attempts under current ledger policy; an unknown price is flagged, not presented as proven free.
    - **O6 failure closure**: rate limit, timeout, invalid key, bad request, dimension mismatch, tool rejection/error, and budget stop close the right spans, preserve ancestors that completed, and never leave an “ok” open span.
    - **O7 prompt provenance**: RAG, reasoning, and termination calls carry explicit prompt name/version/hash; changing the version or exact template changes the corresponding provenance; no prompt body is exposed by default.
    - **O8 inspection + redaction**: trace lookup returns the complete sanitized tree; unknown ids fail cleanly; bounded retention evicts oldest traces; raw/debug capture is size-capped and never includes API keys, authorization headers, or full provider payloads; streaming exposes `trace_id` and TTFT without changing chunk bytes.
    - **O9 control**: tracing installed but unused changes no answer, provider/attempt sequence, tool result, token/cost ledger, or existing fixture; trace collection off is byte-equivalent on all observable application outputs except the absent trace id.
  - [ ] Run the existing gateway, RAG, tool, agent, and evaluation regressions with tracing off and on. Any changed answer or cost is an instrumentation bug, never a reason to move a baseline.
  - [ ] Split the result honestly: **O1–O9's identities, timing relationships, arithmetic, prompt hashes, lookup, and redaction are `MECH`**. Whether a trace is operationally useful and whether its selected attributes balance privacy/debuggability is a design judgment; raw-payload capture remains opt-in, and production retention/export stays `PENDING` rather than being declared solved.

### 9. Measure + Documentation (45 min + 20 min)

- [ ] Rule-4 as the trace report: one request row (`trace_id`, operation, model path, prompt versions, steps/tools/documents, tokens, cost, latency, errors, status, retained?) plus one span tree per hostile scenario. The headline is not “it logs”; it is **where the request's time and money went, with every amount reconciled to the ledger**.
- [ ] Compare at least four paths: successful `/rag`, successful agent investigation, retry→fallback model call, and failed/hung run. Record the trace tree and safe error closure; do not paste raw user content into the report.
- [ ] Verify the diagnostic questions the day was built for: Which provider actually answered? Which prompt version made each model turn? Which documents were shown versus merely retrieved? Which tool was rejected, and what followed it? Which attempt paid the bill? Which span owns the latency? A trace that cannot answer these from safe attributes has not met the deliverable.
- [ ] Record cost shape: adding tracing costs **0 chats and 0 embeds**; model/embedding usage and ledger cost remain identical to the untraced control. Instrumentation has local CPU/storage overhead, which is measured and reported, never hidden inside model cost.
- [ ] Write `docs/concepts/observability.md`: trace vs span vs event, parentage, monotonic timing, ledger reconciliation, prompt refs, privacy defaults, and the boundary between observability, evaluation, Day 24 distributed tracing, and production telemetry.
- [ ] Write `docs/evaluations/trace-day23.md`: the O1–O9 fixture table, four trace trees, latency/cost reconciliation, the O9 control, retention/redaction results, and the honest `PENDING` lines for durable storage/export and cross-process correlation.
- [ ] Fill the Day 23 Daily Learning Record (Problem / Concept / Understanding / … / Tomorrow), and update the implementation rollup honestly: trace mechanics may be `DONE` under the stub; raw capture, production retention, distributed export, and provider-specific full fidelity remain `PARTIAL`/`PENDING`.

## Deliverable

The **traceable AI request** — one sanitized causal record joining the request, model attempts, retrieval, documents, tools, results, final answer, timing, tokens, cost, errors, and prompt provenance:

```text
       REQUEST (trace_id · operation · input hash/size · config ids)
          │
       ├── MODEL.EMBED
       │     └── embed attempt (model · tokens · cost · latency)
       │
       ├── RETRIEVAL
       │     └── documents (id · rank · score · kept/dropped · size/hash)
          │
          ├── GENERATION / AGENT STEPS
          │     ├── chat attempt (provider · model · retry/fallback · prompt version/hash)
          │     ├── tool call (name · safe args shape · validation)
          │     └── tool result (ok/refused/error · evidence ids/counts · latency)
          │
          └── FINAL ANSWER (status · hash/size · citations · termination)

       TOTALS: latency from the root span · tokens/cost from linked ledger records
       ERRORS: typed, attempt-owned, redacted, and closed
       LOOKUP: GET /traces/{trace_id} · bounded local retention
```

Days 7 and 12 made individual RAG and agent runs auditable; Day 4 made every provider attempt costed; Days 20–22 made quality measurable. Day 23 joins those records without confusing their jobs. It does not declare an answer correct, repair a provider, or build a distributed telemetry platform. It gives one request one identity, one tree, one prompt vocabulary, one ledger truth, and one safe inspection surface — so when latency rises, a model changes, a tool rejects, a document is dropped, or a bill surprises us, the system can answer **where**, **why**, and **at what expense** instead of asking us to reconstruct a story from disconnected logs. Day 24 can then carry that identity across components; Days 25–29 can attack, constrain, approve, productionize, and deploy it. **The black box now has a tree, a clock, and a bill.**

## Exit Check — answer in your own words

- What is the difference between a line that says a model was called and a span that makes that call causally inspectable?
- Why does one request need one root trace, and what breaks when a nested component mints a new id for itself?
- Why must duration use a monotonic clock while the record also keeps a wall timestamp?
- Why is the root request latency not the sum of every span's duration?
- How do requested model, attempted provider, actual model, retry depth, and fallback depth answer different questions?
- Why does a prompt need both a stable semantic version and an exact-content hash?
- Why must token and cost totals come from linked ledger records instead of a second pricing calculation?
- What is a document event, a tool-call event, and a tool-result event, and where must each be created?
- What does the metadata-safe default include, what does it exclude, and why is exact replay a different claim from traceability?
- What does O9 forbid, and why is an instrumentation call to the model a second production run rather than a measurement?
