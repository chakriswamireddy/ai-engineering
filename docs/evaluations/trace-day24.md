# Day 24 — Distributed Trace Evaluation

> Rules of the day: fixture expectations written **before** implementation; the
> control (D9) is the row that protects every other; meaning is never promoted
> silently. Reproduce with:
>
> ```bash
> python tools/distributed_smoke.py     # D1–D9 (two real processes, one trace id)
> python test_day24.py                  # the deterministic unit suite underneath
> python tools/regress_all.py           # the gate (20 rows, 0 moved)
> ```
>
> The two processes are this runner and the worker service `ai_os/worker.py` on
> `:8710`; the scripted dual-provider stub runs on `:8700`/`:8701`. No API key.

## 1. The D1–D9 fixture table

| ID | What it proves | Expected | Result |
|---|---|---|---|
| **D1** | inbound continuation | valid `traceparent` adopted (same trace id, fresh span id, remote parent, `kind=server`); absent/malformed/all-zero/future-version each mint a fresh root **with the reason recorded**; forged baggage changes nothing | **green** |
| **D2** | outbound injection | the provider call carries a parseable `traceparent` with the request's trace id and only trace-context headers; the worker echoes the header it received and its remote parent is our client span | **green** |
| **D3** | assembly | out-of-order reports assemble; duplicates counted (not double-counted); late recorded; end-to-end number is the root, never the hop sum; valid JSON | **green** |
| **D4** | end-to-end spine | one request through the real chain is one tree: one `server` root, `internal` retrieval, `client` provider attempt (remote host named), worker `server` span + its `client` Saleor hop; every parent path resolves; `components_seen = [api, gateway, rag, worker]` | **green** |
| **D5** | gap honesty | a dead worker → `dispatch` returns `None`; the expected hop is a named `missing` record; `complete: false`; no fabricated zero-duration success; a fully-arrived request reports `complete: true` | **green** |
| **D6** | structured logs | every line is one JSON object with the fixed spine; log ids resolve to real spans; **>1 component** across both processes; redaction finds no forbidden keys; the worker process's lines share the trace id | **green** |
| **D7** | metrics | counters derive from span closes (`spans_total == span count`); ledger totals reconcile with the trace; declared fixed buckets; the label allow-list rejects `trace_id`/user/question/SQL; the snapshot says metrics cannot identify an instance | **green** |
| **D8** | cross-boundary money/latency | tokens/cost equal the exact linked ledger delta across attempts; the client/server pair is not summed into the end-to-end number (`root=6.744 ms` vs `Σhops=16.933 ms`); TTFT present for streams | **green** |
| **D9** | **control** | tracing/propagation on vs off: same answer, citations, retrieval, generation (wall-clock aside); off injects no header; a collector that raises changes nothing about the request | **green** |

All ten D-checks pass. `test_day24.py` adds the deterministic unit layer beneath
them (traceparent grammar, baggage bounds, inject/extract, assembler idempotency,
collector fail-open, metric label allow-list, log spine + redaction).

## 2. Four assembled trees

### (a) A successful RAG request (stub store)

```text
server  rag                       (component=api)         ← root, span_kind=server
├── model.embed                   (internal)
│   └── model.embed.attempt       (client → 127.0.0.1)     ← injected traceparent
├── retrieval                     (internal, store_kind=local_file, store_version=1)
│   ├── document  rank 1  kept
│   └── document  rank 2  dropped (budget)
├── model.chat                    (internal)
│   └── model.chat.attempt        (client → 127.0.0.1)
└── final_answer event            (answer hash/size, citations)
```

The vector-store hop is **internal** because the store is a local file — the code
does not fake a network hop it does not have. `retrieve_remote()` is the named
seam a real vector DB would use.

### (b) A successful agent investigation that reaches Saleor

```text
server  worker.saleor_catalog     (component=worker)      ← remote child of the caller
└── saleor.graphql               (client → saleor, remote)
```

The worker's root is a `server` span whose `parent_span_id` is the caller's
`worker.job` client span **in the other process** — the same trace id, a fresh
span id. The provider/database hops of the calling request appear under the API
root (see (a)).

