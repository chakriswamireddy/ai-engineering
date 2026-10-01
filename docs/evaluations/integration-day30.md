# Day 30 Evaluation — Integration, Evaluation and Defense

**Report on the complete technical demonstration of the AI Engineering OS.**

---

## Z1–Z9 Fixture Results

| ID | Proves | Assertion | Result | Falsified By |
|----|--------|-----------|--------|--------------|
| **Z1** | Registry is the demonstration | Every `scenarios.yaml` scenario runs; every record has all 10 required columns; every route resolves (HTTP in `server.py` or library call in `ai_os/`); phantom scenario or unaccounted cell refused | **PASS** | A phantom scenario, or an unaccounted record cell (the brochure) |
| **Z2** | Knowledge and Code | S1/S2 assert `cited ⊆ retrieved`; S2 citations are `module#Symbol`; fabricated twin fails; each records store identity | **PASS** | Green `cited ⊆ retrieved` with cited-and-absent id, or answer not attributable to store identity |
| **Z3** | Data | S3 asserts `asserted ⊆ returned` on revenue; backend label `stub (SEED_SQL)`; `Wrote? = no`; attack twin `DELETE` stops (policy_violations: 1, 0 DB execution) | **PASS** | Number absent from result set, silent backend substitution, or write reaching connection |
| **Z4** | Customer Investigation | S4 asserts `conclusion ⊆ evidence` both ways (grounded passes, fabricated fails) and `recalled ⊆ authored`; checkpoint written/read; reasoning names plan steps; state writes = k+1 | **PASS** | Fabricated cause passes, recalled record without authored provenance, plan rendered as prose |
| **Z5** | Engineering Investigation (Absence) | S5 asserts no test-runner tool in `registry.names()`; no `ai_os/` module spawns subprocess/shell; neither corpus holds test outcome; pending names owner with categorical reason (executing code = new authority) | **PASS** | A scripted "test failure explanation" — that is the padding mistake and is `FAIL`, not `pass` |
| **Z6** | Operational Investigation | S6 fetches trace via `obs.default_store().get(trace_id)`; asserts provenance (root, parent paths, monotonic duration, prompt refs, cost == ledger delta); asserts aggregate answer NOT produced; evicted trace reported `evicted` | **PASS** | Aggregate latency claim, trace total disagrees with ledger, eviction reported as data gap |
| **Z7** | Controlled Action (Absence) | S7 asserts no `effect:write` grant; `statement_allow: [select, with]`; one query method per store, no mutation; Postgres session read-only | **PASS** | Write path exists, write grant in file, or "applied" action with non-zero row delta |
| **Z8** | Scorecard | Every scenario maps to ≥1 existing harness; demo verdict and harness verdict agree on shared cells; every pending has owner; scenario with empty pending but unproven cell refused; S3/S4 show `n/a` on golden | **PASS** | Scenario with no harness, disagreeing verdict silently averaged, ownerless pending, golden checkmark on S3/S4 |
| **Z9** | Control — Passive Runner | Offline demo with runner on/off: every prior fixture byte-identical (`regress_all.py:59-77` + 2 layer-1 replays); 0 billed calls added; 7 scenarios run twice (host + Day 29 image) with identical verdicts | **PASS** | Prior fixture moves with runner on, or host/image verdict difference |

**All Z1–Z9 PASS.**

---

## Seven Scenario Records (Summary)

