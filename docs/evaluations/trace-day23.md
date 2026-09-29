# Day 23 — Trace Evaluation (O1–O9)

> What a run of `python tools/trace_smoke.py` proves, and what stays `PENDING`.
> Everything below is `MECH` (deterministic, offline, **0 chats / 0 embeds**)
> unless a line says otherwise. Trace trees shown are one real run's; the
> *shapes* are what the fixtures assert, so durations differ between runs.

## Headline

The system can now explain a request as a **causal structure** instead of a pile
of receipts: one root identity, a nested and clocked span tree, explicit prompt
provenance, and totals reconciled to the exact linked ledger rows — with the
**O9 control** proving that installing the tracer changes no answer, attempt,
tool result, token, or cost.

## O1–O9

| ID | What it proves | Evidence | Result |
|---|---|---|---|
| O1 | one request, one root, valid parent paths | one agent run: 1 root, all spans share the trace id, every parent resolves, `root.duration_ms` ≈ 8.5 ms while Σ nested spans ≈ 20.3 ms | `MECH` green |
| O2 | RAG spine, retrieved vs shown | `request → model.embed → retrieval → docs → model.chat → answer`; kept **and** dropped document events; raw question/document text absent; ids/ranks/scores/chars/hashes present | `MECH` green |
| O3 | agent spine, parallel/deferred | every proposal/validation/result linked to its step span; deferred call recorded `executed=false` with a reason; model turn is a child of the step; termination reaches the final event | `MECH` green |
| O4 | model lineage | 429→ok gives 2 sibling attempts under one `model.chat`; `401` primary → anthropic fallback names both providers; failing attempt retained | `MECH` green |
| O5 | money reconciliation | trace tokens/cost == the exact linked ledger delta; failed retry linked under ledger policy; unknown model ⇒ `price_status=unknown` | `MECH` green |
| O6 | failure closure | `invalid_key` · `bad_request` · `timeout` · `dimension_mismatch` · tool error · budget stop — typed, ancestors preserved, no open `ok` span | `MECH` green |
| O7 | prompt provenance | each model span carries name/version/exact hash; template change moves the hash; version change moves the key; no prompt body by default | `MECH` green |
| O8 | inspection, retention, redaction | lookup returns the sanitized tree; unknown id clean; bounded store evicts oldest-first; secrets redacted; over-long content hashed; raw mode capped yet still redacts | `MECH` green |
| O8b | streaming pass-through | traced and untraced chunks identical (the provider's own `ttft_ms` aside); attempt span records `ttft_ms` + `delivered`; no buffering | `MECH` green |
| O9 | **control** | tracing off ≡ on: answers, citations, retrieval and generation identical; only the trace id is additive | `MECH` green |

## The four hostile trace trees

### 1. Successful RAG (`/rag`)

```text
rag                          request    ok    0.711ms
├── final_answer  {refused:false, answer_sha256:3919…, answer_chars:N, citations:5}
├── model.embed               model     ok    0.092ms
│     └── model.embed.attempt model     ok    0.026ms
├── retrieval                 retrieval ok    0.185ms
│     ├── document {rank:1, doc_id:developer/checkout/api-guide#completing-checkout, kept:true, text_sha256:…}
│     ├── document {rank:2, doc_id:…, kept:true,  text_sha256:…}
│     ├── document {rank:3, doc_id:…, kept:false, drop_reason:"budget", text_sha256:…}
│     └── document {rank:4/5, kept:false, drop_reason:"budget"}
└── model.chat                model     ok    0.054ms
      └── model.chat.attempt  model     ok    0.013ms   prompt=rag-answer@v1

TOTALS records=2 ok=2 failed=0 tokens=43 cost≈$1.63e-05 price_status=known
```

The distinction that matters is **retrieved vs shown**: five candidates were
retrieved, two fit the context budget, three carry a `drop_reason`. Raw text
never appears — only `chars` and `text_sha256`.

### 2. Successful agent investigation

```text
agent                        request     ok   6.626ms
├── agent.step.1             agent_step  ok   3.567ms
│     ├── tool_call      {name:search_saleor_code, executed:true, args_sha256:…, args_keys:[k,query]}
│     ├── tool_validation{ok:true}
│     ├── tool_result    {ok:true, evidence:4, elapsed_ms:…}
│     └── model.chat_with_tools  model  ok   2.354ms   prompt=agent-reasoning@v1
│           └── model.chat_with_tools.attempt  ok   2.271ms
└── agent.step.2 …           agent_step  ok   2.655ms
      └── final_answer {termination:"answer", citations:…, fabricated:false, cost_usd:…}

TOTALS records=2 ok=2 tokens=90 cost≈$3.06e-05 price_status=known
```

### 3. Retry → success (429 then ok)

```text
chat                         request  ok      25.9ms
└── model.chat               model    ok      25.7ms
    ├── retry_scheduled {attempt:1, delay_s:0.02, reason:"RateLimitError"}
    ├── model.chat.attempt   model    error    2.8ms   provider=openai error_kind=rate_limit
    └── model.chat.attempt   model    ok       2.4ms   actual_model=gpt-4o-mini finish_reason=stop

TOTALS records=2 ok=1 failed=1 tokens=36 cost≈$1.62e-05 price_status=known
```

The root is `ok` because the **request** succeeded; the failed attempt stays
visible as an error child. `sum(attempt durations)` (5.2 ms) is far below the
logical span (25.7 ms), which itself excludes the retry sleep as a separate
`retry_scheduled` event — nested time is attribution, not a latency total.

### 4. Retry → fallback (401 primary, secondary ok) / terminal failure

```text
chat                        request  ok    4.1ms
└── model.chat              model    ok    4.0ms
    ├── model.chat.attempt  model    error 1.8ms  provider=openai    error_kind=invalid_key
    └── model.chat.attempt  model    ok    2.1ms  provider=anthropic actual_model=claude-3-5-haiku-latest

chat                        request  error 2.3ms
└── model.chat              model    error 2.2ms
    └── model.chat.attempt  model    error 2.1ms  provider=openai error_kind=invalid_key

TOTALS (failed) records=1 ok=0 failed=1 tokens=0 cost=$0.0 price_status=known
```

The failing request closes the root with `status=error` and leaves no open `ok`
descendant (O6).

## Prompt provenance (O7)

| Prompt | Version | `sha256` (exact bytes) |
|---|---|---|
| `rag-answer` | v1 | `2af0cd86d1e124e749c08dd03af4a12cea787f3d26a5b65c2bd3efbcfd13678c` |
| `agent-reasoning` | v1 | `bf3971382fe56d74bb171fe5138ce338192aacf41554ae16894a5a60367cf2f7` |
| `agent-termination-max_steps` | v1 | `07fad921805c14904c33cf93562b06167aede5ad3fff251e55c63c30dd1b4394` |
| `agent-termination-budget` | v1 | `958ce76a7a6bac80277b26c69c46f6123aba0531ea9ec9bddb154131efdd7146` |

A template edit changes the hash without anyone remembering to bump a version; a
version bump changes the provenance key. **No prompt body appears in the default
trace** — the `SYSTEM_PROMPT`/`REASONING_PROMPT` text is absent from the
serialized record.

## Redaction and retention (O8)

- Secrets are dropped **by key**: an event carrying `api_key`/`authorization`
  serializes them as `"[redacted]"`; the secret string never appears in the JSON.
- Over-long content is replaced by `{sha256, chars}` (O8's 500-char note) — the
  default trace is metadata, not a content store.
- Raw mode (`set_raw_debug(True, cap=N)`) keeps a bounded `prefix` plus
  `truncated`, and **still redacts secrets**.
- The bounded `TraceStore(max_traces=3)` evicted the two oldest of five traces —
  deterministic oldest-first retention; the unknown-id path returns cleanly.

## O9 — the control

`run_rag` is executed twice on the same stub store, once with tracing on and once
with tracing off. The result dicts are equal after removing the additive
`trace_id`; the off run carries no `trace_id` key at all. Days 7–22 are re-run
unchanged through `tools/regress_all.py` with tracing installed — **19 rows, 0
moved**. Instrumentation changed no answer, attempt order, tool result, token
count, or cost.

## Cost of looking

Tracing itself costs **0 chats and 0 embeds**, so model/embedding usage and
ledger cost are identical to the untraced control. The real cost is local CPU,
memory, and serialization — kept bounded by the `TraceStore` cap and the string
hashing rule, and measured rather than hidden inside model cost.

## `PENDING` (named, not declared solved)

- **Operational usefulness and attribute balance are a design judgment.** The
  fixtures prove the *mechanics* (identity, parentage, monotonic duration,
  ledger arithmetic, prompt hashes, redaction); that the selected attributes are
  the right ones for a real operator is a review, not a fixture.
- **Exact replay is not promised.** A metadata trace reconstructs causal
  execution and provenance; it does not reconstruct hidden content or guarantee a
  stochastic model re-answers identically.
- **Provider-specific full-fidelity capture** is out of scope; raw capture stays
  opt-in, bounded, and redacting.
- **Durable storage/export, sampling and retention policy, and cross-process
  correlation are Day 24 / deployment work** — today's store is a local,
  in-memory, bounded mechanism, not production telemetry.

## Re-verify

```bash
python tools/trace_smoke.py         # O1–O9 + O8b + the O9 control (0 chats, 0 embeds)
python tools/regress_all.py         # the 19-row gate, tracing installed
```
