# ai-layer — Days 4–22: Gateway → Search → RAG → Code RAG → Tools → Loop → Data → Safe SQL → Planning → State → Memory → Multi-Agent → Evaluation → RAG Benchmark → Agent Benchmark

A reusable AI layer built in layers:

1. **Day 4 — The Model Gateway**: `Application → ModelGateway → Provider → LLM`.
   The gateway owns retries, exponential backoff + jitter, `Retry-After`,
   ordered fallback between providers, a per-call time budget and the cost
   ledger — while each provider only knows its own wire format.
2. **Day 5 — Vector Search**: brute-force `dot / cosine` nearest neighbour
   search over `(id, text, vector)` documents — no library, hand-rolled.
3. **Day 6 — First RAG Pipeline**: docs → parse → chunk → embed → store →
   retrieve → context → "answer" from the gateway, with citations and a
   refusal path when the documentation cannot cover the question.
4. **Days 7–9 — Debug + improve**: failure taxonomy and a similarity gate (7),
   hybrid lexical+semantic retrieval (8), rerank + MMR + context construction (9).
5. **Day 10 — Code RAG**: a second corpus (Saleor's *source*) behind the same
   seams — AST symbol extraction, interface-vs-body embedding, and identifier
   tokenization — answering **"where is it implemented?"** with `module#Symbol`
   citations and the identical fixture harness.
6. **Day 11 — Tool Calling**: the seams become **declarative** — `ToolRegistry`
   (schemas + hand-rolled validation + dispatch), the five tools
   (`search_saleor_docs`, `search_saleor_code`, `get_product/order/customer`),
   and `run_tool_answer` (one tool turn → one grounded answer). Function calling
   rides the Day-4 gateway as **one pass-through argument**; the refusal gate
   becomes a **return value**; and `cited ⊆ retrieved` moves home to where the
   evidence is produced.
7. **Day 12 — Agent Loop**: the single turn becomes a **loop** — reason → choose →
   execute → observe → reason again → answer — so a question several calls deep
   ("what happens after `CheckoutComplete` submits?") is answerable. Agent state is
   the **transcript** (no hidden variable), observations are the graded evidence
   record **per step**, and termination gains two exits beside the natural one
   (`max_steps`, budget) plus `gave_up`. `run_tool_answer` survives as
   `run_agent(max_steps=1)`, so Day 11's tests stay green by construction.
8. **Day 13 — Agent Failure Modes**: the loop's negative space becomes a
   **taxonomy with an owner and a guard each** — a wall-clock **timeout** on one
   tool call, a **state fingerprint** that names `no_progress`/`oscillation`
   *before* the step cap, a bounded, **disclosed** fallback ladder, and a
   contradiction detector; `fabricated? = no` on every row.
9. **Day 14 — Data Agent**: a third corpus behind the same harness — the
   **read-only** Saleor data seam (`run_saleor_sql`, `describe_saleor_schema`,
   fixed parameterized resolvers), the schema card + business glossary, and the
   invariant **`asserted ⊆ returned`**. The pipeline *is* the loop; the model
   generates SQL as a tool argument, so it stays visible and gradable.
10. **Day 15 — Safe Text-to-SQL**: the seed becomes a **safe SQL execution
    layer** — an ordered choke point (`normalize → parse → policy → cost →
    rewrite → execute`) with an **allow-list parser** (not a deny-list), a
    reviewable `policy.yaml` (table allow, PII column deny, enforced glossary),
    a shape-aware `LIMIT`, a **server-side** `statement_timeout`, and the day's
    sharpest idea, **two rejection classes**: a `repairable` error invites a
    fixed retry, a `policy` error **stops the run** and is never self-corrected.
11. **Day 16 — Agent Planning**: the loop gains the reflective counterpart to its
    reflex — **decide before acting**. A **planner** turns the question into a
    **validated plan** (a DAG of tool calls: `id`, `tool`, schema-valid `args`,
    `depends_on`, `expected`; fail-closed — registered tools · valid args ·
    acyclic · known deps), and an **executor** walks it tracking **execution
    state** and writing **checkpoints** (resumable at *k+1*, replayable), replanning
    when an observation contradicts the plan. The retry ladder gains one rung
    (**repair · retry · fallback · replan**, all bounded), **Day 15's rejection
    classes** decide which rungs a failure may climb (a `policy` wall climbs
    **none**), and the answer must satisfy **`conclusion ⊆ evidence`** — the plan's
    third translation of `cited ⊆ retrieved`. A one-step plan *is* the loop, so a
    trivial question is **not** over-planned.
12. **Day 17 — Memory and State**: Day 16's checkpoint gets a **home**. A layered,
    file-backed **store** (`working` / `conversation` / `task` state) with
    **provenance-mandatory** writes (a write with no author is rejected — the store
    is **closed**), **TTL/expiry** where an expired record *acts absent*, first-class
    **deletion** (`forget` purges the record *and* its derived index), the
    **size cap**, and **cross-process resume** (a new interpreter continues at *k+1*
    without re-running a completed step — safe because the tools are read-only). The
    headline is **`recalled ⊆ authored`** — the fourth translation of `cited ⊆
    retrieved` — and the control is **S9**: with state off, the system is Days 12–16
    byte-for-byte.
13. **Day 18 — Persistent Memory**: Day 17's store gets a **purpose**. A
    **relevance-scored episodic recall** turns the series' retrieval engine on the
    agent's *own* past — the *matching* investigation served above the most recent,
    cited **as an episode**, never absorbed as a lesson — and a **distiller** (one
    guarded chat) turns N agreeing episodes into one `kind=semantic` concept, minted
    only under the mint-time gates **sample-size ≥ 2 · `authored ⊆ grounded` · no
    source contradiction · no principal names**. Expiration becomes
    **relevance-governed** (a concept below its strength τ acts absent *before* its
    TTL; the clock is the backstop) with a **re-prompt loop** (re-distill · decay ·
    retire), **`forget` re-grounds** every concept that cites a deleted episode, and
    *user preferences* are honestly a **data table, not a memory**. The headline is
    **`authored ⊆ grounded`** — recalling Day 17's `recalled ⊆ authored` — and the
    control is **M8**: memory off ≡ Days 12–17, and memory on over an empty store too.
    The recall path stays free (**0 chats, 0 embeds**); the distiller is the series'
    first billable shelf (**1 chat per batch**), metered, not assumed.
14. **Day 19 — Multi-Agent Architecture**: *decide whether more than one loop is
    worth it* rather than assume. Two topologies over Days 12–18: **A** (the single
    loop + a transparent role-context router) is byte-for-byte the prior days with
    routing off; **B** an orchestrator whose tools are `make_agent(role)`
    specialists, sharing Day 18's store under a write policy (specialists write
    episodes; the orchestrator writes **only** `kind=orchestration`). The receipt
    contract (**`delegated ⊆ returned`** — a paraphrase/ghost delegation is rejected
    by code) and **failure as a return value** (`failed(error:…)`/`failed(timeout)`)
    make the seam honest; the stub ties A on coverage and costs more, so the table
    forces the verdict **`justified architecture = SINGLE`**.
