# State Day 17 — the store as a contract (S1…S9)

> Generated from the run of `python tools/state_smoke.py` (offline: the Day-4
> scripted stub for the *chat* wire, the Day-7 doc corpus / Day-10 code corpus for
> *retrieval*, and the Day-14 seeded in-memory sqlite core subset for the *data*
> side). The store is file-backed JSON on a `tempfile`; the wall clock is
> **injectable**, so TTL arithmetic is deterministic (the base is real time, so an
> `expires_at` is honestly in the future for any other process reading the same
> file).
> `MECH` = provable offline, here. `PENDING` = a real gateway is required. Two
> columns carry the day: **`recalled ⊆ authored` = `ok` on every row that recalls**
> and **the `S9` row** — state off ≡ Days 12–16 (the counterweight, exactly as
> `over-planned? = no` was Day 16's). A green table with either column broken is a
> failed day.

```
stores: 9 doc chunks (STUB) / 14 code symbols (STUB)   tools: 7 registered
τ_doc=0.05  τ_code=0.75   STATE_DEFAULT_TTL=86400  STATE_MAX_RECORDS=1000
```

## The fixtures, pre-registered before the run (§8)

| ID | What it proves | Input / injection | Expected |
|---|---|---|---|
| S1 | working state | two turns of one task | plan position + hypothesis from turn 1 **served at turn 2**; no re-derivation |
| S2 | conversation state | a multi-turn exchange | transcript persists per session/principal; back-references answered; self-correction stays attributed |
| S3 | task resume | interrupt after step *k* | **resumes at k+1 in a new process** from the store (Day 16's P4, cold) |
| S4 | serialize/replay | any stored run | byte-identical round-trip; replay from step 0 reproduces the same evidence |
| S5 | retention | a record past `expires_at` | **acts absent** in get/list_for/recall; compaction removes it; hostile tiny-TTL proves eviction |
| S6 | deletion | `forget(principal, key)` | record + derived aggregate removed; **unreachable everywhere**; visible in the trace |
| S7 | provenance | a write with no `created_by`; a runtime-imported record | write **rejected at write time**; every record carries run/step/source; the import is unreachable |
| S8 | boundary | a cross-owner read | **nothing returned**; no run-to-run bleed except explicit Day 18 distillation |
| S9 | **control** | state **disabled** | Days 12–16 fixtures run **unchanged, exactly as green**; over-persistence is a bug |

The `MECH`/`PENDING` split is exactly Day 7's, and this time it is nearly all one
side: **S1–S9 are all `MECH`** — deterministic functions of the store, the TTL math,
the keying, the config, and stored payloads: offline, free, reproducible. The
`PENDING` is the semantic month two days away — relevance-scored recall of episodic
records under a real model, distillation validity, and *"does memory make real
answers better"* as **Day 21's** benchmark on Day 17's store. The stub proves the
**store**; a real model proves the **memory**.

## §9 state table (every cell a real run's)

| id | kind | state written | stored as | recalled | provenance | ttl | expired | deleted | resume | `recalled ⊆ authored` | cost |
|---|---|---|---|---|---|---|---|---|---|---|---|
| S1 | task | plan pos + hypothesis | record | y | `r1/s1/executor` | 86400 | no | no | — | **ok** | $0.00000660 |
| S2 | session | transcript delta | record | y | `r2/4/session` | 86400 | no | no | — | **ok** | $0.00003060 |
| S3 | task | checkpoint k | record | y | `r9/s1/executor` | 86400 | no | no | **y** | **ok** | $0.00003060 |
| S4 | task | full run | file | y | `r11/s2/executor` | 86400 | no | no | **y** | **ok** | $0.00000000 |
| S5 | task | expired record | (absent) | n | `run/s1/executor` | past | **y** | no | — | **ok** | $0.00000000 |
| S6 | session | `forget()` | (gone) | n | `run/1/session` | — | no | **y** | — | **ok** | $0.00000000 |
| S7 | task | no `created_by` / import | rejected | n | — | — | no | no | — | **ok** | $0.00000000 |
| S8 | task | cross-owner read | (none) | n | — | — | no | no | — | **ok** | $0.00000000 |
| S9 | — | state off | — | — | — | — | no | no | — | **ok** | $0.00000000 |

`recalled ⊆ authored = ok on every row that recalls: YES`. The `S9` row is the
counterweight: with state off there is nothing to recall and nothing changed. Every
row is **0 chats / 0 embeds** of *state* — the store is local I/O, one JSON append
per checkpoint; the premium is **latency and disk, not money**.

## What the run actually produced

**S1 — working state is a lookup, not a re-derivation.** Turn 1 (`stop_after=1`)
flushes a task record at `/task/t1`; reading it back through `working_state()` serves
`resume_from=2`, the running hypothesis, and the completed step's `status: done` —
so "the plan is at step 2, the hypothesis is payment-declined" is true *tomorrow*
without re-planning. Provenance reached the reader (`source: executor`, `run_id r1`),
and `recalled_subset_authored` holds.

**S2 — conversation state persists per session, and does not bleed.** After turn 1
(`Fetch order 1`) the conversation record at `/conversation/conv-1` holds two turns
with `source: session`; turn 2 loads **2 prior turns** and answers the back-reference
without re-asking. A different session (`conv-2`) starts **empty**, and a different
principal cannot read `conv-1` at all — the boundary is by key, not by discipline.

**S3 — resume crosses a process.** After an interruption the run returns
`termination_reason: "paused"` with **one** checkpoint. A **genuinely new process**
(`--resume-child`: a fresh interpreter, a fresh `StateStore` over the same file)
continues with `resumed_from_store: true`, executes **only the remaining step**
(`tool_steps: 1`, a counting registry shows `["get_order"]`), keeps the paused run's
evidence, and reaches a grounded answer (`conclusion ⊆ evidence`). In-process, a
fresh store object gives the same result: `resume_from` advances to *k+2* and the
earlier step is never re-run. Resume is safe because the tools are **read-only and
re-runnable** — no side effect can be double-applied.

**S4 — serialization is the contract.** `put → dump → load → get` returns a
**byte-identical** payload (`round_trip(...)["identical"] is True`), and a replay
from step 0 reproduces the **same evidence union** as the original — so "the same run
graded twice is the same row" is true for a stored run, and resume/replay both
consume the *serialized* form, never the live object.

**S5 — retention, proven by hostile config.** A record past `expires_at` **acts
absent** through `get`, `list_for`, and `recall`; `expire()` counts it (1) and
`compact()` physically reclaims it (`removed=1, len=0`). A tiny-TTL store (1.0 s)
with five staggered writes expires **all five**, and compaction leaves an empty
store. A `max_records=2` store evicts the **oldest** record under the cap (three
writes → `len 2`, `/w/0` gone). Eviction is a code decision, not a mood.

**S6 — deletion removes the record *and* its aggregate.** `forget("d1", "/task/x")`
removes one record; it is afterwards unreachable through `get`, `list_for`, and
`recall` (the derived index was purged), `delete` confirms it is gone, and the rest
of the store is untouched. The trace carries `deleted: true` — deletion is visible,
not an omission. A deleted memory that "sometimes reappears" is the same bug as a
fabricated citation, on a different shelf.

**S7 — the store is closed.** A write with `created_by=None` raises `ProvenanceError`;
a write from a foreign source (`source: "import"`) raises too. A record *planted in
the file* with no provenance — and one with an un-authored `created_by` — is **not
admitted on load** (`dropped=['/task/ghost', '/task/ghost2']`), so it is unreachable
through `get`, `list_for`, and `recall`. The authored record is admitted and
recallable; every recalled record carries a `run/step/source` triple. Memory the
system did not write is not memory — it is a channel for Day 25's injection that has
not happened yet.

**S8 — the read path is context-keyed.** Owner A cannot `get` owner B's record,
A's `list_for` and `recall` exclude B's shelf, and an unknown owner reads nothing at
all. No run-to-run bleed exists except by explicit distillation (Day 18).

**S9 — the counterweight.** A disabled store writes nothing (`len 0`, no file),
returns nothing, and reports `state_on_off: off`. A run with **no store** produces an
answer, evidence, and a termination identical to the baseline run — state off ≡ Days
12–16 — and the Days 7–16 suites re-run unchanged (§No-regression below). A
persistence layer that changed a memory-free answer would be an **S9 failure arriving
through the front door**.

## Layer mechanics (the day's own unit checks)

| Assertion | Result |
|---|---|
| `put` rejects a write with no `created_by` (`ProvenanceError`, fail-closed at ingress) | ok |
| `put` rejects a foreign `source` — the store is **closed** to writes it did not make | ok |
| a provenance-less record in the file is **not admitted on load** (unreachable via `get`/`list_for`/`recall`) | ok |
| `get`/`list_for`/`recall` consult expiry **first** — an expired record **acts absent** | ok |
| `compact` physically reclaims the expired; `expire` counts them first | ok |
| hostile tiny-TTL empties the shelf; `max_records` evicts **oldest-first** | ok |
| `forget` removes the record **and** the derived index; unreachable everywhere; `delete` confirms | ok |
| `round_trip` (`put → dump → load → get`) is **byte-identical** (the S4 contract) | ok |
| `recall` is keyed by `principal` — a cross-owner read returns nothing (S8) | ok |
| `recalled_subset_authored` holds on every recalled record; `state_columns` carries `run/step/source` | ok |
| a disabled store makes every read return nothing and every write a no-op (the OFF switch) | ok |

## No-regression line (Days 7–16 ride the store)

| Line | Result |
|---|---|
| `test_day7.py` … `test_day10.py` re-run in subprocesses | **exit 0 — `ALL DAY 7..10 (MECH) CHECKS PASSED`** |
| `tools/tool_smoke.py` (T1–T7) | **exits 0 — `ALL DAY 11 (MECH) CHECKS PASSED`** |
| `tools/agent_smoke.py` (A1–A8) | **exits 0 — `ALL DAY 12 (MECH) CHECKS PASSED`** |
| `tools/failure_smoke.py` (F1–F9) | **exits 0 — `ALL DAY 13 (MECH) CHECKS PASSED`** |
| `tools/data_smoke.py` (Q1–Q8) | **exits 0 — `ALL DAY 14 (MECH) CHECKS PASSED`** |
| `tools/sql_attack_smoke.py` (X1–X11) | **exits 0 — `ALL DAY 15 (MECH) CHECKS PASSED`** |
| `tools/plan_smoke.py` (P1–P9) | **exits 0 — `ALL DAY 16 (MECH) CHECKS PASSED`** |

The store is **additive** — a cold shelf the loop and the executor flush to — and it
changes **one** thing in existing code: `run_agent` gains an optional `state` (+
`principal`/`session_id`/`task_id`/`run_id`) and `run_plan` gains an optional
`state_store` (+ `resume_from_store`). With `state=None`/`state_store=None`, the
transcript is byte-for-byte Day 12 and the executor is Day 16 unchanged, which is why
state off reproduces Days 12–16 and why a store that changed a single-step answer
would be an **S9** failure. `ai_os/trace.py` gains only new symbols
(`recalled_subset_authored`, `state_columns`) — no existing function or row shape
moved.

## Cost shape (state's premium is latency, not money)

| Event | What it spends |
|---|---|
| the **`MECH` layer** (the store, TTL math, keying, provenance, deletion) | **0 chats, 0 embeds** — dict CRUD and arithmetic |
| a **checkpoint flush** (a task-record `put` per step) | **0 chats, 0 embeds** — one JSON append; the premium is a store round-trip |
| **conversation** persistence (load prior turns, append the exchange) | **0 chats** — the transcript is data, not generation |
| a **resume** across a process | **0 extra chats vs a fresh run** — the completed step is *skipped*, not re-run; the saving is the point |
| **recall** of an episodic record | **0 chats** today (verbatim lookup); *relevance-scored* recall is **Day 18's** bill |
| **distillation** (episodes → concepts) | **not run** — `PENDING` (Day 18); a silently-run distiller would be the failure |

The bet the day records honestly: state adds **zero model cost on the answer path**;
its only bill is disk and latency, both measured per fixture. Memory that costs a
chat to recall is Day 18's, and its meter is recorded, not assumed.

## Config knobs (Day 17)

| Key | Default | Meaning |
|---|---|---|
| `STATE_STORE_PATH` | unset | the file-backed store's path; unset → no persistence on disk |
| `STATE_DEFAULT_TTL` | `86400` (24h) | the TTL a `ttl="inherit"` write receives |
| `STATE_MAX_RECORDS` | `1000` | the size cap (oldest-first eviction) |
| `STATE_PRINCIPAL` | `default` | the owner used when a run names none |
| record `schema` | `2` | the versioned record schema (a contract, so a future agent can read a past run) |

## Negatives / `PENDING` (honest, with owners)

1. **Relevance-scored recall of episodic records** — the store serves a recorded
   investigation verbatim by `task_id`; scoring candidate episodes for a *new*
   question is **Day 18/21's**. `PENDING`.
2. **Semantic distillation** — episodes → grounded concepts, with `authored ⊆
   grounded` — is named, not built. Owner: **Day 18**.
3. **Does memory make real answers better?** — the stub proves the *store*; the
   *memory's* usefulness is **Day 21's** benchmark on this store. `PENDING`.
4. **Streaming conversation state** — message ordering across Day 4's mid-stream SSE
   cuts is **named, not built**.
5. **Multi-process synchronization** — the store is single-writer by construction;
   two processes writing one task record concurrently is a recorded seam.
6. **`recalled ⊆ authored` is provenance-shaped** — it proves *who wrote* a record,
   not that it is *true*; grounding a distilled claim is Day 18's, adjudication is Day
   21's.

## Re-verify, in one line

```bash
python tools/state_smoke.py    # S1–S9 + the Days 7–16 no-regression re-run
```

**`recalled ⊆ authored = ok` on every row that recalls**, and the **S9** row holds:
state off ≡ Days 12–16, with Days 7–16's suites green through the store. The
`PENDING` line, plainly: **whether a real model's relevance-scored recall and
semantic distillation make answers better is Day 18's system and Day 21's verdict on
this store.**