| t | Event | Record | The Honest Cell |
|---|-------|--------|-----------------|
| 0 | Context built | 1 gateway · 1 state store · 1 trace store · 1 cache | Integration assumption stated |
| 1 | S1 answers from docs store | `cited ⊆ retrieved`; `cache: docs miss→hit` | Retrieval may miss while invariant holds (Day 21) |
| 2 | S2 answers from code store | `cited ⊆ retrieved`; `cache: code miss→hit` | **S2 faster than S1 for reason not the question** (warm store) |
| 3 | S3 answers from SEED_SQL | `asserted ⊆ returned`; `backend: stub (SEED_SQL)`; `Wrote? = no` | Silent seam named, never trusted |
| 4 | S4 runs stored plan | `conclusion ⊆ evidence`; `recalled ⊆ authored`; k+1 state writes; recall 0 chats | Stored plan proves composition, never plan quality |
| 5 | S5 reports no tool | `finding: no test-runner` | Absence enumerated, not admitted |
| 6 | S6 fetches t=1's trace | `held` \| `evicted`; cost == ledger delta | Trace cap is consumer's problem (Day 23) |
| 7 | S7 reports no write path | `Saleor rows changed: 0` | Decision is the event; nothing moved |

---

## Four Integration Findings (Measured, Not Inherited)

### 1. Trace Store Cap (observability.py:714)
- **Bound**: 256 traces per process (OrderedDict, oldest-first eviction)
- **Measured**: 7 scenarios → 7 roots, `traces_held=7`, `evicted_count=0`
- **Finding**: Cap does not bind in single demo; **will bind in rerun loop**. Demo reports `held | evicted`, states `retention = process lifetime`. Does not claim to be an observability tool.

### 2. Store Cache (rag.py:410–484, Day 28)
- **Mechanism**: Identity-keyed cache (sha256 + embedding_model + dimension), one store per corpus
- **Measured**: S1: docs miss→hit; S2: code miss→hit; **third corpus refused by name** (not evicted)
- **Effect**: S2 latency 89ms < S1 142ms **because store warm, not question easier**
- **Finding**: Reason travels with number; LRU would make "which store answered" a timing question.

### 3. State Store Contention (state.py:607, 838–845)
- **Mechanism**: One file, `get()` rewrites, `put` rewrites all (tmp + os.replace), no lock
- **Measured**: S4 writes 6 records (5 checkpoints + 1 episodic = k+1), serialized in one thread
- **Guard**: Day 29 `SingleWriter` lock (fcntl.flock) makes single-writer assumption **true not lucky**
- **Finding**: File safe for exactly one writer; assumption breaks when second replica appears — Day 29 refuses at boot.

### 4. Gateway Global & Ledger (gateway.py:80–82, ledger.py:65)
- **Mechanism**: Module-level gateway with mutable `last_*`; per-process ledger list
- **Discipline**: Latency read **inside call window** (context rejects late read); cost = **ledger delta** not total
- **Honesty note**: "Demo's latency column measures one call in shared process, not a benchmark"; median of own calls reported.

---

## Findings the Run Did Not Produce (Predicted, Not Observed)

| Finding | Predicted By | Demo Status |
|---------|--------------|-------------|
| Two-writer corruption | Day 28 diagram, Day 29 N4 | **Not reachable** — one process, Day 29 lock refuses second. Demo records prediction, guard, and that it cannot exercise it. Does **not** claim corruption is gone. |
| `$0.00` failure cost | Day 28 (ledger.py:99) | **Confirmed by reading** — invisible offline (no failures), real live. Recorded as Day 28 finding confirmed, not claimed by measuring. |
| Database substitution | Day 14 (data.py:798–806) | **Invisible on stub by construction** — S3's backend label (`stub (SEED_SQL)` vs `postgres`) is the cell that matters. Demo asserts the **label**, not the backend choice. |

---

## Host / Image Parity (Z9 Extension)

| Environment | Gateway | S1–S4 Mode | Verdict |
|-------------|---------|------------|---------|
| Host (offline) | Stub (mock_gateway) | Stub | PASS |
| Day 29 Image (offline) | None (no .env) | Stub | PASS (expected — image has no .env, gateway=None) |

**Verdict**: Identical. The demonstration is a property of the **artifact**, not the laptop.

---

## Cell Accounting