15. **Day 20 — Evaluation Fundamentals**: *stop judging AI quality manually*. The
    **golden dataset** (the five-field, hash-pinned, pre-registered row — with
    `expected_answer` as `claims[] + must_not_assert[]`, oracle-derivable), the
    **layered judge** (`ai_os/judge.py`: layer 1 deterministic, layer 2 semantic
    *slot*, layer 3 LLM-as-judge off by default), the **harness** (a router that
    never touches the answer path), and the **regression gate**
    (`tools/regress_all.py`). The invariant is **`judged ⊆ pre-registered`** and the
    control is **V9**: the harness installed and unused changes nothing.
16. **Day 21 — RAG Benchmark**: *measure retrieval*, don't report it. The metric
    core `ai_os/rag_bench.py` makes **precision@K** writable (over a pre-registered
    `relevant_also` set — the golden set is the relevance oracle), inherits
    **recall@K/MRR** verbatim, and reports **retrieval relevance** as Day 9's free
    `rel()`; `ai_os/semantic_judge.py` fills Day 20's parked cells as
    **claim-coverage over the run's own evidence** (citation support · groundedness ·
    answer relevance). `tools/bench_rag.py` runs the **R-report** on the stub / real
    doc store / code store, calibrates **τ** on real scores (R8), and hosts the
    **R1–R9** fixture set with the **R9 control** (the bench is not a fixer). All
    `MECH`, **0 chats**.
17. **Day 22 — Agent Benchmark**: *measure agent behaviour*, don't watch it. Six
    of the eight curriculum columns already lived in `run_agent`'s dict (task
    success · tool selection · argument correctness · steps · cost · latency); the
    metric core `ai_os/agent_bench.py` prints them and defines the two that never
    had arithmetic — **unnecessary calls** (the pre-registered `necessary_calls`
    envelope · the U1/U2/U3 rules · the owned `undecided` bucket) and **failure
    recovery** (progress-after-failure · `fabricated = false` the gate ·
    `gave_up` the honest zero). `tools/bench_agent.py` runs the **A-report** in two
    labelled worlds (stub / real), pins `data/agent-baseline.json`, and hosts the
    **AB1–AB9** fixture set with the **AB9 control**. All `MECH`, **0 chats** — the
    judge reads one run dict and never re-executes the run.

```
Application
     |
     v
AI Layer (ModelGateway)        retries, fallback, budget, ledger
     |
  +--+--+
  |     |
OpenAI Anthropic              chat (+ OpenAI embeddings), tools passed through
     |
Tool layer                    schemas → model proposes → validate → execute
     |
RAG loop / code RAG           docs → chunk / symbols → retrieve → evidence
```

Same interface across chat and embeddings, two real providers, zero SDKs —
raw HTTP via `urllib`.

## Files

