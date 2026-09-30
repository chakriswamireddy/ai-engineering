# Day 24 Notes — Distributed AI Observability

> Notes answering `docs/day-plan/day-24.md`.
> Twenty-three days built a machine that reads (Days 6–10), acts (11–12), fails honestly (13), computes (14–15), plans (16), remembers (17–18), decides its own architecture (19), learns to be judged (20–22), and explains one request from the inside (23). Day 23 gave a single in-process request one identity, one span tree, one monotonic clock, one prompt vocabulary, and one ledger-reconciled bill. The curriculum now turns the camera outward: **"Understand cross-component execution,"** with the required chain **User → AI API → Agent → RAG → Vector DB → Saleor API → PostgreSQL → Response**, the four things to learn (**correlation IDs, distributed traces, structured logging, metrics**), and the deliverable **end-to-end request trace.** The repository is honest about where each hop lives: `ai_os/server.py` is a real inbound FastAPI edge; `ModelGateway` makes real outbound HTTPS calls to OpenAI/Anthropic/Cloudflare; `RagStore` is **a local JSON file, not a network service**; `ai_os/data.py` reaches Postgres through `psycopg` on a read-only DSN with a server-side `statement_timeout`; the Saleor API is reachable as GraphQL from the same process. So Day 24's "distributed" is a claim about **five boundaries** — inbound HTTP, three outbound clients, one component boundary where work genuinely leaves the process, and the response — not about five microservices. What Day 23 structurally cannot do: a span tree is only as wide as the process that built it, `trace_id` is a Python value that dies at the socket, and provider internals, Saleor resolvers, and Postgres query plans are invisible because we do not own them. The genuine build is **propagation and honest incompleteness**: a wire format both ends agree on, continuation instead of a new root, client-side spans for calls we initiate, explicit gaps for hops we cannot see, an assembly step that tolerates out-of-order/duplicated/lost reports, and two derived views that share the identity without pretending to be the trace.
> The single idea: **a request is only end-to-end observable when its identity survives every boundary it crosses, when every hop we initiate is measured honestly, and when every hop we cannot see is declared — so Day 24 adds W3C trace-context propagation, span kinds with remote parents, a loss-aware assembler, structured logs, and bounded metrics, while keeping Day 23's grammar, ledger truth, and metadata-safe default intact.** Mechanism stays `MECH`: parse/format determinism, parent linking, idempotent merge, gap detection, count/latency/cost arithmetic, and log validity. Meaning does not sneak in: a 412 ms Postgres span says we waited 412 ms, not that the plan was good; a complete trace says we saw everything, not that the answer was right. Three views, one identity — **logs** answer *what happened at this instant*, **traces** answer *what happened to this request*, **metrics** answer *how many and how slow* — and none of them substitutes for another. Gaps are data: a missing span, a late report, and a duplicate are three distinct, named states, and a gap is never rendered as a fast success. Telemetry is **fail-open** (a dead collector must not fail a request) and **fail-safe** (untrusted inbound headers are validated, never obeyed). And D9 keeps the control: propagation, logs, and metrics on or off, the answer, the SQL, the tool results, the attempt sequence, and the ledger are identical; only headers and additive fields differ. Day 23 earned "traceable"; Day 24 earns **"end-to-end"** — with an honest list of what is still outside the trace.
>
> **The one line the day would die defending: a distributed trace is not the union of everyone's logs — it is one identity that crosses every boundary, an honest measurement of each hop we own, and an explicit, named gap for every hop we do not.**

---

## 1. The Loaded Question — "Understand Cross-Component Execution"

Day 24's objective is one clause long, and the shortness is the trap. **Understand cross-component execution** does not mean "install OpenTelemetry." It means: for one request, know what happened at each place where the work left this process's control, know what each of those places cost, and know what we were never able to see.

A **component boundary** is any point where work leaves our control. In this repository today that is:

| Hop | Reality in the repo | What we can observe from here |
|---|---|---|
| User → AI API | real inbound edge: `server.py` FastAPI `/chat`, `/rag`, `/chat/stream` | headers received, route, request size/hash, our handling time, status, response shape |
| AI API → Agent | **in-process** (`agent.py`) | everything: steps, model turns, tool calls, results, termination |
| Agent → Provider | real outbound HTTPS in `gateway.py` (openai/anthropic/cloudflare) | requested/actual model, attempt, fallback depth, latency, tokens, cost, status, error kind, finish reason |
| RAG → Vector DB | **local JSON file** (`RagStore`) — no network service | store version, k, budget, candidate counts, ranks, scores; a *remote* vector store is `PENDING` |
| Agent → Saleor API | GraphQL, outbound client | operation name, latency, status, error class, returned field count — not resolver internals |
| Saleor API → PostgreSQL | `psycopg` on a read-only DSN, server-side `statement_timeout` | statement hash, allow-list/policy verdict, row count, truncation, timeout, duration — not plan/index/lock detail |
| → Response | JSON body or SSE stream | our close time, TTFT, `trace_id` in body and header; **not** the user's DNS/TLS/queue time |

The temptation is to read the curriculum chain and pretend `RagStore` is a vector database service, or that a client span proves what Postgres did internally. Both would be the Day-7 mistake in a new costume: a record that looks complete because it was padded, rather than incomplete because reality is.

What Day 23 already handed over: trace/span/event, monotonic timing, a fixed status and error taxonomy, prompt refs, ledger-linked cost totals, a bounded local store, and `GET /traces/{trace_id}`. All of it is **process-local**. What is missing is everything about *crossing*: a wire format, a continuation rule, span kinds, a remote flag, a report and assembly path, a representation for loss, structured logs, and metrics.