| Metric | Count | Method |
|--------|-------|--------|
| Total cells (7 × 10) | 70 | Curriculum specification |
| Traced to fixture (MECH) | 52 | Z1-Z9 enumerated cells |
| Pending (named owner) | 18 | Scenario `pending` lists |
| **Unaccounted** | **0** | **Target zero — Z1, Z8 assertion** |

**Method line:** Ten columns per scenario. A cell is "filled" if it contains a fixture id (trace_id, harness name, file:line) or a measured number with its caveat. A cell is "pending" if it names an owner. A cell that is neither is refused by the harness.

---

## Cost Shape

| Mode | Scenarios | Billed Calls | Total Cost | Notes |
|------|-----------|--------------|------------|-------|
| Offline (default) | 7 | 0 | $0.00 | Stub gateway, embedded stores, temp state file |
| Live (--live) | 7 | Variable | Variable | Real gateway from .env; retrieval on given store; labelled handoff |

**The series' cost line: 0 billed calls for the entire offline demonstration — seven scenarios, ten columns, zero real model calls.**

---

## PENDING Lines with Owners

| Item | Owner | Why It Remains |
|------|-------|----------------|
| Real code store | Day 10 | Quota-blocked (Cloudflare 10k neurons/day covers ~16k of 29,156 symbols) |
| Real-model observation | `--live` handoff (Day 21/22) | Stub scripts fixture; real model never observed in 30 days |
| Semantic evaluation (L2/L3) | Day 20 L2 / Day 21 | Unchanged; a demo proves path, never judgment |
| Test-runner tool (`run_tests`) | Future ingestion + **sandbox** day | Categorical: executing untrusted code = new authority |
| Write path / refund | **No owner, deliberately** | Day 27 judgment: first write destroys `Wrote? = no` |
| Agent route / auth / decision route | Day 29 (identity lands) | Carried from Day 26/27/29 |
| Trace sink, retention, metric baseline | Day 28 (storage decision refused) | No sink exists; "latency increased" unanswerable across window |
| Real Postgres for S3 | Day 14 seam | Substitutes silently at data.py:798 — demo asserts backend label |

---

## Corrections to the Record (This Day's Own Plan)

1. **"43 modules under ai_os/"** → **40 modules + 3 datasets** (questions.yaml, code_questions.yaml, golden.yaml). The day adds 1 dataset (scenarios.yaml).
2. **Concept-doc count 18** → Measured **18** (Day 24's distributed-tracing.md included); Day 30's integration.md = 19th; Day 29's deployment.md = 20th.
3. **S4 cost `writes: 2`** → Real shape **k+1** (Day 17: checkpoint per step + Day 18: 1 episodic index).
4. **GET /traces/{id} conflated with obs.default_store().get()** → S6 in-process uses the **latter**; route is for curl, store is for demo.
5. **Scorecard S1–S4 → eval_harness** → **Only S1/S2 have golden rows** (doc 9 + code 6 + F/G/H = 18). S3/S4 have no golden row — show `n/a`.
6. **"0 chats, 0 embeds"** → Defined as **0 billed calls** (stub serves chats/embeds on :8700/:8701; series phrase means 0 calls to real provider).

---

## The Day's Honest Sentence

> **What exists after today is a demonstrated, accounted-for system: seven scenarios recorded with ten columns each, a scorecard joined to the harnesses that grade them, four measured integration findings that no per-day fixture could have produced, two absences asserted by enumeration, and a register in which every claim is a fixture id or a named owner with zero unaccounted cells.**

> What does **not** exist: no write path, no test-runner tool, no agent route, no authenticated surface, no decision route, no trace sink, no retention, no metric baseline, no semantic evaluation, no real code store, no real-model observation — and **no claim that the system is finished**, which is the one sentence a rollup is never allowed to write.

---

*Checker: `python tools/demo_smoke.py` (Z1–Z9) • Regression gate: `python tools/regress_all.py` (runner-off equivalence) • Demonstration: `python tools/demonstrate.py` (offline, 0 billed calls)*