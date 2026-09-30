# Distributed Tracing — one identity that survives the wire

> Day 24 concept. Day 23 gave a single in-process request one identity, one span
> tree, one monotonic clock, one prompt vocabulary, and one ledger-reconciled
> bill. Day 24 turns the camera outward: **understand cross-component
> execution** — the chain **User → AI API → Agent → RAG → Vector DB → Saleor API
> → PostgreSQL → Response** — and builds the **end-to-end request trace**.
>
> **The one line the day would die defending: a distributed trace is not the
> union of everyone's logs — it is one identity that crosses every boundary, an
> honest measurement of each hop we own, and an explicit, named gap for every hop
> we do not.**

## 1. "Cross-component" is about boundaries, not boxes

A **component boundary** is any point where work leaves this process's control.
In this repository that is honestly **five**, not eight microservices:

| Hop | Reality in the repo | What we can observe from here |
|---|---|---|
| User → AI API | real inbound edge: `server.py` `/chat`, `/rag`, `/chat/stream` | headers received, route, request hash/size, our handling time, status |
| AI API → Agent | in-process (`agent.py`) | everything: steps, model turns, tool calls, results, termination |
| Agent → Provider | real outbound HTTPS (`gateway.py`) | requested/actual model, attempt, fallback depth, latency, tokens, cost, status, error kind |
| RAG → Vector DB | **local JSON file** (`RagStore`) — no network service | store version, k, budget, candidate counts, ranks, scores; a *remote* store is `PENDING` |
| Agent → Saleor API | in-process seam (`data.py` + `worker.py`) | operation, field count, status, latency — not resolver internals |
| Saleor API → PostgreSQL | `psycopg` on a read-only DSN | statement **hash**, policy verdict, row count, truncation, `statement_timeout`, duration — not the plan |
| → Response | JSON body or SSE stream | our close time, TTFT, `trace_id` in body and header; never the user's DNS/TLS/queue |

Inflating `RagStore` into "a vector database" or reading a client span as proof
of Postgres's plan would be the Day-7 mistake in a new costume: a record that
looks complete because it was padded, rather than incomplete because reality is.

## 2. Correlation id ≠ trace id ≠ span id

- A **correlation id** is any identifier that stitches records together — a
  request id, a job id. A system may have several.
- A **trace id** names **one distributed trace**. It must be globally unique,
  fixed-size, and never reused: it is the key every component uses to agree they
  are talking about the same request.
- A **span id** names one operation *inside* that trace; the parent relationship
  is what makes it a tree rather than a bag.

On an inbound request the **trace id is adopted** and a **fresh span id is
minted**. That is what makes continuation possible without a mapping layer.

## 3. The wire format is a contract

W3C Trace Context is deliberately boring and deliberately strict:

```text
version   = 2 hex chars   (00 = current)
trace_id  = 32 hex chars  (16 bytes) — not all zero
parent_id = 16 hex chars  (8 bytes)  — not all zero
flags     = 2 hex chars   (bit 0 = sampled)
→ 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
```

Validation is part of the format. `ai_os/observability.py`'s `TraceContext.parse`
returns `(context, reason)` and **records why** an identity was not adopted:
`no_header`, `malformed` (wrong width / non-hex / uppercase), `all_zero`, or
`version_unsupported` (a well-formed future version we do not speak). A bad
header never fails a legitimate request — it mints a fresh, clearly-labeled root.

This repo's own ids were made wire-shaped to make the rule true: `new_trace_id()`
is 32 lowercase hex and every span id is 16 lowercase hex, so a span id *is* a
legal `traceparent` parent id and the id can cross a socket unchanged.

### Sampling is a propagated decision

The sampled bit travels with the context so downstream components do not each
roll their own dice. Day 24 keeps the bit honest and the policy trivial
(everything sampled locally, one switch) because a real head sampler needs a cost
model the day does not have — but the *mechanism* is right now, so a policy can be
added later without changing the wire.

### Baggage is untrusted, bounded, non-secret, non-authoritative

