# Production Architecture — Day 28

**Two plates, one table, five triggers.** The diagram is a set of claims about failure, not a set of boxes.

---

## Plate I — What Is (Single Process)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            ONE PROCESS (ai_os/server.py:41)                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │  FastAPI     │  │  Model       │  │  RAG Store   │  │  State Store │   │
│  │  (app)       │  │  Gateway     │  │  (in-memory) │  │  (file)      │   │
│  │  ai_os/server.py:41│  │  ai_os/server.py:35│  │  ai_os/rag.py:97   │  │  ai_os/state.py:493│   │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘   │
│         │                 │                 │                 │           │
│         │         ┌───────┴───────┐         │                 │           │
│         │         │  Token Ledger │         │                 │           │
│         │         │  ai_os/ledger.py:65 │         │                 │           │
│         │         └───────────────┘         │                 │           │
│         │                 │                 │                 │           │
│         │         ┌───────┴───────┐         │                 │           │
│         │         │  Permission   │         │                 │           │
│         │         │  ai_os/permissions. │         │                 │           │
│         │         │  py:177       │         │                 │           │
│         │         └───────────────┘         │                 │           │
│         │                 │                 │                 │           │
│         │         ┌───────┴───────┐         │                 │           │
│         │         │  Audit Chain  │         │                 │           │
│         │         │  ai_os/audit.py:101 │         │                 │           │
│         │         └───────────────┘         │                 │           │
│         │                 │                 │                 │           │
│         │         ┌───────┴───────┐         │                 │           │
│         │         │  Approval     │         │                 │           │
│         │         │  Store        │         │                 │           │
│         │         │  ai_os/approval.py:220 │         │                 │           │
│         │         └───────────────┘         │                 │           │
│         │                 │                 │                 │           │
│         │         ┌───────┴───────┐         │                 │           │
│         └────────▶│  Agent Loop   │         │                 │           │
│                   │  ai_os/agent.py:861 │         │                 │           │
│                   └───────────────┘         │                 │           │
│                                             │                 │           │
│                   ┌───────┬───────┐         │                 │           │
│                   │ Trace │ Corpus│         │                 │           │
│                   │(mem)  │(disk) │         │                 │           │
│                   └───────┴───────┘         └─────────────────┘           │
│                                                                           │
│  VANISH AT RESTART: ledger, trace, gateway.last_*, parsed RAG store      │
│  SURVIVE RESTART: corpus files, RAG store file, state file, audit chain  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Single-Process Failure Modes (Live Today)

| Failure Mode | Source | Label |
|--------------|--------|-------|
| RAG path no store | `ai_os/server.py:202-204` | fails-loud |
| Memory stub answers | `ai_os/data.py:454` | fails-quiet |
| Trace ID no sink | `ai_os/rag.py:266` | fails-lost |
| Sync generator on event loop | `ai_os/server.py:123-152` | fails-slow |
| Gateway last_* shared | `ai_os/server.py:114-116` | duplicates |

---

### Component → File:Line Mapping

| Component | File:Line | Survives Restart |
|-----------|-----------|------------------|
| FastAPI app | `ai_os/server.py:41` | no (process) |
| ModelGateway | `ai_os/server.py:35` | no (module global) |
| Token Ledger | `ai_os/ledger.py:65` | **no** (in-memory list) |
| RAG Store (parsed) | `ai_os/rag.py:97` | **no** (in-memory) |
| RAG Store (file) | `data/store.json` | **yes** |
| State Store | `ai_os/state.py:493` | **yes** (file) |
| Permission Layer | `ai_os/permissions.py:177` | **yes** (file) |
| Audit Chain | `ai_os/audit.py:101` | **yes** (file) |
| Approval Store | `ai_os/approval.py:220` | **yes** (file, via StateStore) |
| Agent Loop | `ai_os/agent.py:861` | no (in-process) |
| Agent Loop (steps cap) | `ai_os/agent.py:879` | no (in-process) |
| Trace Store | `ai_os/observability.py:744` | **no** (in-memory) |
| Corpus (docs) | `corpus/` | **yes** |
| Corpus (code) | `corpus_code/` | **yes** |

---

## Plate II — What Breaks, In Order, At First Trigger

**Trigger:** *first request that must be served while another is in flight* (→ process splits)

