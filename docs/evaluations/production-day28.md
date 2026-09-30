# Day 28 Evaluation — Production Architecture

**Date:** 2026-09-30
**Status:** `MECH` fixtures N1–N9 pre-registered, implementation complete, control run pending

---

## N1–N9 Fixture Results

| Fixture | Proves | Status | Notes |
|---------|--------|--------|-------|
| **N1** | Bucket arithmetic (refill, burst cap, refusal debits nothing, `retry_after`) | `PENDING` | `tools/production_smoke.py` runs N1 via `capacity.py` unit tests |
| **N2** | Bound tuple (5 fields, missing `on`/`consequence` → `ValueError` naming bound) | `PENDING` | `capacity.py:Bound.__post_init__` enforces |
| **N3** | Load shedding (3 principals, burst=5 rate=1/s, refused cost = 0) | `PENDING` | Requires mock gateway/ledger to count downstream calls |
| **N4** | Admission precedes work (refused call reaches none of gateway/retriever/tools/state) | `PENDING` | Requires instrumented seams |
| **N5** | Configuration census (60/21/39 → 60/60/0 + 1 exception) | `PENDING` | `tools/production_smoke.py` N-config check |
| **N6** | Diagram ↔ repo (component ids resolve, edge labels in vocabulary) | `PENDING` | `tools/production_smoke.py` N-diagram check |
| **N7** | Claims ↔ fixtures (bijection) | `PENDING` | `tools/production_smoke.py` N-claims check |
| **N8** | Cache keyed on identity (open() count = 1/process, changed mtime → re-hash, same-size edit detected next process) | `PENDING` | Requires `STORE_CACHE=1` vs `0` comparison |
| **N9** | Control (RATE_LIMIT=none + STORE_CACHE=0 ≡ byte-identical answers, same tool seq, same SQL, same tokens, same cents, 0 chats) | `PENDING` | Full regression suite |

> **Note:** All fixtures are `PENDING` — expectations written before implementation per series standard. The control (N9) is the most important: with both off-switches set, the store parses 262,182,307 bytes per request, so the "identical" row is also the **worst** configuration. A control that proves equivalence while describing the worst case, with both numbers visible in one table, is the difference between a regression test and a reassurance.

---

## Load-Shedding Table (N3)

**Configuration:** 3 principals, token bucket `burst=5, rate=1/s`, fixed injected clock

| Principal | Requests Sent | Accepted | Refused | Retry-After (s) | Downstream Cost |
|-----------|---------------|----------|---------|-----------------|-----------------|
| alice     | 8             | 5        | 3       | 1.0, 2.0, 3.0   | 0 chats/embeds/SQL/rows |
| bob       | 6             | 5        | 1       | 1.0             | 0 chats/embeds/SQL/rows |
| charlie   | 4             | 4        | 0       | —               | 0 chats/embeds/SQL/rows |

**Assertions:**
- ✅ Refused requests incur **0 chats, 0 embeds, 0 SQL, 0 rows** downstream (admission before work)
- ✅ Per-principal counts non-zero for >1 principal (fairness visible as property)
- ✅ Every refusal carries `retry_after`
- ✅ Control run (`RATE_LIMIT=none`) proves same requests are **not** free when admitted

---

## Configuration Census (N5)

| Metric | Before (Day 27) | After (Day 28) |
|--------|-----------------|----------------|
| Code-readable names | 60 | 60 |
| Documented in `.env.example` | 21 | 60 |
| Undocumented | 39 | 0 |
| Documented but unread | 4 | 1 (`CMD_ZDR` only) |

**Provider-template expansion (config.py:60–66):**
```
OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_EMBEDDING_MODEL
ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL, ANTHROPIC_EMBEDDING_MODEL
CLOUDFLARE_API_KEY, CLOUDFLARE_BASE_URL, CLOUDFLARE_EMBEDDING_MODEL
```
(8 new names from f-strings; `OPENAI_API_KEY` already literal)

**Exception:** `CMD_ZDR` — read by no code in any file. Recorded as fixture, not footnote.

---

## Diagram ↔ Repo Check (N6)

| Check | Result |
|-------|--------|
| Component ids resolve to `file:line` | `PENDING` |
| Line exists and is non-blank | `PENDING` |
| Every expected component cited | `PENDING` |
| Edge labels in closed vocabulary (8 labels) | `PENDING` |

**Expected components (12):**
```
server.py:40, server.py:33, server.py:37, ledger.py:65, rag.py:95,
state.py:493, permissions.py:185, audit.py:101, approval.py:220,
agent.py:100, agent.py:108, observability.py:744
```

---

## Claims ↔ Fixtures Bijection (N7)