Six invariants keep the crossing honest:

1. **Continue, don't restart.** A valid inbound correlation id is adopted, not replaced.
2. **Every outbound call carries context.** An uninjected outbound hop is a silently severed trace.
3. **Gaps are visible.** An expected-but-absent span is a recorded gap with a named component — never an implied success.
4. **Fail open.** Telemetry failure must not change the answer, the SQL, the retry, or the cost.
5. **Metadata-safe does not relax.** Cross-process is not an exemption from redaction, and untrusted inbound headers are validated, never obeyed.
6. **D9: tracing is passive.** On/off is behaviorally and financially identical except for additive fields and headers.

The scope boundary matters as much. A production collector, log shipping, a metrics backend, real sampling, and scrape-based alerting are deployment concerns. Instrumenting the inside of Postgres, Saleor, or a provider is impossible for us and stays a labeled gap. Authenticating an ingest endpoint is Day 26. Client-side spans in a user's browser need a client we do not have. Day 24 builds propagation and honest assembly — the smallest mechanism that can make a request explainable *across* components.

---

## 2. Correlation IDs and Context Propagation — The Wire Format

The vocabulary gets blurred constantly, so pin it down.

A **correlation id** is any identifier that stitches records together — a request id, a job id, a session id. It is a joining concern, and different systems may have several.

A **trace id** names **one distributed trace**. It must be globally unique, fixed-size, and never reused, because it is the key every component uses to agree they are talking about the same request.

A **span id** names one operation within that trace, and the *parent* relationship is what makes it a tree rather than a bag.

One request has one trace id, many spans, and potentially many correlation ids layered on top (a UI request id, a queue job id) that a trace can link without being confused by.

The **wire format** matters because it is a contract, not a convenience. W3C Trace Context is deliberately boring and deliberately strict:

```text
version   = 2 hex chars   (00 = current)
trace_id  = 32 hex chars  (16 bytes) — not all zero
parent_id = 16 hex chars  (8 bytes)  — not all zero
flags     = 2 hex chars   (bit 0 = sampled)
→ 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
```

Validation is part of the format, not a nicety: wrong length, non-hex (uppercase is illegal), all-zero ids, or an unknown version must be handled explicitly. A future version should be parsed leniently and preserved rather than rejected outright — rejecting a valid future header turns a compatibility question into an outage.

**Sampling is a propagated decision.** The sampled bit travels in the context so downstream components do not each roll their own dice; a component that receives an unsampled context does not emit sampled spans. If each hop sampled independently, a slow trace would be missing exactly the hop that mattered. Day 24 keeps the bit honest and the policy trivial (sample everything locally, one explicit knob) because a real head sampler needs a cost model the day does not have — but the *mechanism* must be right now so a policy can be added later without changing the wire.

**Baggage** is where distributed tracing leaks data, so treat it as untrusted, bounded, non-secret, and non-authoritative. A tiny allow-list (say `tenant`, `surface`, `request_kind`) with a hard size cap may ride along. It must never carry API keys, user PII, SQL text, prompts, or unbounded values. And it must never be used to **select** anything: a forged inbound `tenant` must not choose a store, an index, a connection, or a permission. Baggage is metadata to propagate, not authority to obey.

Two directions, both easy to get half-right:

- **Extract** on inbound: parse and validate, then decide continuation versus fresh root, and record *why* you decided.
- **Inject** on outbound: write the current context into the headers of the provider call, the Saleor GraphQL call, the Postgres wrapper's client-info, and the worker RPC.

Forgetting to inject is the single most common way a distributed trace quietly becomes two disconnected local traces that each look perfectly healthy. If a transport offers no header hook — a third-party SDK owning its own client, a driver that cannot set client-info — the honest move is to **record the gap**, not to claim propagation.

---

## 3. Distributed Traces — Remote Parents, Span Kinds, and the Topology You Do Not Own

**Span kinds** are an honest statement about who did the work:

- `server` — we handled an inbound request;
- `client` — we called something else;
- `producer` / `consumer` — a message or queue boundary;
- `internal` — local computation with no boundary crossed (today: the `RagStore` file read, reranking, budget logic).

A chain where every span claims `server` is a chain that never left the building. Conversely, marking today's local JSON store read as `client` would be inflation: no request left the process.

**Remote-ness is a flag, not a second schema.** A child created from a remote parent's context keeps its own fresh `span_id`, records the caller's span as `parent_span_id`, and marks the edge remote. Remote and local children then assemble identically. The flag exists so nobody later reads a span and assumes we measured the inside of an external system.

**The double-count trap.** For one provider call there are two truthful spans: our `client` span (what we waited) and, if the far side is instrumented, its `server` span (what it did). Both are real; adding them counts one interaction twice. The end-to-end number is the **critical path** — the root's own wall-clock duration, with per-hop attribution beside it — never the sum of hop durations. This is Day 23's "nested duration is not additive" returning one level up, where the temptation is stronger because the hops *look* sequential.

**Four states of a span report.** Telemetry is a lossy channel, and the record must model that:

- **delivered** — arrived, assembled, normal;
- **late** — arrived after its parent closed or after the trace was read once already;
- **duplicate** — the same `span_id` re-sent;
- **missing** — expected by the topology, never arrived.

Assembly must be idempotent on `span_id`, order-independent, and must represent a missing span as a first-class record naming the component and why we expected it. A trace that silently omits a dead worker's span is not a shorter trace; it is a false one.

