# Day 17 — Memory and State

## Objective

Days 12–16 built an agent that *remembers while it works*: Day 12 made the transcript the loop's memory (agent state = the messages, no hidden variable), Day 13 hung guards on that loop, and Day 16 gave it a plan with **execution state and checkpoints** — while being explicit that all of it is **ephemeral**, living only for the run, and that "the persistent store is Day 17's seam, named not built." Today the seam is built. The curriculum's question is *"Understand what an agent needs to remember"*, and its answer is the day's structure: start with the three states — **working state** (the facts and hypotheses of *this* task, Day 16's execution state promoted to first-class), **conversation state** (Day 12's transcript, plus the turn/session context that makes it answerable), and **task state** (the plan, its steps, its evidence, and its budget ledger — Day 16's plan as a durable record) — and only then ask whether *persistent memory* is useful. The day's discipline is to keep **state and memory distinct**: state is *pinned to a context* (loaded because the run that owns it is running), memory is *retrievable across contexts* (generalized facts that survive their origin); the three states are Day 17's, episodic and semantic memory are **Day 18's** — and the curriculum says to introduce persistent memory *only if useful*, so the day must **measure** that usefulness instead of assuming it. The deliverable is the **agent state/checkpoint system**: Day 16's checkpoint record becomes a real **store**, with a **serialization/replay** contract (a run interrupted at step *k* resumes at *k+1* in a new process — S3), a **retention policy** (TTL expiry and first-class **deletion** — a store that only grows is a memory leak with a dashboard), provenance that makes every recalled claim attributable to the run that **authored** it (**`recalled ⊆ authored`**, the honesty invariant's fourth translation: you may only remember what the system itself wrote under its own grounding rules), and the control that proves the addition is additive — with state off, the system is Days 12–16 byte-for-byte (S9). What crosses the line into general knowledge — *distillation* into semantic memory — is named, not built: that is Day 18.

## Duties Outline

### 1. Learn: State vs Memory — What Does an Agent Need to Remember? (45 min)