| File | What it is |
|---|---|
| `ai_os/contract.py` | `Usage`, `Completion`, `Embedding`, 5-class error taxonomy |
| `ai_os/providers/openai.py` | OpenAI wire format (`/v1/chat/completions`, `/v1/embeddings`) |
| `ai_os/providers/anthropic.py` | Anthropic wire format (`/v1/messages`, `system` top-level); no embeddings |
| `ai_os/gateway.py` | chat **and** `embed()` — retries, backoff+jitter, `Retry-After`, budget, fallback chain, ledger |
| `ai_os/ledger.py` | token accounting + cost per model; one `CallRecord` per attempt |
| `ai_os/config.py` | `build_gateway_from_env()` — everything is env config |
| `ai_os/search.py` | day 5: `Index`, document scoring, `dot`/`normalize` |
| `ai_os/chunk.py` | day 6: `chunk_text()` with overlap + tail grace + guards |
| `ai_os/ingest.py` | day 6: fetch/parse Saleor `llms.txt` docs → `Document`s |
| `ai_os/rag.py` | day 6: `RagStore` (persistent, dimension-locked), retrieval, `build_context` (budget floor), `run_rag`; day 10: channels optional code metadata + `code_store_path_from_env` |
| `ai_os/ingest_code.py` | day 10: `SymbolExtractor` (stdlib `ast`) + `ingest_code` — the code ingestion seam |
| `ai_os/code_questions.yaml` | day 10: the pre-registered code fixtures (CA1–CE1), asserting `module#Symbol` sets |
| `ai_os/server.py` | FastAPI: `/chat`, `/chat/stream`, `/rag` (`corpus="docs"｜"code"`), `/ledger`, `/health` |
| `ai_os/tools.py` | day 11: `ToolRegistry` (schemas/validation/dispatch), `ToolResult`, the five tools, the hand-rolled JSON-Schema subset |
| `ai_os/sql_safety.py` | day 15: the safe SQL choke point — `normalize`, the allow-list `parse_readonly` grammar, `Policy` (from `policy.yaml`), the cost pre-check, the shape-aware `rewrite_limit`, and `classify()` (repairable vs policy) |
| `ai_os/planner.py` | day 16: `Step`/`Plan`, `parse_plan` + fail-closed `validate_plan` (registered tools · schema args · acyclic · known deps), `topological_order`, `estimate_cost`, the `needs_plan` entry rule, and the model calls `plan`/`replan`/`repair_args` |
| `ai_os/executor.py` | day 16: `run_plan` — the state machine that walks a plan through Day 12's atoms, tracking execution state + **checkpoints** (resume at *k+1*), the re-plan gate, the bounded ladder (repair · retry · fallback · replan), the **policy wall**, and `conclusion ⊆ evidence`; day 17: an optional `state_store` flushes **task state** to a shelf (`put("/task/{id}", …)`) and `resume_from_store` continues a run in a **new process** |
| `ai_os/state.py` | day 16: `StepRecord`/`Checkpoint`/`ExecutionState` — the **ephemeral** execution state and the checkpoint format that round-trips; **day 17**: the persistent **store** — `StateRecord` + `StateStore` (`put`/`get`/`list_for`/`recall`/`delete`/`forget`/`expire`/`compact`/`round_trip`), the closed, provenance-enforced write path, TTL/expiry, and the key/wired helpers for working/conversation/task state; **day 18**: the memory shelves (`episodic`/`semantic`/`preference`) + their keys, the `strength`/`recall_threshold` meter (`is_decayed`/`is_served`) and `touch`, `forget`'s **re-grounding** of every concept citing a deleted episode, and the `distiller` as an authorized author |
| `ai_os/memory.py` | day 18: episodic memory — `episodicize` (the compact signature), `recall` (Day 8/9's scorer over the principal's episodes: lexicon + fusion + MMR/dedup + Day 7's τ + Day 6's top-k, **0 chats / 0 embeds**), `recall_block` (episodes **as** episodes — cite, never absorb), and the principal-keyed **preference data table** (`set_preference`/`read_preferences`) |
| `ai_os/distill.py` | day 18: the distiller — one guarded chat over N episodes, the **mint-time gate** (`validate_concept`: sample size ≥ `MEMORY_MIN_SOURCES` · `authored ⊆ grounded` over the union of source evidence · no source contradiction · no principal names), `mint` (writes `kind=semantic` with provenance through the closed path), and the re-prompt verbs (`decay_concept` / `retire_concept` / `reprompt_action`) |
| `policy.yaml` | day 15: the reviewable allow/deny document — statement heads, `tables_allow`, PII `columns_deny`, the enforced `definitions`, and the resource caps (all config, not code) |
| `ai_os/receipt.py` | day 19: the delegation contract — `Receipt` (`{task_id, principal, role, question_asked, evidence, conclusion, provenance, status}`), **`check_delegated_subset_returned`** (a fact attributed but absent is a handoff fabrication), `check_per_hop_grounding`, `receipt_block` |
| `ai_os/orchestrator.py` | day 19: the delegation loop — `[delegate(role, question)]` as the tool list, the rule-table plan/`detect_surfaces`, bounded delegation (`MAX_DELEGATIONS`/`HANDOFF_TIMEOUT`), failure-as-a-return-value, the union-context ledger |
| `ai_os/eval_dataset.py` | day 20: the golden-row parser/validator — the five fields, **oracle-derivability** (`claim_derivable`), the content hash + stale-hash refusal, and `rows_registered_before`; day 21: the **length-guarded** stem matcher (`checkout` never licenses `checkoutCreate`) |
| `ai_os/golden.yaml` | day 20/21: the pre-registered, hash-pinned dataset — the doc nine + code six + the adversarial F/G/H, plus Day 21's `relevant_also` (precision's relevance oracle), `human_review` (I1) and `semantic_fail` (K1) and the I1/J1/K1 rows |
| `ai_os/judge.py` | day 20: the layered grader — layer 1 deterministic (`MECH`, 0 chats), layer 2 the semantic *slot*, layer 3 LLM-as-judge **off by default** (explains, never decides); **`judged ⊆ pre-registered`** |
| `ai_os/rag_bench.py` | day 21: the metric core — `precision_at_k` over `expected ∪ relevant_also` (honest `n/a` when undefined), `retrieval_relevance` (mean free `rel()`), `retrieve_bench` (recall@K/MRR via `trace.expected_stats`), `tau_sweep`, `control_agrees` (R9) |
| `ai_os/semantic_judge.py` | day 21: layer 2 of `judge.py` — citation support · groundedness · answer relevance as **claim-coverage over the run's own evidence**; the deterministic subnet + the flagged per-claim lane (`EVAL_JUDGE=model`, off) |
| `tools/eval_harness.py` | day 20: the evaluation harness (a **router, not a grader**) + the **V1–V9** evaluation set |
| `tools/bench_rag.py` | day 21: the RAG benchmark runner — the R-report, `--stub`/`--real [--source doc\|code]`, `--k`, `--tau-sweep`, `--baseline`, and the **R1–R9** fixtures |
| `ai_os/agent_bench.py` | day 22: the agent bench metric core — every column a pure function of one `run_agent` dict (the judge never re-executes): `task_success` · `tool_selection` (TSR@1/coverage/misses) · `argument_correctness` (+`arg_deviation`s) · `step_count` (the envelope) · `unnecessary_calls` (U1/U2/U3 + the owned `undecided`) · `necessary_call_coverage` (`necessary ⊆ made`) · `failure_recovery` (progress-after-failure, the `fabricated` gate) · `cost_row` · `latency_row` (per-step/tool attribution) · `attribute_failure` (retrieval vs agent debit) · `control_agrees` (AB9) |
| `tools/bench_agent.py` | day 22: the agent benchmark runner — the **A-report** (`--stub` / `--real`), `--baseline`, and the **AB1–AB9** fixtures; the scripted `BenchGateway` (the hostile rows: injected wrong-tool / bad-args / no-progress / refusal→pivot / hang) |
| `tools/regress_all.py` | day 20/21: Rule-2's regression gate — the retrieval families + every day suite (incl. the bench) + the golden rows, exactly as green |
| `ai_os/agent.py` | day 11/12: `run_tool_answer` (one tool turn → grounded answer) + `run_agent` — the loop: transcript-as-state, three exits + `gave_up`, per-step observations; `cited ⊆ retrieved` over the accumulated transcript; `REASONING_PROMPT` is the reasoning-turn system prompt; day 16: an optional `plan` delegates to `executor.run_plan` (a one-step plan *is* the loop); day 17: an optional `state` (+ `session_id`) persists **conversation state** per session/principal — prior turns load as headers, never a hidden variable; day 18: an optional `memory_store` runs the **recall pre-step** (scored episodic recall, cited as episodes), loads the **preference data table** as cited data, and at **run-end** indexes the finished run as an authored `kind=episodic` record (the deliverable's "finished run → episodic record" arrow) — with no store, or an empty shelf, the transcript is unchanged (M8), and `MEMORY_ENABLED=0` forces the subsystem off |
| `tools/mock_gateway.py` | scripted dual-provider stub `:8700`/`:8701` (chat, embeddings, **tool_calls** scenarios: `tc_d12_*`/`tc_d13_*`/`tc_d14_*`/`tc_d15_*`; day 16's `D16_PLAN_*` stored plans + `answer_d16_*`; day 18's distiller JSON — `D18_CONCEPT*` + `answer_d18_*`) |
| `tools/tool_smoke.py` | day 11: T1–T7 offline suite (selection, validation matrix, refusal, cap, dialect parity, no-regression) |
| `tools/sql_attack_smoke.py` | day 15: X1–X11 offline attack set (parser, policy, PII, cost, LIMIT, the two rejection classes) + the `Wrote? = no` invariant + the Days 11–14 no-regression re-run |
| `tools/plan_smoke.py` | day 16: P1–P9 offline suite (plan validation, execution state, checkpoints/resume, the re-plan gate, the ladder + policy wall, `conclusion ⊆ evidence` both ways, the P9 control) + the Days 11–15 no-regression re-run |
| `tools/state_smoke.py` | day 17: S1–S9 offline suite (working/conversation/task state, cross-process resume, serialize/replay, TTL/expiry/compaction, first-class deletion, the closed write path, the boundary, the S9 off-switch) + **`recalled ⊆ authored`** + the Days 7–16 no-regression re-run |
| `tools/memory_smoke.py` | day 18: M1–M9 offline suite (scored episodic recall vs recency, the distiller's mint gates, semantic expiry before the clock, `forget` re-grounding, privacy/preferences-as-data, the run-end episodic index, the M8 off/empty-store control) + **`recalled ⊆ authored`** and **`authored ⊆ grounded`** + the Days 7–17 no-regression re-run |
| `tools/agent_smoke.py` | day 12: A1–A8 offline suite (loop, three exits + `gave_up`, depth-1 = Day 11, parallel observations, grounding over the transcript, cost shape, T1–T7 re-run); `--live` measures real-model composition against the real gateway |
| `tools/gateway_smoke.py` | day 4 deliberate-failure suite + token economics |
| `tools/build_rag_store.py` | embed a `corpus/` of markdown → `data/store.json` |
| `tools/build_code_store.py` | day 10: embed a Saleor source tree → `data/code_store.json` (interface-only, resumable) |
| `tools/code_debug.py` | day 10: offline stub code corpus + identifier-hash gateway |
| `tools/retrieval_day10.py` | day 10: Doc-RAG vs Code-RAG runner (one harness, two datasets) |
| `tools/rag_smoke.py` | day 6 deliberate-failure suite for the RAG loop |
| `corpus/`, `data/store.json` | 6 real Saleor doc pages, committed store (250 chunks, stub vectors) |
| `test_search.py`, `test_chunk.py`, `test_rag.py`, `test_day10.py` | deterministic unit suites (no network) |

## Run it for real

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
# fill .env: OPENAI_API_KEY / ANTHROPIC_API_KEY / LLM_PROVIDERS (see .env.example)
uvicorn ai_os.server:app --port 8023
```

Chat — replies via the gateway:

```bash
curl -s localhost:8023/chat \
  -H 'content-type: application/json' \
  -d '{"messages":[{"role":"user","content":"What is Saleor?"}]}'
# → {message, model, finish_reason, usage, cost_usd, latency_ms, provider, attempts}
```

RAG — grounded answer over `data/store.json`:

```bash
curl -s localhost:8023/rag \
  -H 'content-type: application/json' \
  -d '{"question":"How do I create a checkout?"}'
# → {answer, refused, retrieval{k, candidates, model, cost_usd},
#    generation{...}, citations[{doc_id, heading, position, chunk_id, score}], cost_usd}
```

Code RAG — the same endpoint, the *second* corpus, cited `module#Symbol:line`:

```bash
curl -s localhost:8023/rag \
  -H 'content-type: application/json' \
  -d '{"question":"Where is checkout completed?","corpus":"code"}'
# → citations[{doc_id:"saleor/checkout/complete_checkout.py#complete_checkout",
#              symbol, kind, line, score}, ...]
```

To build the stores for real (needs a working embeddings key in `.env`). Pin
`LLM_PROVIDERS` so a store never mixes embedding models — the code store's build
is resumable and, on Cloudflare's free tier (10k neurons/day ≈ 16k symbols), the
full Saleor tree simply takes more than one day:

```bash
python tools/build_rag_store.py  --corpus corpus               --out data/store.json
LLM_PROVIDERS=cloudflare: python tools/build_code_store.py --corpus corpus_code/saleor \
    --out data/code_store.json --batch 100                    # resume to finish
```

## Run it locally — no API key, deterministic failures

```bash
python tools/gateway_smoke.py     # day 4: spawns the stub, all 13 scenarios
python tools/rag_smoke.py         # day 6: retrieves, budgets, retries, fallbacks
python test_chunk.py && python test_search.py && python test_rag.py
python test_day7.py && python test_day8.py && python test_day9.py && python test_day10.py
python tools/rag_debug.py --stub        # day 7 offline mechanics
python tools/retrieval_day9.py --stub   # day 9 evidence pipeline (ΣΔMRR +1.000)
python tools/retrieval_day10.py --stub  # day 10 doc-vs-code (code 6/6, doc 9/9)
python tools/tool_smoke.py              # day 11 T1–T7 (tool calls, validation, cap)
python tools/agent_smoke.py             # day 12 A1–A8 (loop, exits + gave_up, depth-1, grounding over transcript)
python tools/failure_smoke.py           # day 13 F1–F9 (guards, the failure taxonomy, fabricated? = no)
python tools/data_smoke.py              # day 14 Q1–Q8 (read-only guards + asserted ⊆ returned)
python tools/sql_attack_smoke.py        # day 15 X1–X11 (attack set + control; Wrote? = no) + the Days 11–14 re-run
python tools/plan_smoke.py              # day 16 P1–P9 (plan validation · state · checkpoints · ladder · policy wall; conclusion ⊆ evidence) + the Days 11–15 re-run
python tools/state_smoke.py             # day 17 S1–S9 (the store · provenance · expiry/deletion · cross-process resume · the S9 control) + the Days 7–16 re-run
python tools/memory_smoke.py            # day 18 M1–M9 (scored episodic recall · the distiller's mint gates · semantic expiry · the M8 off/empty-store control) + the Days 7–17 re-run
python tools/architecture_smoke.py      # day 19 G1–G9 (the two topologies · the receipt contract · containment · the union-context ledger · the G9 control) + the Days 7–18 re-run
python tools/eval_harness.py --fixtures # day 20 V1–V9 (integrity · reproducibility · drift · the gate · the V9 control)
python tools/bench_rag.py --fixtures    # day 21 R1–R9 (recall@K · precision@K · MRR · relevance · the semantic subnet · τ · the R9 control), 0 chats
python tools/bench_rag.py --stub        # day 21 §9 R-report (stub: D1 precision 0.444; K1 fails support; I1 pending-human)
python tools/bench_agent.py --fixtures  # day 22 AB1–AB9 (task success · TSR@1 · args · steps · unnecessary · recovery · cost · latency · the AB9 control), 0 new chats
python tools/bench_agent.py --stub      # day 22 §9 A-report (the stub world; 21 rows, 21 verdicts)
python tools/regress_all.py             # day 20/21 Rule-2 gate: doc nine + code six + every day suite (7–21) + the golden rows, exactly as green
```

The stub serves both providers on `:8700` (OpenAI) and `:8701` (Anthropic)
and can be programmed to return 200 / 429 / 5xx / 401 / 400 / timeouts /
malformed JSON / mid-SSE cuts / empty-content 200s — and now hand-rolled
embeddings whose vectors are sums of a small theme vocabulary **and scripted
`tool_calls` scenarios** (`tc_code`, `tc_bad_args`, `tc_unknown_tool`,
`tc_two`, `tc_malformed`, …) so the tool layer is testable with zero keys:

```bash
python tools/mock_gateway.py &    # both ports on 127.0.0.1:8700-8701
python tools/rag_smoke.py
```

Day 6 smoke suite verifies (all mechanics, no real network):

| Scenario | Verified behavior |
|---|---|
| answer in docs | grounded answer, citations = retrieved chunks, separate retrieval cost |
| question not in corpus | nearest neighbours returned anyway (semantic refusal is Day 7) |
| answer spanning two chunks | overlap keeps the seam fact in both neighbours; K>1 retrieves both |
| empty corpus | honest refusal, no chat call at all |
| empty query | rejected up front |
| K beyond corpus / budget below one chunk | clamped / empty-safe refusal |
| embed 500 → 200 | retried through the gateway (Day 4 rule applied to embeddings) |
| embed 401 | never retried — `InvalidKeyError` |
| chat 500 via `/rag` | falls back to the anthropic provider |
| citation trace | cited ⊆ retrieved, with doc_id + score |
| ledger | retrieval billed under the embed model ≠ generation bills |
| identical texts | identical vectors; self-similarity 1.0, cross-theme lower |
| lost-in-the-middle | all 8 candidates reach the prompt in retrieval order |

Day 11 tool suite (`tools/tool_smoke.py`, T1–T7, offline):

| Fixture | Verified behavior |
|---|---|
| T1 tool selection | `search_saleor_code`; evidence = `complete_checkout` + `CheckoutComplete` (split-brain double-hit), all ⊆ store |
| T2 selection by corpus | `search_saleor_docs`; every result ⊆ the doc store |
| T3 data tool | `get_order(id="1")` valid; real resolver returns order 1001 / UNFULFILLED, **no** `pending_resolver` (Day 14 fills the stub) |
| T4 argument matrix | missing / wrong-enum / unknown-key / malformed-JSON each rejected **by name**, no crash |
| T5 unknown tool | `get_invoice` → rejection result (never an exception) |
| T6 refusal | out-of-corpus → `refused`, empty evidence; a restated refusal is flagged; a fabricated `module#Symbol` is caught |
| T7 single-turn cap | two `tool_calls` → one executed, `loop_deferred: day12` recorded |
| dialect parity | the same turn over Anthropic's `tool_use`/`tool_result` wire |
| regression | doc nine 9/9, code six 6/6 through the tools (same `retrieve()`) |

Day 12 agent suite (`tools/agent_smoke.py`, A1–A8 + exits, offline):

| Fixture | Verified behavior |
|---|---|
| A1 multi-step | 2 `search_saleor_code` steps; step 2's query names the symbol step 1's evidence carried; answer cites from ≥2 observations |
| A2 self-correction | step 1 rejected (enum violation named), step 2 fixed → 1 rejection + 1 success |
| A3 pivot after refusal | code tool refuses → step 2 legally switches to `search_saleor_docs`; no symbol fabricated |
| A4 parallel calls | two `tool_calls` one step → both executed, both observed |
| A5 termination | `max_steps` → `truncated`, answer names the limit; `budget_usd` → exit 3 |
| A6 grounding | `cited ⊆ ∪(step observations)`; a `ghost#Symbol` outside every observation is caught |
| A7 zero-tool | no `tool_calls` → `tool_steps: 0`, one turn |
| A8 parallel capacity | three calls one step → all validated/executed/observed, none dropped |
| gave_up | `self_correct=1`, repeat the bad call → bound reached, answer names the stuck point |
| cost | `chats == tool_steps + 1`, `embeds == tool_steps`, retrieval gateway chat calls `0` |
| depth-1 | `run_agent(max_steps=1)` *is* Day 11 — natural `answer` exit, `turns: 2`, no `max_steps` note |
| regression | `tools/tool_smoke.py` re-run unchanged (T1–T7) |

Both suites also run **live** — `python tools/tool_smoke.py --live` (Day 11 tool
*choice*) and `python tools/agent_smoke.py --live` (Day 12 multi-step *composition*).
Live swaps only the *chat* wire for the real gateway from `.env`; retrieval stays on
the deterministic stub stores. The harness contract (valid args, `cited ⊆ ∪
observations`, a real exit) is hard-checked; the semantic cells are measured per run.
With the reasoning prompt in place the free `nvidia/nemotron-3-super-120b-a12b:free`
model **follows evidence-carried symbols** (`complete_checkout` →
`create_order_from_checkout` → `process_payment`); a provider outage reports `[SKIP]`
and exits 2 rather than a false FAIL.

## Config (via env, never committed)

| Var | Default | Meaning |
|---|---|---|
| `LLM_PROVIDERS` | `openai:gpt-4o-mini,anthropic:claude-3-5-haiku-latest` | ordered fallback chain `provider:model` |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | — | provider keys |
| `OPENAI_BASE_URL` / `ANTHROPIC_BASE_URL` | official APIs | point at vLLM/Ollama/stub |
| `LLM_MAX_ATTEMPTS` | `3` | retries per provider per call |
| `LLM_TIMEOUT_S` | `60` | per-request socket timeout |
| `LLM_BASE_DELAY_S` | `1.0` | first backoff (grows `×2` + jitter) |
| `LLM_MAX_DELAY_S` | `30.0` | backoff cap (`Retry-After` wins when present) |
| `LLM_TOTAL_BUDGET_S` | `0` | wall-clock budget for the whole chain; `0` = unlimited |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | OpenAI embeddings model (token-priced in the ledger) |
| `RAG_STORE_PATH` | `data/store.json` | where `POST /rag` loads the store from |
| `TOOL_GATE_TAU` | `0.55` | the fused-score gate behind the retrieval tools |
| `TOOL_TOP_K` | `4` | default `k` when the model does not name one |
| `TOOL_MAX_TURNS` | `1` | day 11's per-step call cap (day 12's `run_tool_answer`) |
| `AGENT_MAX_STEPS` | `6` | day 12: the loop's step cap (exit 2) |
| `AGENT_BUDGET_CHARS` | `4000` | day 12: per-observation clamp (day 11's `max_chars`, per step) |
| `AGENT_SELF_CORRECT` | `2` | day 12: re-issues allowed after a rejection, per question |
| `AGENT_BUDGET_USD` | unset (`0`) | day 12: run-level cost cap → exit 3 |
| `SALEOR_READONLY_DB_URL` | unset | day 14: the `saleor_read_only` DSN; unset → the offline seeded sqlite stub |
| `SALEOR_SQL_ROW_LIMIT` | `100` | day 14: rows per data query (truncation is reported) |
| `SALEOR_SQL_SCHEMA_BUDGET` | `4000` | day 14: chars of schema card (budget-clamped, cut marked) |
| `SALEOR_SQL_POLICY` | `policy.yaml` | day 15: the allow/deny document the safe SQL layer enforces |
| `AGENT_MAX_REPLANS` | `1` | day 16: total replans per run (the ladder's rung 4) |
| `AGENT_MAX_REPAIRS` | `1` | day 16: repairs per step (the ladder's rung 1) |
| `STATE_STORE_PATH` | unset | day 17: the file-backed store's path; unset → no persistence on disk |
| `STATE_DEFAULT_TTL` | `86400` | day 17: TTL a `ttl="inherit"` write receives (24h) |
| `STATE_MAX_RECORDS` | `1000` | day 17: the size cap (oldest-first eviction) |
| `STATE_PRINCIPAL` | `default` | day 17: the owner used when a run names none |
| `MEMORY_ENABLED` | `1` | day 18: the memory switch; `0`/`off` → recall serves nothing (M8) |
| `MEMORY_RECALL_TOP_K` | `5` | day 18: the served recall set's size (Day 6's budget floor) |
| `MEMORY_RECALL_TAU` | `0.05` | day 18: Day 7's gate, on the memory shelf — below it, absent |
| `MEMORY_MIN_SOURCES` | `2` | day 18: the distiller's sample-size rule (a receipt is not a lesson) |
| `MEMORY_DISTILL_TTL` | `604800` | day 18: a concept's clock backstop (7d; relevance is the policy) |
| `MEMORY_STRENGTH_DECAY` | `0.5` | day 18: the re-prompt loop's decay step (`strength × (1 − rate)`) |

Omitted keys are a startup error in `build_gateway_from_env` (fail fast, not a
runtime 401 surprise). Embedding model is passed per-provider, so the key lives
with the provider config it belongs to.

## API

- `GET /health` → `{ok, providers[], max_attempts, timeout_s, ...}`
- `POST /chat` — `{messages, temperature, max_tokens}` → `{message, model, finish_reason, usage, cost_usd, latency_ms, provider, attempts}`
- `POST /chat/stream` — same body, SSE events; first chunk carries `ttft_ms`
- `POST /rag` — `{question, k?, budget_chars?, corpus?}` → answer, `refused`, retrieval/generation cost split, citations (`corpus="docs"` default, `"code"` for symbols)
- `GET /ledger` → aggregates: records, ok/failed, tokens, `cost_usd`, by_model, by_day

`usage` is the normalized `{input_tokens, output_tokens, total_tokens}` shape.
`/ledger` counts failed attempts too — retries are not free, and now visible.

## Notes

- Day 3 single-provider client still lives at `ai_os/llm_client.py` (regression-safe).
- Day 5 hand-rolled search lives at `ai_os/search.py` — the RAG loop uses it.
- Day 5/Day 6 notes: `saleor-platform/docs/day-plan/day-5-notes.md`, `day-6.md`, `day-6-notes.md`
- Concept docs: `saleor-platform/docs/concepts/model-gateway.md` (+ rag / vector-search concepts).
- The committed `data/store.json` was built with **stub** vectors (dim 8) so tests
  are deterministic; rebuild it with a real key before trusting answers.
- Day 10 adds the code corpus behind the *same* seams: `ai_os/ingest_code.py`
  (stdlib `ast` symbol extraction), `tools/build_code_store.py` (interface-only,
  resumable), `tools/retrieval_day10.py` (doc-vs-code). See
  `docs/concepts/code-rag.md` and `docs/evaluations/code-day10.md`.
- Day 11 adds the action layer: `ai_os/tools.py` (schemas + validation +
  dispatch), `ai_os/agent.py` (`run_tool_answer`), the providers' tool wire,
  and `gateway.chat_with_tools` (one pass-through argument). See
  `docs/concepts/tool-calling.md` and `docs/evaluations/tool-day11.md`. A
  tool-turn chat is a chat call, so it bills like one; tool execution is free
  arithmetic. The real model's *choice* of tool is Day 21's measure.
- Day 12 adds the loop: `run_agent` generalizes `run_tool_answer` (which is now
  `run_agent(max_steps=1)`) — transcript-as-state, three exits + `gave_up`,
  parallel observations, and `cited ⊆ retrieved` over the accumulated transcript.
  The loop asks once more after the last allowed tool step, so `max_steps=1` takes
  Day 11's *natural* answer exit (never a false "step limit" note); a completion
  that still asks for tools at the cap is recorded, not executed. `REASONING_PROMPT`
  precedes every reasoning turn (observations are evidence for the *next* step). See
  `docs/concepts/agent-loop.md` and `docs/evaluations/agent-day12.md`. The real
  model's *composition* of a multi-step investigation is Day 21's measure, now run
  per request via `python tools/agent_smoke.py --live` — the free model walks the
  map (`complete_checkout` → `create_order_from_checkout` → `process_payment`).
- Day 14 adds the data corpus: `ai_os/data.py` (read-only seam — `SqliteDataStore`
  seeded stub / lazy `PostgresDataStore`; the schema card + business glossary; the
  `SELECT`/`WITH`-only validator seed; the parameterized resolvers), plus
  `run_saleor_sql` / `describe_saleor_schema` in the registry and the data columns
  in the trace. The pipeline **is** the loop: the model generates SQL as a tool
  argument, so it stays visible (repairable, gradable — Day 15's target), and the
  final answer's numbers must be in the result set (`asserted ⊆ returned`). See
  `docs/concepts/text-to-sql.md`, `docs/evaluations/data-day14.md`, and
  `python tools/data_smoke.py` (Q1–Q8: the two read-only guards, the schema card,
  measured truncation, and the invariant proven both ways). A data answer costs
  **0 embeds + 2 chats** — exact set logic, no vectors.
- Day 15 adds the safe SQL execution layer: `ai_os/sql_safety.py` (the ordered
  choke point — `normalize`, an **allow-list** `parse_readonly` grammar, `Policy`,
  the cost pre-check, the shape-aware `LIMIT`, and `classify()`), `policy.yaml`
  (the allow/deny document), the **server-side** `statement_timeout` +
  lock/idle timeouts on the Postgres seam, and the **two rejection classes**
  (`repairable` → the loop may fix it; `policy` → `policy_violations`, the run
  stops, no self-correction). The guarantee is still the `saleor_read_only`
  **role**; the parser/policy/cost guards are defense-in-depth. See
  `docs/concepts/safe-sql.md`, `docs/evaluations/sql-safety-day15.md`, and
  `python tools/sql_attack_smoke.py` (X1–X11: `Wrote? = no` on every attack row,
  the X11 control passes, and Days 11–14 re-run unchanged). A rejected attack
  costs **0 DB execution, 0 chats, 0 embeds**.
- Day 16 adds planning: `ai_os/planner.py` (the `Plan`/`Step` data shape,
  **fail-closed** `validate_plan`, the deterministic `topological_order`, the
  `needs_plan` entry rule, and the model calls `plan`/`replan`/`repair_args`),
  `ai_os/executor.py` (`run_plan`: execution state + **checkpoints**, the
  **re-plan gate**, the bounded four-rung ladder, Day 15's classes deciding which
  rungs a failure may climb — a `policy` rejection is a **wall** that climbs
  none), and `ai_os/state.py` (the **ephemeral** state + the checkpoint format that
  round-trips). `run_agent(plan=…)` delegates; with no plan the loop is Day 12
  unchanged, so a **one-step plan is the identity case**. The headline is
  **`conclusion ⊆ evidence`** — an answer may assert only what the plan observed —
  and the counterweight is P9: a trivial question is **not** over-planned. See
  `docs/concepts/planning.md`, `docs/evaluations/planning-day16.md`, and
  `python tools/plan_smoke.py` (P1–P9, the fabricated twin the only `FAIL`). The
  real model's *plan quality* is **Day 21's** measure; durable state is **Day 17's**.
- Day 17 gives Day 16's checkpoint a home: `ai_os/state.py` grows the **store**
  (`StateRecord`/`StateStore` — a provenance-mandatory, **closed** write path,
  expiry-acts-absent, first-class `forget` + compaction, and the size cap),
  `ai_os/agent.py` persists **conversation state** per session/principal, and
  `ai_os/executor.py` flushes **task state** and can **resume in a new process** at
  step *k+1* (safe because the tools are read-only). The headline is
  **`recalled ⊆ authored`** — memory the system did not write is unreachable — and
  the control is **S9**: with state off the system is Days 12–16 byte-for-byte. See
  `docs/concepts/state-memory.md`, `docs/evaluations/state-day17.md`, and
  `python tools/state_smoke.py` (S1–S9). State costs **0 chats, 0 embeds** on the
  answer path; episodic recall and semantic distillation are **Day 18's**.
- Day 18 gives the store a **purpose**: `ai_os/memory.py` (**relevance-scored
  episodic recall** — Day 8/9's scorer over the agent's own past, the *matching*
  investigation served above the newest, cited **as an episode**; plus the
  principal-keyed **preference data table**), `ai_os/distill.py` (**the distiller** —
  one guarded chat, the mint-time gate **sample-size ≥ 2 · `authored ⊆ grounded` · no
  source contradiction · no principal names**, and the re-prompt verbs
  re-distill/decay/retire), the semantic `strength` meter + `forget`'s re-grounding in
  `ai_os/state.py`, and the recall pre-step in `ai_os/agent.py`. The headline is
  **`authored ⊆ grounded`** — recalling Day 17's **`recalled ⊆ authored`** — and the
  control is **M8**: memory off ≡ Days 12–17, and memory on over an empty store too.
  The recall path is **0 chats / 0 embeds**; the distiller is the series' first
  billable shelf (**1 chat per batch**). See `docs/concepts/memory.md`,
  `docs/evaluations/memory-day18.md`, and `python tools/memory_smoke.py` (M1–M9). One
  honest limit, named not hidden: **"you cannot un-distill"** — deletion ends *future*
  recall, not the past answers a concept was absorbed into.
- Day 19 answers "are more agents actually useful?" with a table, not a mood:
  `ai_os/receipt.py` (the delegation contract, **`delegated ⊆ returned`**),
  `ai_os/orchestrator.py` (the delegation loop + bounded hops + the union-context
  ledger), and the router/scoped-registry/`make_agent` seam in `ai_os/agent.py`.
  Under the stub the two-surface split ties the single loop on coverage and costs
  more, so the verdict is **`justified architecture = SINGLE`**. See
  `docs/concepts/multi-agent.md`, `docs/evaluations/architecture-day19.md`, and
  `python tools/architecture_smoke.py` (G1–G9). The control is **G9**: machinery
  off ≡ Days 12–18.
- Day 20 makes judgment re-runnable: `ai_os/eval_dataset.py` (the five-field,
  hash-pinned golden row + oracle-derivability), `ai_os/golden.yaml`,
  `ai_os/judge.py` (the layered grader), `tools/eval_harness.py` (a router, not a
  grader), and `tools/regress_all.py` (Rule-2's gate). The invariant is
  **`judged ⊆ pre-registered`**; the control is **V9**: the harness installed and
  unused changes nothing. See `docs/concepts/evaluation.md`,
  `docs/evaluations/eval-day20.md`, and `python tools/eval_harness.py --fixtures`.
- Day 21 measures retrieval: `ai_os/rag_bench.py` (the metric core — precision@K
  over a pre-registered `relevant_also`, plus recall@K/MRR and the free `rel()`
  relevance), `ai_os/semantic_judge.py` (Day 20's parked cells as claim-coverage
  over the run's own evidence), and `tools/bench_rag.py` (the R-report + the
  **R1–R9** fixtures + the τ calibration). On the real doc store the bench
  **indicts its own pipeline** — 4 of 7 answerable rows surface none of their
  expected docs at k=9 — and the τ sweep gives a hairline separation (**+0.011**,
  τ=0.794, 0 leaks), so the fitted gate ships but the durable refusal verdict
  stays the semantic tier. All `MECH`, **0 chats**. See
  `docs/concepts/rag-benchmark.md`, `docs/evaluations/bench-day21.md`, and
  `python tools/bench_rag.py --fixtures`. Real answer *quality* is the real-key
  handoff; agent behaviour is **Day 22's** benchmark.