```
BREAKAGE 1 — StateStore (`ai_os/state.py:894`)  [fails-corrupt, drops]
├─ Mechanism: tmp-file + os.replace, NO LOCK
├─ Failure mode: last-writer-wins → silent record loss
├─ Reader sees: stale file for unbounded time
└─ Fix order: 1st — make StateStore a database (Postgres)

BREAKAGE 2 — Token Ledger (`ai_os/ledger.py:65`)  [fails-lost, duplicates]
├─ Mechanism: per-process list `self.records`
├─ Failure mode: N processes = N ledgers = N amnesias on restart
├─ Cost accounting splits N ways; no global view
└─ Fix order: 2nd — make ledger durable (write-ahead or DB)

BREAKAGE 3a — Audit Chain (`ai_os/audit.py:215`)  [fails-corrupt, fails-stale]
├─ Mechanism: hash chain in file, implicit single writer
├─ Failure mode: two appenders → interleaved entries, `seq` ordering breaks
├─ Incident review depends on `seq`; truncation + reordering both invisible
└─ Fix order: 3rd — append-only with real lock + external anchor (Day 29)

BREAKAGE 3b — Approvals (`ai_os/approval.py:651`)  [fails-corrupt, fails-stale]
├─ Mechanism: approval records in file, implicit single writer
├─ Failure mode: two appenders → interleaved entries, `seq` ordering breaks
├─ Incident review depends on `seq`; truncation + reordering both invisible
└─ Fix order: 3rd — append-only with real lock + external anchor (Day 29)

SAFE TO SHARE (read-only after ingest):
├─ Corpus files (`corpus/`, `corpus_code/`)
├─ RAG store contents (`data/store.json`, `data/code_store.json`)
├─ Policy file (`policy.yaml`)
├─ Permissions file (`permissions.yaml`)
└─ Capacity file (`capacity.yaml`)
```

---

## Twelve-Item Status Table

| # | Item | Status | Where | Owner | Trigger |
|---|------|--------|-------|-------|---------|
| 1 | **Docker** | absent-by-refusal | — | Day 29 | image build |
| 2 | **Queues** | record-without-consumer | `ai_os/approval.py:220` (requested records) | Day 27/29 | depth bound needed |
| 3 | **Caching** | **exists-and-good (today)** | `ai_os/rag.py:448` | Day 28 | — |
| 4 | **Rate Limiting** | **exists-and-good (today)** | `ai_os/capacity.py:215` | Day 28 | authenticated caller |
| 5 | **Async Workers** | absent-by-refusal | — | Day 29 | second process |
| 6 | **Secrets** | exists-and-broken | `ai_os/config.py:23` + `.env` (39/60 missing) | Day 28 | census |
| 7 | **Model Fallback** | exists-and-good | `ai_os/gateway.py:151` (ordered chain) | Day 4 | — |
| 8 | **Retries** | exists-and-good | `ai_os/gateway.py:53` (backoff + Retry-After) | Day 4 | — |
| 9 | **Failure Recovery** | exists-and-broken | Day 13/17/26 (per-process only) | Day 29 | second process |
| 10 | **Database Scaling** | absent-by-refusal | `ai_os/data.py:528` (one connection) | Day 29 | two processes |
| 11 | **Vector Storage** | exists-and-broken | `ai_os/rag.py:140` (brute-force, 251 MB parse/request) | Day 28/29 | corpus × 10 |
| 12 | **Object Storage** | record-without-consumer | filesystem (`corpus/`) | Day 29 | refresh > deploy |

**Status vocabulary (closed):**
- `exists-and-good` — mechanism works, correctly implemented
- `exists-and-broken` — mechanism exists but has a structural defect
- `record-without-consumer` — data structure exists, no mechanism drives it
- `absent-by-refusal` — deliberately not built; owner and trigger named

---

## Five Triggers (Conditions, Not Dates)

| Trigger | What It Activates | Plate II Breakage |
|---------|-------------------|-------------------|
| **T1** First request that must be served while another is in flight | Process splits (workers, containers) | StateStore, Ledger, Chain |
| **T2** Corpus × 10 (110K chunks → brute-force too slow) | ANN index, vector DB | Vector Storage |
| **T3** Corpus refresh slower than deploy | Object storage, CDN | Object Storage |
| **T4** Second provider configured in `LLM_PROVIDERS` | Fallback grading (`unresolvable`) | Model Fallback |
| **T5** Authenticated caller (real identity, not header) | Limiter attachment, audit attribution | Rate Limiting, Secrets |