**What client-side instrumentation can and cannot see** for the three real external hops:

- **Provider:** requested and actual model, attempt number, fallback depth, latency, tokens, cost, status, error kind, finish reason. **Not** queue time, internal retries, or safety filtering.
- **Saleor GraphQL:** operation name, latency, status, error class, returned field count. **Not** resolver-level timing or N+1 behavior.
- **PostgreSQL:** statement hash, allow-list/policy verdict, row count, truncation flag, `statement_timeout`, total latency. **Not** the plan, the indexes used, lock waits, or what the server did with the connection.

Saying "the database took 412 ms" when we measured our own socket wait is precisely the overclaim to avoid. The correct phrasing is a `client` span named `postgres.query` that says we waited 412 ms for a statement we hashed, and a note that the server's internals are unobserved.

**The end-to-end number is bounded by the trace.** Root span duration is server-side. The user's stopwatch also contains DNS, TLS, proxy, and queueing before our first byte and after our last — and, for streaming, time-to-first-byte plus the stream itself. So the honest report shows: server-side root duration, first-hop and last-hop spans, TTFT for streams, and a named remainder outside the trace. Claiming the root duration as "the user experienced 412 ms" is a small lie that becomes a large one the first time someone debugs a slow request on a bad network.

---

## 4. Structured Logging and Metrics — Three Views, One Identity

Three questions, three views, one identity. Collapsing them is the mistake.

**Logs** answer *what happened at this instant, in this order, with these fields*. They are the right home for rare, high-cardinality, human-readable detail, and they must be greppable. **Traces** answer *what happened to this one request, causally*, and they are where you go for a single slow or failing case. **Metrics** answer *how many, how slow, how expensive, how often broken* across a window — and by construction **cannot name an instance**. A log line with no `trace_id` is a receipt. A metric labeled with `trace_id` is a cardinality bug wearing a helpful hat.

**The structured-log contract** is one JSON object per line with a fixed spine:

```text
ts · level · logger · component · event · msg
trace_id · span_id · parent_span_id · duration_ms · status · (safe attributes…)
```

No f-string prose that a machine must later parse; free text belongs in `msg` with the structured facts beside it. Ids bind from the *same* Day-23 context variable that binds spans, so log lines land in the tree without a second plumbing mechanism — one context, three views.

**Logging is not tracing, and must not double-write.** Spans record an operation's boundaries; log lines record discrete facts and decisions. Emitting both for one fact is fine only when they serve different readers. And the redaction rule is absolute in both views: no authorization headers, API keys, SQL text, row contents, prompts, document text, or customer records. The existing `ai_os.gateway` logger is the first migration — it is the perfect before/after, because it currently emits prose with no ids at all.

**The metrics model needs no vendor.** Counters (requests, errors, tokens, cost, tool calls, SQL executions by verdict) and histograms with **fixed, declared buckets** for latency (root duration, per hop, TTFT), labeled from a small bounded set: component, operation, model, provider, status, error kind, corpus. Two rules make this trustworthy: metrics increments happen at **span close**, so traces and metrics cannot disagree about the same window; and the label allow-list is *code*, so adding a high-cardinality label is a visible diff rather than an accident.

**The traps worth naming:**

- never label by `trace_id`, user, session, question text, document id, SQL string, or error message;
- buckets must be enumerable in the source, or nobody can reason about the histogram later;
- the registry needs a deterministic reset so fixtures are reproducible;
- and the honest line belongs in the report: **metrics cannot answer "which request was slow."** They can tell you it was 3% of requests — and then the trace is where you go.

`GET /metrics` (a JSON snapshot, with a documented Prometheus-text option) plus a bounded local ring for logs and traces is a **local development surface**, not a production metrics stack. Durable shipping, a real collector, and scrape-based alerting are deployment concerns, and saying otherwise would be the Day-24 version of calling an in-memory trace store "durable telemetry."

---

## 5. Design: The Cross-Component Contract

```text
  INBOUND                                  OUTBOUND
  headers ─▶ extract()                     inject(headers) ─▶ provider / Saleor / worker
             │ validate hex, version, zeros   │
             ▼                                 ▼
       adopt trace_id                   child span kind=client, parent=current
       new span_id, parent=remote          │
       kind=server, remote edge            │  work happens — possibly unseen
             ▼                              ▼
       assemble(tree) ◀── span report ──◀ ingest(span)   [out-of-order | late | duplicate | missing]
             │
             ├── structured log lines (same ids, same redaction)
             └── metric increments (from span close, bounded labels)
```

Six decisions carry the design.

**Decision 1 — one grammar, two scopes.** Day 24 reuses Day 23's `Trace`/`Span`/event schema and adds only what a boundary requires: `kind`, `remote`, an optional `remote_endpoint`, and gap records. No second trace type, no second store, no schema fork. A `schema_version` bump is expected and honest; a parallel model is not. If a vendor SDK is adopted later, it must satisfy this schema and these fixtures, not the other way round.

**Decision 2 — continuation, with a recorded reason.** Valid inbound context is adopted. Absent, malformed, all-zero, refused, or unknown-version context mints a fresh root **and records why** (`no_header`, `malformed`, `all_zero`, `version_unsupported`, `rejected_untrusted`). A forged header must never let a caller attach spans to someone else's trace, and must never influence which store, tenant, index, or permission is used. A bad header is not a reason to fail a legitimate request — it is a reason to start a new, clearly-labeled trace.

