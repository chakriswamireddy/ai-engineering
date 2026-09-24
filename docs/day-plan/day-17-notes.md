# Day 17 Notes — Memory and State

> Notes answering `docs/day-plan/day-17.md`.
> Sixteen days built a machine that *reads* (Days 6–10), *acts* (Day 11), *drives itself* (Day 12), *survives failure* (Day 13), *computes* (Day 14), *defends itself* (Day 15), and *plans before acting* (Day 16). Every one of those remembered its work **only while it worked**: Day 12's transcript died with the process, and Day 16's execution state and checkpoints were explicitly **ephemeral — in-run** — with the persistent store recorded as "Day 17's seam, named not built." Day 17 builds that seam. The curriculum asks *"Understand what an agent needs to remember"* and answers it structurally: start with the three states — **working state** (the facts and hypothesis of *this* task), **conversation state** (the session's transcript), **task state** (the plan, its steps, evidence, and ledger as a durable record) — and only then ask whether **persistent memory** is useful, introducing it *only if it earns it*. The deliverable is the **agent state/checkpoint system**: a layered **store** that gives Day 16's checkpoint format a real home, with serialization/replay (a run resumes at step *k+1* in a new process), retention (TTL, expiry, first-class deletion), and provenance that makes every recalled claim traceable to the run that **authored** it — the honesty invariant's fourth translation, **`recalled ⊆ authored`**.
> The single idea: **state is pinned to a context; memory is retrievable across contexts — and Day 17 builds the former, names the latter, and refuses to fake the seam.** Working/conversation/task state are Day 17's store. Episodic memory (one task's record at rest) is Day 17's format, recallable verbatim. Semantic memory (generalized "known concepts / workflows," distilled over episodes) is **Day 18** — and the day carries a control (S9) that proves the whole addition is additive: with state off, the system is Days 12–16 byte-for-byte.

---

## 1. State vs Memory — What an Agent Needs to Remember

The curriculum opens with the question every agent framework eventually fumbles: everything that happened isn't everything you should keep, and nothing you keep keeps itself. The day's first discipline is a definition worth its weight:

| | **State** | **Memory** |
|---|---|---|
| Pinned to | a context (task / session / run) | no single context |
| Loaded because | *this* context owns it | it is *relevant* here |
| Injected to | the run that wrote it (or its session) | any run that matches |
| Dies when | the context ends, or expiry expires it | it is deleted / superseded |
| Day | **17** | **18** (distilled) |

Day 12's transcript was the day's accident disguised as a discovery: agent state = the messages, no hidden variable — but the messages died with the process, which is *state* in the purest sense (pinned, short-lived, context-owned). Nothing in Days 12–16 outlived a run except the corpus stores, which are *knowledge the engineer built*, not knowledge the agent built. Day 17 is the first day the agent's own working knowledge outlives a single request.

The axis that orders the curriculum's vocabulary is **lifetime × generality**:

```text
working state    · short-term, task-pinned      →  Day 17 store
conversation state · session-pinned             →  Day 17 store
task state        · run-pinned, durable record  →  Day 17 store
episodic memory   · what happened, a record     →  Day 17 format, Day 18 retrieval
semantic memory   · what is generally true      →  Day 18, only if useful
```