---

## Cost Shape (Per-Request Constants Today)

| Operation | Cost | Source |
|-----------|------|--------|
| RAG store parse | **262,182,307 bytes** | `ai_os/server.py:198` (was per-request, now per-process via cache) |
| State store read | **full file rewrite** | `ai_os/state.py:654` → `_maybe_save()` on every `get()` |
| Provider attempt | **1 ledger row** | `ai_os/ledger.py:59` (failures included) |
| Failed provider attempt | **$0.00 recorded** | `ai_os/ledger.py:99` — **DEFECT**: real tokens spent, $0.00 logged |

> **The one number that is wrong:** `ai_os/ledger.py:99` records failed attempts as `$0.00` with no token counts. A timed-out 429 attempt costs real money at the provider and `$0.00` in the ledger. This is `PENDING` with an owner — changing it would move every pinned cost in Days 7, 20, 21, 22.

---

## Residual Register (Owners Named)

| Item | Status | Owner | Notes |
|------|--------|-------|-------|
| Second process (workers, container) | named, drawn, **not built** | Day 29 | T1 is the trigger |
| Identity, transport, TLS, external chain anchor | named | Day 29 | carried from Days 26, 27 |
| Missing decision route (`POST /approvals/{id}/decide`) | named | Day 29 | same gap as trace sink |
| Missing trace sink (exporter, retention) | named | Day 29 | `ai_os/observability.py` has no sink |
| ANN index + real vector DB | named, **ordered** (step 4 of 5) | T2: corpus × 10 | migration: cache → mmap → ANN → DB |
| Connection pool + read replica | named | Day 29 | T1: two processes |
| `AI_TRACING` sampling + retention | named | Day 29 | no sink → no retention policy |

---

## Three Carried Claims (Visible in Diagram)

```
Wrote? = no                                    (unchanged; no write path → nothing to scale)
Saleor rows changed: 0                         (Day 27 lifecycle number)
0 chats, 0 embeds, 0 SQL, 0 rows for MECH     (all N1–N9 fixtures; 28 days affordable)
```

---

## Ratchet Assertions (Falsifiable Diagram)

| Assertion | Check |
|-----------|-------|
| **N-diagram** | Every component id resolves to existing `file:line`; every cited file is used |
| **N-claims** | Every arrow label is a fixture id; every fixture id appears on an arrow |
| **N-config** | Every `os.getenv`/`_env_int`/`_env_float` name in `ai_os/` is in `.env.example` or named exception list |

**N-config census (before → after):**
```
Code-readable names: 60  (52 literal + 8 provider-template f-strings)
Documented in .env.example: 21 → 60
Undocumented: 39 → 0
Documented but unread: 1 (CMD_ZDR — only genuine exception)
```

---

## Load-Shedding Table (Admission Control)

**Scenario:** 3 principals, bucket `burst=5, rate=1/s`, fixed injected clock

| Principal | Requests Sent | Accepted | Refused | Retry-After (s) |
|-----------|---------------|----------|---------|-----------------|
| alice     | 8             | 5        | 3       | 1.0, 2.0, 3.0   |
| bob       | 6             | 5        | 1       | 1.0             |
| charlie   | 4             | 4        | 0       | —               |

**Assertions (N3):**
- Refused requests: **0 chats, 0 embeds, 0 SQL, 0 rows** downstream
- Per-principal counts non-zero for >1 principal (fairness visible, not claimed)
- Every refusal carries `retry_after`
- `RATE_LIMIT=none` restores old path exactly (control)

---

## Fallback Grading Rule (New)

> A golden verdict on a run whose `fallback_depth > 0` is **`unresolvable`** (not `wrong`), carrying the depth and the model that answered.
>
> Implemented as a pre-check in the bench's judge path. No model call, no new lane, no semantic grade.
>
> Belongs on Day 28 (deployment topology): single-provider deployment has `fallback_depth = 0` always; rule is invisible until T4 (second provider configured).