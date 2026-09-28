# Agent State and Memory — What Survives the Run, Who May Read It, and How It Dies

> Day 17 of the AI-layer apprenticeship. Sixteen days built a machine that *reads*
> (6–10), *acts* (11), *drives itself* (12), *survives failure* (13), *computes*
> (14), *defends itself* (15), and *plans before acting* (16) — and every one of
> those remembered its work **only while it worked**. Day 12's transcript died with
> the process; Day 16's execution state and checkpoints were **ephemeral — in-run**
> by design, with the persistent store recorded as *"Day 17's seam, named not
> built."* Day 17 builds that seam. The curriculum asks *"Understand what an agent
> needs to remember"* and answers it structurally: start with the **three states** —
> *working* state (this task's facts and hypothesis), *conversation* state (the
> session's transcript), *task* state (the plan, its steps, evidence, and ledger) —
> and only **then** ask whether **persistent memory** is useful, introducing it
> *only if it earns it*. The deliverable is the **agent state/checkpoint system**: a
> layered **store** that gives Day 16's checkpoint a home, with serialization/replay,
> retention (TTL · expiry · first-class **deletion**), and provenance that makes
> every recalled claim traceable to the run that **authored** it — the honesty
> invariant's fourth translation, **`recalled ⊆ authored`**.
> Implementation: `ai_os/state.py` (the store), `ai_os/agent.py` (conversation
> state), `ai_os/executor.py` (task state + resume), `ai_os/trace.py` (state
> columns), `tools/state_smoke.py` (S1–S9).

The single idea: **state is pinned to a context; memory is retrievable across
contexts — and Day 17 builds the former, names the latter, and refuses to fake the
seam.** Working / conversation / task state are Day 17's store. Episodic memory (one
task's record at rest) is Day 17's *format*, recallable verbatim. Semantic memory
(generalized "known concepts / workflows," distilled over episodes) is **Day 18** —
and the day carries a control (**S9**) that proves the whole addition is additive:
with state off, the system is Days 12–16 byte-for-byte.

## 1. State vs Memory — the distinction that orders the day

Everything that happened is not everything you should keep, and nothing you keep
keeps itself. The day's first discipline is the definition:

| | **State** | **Memory** |
|---|---|---|
| Pinned to | a context (task / session / run) | no single context |
| Loaded because | *this* context owns it | it is *relevant* here |
| Injected to | the run that wrote it (or its session) | any run that matches |
| Dies when | the context ends, or expiry expires it | it is deleted / superseded |
| Owner | **Day 17** | **Day 18** (distilled) |

Day 12's "agent state = the transcript, no hidden variable" was *state* in the
purest sense — pinned, short-lived, context-owned. Nothing in Days 12–16 outlived a
run except the **corpus** stores, which are knowledge the *engineer* built, not
knowledge the *agent* built. Day 17 is the first day the agent's own working
knowledge outlives a single request.

The axis that orders the vocabulary is **lifetime × generality**:

```text
working state       · short-term, task-pinned      →  Day 17 store
conversation state  · session-pinned               →  Day 17 store
task state          · run-pinned, durable record   →  Day 17 store
episodic memory     · what happened, a record      →  Day 17 format, Day 18 retrieval
semantic memory     · what is generally true       →  Day 18, only if useful
```

The failure at both extremes is one disease in two coats. Treat **everything as
state** — a transcript that never evicts, a store that only grows — and you have a
memory leak with a dashboard, until the token budget swallows the loop (Day 13's
non-termination, in cost form). Treat **everything as memory** — generalize every
episode into a lesson — and the agent bleeds run-to-run context into answers where
it has no right; a working fact from last Tuesday served back as a general truth is
the first step toward an agent hallucinating about its own past. **S8** makes that
bleed *impossible by keying*, not by good intentions.

The curriculum's loaded phrase — *"introduce persistent memory only if useful"* — is
the third discipline: usefulness is **measured or refused**. The fixtures encode
both verdicts: **S3** demands memory be *load-bearing* (resumption fails without
stored task state) and **S9** demands it be *invisible* when it isn't (state off ≡
Days 12–16). A memory that changes no answer in the control is overspending, not a
win.

### The invariant the day pre-commits

**`recalled ⊆ authored`** — the fourth translation of the series' honesty rule:

| Day | Invariant | What it constrains |
|---|---|---|
| 6 | `cited ⊆ retrieved` | prose citations |
| 14 | `asserted ⊆ returned` | result rows |
| 16 | `conclusion ⊆ evidence` | the plan's observations |
| **17** | **`recalled ⊆ authored`** | **the store's own recall** |

