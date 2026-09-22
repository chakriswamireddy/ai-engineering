# Progress — ai-layer (Days 2–9)

> Live status of what is implemented, verified, and still open in this repo.
> Kept up to date as days are worked; the canonical per-day detail lives in
> `docs/day-plan/day-N-notes.md`.

Updated: 2026-09-22

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

## How to re-verify, in one line

```bash
python test_chunk.py && python test_search.py && python test_rag.py && python test_day7.py && python test_day8.py && python test_day9.py
python tools/gateway_smoke.py
python tools/rag_smoke.py
python tools/rag_debug.py --stub       # day 7 offline mechanics
python tools/retrieval_day8.py --stub  # day 8 offline comparison (6/9 → 7/9)
python tools/retrieval_day9.py --stub  # day 9 offline comparison (ΣΔMRR +1.000, 0 leaks)
python tools/retrieval_day9.py --stub --sweep   # λ×sim_tau×best_first + weights×order focus
python tools/retrieval_day9.py --stub --lom     # lost-in-the-middle protocol (MECH)
python tools/retrieval_day9.py --store data/store.json    # real-corpus comparison
python tools/retrieval_day9.py --store data/store.json --sweep --lom  # real knobs + LOM
```