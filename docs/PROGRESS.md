# Progress — ai-layer (Days 2–10)

> Live status of what is implemented, verified, and still open in this repo.
> Kept up to date as days are worked; the canonical per-day detail lives in
> `docs/day-plan/day-N-notes.md`.

Updated: 2026-09-25

## Status legend

- **DONE** — code exists, deterministic verification is green in this repo.
- **PARTIAL** — code exists but a documented part is `PENDING` (needs a real
  API key / real corpus / real model to finish).
- **NOTES ONLY** — the learning record exists; no implementation in this repo.

---

## Day-by-day rollup

| Day | Title | Status | Evidence |
|---|---|---|---|---|
| 2 | Understand Saleor as an AI environment | NOTES ONLY | `docs/day-plan/day-2-notes.md` |
| 3 | LLM fundamentals (stdlib client) | DONE | `ai_os/llm_client.py` |
| 4 | Model gateway (retries, fallback, ledger) | DONE | committed `35a1e72`; `tools/gateway_smoke.py` |
| 5 | Embeddings + brute-force vector search | DONE | `ai_os/search.py`, `test_search.py` |
| 6 | First RAG pipeline | PARTIAL (mechanics green; real store built, chat quota-blocked) | `chunk/ingest/rag`, `build_rag_store.py`, `rag_semantic_smoke.py`, `rag_smoke.py` |
| 7 | RAG debugging / failure report | DONE (MECH half; semantic cells `PENDING` until a real run) | `ai_os/gate.py`, `ai_os/trace.py`, `ai_os/oracle.py`, `ai_os/fixtures.py`, `ai_os/questions.yaml`, `tools/rag_debug.py`, `test_day7.py`, `docs/failures/rag-naive.md` |
| 8 | Retrieval improvements | PARTIAL (MECH green; semantic confirmation + τ recalibration `PENDING` real run) | `ai_os/lexicon.py`, `ai_os/retrieval.py`, `test_day8.py`, `tools/retrieval_day8.py`, `docs/concepts/retrieval.md`, `docs/evaluations/retrieval-day8*.md` |
| 9 | Reranking and context construction | PARTIAL (all MECH green; **real-corpus comparison made** — envelope-blocked at Day 8, C2 partial move, D2 regression; τ/LOM/cross-encoder verdicts `PENDING` Day 20/21) | `ai_os/rerank.py`, `ai_os/context.py`, `test_day9.py`, `tools/retrieval_day9.py`, `docs/concepts/reranking.md`, `docs/evaluations/rerank-day9.md` |
| 10 | Code RAG | PARTIAL (all MECH green offline; code fixtures 6/6 + doc nine 9/9 on the stub; real Saleor build + semantic `file:line` verdicts `PENDING` Day 21) | `ai_os/ingest_code.py`, `ai_os/code_questions.yaml`, `ai_os/retrieval.py` (code vocab), `ai_os/rag.py` (code metadata), `tools/build_code_store.py`, `tools/code_debug.py`, `tools/retrieval_day10.py`, `test_day10.py`, `docs/concepts/code-rag.md`, `docs/evaluations/code-day10.md` |
| 11 | Tool Calling | PARTIAL (all MECH green offline; T1–T7 + dialect parity + the Day-6 empty-content sibling; real model *tool choice* `PENDING` Day 21) | `ai_os/tools.py`, `ai_os/agent.py`, `ai_os/contract.py` (`ToolCall`), `ai_os/providers/{openai,anthropic}.py` (tool wire), `ai_os/gateway.py` (`chat_with_tools`), `tools/mock_gateway.py` (tc_* scenarios), `tools/tool_smoke.py`, `docs/concepts/tool-calling.md`, `docs/evaluations/tool-day11.md` |
| 12 | Agent Loop | PARTIAL (all MECH green offline; A1–A8 + the `gave_up` bound + the depth-1 line + the cost shape; `--live` real-model *composition* **verified** — the free model walks the map with the reasoning prompt; Day 21 rules cross-model strength) | `ai_os/agent.py` (`run_agent`, `REASONING_PROMPT`), `ai_os/trace.py` (`step_trace`, `grounding_over_transcript`), `tools/mock_gateway.py` (multi-step `tc_*`/`answer_*`), `tools/agent_smoke.py` (`--live`), `docs/concepts/agent-loop.md`, `docs/evaluations/agent-day12.md` |
| 13 | Agent Failure Modes | PARTIAL (all MECH green offline; F1–F9 with `fabricated? = no` on every row + the timeout wall on an injected clock + the fingerprint firing before the cap + the disclosed fallback ladder + the contradiction flag with its two negative twins; A1–A8 / T1–T7 re-run through the guarded loop; real-model *tripping* and contradiction *adjudication* `PENDING` Day 21, real latency `PENDING` Day 14) | `ai_os/tools.py` (`ToolError`, `error_kind` taxonomy, `REJECTION_KINDS`/`TOOL_ERROR_KINDS`), `ai_os/agent.py` (`execute_bounded`, the fingerprint, the fallback ladder, `_derive_failure_kind`), `ai_os/trace.py` (`fingerprint`/`detect_repeat`/`detect_oscillation`/`contradiction_rows`/`contradiction_coverage`), `tools/mock_gateway.py` (`tc_d13_*`), `tools/failure_smoke.py`, `docs/concepts/agent-failure-modes.md`, `docs/evaluations/failure-day13.md` |
| 14 | Data Agent | PARTIAL (all MECH green offline; Q1–Q8 with **`asserted ⊆ returned = ok` on every answered row** (the fabricated twin excepted) + the **two independent read-only guards** (validator rejects before execution; the engine refuses a bypassed write) + the schema card (DB schema, not `schema.graphql`) + row-limit/truncation + `no_data` as a finding + the `unknown_column` repair; A1–A8 / T1–T7 / F1–F9 re-run through the same harness; real-model *SQL generation + grounding* `PENDING` Day 21, real Postgres latency/schema reconciliation `PENDING` a live role) | `ai_os/data.py` (new), `ai_os/tools.py` (`run_saleor_sql`, `describe_saleor_schema`, real resolvers), `ai_os/trace.py`/`ai_os/agent.py` (data columns + the invariant), `tools/data_smoke.py`, `tools/mock_gateway.py` (`tc_d14_*`/`answer_d14_*`), `docs/concepts/text-to-sql.md`, `docs/evaluations/data-day14.md` |
| 15 | Safe Text-to-SQL | PARTIAL (all MECH green offline; X1–X11 with **`Wrote? = no` on every attack row** — the DB byte-for-byte unchanged — and **`X11 false-reject? = no`**; the allow-list parser (not a deny-list), the policy (table allow / PII column deny / enforced glossary), single-statement + homoglyph normalization, the **two rejection classes** with `policy_violations` stopping the run on a policy hit, the cost pre-check, the shape-aware `LIMIT`, and the *server-side* `statement_timeout` (proven offline by its mapping); Q1–Q8 / A1–A8 / T1–T7 / F1–F9 re-run unchanged through the hardened executor; **X10's real-model behaviour `PENDING` Day 25/26 + Day 21, parser soundness `PENDING` long-term hardening, real Postgres timeout/`EXPLAIN` `PENDING` a live role**) | `ai_os/sql_safety.py` (new), `policy.yaml` (new), `ai_os/data.py` (server timeouts + safety routing + `rejection_class`), `ai_os/tools.py` (`ToolResult.rejection_class`), `ai_os/trace.py` (safety columns), `ai_os/agent.py` (the policy stop), `tools/sql_attack_smoke.py` (new), `tools/mock_gateway.py` (`tc_d15_*`/`answer_d15_*`), `docs/concepts/safe-sql.md`, `docs/evaluations/sql-safety-day15.md` |
| 16 | Agent Planning | PARTIAL (all MECH green offline; P1–P9 with **`conclusion ⊆ evidence = ok` on every answered row** — the fabricated twin the only `FAIL`, and the point of the fixture — and **`over-planned? = no` on P9**; plan validation fail-closed (registered tools · schema args · acyclic · deps exist — an invalid plan runs **zero steps, zero bills**), execution state + **checkpoints** (resume at *k+1* without re-running a completed step; replay reproduces the evidence), the re-plan gate (divergence → replan), the bounded retry ladder (repair · retry · fallback · replan) with the **policy wall climbing no rung** (`rungs: []`), and the 6→7 gate; T1–T7 / A1–A8 / F1–F9 / Q1–Q8 / X1–X11 re-run unchanged; **real-model plan quality `PENDING` Day 21, durable state `PENDING` Day 17/18, parallel DAG branches named not built**) | `ai_os/planner.py` (new), `ai_os/executor.py` (new), `ai_os/state.py` (new), `ai_os/agent.py` (`run_agent(plan=…)`), `ai_os/trace.py` (plan columns + `PLAN_TERMINATIONS`), `tools/plan_smoke.py` (new), `tools/mock_gateway.py` (`D16_PLAN_*`/`answer_d16_*`), `docs/concepts/planning.md`, `docs/evaluations/planning-day16.md` |

---

## Day 4 — Model Gateway `DONE`

(committed `35a1e72`; Days 5–9 committed — HEAD is `d62acda`, the Day-9
evaluation rollup. The Day-9 knob additions (`--weights` / `--dedup-first`,
sweep focus) and the real-run records are the current uncommitted change.)

- `ai_os/contract.py`, `gateway.py`, `ledger.py`, `config.py`,
  `providers/openai.py`, `providers/anthropic.py`, `ai_os/server.py`
- `/chat`, `/chat/stream`, `/ledger`, `/health` endpoints
- Chat **and** embeddings through one gateway (retries, backoff+jitter,
  `Retry-After`, ordered fallback, per-call budget, ledger)
- Verified: `python tools/gateway_smoke.py` (13 scenarios) + token economics

## Day 5 — Embeddings & Vector Search `DONE`

- `ai_os/search.py`: hand-rolled `dot` / `normalize` / brute-force nearest
  neighbour; normalize-on-write fixes the zero-vector bug
- Verified: `python test_search.py` — all assertions pass (incl. scale
  invariance). No embedding *model* used — vectors are synthetic.

## Day 6 — First RAG Pipeline `PARTIAL`

### Implemented & verified (mechanics, deterministic, no API key)

- `ai_os/chunk.py` — 200/40 chunker, tail-grace bound, `overlap >= size`
  rejected. Verified: `python test_chunk.py` — 12 size/overlap combos + edges,
  all pass.
- `ai_os/ingest.py` — fetches `llms.txt` sections + markdown pages, keeps
  headings/prose/code, drops navigation cruft.
- `ai_os/rag.py` — `RagStore` (file-backed, dimension-locked), `retrieve`,
  `build_context` (rank-order budget floor), `run_rag` (empty-retrieval
  refusal by code, citations).
- `ai_os/server.py` — `POST /rag` endpoint wired to the gateway.
- Verified: `python test_rag.py` and `python tools/rag_smoke.py` — 11
  deliberate-failure scenarios (k/budget clamping, embed 500→retry, 401→no
  retry, chat fallback, refusal, citation ⊆ retrieved) all `ALL CHECKS PASSED`.
- `tools/build_rag_store.py` — **resumable, dimension-locked corpus builder**:
  per-chunk `chunk_id` resume (`{doc}#{position}`), checkpoint saves, live
  embed-dim mismatch → wipe-and-rebuild, `--pages` curated-subset mode,
  `--batch-chars` char-capped pacing for free tiers throttled on input volume
  (Gemini ~2k chars/min), 429/`Retry-After`/URLError-tolerant with bounded
  retries. Offline stub builds pass `--batch` only and never sleep.
- `tools/rag_semantic_smoke.py` — real-endpoint semantic suite: R1 retrieval
  ranking, R2 dimension-lock, R3 grounded answer + citations, R4 "not covered"
  refusal, R5 lost-in-the-middle, R6 ledger economics; chat checks SKIP
  cleanly when a provider's quota is exhausted.
- `ai_os/providers/openai.py` — a 200-with-empty-content completion is a
  retryable `ProviderError` (throttled OpenAI-compat endpoints do this).
- Verified: `python test_chunk.py && python test_search.py && python test_rag.py`
  and both smokes — all green offline (stub store below).

### Real corpus — DONE

- `corpus/` now holds **233 real Saleor pages** (`python tools/download_corpus.py`,
  1,878,766 chars across quickstart + developer sections).
- `data/store.json` is the **real store, built through Cloudflare Workers AI**:
  **11,061 / 11,156 chunks at dim 768**
  (`@cf/baai/bge-base-en-v1.5`, normalized) — see "Real embeddings & chat"
  below. It is dimension-locked and resumable, with retrieval verified live.
  The offline suites (which build their own mock stores) are unaffected.

### Open (`PENDING` — real-key run blocked by free tier today)

- **Real embeddings — Cloudflare Workers AI (2026-09-21):** swapped the
  OpenRouter free embed model for Cloudflare:
  `CLOUDFLARE_API_KEY` / `CLOUDFLARE_ACCOUNT_ID` with
  `EMBEDDING_MODEL=@cf/baai/bge-base-en-v1.5` (dim **768**), added
  `ai_os/providers/cloudflare.py` (embeddings-only provider; chat falls
  through to a chat-capable provider). `cloudflare:` listed first in
  `LLM_PROVIDERS`. Linearly verified live through the gateway (real vectors).
  The real store is now **11,061 / 11,156 chunks (dim 768, normalized)** —
  `data/store.json`.
  - **Resume** (same embed model, dim locked):
    ```bash
    python tools/build_rag_store.py --corpus corpus --out data/store.json --batch 100
    ```
  - **Finish semantics** once chat quota is available:
    ```bash
    python tools/rag_semantic_smoke.py --store data/store.json
    ```
  - Chat generation is still gone through the OpenRouter key.

- **Real chat — OpenRouter run (2026-09-21):** `LLM_PROVIDERS`
  `openai:nex-agi/nex-n2.5-pro:free,openai:nex-agi/nex-n2.5-mini:free`
  verified live through the gateway, but the key hit its **free-tier cap — 50
  free-model requests/day** (`X-RateLimit-Reset` ~daily; `OPENAI_EMBEDDING_MODEL`
  kept as the embed fallback if Cloudflare is unreachable).

## Day 7 — RAG Debugging `DONE (MECH half)`

Full learning record: `docs/day-plan/day-7-notes.md`. Now implemented, with
the deterministic (`MECH`) half verified offline — the semantic cells stay
`PENDING` because they need the real gateway (see Known gaps).

- `ai_os/gate.py` — `should_refuse(results, τ)`: refusal as a code decision
  (`MECH`), `score_gap` (top1 − topK) as the ambiguity fingerprint.
- `ai_os/trace.py` — per-query trace: retrieved top-K (doc/heading/score/
  rank), expected-set membership, recall@K, MRR, duplicate ratio/count,
  context utilization, gate decision; `retrieve_trace` (no LLM) and
  `run_trace` (same retrieval + the run's answer, `cited ⊆ retrieved` by
  construction); `classify_trace` = the §4 decision tree (verdict per layer).
- `ai_os/oracle.py` — the oracle substitution isolation test (inject the
  expected docs as the only context; cost: one chat call).
- `ai_os/questions.yaml` — the **nine pre-registered** questions (A–E),
  expected doc ids written before any run, with predicted class + fix routing.
- `ai_os/fixtures.py` — the model-free regression set: `check_fixture`
  (expected ⊆ retrieved, `must_not`, `must_refuse` under τ), `run_fixtures`,
  YAML loader.
- `tools/rag_debug.py` — the runner: `--stub` (offline mechanics; 6/9
  fixtures green, C1/E near-miss rows correctly `PENDING`) or real mode
  (`--store data/store.json`; chat columns skip when quota is spent).
- `docs/failures/rag-naive.md` — the filled failure report: hypothesis /
  decision-tree / report rows (run cells `⟨run⟩`, not invented) / gate /
  fixtures / shortlist + exit-check answers.
- Verified: `python test_day7.py` — **36 MECH checks pass** (gate math,
  duplicate/rank metrics, all verdict-tree branches, budget-floor → context,
  empty-context refusal, fixture mechanics, oracle membership/budget guards).

## Day 8 — Retrieval Improvements `PARTIAL`

Day 8's fix list is the `Proposed fix` column of `docs/failures/rag-naive.md`
§7 (shortlist) — now implemented with the MECH half verified offline:
`docs/concepts/retrieval.md` (concept), `docs/evaluations/retrieval-day8*.md`
(run records).

- `ai_os/lexicon.py` — deterministic keyword scorer: `STOP`, `tokenize()`,
  `Lexicon.idf` (smoothed, built once at construct), `score`/`normalized`
  (set-membership saturation at both ends, corpus-max normalization → [0, 1],
  all-zero for out-of-corpus queries).
- `ai_os/retrieval.py` — `Retriever`; `rewrite()` (deterministic rule table for
  the token-gap families), `decompose()` (coordinated multi-intent split on
  `and/then/but/or`, capped at 3), `rule_section()` (metadata filter that only
  fires when the query names *one* section; `SECTION_PREFIXES` covers every
  live folder including `quickstart/api`), `fuse_score(α, semantic, lexical)`
  (α-weighted linear fusion), max-score per-chunk merge + dedup, Day-6
  tie-breaks preserved for `mode="semantic"`. Retrieval-only — zero chat.
- `tools/retrieval_day8.py` — baseline-vs-improved runner: `--stub` (offline
  mechanics) or `--store data/store.json`; `--k`, `--tau`, `--alpha`, `--sweep`
  (α set recorded per setting), `--no-rewrite` (isolates the filter as the
  C1 fix), `--report PATH`.
- `test_day8.py` — **29 MECH checks pass**: lexicon math/saturation,
  rewrite/decompose/rule rules, lexical-gap fix (baseline picks refund doc;
  hybrid + pure lexical + tied-α-average all pick the `orderCancel` doc),
  multiquery both-intents + embed ledger, explicit-filter-wins + filter-miss
  annotation, gate-survives-fusion, mode-semantic ≡ Day-6 seam, and the
  nine-fixture regression (baseline still fails C1; improved keeps 7/7 green,
  red→green exactly `["C1"]`, moves-alone keeps A1).
- Stub run (k=5, τ=0.05): recall 1.0 either way; MRR 0.6667 → **0.6905**
  (α=0.5); C1 red→green; E-family still refuses **0/2** → τ recalibration onto
  the fused score is `PENDING` (needs a real gateway, recorded in the eval doc,
  not papered over).
- `--no-rewrite` isolates the attribution: without the section filter C1 fails
  again — the *filter* fixed C1, not fusion alone.

## Day 9 — Reranking and Context Construction `PARTIAL`

Day 9 owns the second half Day 8 deferred: given the right chunks in top-K,
decide *which wins, where it sits, and what survives the budget*. All MECH
green offline; the **real-corpus comparison is now made** (11,061 Saleor
chunks) — the findings land loudly in Day 8's backlog. Semantic verdicts
(answer correctness per slot, τ on the fused score, the cross-encoder
paid-vs-free question) still need Day 20/21. Concept + learning record:
`docs/concepts/reranking.md`, `docs/day-plan/day-9-notes.md`.

- `ai_os/rerank.py` — pointwise reranker, two seams kept narrow:
  `rel(c) = w_fused·fused + w_rare·rare + w_heading·heading + w_pos·pos`
  (defaults 0.5/0.25/0.2/0.05), candidates in `[(score, Chunk)]` out,
  `delta_ranks()` per-doc pre→post rank. `_match` one-way substring with
  `MIN_DISCRIMINANT_LEN = 4` (never lets a 3-char verb out-IDF
  `checkoutComplete`); `discriminants` uses the Day-8 `Lexicon` IDF with a
  heading-only fallback weight. `mmr(ranked, λ, k)` = greedy
  `λ·rel − (1−λ)·max cos(v, kept)` over stored vectors — zero new embeds.
  `relevance_cross_encoder` is a `PENDING` seam (raises, so it can't bill).
- `ai_os/context.py` — `build_context` v2 behind the Day-6 signature:
  3 jobs — order (`best_first=True` default), dedup (`text_sim` token-Jaccard
  over the *chosen* set, `sim_tau=0.55`, top chunk never dropped), and
  evidence-weighted compression (`budget_fill` drops lowest-rel chunks with a
  `reason:"budget"` record; Day-6 rank-order truncation retired as a policy).
- `tools/retrieval_day9.py` — Day 8 vs Day 9 runner: `--stub` (table below),
  `--sweep` ({λ, sim_tau, best_first} **plus the pre-registered weights ×
  mmr/dedup-order focus** — the fourth swept knob, `--weights` /
  `--dedup-first` now wired through the pipeline), `--lom` (lost-in-the-middle
  protocol, MECH; verdict PENDING), `--report` markdown writer.
- `test_day9.py` — **all DAY 9 (MECH) CHECKS PASSED**: matcher + IDF math,
  heading/rare/position signals, rerank flip (B2-style), MMR culls the twin +
  the B1 guard case, dedup guard (top chunk never similarity-dropped), budget
  floor/reporting, the two instance-cost ledgers (2 embeds, 0 chat), E1/E2
  still refuse with the gate reading the *fused* score, the nine-fixture
  regression (green rows stay green, no context leaks), **and the knobs: the
  weights vector reaches rerank, dedup-first is a config (not a crash), and
  the knobbed pipeline keeps the zero-ledger story**.
- Stub run (λ=0.7, τ=0.05): **Σ ΔMRR +1.000** across the answerable rows,
  2 rows moved, **0 leaks**. B2 0.5→1.0 (rerank lifts completing-checkout),
  D2 0.5→1.0 (heading lifts quickstart), A1/B1/C1/C2/D1 held. E1/E2 still 0/2
  refusals at the stub τ — τ recalibration on the fused score carries as
  `PENDING` from Day 8. Stub weights×order focus: default weights are the widest
  no-leak corner (heading-heavy +1.500 but leaks 1 expected doc; rare-heavy
  +1.000 but leaks 1) — recorded as the knob's honest trade-off.
- **Real-corpus run** (`--store data/store.json`, 11,061 chunks,
  bge-base-en-v1.5): **the envelope is the bottleneck and it is Day 8's door.**
  Seven of nine expected docs never reach the rerank top-8 (fused ranks 16–20
  at k=30; A1's expected doc is not in the top-30), so Day 9 moved nothing on
  them — per §7's re-attribution rule they return to the Day 8 backlog. **C2
  partially moved (D9 MRR 0.500):** the rerank lifted the one expected doc that
  reached the envelope (5→2). **D2 is Day 9's own regression on real data**
  (D8 0.200 → D9 0.000): MMR at λ=0.7 dropped a marginal expected doc; the
  real sweep shows λ=0.6 recovers it. **E-family τ data exists for the first
  time**: fused top-1 0.854 / 0.874 on the real store — overlapping the
  answerable family, so a fused-score τ *cannot* separate E; the refusal
  signal must move semantic (Day 21).
- Sweep: `λ=0.7, sim_tau=0.5–0.6` is the widest safe corner on the stub —
  ΣΔMRR +1.000, 0 leaks; λ≤0.6 leaks B1's quickstart (the recorded B1 trap);
  λ≥0.8 recovers it but rounds to +0.917. On the real corpus weights/order are
  inert (envelope-bound: every combo +0.1, 7 leaks); the λ-Sim sweep tops out
  at +0.4333 (λ=0.6 or 0.9, sim_tau=0.4) — D2's recovery, nothing more.
- Latency/cost: rerank+MMR+dedup+budget ≈ **0.57 ms/query** on the stub
  (`/tmp` probe ×2000); real pipeline end-to-end ≈ 0.3 s/question on 11,061
  chunks (dominated by retrieval fusion), against **zero** new embed/chat calls
  vs Day 8 — an improvement that costs the ledger nothing until you opt into
  the `PENDING` cross-encoder (+N chat calls/candidate, price recorded in the
  eval doc).

---

## Day 10 — Code RAG `PARTIAL`

Day 10 changes the **corpus**, not the system: the retriever (Day 8) and the
evidence engine (Day 9) carry over with **zero lines changed**. The additions are
two ingestion seams (`ingest_code`, `build_code_store`) and a vocabulary layer
inside the unchanged `Retriever` contract. Concept + learning record:
`docs/concepts/code-rag.md`, `docs/evaluations/code-day10.md`,
`docs/day-plan/day-10-notes.md`.

- `ai_os/ingest_code.py` — `SymbolExtractor` (stdlib `ast`, no parser dep): one
  `Symbol` per function/class/method with `kind/signature/docstring/imports/
  dependencies/first_line/last_line/body`; `module#Symbol` ids; top-level
  statement runs → `kind="module"`. Two defects found at real ingest and fixed:
  the size bound is now **per chunk** (`MAX_CHUNK_CHARS`, a file-level gate
  windowed 128 legit modules incl. `complete_checkout.py`/`types.py` — the
  fixtures' own answers), and **nested classes carry their scope** (`User.Meta`;
  bare `Meta` gave 233 duplicate ids). The split test ("no chunk starts
  mid-`def`") is a structural assertion over the whole corpus.
- `ai_os/rag.py` — `Chunk` gains optional `symbol/kind/signature/docstring/
  imports/dependencies`; save/load round-trip them (a doc store still loads,
  defaults fill in); `citation()` adds `symbol/kind/line` for code chunks only,
  so `doc_id` stays `module#Symbol` and `cited ⊆ retrieved` is untouched;
  `code_store_path_from_env` / `RAG_CODE_STORE_PATH`.
- `ai_os/retrieval.py` — `code_tokens` (split non-word chars + camelCase + `_`,
  lowercase, drop stopwords), `CODE_REWRITES`, and `kind`/`module_prefix`
  metadata filters; `Retriever` gains a `tokenizer` knob (defaults to the prose
  `tokenize`). Day 8 behaviour byte-identical when the knob is unused.
- `ai_os/lexicon.py` — `Lexicon(chunks, tokenizer=...)`: the IDF scorer is
  retrained for identifiers, not rewritten.
- `ai_os/code_questions.yaml` — the pre-registered code fixtures (CA1–CE1),
  asserting on `module#Symbol` sets exactly as Day 7's asserted on `doc_id`s.
- `tools/build_code_store.py` — mirrors `build_rag_store` over `corpus_code/`
  (AST + interface-only embedding, resumable, dimension-locked). Hardened: on a
  mid-build *embedding-space* switch it now stops and **preserves** the partial
  store instead of wiping (Cloudflare's cap fell through to a dim-2048 model and
  would have thrown away 16k symbols).
- `tools/code_debug.py` — offline stub code corpus (14 symbols, real Saleor
  layout) + identifier-hash gateway; `tools/retrieval_day10.py` — the
  doc-vs-code runner; `ai_os/server.py` — `/rag` gains `corpus="docs"|"code"`.
- `test_day10.py` — **ALL DAY 10 (MECH) CHECKS PASSED**: tokenizer collisions,
  extractor (kinds/signature/docstring/imports/deps/lines/split test/module run/
  windowed fallback/test-module detection), directory ingest, chunk-metadata
  round-trip + citation, `kind`/`module_prefix` filters + filter-miss, fixture
  set-algebra (`must_not`, `must_refuse`), the stub code set (6/6), the doc nine
  regression (9/9, ΣΔMRR +1.000), the zero-chat-call ledger, and the gate.
- Stub run (`--stub`): **code 6/6 green, doc nine 9/9 green** — the code corpus
  is additive, not a rewrite. CA1 surfaces the split-brain double-hit
  (`CheckoutComplete` + `complete_checkout`, Δrank +1); CB1/CB2/CC1/CD1 rank
  top-1; CE1 refuses. τ=0.75 is a stub *demo* choice, not a calibration — the
  stub's answerable top-1s (0.636–0.863) overlap CE1's (0.703), repeating Day 9's
  real finding that a fused τ cannot separate E. `PENDING`, owner Day 21.
- **Real corpus (2026-09-23): cloned and ingested — build quota-blocked.** The
  clone is real (`corpus_code/saleor/`, 4,323 files → **29,156 symbols** / 29,133
  unique ids) and all eight fixture symbols now resolve. The build hit Cloudflare
  Workers AI's **daily free cap of 10,000 neurons** at ~16k symbols; the full tree
  needs ≈18k neurons (**two** free days), the fixture domains ≈6.7k (**one**).
  Pinning the embed provider and resuming is one command (see the gap below and
  `docs/evaluations/code-day10.md` → "Real corpus — 2026-09-23").

---

## Day 11 — Tool Calling `PARTIAL`

Day 11 changes the *interface*, not the engine: the retrieval seams become
function schemas, the model proposes a call, the harness validates and executes
it, and the answer is grounded in the tool's evidence. The evidence side
(`Retriever`, rerank, MMR, dedup, the gate) is **called, never modified** —
which is why the tool layer is `DONE` mechanically and the retrieval numbers
do not move. Concept + evaluation:
`docs/concepts/tool-calling.md`, `docs/evaluations/tool-day11.md`.

- `ai_os/contract.py` — `ToolCall{name, arguments-as-string, id}`;
  `Completion.tool_calls`. `arguments` stays the raw wire string so validation
  owns "malformed arguments" instead of a provider coercing.
- `ai_os/providers/openai.py` — `tools=` body field, `message.tool_calls`
  parsed, assistant/tool messages re-encoded; **the Day-6 empty-content check
  gains a sibling** (`content: null` *with* `tool_calls` is a valid tool turn;
  *without* is still the retryable glitch). The two cases are separate test rows
  so fixing one can never re-break the other.
- `ai_os/providers/anthropic.py` — native `tools` (`input_schema`), `tool_use`
  folded into the same `ToolCall`, `tool_result` carried back in a user message.
  `cloudflare.py` (embeddings-only) takes the `tools` kwarg so the pass-through
  cannot `TypeError`.
- `ai_os/gateway.py` — `chat_with_tools(messages, tools, ...)` = `chat()` + one
  opaque argument. Retries, backoff+jitter, `Retry-After`, ordered fallback and
  the per-attempt ledger row are untouched: a tool-turn chat is a chat call.
- `ai_os/tools.py` — `ToolRegistry` (`register`/`schemas`/`validate_args`/
  `execute`/`run`), `ToolResult` (`success`/`failure`/`refusal`), the
  hand-rolled JSON-Schema subset, and the five tools: `search_saleor_docs`,
  `search_saleor_code` (both built on the unchanged `Retriever`, with per-corpus
  τ), and `get_product/order/customer` over seeded fixtures tagged
  `pending_resolver: "day14"`. Refusal is a **return value**; rejection is a
  **result** naming the field.
- `ai_os/agent.py` — `run_tool_answer`: tool turn → validate → execute → a
  second grounded chat turn; `cited ⊆ retrieved` checked on the **tool's**
  result set (a fabricated `module#Symbol` is caught); refusals surfaced and a
  restated refusal flagged; `max_tool_turns` cap (a config) recording
  `loop_deferred: day12`.
- `tools/mock_gateway.py` — scripted `tc_*` tool-call scenarios and
  `answer_*` answer turns per dialect, plus an `empty` outcome, so every
  mechanical row runs offline with zero keys.
- `tools/tool_smoke.py` — **ALL DAY 11 (MECH) CHECKS PASSED**: T1 selection +
  split-brain double-hit, T2 by-corpus selection, T3 data tool, T4 the argument
  matrix (missing / enum / unknown key / malformed JSON, each named), T5 unknown
  tool, T6 refusal + the fabrication guard, T7 the single-turn cap, Anthropic
  dialect parity, the Day-6 sibling rows, the 3-bill cost assertion, and the
  no-regression lines (doc nine 9/9 ΣΔMRR +1.000, code six 6/6).
- `PENDING`: whether a *real* model chooses the right tool, spells the right
  args, and uses the result — Day 21's benchmark on this harness. The stub
  proves the plumbing carries a tool turn; it does not pretend a model drives it
  well. Streamed tool-call deltas and `arguments` truncation are the real-wire
  margins that need a key.

---

## Day 12 — Agent Loop `PARTIAL`

Day 12 lifts Day 11's single-turn cap into a **loop**: reason → choose a tool →
execute → observe → reason again → answer, so a question several calls deep
("what happens after `CheckoutComplete` submits?") becomes answerable. What changes
is underneath the loop, and it is mostly a *deletion*: **agent state is the
transcript, nothing else** (no hidden `plan`, no scratch dict the model cannot
see); **observations** arrive as the same graded evidence record Day 7 has checked
since, now per step; **termination** gains two exits beside the natural one
(`max_steps`, budget) plus the narrower `gave_up`. Everything Day 11 built — the
registry, validation-as-contract, refusal-as-a-return-value, the Day-4 gateway's
tool wire — is exercised **as-is**: `run_agent(max_steps=1)` *is* Day 11's
`run_tool_answer`, which is what keeps T1…T7 green by construction.
Concept + evaluation: `docs/concepts/agent-loop.md`,
`docs/evaluations/agent-day12.md`.

- `ai_os/agent.py` — `run_agent(request, registry, gateway, *, max_steps,
  budget_chars, self_correct, budget_usd, max_tool_calls_per_step)`: transcript as
  the growing `messages` list; per-step execution of parallel `tool_calls` (each
  its own `tool` observation, never summarized); the exit-1 check reads "no
  `tool_calls`" off the **already-corrected** completion parser (a null-content
  answer turn is still the Day 6 retry, not an exit). At the last allowed step the
  loop asks **once more** so the model can answer *naturally*; a completion that
  still carries `tool_calls` at the cap is recorded-not-executed
  (`capped_tool_calls`) and only then is a forced final *plain* chat issued — which
  is what makes `run_agent(max_steps=1)` reproduce Day 11 exactly (a pre-fix
  regression injected a false `max_steps` note into every single-tool answer, which
  a real model echoed; `--live` caught it). `run_tool_answer` is now
  `run_agent(max_steps=1, max_tool_calls_per_step=cap)` — Day 11 survives as a
  config, not a replica. The transcript opens with `REASONING_PROMPT` (§6) — the
  reasoning-turn system prompt that tells the model observations are evidence for
  the *next* step; its absence was why the real model rephrased instead of
  composing, and `--live` measured both. `mentioned_ids` + the fabrication guard
  (`fabricated`, `cited_subset_retrieved`) are carried in from Day 11.
- `ai_os/trace.py` — `step_trace(step, rows)` (the per-step row:
  `name`/`args_parsed`/`args_valid`/`result_ok`/`refused`/`error`/`evidence_ids`/
  `obs_clamped`, plus `rejections`/`observed_refusals`) and
  `grounding_over_transcript` (`cited ⊆ ∪ observations`, annotating each step with
  its own citations) — Day 7's per-query trace, serialized per step.
  `AGENT_TERMINATIONS` names the four exits.
- `ai_os/tools.py`, `ai_os/gateway.py` — **unchanged**: the loop consumes the seam,
  never edits it; the loop adds retry *steps*, never new retry *behavior* inside a
  call.
- `tools/mock_gateway.py` — scripted multi-step scenarios: `tc_d12_step1/step2`
  (walk the map), `tc_d12_fix_step1/step2` (fix-then-answer), `tc_code_ooc` +
  `tc_d12_pivot_step2` (pivot-after-refusal), `tc_two`/`tc_three` (parallel), the
  `answer_limit`/`answer_budget`/`answer_gaveup` turns (the stops say why).
- `tools/agent_smoke.py` — **ALL DAY 12 (MECH) CHECKS PASSED**: A1 multi-step
  (step 2's query names the symbol step 1's evidence carried; cites from ≥2
  observations), A2 self-correction (1 rejection + 1 fixed call), A3 pivot after
  refusal (no retried refusal, no fabricated symbol), A4/A8 parallel calls (none
  dropped, mixed corpora in one transcript), A5/A5b the `max_steps` and budget
  exits (truncated, named; the over-asking call is recorded-not-executed), A6
  grounding over the transcript (both directions), A7 zero-tool question, the
  `gave_up` bound, the **depth-1 line** (`run_agent(max_steps=1)` is Day 11: the
  natural `answer` exit, `turns: 2`, no `max_steps` note), the cost assertion
  (`chats == tool_steps + 1`, `embeds == tool_steps`), and the **no-regression
  line** (re-runs `tools/tool_smoke.py` unchanged, T1–T7). `--live` swaps the chat
  wire for the real gateway (retrieval stays on the stub stores): contract cells are
  hard-checked, composition cells are `measure`d, and a provider outage reports
  `[SKIP]`/exit 2 rather than a false FAIL. The free
  `nvidia/nemotron-3-super-120b-a12b:free` model **composes** with the reasoning
  prompt — it walked `CheckoutComplete` → `complete_checkout` →
  `create_order_from_checkout` → `process_payment`, each step naming a symbol the
  previous step's evidence carried.
- `PENDING` (now measured): whether a *real* model **composes** — follows an import
  to a `complete_checkout` it was never told and keeps citing each step. `--live`
  answers it per run, and with the §6 reasoning prompt the free model **does** walk
  the map (see the `tools/agent_smoke.py` bullet). Day 21 owns the *cross-model*
  strength verdict. The failure *modes* a reactive loop can still hit (oscillation,
  contradiction, hallucinated tools) are Day 13's taxonomy; the real data the stub
  cannot provide is Day 14's.

---

## Day 13 — Agent Failure Modes `PARTIAL`

Day 13 turns the loop's negative space into the subject. Day 12 made failure *reachable
and terminating*; Day 13 makes it *named and handled* — the seven curriculum modes
(wrong tool, invalid args, infinite loop, missing information, contradictory results,
hallucinated tools, tool failure) become a **taxonomy with an owner and a guard each**,
and the loop gains the three mechanisms Day 12 deferred: a **wall-clock timeout** (the
bound money never gave), a **state fingerprint** (repeat/oscillation diagnosed *before*
the cap), and a **bounded fallback ladder** (retry → sibling tool → degrade/stop, and
it never fabricates). Five of the seven guards are **renamed** Day 11–12 seams; the two
genuinely new ones are tool failure (execution) and contradiction (semantic detection,
not adjudication). Concept + evaluation: `docs/concepts/agent-failure-modes.md`,
`docs/evaluations/failure-day13.md`.

- `ai_os/tools.py` — the **third error family**. `ToolError{error_kind, message,
  retryable}` and the constants `invalid_args`/`unknown_tool` (Day 11 rejections) vs
  `timeout`/`tool_error`/`no_data`/`internal` (Day 13 execution). `ToolResult` gains
  `error_kind`/`retryable`/`fallback_from`; `failure()` and a new `tool_error()` carry
  the type. `validate_args` tags rejections; `execute` turns any raise into a typed,
  retryable `tool_error` **observation** (never an exception into the loop), a
  malformed return into `internal`, and a missing record into `no_data`.
  `REJECTION_KINDS` (model fixes it) and `TOOL_ERROR_KINDS` (the ladder owns it) encode
  the recovery split; `registry.properties()` feeds the sibling-arg filter.
- `ai_os/agent.py` — `execute_bounded(registry, name, args, *, tool_timeout_s,
  clock)` measures the wall around **one** `registry.execute` (not the step) and
  returns `(ToolResult, elapsed_ms)`; a timeout is an observation. `run_agent` gains
  `tool_timeout_s`, `repeat_window`, `tool_retries`, `fallbacks`, and an injectable
  `clock`, plus the **guard ring**: the **fingerprint** (identical call → `no_progress`;
  `A,B,A,B` → `oscillation`, both *before* `max_steps` and layered on top of it) and
  the **fallback ladder** (rung 1 same-arg retry, rung 2 sibling `search_saleor_code ⇄
  search_saleor_docs` with a **disclosed** `fallback_from`, rung 4 `tool_failure`). The
  run returns both `termination_reason` and `failure_kind` (`_derive_failure_kind`),
  plus `tool_timeouts`, `observed_failures`, `no_progress_at_step`,
  `oscillation_detected`, `distinct_step_fingerprints`, `contradiction`,
  `contradiction_covered`, and `fallback_used`. `AGENT_TOOL_TIMEOUT_S=30`,
  `AGENT_REPEAT_WINDOW=4`, `AGENT_TOOL_RETRIES=1`, `AGENT_FALLBACKS=1`.
- `ai_os/trace.py` — the state fingerprint (`fingerprint` = sha1 of name+canonical args,
  stable across processes; `detect_repeat`/`detect_oscillation`) and the contradiction
  detector (`contradiction_rows` groups claims by `field`, fires on conflicting
  *values*; `contradiction_coverage` = the completeness clause requiring both sides
  cited; `observed_failures`). `step_trace` carries the failure ledger beside each step
  row; `AGENT_TERMINATIONS` gains `no_progress`/`oscillation`/`tool_failure`.
- `tools/mock_gateway.py` — Day 13 scripted turns: `tc_d13_repeat`, `tc_d13_osc_a|b`,
  `tc_d13_docs_miss` (the wrong-corpus refusal that licenses a pivot),
  `tc_d13_contra_step1|2` + `tc_d13_contra_agree` (the contradiction and its agreeing
  twin), and the `answer_noprogress`/`answer_oscillation`/`answer_tool_failure`/
  `answer_contradiction` turns (each says *which* disease stopped the loop, from the
  evidence only).
- `tools/failure_smoke.py` (new) — **ALL DAY 13 (MECH) CHECKS PASSED**: F1 wrong tool
  (pivot, `tool_miss`), F2 invalid args (typed rejection, self-correct, A2 stays green),
  F3 repeat (fire at step 2, 0 calls), F4 oscillation (fire at step 4), F5 missing
  info (refusal + restated "not covered", zero evidence), F6 contradiction (flagged,
  both sides cited, **two negative twins** — agreement and the split-brain — must not
  fire), F7 hallucinated tool (typed `unknown_tool`, no execution), F8 tool failure
  (transient retry / persistent sibling fallback disclosed / ladder exhausted →
  `tool_failure`), F9 timeout (wall proven on an injected clock), and the
  **no-regression** gate (re-runs `agent_smoke.py` A1–A8 and `tool_smoke.py` T1–T7
  unchanged in subprocesses). `fabricated? = no` on every row.
- **No regression:** the guards wrap the loop — a healthy run is byte-for-byte
  unchanged (`failure_kind: None`), A2 (self-correction) and F2 (invalid args) coexist
  because the fingerprint never normalizes the *fix*, and A5 (`max_steps`) uses four
  *distinct* calls so the cap, not a detector, is the thing under test. `ai_os/gateway.py`
  and the retrieval engine are **untouched**: a failure-handling layer that modifies the
  thing it protects is a bug wearing a safety vest.
- `PENDING`: whether a *real* model **trips** F1/F5/F6/F7 and whether it **recovers** or
  papers over — Day 21's judgment on Day 13's guards. The stub proves the guards *fire*;
  a real key proves a real model can *trip* them. The stubbed failures cannot represent
  real DB latency (Day 14), the fingerprint cannot catch slow-but-genuine progress (Day
  16), and a contradiction is flagged, never adjudicated (Day 21).

---

## Day 14 — Data Agent `PARTIAL`

- `ai_os/data.py` (new) — the third corpus and the read-only seam. `SqliteDataStore`
  (stdlib, seeded, `PRAGMA query_only` — the offline engine) and `PostgresDataStore`
  (lazy `psycopg` over `SALEOR_READONLY_DB_URL`, `default_transaction_read_only` +
  `statement_timeout`); `SCHEMA_CARD` (seven core tables + FKs + purposes) and the
  business-definition `GLOSSARY` serialized by `schema_card_text()` (budget-clamped,
  cut marked); `validate_readonly()` (the §5 seed: single statement, `SELECT`/`WITH`
  only, DML/DDL denied — **a keyword check, not a parser**); `DataAgent` (the
  validate → execute seam + the fixed parameterized resolvers); `sql_error_kind`
  (`invalid_sql` / `unknown_column` / `unknown_table`); `asserted_subset_returned()`
  (the §7 invariant); `SEED_SQL` (the curated core subset).
- `ai_os/tools.py` (extended) — `run_saleor_sql(sql)` (the **generated**-query tool,
  schema card in its description) and `describe_saleor_schema(table)` registered;
  `get_product/get_order/get_customer(id)` re-pointed at **real parameterized
  queries** and their `pending_resolver` stub tag removed (the Day 11 interface is
  *filled*, unchanged). Records carry `query_kind` (`fixed` / `generated`) so the
  trace distinguishes a parameterized answer from a generated one. **Seven** tools
  now.
- `ai_os/trace.py` (extended) — `data_columns()` promotes `gen_sql` / `validation` /
  `executed` / `query_kind` / `row_count` / `truncated` / `db_elapsed_ms` into the
  per-call row; `generated_sqls()` collects the generated queries. `ai_os/agent.py`
  adds the run object's `gen_sql` / `data_records` (`asserted ⊆ returned`) — the loop
  is otherwise untouched (**the pipeline is the loop**).
- `tools/mock_gateway.py` — Day 14 scripted turns: `tc_d14_*` (revenue, customers,
  empty, bad column, fix, lines, delete, order, customer, schema) and `answer_d14_*`
  (grounded, customers, empty, truncated, error, orders, and the **fabricated** twin).
- `tools/data_smoke.py` (new) — **ALL DAY 14 (MECH) CHECKS PASSED** (61 checks):
  Q1 aggregation (four-table join, window + DRAFT from the glossary), Q2 the
  parameterized tool completed (`query_kind: fixed`, no `pending_resolver`), Q3 the
  customers ⋈ orders join, Q4 `no_data` as a finding, Q5 **two independent read-only
  guards** (validator rejects *before* execution; the engine refuses a bypassed write
  — nothing deleted) + the 10-case validator matrix, Q6 `unknown_column` → self-correct,
  Q7 measured truncation (`fetch limit+1`), Q8 the invariant proven **both ways**
  (grounded passes, fabricated fails), the §2 schema-grounding assertions (DB tables,
  FK joins, glossary, no GraphQL names, budget clamp), and the **no-regression** gate
  (re-runs `tool_smoke.py` / `agent_smoke.py` / `failure_smoke.py` unchanged).
- **Headline:** `asserted ⊆ returned = ok` on every answered row (the fabricated twin
  excepted). Cost shape **differs from retrieval**: **0 embeds + 2 chats** (generate
  SQL, explain rows; +1 chat on a repair) + the schema card's per-step input tokens —
  the DB does exact set logic, so no vectors are needed to *find* the data.
- One deliberate Day 11 edit (recorded): T3's assertion flips from "the fixture says
  `pending_resolver: day14`" to "the real resolver returned order 1, no
  `pending_resolver`", and the schema-name check grows from five tools to seven. No
  retrieval or guard number moved.
- **No regression:** the Data Agent is **additive** — a fourth corpus behind the same
  harness. `ai_os/gateway.py`, the retriever, and the loop's control flow are
  **untouched**; `run_saleor_sql` is just another tool.
- `PENDING`: whether a *real* model writes the right join and the right window for Q1
  and grounds its explanation in the rows (Q8 under a real model) — Day 21's benchmark
  on Day 14's schema card; real Postgres latency and reconciling the curated card
  against a live `information_schema` need a live `saleor_read_only` role; the
  validator is defeatable and the hardened parser is **Day 15's**.

---

## Day 15 — Safe Text-to-SQL `PARTIAL`

Day 14 made the Data Agent *work* and admitted the seed: a keyword validator is
not a parser. Day 15 attacks the seed — DELETE, UPDATE, invalid SQL, expensive
queries, nonexistent tables, ambiguous definitions, plus the agent-specific
indirect injection — and hardens layer 1 into a real **allow-list parser +
policy**, while it keeps telling the truth about *where the guarantee lives*.
Three ideas: **allow-lists beat deny-lists**, **privileges beat parsers** (the
`role` is the invariant; the parser is defense-in-depth), and **fail closed but
stay honest** (over-blocking is a measured bug). Concept + evaluation:
`docs/concepts/safe-sql.md`, `docs/evaluations/sql-safety-day15.md`.

- `ai_os/sql_safety.py` (new) — the ordered choke point. `normalize()` (comment
  obfuscation, homoglyph refusal, single-statement split), `parse_readonly()` (a
  conservative recursive-descent grammar with **no write production**; residual
  write-keyword scan as belt-and-braces), `Policy` (loaded from `policy.yaml`:
  `tables_allow`, `columns_deny`, `definitions`, `row_limit`, `max_cost`,
  timeouts), `estimate_cost`/`check_cost` (EXPLAIN when available, else a shape
  heuristic), `rewrite_limit` (shape-aware, skipped before an aggregate),
  `classify()` (**the** repairable/policy routing), and `SafeSql.inspect`/
  `rewrite` — the `{ok, …} | {ok: false, error_kind, rejection_class, stage,
  message}` contract.
- `policy.yaml` (new) — the reviewable allow/deny document. The schema card
  becomes an **enforcement artifact** (table allow-list), PII is denied by
  *column* (never readable, even where the table is allowed), the glossary's
  "revenue excludes DRAFT" becomes a **definition guard**, and every cap is
  config (env `SALEOR_SQL_POLICY` points at another document).
- `ai_os/data.py` (extended) — `DataAgent.run` routes through `SafeSql`
  (normalize → parse → policy → cost → rewrite → execute), preserving the raw
  model SQL as `gen_sql` and recording `executed_sql` separately; `QueryResult`
  gains `rejection_class` / `stage` / `cost_estimate` / `statement_timeout_ms`;
  `PostgresDataStore` now sets `statement_timeout`, `lock_timeout`,
  `idle_in_transaction_session_timeout` beside the read-only transaction, and
  exposes `explain_cost()`. The `validate_readonly` seed is kept for the
  Day-14 regression matrix.
- `ai_os/tools.py` (extended) — `ToolResult.rejection_class` (surfaced in
  `as_dict`, so the model is told the *class*); `_data_result` carries it
  through; `tool_error`/`failure` accept it.
- `ai_os/trace.py` (extended) — `data_columns` promotes `rejection_class`,
  `safety_stage`, `executed_sql`, `cost_estimate`, `statement_timeout_ms`;
  `policy_violations` / `rejection_classes` / `cost_estimates` aggregate a run.
  `AGENT_TERMINATIONS` gains the `policy` exit.
- `ai_os/agent.py` (extended) — the loop consults `rejection_class`: a **policy**
  rejection increments `policy_violations`, is excluded from **both** ladder
  rungs, and **stops** the run (`termination_reason: "policy"`,
  `failure_kind: "policy_violation"`, a forced final turn told not to rephrase);
  the run object carries `policy_violations` / `rejection_classes` /
  `cost_estimates`; `REASONING_PROMPT` gains rule 5 (stop on `policy`, retry only
  on `repairable`).
- `tools/mock_gateway.py` — the D15 attack SQL (`D15_SQL_*`) and the scripted
  hostile turns: `tc_d15_delete` (the forbidden request that stops the run),
  `tc_d15_fix` (the retry the loop must NOT take), `tc_d15_injection` (X10),
  `tc_d15_control` (X11) + `answer_d15_policy`/`_blocked`/`_control`.
- `tools/sql_attack_smoke.py` (new) — **ALL DAY 15 (MECH) CHECKS PASSED** (99
  checks): X1–X9+the X11 control through the choke point, X4's server-timeout
  mapping on a fake engine, X10 end-to-end through the loop (policy stop, one
  step, nothing fabricated), the classification matrix, the grammar/allow-list
  matrix (21 cases), the policy-is-config tests, the shape-aware `LIMIT` + the
  aggregation trap, the schema-leak guard, and the **no-regression** gate
  (re-runs `tool_smoke` / `agent_smoke` / `failure_smoke` / `data_smoke`).
- **Headline:** `Wrote? = no` on every attack row — the three tables snapshotted
  before/after are **byte-for-byte unchanged** — and `X11 false-reject? = no`
  (a multi-join, aggregate, *window* control passes through the choke point and
  the full loop). The two independent guards are both proven: the parser rejects
  *before* execution, and the engine refuses a write when the parser is bypassed.
- One deliberate Day 14 edit, recorded: Q5's DELETE now also asserts the Day 15
  behaviour (classified `policy`; the run **stops** — `policy_violations: 1`, one
  step, no self-correction; fabricates nothing), and its second script turn moved
  from `tc_d14_fix` to `answer_d15_policy`. Q5's original assertions all still
  pass; `asserted ⊆ returned = ok` is unchanged.
- **No regression:** the hardening changes the *executor*, not the loop or the
  corpora. Days 7–14 unit suites and all four smoke suites stay green.
- `PENDING`: **adversarial completeness is unprovable** — X1–X10 are a *sample*
  and the parser is best-effort; the **guarantee is the `saleor_read_only`
  role**. X10's *real-model* behaviour is **Day 25/26** (injection) + **Day 21**;
  the offline cost estimate is a shape heuristic (real `EXPLAIN` needs a live
  role); the real server timeout is proven offline only by its mapping; parser
  *soundness* is **long-term hardening**. Re-verify with
  `python tools/sql_attack_smoke.py`.

---

## Day 16 — Agent Planning `PARTIAL`

Days 6–15 built a machine that reads, acts, drives itself, survives failure,
computes, and defends itself — and every one of those is **reactive**: the loop
chooses its next step only *after* reading the last observation. Day 16 adds the
reflective counterpart — **decide before acting** — for a concrete multi-step
investigation ("investigate why customer X could not complete checkout"), while
keeping the change small. Concept + evaluation: `docs/concepts/planning.md`,
`docs/evaluations/planning-day16.md`.

- `ai_os/planner.py` (new) — `Step(id, tool, args, depends_on, expected)` and
  `Plan`; `parse_plan` (fenced or bare JSON), `validate_plan` (**fail-closed**:
  registered tools · schema-valid args · known deps · **acyclic** — all errors
  collected), `topological_order` (deterministic, dependency-safe),
  `estimate_cost`/`within_budget` (Day 12's cap, plan-wide), `needs_plan` (the
  **entry rule**, `MECH`), and the three model calls: `plan` (one chat, **one**
  informative retry carrying the validator's errors), `replan` (rung 4), `repair_args`
  (rung 1). The plan is *data*; validation is the plan-level analogue of Day 11's
  argument check and Day 14's schema grounding.
- `ai_os/executor.py` (new) — `run_plan`: walks the plan in topological order, one
  `chat_with_tools` turn per step through the **same** atoms as Day 12 (the registry,
  `execute_bounded`, the error taxonomy, the sibling map, `step_trace`), and adds the
  plan layer: **execution state** + a **checkpoint** per step (resume at *k+1* /
  replay), the **re-plan gate** (the observation vs `expected` → replan), the
  **four-rung ladder** (repair · retry · fallback · replan, each bounded) with
  **Day 15's classes** deciding which rungs a failure may climb — a `policy`
  rejection is a **wall** that climbs **none** — and the **6→7 gate** plus
  `conclusion ⊆ evidence`. `resume=True`+`stop_after=k` make the checkpoint's
  resumability provable offline.
- `ai_os/state.py` (new) — `StepRecord` / `Checkpoint` / `ExecutionState`, with
  `as_dict`/`from_dict` so the checkpoint **format** round-trips (the resume path).
  **Ephemeral by design**: the *store* is **Day 17/18's** seam, named not built.
- `ai_os/agent.py` (extended, minimally) — `run_agent` gains an optional `plan` and
  delegates to `executor.run_plan`; with no plan the Day-12 body is **untouched**,
  which is what makes a **one-step plan the identity case** (P9) and keeps
  `run_agent(max_steps=1)` ≡ Day 11.
- `ai_os/trace.py` (extended) — `PLAN_TERMINATIONS`, `plan_adherence` (grades the
  step's tool, not the sequence), `conclusion_subset_evidence` (ids **and** numbers),
  `plan_columns` (the §8 row). No existing function or row shape moved.
- `tools/mock_gateway.py` — Day 16 fixtures: `D16_PLAN_GOOD` (the curriculum's
  five-step DAG with the `payment ← order_state` edge), `D16_PLAN_BAD`, and the
  `answer_d16_*` turns (the scripted plan, its correction, and the honest non-answer
  the gate forces).
- `tools/plan_smoke.py` (new) — **ALL DAY 16 (MECH) CHECKS PASSED** (68 checks):
  P1 validation + the fail-closed twins, P2 coverage, P3 execution state (+ a failed
  step recorded, not dropped), P4 resume/replay, P5 replan-on-contradiction, P6 the
  repair rung and its bound, P7 the policy wall with `rungs: []`, P8 the 6→7 gate
  **both ways** (the fabricated twin caught), P9 the control (+ an over-planned twin),
  FC1/FC2 fail-closed, and the **no-regression** gate (re-runs `tool_smoke` /
  `agent_smoke` / `failure_smoke` / `data_smoke` / `sql_attack_smoke`).
- **Headline:** `conclusion ⊆ evidence = ok` on every answered row (the fabricated
  twin the only `FAIL`), and `over-planned? = no` on P9 — `needs_plan("Fetch order
  1")` is `False`, so a one-step stored plan costs **1 step chat + 1 answer chat, 0
  plan chats**, exactly Day 11/12's shape.
- **No regression:** the planner is **additive** — an agenda above the loop. Days
  7–15's unit suites and all five smoke suites stay green; the only edit to existing
  behaviour is the `plan=None` default.
- `PENDING`: whether a **real** model writes a **good** plan (the right
  decomposition, the right order, sensible replans) and grounds its answer under it —
  **Day 21's** benchmark on Day 16's planner. Durable state/memory is **Day 17/18**;
  parallel DAG branches are *named, not built*; the entry rule's heuristic and the
  id/number shape of the invariant are recorded limits. Re-verify with
  `python tools/plan_smoke.py`.

---

## Known gaps (what blocks "everything green for real")

1. **Git:** Day 9 is committed (`39285dd`, `76af2e0`, `37bd8d9`, `d62acda`);
   the newly added knobs + real-run docs are the uncommitted working-tree
   change alongside the Day 11–15 plans.
2. **Chat quota / rate limits:** live chat now works via OpenRouter free models
   (`nex-agi/nex-n2.5-pro:free`, daily free cap ~50 req); embeddings via
   Cloudflare, free tier. Full semantic sweeps stay budgeted, not assumed.
3. **Day 7 semantics** — the MECH half is implemented and green
   (`test_day7.py`); the report's `⟨run⟩` cells and τ calibration still need
   graded gateway runs (`python tools/rag_debug.py --store data/store.json`).
4. **Day 8 semantics + the real envelope** — the MECH half is green
   (`test_day8.py`), but the **real-corpus run exposes that Day 9's expected
   docs mostly never reach the fused top-8** (ranks 16–20 at k=30; A1's not in
   top-30). τ recalibration and the Day-8 comparison must be re-run on the real
   store (`python tools/retrieval_day8.py --store data/store.json   # real runs
   python tools/retrieval_day8.py --store data/store.json --sweep # α fit`).
5. **Day 9 semantics** — the MECH half is green (`test_day9.py`), and **the
   real-corpus comparison is made** (`python tools/retrieval_day9.py --store
   data/store.json`): C2 partially moved (0.2→0.5), D2 regressed (0.2→0.0, MMR
   λ), everything else is envelope-blocked and re-attributed to Day 8. What
   still needs graded chat runs: the lost-in-the-middle *verdict* (only C2 is
   even exercisable today), τ-separation for E (real data recorded: fused top-1
   0.854/0.874 overlaps the answerable family), and the `PENDING` cross-encoder
   question (does its N chat-calls/candidate beat free rel() on this table).
6. **Day 10 semantics + the real code corpus** — the MECH half is green
   (`test_day10.py`; code 6/6, doc 9/9 on the stub) and the real tree is ingested
   (29,156 symbols). Still `PENDING`: (a) the **real embed build** is
   **quota-blocked** — Cloudflare's free 10k neurons/day only covers ~16k symbols,
   so the full tree spans two free days; pin the provider and resume (resume
   skips what is embedded):
   ```bash
   LLM_PROVIDERS=cloudflare: python tools/build_code_store.py \
       --corpus corpus_code/saleor --out data/code_store.json --batch 100
   # one-day option — the fixture domains (all CA1–CE1 symbols live here):
   LLM_PROVIDERS=cloudflare: python tools/build_code_store.py \
       --corpus corpus_code/saleor --out data/code_store.json --batch 100 \
       --modules saleor/checkout saleor/order saleor/payment saleor/webhook \
                 saleor/graphql/checkout saleor/graphql/order saleor/graphql/core \
                 saleor/graphql/payment saleor/graphql/webhook
   ```
   then `python tools/retrieval_day10.py --store data/code_store.json
   --doc-store data/store.json`; (b) whether a real chat model turns the
   right symbol into the right `file:line:why` answer (Day 21); (c) E-family τ
   recalibration on the code corpus (the stub's answerable 0.636–0.863 overlaps
   CE1's 0.703 — same overlap Day 9 saw on real docs); (d) the interface-vs-body
   sweep (does body-embedding recover the behaviour-but-not-name symbols?).
7. **Day 11 semantics** — the MECH half is green (`tools/tool_smoke.py`; T1–T7,
   Anthropic dialect parity, the Day-6 empty-content sibling, doc nine 9/9 and
   code six 6/6 through the tools). Still `PENDING`: (a) whether a real model
   **chooses** `search_saleor_code` over `search_saleor_docs` and spells `kind`
   right — the stub scripts the call, so tool-selection recall@1 is stub-truth
   only (Day 21); (b) the real providers' tool-wire margins (streamed tool-call
   deltas, `arguments` truncation, finite call-id formats); (c) whether a real
   model *uses* the evidence or paraphrases past it — the fabrication guard
   catches cited-but-absent ids, not prose that ignores the tool; (d) whether the
   single-turn cap is the right amount of agency, which Day 12 answers by lifting
   `TOOL_MAX_TURNS`.
8. **Day 12 semantics** — the MECH half is green (`tools/agent_smoke.py`; A1–A8,
   the `gave_up` bound, the **depth-1 line** (`max_steps=1` is Day 11 — the natural
   exit, no `max_steps` note), the `chats == tool_steps + 1` / `embeds ==
   tool_steps` cost assertion, and T1–T7 re-run unchanged through
   `run_agent(max_steps=1)`). `--live` measures the real-model half per run, and
   with the §6 reasoning prompt the free model **follows the map**
   (`complete_checkout` → `create_order_from_checkout` → `process_payment`). Still
   `PENDING`: (a) *cross-model* strength — composition now holds on the free model,
   and a stronger paid tool-calling model is Day 21's call to confirm breadth; (b)
   whether `max_steps` is the right
   stopping lever or a worth-conscious reasoner should stop on sufficiency
   (Day 16/21); (c) whether per-step `cited ⊆ retrieved` catches the *important*
   failure (evidence ignored between steps) or only the cheap one (a dropped
   citation) — the loop-prompt ownership family is new and ungraded by a real
   model; (d) the failure modes a reactive loop still hits (oscillation,
   contradiction, hallucinated tools) — **Day 13**'s guards, built on this trace.
9. **Day 13 semantics** — the MECH half is green (`tools/failure_smoke.py`; F1–F9,
   `fabricated? = no` on every row, the timeout wall on an injected clock, the
   fingerprint firing before the cap, the disclosed fallback ladder, the contradiction
   flag with its two negative twins, and A1–A8 / T1–T7 re-run unchanged through the
   guarded loop). Still `PENDING`: (a) whether a *real* model **trips** F1/F5/F6/F7 and
   whether it **recovers** or papers over — Day 21's judgment on Day 13's guards (the
   stub proves the guards *fire*, a real key proves a model can *trip* them); (b)
   **contradiction adjudication** — the harness flags a conflict and requires both
   sides cited, but no mechanical rule decides *which* is right (Day 21); (c) real DB
   timeouts and resolver errors, which the stubbed failures cannot represent (Day 14);
   (d) slow-but-genuine progress that still ends at `max_steps` — the fingerprint
   cannot see it (Day 16's planner). Re-verify with
   `python tools/failure_smoke.py`.
10. **Day 14 semantics** — the MECH half is green (`tools/data_smoke.py`; Q1–Q8,
    `asserted ⊆ returned = ok` on every answered row with the fabricated twin the
    only `FAIL`, the two independent read-only guards, the schema card, measured
    truncation, `no_data` as a finding, the `unknown_column` repair, and A1–A8 /
    T1–T7 / F1–F9 re-run unchanged). Still `PENDING`: (a) whether a *real* model
    writes the right join and window from the schema card and grounds its
    explanation in the rows — Day 21's benchmark on Day 14's card and tool; (b)
    **real Postgres latency** and reconciling the curated card against a live
    `information_schema` — needs `SALEOR_READONLY_DB_URL` (the driver `psycopg` is
    declared; the seam is built); (c) the **validator is a keyword check, not a
    parser** (defeatable; the `saleor_read_only` role is the certain guard) — the
    real parser + allow/deny + cost guards are **Day 15**; (d) **schema retrieval**
    for a card that cannot fit the whole DB (Day 16/19). Re-verify with
    `python tools/data_smoke.py`.

11. **Day 15 semantics** — the MECH half is green (`tools/sql_attack_smoke.py`;
    X1–X11, `Wrote? = no` on every attack row with the DB byte-for-byte unchanged,
    `X11 false-reject? = no`, the classification matrix, the grammar/policy/config
    checks, the shape-aware `LIMIT` + the aggregation trap, the leak guard, and
    Days 7–14 re-run unchanged). Still `PENDING`: (a) **adversarial completeness is
    unprovable** — X1–X10 are a sample and the parser is best-effort; the guarantee
    is the `saleor_read_only` **role** (long-term hardening: a vetted AST validator
    is the recorded seam); (b) X10's *real-model* behaviour — will a real model emit
    hostile SQL when a poisoned corpus asks? (Day 25/26), and unprompted (Day 21);
    (c) the offline **cost estimate is a shape heuristic** — real `EXPLAIN
    (FORMAT JSON)` needs a live role; (d) the real **server `statement_timeout`** is
    proven offline only by its *mapping* (a fake engine), not by a live server;
    (e) the **definition guard is SQL-shape arithmetic**, not semantic understanding.
    Re-verify with `python tools/sql_attack_smoke.py`.

12. **Day 16 semantics** — the MECH half is green (`tools/plan_smoke.py`; P1–P9 with
    `conclusion ⊆ evidence = ok` on every answered row, the fabricated twin the only
    `FAIL`, and `over-planned? = no` on P9; plan validation fail-closed (an invalid
    plan runs **zero steps, zero bills**), execution state + checkpoints (resume at
    *k+1* without re-running a completed step; replay reproduces the evidence), the
    re-plan gate, the bounded ladder with the **policy wall climbing no rung**, and
    the 6→7 gate; Days 11–15 re-run unchanged). Still `PENDING`: (a) whether a *real*
    model writes a **good** plan — the right decomposition, the right order, sensible
    replans — and grounds its answer under it; the stub supplies a **stored** plan, so
    plan quality is **Day 21's** benchmark on this harness; (b) **durable state and
    memory** — the checkpoint *format* round-trips, the persistent store is
    **Day 17/18's** seam, named not built; (c) **parallel DAG branches** — the day
    executes a topological linearization, and true concurrency's checkpoint/evidence
    semantics are a recorded seam (*named, not built*); (d) the **entry rule is a
    heuristic** (a long single-intent question can be over-planned) and
    `conclusion ⊆ evidence` is **id/number-shaped** (prose asserting a cause without
    an id or a figure is not caught) — the same honest limit Day 12's fabrication
    guard carries, owned by **Day 21**. Re-verify with `python tools/plan_smoke.py`.

## How to re-verify, in one line

```bash
python test_chunk.py && python test_search.py && python test_rag.py && python test_day7.py && python test_day8.py && python test_day9.py && python test_day10.py
python tools/gateway_smoke.py
python tools/rag_smoke.py
python tools/rag_debug.py --stub       # day 7 offline mechanics
python tools/retrieval_day8.py --stub  # day 8 offline comparison (6/9 → 7/9)
python tools/retrieval_day9.py --stub  # day 9 offline comparison (ΣΔMRR +1.000, 0 leaks)
python tools/retrieval_day9.py --stub --sweep   # λ×sim_tau×best_first + weights×order focus
python tools/retrieval_day9.py --stub --lom     # lost-in-the-middle protocol (MECH)
python tools/retrieval_day9.py --store data/store.json    # real-corpus comparison
python tools/retrieval_day9.py --store data/store.json --sweep --lom  # real knobs + LOM
python tools/retrieval_day10.py --stub  # day 10 doc-vs-code (code 6/6, doc 9/9)
python tools/tool_smoke.py              # day 11 T1–T7 (calls, validation, cap, parity)
python tools/agent_smoke.py             # day 12 A1–A8 (loop, three exits + gave_up, depth-1, grounding over transcript)
python tools/agent_smoke.py --live      # day 12 A1/A6/A7 against the real gateway (retrieval on the stub stores)
python tools/failure_smoke.py           # day 13 F1–F9 (guards + fabricated? column) + A1–A8 / T1–T7 re-run
python tools/data_smoke.py              # day 14 Q1–Q8 (read-only guards + asserted ⊆ returned) + A1–A8 / T1–T7 / F1–F9 re-run
python tools/sql_attack_smoke.py        # day 15 X1–X11 (attack set + control) + Wrote?=no + the Days 11–14 re-run
python tools/plan_smoke.py              # day 16 P1–P9 (plan validation · state · checkpoints · ladder · policy wall) + the Days 11–15 re-run
LLM_PROVIDERS=cloudflare: python tools/build_code_store.py --corpus corpus_code/saleor --out data/code_store.json --batch 100  # real build (resume; see gap 6)
python tools/retrieval_day10.py --store data/code_store.json --doc-store data/store.json  # real corpora
```