A tiny allow-list (`tenant`, `surface`, `request_kind`) may ride along, each value
length-capped and the whole set capped at 256 chars. It must never carry API
keys, PII, SQL text, prompts, or unbounded values — and it must never **select**
anything: a forged inbound `tenant` must not choose a store, an index, a
connection, or a permission. `extract_baggage` drops any key not on the allow-list
and any secret-shaped key; a forged header is data to validate, never authority
to obey.

### Extract and inject — two directions, both easy to get half-right

- **extract** on inbound: parse, validate, decide continuation vs fresh root, and
  record the reason (`start_server_trace`).
- **inject** on outbound: write the current context into the request headers of
  every client edge. `observability.outbound_headers()` is the single choke point;
  each provider merges it into `_headers()`, and `worker.dispatch` carries it.

Forgetting to inject is the single most common way a distributed trace silently
becomes **two disconnected local traces that each look perfectly healthy**.

## 4. Span kinds, remote parents, and the topology you do not own

**Span kinds** (`span_kind`) are an honest statement about who did the work:
`server` (we handled an inbound request), `client` (we called something else),
`internal` (local computation, e.g. the `RagStore` read or reranking),
`producer`/`consumer` (a queue boundary). A chain where every span claims
`server` never left the building; marking a local file read `client` is inflation.

> **A naming note.** `Span.kind` already carries Day 23's *operation family*
> (`model`, `retrieval`, `agent_step`, …). Day 24 adds `span_kind` as a **second,
> independent axis** rather than overloading the first. One grammar, two scopes —
> a parallel field, not a parallel model. It is the day's rule applied literally:
> add only what crossing a boundary requires.

**Remote-ness is a flag, not a second schema.** A child created from a remote
parent's context keeps its own fresh `span_id`, records the caller's span as
`parent_span_id`, and sets `remote=True`. Remote and local children assemble
identically; the flag exists so nobody later claims we measured an external
system's internals.

**The client/server double-count trap.** For one call there are two truthful
spans: our `client` span (what we waited) and, if the far side is instrumented,
its `server` span (what it did). Both are real; **adding them counts one
interaction twice**. The end-to-end number is the **critical path** — the root's
own wall-clock duration — never the sum of hop durations. Day 23's nested-time
lesson, one level up where the temptation is stronger because hops *look*
sequential.

**What client instrumentation can and cannot see:**

- **Provider** — requested/actual model, attempt, latency, tokens, cost, status,
  error kind, finish reason. *Not* queue time, internal retries, safety filtering.
- **Saleor** — operation, latency, status, field count. *Not* resolver timing.
- **PostgreSQL** — statement hash, policy verdict, row count, truncation,
  `statement_timeout`, total latency. *Not* the plan, indexes, or lock waits.

Saying "the DB took 412 ms" when we measured our own socket wait is the exact
overclaim to avoid. The honest phrasing: a `client` span `postgres.query` that
says we waited 412 ms for a statement we hashed, and a note that the server's
internals are unobserved.

## 5. Assembly: idempotent, order-free, loss-aware

Telemetry is a lossy channel. A span report has **four states**: `delivered`,
`late` (arrived after its parent closed or after the trace was assembled),
`duplicate` (same `span_id` re-sent), and `missing` (expected, never arrived).

`TraceAssembler` merges by `(trace_id, span_id)`, tolerates a child before its
parent, **counts** duplicates instead of double-counting durations, records late
arrivals, and materializes `missing` entries from a **declared topology** — a
hand-written per-component list of the children it is expected to produce. That
is what makes "the worker died" distinguishable from "the worker was never
asked", and how a gap gets a name instead of a shrug. The trace exposes
`complete`, `span_count`, `gap_count`, and `components_seen`, and `complete: true`
is only true when every declared child actually arrived.

**Never invent a zero-duration success.** A trace that silently omits a dead
worker's span is not a shorter trace; it is a false one.

## 6. The collector is local, bounded, and fail-open

`Collector` is a bounded, thread-safe, oldest-first local collector: an
in-process submit path plus an optional loopback-only HTTP ingest route that is
**off by default** (unauthenticated ingest is a Day-26 boundary). No broker, no
vendor SDK, no OpenTelemetry dependency. If propagation is later delegated to a
vendor, the schema, validation rules, and the D-fixtures remain the contract that
adapter must satisfy.