**Decision 3 — assembly is idempotent, order-free, and loss-aware.** Merge on `(trace_id, span_id)`; tolerate a child before its parent; count duplicates instead of double-counting durations; mark late arrivals; materialize `missing` entries for spans the topology expected. The trace exposes `complete`, `span_count`, `gap_count`, and `components_seen`, and `complete: true` is only true when every declared child actually arrived.

**Decision 4 — the far side we do not own is a labeled gap, not a span we fake.** Client spans describe *our* wait, status, and result shape; remote internals are described as unobserved. The single exception is a component we genuinely run and deploy — a worker process we instrument end to end — which is marked as ours so the distinction stays meaningful.

**Decision 5 — fail-open telemetry.** Report delivery, the ingest route, and metric writes are wrapped so any failure is recorded locally and swallowed. The application's own policy — the SQL allow-list, tool rejection, budget — stays fail-closed exactly as before. Observability must never become the reason a request fails; a telemetry dependency is a dependency with no user.

**Decision 6 — local before platform.** The transport is a bounded collector: an in-process submit path plus a loopback-only HTTP ingest route for the real second process in the fixtures, off by default. No broker, no vendor SDK, no OpenTelemetry dependency. The schema and the merge algorithm must survive a later move to a real backend.

---

## 6. Build: Propagation Core, Span Reporting, and the Assembler

`ai_os/observability.py` grows the crossing primitives without changing Day 23's grammar:

- `TraceContext` — parse, format, validate, `new_root`, `child`, `remote_child`, trace flags, and explicit reasons for every accept/reject decision;
- `inject` / `extract` for HTTP headers and for explicit in-process carriers;
- span kinds (`server`, `client`, `internal`, `producer`, `consumer`) and a `remote` flag;
- a `remote_endpoint` attribute that names a system (`api.openai.com`, `saleor`, `postgres`) and never a credential or a secret-bearing URL.

`SpanReport` and `TraceAssembler` do the merging. The rules are small and testable: idempotent on `span_id`, order-independent, duplicates counted, late arrivals recorded, missing children materialized from a declared topology. Serialization stays deterministic JSON, so `GET /traces/{trace_id}` — which already exists from Day 23 — serves assembled and local traces through one schema and one redaction path.

The **topology declaration** is deliberately hand-written per component: which span kinds and child names that component is expected to produce. That is what makes "the worker died" distinguishable from "the worker never had a job," and it is how a gap gets a name instead of a shrug. It is not an inferred service map.

The **collector** is bounded, thread-safe, oldest-first, and cannot raise into a caller. It exposes an in-process submit function and an optional loopback-only HTTP ingest route, off by default, with unauthenticated ingest named explicitly as a Day-26 boundary. And the core stays dependency-free: if propagation is later delegated to a vendor SDK, the schema, the validation rules, and the D-fixtures remain the contract that adapter must satisfy.

---

## 7. Build: Instrument the Edges

**Inbound (`server.py`).** Extract and validate on `/chat`, `/rag`, and `/chat/stream`; continue the trace or mint a fresh root with a recorded reason; open a `server` root; return `trace_id` in the response body **and** a response header; close the root after the response is produced, with a `response_sent` event and TTFT for streams. A malformed header produces a fresh trace and a normal response — never a 400 caused by telemetry.

**Provider (`gateway.py`).** Every attempt span becomes `kind=client`, injects the current context into the provider request, and records `remote_endpoint` as the provider host only. If a provider SDK owns its transport with no header hook, the gap is recorded explicitly instead of claiming propagation. Nothing about Day 4's retry, fallback, budget, or ledger changes.

**Saleor and Postgres (`data.py`).** `run_saleor_sql` and `describe_saleor_schema` get `client` spans carrying operation name, statement **hash** (never the SQL text), policy/allow-list verdict, row count, truncation flag, `statement_timeout`, and duration. No row contents, no connection string, no credentials. Postgres client-info carries the correlation id when the driver permits it. Day 15's two rejection classes and read-only guarantees are untouched — a rejected statement is still rejected, and now it is also visible as a closed `client` span with the rejection class attached.

**Vector store.** Today's `RagStore` is a local file, so its read is an `internal` span with store version, k, budget, and candidate counts. The seam a real network vector database will use is defined now (a `client` span with injection), and the remote store itself is marked `PENDING` until one exists. The curriculum's chain names the Vector DB; the code does not fake it.

**The genuinely crossing component.** A small worker/service process — a second FastAPI app or stdlib HTTP service — that extracts the header, continues the trace with a remote parent, does one piece of work, and reports its span back through the collector. This is the fixture that makes "distributed" real rather than notional: two processes, one trace id, one assembled tree, and one place where a kill can produce a real gap.

Nested in-process calls still attach to the active root through scoped context, and a call with no active request gets its own root rather than borrowing whichever request happened to be active on another thread.

---

## 8. Build: Structured Logging, Metrics, and the Inspection Surface

`ai_os/structured_logging.py` provides a JSON formatter with the fixed spine, a context-binding filter that pulls `trace_id`/`span_id` from the Day-23 context, a redaction filter that drops or masks forbidden keys **before** serialization, and a bounded in-memory ring with `GET /logs?trace_id=…`. The existing `ai_os.gateway` logger migrates first, because it currently logs prose with no ids — the before/after is the whole argument for structured logging in one diff.

`ai_os/metrics.py` provides counters, fixed-bucket histograms, a registry with a deterministic snapshot and reset, and increment points that fire on **span close** so metrics and traces cannot drift. Labels come from a declared allow-list. `GET /metrics` exposes a JSON snapshot with a documented Prometheus-text option, and `GET /traces/{trace_id}` gains completeness fields and remote spans. Unknown ids still fail cleanly, and a trace assembled purely from reports renders in the same schema as a local one.