The failure at both extremes is the same disease wearing two coats. Treat **everything as state** — a transcript that never evicts, a store that only grows — and you have a memory leak with a dashboard: context gets more expensive per turn until the token budget swallows the loop (Day 13's non-termination in cost form). Treat **everything as memory** — generalize every episode into a lesson — and the agent bleeds run-to-run context into answers where it has no right (a working fact from last Tuesday served back as a general truth is the first step toward an agent hallucinating about its own past). S8 exists precisely to make that bleed *impossible by keying*, not by good intentions.

The curriculum's loaded phrase — "introduce persistent memory *only if useful*" — is the day's third discipline: usefulness is **measured or refused**. The fixture set encodes both verdicts: S3 demands memory be *load-bearing* (a resumption that fails without stored task state), and S9 demands it be *invisible* when it isn't (state off ≡ Days 12–16). A memory that changes no answer in the control is not a win, it is overspending. The measure, not the aspiration, decides.

### The invariant the day pre-commits

**`recalled ⊆ authored`** — the fourth translation of the series' honesty rule:

| Day | Invariant | What it constrains |
|---|---|---|
| 6 | `cited ⊆ retrieved` | prose citations |
| 14 | `asserted ⊆ returned` | result rows |
| 16 | `conclusion ⊆ evidence` | the plan's observations |
| **17** | **`recalled ⊆ authored`** | **the store's own recall** |

A record is recallable only if the system itself wrote it — under its own rules, with provenance (`run_id`, step, source). The sharp edge: a fact the system did **not** author (an imported document, an injected string, a stored "fact" with no writer) must be unreachable, because a memory channel the system does not control is Day 25's prompt injection that has simply not happened yet. Day 15 relocated the safety guarantee to a *database role*; Day 17 relocates it to the **write path** — the store is *closed* to writes it did not make.

---

## 2. The Three States — and Where "Short-Term Memory" Lives

The curriculum names three states; the day's work is reading each of them as a *renamed discipline from the earlier days*, not a new subsystem.

**Working state** is Day 16's execution state promoted to first-class. A multi-step investigation *is* a running thing — current step, observations so far, the plan's running hypothesis — and Day 16 kept that in a hot in-run list, then handed the persistence question to Day 17. Nothing about the *shape* changes; what changes is that "the plan is at step 4, hypothesis is payment-declined" is now *true tomorrow*. This is the cheapest genuine memory an agent has, and the one frameworks nearly always get wrong by either forgetting it entirely or promoting it to "long-term memory" — a promotion nothing earned. It lives as long as the **task** does, and it is never shared with another task except by explicit distillation (Day 18).

**Conversation state** is Day 12's transcript, plus the honesty Day 12 only implied. The transcript *was* the memory, and Day 12 was right that no hidden variable existed — but a transcript is not self-describing: which turn was the plan agreed at, which session does it belong to, what did the user themselves state as a preference? Those are **headers on the state, not hidden variables** (Day 12's no-hidden-variable rule survives the upgrade to a store). The agent is not a better model because its conversation state is richer; it is a *safer* agent because session identity is a key, not a guess.

**Task state** is Day 16's plan made durable: `Plan`, steps, statuses, evidence, the budget ledger, the termination reason. The point is that Day 16's question — *"where is the plan, and can it continue?"* — was always a question the *store* should answer. A terminated run's **why** (Day 13's `termination_reason`, Day 15's `rejection_class`, Day 16's `over_planned`) survives beside its **what**, so "what happened" is a lookup, not a re-run.

**Short-term memory** is the umbrella the curriculum hangs over all three, and the word matters: the store they share is a **bounded, expiring, context-swapped-in** store — not free-form long-term storage. Sizing is a policy (`max_records`, `ttl`), eviction is explicit (LRU or TTL), and the working shelf is loaded *for its owner*, never broadcast. A working context that never evicts is Day 13's non-terminating loop — in token-cost form, where the cost is silent.

### The seam that must not be crossed

**Episodic memory** records *what happened*. That is Day 17's task state at rest — a receipt. **Semantic memory** distills *what is generally true* — a lesson: "checkout failures in Saleor usually trace to the payment, inspect order state first." The distance between a receipt and a lesson (generalization over many episodes, with a judgment of which lesson is *right*) is the entire Day 18 question — and the day's ordering rule is: build the states, persist them, then ask whether distillation is useful, with the answer honestly allowed to be *not yet*.

---

## 3. The Store — One Shelf per Lifetime

Day 16 built the *format* (a checkpoint: step *k* done, evidence held, hypothesis running). Day 17 builds the *store* — "where it lives," the schema it obeys, who may read it, and how it dies. The design is five decisions, each a reflex from an earlier day.

**Decision 1 — state is data, and its schema is a contract.** A record is `{kind, principal, key, payload, ttl, expires_at, created_by, created_at, schema}`. The `schema` field is not bureaucracy: a future agent must be able to read a past run, and "can't read my own diary" is a *schema* failure, not a mystery. Version it like the corpus stores dimension-lock embeddings.

**Decision 2 — provenance is mandatory, not metadata.** `created_by` (run_id + step + source) is the raw material of `recalled ⊆ authored`. A write with no creator is **rejected at write time** — fail-closed at the moment of ingress, Day 15's reflex, because provenance added later is provenance you cannot trust.

**Decision 3 — the store is closed.** Only the agent runtime writes; only context-matching readers (`principal` + `kind` + key prefix) recall; nothing is imported at runtime. The thread that makes this a *safety* decision rather than an aesthetics decision is Day 15's, moved one layer up: an open store is how an injection becomes a *memory* — injected once, believed forever. "Privileges beat parsers" becomes "closed write paths beat sanitizers."