A record is recallable only if the system itself wrote it — under its own rules,
with provenance (`run_id`, step, source). The sharp edge: a fact the system did
**not** author (an imported document, an injected string, a stored "fact" with no
writer) must be *unreachable*, because a memory channel the system does not control
is Day 25's prompt injection that simply has not happened yet. Day 15 relocated the
safety guarantee to a *database role*; Day 17 relocates it to the **write path** —
the store is **closed** to writes it did not make.

## 2. The three states — each a renamed discipline from an earlier day

The curriculum names three states; the work is reading each as a *renamed earlier
day*, not a new subsystem.

- **Working state** is Day 16's execution state promoted to first-class: the current
  plan step, the observations so far, the running hypothesis. The *shape* does not
  change; what changes is that "the plan is at step 2, hypothesis is payment-declined"
  is now **true tomorrow**. Hot to execute, cold to persist — the line Day 16 drew is
  kept in code, so "persist everything" cannot silently erase it.
- **Conversation state** is Day 12's transcript, plus the honesty Day 12 only
  implied. A transcript is not self-describing: which session it belongs to, which
  turn a plan was agreed at, what the user stated as a preference. Those are
  **headers on the state, not hidden variables** — Day 12's no-hidden-variable rule
  survives the upgrade to a store.
- **Task state** is Day 16's plan made durable: `Plan`, steps, statuses, evidence,
  the budget ledger, the termination reason. Day 16's question — *"where is the plan,
  and can it continue?"* — was always the *store's* to answer. A terminated run's
  **why** (Day 13's `termination_reason`, Day 15's `rejection_class`, Day 16's
  `over_planned`) now survives beside its **what**, so "what happened" is a lookup,
  not a re-run.

**Short-term memory** is the umbrella over all three, and the word matters: the store
they share is **bounded, expiring, context-swapped-in** — not free-form long-term
storage. Sizing is policy (`max_records`, `ttl`); eviction is explicit (oldest-first
on the cap, TTL on expiry); the working shelf is loaded *for its owner*, never
broadcast.

## 3. The store — one shelf per lifetime, five decisions

`ai_os/state.py` grows the crossing primitives beside Day 16's ephemeral
`ExecutionState`, which is untouched:

```text
StateRecord  {kind, principal, key, payload, ttl, expires_at,
              created_by: {run_id, step, source}, created_at, accessed_at, schema}
StateStore   put · get · list_for(principal, kind, key_prefix) · recall · delete
             forget(principal, key) · expire · compact · dump/load · round_trip
helpers      task_key / conversation_key / working_key
             persist_task_state / load_task_state / working_state
             persist_conversation / load_conversation
```

File-backed JSON like the corpus stores — inspectable, replayable, verifiable
offline. No hidden store: the series' "state is data" rule holds for the *new* state
as it did for the transcript. Five decisions, each a reflex from an earlier day:

1. **State is data, and its schema is a contract.** `schema: 2` is versioned so a
   future agent can read a past run — "can't read my own diary" is a *schema* failure,
   not a mystery (the corpus stores' dimension-lock, applied to records).
2. **Provenance is mandatory, not metadata.** `created_by` (run_id + step + source) is
   the raw material of `recalled ⊆ authored`. A write with no creator is **rejected at
   write time** (`ProvenanceError`) — fail-closed at ingress, because provenance added
   later is provenance you cannot trust (Day 15's reflex).
3. **The store is closed.** Only the runtime's `AUTHORED_SOURCES`
   (`agent · executor · session · trace · run · checkpoint`) may write; a record
   planted in the file with no provenance, or a foreign `source` like `import`, is
   **not admitted on load**, so an injected fact can never become a recalled one. An
   open store is how an injection becomes a *memory* — injected once, believed
   forever. "Privileges beat parsers" becomes "closed write paths beat sanitizers."
4. **Deletion is first-class.** `forget(principal, key)` removes a record **and its
   derived index**; expired records *act as absent*; `compact` physically reclaims. A
   store that only grows is a leak with a dashboard.
5. **The OFF switch is real.** `enabled=False` makes every read return nothing and
   every write a no-op; with state off the system is Days 12–16 byte-for-byte (S9).

The five decisions collapse into the day's three invariants: **`recalled ⊆
authored`** (nothing recallable the system did not write) · **context-keyed reads**
(no bleed across owners) · **state off ≡ Days 12–16** (the addition is additive by
construction and by fixture).

## 4. Working + conversation state wired — turns and sessions

Day 16's executor now reads and writes the plan position and the running hypothesis
*through the store*: after every step it flushes a **task record** at
`/task/{task_id}` (hot to execute, cold to persist). The concrete consequence is
**S1**: a question answered across two turns does not re-derive "the plan is at step
2" — `working_state(payload)` serves the position and the hypothesis the next turn.
This is the exact cell Day 16 deleted and handed to Day 17; the only change is the
*home*, not the shape.

Day 12's transcript persists per **session/principal** as a conversation record at
`/conversation/{session_id}`; `run_agent(state=…, session_id=…)` loads prior turns
as *headers* on the transcript before the run and appends the new exchange after it.
A user who says "the order we discussed this morning" is answered from the stored
exchange, and a self-correction stays attributed to the same conversation — a
self-correction that looked like a *new* request on a *new* session is how an agent
loses the thread and re-commits the error it already fixed. Two rules keep it
honest:

- **Serialization is the contract.** A record round-trips (`put → dump → load →
  get`) to a byte-identical payload (**S4**). Resume and replay consume the
  *serialized* form, never the live object — the live object dies with the process;
  the only survivor is the file.
- **The store augments the transcript; it does not replace it.** A run must stay
  reconstructible from transcript + store, and a run whose store disagrees with its
  transcript is a *trace* failure, not a preference.

## 5. Task state and resume — Day 16's checkpoint, made durable

Day 16 split *"when may the loop stop?"* (termination, Days 12/13) from *"where is
the plan, and can it continue?"* (the checkpoint). Day 17 makes the second a
**lookup**, not a survivor of luck. After every step the executor writes
`put("/task/{task_id}", {plan_ids, steps, evidence, hypothesis, checkpoints,
resume_from, …})`. A **resume** loads that record, marks step *k+1* running, and
walks the remaining steps **without re-running 1…k** (**S3**). It is safe for one
reason, and the reason is two days old:

- The tools are **read-only and re-runnable** (`get_order`, `get_customer`, the
  search tools, `describe_saleor_schema`, the hardened `run_saleor_sql`): no side
  effect can be double-applied, so resume is *correct*, not merely possible. The
  property Day 14/15 chose for **safety** is the property that makes Day 17's resume
  **right** — two days' goals, one property.
- A **mutating executor could never offer resume for free** — re-running a tool's
  `INSERT` after a crash needs exactly-once semantics the series never built, and
  that limit is **named, not hidden**.

**Replay** is resume's sibling and the audit path: re-run from step 0 against the
serialized form and require *identical observations* (**S4**). Resume continues the
run; replay *re-derives* it. Both consume the same checkpoint log — the
investigation's provenance, at rest.

## 6. Retention — TTL, expiry, and deletion as code decisions

Both verbs the curriculum lists are **code decisions, not policy moods** — the
lesson Day 8 taught about the refusal gate and Day 13 about the guards.

- **Expiry**: every record carries `ttl` (or inherits the store default) and
  `expires_at`; `get`, `list_for`, and `recall` consult expiry **first**. An expired
  record **acts absent** — a stale memory that can still be retrieved is not expired,
  it is *lying* — and `compact` physically removes it. Eviction is proven by hostile
  config, not assumed: a tiny-TTL fixture must show the shelf emptying (**S5**).
- **Deletion**: two verbs, because "the user wants this gone" and "this aged out" are
  different rights. `expire` is automatic; `forget(principal, key)` is explicit —
  an operator or a user retracts. Both must remove **derived aggregates**, or deletion
  is cosmetic (Day 15's fail-closed reflex, applied to forgetting). And it is
  *checked*, not hoped: after `forget`, the record is unreachable through
  `get / list_for / recall` (**S6**) — a deleted memory that "sometimes reappears" is
  the same bug as a fabricated citation, on a different shelf.

**Decay is visible.** Records carry `created_at / expires_at / accessed_at`, so which
shelf dies of *age* vs *neglect* is measurable. The counterweight: **retention is not
hoarding.** Default TTLs are deliberately short; a long TTL is a conscious config,
not the default; and the honest limit is stated plainly — what *should* live long-term
(known Saleor concepts, reusable workflows) is decided by **distillation with
relevance scoring** (Day 18), not by a long TTL on a task record. A long TTL is not
memory; it is procrastination with a timestamp.

## 7. Provenance and the boundary — the write path and the read path

The write path is where `recalled ⊆ authored` is enforced (**S7**): a write with no
`created_by`, or from a source outside `AUTHORED_SOURCES`, raises `ProvenanceError`
and never lands. A record present in the file but not authored is dropped on load
(`store.dropped`), so it is unreachable through every reader — the reader can never
be lied to, because the liar never got in. `trace.recalled_subset_authored(records)`
re-checks the invariant at the recall site: every recalled record must carry a
`run_id` and an authored `source`, and `trace.state_columns(…)` promotes the
`run/step/source` triple into the §9 row.

The read path is **context-keyed** (**S8**): `get`/`list_for`/`recall` are keyed by
`(principal, kind, key)`, so a working-state record is served only to its owner
context, a cross-owner read returns nothing, and no run-to-run bleed exists except
by explicit distillation (Day 18). Isolation is by *keying*, not by discipline.

## 8. Episodic vs semantic — the seam, and the "only if useful" measurement

- **Episodic memory** is *what happened*: one investigation's full record — task
  state at rest, plus the trace. Day 17's store serves these **verbatim**: recall a
  finished investigation by `task_id` when a new question matches its metadata. That
  is *memories as retrievable records* — Day 8/9's retrieval applied to the agent's
  own past, with relevance scoring explicitly `PENDING` (Day 18/21).
- **Semantic memory** is *what is generally true*: a distilled "known Saleor concept"
  or "a reusable checkout-repair workflow." The distance between an episode and a
  lesson is **generalization**, and generalization is exactly what a small system
  must not fake. Day 13's rule, applied: semantic memory that is **stale** (a
  distillery without expiry) or **wrong** (a lesson distilled from a single failed
  run) is *worse* than none. `recalled ⊆ authored` must be followed by
  **`authored ⊆ grounded`**: a distilled claim is a grounded claim (Day 14's wording)
  or it is not minted.

The day's answer to "only if useful" is a **measurement, not a vibe** — S3
(resumption is load-bearing) and S9 (off ≡ on for memory-free questions) are the two
verdicts — and the day ships with the **distillation cell `PENDING`**: no
semantic-memory extraction runs until a real key can judge whether anything distilled
is worth recalling. A silently-run distiller is the failure; a named not-built is the
success.

The Day 18 line is drawn precisely so next day's first move is fixed — **schema,
provenance, TTL, deletion → Day 17 (built); distiller, relevance-scored recall,
expiry/re-prompt loop → Day 18 (named)** — and even the curriculum's own candidate
memories are tagged with their real owner:

| Curriculum candidate | Kind | Real owner |
|---|---|---|
| Previous investigations | episodic | Day 17 — recallable verbatim by `task_id` |
| Known Saleor concepts | semantic | Day 18 — distilled |
| Past successful workflows | semantic | Day 18 — distilled |
| User preferences | a *data table*, not a memory | Day 17's store, as data — never generalized |

## What this is not

Stated plainly, because a green state table implies more than it proves:

- **Episodic and semantic memory are named, not built.** The store they will be
  served from is built; the *distiller* (episodes → concepts) and *relevance-scored
  recall* are **Day 18's**. A long TTL is not memory.
- **State is not a streaming UI's conversation.** Message ordering across Day 4's
  mid-stream SSE cuts is **named, not built** — the transcript is persisted per
  exchange, not per token.
- **Multi-process synchronization is named, not built.** The store is single-writer
  by construction; two processes writing one task record concurrently is a recorded
  seam, not a solved problem.
- **Whether memory makes answers better is Day 21's.** The stub proves the *store*
  (put/get/TTL/provenance/deletion/resume); a real key proves the *memory*.
- **`state off ≡ Days 12–16` is a control, not a claim.** It is asserted by S9 *and*
  by re-running Days 7–16's suites unchanged; a persistence layer that moves a
  memory-free answer is a bug wearing a diary.

## Config (env-overridable)

| Key | Default | Meaning |
|---|---|---|
| `STATE_STORE_PATH` | unset | path to the file-backed store; unset → no store on disk |
| `STATE_DEFAULT_TTL` | `86400` (24h) | record TTL when a write `ttl="inherit"` |
| `STATE_MAX_RECORDS` | `1000` | the size cap; oldest-first eviction |
| `STATE_PRINCIPAL` | `default` | the default owner when a run names none |
| `STATE_SCHEMA` (constant) | `2` | the record schema version |

## Negatives (honest, with owners)

1. **Episodic/semantic recall quality** — relevance-scored recall of episodic records,
   and distillation validity — is unmeasured. Owner: **Day 18's** system and **Day
   21's** verdict on this store.
2. **Real-model usefulness** — does memory make real answers better? The stub proves
   the store; this is **Day 21's** benchmark.
3. **Streaming conversation state** — message ordering across mid-stream cuts (Day 4's
   SSE) is named, not built.
4. **Multi-process synchronization** — the store is single-writer by construction;
   concurrent writers of one task record are a recorded seam.
5. **`recalled ⊆ authored` is provenance-shaped** — it proves *who wrote* a record,
   not that the record is *true*. Grounding a distilled claim is Day 18's
   `authored ⊆ grounded`, and adjudication is Day 21's.

## Re-verify, in one line

```bash
python tools/state_smoke.py    # S1–S9 + the Days 7–16 no-regression re-run
```

**`recalled ⊆ authored = ok` on every row that recalls**, and **S9** holds the line:
state off ≡ Days 12–16. See `docs/evaluations/state-day17.md` for the §9 state table.