Every knob is env-driven per Day 4: propagation on/off, log level and ring size, metrics enable, ingest route enable and bind address. **Off must be genuinely off** — no headers added, no counters incremented, no report submitted. A flag that still injects headers while "disabled" would quietly contaminate every D-fixture.

---

## 9. Test: The D-Fixtures and the Control

Expected behavior, written before implementation:

| ID | What it proves | Input / injection | Expected |
|---|---|---|---|
| D1 | inbound continuation | valid / absent / malformed / all-zero / unknown-version `traceparent` | valid → same `trace_id`, fresh `span_id`, remote parent, `kind=server`; each failure → fresh root **with the reason recorded**; a forged header changes no store/tenant/index/permission |
| D2 | outbound injection | provider, Saleor, Postgres wrapper, worker | each carries a parseable `traceparent`; the worker echoes what it received; parent chain reconstructs; only allow-listed bounded baggage present; no keys or unbounded values |
| D3 | assembly | reports out of order, duplicated, late | correct tree; duplicates do not double-count duration or cost; late recorded as late; repeated submission is idempotent |
| D4 | end-to-end spine | one request through the real chain | one tree: `server` → agent internals → RAG/vector store → Saleor/Postgres → response, with kinds and parents matching the declared topology |
| D5 | gap honesty | an un-instrumented hop; a killed worker | explicit `missing` records naming component, expected span, and reason; `complete: false`; **no** fabricated zero-duration success; `complete: true` only when every declared child arrived |
| D6 | structured logs | full run across both processes | every line is one JSON object with the fixed spine; ids resolve to real spans; one grep by `trace_id` returns the request's lines across processes; redaction finds no secrets, SQL text, rows, prompts, or documents |
| D7 | metrics | same run, metrics on | counters/histograms derive from span closes; metric totals reconcile with span totals for the window; declared fixed buckets; label allow-list rejects `trace_id`/user/question/SQL; report states metrics cannot identify an instance |
| D8 | cross-boundary money and latency | provider + Saleor/Postgres run | tokens/cost == exact linked ledger delta across components; the client/server pair for one provider call is not summed twice; end-to-end latency is the root/critical path, not Σ hops; TTFT present for streams; outside-the-trace remainder named |
| D9 | **control** | propagation/logging/metrics on vs off; collector raising, hanging, unreachable | same answers, provider/attempt order, tool results, SQL and row counts, tokens and cost; every existing fixture unchanged; only headers and additive fields differ; telemetry failure changes nothing |

Day 23's O1–O9 must also re-run through the distributed path: the local grammar, prompt provenance, ledger reconciliation, and redaction all have to hold with remote spans present. If a remote span breaks prompt provenance or cost reconciliation, the grammar is wrong, not the fixture.

The `MECH`/`PENDING` split stays honest. **Parse/format, parent linking, idempotent merge, gap detection, metric and span arithmetic, log validity, and redaction are `MECH`.** Whether the declared topology matches reality, whether the attribute set is operationally sufficient, and whether a local collector is the right long-term shape are design judgments. Production sampling and retention, durable log shipping, a metrics backend, remote internals of Postgres/Saleor/providers, client-side spans, and ingest authentication are explicitly outside today's proof.

The control row is the one that protects everything else. If a header, a log line, a metric, or an ingest hiccup changes an answer, a tool result, an attempt sequence, or a cent of cost, the instrumentation is wrong. Fix the tracer; do not bless a new baseline. **Telemetry that can change the request is not observability — it is a participant.**

---

## 10. Measure: The End-to-End Report and the Price of Crossing

The report has two layers. First, one request table across the whole chain:

| trace_id | hops | components seen | complete / gaps | model path | prompt versions | docs | tools | SQL verdict | tokens | cost | root ms | ttft ms | errors | status |
|---|---|---|---|---|---|---:|---:|---|---:|---:|---:|---:|---|---|
| `…` | 9 | api, agent, rag, store, provider | complete / 0 | primary | rag-answer-v1 | 4/7 | — | — | … | … | … | — | — | ok |
| `…` | 11 | api, agent, data, saleor, postgres | complete / 0 | primary | reasoning-v1 + termination-v1 | — | 2/2 | allow-listed | … | … | … | — | one rejection | ok |
| `…` | 8 | api, agent, provider | complete / 1 (worker late) | primary → fallback | reasoning-v1 | — | 1/2 | policy stop | … | … | … | — | rate_limit→ok | ok |

Second, the assembled trees for four hostile paths: a successful RAG request, a successful agent investigation that reaches Saleor/Postgres, a retry→fallback run where one span report arrives late and one is duplicated, and a request with a genuinely missing hop. Each tree should be readable as a story: what the user asked, which component continued it, what each hop cost, what the far side never told us, and how the response closed.

The questions the day exists to answer, answered from the record:

- Which component continued this request, and under which inbound correlation id?
- Which spans belong to systems we do not own, and what exactly did we observe about them?
- Where did user-perceived time go versus server-side root duration?
- Which report was late, which was duplicated, which hop is missing, and does the trace say so?
- Can one grep find every line for this request across both processes?
- Do the metrics agree with the traces for the same window?

Distributed tracing costs **0 chats, 0 embeds, and 0 extra SQL**. Model, embedding, and query costs are identical to the Day-23 untraced control. The real overheads — header bytes, context serialization, report transport, metric increments, log volume, collector memory — are measured and reported rather than hidden, and they are never allowed to nudge a retry decision. If instrumentation overhead is large enough to change timeout pressure or fallback behavior, D9 fails even when the report looks excellent.

