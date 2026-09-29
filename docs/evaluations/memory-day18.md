# Memory Day 18 — the subsystem as a contract (M1…M9)

> Generated from the run of `python tools/memory_smoke.py` (offline: the Day-4
> scripted stub for the *chat* wire — including the distiller's JSON replies — the
> Day-7 doc corpus / Day-10 code corpus for *retrieval*, and the Day-14 seeded
> in-memory sqlite core subset for the *data* side). The store is file-backed JSON on
> a `tempfile`; the wall clock is **injectable**, so TTL and decay arithmetic are
> deterministic (the base is real time, so an `expires_at` is honestly in the future
> for any other process reading the same file).
> `MECH` = provable offline, here. `PENDING` = a real gateway is required. Three
> columns carry the day: **`recalled ⊆ authored` = `ok` on every row that recalls**
> (Day 17's headline, still read on the memory shelf), **`authored ⊆ grounded` = `ok`
> on every row that mints** (Day 18's — M3's *rejection* rows prove the minter
> *cannot* lie), and **the `M8` row** — memory off ≡ Days 12–17 (the counterweight,
> exactly as `over-planned? = no` was Day 16's and the S9 row was Day 17's). A green
> table with any of those broken is a failed day.

```
stores: 9 doc chunks (STUB) / 14 code symbols (STUB)   tools: 7 registered
MEMORY_RECALL_TOP_K=5  MEMORY_RECALL_TAU=0.05  MEMORY_MIN_SOURCES=2  MEMORY_DISTILL_TTL=604800
read path: alpha=0.0 (lexical-only → 0 chats, 0 embeds)   distiller: 1 chat/batch
```

## The fixtures, pre-registered before the run (§8)

| ID | What it proves | Input / injection | Expected |
|---|---|---|---|
| M1 | episodic recall relevance | two past investigations, one matching the new question | the **matching** one served above the other, **scored** — never "most recent" |
| M2 | distiller | N ≥ 2 agreeing episodes | one concept, `{claim, source_episodes, strength, ttl, created_by}`, minted with provenance |
| M3 | `authored ⊆ grounded` | an over-claiming / one-source / self-contradicting concept | **rejected at mint time** (a receipt is not a lesson; a lesson must not over-claim) |
| M4 | semantic expiry | a concept decays in relevance before its TTL | **acts absent** on recall before the clock bites; hostile tiny-strength config fires the demotion |
| M5 | deletion end-to-end | `forget` an episode that sired concepts | every concept citing it re-grounded or retired; record **unreachable everywhere**; visible in the trace |
| M6 | privacy | cross-principal read; a preference distilled; a concept naming a principal | **nothing returned**; preferences serve as data, never distilled; the concept is rejected |
| M7 | shelf boundary | a recalled episode, a minted concept | episode served **as an episode** (cited, not absorbed); no bleed except through the distiller |
| M8 | **control** | memory **off**, or an empty store | Days 12–17 fixtures run **unchanged, exactly as green**; over-memory is a bug |
| M9 | curriculum routing | the four candidate memories | previous investigations → episodic; workflows + concepts → semantic; preferences → data table; and **"only if useful" measured** on the two answerable rows |

The `MECH`/`PENDING` split is Day 7's, exact as ever. **M1, M3's checks, M4, M5, M6,
M7, M8, and M9's routing are `MECH`** — deterministic functions of the scorer, the
sample-size/groundedness gate, the expiry/backstop math, `forget`, the keying, and
stored payloads: offline, free, reproducible. **M2's and M3's real-model distillation
quality** (does a real model produce a concept that is correct, general, and grounded)
and **M9's real "useful?" verdict** on an actual question are `PENDING` → **Day 21's
benchmark** on Day 18's distiller and recall. The stub proves the **subsystem**; a real
key proves the **memory**.

## §9 memory table (every cell a real run's)

| id | kind | minted/recalled | score/τ | source_episodes | strength | `recalled ⊆ authored` | `authored ⊆ grounded` | expired | deleted | privacy | cost | wall_ms |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| M1 | episodic | recalled | top1 **1.0** / τ 0.05 | m1a | — | **ok** | — | no | no | ok | 0-chat | 0.905 |
| M2 | semantic | minted | — | e1,e2 | 1.0 | **ok** | **ok** | no | no | ok | **1 chat** | 0.000 |
| M3-overclaim | semantic | **rejected** | — | — | — | — | — | no | no | ok | 1 chat (gated) | 0.000 |
| M3-one-source | semantic | **rejected** | — | — | — | — | — | no | no | ok | 1 chat (gated) | 0.000 |
| M3-contradiction | semantic | **rejected** | — | — | — | — | — | no | no | ok | 1 chat (gated) | 0.000 |
| M4 | semantic | unserved | strength 0.0 / τ 0.5 | — | ↓ | — | **ok** | **relevance**\* | no | ok | 0-chat | 0.000 |
| M5 | semantic | (gone) | — | — | — | — | — | no | **y** | ok | 0-chat | 0.000 |
| M6 | preference | none | — | — | — | — | — | no | no | **y** | 0-chat | 0.000 |
| M7 | episodic | recalled | — | b1 | — | **ok** | — | no | no | ok | 0-chat | 0.000 |
| M8 | — | memory off | — | — | — | — | — | — | — | — | 0-chat | 0.000 |
| M9 | all | measured | τ-fit top1 1.0 | e1,e2 | — | **ok** | **ok** | — | — | ok | **1 chat (distill)** | 0.905 |

\*M4: not expired by the *clock* (`expires_at − now = 86400 s`) — expired by
*relevance* (`strength 0.0 < τ 0.5`). That is the row the day exists to prove.

**`recalled ⊆ authored = ok on every row that recalls: YES`.** **`authored ⊆
grounded = ok on every row that mints: YES`** (and M3's three rejection rows prove it
*bites* — the minter cannot lie). **Rows ok: ALL (58 checks).** The **M8** row is the
counterweight: with memory off — and with memory on over an *empty* store — the
memory-free answer is **identical** (same answer, same evidence, same exit).

## What the run actually produced

**M1 — relevance beats recency.** Two finished investigations are written to one
principal: `m1a` (*checkout order 4 declined payment*) and `m1b` (*order fulfillment
shipping status*), with `m1b` inserted **more recently**. The new question is
`"checkout order 4"`. `recall()` serves `m1a` **first, scored 1.0**, above `m1b`
(0.2693) — the *matching* episode, not the newest (the C1 ancestor, on the memory
shelf). The served record is an **episode** (`served_as: episode`) carrying
provenance, and the read path is **0 chats / 0 embeds**. An **empty** shelf returns
nothing (`refused: true`, `recall_mode: none`) — never a hallucinated "past" — and a
**stale** episode (TTL 10 s, advanced past it) acts absent on the recall path, Day 17's
S5 enforced on the new reader.

**M2 — the distiller mints one grounded concept.** Two agreeing episodes (e1, e2 —
both "checkout failure caused by declined payment") go to one guarded chat; the parsed
candidate passes every gate and is written `kind=semantic` with
`{claim, source_episodes: [e1, e2], strength: 1.0, episodes_considered: 2,
created_by: {run_id: r-m2, step: distill, source: distiller}}`. The store **admits the
distiller's write** because it carries provenance (`recalled ⊆ authored` reads `ok` on
the new record) — `AUTHORED_SOURCES` gained `distiller`, so S7's write-time check is
its tab-keep. The minted concept is then **recallable as a concept** (served alongside
the two episodes), and `authored ⊆ grounded` reads `ok` on the mint.