**Fail-open is the rule.** `Collector.submit` and the module-level `submit_report`
both swallow any failure and report it by value; the closure — a collector that
raises — must change nothing about the request. The application's own policy (SQL
allow-list, tool rejection, budget) stays fail-closed, exactly as before.
Telemetry that can fail the request is not observability; it is a dependency with
no user.

## 7. Logs, traces, metrics — three views, one identity

Three questions, three views, one `trace_id`. Collapsing them is the mistake.

- **Logs** answer *what happened at this instant, in this order, with these
  fields* — the home for rare, high-cardinality, human-readable detail.
  `ai_os/structured_logging.py` emits one JSON object per line on a fixed spine
  (`ts · level · logger · component · event · msg · trace_id · span_id ·
  parent_span_id · duration_ms · status`), binding ids from the **same** context
  variable Day 23 already uses for spans — one context, three views. Redaction
  runs **before** serialization and is the trace's rule verbatim; the
  `ai_os.gateway` logger was the first citizen to migrate (it used to emit prose
  with no ids at all).
- **Traces** answer *what happened to this one request, causally*.
- **Metrics** answer *how many, how slow, how expensive, how often broken* across
  a window — and by construction **cannot name an instance**. `ai_os/metrics.py`
  provides counters and fixed-bucket histograms whose increments fire at **span
  close** (via a registered hook), so metrics and traces cannot disagree about the
  same window; and whose labels come from an enumerable allow-list, so a
  `trace_id`/user/question/SQL label is a **visible diff, not an accident**.

A log line with no `trace_id` is a receipt; a metric labeled with a `trace_id` is
a cardinality bug wearing a helpful hat.

`GET /metrics` (JSON, optional Prometheus text) plus a bounded local ring for logs
and traces is a **local development surface**, not a production metrics stack.
Durable shipping, a real collector, and scrape-based alerting are deployment
concerns, and saying otherwise would be the Day-24 version of calling an
in-memory trace store "durable telemetry".

## 8. The inspection surface

- `GET /traces/{trace_id}` serves the same schema for a locally-built trace and an
  assembled one (one lookup, one redaction path).
- `GET /logs?trace_id=…` greps the bounded ring by request identity.
- `GET /metrics` is the aggregated snapshot.
- `POST /ingest/span` is **off by default** (`TRACE_INGEST=1` to enable).

## 9. Decisions, and where the boundary is

1. **One grammar, two scopes** — Day 23's `Trace`/`Span`/event schema, plus
   `span_kind`, `remote`, `remote_endpoint`, and gap records. `schema_version`
   bumped 1 → 2. No second trace type, no second store.
2. **Continuation, with a recorded reason** — adopt when valid; otherwise mint a
   fresh root and record `no_header`/`malformed`/`all_zero`/`version_unsupported`.
3. **Assembly is idempotent, order-free, loss-aware** — merge on `span_id`,
   count duplicates, mark late, materialize missing from a declared topology.
4. **The far side we do not own is a labeled gap, not a fabricated span.**
5. **Fail-open telemetry** — report delivery, ingest, and metric writes never
   raise into the request.
6. **Local before platform** — bounded collector + loopback ingest, no broker.

Out of scope today, named not hidden: a production collector / log shipping /
metrics backend / scrape alerting; real sampling and retention policy;
instrumenting the inside of Postgres, Saleor, or a provider; client-side spans;
and authentication/rate-limiting on the ingest route (Day 26).

## 10. Configuration

| Var | Default | Meaning |
|---|---|---|
| `TRACE_PROPAGATION` | `1` | inject/extract `traceparent`; `0` adds no headers |
| `TRACE_LOGGING` | `1` | structured-log JSON spine |
| `TRACE_METRICS` | `1` | span-close metric increments |
| `TRACE_INGEST` | `0` | enable `POST /ingest/span` (off; Day-26 boundary) |
| `AI_TRACING` | `1` | the Day-23 master switch (off ⇒ no trace at all) |

**Off must be genuinely off** — no headers added, no counters incremented, no
report submitted. A flag that still injects a header while "disabled" would
quietly contaminate every D-fixture; D9 asserts it does not.