And the day does not claim more than it built. A safe assembled trace reconstructs causal execution and provenance across the boundaries we cross. It does not see inside Postgres, Saleor, or a provider; it does not measure the user's network; it does not ship to a collector or survive a restart; it does not authenticate its ingest route. Naming those as `PENDING` is not a gap in the notes — it is the notes' most load-bearing sentence.

---

## 11. Documents

- `docs/concepts/distributed-tracing.md` — traceparent and baggage, continuation versus new root, span kinds, remote flags, the client/server double-count trap, gap/late/duplicate semantics, logs versus metrics versus traces, cardinality limits, fail-open telemetry, and the boundary between today's local collector and production telemetry.
- `docs/evaluations/trace-day24.md` — D1–D9 results, four assembled trees, cross-boundary ledger reconciliation, the double-count and gap demonstrations, D9 control, and the remaining `PENDING` lines.
- PROGRESS rollup — mark propagation, assembly, structured logging, and metrics mechanics only when their deterministic verification is green. Keep production collector/export, sampling policy, remote internals, client-side spans, and ingest authentication `PARTIAL`/`PENDING`.

---

## 12. Daily Learning Record

### Problem

Day 23 solved causality inside one process. The moment work leaves that process, every guarantee quietly evaporates: the `trace_id` is a Python value that dies at the socket, the provider's queue time and safety filtering are invisible, Saleor's resolver timing is invisible, Postgres's plan is invisible, and a worker that dies before reporting leaves no evidence at all. Meanwhile the repo has three disconnected kinds of truth — a span tree for one process, a ledger of provider costs, and an evaluation trace for quality — and no way to say "these all belong to the same user request" once a request spans an API, an agent, a retrieval store, and a database. So a slow request cannot be attributed to a hop, a failed request cannot be told apart from an unobserved one, and a surprising bill cannot be decomposed across components. The naive fix is worse than the gap: a central log pile that looks complete because uninstrumented hops were padded with zeroes, metric labels carrying `trace_id` until the registry explodes, and telemetry that can fail a customer request because an exporter was down. The problem is not missing logging — it is missing **identity across boundaries, honest measurement of hops we own, explicit representation of hops we do not, and views (logs, metrics) that share the identity without fragmenting the truth.**

### Concept

**A distributed trace is one identity that survives every boundary, an honest span for each hop we initiate, and an explicit gap for each hop we do not own.** An inbound `traceparent` is adopted rather than replaced: same `trace_id`, fresh `span_id`, remote parent, `kind=server` — and a rejected header mints a fresh root with a recorded reason rather than failing the request. Every outbound call injects the current context and creates a `client` span describing *our* wait, status, and result shape, never the far side's internals. Reports arrive out of order, duplicated, or late, and sometimes never; assembly is idempotent on `span_id`, order-free, and materializes `missing` entries from a declared topology, so completeness is a fact in the record rather than an impression. Two views sit beside the trace without replacing it: **structured logs** (one JSON object per line, ids bound from the same context, same redaction) for instant, high-cardinality detail, and **metrics** (counters and fixed-bucket histograms incremented at span close, labels from an enumerable allow-list) for aggregate behavior — with the explicit admission that metrics cannot name an instance. Money stays the ledger's, reconciled across components, and the client/server pair for one interaction is never summed twice. Telemetry is fail-open; application policy stays fail-closed; D9 keeps observation from becoming intervention.

### Understanding

The re-readings hit in order: **(1) A boundary is a control edge, not a box.** The honest count for this repo is five, not eight services: inbound HTTP, three outbound clients, one real component crossing, and the response. Inflating `RagStore` into a "vector database" would be the Day-7 padding mistake wearing new clothes. **(2) Correlation id ≠ trace id ≠ span id.** The correlation id joins records, the trace id names one global trace, the span id names one operation; adopting a trace id while minting a new span id is what makes continuation possible. **(3) A wire format is a contract.** W3C traceparent is boring on purpose — fixed widths, lowercase hex, non-zero ids, a version, a sampled bit — and validation is part of the format, because a lenient parser is an outage waiting for a future version. **(4) Forgetting to inject is the quiet failure.** Two disconnected local traces each look healthy; the seam that breaks is the outbound header. **(5) Baggage is untrusted data.** It can carry `surface=cli`; it must never carry a key, and it must never select a store, tenant, index, or permission — otherwise observability becomes an injection vector two days before Day 25 attacks the system. **(6) Span kinds are claims.** `server`/`client`/`internal` say who did the work; a chain of all-`server` spans never left the process, and marking a local file read as `client` is inflation. **(7) Two truthful spans can double-count one interaction.** Our `client` span and the far side's `server` span both exist; the end-to-end number is the root/critical path, never Σ hops — Day 23's nested-time lesson, one level up, where the temptation is stronger. **(8) We do not own most of the topology.** Provider internals, Saleor resolvers, and Postgres plans are unobservable from a client; "the DB took 412 ms" should read "we waited 412 ms for statement `sha256:…`." **(9) Loss is data.** Missing, late, and duplicate are three named states; a gap rendered as a fast success is a lie with good typography, and `complete: true` must mean every declared child actually arrived. **(10) Logs, traces, and metrics are three questions.** One identity serves all three; labeling a metric with `trace_id` or question text is a cardinality bug that works locally and fails in production. **(11) Fail-open is a requirement, not a nicety.** A telemetry dependency with no user is a liability — it must never fail a request, and application policy must stay fail-closed. **(12) Client-perceived time is not root duration.** DNS, TLS, proxy, and queue live outside the trace; claiming the root span as "what the user experienced" is a small lie that compounds.