**Decision 4 — deletion is a first-class operation.** `forget(principal, key)` removes a record *and its derived aggregates*; expired records *act as absent*; a compaction pass physically removes them. A store that only grows is not memory, it is a leak with a dashboard.

**Decision 5 — the OFF switch is real.** With state disabled, the system is Days 12–16 byte-for-byte (S9). This is the day's counterweight, the direct descendant of Day 15's X11 control (a safety layer that blocks the legitimate query is a bug) and Day 16's P9 control (a planner that plans a one-step question is a bug): a *persistence* layer that changes a memory-free answer is a bug wearing a diary.

The five decisions collapse into the day's three invariants:

1. **`recalled ⊆ authored`** — nothing recallable that the system did not write, with provenance.
2. **State is swapped in by context only** — working/conversation/task records never bleed across owners except by explicit distillation (Day 18).
3. **State off ≡ Days 12–16** — the addition is additive by construction and by fixture.

---

## 4. Working + Conversation State — Turns and Sessions

### The store

`StateStore` with `put / get / list_for(principal, kind, key_prefix) / delete / expire`, file-backed JSON like the corpus stores — inspectable, replayable, verifiable offline. No hidden store: the series' "state is data" rule holds for the *new* state as it did for the transcript; a memory system that cannot be zeroed or inspected by a human is not a memory system, it is a black box with a retention policy.

### Working state wired

Day 16's executor now reads and writes the current plan position and the running hypothesis *through the store*. The concrete consequence is S1: a question answered across two turns doesn't re-derive "the plan is at step 4." This is the exact cell Day 16 explicitly deleted — its notes say "execution state is not memory," and the seam handed it to Day 17. What changed is not the structure but the *home*: hot to execute, cold to persist, at each checkpoint. The line is drawn in code — the executor's in-process state stays hot and cheap, the store is the cold shelf it flushes to — so Day 16's ephemeral/durable distinction is not silently erased by the well-meaning "persist everything."

### Conversation state wired

Day 12's transcript persists per session/principal. A user who says "the order we discussed this morning" is answered from the stored exchange, not by re-asking or by luck. And Day 12/13's self-correction turns — the loop's "that was wrong, re-issue" — stay attributed to the same conversation, which matters far more than it sounds: a self-correction that looks like a *new* user request on a *new* session is how an agent loses the thread and re-commits the error it had already fixed.

Two rules keep this honest:

- **Serialization is the contract.** A record round-trips (`put` → dump → load → `get`) to a byte-identical payload (S4). Resume and replay both consume the *serialized* form, never the live object — because the live object dies with the process; the only survivor is the file.
- **The store augments the transcript; it does not replace it.** A run must be reconstructible from transcript + store, and a run whose store disagrees with its transcript is a *trace* failure, not a preference. Day 12's no-hidden-variable rule survives: adding a store is not permission to stop logging.

---

## 5. Task State and Resume — Day 16's Checkpoint, Made Durable

Day 16 named the two things people conflate — *"when may the loop stop?"* vs *"where is the plan, and can it continue?"* — and split them cleanly: termination was Day 12/13's, the checkpoint was Day 16's. Day 17 closes the loop by making "where is the plan" a *lookup*, not a survivor of luck.

After every step the executor writes `put("/task/{task_id}", {plan, step_statuses, evidence, hypothesis, budget_ledger, termination_reason})`. A **resume** starts with an optional `task_id`, loads the record, marks step *k+1* `pending → running`, and walks the remaining steps without re-running 1…*k* (S3). This is the concrete payoff Day 16 *named* — "resuming requires the tools to be re-runnable; a read-only executor gets resumability for free" — and Day 17 now collects it, with the honesty attached:

- The tools are **read-only and re-runnable** (`get_order`, `get_customer`, the search tools, `describe_schema`, the hardened `run_saleor_sql`): no side effect can be double-applied, so resume is *safe*, not just possible.
- A **mutating executor could never offer resume for free** — re-running tool 4's INSERT after a crash would need transactional or exactly-once semantics the series never built, and that limit is **named, not hidden**. The read-only property Day 14/15 chose for *safety* turns out to be the property that makes Day 17's resume *correct* — two days' goals, one property.

**Replay** is resume's sibling and the audit path: re-run from step 0 against the serialized form and require *identical observations* (S4). Resume continues the run; replay *re-derives* it. Both consume the same checkpoint log — which is why the checkpoint log was worth writing in the first place: it is the investigation's provenance, at rest.

Termination stays wired end-to-end: `termination_reason`, `over_planned`, `rejection_class` all ride on the task record, so a terminated run's *why* survives beside its *what*. The store makes the audit trail a lookup.

