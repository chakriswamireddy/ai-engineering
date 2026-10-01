# Integration — The Discovery of Shared State

**Concept 19 of 20** (joins the 18 existing concepts in `docs/concepts/`)

---

## Definition

**Integration is not the sum of day suites.** It is the discovery of which assumptions about the world stop being true when modules that each passed alone are placed in one address space under one clock.

Every day suite since Day 7 constructed its own world:
- `tools/trace_smoke.py` — its own trace store, never filled
- `tools/data_smoke.py` — its own `sqlite3.connect(":memory:")` seeded from `SEED_SQL`
- `tools/state_smoke.py` — its own store file
- `tools/bench_agent.py` — its own `BenchGateway` answering by script

"Green" has only ever meant *green in that day's world*. **Assembly is where state stops being private.**

---

## The Four Shared Resources

A demonstration is the first artifact that has to live in one world. It has exactly one of each:

| Resource | Implementation | Bound | Integration Finding |
|----------|---------------|-------|---------------------|
| **Trace Store** | `OrderedDict` capped at 256 (`observability.py:714`) | `retention = process lifetime` | 7 scenarios fit; rerun loop evicts. Demo reports `held \| evicted`. |
| **Store Cache** | Identity-keyed (sha256 + model + dim), 1 store/corpus (`rag.py:410`) | Third corpus refused by name | S2 faster than S1 (warm store, not easier question). Reason travels with number. |
| **State Store** | One file, `get()` rewrites, `put` rewrites all (`state.py:607, 838`) | Safe for exactly 1 writer | S4's checkpoints + episodic index = 2 consumers serialized. Day 29 lock makes this true. |
| **Gateway & Ledger** | Module-level global with mutable `last_*` (`gateway.py:80`); per-process list (`ledger.py:65`) | Latency read in call window; cost = delta not total | Shared global trap: reading `last_latency_ms` after next scenario = wrong call. |

---

## The Invariant — One Sentence, Five Vocabularies

The same rule — *the answer may assert only what the evidence supports* — translated across the series:

| Day | Vocabulary | Check Function |
|-----|------------|----------------|
| 6 | `cited ⊆ retrieved` | `grounding_over_transcript` |
| 14 | `asserted ⊆ returned` | `expected_stats` on data records |
| 16 | `conclusion ⊆ evidence` | `conclusion_subset_evidence` |
| 17/18 | `recalled ⊆ authored` | `recalled_subset_authored` |
| 19 | `delegated ⊆ returned` | `topology_columns.delegated_subset_returned` |

**The demonstration's contribution:** Run all five on one artifact, calling the existing functions rather than re-typing. The fabricated twins (P8, Q8, T6, X1) still fail — proving the demonstration grades the system, not congratulates it.

---

## A Scenario Is a Fixture

The curriculum's ten columns are not a report template — they are **ten expectations written before the run**:

| Column | Expectation Type | Failure Mode |
|--------|------------------|--------------|
| input | Question (hashed) | Scenario quietly becomes different question |
| reasoning/workflow | Path (plan with step ids, or single call) | Workflow rendered as prose |
| retrieval | Evidence set with ids/scores | Retrieved set disagrees with invariant check |
| tools | Ordered sequence | Wrong tool used but answer looks right |
| evidence | The invariant (`cited ⊆ retrieved` etc.) | Invariant fails on fabricated twin |
| output | Answer with citations | Fabricated answer passes |
| latency | Measured number with caveat | Read outside call window / warm store not named |
| cost | Ledger delta with units | Reads process total not delta |
| evaluation score | Layer-1 deterministic verdict | Semantic cell flattered to pass |
| failure cases | Negative twin + refusal code | Negative twin missing or scripted |

**Rule:** A column with no expectation is refused. A record cell that is neither a fixture id nor a named `PENDING` is refused. The register cannot acquire a blank.

---

## Absence Is a Finding

Two of the seven scenarios cannot be demonstrated, and a demonstration that scripts around them is a brochure.

| Scenario | Missing Capability | Assertion Method |
|----------|-------------------|------------------|
| S5: *Why is this test failing?* | Test-runner tool, CI artifact, log-ingest, test outcome in corpus | Enumerate `registry.names()` (7 reads, 0 executors); assert no `ai_os/` spawns subprocess; assert no test outcome in corpus |
| S7: *Perform an approved operation* | Write path | Assert `statement_allow: [select, with]`; no `effect:write` grant; stores expose query only; Postgres read-only |

**A limitation asserted by enumeration can be closed and observed. A limitation admitted in prose is inherited by whoever edits the file next.**

---

## The Runner Is a Reader, Not a Router