**M3 — the gate is code, not sentiment.** Three twin candidates are each refused **at
mint time** and **no softened record is written** (the semantic shelf stays empty):

- **over-claim** — the claim adds "…or shipping error," and `shipping`/`error` appear
  in **no** source's evidence → `ungrounded`.
- **one-source** — the same claim cites a single receipt → `sample_size` (a lesson from
  one run is an anecdote).
- **self-contradiction** — the concept asserts `checkout_failure_cause: "shipping"`
  while its own sources say `"payment"` → `contradiction` (the semantic card of Day 13's
  fabrication).

The sample-size rule is also proven directly on the gate: `validate_concept` over a
single-episode batch with `min_sources=2` returns `["cites unprovided episode",
"sample_size"]`.

**M4 — relevance expires before the clock.** A concept with `strength 0.0` and
`recall_threshold 0.5` is written with a live 24 h TTL (`expires_at − now = 86400 s`).
It **is not clock-expired** but **is relevance-decayed** (`is_decayed()` true), and on
recall it **acts absent** (`recalled: []`, `recall_mode: "below-τ"`) — while staying
**alive and inspectable** through `list_for` (alive, unserved). A healthy concept
(`strength 0.9 ≥ τ`) *is* served; one `decay_concept(rate=0.5)` step drops it to `0.45`
and it unserves itself. The re-prompt loop's verbs read correctly by code: a
contradiction → `re-distill`; a decayed concept → `retire`.