### (c) A retry → late/duplicate reports

```text
model.chat                        (internal)
├── model.chat.attempt 429        (client)  status=error, error_kind=rate_limit
└── model.chat.attempt ok         (client)  status=ok, actual_model, finish_reason
```

Retries are **sibling attempts** under one logical call (Day 23's lineage,
unchanged). In the assembly fixture a duplicate report of the same `span_id` is
counted in `duplicates` and changes no duration or cost total; a report that
arrives after `finalize()` is listed in `late` — a fourth state beside
`delivered`/`duplicate`/`missing`.

### (d) A request with a genuinely missing hop

```text
request                           (root)
└── worker.job                    (client) status=error     ← dispatch failed open
gaps: [{component: worker, span: worker.saleor_catalog, reason: never_arrived}]
complete: false  gap_count: 1
```

No fabricated zero-duration success appears for the dead hop. A trace that
silently omitted it would be a false trace, not a shorter one.

## 3. Cross-boundary money and latency

- **Money.** The D8 row starts from a provider retry (429 → ok) and asserts the
  trace's totals equal the **exact linked ledger delta**: two records, one failed,
  tokens and cost equal to the penny. The trace never reprices; the ledger owns
  money.
- **Latency.** The end-to-end number is the **root/critical path**. In the stub
  chain the root measured `6.744 ms` while the sum of the individual hop spans was
  `16.933 ms` — summing hops (or a client span and its far-side server span) would
  double-count one interaction. Hops are per-hop attribution, never a total.
- **Streams.** `model.stream.attempt` carries `ttft_ms`; the SSE bytes are
  unchanged (Day 23's O8b, still green).

## 4. D9 — the control

| Assertion | Result |
|---|---|
| tracing off adds no `trace_id` | green |
| answers/citations/retrieval/generation identical (wall-clock aside) | green |
| tracing off injects **no** provider `traceparent` | green |
| a collector that **raises** changes nothing about the request | green |

This is the row that protects the others. If a header, a log line, a metric, or
an ingest hiccup changes an answer, a tool result, an attempt sequence, or a cent
of cost, the instrumentation is wrong — fix the tracer, do not bless a new
baseline.

## 5. No regression

`tools/regress_all.py` gained one row (**D1–D9**, the suite above); the gate is
now **20 rows, 0 moved**. The full replay (doc nine 9/9 + code six 6/6 + every day
suite Days 7–23 + the golden rows + O1–O9 + D1–D9) is green with propagation,
logging, and metrics installed. Nothing in the answer path moved: with
`AI_TRACING=0` the transcript is byte-for-byte Days 12–23, and O9 still proves it.

> Harness note: the suites share the mock gateway on `:8700`/`:8701`, so two
> concurrent test runs on one host contend for that port. That is a harness/
> environment property (a fixed dev port), not a Day-24 behaviour; the fixtures
> themselves are deterministic (each suite passes 4/4 standalone, and the gate is
> green on repeated runs).

## 6. Cost shape

Distributed tracing costs **0 chats, 0 embeds, and 0 extra SQL**. Model,
embedding, and query costs are identical to the Day-23 untraced control (D9). The
real overheads — header bytes, context serialization, report transport, metric
increments, log volume — are local CPU/memory, and the collector is bounded with
oldest-first eviction. No instrumentation decision is allowed to nudge a retry.

## 7. `PENDING` (named, not declared solved)

- **A production collector, log shipping, a metrics backend, and scrape-based
  alerting** are deployment work, not today's local ring/snapshot.
- **Real sampling and retention policy** — the mechanism is right (the sampled bit
  is propagated); the policy is trivial by admission.
- **Remote internals** of Postgres, Saleor, and the providers are unobservable
  from a client and stay labeled gaps.
- **Client-side spans** (a browser beacon, a CLI wrapper) need a client we do not
  have; the outside-the-trace remainder (DNS/TLS/queue) is named, not claimed.
- **Ingest authentication and rate limiting** are a Day-26 boundary; the route
  ships disabled.
- **The topology is hand-written**, which is honest but can drift from reality; a
  mismatch is a documentation bug or a real gap, and the fixture makes you decide
  which.