### Implementation

- `ai_os/observability.py` (extends Day 23) — `TraceContext` parse/format/validate, `new_root`/`child`/`remote_child`, inject/extract for headers and carriers, span kinds, `remote` flag, `remote_endpoint`, gap records, `SpanReport`, `TraceAssembler`, bounded collector, and `schema_version` bump.
- `ai_os/structured_logging.py` (new) — JSON formatter with the fixed spine, context-binding filter, pre-serialization redaction filter, bounded ring, `GET /logs?trace_id=…`; the `ai_os.gateway` logger migrates first.
- `ai_os/metrics.py` (new) — counters, fixed-bucket histograms, registry with snapshot/reset, span-close increment points, declared label allow-list, `GET /metrics` (JSON, optional Prometheus text).
- `ai_os/server.py` (extends) — inbound extract/validate/continue-or-mint with recorded reason, `server` root, `trace_id` in body and header, `response_sent` event, TTFT, non-fatal header rejection.
- `ai_os/gateway.py` (extends) — attempt spans become `kind=client`, inject context per provider request, `remote_endpoint` = provider host, explicit gap when a transport offers no header hook; retry/fallback/budget/ledger unchanged.
- `ai_os/data.py` (extends) — `client` spans for `run_saleor_sql` / `describe_saleor_schema` with operation name, statement hash, policy verdict, row count, truncation, timeout, duration; no SQL text, rows, or DSN; client-info correlation id where supported.
- `ai_os/rag.py` / vector-store seam (extends) — local `RagStore` read as an `internal` span; the future remote-store `client` seam defined and marked `PENDING`.
- `ai_os/worker.py` (new) — the genuinely crossing component: extract, continue with a remote parent, do one job, report the span back; the fixture that makes "distributed" real.
- `tools/distributed_smoke.py` + `test_day24.py` — D1–D9 plus Day 23's O1–O9 re-run, offline and deterministic.
- `docs/concepts/distributed-tracing.md`, `docs/evaluations/trace-day24.md`.

### Failure

1. **A new root per component.** Each service mints its own trace id, producing N healthy local traces and no end-to-end story. Anti-fix: continuation on valid inbound context, with the reason recorded when refused.
2. **Forgotten injection.** The provider call carries no header, so the provider's report lands in a different trace. Anti-fix: inject at every outbound seam and assert it in D2.
3. **Baggage used as authority.** A forged `tenant` or `index` header selects a store or connection. Anti-fix: baggage is data; selection comes from authenticated, server-side configuration; untrusted headers are validated, never obeyed.
4. **Unpadded gaps.** A dead worker's span is simply absent, and the trace reads as a fast success. Anti-fix: declared topology + `missing` records + honest `complete`.
5. **Sum of hops as end-to-end latency.** Client + server spans for one provider call, or all hops, added together. Anti-fix: root/critical path is the number; hops are attribution.
6. **Client spans as proof of internals.** "Postgres took 412 ms" implies a plan we never saw. Anti-fix: name the far side as unobserved; measure our own wait, status, and result shape.
7. **`trace_id` in metric labels.** Works locally, explodes cardinality in production. Anti-fix: enumerable label allow-list in code; instance questions go to the trace.
8. **Telemetry on the answer path.** An ingest failure, a slow exporter, or a log write raises into the request. Anti-fix: fail-open everywhere; D9 includes a collector that raises, hangs, and is unreachable.
9. **Double-writing logs and spans.** The same fact emitted twice, then one copy redacted and one not. Anti-fix: single redaction point, distinct reader purpose, one redaction fixture over both views.
10. **"Off" that still sends.** The disabled flag injects headers or increments counters anyway, quietly contaminating every fixture. Anti-fix: off means no headers, no reports, no counters; assert it in D9.
11. **Incomplete reported as complete.** `complete: true` because the assembler saw no *declared* child, after the topology was trimmed to match what happens to be instrumented. Anti-fix: topology written from the chain, not from the code; gaps have names.
12. **The local collector called production telemetry.** In-memory traces and a JSON `/metrics` are described as durable, sampled, shipped, or alerting. Anti-fix: name the boundary; deployment owns the rest.
13. **Payloads crossing in baggage or logs.** SQL text, rows, prompts, or documents smuggled in for "debuggability." Anti-fix: metadata-safe default, statement hashes, pre-serialization redaction, secret-scanning fixture.

### Debugging

- **Two traces for one user action** → compare the outbound headers first. A missing or malformed `traceparent` on one hop is the whole bug; then check whether the far side validated or refused it.
- **A child span is orphaned in the assembled tree** → its report arrived without a parent. Either the parent was never reported (a gap on the parent side) or the child minted a local root instead of continuing; inspect the continuation reason on the child's root.
- **Durations look doubled** → check for a client/server pair being summed, or a duplicate report counted twice. Assembly must be idempotent on `span_id`; if it is not, fix the assembler, not the report.
- **A trace is `complete: false` with no obvious dead component** → compare the declared topology against what actually emits spans. Either a hop is uninstrumented (a real gap) or the topology is wrong (a documentation bug) — decide which, and write it down.
- **Metrics disagree with traces for the same window** → the increment is not firing at span close, or a counter is incremented on a path with no span. Metrics must be derived from spans; a hand-incremented counter is the bug.
- **A forged header changed behavior** → stop and treat it as a security defect, not an observability one. Header-derived values must never select a store, tenant, index, credential, or permission; that is Day 26's boundary being touched early.
- **The request got slower with tracing on** → find what is on the critical path: synchronous report delivery, a blocking log write, an unbounded ring. Telemetry must be off the answer path; move it to the same fail-open wrapper.
- **Nothing was logged for a request that definitely happened** → check the propagation flag and the ring bound before suspecting the code path. Then check the collector's eviction: a small ring under load evicts the traces you were about to look at.
- **A log line has no `trace_id`** → the call happened outside any request context. That is legitimate for boot and cron, but if it is a request, the context binding is broken and the line is unattributable.