---

## 6. Retention — TTL, Expiry, and Deletion as a First-Class Operation

The curriculum lists "expiration" and "deletion" as learn items, and the day's reading is that both are **code decisions, not policy moods** — the same lesson Day 8 taught about the refusal gate and Day 13 about the guards.

**Expiry**: every record carries `ttl` (or inherits the store default) and `expires_at`; `get` and `list_for` consult expiry *first*. An expired record **acts as absent** — it does not exist to the reader, which is the failure mode that matters (a stale memory that can still be retrieved is not expired, it is lying) — and compaction physically removes it. Eviction is proven by hostile config, not assumed: a fixture with a tiny TTL must show the shelf emptying (S5).

**Deletion**: two verbs, because "the user wants this gone" and "this aged out" are different rights. `expire` is automatic; `forget(principal, key)` is explicit — an operator or a user retracts a record. Both must remove **derived aggregates** (a recall index, a summarized view), or deletion is cosmetic: Day 15's fail-closed reflex applied to forgetting. And deletion is checked, not hoped: after `forget`, a trace-level verification that the record is unreachable through `get / list_for / recall` (S6) — a deleted memory that "sometimes reappears" is the same bug as a fabricated citation, on a different shelf.

**Decay is visible.** Records carry `created_at / expires_at / accessed_at`, so which shelf dies of *age* vs *neglect* is measurable — Day 13's "record the guard that fired" reflex applied to forgetting. And the (important) counterweight: **retention is not hoarding.** Default TTLs are deliberately short; a long TTL is a conscious config, not the default; and the day's honest limit is stated plainly — the things that *should* live long-term (known Saleor concepts, reusable workflows, the curriculum's Day 18 list) are decided by **distillation with relevance scoring**, not by a long TTL on a task record. A long TTL is not memory; it is procrastination with a timestamp.

---

## 7. Episodic vs Semantic — the Seam, and the "Only If Useful" Measurement

The curriculum names episodic and semantic memory, and the day's temptation is to "start" both. The discipline is to build the seam and measure the case.

- **Episodic memory** is *what happened*: one investigation's full record — task state at rest, plus the trace. Day 17's store can serve these **verbatim**: recall a finished investigation by `task_id` when a new question matches its metadata. That is *memories as retrievable records* — Day 8/9's retrieval applied to the agent's own past, with relevance scoring explicitly `PENDING` (Day 18/21).
- **Semantic memory** is *what is generally true*: a distilled "known Saleor concept" or "a reusable checkout-repair workflow." The distance between an episode and a lesson is **generalization**, and generalization is exactly where a small system must not fake it. The day's rule, Day 13's way: semantic memory that is **stale** (a distillery without expiry) or **wrong** (a lesson distilled from a single failed run) is *worse* than none — the system answers with confidence from a memory it misattributes. `recalled ⊆ authored` must be followed by **`authored ⊆ grounded`**: a distilled claim is a grounded claim (Day 14's wording) or it is not minted.

The curriculum's "only if useful" is answered with a **measurement, not a vibe**. The fixture set contains both verdicts — S3 (resumption is load-bearing) and S9 (off ≡ on for memory-free questions) — and the day ships with the **distillation cell `PENDING`**: no semantic-memory extraction runs until a real key can judge whether anything distilled is worth recalling. A silently-run distiller is the failure; a named not-built is the success — the series' standing rule about `PENDING` cells.

The Day 18 line is drawn precisely so next day's first move is fixed. Schema, provenance, TTL, deletion → **Day 17, built**. Distiller (episodes → concepts), relevance-scored recall, expiry/re-prompt loop → **Day 18, named**. And the curriculum's own candidate memories are each tagged with their real owner:

| Curriculum candidate | Kind | Real owner |
|---|---|---|
| Previous investigations | episodic | Day 17 — recallable verbatim by `task_id` |
| Known Saleor concepts | semantic | Day 18 — distilled |
| Past successful workflows | semantic | Day 18 — distilled |
| User preferences | a *data table*, not a memory | Day 17's store, as data — never generalized into "the user always…" |

Even the curriculum's list respects the seam: "user preferences" is *data about the user*, not a lesson to be generalized; treating a preference as a semantic memory is how agents start condescending to users from stale memory.

---

## 8. Test: The State Set (S1…S9), and the Store as a Contract

Expected behavior written **before** any run, one fixture per property plus a control:

| ID | What it proves | Input / injection | Expected |
|---|---|---|---|
| S1 | working state | two turns of one task | plan position + hypothesis from turn 1 **served at turn 2**; no re-derivation |
| S2 | conversation state | a multi-turn exchange | transcript persists per session/principal; back-references answered; self-correction stays attributed |
| S3 | task resume | interrupt after step *k* | **resumes at k+1 in a new process** from the store (Day 16's P4, cold) |
| S4 | serialize/replay | any stored run | byte-identical round-trip; replay from step 0 reproduces the same evidence |
| S5 | retention | a record past `expires_at` | **acts absent** in get/list/recall; compaction removes it; hostile tiny-TTL proves eviction |
| S6 | deletion | `forget(principal, key)` | record + derived aggregates removed; **unreachable everywhere**; visible in the trace |
| S7 | provenance | a write with no `created_by`; a runtime-imported record | write **rejected at write time**; every record carries run/step/source; the import is unreachable |
| S8 | boundary | a cross-owner read | **nothing returned**; no run-to-run bleed except explicit Day 18 distillation |
| S9 | **control** | state **disabled** | Days 12–16 fixtures run **unchanged, exactly as green**; over-persistence is a bug |

The `MECH`/`PENDING` split is exactly Day 7's, and this time it is nearly all one side: **S1–S9 are all `MECH`** — deterministic functions of the store, the TTL math, the keying, the config, and stored payloads: offline, free, reproducible. The `PENDING` is the semantic month two days away — relevance-scored recall of episodic records under a real model, distillation validity, and *"does memory make real answers better"* as Day 21's benchmark on Day 17's store. The stub proves the **store**; a real model proves the **memory**.

Three obligations, unchanged in spirit:

1. **No regression on Days 7–16, *through the store*.** The loop, the planner, and the executor run with state on and state off. S9 guarantees the *off* case equals Days 12–16 exactly — so a store that changes a single-step or single-plan answer is an S9 failure arriving through the front door.
2. **The day's own counterweight.** Like Day 15's `false-reject?` and Day 16's `over-planned?`, the **S9 no-change row** is the measurement that keeps "we added state" from meaning "we changed everything." It must read: state off ≡ Days 12–16.
3. **Isolation, one owner per symptom.** A resume gone wrong → **the checkpoint write** (task state). A cross-principal leak → **`list_for` keying**. A deleted record that reappears → **compaction / derived-aggregate removal**. An expired record recalled → **the expiry check in `get`**. A no-provenance record in the store → **the write path** (it failed its fail-closed guard, not the reader). Say which, in the trace.

### Measure: the store as a contract

Rule-4 as a state table, one row per fixture, every cell a real run's:

| Fixture | Kind | State written | Stored as | Recalled | Provenance | TTL | expired? | deleted? | resume? | cost | wall_ms |
|---|---|---|---|---|---|---|---|---|---|---|---|
| S1 working state | task | plan pos + hypothesis | record | y | run/step/source | … | no | no | — | 0-chat | … |
| S2 conversation | session | transcript delta | record | y | run/step | … | no | no | — | 0-chat | … |
| S3 resume | task | checkpoint k | record | y | run/step | … | no | no | y | 0-chat | … |
| S4 serialize/replay | task | full run | file | y | run/step | … | no | no | y | 0-chat | … |
| S5 retention | task | expired record | (absent) | n | run/step | past | **y** | no | — | 0-chat | … |
| S6 deletion | session | forget() | (gone) | n | run/step | … | no | **y** | — | 0-chat | … |
| S7 provenance | task | no created_by | **rejected** | n | — | … | no | no | — | 0-chat | … |
| S8 boundary | task | cross-owner read | (none) | n | — | … | no | no | — | 0-chat | … |
| S9 control | — | state off | — | — | — | — | — | — | — | 0-chat | … |

Two columns carry the day: **`recalled ⊆ authored`** must read `ok` on every row that recalls (S1–S3 must prove provenance reached the reader; S7's row proves the reader *can't* be lied to), and **the S9 row** — state off ≡ Days 12–16 — is the counterweight, exactly as `over-planned? = no` was Day 16's. The cost column is the day's honest headline: **0 chats, 0 embeds on every row** — state is store I/O, one JSON append per checkpoint; the premium is latency, not money. Memory that costs a chat to recall is Day 18's, and its meter is recorded, not assumed.

The negatives column, with owners: episodic/semantic memory and its relevance-scored recall are **Day 18**; whether real memory makes answers better is **Day 21**; true streaming conversation state (message ordering across Day 4's SSE mid-stream cuts) is **named, not built**; and multi-process *synchronization* of one task state is **named, not built** — the store is single-writer by construction today, and the seam is recorded. Empty negatives mean the day learned nothing; these have owners.

---

## 9. Documents

- `docs/concepts/state-memory.md` — state vs memory (pinned vs retrievable), the three states (working / conversation / task) and where each lives, the serialization/replay contract, TTL / expiry / first-class **deletion**, provenance (**`recalled ⊆ authored`**) and the **closed store**, and the episodic-vs-semantic seam with the "only if useful" measurement.
- `docs/evaluations/state-day17.md` — the §8 state table with `recalled ⊆ authored = ok` and the **S9 no-change invariant**, the S1…S9 pre-registered fixtures and the S9 control, the store configs and defaults (`STATE_STORE_PATH`, `STATE_DEFAULT_TTL`, `STATE_MAX_RECORDS`, `STATE_PRINCIPAL`), the no-regression lines (Days 7–16 through the store, state on *and* off), and the semantic-memory `PENDING`.
- PROGRESS rollup: Day 17 mechanics move to `DONE` under the stub (working/conversation/task stores, checkpoint persistence, resume-replay, TTL/expiry/deletion, provenance enforcement, and the S9 off-switch verified offline); the episodic-relevance and semantic-distillation cells stay `PARTIAL`/`PENDING` until a real gateway run confirms a model can mint a *grounded* memory — Day 18's first move.

---

## 10. Daily Learning Record

### Problem

Sixteen days produced an agent that is **competent and amnesiac**: Day 12's transcript died with the process, and Day 16's execution state and checkpoints were deliberately ephemeral — "the persistent store is Day 17's seam, named not built." The running investigation of the curriculum ("why could customer X not complete checkout," Day 16's eight-step plan) survives *within* a run but not *across* one: a crash, a pause, or even the next question in the same conversation means re-deriving where the plan was, what it observed, and what it had concluded. The problem is to give the agent the missing faculty — **remembering beyond the run** — without breaking the series' two inviolates: the addition must be **additive** (with state off, the system is Days 12–16 byte-for-byte), and it must respect the curriculum's own discipline that *persistent memory is introduced only if it is useful* — which means the day must *measure* usefulness, not assume it. And it must do all of this without crossing into Day 18's territory: episodic and semantic memory are real, named, and *not built today* — the day builds the store they will one day be served from.

### Concept

**State is pinned to a context; memory is retrievable across contexts — and the agent state/checkpoint system is the store that turns the former into a fact and names the latter as a seam.** Day 17 owns three states: **working** (Day 16's execution state — the plan position, observations, running hypothesis — now true for the next turn), **conversation** (Day 12's transcript, persisted per session/principal, with session identity as a header on the state, not a hidden variable), and **task** (Day 16's Plan made durable — steps, statuses, evidence, budget ledger, termination reason). Each lives on a **shelf** in one store, keyed by `principal` + `kind`, sized and expiring by policy (`max_records`, `ttl`) — this shared store is what the curriculum calls **short-term memory**: bounded, expiring, context-swapped-in, never broadcast. Every record carries **provenance** (`run_id`, step, source) **mandatorily** — a write with no creator is rejected at write time — which is the raw material of the day's invariant, the fourth translation of the series' honesty rule: **`recalled ⊆ authored`**. Any record the system did not author under its own rules is unreachable, because an *open* memory channel is Day 25's injection that has not happened yet (Day 15's "privileges beat parsers," inverted onto the write path). **Retention is code, not mood**: expiry acts as absence, deletion (`forget`) is first-class and removes derived aggregates, compaction physically reclaims. **Episodic** memory (one task's record at rest) is Day 17's format, recallable verbatim; **semantic** memory (generalized lessons) is **Day 18's**, with the curriculum's "only if useful" answered by a measurement (S3 load-bearing, S9 invisible-when-off) and a `PENDING` cell, never a silently-run distiller. The day's counterweight, stated as a fixture: **over-persistence is a bug** — a store that changes a memory-free answer is the Day 16 P9 failure wearing a diary.

### Understanding

The unlock is that **Day 17 adds a *home*, not a new faculty: every shape the store holds already exists, and the new code is where those shapes sleep.** Day 16's checkpoint becomes a store write; Day 12's transcript becomes conversation state on a shelf; Day 16's plan becomes a task record. The originality is in the boundaries, not the bricks: **(1) state vs memory is a *pinning* distinction, not a size distinction** — the same fact is state when pinned to its task and (after distillation) memory when retrievable anywhere, and blurring that line is how an agent starts to hallucinate about its own past. **(2) Resume is safe *because* the executor is read-only** — Day 14/15 chose read-only for safety, and it turns out to be the property that makes re-running tool calls harmlessly idempotent; a mutating agent could never offer Day 17's resume for free, and that limit is named. **(3) The store is closed for the same reason the database role was the guarantee** — Day 15 put the invariant where it cannot be argued with; Day 17 puts *memory provenance* in the write path, so a fact the system did not author *cannot* be recalled, no matter how plausible. And the honest one: all of Day 17 is `MECH` — green offline, deterministic, free — while the two cells that actually answer "does memory make this better" (relevance-scored recall of episodic records, distillation into semantic facts) are `PENDING` until a real model can be judged — Day 18's first move, Day 21's verdict.

### Implementation

- `ai_os/state.py` (new) — `StateStore`: `put / get / list_for(principal, kind, key_prefix) / delete / expire`, file-backed JSON; record schema `{kind, principal, key, payload, ttl, expires_at, created_by (run_id+step+source), created_at, schema}`; expiry consulted on every read; compaction pass; the **closed write path** (runtime `StateStore` refuses a write without provenance).
- `ai_os/agent.py` (extended, minimally) — the Day 12 loop and Day 16 executor gain an optional `StateStore`: conversation state persists the transcript per session; working state flushes execution state at each checkpoint; with no store, the code path is Days 12–16 unchanged (S9).
- `ai_os/executor.py` / `ai_os/planner.py` (extended, minimally) — the Day 16 checkpoint becomes `put("/task/{task_id}", {...})`; a `task_id` on start loads the task record and **resumes at step k+1** (read-only tools make re-run safe); replay re-derives from step 0 and must match stored evidence.
- `ai_os/trace.py` (extended) — state columns: `state_kind`, `principal`, `recalled`, `provenance (run/step/source)`, `expired`, `deleted`, `resume_task_id`, `recalled_subset_authored`, `state_on_off`, beside Day 15/16's `rejection_class` / `over_planned`.
- `tools/state_smoke.py` (new) — offline suite for S1–S9 (including the S9 control: Days 7–16 fixtures run through the executor with state **on and off**), plus hostile configs (tiny TTL, empty store) that prove eviction and absence.
- `docs/concepts/state-memory.md`, `docs/evaluations/state-day17.md`.

### Failure

1. **The store opened to foreign writes.** A record is imported at runtime — a chunk, a "fact," a prompt fragment — and the system recalls it with the authority of its own memory. It is Day 25's injection, pre-installed; the **closed write path** and `recalled ⊆ authored` are the guard, and S7 is the signature.
2. **State promoted into memory.** A working fact survives its task and is served to a *different* task as general truth — the agent "remembers" last week's checkout as if it were doctrine. S8 (context-keyed reads) is the guard; the failure looks like confidence.
3. **Resume replayed the side effects.** A mutating tool re-runs on resume and the loop double-applies writes. The day avoids it by *choosing* a read-only executor — but a future agent with a mutating tool inherits the failure unless resumability is re-earned; the limit is named, not hidden.
4. **The checkpoint was never flushed.** Execution state stays hot, a crash loses it, and "resumable" turns out to mean "in memory." The checkpoint *write* is the unit of durability; a resume that silently re-derives from step 0 instead of step k+1 is the same bug wearing an audit log.
5. **Deletion as omission.** `forget` drops the record but leaves a derived aggregate (an index row, a summary), and the deleted memory "sometimes reappears." Deletion of aggregates is part of `forget`, verified in the trace (S6); the failure is the same one as a fabricated citation, on a different shelf.
6. **Expiry as decoration.** `expires_at` is stored but `get` ignores it — a stale record is *more* dangerous than an expired one because it reads as a fact. Expiry consulted on every read, before anything else; S5 is the guard.
7. **The silent distiller.** The day quietly runs a "summarize" pass on task records and stores the results as general knowledge — semantic memory by sneak, with no measurement and no expiry. The failure is Day 18's, committed early; a **named `PENDING`** is the honest alternative, and the curriculum's "only if useful" is the meter.

### Debugging

Everything Day 17 adds is `MECH` under the stub: the store is CRUD with TTL math, resume is a record load + a status flip, replay is a deterministic re-run, retention is expiry-check-then-return, and `recalled ⊆ authored` is set membership over records that carry `created_by`. The debug loop is Day 7's, re-rooted onto the state owners:

- **A resume gone wrong** → **the checkpoint write** owns it (task state) — read the stored task record, not the model's prose.
- **A cross-principal leak** → **`list_for` keying** owns it.
- **A deleted record that reappears** → **compaction / derived-aggregate removal** owns it.
- **An expired record recalled** → **the expiry check in `get`** owns it.
- **A no-provenance record in the store** → **the write path** owns it — it failed its fail-closed guard, not the reader.
- **A store that changed a memory-free answer** → **the S9 control** owns it — over-persistence is the bug, and the offense is the change, not the memory.

The trace says which, never the vibes; the record is the unit of attribution, and each has an owner from the day it was designed.

### Evaluation

The §8 state table in `docs/evaluations/state-day17.md`: per-fixture `recalled`, `provenance`, `expired?`, `deleted?`, `resume?`, and the two columns that carry the day — **`recalled ⊆ authored = ok`** on every row that recalls (S7's row proves the reader cannot be lied to) and **the S9 row** (state off ≡ Days 12–16, exactly as green). The numbers a future observer can carry: whether any answer was ever served from a memory no run authored (never), whether a cross-owner read ever leaked (never), whether an expired or deleted record was ever recalled (never), and whether adding state moved any of Days 7–16's numbers (it must not). The `PENDING` line, plainly: whether memory makes *real* answers better — a model's relevance-scored recall of episodic records, and its distillation of grounded semantic facts — is Day 18's system and Day 21's judgment on Day 17's store.

### Architecture

The claim of the day is the series' claim, one faculty further: **the system took a new capability — surviving its own work — and stayed small.** The store adds zero chats and zero embeds on the answer path; every ingress is a record the run *already* produced (the transcript, the execution state, the checkpoint), and every egress is a context-keyed, expiry-checked, provenance-bearing read of that same record. Nothing in the loop, the planner, the executor's logic, or the corpora changed — the addition is a *home* for state the days already carry, and the OFF switch (S9) proves the home is optional. The seams are named and owned: episodic recall and semantic distillation are **Day 18**; whether real memory improves answers is **Day 21**; streaming conversation state and multi-process synchronization are **named, not built**; and the day's counterweight — over-persistence is a bug — is a fixture, not a footnote. **Day 17 gave the states a home, the checkpoints a shelf, and the past a door; the read-only executor Day 15 hardened and the diagnosis Day 16 kept are what made "remembering" a store instead of a subsystem — and Day 18 inherits a store that already knows who wrote every fact it holds.**

### Questions

- Is *provenance + closed writes* enough to trust recalled memory, or does real recall need an **authority model** — exceptions for engineer-authored stores, and can trust ever be delegated without reopening Day 25's injection channel?
- Resume is safe only because the tools are read-only; when the agent gains a mutating tool (Day 28-ish), does resumability require *exactly-once* semantics, *transactional* task state, or the harder honesty of *"a mutating agent is never safely resumable, only repairable"*?
- TTL is a blunt instrument: a fact that stays true deserves to outlive `expires_at`, and a fact that was always wrong should never have been stored. Is **semantic expiry** (relevance + confidence instead of a clock) the real Day 18 work, and does it make the clock a backstop rather than a policy?
- The store is single-writer by construction. When two concurrent runs share a task (a user and an agent, Day 27's human-in-the-loop), which state becomes a *merge problem*, and is last-writer-wins ever acceptable for task state, or only for conversation state?
- Deletion removes derived aggregates, but aggregation over an *append-only* episodic log (Day 18's distiller) has already *seen* the deleted record. Can distilled knowledge be deleted, or is "you cannot un-distill" the day's most honest limit?

### Tomorrow

Day 18 — Persistent Memory: Day 17 built the store; Day 18 decides what it is *for*, and does so under the curriculum's own condition — persistent memory only if it earns its keep. The candidates are already tagged: **previous investigations** are episodic records Day 17 can serve verbatim, and the day's first job is making that recall *relevant* (Day 8/9's retrieval applied to the agent's own past: return the finished investigation that matches, scored, not the most recent one); **known Saleor concepts** and **past successful workflows** are *semantic* — distillation over episodes, gated by Day 17's `authored ⊆ grounded` rule and expired by relevance, not just by a clock; and **user preferences** turn out to be a **data table**, not memory. Day 17's guards run as-is — the closed store, provenance, TTL, front-and-center deletion — and Day 18 adds the *distiller* and the *relevance scorer* the store's shelf was built to serve. Day 16 was where the agent learned to *think before it acts*; Day 17 was where it learned to *remember what it did*; Day 18 is where it learns to *carry a lesson from one task to the next* — and only if the lesson is worth carrying.