- [ ] Name the distinction the curriculum opens with: **state vs memory**. State is *pinned to context* — working facts, this conversation, this task; it is loaded because *this* context owns it and it dies with that context (Day 12's transcript died with the process). Memory is *retrievable from the past* — a fact that outlives its context and can be brought to a *different* one. Day 17 owns the former; Day 18 owns the (distilled) latter.
- [ ] Place the curriculum's list on one axis by lifetime and generality: **working state** (short-term, task-pinned), **conversation state** (session-pinned), **task state** (run-pinned, durable record), **episodic memory** (*what happened*, a record), **semantic memory** (*what is generally true*, distilled knowledge). The first three are Day 17; the last two are Day 18 — and the day's job is to keep the shelf boundaries honest, not to blur them into "stuff we stored".
- [ ] Name the failure at both extremes: treating everything as *state* (a transcript that never evicts, an unbounded store) is a leak; treating everything as *memory* (generalized facts that bleed run-to-run and go stale) is a lie — S8 exists because a working fact served back as a general truth is how an agent starts hallucinating about its own past.
- [ ] Learn the curriculum's loaded condition: **"introduce persistent memory only if useful."** Usefulness is *measured or refused*, never assumed: the fixture set carries both an S-fixture that demands memory be load-bearing (S3 — resumption) and a control that demands it be invisible when it isn't (S9). A memory that changes no answer in the control set is wasted budget, not a free win.
- [ ] Pre-commit the day's invariant from the start: **`recalled ⊆ authored`** — a record is recallable only if the system itself wrote it under its own honest rules, carrying provenance (`run_id`, step, source). Memory the system did not author (an imported, injected, or unrecorded fact) is not memory — it is a channel for Day 25's injection that just has not happened yet, so the store is **closed** to writes it did not make (Day 15's privilege-beats-parser reflex, inverted onto persistence).

### 2. Learn: The Three States, and Where Each One Lives (45 min)

- [ ] Learn **working state** as Day 16's execution state promoted: the current plan step, the observations, the running hypothesis — the *task-shaped* working memory Day 16 kept in-run and explicitly named Day 17 as owner of. It lives as long as the task does, and it is never shared with another task except by explicit distillation (Day 18).
- [ ] Learn **conversation state** as Day 12's transcript plus the honesty Day 12 already claimed: agent state = the messages, but session identity, which turn a plan was agreed at, and the user's stated preference are *headers on the state*, not hidden variables — Day 12's no-hidden-variable rule survives the upgrade to a store.
- [ ] Learn **task state** as Day 16's plan made durable: `Plan`, steps, statuses, evidence, budget ledger, termination reason — the run's full record, so Day 16's *"where is the plan?"* is answerable tomorrow, not just mid-run. This is the curriculum's "task state" read properly: not a loose note, but the run's actual state machine at rest.
- [ ] Learn **short-term memory** as the umbrella over those three: the store they share is a **bounded, expiring, context-swapped-in** store — not free-form long-term storage. Sizing is a policy (`max_records`, `ttl`); eviction is explicit (LRU or TTL), because a working context that never evicts is the token-cost version of Day 13's non-terminating loop.
- [ ] Learn the seam the curriculum draws and the day must not cross: **episodic** memory records *what happened* — that is Day 17's task state at rest, recallable verbatim; **semantic** memory distills *what is generally true* (a known Saleor concept, a reusable workflow) — that is Day 18. The difference is a receipt vs a lesson: Day 17 stores receipts and names lessons.
- [ ] Pre-commit the ordering: Day 17 builds the states, persists them, and *then* asks "is distillation useful?" — with the answer honestly allowed to be *not yet*, recorded as the Day 18 seam rather than papered over with a long TTL.

### 3. Design: The State/Checkpoint System — One Shelf per Lifetime (30 min)

- [ ] Design the tiered store, one shelf per lifetime, so "where does this memory live?" has one canonical answer:
  ```text
  working state       task-scoped,   survives a turn,    ephemeral-by-default   (17: store)
  conversation state  session-scoped, survives a session                       (17: store)
  task state          run-scoped,    survives a process crash → resumable      (17: store)
  episodic record     one task's full record, append-only, TTL                (17: format; 18: retrieval)
  semantic memory     distilled, generalized, relevance-scored                (18: only if useful)
  ```
- [ ] Decide what Day 16 handed off: **the checkpoint is the persistence candidate** — same shape (step *k* done, evidence held, hypothesis running) — now written to a *store* instead of an in-run list. Day 17's deliverable is the **store**, not a new shape: Day 16 built the format, Day 17 builds where it lives.
- [ ] Design decision #1 — **state is data, and its schema is a contract.** Every record is `{kind, principal, key, payload, ttl, expires_at, created_by (run_id + step + source), created_at, schema}`. Versioning is explicit (`schema: 2`), so a future agent can read a past run — "can't read my own diary" is a schema failure, not a mystery.
- [ ] Design decision #2 — **provenance is mandatory, not metadata.** Every record states who wrote it, from what evidence it derives, and when; a write with no `created_by` is **rejected at write time** (fail-closed, Day 15's reflex). Provenance is the raw material of `recalled ⊆ authored`.
- [ ] Design decision #3 — **the store is closed.** Only the agent runtime writes; only context-matching readers (`principal` + `kind` + key prefix) recall; nothing is imported at runtime. An open store is how Day 25's injection becomes a *memory* — Day 15 relocated the guarantee to the database role; Day 17 relocates it to the write path.
- [ ] Design decision #4 — **deletion is a first-class operation, not an omission.** `forget(principal, key)` removes the record *and its derived aggregates*; expired records act as absent (S5) and a compaction pass physically removes them. A store that only grows is a memory leak with a dashboard.
- [ ] Pre-commit the three invariants: **(a)** `recalled ⊆ authored` — nothing is recallable that the system did not write, with provenance; **(b)** state is swapped in by context only — working/conversation/task records never bleed across owners except by explicit distillation (Day 18); **(c)** with state disabled, behavior is Days 12–16 byte-for-byte (S9 — the over-persistence counterweight, Day 16's P9 reflex on the store).

### 4. Build: Working + Conversation State — the Store (1 hour)

- [ ] Build the store as the data seam behind Day 12/16's ephemeral structs: `StateStore` with `put / get / list_for(principal, kind, key_prefix) / delete / expire`, file-backed JSON (like the corpus stores) so it is inspectable, replayable, and verifiable offline — the series' "state is data" rule, with no hidden store.
- [ ] Wire **working state**: Day 16's executor reads and writes the current plan position and the running hypothesis *through the store*, so "the plan is at step 4" is true for the *next* turn (S1) — the exact cell Day 16 deleted the code for, now restored as a store write instead of a liveness assumption.
- [ ] Wire **conversation state**: Day 12's transcript persists per session/principal, so a multi-turn exchange is answerable the way a human conversation is — the context survives the request/response boundary, and Day 12/13's self-correction turns stay attributed to the same conversation (S2).
- [ ] Use Day 14/15's config reflex: all sizing is policy — `STATE_STORE_PATH`, `STATE_DEFAULT_TTL`, `STATE_MAX_RECORDS`, `STATE_PRINCIPAL` — so a TTL or eviction policy is a config change, not a code change, and the tests prove eviction by running *hostile* configs (tiny TTL, empty store) as fixtures.
- [ ] Prove the honest seam: **serialization is the contract.** A record must round-trip (`put` → dump → load → `get`) to a byte-identical payload (S4) — because resume and replay both consume the *serialized* form, never the live object.
- [ ] Keep Day 12's no-hidden-variable rule intact: the store **augments the transcript, it does not replace it**. A run must be reconstructible from transcript + store, and a run whose store disagrees with its transcript is a *trace* failure, not a preference.

### 5. Build: Task State and Resume — Day 16's Checkpoint, Made Durable (1 hour)

- [ ] Build **task state** as Day 16's Plan made a record: after every step, `put("/task/{task_id}", {plan, step_statuses, evidence, hypothesis, budget_ledger, termination_reason})` — the checkpoint is now a *store write*, and "where is the plan?" is answerable in a new process (S3).
- [ ] Implement **resume**: on start, an optional `task_id` loads the task record, marks step *k+1* `pending → running`, and the executor walks the remaining steps without re-running 1…*k* — the concrete payoff Day 16 named for Day 17. Record honestly *why* resume is safe here: the agent's tools are **read-only and re-runnable** (get_order, search, describe_schema — none mutate), so a resumed run cannot double-apply a side effect; a mutating executor could never offer resume for free, and that limit is named, not hidden.
- [ ] Implement **replay**: a deterministic re-run from step 0 must reproduce the *same* observations as the stored evidence (S4) — replay is the audit of task state, exactly as Day 12's transcript was the audit of the loop.
- [ ] Separate the two consumers Day 16 left conflated: **resume** (continue the run — cheapest, most honest use of a checkpoint) vs **replay/audit** (re-derive the run — the checkpoint log is the investigation's provenance, at rest).
- [ ] Connect the termination seam to the store: Day 13's `termination_reason`, Day 16's `over_planned`, Day 15's `rejection_class` all ride on the task record — a terminated run's *why* survives beside its *what*, so "what happened" is a lookup, not a re-run.
- [ ] Keep the ephemeral/durable line Day 16 drew, in code not just prose: the executor's in-process execution state stays **hot** (cheap, fast, per-step); the store is the **cold** shelf it flushes to at each checkpoint. Hot to execute, cold to persist — the line is explicit, so Day 16's distinction isn't silently erased by "persist everything".

### 6. Build: Retention — TTL, Expiry, and First-Class Deletion (45 min)

- [ ] Make **expiration a code decision**: every record carries `ttl` (or inherits the store default) and `expires_at`; `get` and `list_for` consult expiry *first* — an expired record **acts as absent**, and a compaction pass physically removes it. Expiry is Day 17's version of Day 8's gate: a code decision, not a policy mood (S5).
- [ ] Learn the two deletion verbs the curriculum's "deletion" must cover: **expire** (the record ages out automatically) and **delete** (`forget(principal, key)` — an operator or user explicitly retracts it). Both must also remove *derived aggregates* (a recall index, a summarized view), or deletion is cosmetic — Day 15's fail-closed reflex applied to forgetting.
- [ ] Make deletion honest end-to-end: after `forget`, a trace-level check that the record is unreachable through `get / list_for / recall` (S6) — a deleted memory that "sometimes reappears" is the same bug as a fabricated citation, on a different shelf.
- [ ] Keep decay visible: records carry `created_at / expires_at / accessed_at`, so *which* shelf dies of age vs of neglect is measurable — Day 13's "record the guard that fired" reflex, applied to forgetting.
- [ ] Pre-commit the counterweight: retention is not hoarding. A store with a `PENDING`-forever semantic layer and an unbounded lifespan is Day 18's budget problem arriving early — the default TTLs are **deliberately short**, and making them long is a conscious config, not the default.
- [ ] Name the honest limit: what *should* be remembered long-term (a known Saleor concept, a reusable workflow — the curriculum's Day 18 list) is decided by **distillation with relevance scoring**, not by a long TTL on a task record. Day 17 stores the evidence distillation could use and does not pretend a long TTL *is* memory.

### 7. Build: Episodic vs Semantic — the Seam, Not the System (45 min)

- [ ] Learn the two memory kinds the curriculum names and the difference that matters: **episodic memory** is *what happened* — one investigation's full record (task state at rest plus the trace); **semantic memory** is *what is generally true* — a distilled "known Saleor concept" or "a reusable checkout-repair workflow". The first is a receipt, the second is a lesson, and the distance between them (generalization over many episodes) *is* the Day 18 question.
- [ ] Build the seam only: Day 17's store can serve **episodic records verbatim** — recall a finished investigation by its `task_id` when a new question matches its metadata. That is **memories as retrievable records**: Day 8/9's retrieval applied to the agent's own past, with relevance scoring explicitly `PENDING` (Day 18/21).
- [ ] Answer the curriculum's "only if useful" with a **measurement, not a vibe**: the S-fixture set includes the resumption payoff (S3) and the no-change control (S9). The day ships with the **distillation cell marked `PENDING`** — no semantic-memory extraction runs until a real key can be judged. A silently-run distiller is the failure; a named not-built is the success.
- [ ] Name the honest cost of getting memory wrong, Day 13's way: semantic memory that is **stale** (a distillery without expiry) or **wrong** (a lesson distilled from a single failed run) is worse than none — the system answers with confidence from a memory it misattributes. `recalled ⊆ authored` must be followed by `authored ⊆ grounded`: a distilled claim is a *grounded claim* (Day 14's wording) or it is not minted.
- [ ] Draw the Day 18 line precisely, so next day's first move is fixed: the store's *schema, provenance, TTL, and deletion* are Day 17's (built); the *distiller* (episodes → concepts), the *relevance-scored recall*, and the *expiry/re-prompt loop* are Day 18's (named). The curriculum's candidate memories are each tagged with their real owner: **previous investigations** → episodic, cheap, Day 17 recallable; **known Saleor concepts** and **past successful workflows** → semantic, distilled, Day 18; **user preferences** → a *data table*, not a memory at all — the seam even applies to the curriculum's own list.

### 8. Test: The State Set (S1…S9) (1 hour)

- [ ] Pre-register the state fixtures (**S1…S9** — *state* fixtures, one per property, plus a control) with expectations written **before** any run:
  - **S1** working state: two turns of one task — the plan position and hypothesis written at turn 1 are **served at turn 2** (no re-derivation, no re-planning).
  - **S2** conversation state: a multi-turn exchange — the transcript persists per session/principal; a user referring back to "the order we discussed" is answered without re-asking; Day 12/13's self-correction stays attributed to one conversation.
  - **S3** task resume: a run interrupted at step *k* **resumes at k+1 in a new process** from the store (Day 16's P4, moved from the in-run list to the cold shelf).
  - **S4** serialize/replay: state round-trips to a byte-identical payload; replay from step 0 against the serialized form reproduces the same evidence.
  - **S5** retention: a record past `expires_at` **acts absent** through `get/list_for/recall`; compaction physically removes it; a hostile tiny-TTL config proves eviction fires.
  - **S6** deletion: `forget` removes the record *and its derived aggregates*; it is **unreachable everywhere**; deletion is visible in the trace, not an omission.
  - **S7** provenance / `recalled ⊆ authored`: a write with no `created_by` is **rejected at write time**; every record carries `run_id+step+source`; a runtime-imported record is unreachable — memory the system did not author is not recallable.
  - **S8** boundary: a working-state record is served **only to its owner context**; a cross-owner read returns nothing; no run-to-run bleed unless distillation (Day 18) produced it.
  - **S9** **control**: with state **disabled**, Days 12–16 run *unchanged and exactly as green* (A1…A8, the doc nine, F1…F9, Q1…Q8, X1…X11, P1…P9) — over-persistence, state that changes a memory-free answer, is a bug.
- [ ] Split the day honestly, Day 7's way: **S1–S9, all of them, are `MECH`** — deterministic functions of the store, TTL math, keying, config, and stored payloads: offline, free, reproducible. The `PENDING` is the *semantic* month two days away: relevance-scored recall of episodic records under a real model, distillation validity (episodes → concepts), and *"does memory make real answers better"* as Day 21's benchmark on Day 17's store.
- [ ] Re-run Days 7–16's fixtures as the no-regression gate **through the store**: the loop, the planner, and the executor run with state *on* and state *off* — S9 guarantees the off case equals Days 12–16 exactly, so a store that changes a single-step or single-plan answer is an S9 failure arriving through the front door.
- [ ] Apply Day 7's isolation, one owner per symptom: a **resume gone wrong** → the checkpoint write (task state) owns it; a **cross-principal leak** → `list_for` keying owns it; a **deleted record that reappears** → compaction / derived-aggregate removal owns it; an **expired record recalled** → the expiry check in `get` owns it; a **no-provenance record in the store** → the write path owns it (it failed its fail-closed guard, not the reader). One owner per symptom, in the trace.

### 9. Measure + Documentation (45 min + 20 min)

- [ ] Rule-4 as a state table, one row per fixture, every cell a real run's: `kind | state written | stored as | recalled | provenance (run/step/source) | ttl | expired? | deleted? | resume? | cost | wall_ms` — the **`recalled ⊆ authored`** column is the day's headline and must read `ok` on every row, and **S9's row** (state off ≡ Days 12–16) is the counterweight, exactly as `over-planned? = no` was Day 16's.
- [ ] Record the cost shape honestly: state adds **0 chats and 0 embeds** on the answer path — the store is local reads/writes, one JSON append per checkpoint; the *premium* is **latency** (one store round-trip per checkpoint), not money. Memory that costs a chat to recall is Day 18's, and its meter is recorded, not assumed.
- [ ] Keep the negatives column with owners: episodic/semantic memory and its relevance-scored recall are **Day 18**; whether real memory makes answers better is **Day 21**; the true conversation state of a streaming UI (message ordering across mid-stream cuts, Day 4's SSE) is **named, not built**; and *multi-process synchronization* of one task state is **named, not built** — the store is single-writer by construction today, and the seam is recorded.
- [ ] Write `docs/concepts/state-memory.md` — state vs memory (pinned vs retrievable), the three states (working / conversation / task) and where each lives, the serialization/replay contract, TTL / expiry / first-class deletion, provenance (**`recalled ⊆ authored`**) and the closed store, and the episodic-vs-semantic seam with the "only if useful" measurement.
- [ ] Write `docs/evaluations/state-day17.md` — the §8 state table with `recalled ⊆ authored = ok` and the S9 no-change invariant, the S1…S9 pre-registered fixtures and the S9 control, the store configs and defaults, the no-regression lines (Days 7–16 through the store), and the semantic-memory `PENDING`.
- [ ] Fill the Day 17 Daily Learning Record (Problem / Concept / Understanding / … / Tomorrow).
- [ ] Record token economics: state is **0 tokens** — local store I/O at checkpoints; the only billable memory is Day 18's generation-time distillation or recall re-ranking; the day's expense is disk and latency, and both are measured per fixture.

## Deliverable

The agent state/checkpoint system — Day 16's checkpoints given a home, and the seam to real memory drawn honestly:

```text
Day 12 transcript ────conversation state────→  [store shelf: session-scoped,   TTL]
Day 16 executor ──────working state──────→  [store shelf: task-scoped,      survives the turn]
Day 16 checkpoint ─────task state───────→  [store shelf: run-scoped,       survives the process → resume at k+1]
               then:  episodic = those task records at rest, recallable verbatim   (17: store)
                      semantic = distillation of them → "known concepts/workflows" (18: only if useful)

   every record:  {kind, principal, key, payload, ttl, expires_at,
                   created_by: run_id + step + source, created_at, schema}
   write path:    fail-closed on missing provenance  →  recalled ⊆ authored
   read path:     context-keyed  ·  expiry-checked  ·  deleted == absent
   retention:     TTL + compaction + first-class forget(principal, key)
   seam:          hot execute → cold persist at each checkpoint  (Day 16's line, in code)
   OFF switch:    state disabled ≡ Days 12–16  (S9 control: over-persistence is a bug)
```

Days 12–16 answered "what does this look like while the run is alive"; Day 17 answers **"what survives the run, who may read it, and how does it die."** A multi-turn conversation is answered from stored conversation state; a multi-step investigation is resumable across a process; every recalled fact is attributable to the run that wrote it; nothing is recalled that the system did not author; expiry and deletion are code decisions; and semantic memory — the *only* thing that could make a new question better than a fresh investigation — is named, measured, and deferred to Day 18, which is precisely where the curriculum says the "only if useful" question lands.

## Exit Check — answer in your own words

- What is the difference between *state* and *memory*, and why does Day 12's transcript turn out to be *state*, not memory?
- Name the three states the curriculum opens with, and where each lives in the store.
- How does Day 16's checkpoint become a *resumable* run, and which earlier day's read-only property makes that safe?
- What does `recalled ⊆ authored` forbid, how does it relate to `cited ⊆ retrieved` and `conclusion ⊆ evidence`, and why is the store "closed"?
- Why is deletion a first-class operation rather than an omission, and what does the curriculum mean by introducing persistent memory "only if useful"?