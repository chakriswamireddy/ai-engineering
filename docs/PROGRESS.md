# Progress — ai-layer (Days 2–7)

> Live status of what is implemented, verified, and still open in this repo.
> Kept up to date as days are worked; the canonical per-day detail lives in
> `docs/day-plan/day-N-notes.md`.

Updated: 2026-09-21

## Status legend

- **DONE** — code exists, deterministic verification is green in this repo.
- **PARTIAL** — code exists but a documented part is `PENDING` (needs a real
  API key / real corpus / real model to finish).
- **NOTES ONLY** — the learning record exists; no implementation in this repo.

---

## Day-by-day rollup

| Day | Title | Status | Evidence |
|---|---|---|---|
| 2 | Understand Saleor as an AI environment | NOTES ONLY | `docs/day-plan/day-2-notes.md` |
| 3 | LLM fundamentals (stdlib client) | DONE | `ai_os/llm_client.py` |
| 4 | Model gateway (retries, fallback, ledger) | DONE | committed `35a1e72`; `tools/gateway_smoke.py` |
| 5 | Embeddings + brute-force vector search | DONE | `ai_os/search.py`, `test_search.py` |
| 6 | First RAG pipeline | PARTIAL (mechanics green; real store built, chat quota-blocked) | `chunk/ingest/rag`, `build_rag_store.py`, `rag_semantic_smoke.py`, `rag_smoke.py` |
| 7 | RAG debugging / failure report | DONE (MECH half; semantic cells `PENDING` until a real run) | `ai_os/gate.py`, `ai_os/trace.py`, `ai_os/oracle.py`, `ai_os/fixtures.py`, `ai_os/questions.yaml`, `tools/rag_debug.py`, `test_day7.py`, `docs/failures/rag-naive.md` |

---

## Day 4 — Model Gateway `DONE`

Committed only as `35a1e72` — **nothing after Day 4 is committed yet**.

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

## Day 8 — Retrieval Improvements `NOTES ONLY`

- Nothing implemented yet; Day 8's fix list is the `Proposed fix` column of
  `docs/failures/rag-naive.md` §7 (shortlist), nothing else.

---

## Known gaps (what blocks "everything green for real")

1. **Git:** only Day 4 is committed. Days 5–7 (code + `docs/` + `corpus/` +
   `data/`) are uncommitted working-tree changes.
2. **Real run quota:** real chat runs live via OpenRouter (free models,
   verified), but the key hit its **free-tier cap — 50 free requests/day**.
   Embeddings now run through Cloudflare (free tier, full store built).
   Fix: ≥$10 credits on the key (→ 1,000 free requests/day) or wait for the
   daily reset — then re-run the R3–R5 chat checks
   (`python tools/rag_semantic_smoke.py --store data/store.json`).
3. **Day 7 semantics** — the MECH half is implemented and green
   (`test_day7.py`); the report's `⟨run⟩` cells and τ calibration still need
   a real gateway run (`python tools/rag_debug.py --store data/store.json`)
   once chat quota is available. Re-run after the daily reset
   (`X-RateLimit-Reset` ~daily on the OpenRouter key).

## How to re-verify, in one line

```bash
python test_chunk.py && python test_search.py && python test_rag.py && python test_day7.py
python tools/gateway_smoke.py
python tools/rag_smoke.py
python tools/rag_debug.py --stub     # day 7 offline mechanics
```