**M5 — deletion reaches the derived aggregate.** A minted concept citing `e1+e2` is
re-grounded when `forget(key=/episodic/e1)` runs: it drops to one surviving source,
falls below the sample-size rule, and is **retired** (`action: retired, reason: "below
sample size after deletion"`), then is **unreachable** through `get`/`list_for`/`recall`
(the surviving episode e2 is still legitimately recallable — the *concept* is gone).
The other direction is proven too: a concept citing `e1+e2+e3` is **re-grounded** — its
`source_episodes` shrinks to `[e2, e3]`, `strength 1.0 → 0.6667`, `action:
re-grounded`. Deletion is visible in the trace (`store.regrounded` is populated;
`memory_columns(deleted=True)` carries `deleted: true`).

**M6 — privacy is the keying and the mint gate.** A preference (`currency = EUR`)
serves **its own** principal as cited data, is stored `kind=preference` (a data table,
not a memory), and a **cross-principal** preference read returns **nothing**; a
cross-principal **memory** recall returns **nothing**. `list_for(u1, *)` shows u1 only
u1's records. The preference is **not** on the episodic shelf, so the distiller's
episodic-only batch can never select it. And a concept that **names a principal**
("customer ada always fails checkout") is **rejected** (`["ungrounded",
"principal_name"]`) — the privacy wall is a code check on the candidate parse, not a
prompt hope.

**M7 — the shelf boundary holds.** Recalling an episode mints **no concept** and spends
**no chat** (the ledger is unchanged; the semantic shelf is empty); every served record
is an **episode**. The prompt block the recall contributes states the rule in-band —
*"Serve these as RECORDS you may cite, not as rules: do not generalize an episode into
a lesson (only a distilled concept is a lesson)"* — so an episode cannot be promoted
inline (S8 with a search engine on top, refused). A different principal recalls
nothing: no run-to-run bleed except through the distiller.

**M8 — the counterweight.** Three runs of a memory-free question (`Fetch order 1`) —
baseline (no store), **memory off** (`enabled=False`), and **memory on over an empty
store** — produce an **identical answer, evidence set, and termination**. The run
reports the switch: `off → memory_on_off: "off"`; empty on-store → `memory_on_off:
"on"`, `recall_mode: "none"`. Adding memory changed **nothing** when there was nothing
to recall — the addition is *silently* optional, which is the whole point. The
deliverable's first arrow is proven here too: the **memory-on** run **indexed itself**
— exactly one *authored* `kind=episodic` record (`/episodic/run-…`) written at
run-end (`episodic_indexed: true`) — while the **memory-off** run wrote **nothing**
(`episodic_indexed: false`); and `MEMORY_ENABLED=0` forces the subsystem off
(`memory_on_off: "off"`) even over an enabled store, so the documented knob is wired,
not decorative.