The trace should answer the question directly: which component continued this, which hop is remote, what did we pay in time and money, what did we never see, and which hop went dark. A debugging loop that still needs intuition has not finished the distributed trace.

### Evaluation

The D1–D9 table and four assembled trees in `docs/evaluations/trace-day24.md` are the evidence. The numbers a future observer can carry: requests whose inbound context was continued versus freshly minted, **with the reason recorded in every case** (target: 100% of non-continuations explained); outbound calls carrying a parseable `traceparent` (target 100%); assembled traces with a complete parent path (target 100%); orphan spans (target 0); **gaps: every incomplete trace names its missing component** (target 100%, with 0 fabricated zero-duration successes); duplicate reports that changed a duration or cost total (target 0); token/cost deltas between traced and untraced runs across all components (target 0, reconciled to the ledger); log lines that fail JSON parsing or the redaction scan (target 0); metric totals versus span totals for the same window (target 0 delta); metrics carrying a high-cardinality label (target 0, enforced by an allow-list); TTFT present for every stream; and every existing gateway/RAG/tool/agent/data/SQL-safety/O1–O9 fixture unchanged with propagation, logging, and metrics on and off, including a collector that raises, hangs, and is unreachable.

The `PENDING` line is plain: there is no production collector, log shipping, metrics backend, or scrape-based alerting; no sampling or retention policy beyond a local ring; no visibility into Postgres, Saleor, or provider internals; no client-side spans; no authentication on the ingest route; and no real remote vector store. Day 25 attacks the system, Day 26 constrains it, Days 27–29 put a human in the loop, harden it, and deploy it — none of which is served by a better trace, and all of which will be measured by one.

### Architecture

The claim of the day is the series' claim at a new boundary: **the system can now explain a request as one causal structure that survives every edge it crosses, and can say honestly where it went dark.** It does not pretend observability is evaluation (operational trace owns identity, time, status, and cost references; retrieval/agent traces own quality; the ledger owns money), and it does not pretend a client is a server (we measure our wait; remote internals are labeled unobserved). The architecture stays local and dependency-free because Rule 6 still holds: a hand-written topology, a bounded collector, a loopback-only ingest route, and no broker or vendor SDK — because the single-process need was real before the distributed one, and the schema plus the fixtures are the contract any later backend must satisfy.

The existing seams gain meaning rather than replacement. The server's request handler becomes the continuation point. The gateway's attempt spans become `client` edges with injected context. The data agent's query spans become hashed, verdict-carrying client spans that keep Day 15's safety intact. The Day-23 trace store becomes the read surface for assembled trees. The `gateway` logger becomes the first structured-log citizen. This is "build before abstracting" applied to distributed tracing: learn the wire format, the continuation rule, and the loss semantics from first principles before asking whether OpenTelemetry, a collector, or a vendor backend is the right future adapter — and keep the fixtures sharp enough that the answer is testable rather than fashionable.

### Questions

- Day 24's collector is local and fail-open. What is the right production shape — a sidecar, an in-process OTel SDK with a collector, or log-derived reconstruction — and which of today's fixtures would survive each choice?
- Sampling is propagated but trivial. When a head sampler arrives, should the decision be made at the API edge only, or should error-biased tail retention keep every failed trace regardless of the head coin flip?
- Baggage is capped and allow-listed here. Is any of it worth propagating at all in production, or does every propagated key become a compatibility obligation?
- A `client` span measures our wait. Should the far side's own span be fetched and joined when a provider offers trace headers back, or is that a trust boundary we should refuse?
- The topology is hand-written, which is honest but drifts. Should a mismatch between declared and observed topology raise, warn, or just show up as a permanent gap count in the trace?
- Logs, traces, and metrics now share one id. Which of the three should be authoritative when they disagree — and what should the disagreement itself look like to an operator?
- The end-to-end number is server-side. Do we ever want client-side spans (a browser beacon, a CLI wrapper) to close the loop, and what would that cost in trust and privacy?
- The ingest route is unauthenticated and loopback-only. What is the minimum authentication and rate limit before it could ever bind beyond localhost, and does that belong to Day 26 or to deployment?

### Tomorrow

Day 25 — Prompt Injection: the system goes on the offensive. The curriculum turns **"attack the system"** into three concrete attacks — **direct injection** (`Ignore all previous instructions…`), **indirect injection** (malicious instructions hidden inside retrieved documents), and **tool manipulation** (tricking the agent into calling a dangerous tool) — with the deliverable **threat model and initial defenses.** Day 24 built the map of where untrusted input enters and where privileged actions leave; Day 25 walks those same boundaries adversarially. The chain stops being a happy path and becomes an attack surface: the document that RAG retrieved is now a weapon, the tool call the agent proposed is now a target, and the trace that recorded them faithfully is now the evidence an incident review will need. **The tree has learned to cross doors; tomorrow we find out who is knocking.**