`tools/demonstrate.py` presents each question to production exactly as a user would:
- Offline (default): stub gateway (`tools/mock_gateway.py`), stub stores, temp state file — **0 billed calls**
- `--live`: swaps only the chat wire for real gateway from `.env`

**It adds nothing:** no tool, no route, no span kind, no store, no model call.
**Its control (Z9):** With runner off, every prior fixture byte-identical. With runner on, every prior fixture still byte-identical. The only resource it changes is the trace store's contents — whose **consumption** is the thing under test.

---

## The Scorecard Is a Join, Not a Second Opinion

| Scenario | Harness | Demo Verdict | Harness Verdict |
|----------|---------|--------------|-----------------|
| S1/S2 | `bench_rag` (R1–R9), `retrieval_day10` (stub), `eval_harness` (golden) | PASS | PASS |
| S3 | `data_smoke` (Q1–Q8), `sql_attack_smoke` (X1–X11) | PASS | PASS |
| S4 | `plan_smoke` (P1–P9), `bench_agent` (AB1–AB9) | PASS | PASS |
| S5 | Absence fixture | PENDING | N/A |
| S6 | `trace_smoke` (O1–O9), `distributed_smoke` (D1–D9) | MECH/PARTIAL | PASS |
| S7 | `approval_smoke` (H1–H9) + absence | PENDING | PASS |

**A disagreement is a gate bug to fix, never a demo to adjust** (Day 19: the fixture is never weakened).

---

## The Demonstration Is a Falsifiable Document

Three documents, one register:

1. **`docs/demonstration.md`** — The artifact a reader who did not build the system opens first. Seven scenario records, scorecard, integration findings, defense transcript, carried claims, residual register.
2. **`docs/evaluations/integration-day30.md`** — The report: Z1–Z9 table, seven records, scorecard joins, four integration findings, host/image parity, cell accounting, PENDING lines.
3. **`docs/concepts/integration.md`** — This concept doc (the 19th in `docs/concepts/`).

All three keep the same three lines visible:
- **What exists** (mechanized, traced to fixture)
- **What does not** (PENDING with owner, or no owner deliberately)
- **Who owns each absence** (the residual register)

---

## The Ratchet

Day 28: Every box is a file (diagram ↔ spec bijection).
Day 29: An image is a subset with a manifest (lock refuses second writer).
Day 30: A demonstration is a set of pre-registered scenarios whose refusals are as load-bearing as its answers.

**Every cell is a fixture id or a named absence. A cell that is neither is refused.** This is the only thing that makes a demonstration document trustworthy six weeks from now.

---

## What Integration Does Not Mean

| Not Integration | Why |
|-----------------|-----|
| Running all day suites in sequence | They were written to verify *a world*; the demo has *one* world |
| "It ran end-to-end" | Proves 7 code paths didn't raise; proves nothing about the system or any specific answer |
| A dashboard / notebook / live screenshot | The demo's cost claim is reproducibility at **0 billed calls**; `--live` is a labelled add-on |
| Quality claims | Semantic cells stay `PENDING` (Day 20 L2 / Day 21); a green scenario proves the path, not the model |

---

## The Honest Negative

The most valuable rows in the register are the ones the system must decline:

- **S5** enumerates: `registry.names() = 7 reads, 0 executors`; no subprocess; no test outcome in corpus. The categorical finding: a `run_tests` tool would be the first request to ask the system to **execute something** — a different authority from every read tool, needing a sandbox the permission model doesn't have.
- **S7** enumerates: `statement_allow = [select, with]`; no `effect:write` grant; stores have query only; Postgres read-only. The decision (request, digest, principal, refusal) is the entire security event. `Saleor rows changed: 0`.

**A demonstration whose negative cells are blank measured only the code it just read.**

---

## Related Concepts

- `docs/concepts/observability.md` (Day 23) — Trace core, the store this concept measures
- `docs/concepts/distributed-tracing.md` (Day 24) — Cross-process propagation
- `docs/concepts/deployment.md` (Day 29) — Single-writer lock, image manifest
- `docs/concepts/evaluation.md` (Day 20) — Layer-1 deterministic verdicts
- `docs/concepts/planning.md` (Day 16) — Plan adherence, conclusion ⊆ evidence
- `docs/concepts/state.md` (Day 17) — Checkpoints, the file this concept contends
- `docs/concepts/memory.md` (Day 18) — Episodic/semantic, recalled ⊆ authored
- `docs/concepts/architecture.md` (Day 19) — Specialists, delegated ⊆ returned

---

*This concept exists because Day 30 was the first day that had to place seventeen private worlds in one room and measure what broke. The four shared resources above are not worries — they are file:line references. The next concept (`deployment.md`, Day 29) is the 20th and final concept in this series.*