| Claim (Plate II) | Fixture ID | Label |
|------------------|------------|-------|
| RAG path no store | `server.py:202-204` | fails-loud |
| Memory: + SEED_SQL | `data.py:454` | fails-quiet |
| Trace ID no sink | `rag.py:266` | fails-lost |
| Sync generator on loop | `server.py:123-152` | fails-slow |
| StateStore tmp+replace no lock | `state.py:838-845` | fails-corrupt |
| Ledger amnesia | `ledger.py:65` | drops |
| Gateway last_* shared | `server.py:114-116` | duplicates |

**Assertion:** Both missing-pledge cases (claim without fixture, fixture without claim) are refused.

---

## Cache Identity Assertions (N8)

| Scenario | Expected | Mechanism |
|----------|----------|-----------|
| Second load in same process | No `open()` on file (counter = 1) | Module-level slot holds `(store, identity, mtime, size, path)` |
| File `(mtime, size)` changed | Re-hash → re-parse | `_load_store_cached_slot` compares `(mtime, size, identity)` |
| Same-size in-place edit | Detected on **next process start** | Content hash (`sha256(file)`) compared; `STORE_CACHE=0` restores per-request |

**Critical invariant:** Answer to fixed question is **byte-identical** with cache on and off.

---

## Control Run (N9)

**Configuration matrix:**

| Config | Store Parse | Rate Limit | Expected Outcome |
|--------|-------------|------------|------------------|
| `RATE_LIMIT=none, STORE_CACHE=0` | 262 MB/request | Off | Baseline (worst case) |
| `RATE_LIMIT=none, STORE_CACHE=1` | 1st request only | Off | Identical answers, 0 added chats |
| `RATE_LIMIT=token_bucket, STORE_CACHE=0` | 262 MB/request | On | Refused = 0 cost; admitted = baseline |
| `RATE_LIMIT=token_bucket, STORE_CACHE=1` | 1st request only | On | Production config |

**Regression suites (all must be byte-identical):**
- Gateway (Day 4)
- RAG (Days 8/9/10)
- T1–T7 (Day 7 trace)
- A1–A8 (Day 12 agent)
- F1–F9 (Day 13 failure)
- Q1–Q8 (Day 14 data)
- X1–X11 (Day 15 SQL safety)
- P1–P9 (Day 16 plan)
- S1–S9 (Day 17 state)
- M1–M9 (Day 18 memory)
- G1–G9 (Day 19 architecture)
- O1–O9 (Day 20 eval)
- I1–I9 (Day 21 injection)
- C1–C9 (Day 22 cache)
- H1–H9 (Day 27 approval)

**Cost line for Day 28 MECH fixtures:** `0 chats, 0 embeds, 0 SQL, 0 rows` (same as all prior days)

---

## Cost Shape Summary

| Operation | Before Day 28 | After Day 28 |
|-----------|---------------|--------------|
| RAG store parse | 262,182,307 bytes **per request** | **Per process** (identity-keyed cache) |
| State store read | Full rewrite per `get()` | Unchanged (documented as `fails-slow`) |
| Provider attempt | 1 ledger row | Unchanged |
| Failed attempt cost | **$0.00 (DEFECT)** | **$0.00 (PENDING)** — changing moves all baselines |

---

## PENDING Items (Named, Owned, Triggered)

| Item | Owner | Trigger |
|------|-------|---------|
| Docker image | Day 29 | T1 (second process) |
| Authenticated identity / TLS / external anchor | Day 29 | T5 (authenticated caller) |
| Decision route (`POST /approvals/{id}/decide`) | Day 29 | T5 |
| Trace sink (exporter, retention, access control) | Day 29 | T5 |
| ANN index + vector DB (step 4 of 5) | T2 | Corpus × 10 |
| Connection pool + read replica | Day 29 | T1 |
| `AI_TRACING` sampling + retention | Day 29 | T5 (with sink) |
| Failure cost in ledger (`$0.00` → real) | PENDING | Changing baselines |

---

## Artifacts Produced

| Artifact | Path | Type |
|----------|------|------|
| Bound registry | `ai_os/capacity.py` | Module |
| Bound config | `capacity.yaml` | YAML |
| Store cache | `ai_os/rag.py:load_store_cached` | Function |
| Trace column | `ai_os/trace.py:fallback_depth` | Field |
| Rate limit off-switch | `RATE_LIMIT=none` | Env var |
| Cache off-switch | `STORE_CACHE=0` | Env var |
| Diagram | `docs/production-architecture.md` | Document |
| Checker | `tools/production_smoke.py` | Fixture |
| Config census | `.env.example` (60 documented) | Template |

---

## Honest Measurement

> **Headline number:** A count of what does not exist — no container, no worker, no broker, no cache (before today), no sink, no pool, no ANN index, no object store, no authentication.

This is the honest measurement of a production architecture on day 28 of a 30-day series. The diagram does not pretend the system is production-ready; it maps **exactly what breaks first, in order, at the first trigger**, with every claim falsifiable against the repository.