**M9 — curriculum routing, and the meter.** The four candidate memories land on their
real shelves: **previous investigations → episodic** (1 record), **past workflows +
known concepts → semantic** (1 distilled concept, `source_episodes: [e1, e2]`), **user
preferences → a data table** (`kind=preference`, and *not* minted as a concept). And
the "only if useful" measurement is recorded, not assumed: a matching recall costs
**0 chats**, while the fresh investigation it would replace cost **$0.00003060** (the
baseline run's ledger) — the recall path beats the alternative on cost, and whether it
beats it on *quality* is Day 21's.

## Layer mechanics (the day's own unit checks)

| Assertion | Result |
|---|---|
| `recall()` is scored, gated at τ, and refused when nothing matches (not a most-recent fallback) | ok |
| an **empty** shelf returns nothing; a **stale** episode acts absent on the recall path | ok |
| recall is **lexical-only** (`alpha=0.0`): 0 chats, 0 embeds on the read path | ok |
| an episode is served **as an episode** — carrying `task_id`, evidence, conclusion, provenance | ok |
| the distiller parses a fenced/bare JSON candidate; an unparseable reply is discarded, never written | ok |
| mint-time gates: `sample_size ≥ MEMORY_MIN_SOURCES` · `authored ⊆ grounded` · no source contradicts · no principal names | ok |
| a rejected candidate writes **no** record — an over-claim is never stored "softened" | ok |
| mint provenance `created_by: {run_id, step: distill, source: distiller}` — the store admits the distiller as an **author** | ok |
| a concept's `strength` clears its `recall_threshold` or it **acts absent** (alive, unserved, still inspectable) | ok |
| the re-prompt loop's verbs: contradiction → re-distill · sustained misses → decay · decayed → retire | ok |
| `forget` re-grounds every concept citing the deleted episode: `source_episodes` shrinks, or the concept is retired below sample size | ok |
| a preference is `kind=preference` (data), never on the episodic shelf, never distilled | ok |
| cross-principal preference/memory reads return **nothing** (the keying); `list_for(p, *)` is principal-scoped | ok |
| a concept naming a principal is rejected (`principal_name`) | ok |
| memory off (and an empty store) leaves a memory-free answer byte-identical (the M8 control) | ok |
| the run-end **episodic index** — a finished run with memory on writes exactly one authored `kind=episodic` record; memory off writes nothing | ok |
| `MEMORY_ENABLED=0` forces the subsystem off even with an enabled store (the knob is wired, not decorative) | ok |
| `trace.memory_columns` / `recall_mode` / `concept_subset_grounded` carry the §9 verdicts | ok |

## No-regression line (Days 7–17 ride the memory subsystem)

| Line | Result |
|---|---|
| `test_day7.py` … `test_day10.py` re-run in subprocesses | **exit 0 — `ALL DAY 7..10 (MECH) CHECKS PASSED`** |
| `tools/tool_smoke.py` (T1–T7) | **exits 0 — `ALL DAY 11 (MECH) CHECKS PASSED`** |
| `tools/agent_smoke.py` (A1–A8) | **exits 0 — `ALL DAY 12 (MECH) CHECKS PASSED`** |
| `tools/failure_smoke.py` (F1–F9) | **exits 0 — `ALL DAY 13 (MECH) CHECKS PASSED`** |
| `tools/data_smoke.py` (Q1–Q8) | **exits 0 — `ALL DAY 14 (MECH) CHECKS PASSED`** |
| `tools/sql_attack_smoke.py` (X1–X11) | **exits 0 — `ALL DAY 15 (MECH) CHECKS PASSED`** |
| `tools/plan_smoke.py` (P1–P9) | **exits 0 — `ALL DAY 16 (MECH) CHECKS PASSED`** |
| `tools/state_smoke.py` (S1–S9) | **exits 0 — `ALL DAY 17 (MECH) CHECKS PASSED`** |

The subsystem is **additive** — two consumers of Day 17's store, no new storage shape.
Day 17's `S1–S9` tests run **untouched**; Day 18 changes **no** stored record's schema
(the semantic shelf's `strength`/`recall_threshold` are additive, `None` on every
non-semantic record). The only edit to existing code is minimal and optional:
`run_agent` gains `memory_store=None` (the recall pre-step + the preference data
table), `StateStore` gains `strength`/`recall_threshold`/`touch`, `forget` gains
re-grounding, and `AUTHORED_SOURCES` gains `distiller`. With `memory_store=None` the
transcript is byte-for-byte Days 12–17 — which is exactly what M8 proves, and why a
memory that changed a memory-free answer would be an **M8** failure arriving through
the front door.

## Cost shape (memory's first billable shelf)

| Event | What it spends |
|---|---|
| **episodic recall** (`recall()`) | **0 chats, 0 embeds** — Day 8's lexicon + fusion + Day 9's MMR/dedup over stored signatures |
| the **preference** data table (load for a principal) | **0 chats** — a store read |
| **`forget` re-grounding** / **semantic expiry** / **the keying** | **0 chats** — set arithmetic and dict CRUD |
| **distillation** (N episodes → 1 concept) | **1 chat per batch**, gated by code — the day's only bill |
| a **rejected** candidate (M3/M6) | **1 chat** (the batch ran) — and **no write**; the gate, not the model, decides |
| the `PENDING` cross-encoder relevance scorer | **not paid** — a chat per candidate-pair, `PENDING` |

Day 18 is the series' first departure from "0 tokens": the **recall path preserves the
free deterministic tier** (the ledger Day 9 protected), and the **distiller bills one
chat per batch**, recorded per concept. M9's meter makes the bet explicit — a recall is
**0 chats vs a fresh investigation's ≥1** — and the *quality* half of "only if useful"
is Day 21's verdict, not a fixture's.

## Config knobs (Day 18)

| Key | Default | Meaning |
|---|---|---|
| `MEMORY_ENABLED` | `1` | the switch; `0`/`off` → recall serves nothing (M8) |
| `MEMORY_RECALL_TOP_K` | `5` | the served set's size (Day 6's budget floor) |
| `MEMORY_RECALL_TAU` | `0.05` | Day 7's gate, on the memory shelf — below it, absent |
| `MEMORY_MIN_SOURCES` | `2` | the sample-size rule (a receipt is not a lesson) |
| `MEMORY_DISTILL_TTL` | `604800` (7d) | a concept's clock backstop; the relevance meter is the policy |
| `MEMORY_STRENGTH_DECAY` | `0.5` | the re-prompt loop's decay step (`strength × (1 − rate)`) |

## Negatives / `PENDING` (honest, with owners)

1. **Real-model distillation quality** — does a real model mint a concept that is
   correct, general, and grounded? The stub proves the *gate*; the *model* is **Day
   21's** benchmark on Day 18's distiller. `PENDING`.
2. **Real "only if useful" verdict** — M9 measures the *cost* half (recall is free, the
   fresh alternative is not); whether recalled memory makes real answers *better* is
   **Day 21's**. `PENDING`.
3. **The paid relevance scorer** — the `PENDING` cross-encoder (`ai_os/rerank.py`)
   would be a chat per candidate-pair; it is **named, not paid**, and the read path
   stays free until it is knowingly billed.
4. **(Sub)agents** — the natural next consumer of shared memory — are **Day 19**, and
   the curriculum's rule is explicit: *do not assume multiple agents are useful.*
5. **Cross-machine / cross-schema consolidation** — the store is single-writer; syncing
   memories is **named, not built**.
6. **Memory poisoning** — using the store as an injection vector is **Day 25's**
   security seam, and the reason the store is **closed** and provenanced today.
7. **"You cannot un-distill"** — a concept already absorbed into a past answer cannot be
   un-remembered from that answer; deletion ends *future* recall. Stated, with an owner.

## Re-verify, in one line

```bash
python tools/memory_smoke.py    # M1–M9 + the Days 7–17 no-regression re-run (memory on and off)
```

**`recalled ⊆ authored = ok` on every row that recalls** and **`authored ⊆ grounded =
ok` on every row that mints**, with the **M8** row holding the line: memory off ≡ Days
12–17, and Days 7–17's suites green through the subsystem. The `PENDING` line, plainly:
**whether a real model's distillation and recall make answers better is Day 21's
verdict on Day 18's subsystem, on Day 17's store.**
