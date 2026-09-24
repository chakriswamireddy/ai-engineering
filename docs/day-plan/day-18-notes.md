# Day 18 Notes — Persistent Memory

> Notes answering `docs/day-plan/day-18.md`.
> Seventeen days built a machine that *reads* (Days 6–10), *acts* (Day 11), *drives itself* (Day 12), *survives failure* (Day 13), *computes* (Day 14), *defends itself* (Day 15), *thinks before acting* (Day 16), and *remembers what it did* (Day 17). Every memory until now was the agent's own work held in a closed, provenanced, expiring store — and Day 17 was explicit about the seam: it built the shelves and named the consumers (the distiller, the relevance scorer, the expiry/re-prompt loop) as **Day 18's, only if useful**. Day 18 builds them. The curriculum is literally titled *Persistent Memory*, with the objective the one word that changes everything — "add **controlled** long-term memory" — and five learn items: *memory retrieval, memory relevance, expiration, deletion, privacy*. The deliverable is the **basic persistent memory subsystem**: **relevance-scored recall** of the agent's own past (Day 8/9's retrieval turned on previous investigations — the *matching* one, scored, not the most recent), a **distiller** that turns agreeing episodes into one grounded concept (a known workflow, a known Saleor concept) under its own mint-time gates, **semantic expiry** with Day 17's clock as the backstop, deletion extended to the distiller's derived knowledge, and **privacy** as a first-class duty — with *user preferences* honestly demoted to **a data table, not a memory**.
> The single idea: **Day 17 said "you may only recall what you authored"; Day 18 adds "and you may only distill what is grounded" — memory stays closed, and now it also earns its shelf.** Recall is Day 8/9's scorer over the agent's own receipts; the distiller is one chat whose *output* is gated by code (sample size, `authored ⊆ grounded`, no principal names); expiry is relevance-governed with the clock demoted to a backstop; deletion reaches every concept that cites a deleted source; and the curriculum's "only if useful" — the meter Day 17 could only name — becomes a measured number (M9) with a control (M8) proving that memory off ≡ Days 12–17. Memory costs its first chat today (the distiller); the recall path stays free, and both are metered, not assumed.

---

## 1. Memory Retrieval — Day 8/9's Engine, Turned on the Agent's Own Past

Day 17 made "remember what the agent did" *true*; Day 18 makes it *useful*. Everything the agent needs to retrieve already exists — task records, transcripts, checkpoints, all `principal`-keyed, all carrying provenance — so the curriculum's first learn item, **memory retrieval**, is not a new engine. It is the series' retrieval machinery, pointed at the agent's own finished work: index the episodes, score them against a new question, serve the ones that **match**. Day 8's `Retriever` (lexicon + fusion), Day 9's `rel()` + MMR dedup, Day 7's threshold gate, Day 6's budget floor — every engine is reused; only the *catalogue* changes (from Saleor chunks to the principal's investigations).

The discipline that keeps this small is the boundary Day 17 drew and Day 18 must not blur: **episodes are recalled as episodes.** A finished investigation is a *receipt* — returned with its `task_id`, its evidence, its conclusion, its provenance — to be *cited* like any Day 6 source, never to be re-worded, merged, or generalized by the answer loop. Generalization is the distiller's job (§5) and only its. The moment the recall path lets a receipt read as a lesson ("order 412 failed at checkout, therefore checkout failures trace to payments") it has crossed S8's boundary with a search engine on top — the retrieval version of Day 9's D2 leak.

Day 18 is also the day retrieval learns it is not the whole story. The catalogue is small by construction (sized, expiring, principal-keyed), so `recall()` is the **free deterministic tier** — the exact arithmetic Day 8/9 verified, costing 0 chats and 0 embeds. That is not a boast, it is Day 9's ledger preserved: the memory *read* path never bills until one opts into the `PENDING` cross-encoder, and the meter for that is recorded when (if) it is ever paid.

---

## 2. Memory Relevance — the "Only If Useful" Meter, Now a Scorer

The curriculum's second learn item, **memory relevance**, is the fulfillment of a pledge that has been running since Day 7: *"introduce persistent memory only if useful."* Day 17 could only *name* that measurement — an S-fixture that demands memory be load-bearing (S3) and a control that demands it vanish when it isn't (S9). Day 18 owns the actual number. Relevance **is** the meter: a memory is worth recalling only if it beats the alternative — a fresh investigation — and below threshold it is **not served**. The RAG gate Day 7 put in front of retrieval (refuse when nothing covers the question) is re-born on the memory shelf: refuse to *recall* when nothing covers the episode.

Two relevance judgments, kept distinct:

- **Episodic relevance** — does this *past investigation* answer this *new question*? Score the episode's signature (question paraphrase, tools used, entities touched, conclusion) against the question.
- **Semantic relevance** — does this *distilled concept* explain this *new question*? Score the concept's claim against the question.

Both feed one recall path; both share one refusal rule. The scoring choice wires in the day's honest architecture: memory serves the loop the way retrieval served the RAG answer — it improves the answer's *starter set*, never its honesty contract.

The failure shapes relevance exists to prevent are Day 9's, re-described on the memory shelf:

- **Irrelevant recall** — the most recent investigation served *because* it is recent (the ancestor of C1's refund-doc trap: recency dressed as relevance).
- **Half-relevant recall** — a concept that matches one term of a two-intent question and is served whole (Day 8's decomposition problem, on the memory shelf).
- **Confidently wrong recall** — a *stale* concept that sounds like the answer (the memory-flavored version of Day 13's fabricated citation: fluent, confident, and grounding-free).

Each becomes a fixture. And the ordering is deliberate: **retrieval before distillation**, because the scorer is what decides whether any distilled concept is worth its minting budget at all — a concept recall never matches was a wasted chat.

---

## 3. The Subsystem — Two Tranches, One Store, Four Invariants

Day 17 named the seam precisely so this day's design is mostly archaeology: the **distiller** and the **relevance-scored recall** are not new stores, they are *consumers* of the store that already exists. The design rule is **no new storage shape** — episodic recall reads `kind=episodic`, the distiller writes `kind=semantic`, both into the same file-backed, schema-carrying, provenance-enforcing store; S1–S9's writes, expiry, and deletion apply to the new kind untouched. Day 18 adds a *purpose*, not a schema.

Three design decisions give the day its shape:

**Decision 1 — episodic recall is a read-path function, not a feature.** `recall(question, principal)` = retrieve over `past_investigations_of(principal)` → top-k above τ → attach each episode's evidence + `task_id` + provenance into the running context **as an episode**, cited like any citation. Nothing new is called; the answer loop gains a "consult the past" needle it never had.

**Decision 2 — the distiller is a chat, gated by code.** One batch job collects N finished task records; one model call runs under a strict contract (distill only what the evidence supports; name the supporting episodes; generalize only across episodes that agree; never mint from a single receipt) and produces a candidate concept:

```text
{ claim, source_episodes[], strength, ttl, created_by, schema }
```

Minted only if: **≥2 agreeing sources** (a one-episode "lesson" is an anecdote), **no source contradicts the claim**, and the claim is general — the assertion-check of Day 14 (`asserted ⊆ returned`) reconstructed for distillation as **`authored ⊆ grounded`**.

**Decision 3 — expiration becomes relevance-governed, the clock demoted to backstop.** Day 17's `expires_at` stays as the hard floor; Day 18 adds the strength metric. A concept is served only while its strength (supporting episodes, recency, recall hit-rate) clears the recall threshold; decay is *re-prompt-driven* (each served recall refreshes `accessed_at`; a concept that stops matching decays by relevance first, then by the clock). This is Day 17's own question from its §6 — *"is semantic expiry (relevance + confidence instead of a clock) the real Day 18 work?"* — answered in code, with the clock left honest as the backstop.

**Decision 4 — privacy is a data-table duty, baked into the design.** The curriculum's *user preferences within the application* are not a memory: they are a small `principal`-keyed data table — facts the answer loop may *cite as data*, readable only by their principal, never distilled, never generalized into "the user always…". Privacy covers the whole subsystem because memory is the agent's most personal surface: principal-bounded reads on every path, no generalization from personal data, consent-visible provenance, and first-class deletion are *design* decisions, not features.

The day's four invariants, pre-committed:

1. **`recalled ⊆ authored`** (Day 17, untouched) — nothing recallable that the system did not write, with provenance.
2. **`authored ⊆ grounded`** (Day 18's headline) — a distilled claim is grounded in its source episodes, or it is not minted.
3. **Recall is scored and above τ, or absent** — relevance is the meter; recency and vibes are not.
4. **Memory off ≡ Days 12–17** (M8) — over-memory is a bug, Day 17's S9 reflex on the memory shelf.

---

## 4. Episodic Recall — Relevant Past Investigations, Scored

The build has four parts, each a known reflex.

**The index.** At a run's end, the finished task record becomes `kind=episodic` with a compact signature — the question's paraphrase, the tools used, the entities/order ids touched, the conclusion — enough for lexical scoring, small enough to keep the shelf sized. The signature is Day 8's lexicon-friendly corpus, minus the corpus.

**The scorer.** `recall()` = Day 8's `Retriever` over the principal's episode signatures → Day 9's dedup/MMR to drop near-duplicate investigations → Day 7's gate → top-k enforced by Day 6's context budget. Zero new models; the arithmetic is verified arithmetic. The M1 fixture is the point: two past investigations, one matching the new question — the matching one serves **above** the other, *scored*, because a recalled memory is worthless if serving the *most recent* investigation is what "memory" degrades into (Day 9's C1 read its own lesson).

**The citation discipline.** The recalled episode enters the answer path carrying its evidence, its conclusion, and its provenance — a block the answer may *cite*, never absorb as asserted fact. `cited ⊆ retrieved` holds over episode records exactly as it held over chunks; the difference is the retrieved set is the agent's own receipts. M7 is the enforcement: no run-to-run bleed except through a distiller-minted concept.

**The honest refusal.** An empty shelf returns **nothing** — not a fabricated "past," not a most-recent-anyway fallback (M8's empty-store control). A stale episode acts absent (Day 17's S5 applies to the recall path too). Retrieval refuses rather than guesses, the Day 7 reflex, once more.

The cost column is why this is the day's *cheap* tranche: the whole recall path is lexicon + fusion + MMR over stored signatures — **0 chats, 0 embeds**, the same ledger Day 9 protected. The `PENDING` cross-encoder stays `PENDING`, and if it is ever paid, the chat per candidate-pair lands in the table before it lands in the code.

---

## 5. Semantic Memory — the Distiller, Episodes → Grounded Concepts

The distiller is where Day 18's cost honesty starts, because it is the first shelf that **bills a chat** — one per batch, and only under code's permission.

The prompt's contract is short and strict: *distill only what the evidence supports; name each supporting episode; generalize only across episodes that agree; never mint from a single receipt.* The output, a candidate concept, is then gated not by the model but by **code**, at three checkpoints:

1. **Sample size ≥ 2.** A lesson from one run is an anecdote. The distiller may *store* a single episode (on the episodic shelf, recallable as a receipt); it may not *mint* a general truth from it. This is the "receipt vs lesson" boundary made computable.
2. **`authored ⊆ grounded`.** Every claim in the candidate must be satisfiable against the **union of its source episodes' evidence** — each episode carries its evidence, so the check is set membership over that union, exactly Day 14's `asserted ⊆ returned` reconstructed for distillation. An over-claim is **rejected at mint time**, never stored "softened." A claim a source episode *contradicts* is rejected too — a concept asserting a pattern its own evidence denies is the semantic card of Day 13's fabrication.
3. **No principal names.** A concept that names a customer, an order, or a payment id is a **privacy failure** (§7) and is rejected. Data can be the *source* of a lesson ("checkout failures trace to the payment"); an individual can never *be* the lesson.

What is minted carries full provenance: `created_by = {run_id, step, source: "distiller"}`, `source_episodes = [task_ids]`, `strength = |agreeing episodes| / |episodes considered|`, `ttl` from policy. The store accepts it **because it carries provenance** — the distiller is only another authorized writer, and Day 17's S7 write-time check is its own tab-keep. `recalled ⊆ authored` was not weakened to admit the distiller; the distiller *is* an author.

The curriculum's candidate list routes through this single mechanism:

| Curriculum memory | Real shelf | How it is minted |
|---|---|---|
| Previous investigations | episodic | no minting — recalled verbatim, scored, cited as an episode |
| Past successful workflows | semantic | distilled over **verified** investigations (plans that ran green to `conclusion ⊆ evidence`) |
| Known Saleor concepts | semantic | distilled the same way, with the doc corpus (Days 6–10) corroborating the claim |
| User preferences | **data table** | never minted — stored per-principal, cited as data |

The control on this list: a "known Saleor concept" can never be minted from one customer's checkout story — because the sample-size rule wouldn't let it, and because the concept's shelf-mates (workflows) trace to verified runs, not anecdotes.

The honest `PENDING` sails with the rest of the days: the **routing** of the distiller (when it fires, the sample-size gate, the groundedness check, provenance minting) is `MECH` — deterministic functions of stored episodes, offline and free. Whether a *real* model distills a concept that is actually correct, general, and useful is **Day 21's** benchmark on Day 18's distiller — the same day that owns the planner's quality and the scorer's real answer.

---

## 6. Semantic Expiry, Deletion, and the Re-prompt Loop

Day 17 proved the **clock** (expiry consulted before every read, compaction physically reclaiming). Day 18's learn item *expiration* is the harder half: **a fact's truth does not live on a clock.** A fact that stays true deserves to outlive `expires_at`; a fact that was always wrong should never have been stored — Day 17's own question, delivered as a duty.

The answer is **semantic expiry with the clock as backstop.** Every concept carries `strength` (supporting episodes, recency, recall hit-rate) and a recall threshold; every `recall()` records `accessed_at` and a successful re-serving refreshes it. A concept whose strength falls below τ — its sources expired, or recall stopped matching, or a fresh investigation contradicted it — is **demoted below threshold: alive, but unserved**. Then, and only then, the clock retires it. M4 is the fixture that proves the ordering: a concept that decays in *relevance* acts absent **before** its TTL bites.

The **re-prompt loop** closes the circuit the curriculum's "expiration" implies. When a served concept stops earning its keep — unmatched recalls over N turns, or a contradiction (a fresh investigation's grounded evidence now denies it) — the loop chooses, by code:

- **re-distill** — feed the *larger* episode set back to the distiller and re-mint;
- **decay** — lower strength below τ, the concept unserves itself;
- **retire** — delete.

One rule keeps this honest: a concept is **never refreshed by re-serving alone**. Recency is not truth — the Day 9 lesson, found again on the memory shelf. A concept that keeps being *read* is not a concept that keeps being *right*.

**Deletion** extends Day 17's first-class operation to the distiller, where the derived aggregate now means *the concepts.* `forget(principal, key)` on an episode must **re-ground every concept that cites it**: re-check the concept against its remaining sources, and if it now fails the sample-size rule (it had 2 sources, one is deleted), **retire it too**. M5 proves unreachability end-to-end. And the day states its most honest boundary as a result, not an apology: **"you cannot un-distill."** A concept already recalled and absorbed into a past answer cannot be un-remembered *from that answer* — deletion means future recall ends, not that history is rewritten. That is Day 18's privacy footnote and its epistemic humility in one sentence.

---

## 7. Privacy — Memory Is Personal Data, Preferences Are a Table

The curriculum's fifth learn item, *privacy*, is the easiest to skip and the hardest to fake. Memory is the agent's **most personal surface**: it holds what users asked, what the agent investigated about them, and their stated preferences. Day 18 treats it as personal data of a *principal*, full stop.

Three mechanisms make that real:

1. **Every read is principal-bounded** (Day 17's `principal` keying, extended to *every* path — including the distiller's batch source selection, which may only read its owner's episodes). M6 proves a cross-principal preference or memory read returns **nothing**.

2. **User preferences remain a data table, not a memory.** A `kind=preference` record per principal — `{principal, key: "currency", value: "EUR", source: "user-stated", created_at, ttl}` — is served to its own answer loop as **cited data** and nothing else. It is never distilled, never generalized, never merged across principals. Day 17 tagged this by analysis ("a preference is a data table, never a lesson"); Day 18 codes it. The moment the agent says "the user always pays in EUR" it has turned a datum into a doctrine — the privacy failure that looks like helpfulness.

3. **The distiller's wall is a mint-time check.** Distillation runs over task records the runs authored; a record that contains personal data (an order, a customer id — Day 14/15's data surface) is **seed, not lesson**. The distiller may form "checkout failures → inspect the payment" from order data; it may not form "customer X always fails." A concept that names a principal is rejected by the no-principal-names gate, which is not a prompt sentiment but a code check on the candidate parse.

Two closing duties keep it auditable: **deletion as a right** (`forget` on a preference is the same aggregates-included operation — M5), and **inspectability as the privacy answer** — `list_for(principal, *)` can show a principal everything the subsystem holds about *them*, and nothing about anyone else (Day 17's inspectable store, now the answer to "what do you know about me?").

The tension is recorded Day 15's way: **privacy is enforced by the write path and the keying, not by the prompt.** The distiller is a model; its output is gated by code (groundedness, sample-size, no-principal-names), exactly as Day 15 gated the SQL by role rather than by asking nicely. When the store is the attacker's target (Day 25's security), the *closed store* — not a sanitizer's hope — is what makes the injected memory unreachable.

---

## 8. Test: The Memory Set (M1…M9), and the Store as a Contract, Extended

Expected behavior written **before** any run, one fixture per property plus a control:

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

The `MECH`/`PENDING` split is Day 7's, exact as ever. **M1, M3's checks, M4, M5, M6, M7, M8, and M9's routing are `MECH`** — deterministic functions of the scorer, the sample-size/groundedness gate, the expiry/backstop math, `forget`, the keying, and stored payloads: offline, free, reproducible. **M2's and M3's real-model distillation quality** (does a real model produce a concept that is correct, general, and grounded) and **M9's real "useful?" verdict** on an actual question are `PENDING` → **Day 21's benchmark** on Day 18's distiller and recall. The stub proves the **subsystem**; a real key proves the **memory**.

Three obligations, unchanged in spirit:

1. **No regression on Days 7–17, with memory on *and* off.** The distiller and the recall path change no stored schema; Day 17's S1–S9 tests run untouched. **M8 guarantees the off case equals Days 12–17 exactly**, and the on-case *with an empty store* must also equal it — a memory subsystem that changes a memory-free answer is an M8 failure arriving through an empty shelf.
2. **The day's own counterweight.** Like Day 16's `over-planned?` and Day 17's S9, the **M8 no-change row** answers the curriculum's "only if useful" with a measurement, not a vibe: adding memory is *supposed* to leave memory-free answers untouched.
3. **Isolation, one owner per symptom.** Irrelevant recall → **the scorer/τ**. A concept that over-claims → **the distiller's `authored ⊆ grounded` gate**. A stale concept recalled → **semantic expiry**. A deleted episode's concept survives → **`forget`'s derived-aggregate removal**. A cross-principal read → **the keying**. Memory changing a memory-free answer → **the M8 control**. Say which, never vibes.

---

## 9. Measure: Memory as a Contract, at Its First Cost

Rule-4 as a memory table, one row per fixture, every cell a real run's:

| Fixture | Kind | Minted/recalled | Score/τ | Source episodes | Strength | recalled ⊆ authored | authored ⊆ grounded | expired? | deleted? | Privacy ok? | Cost | wall_ms |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| M1 episodic recall | episodic | recalled | top-1 | — | — | **ok** | — | no | no | ok | 0-chat | … |
| M2 distiller | semantic | minted | — | N | s | **ok** | **ok** | no | no | ok | **1 chat** | … |
| M3 groundedness | semantic | **rejected** | — | 1 / weak | — | — | — | no | no | ok | 1 chat (gated) | … |
| M4 semantic expiry | semantic | unserved | < τ | — | ↓ | — | **ok** | no* | no | ok | 0-chat | … |
| M5 deletion | semantic | (gone) | — | — | — | — | — | no | **y** | ok | 0-chat | … |
| M6 privacy | preference | none | — | — | — | — | — | no | no | **y** | 0-chat | … |
| M7 boundary | episodic | recalled | top-1 | — | — | **ok** | — | no | no | ok | 0-chat | … |
| M8 control | — | memory off | — | — | — | — | — | — | — | — | 0-chat | … |
| M9 routing/useful? | all | measured | τ-fit | — | — | **ok** | **ok** | — | — | ok | 1 chat (distill) | … |

*M4: not expired by the *clock*, expired by *relevance* — the row the day exists to prove.

Three columns carry the day: **`recalled ⊆ authored = ok`** (Day 17's headline, still read on every row that recalls), **`authored ⊆ grounded = ok`** on every row that mints (M3's row proves the distiller *can't* over-claim — the reader can't be lied to, and the minter can't lie either), and **M8's row** — memory off ≡ Days 12–17 — the counterweight. The **Cost** column is the day's honest headline and the series' first departure from "0 tokens": the recall path is **0 chats, 0 embeds** (the free deterministic tier preserved), but the **distiller is 1 chat per batch**, recorded per concept — and M9's two answerable rows measure the whole bet: **"does recall beat a fresh investigation?"** as a number with wall_ms, not a promise.

The negatives column, with owners: whether distilled memory makes *real* answers better is **Day 21** (the same judgment that owns planner quality); (sub)agents — the natural next consumer of shared memory — are **Day 19**, and the curriculum's rule is explicit (do not assume multiple agents are useful); cross-machine or cross-schema memory consolidation is **named, not built** (single-writer store, no sync seam yet); and memory *poisoning* — using the store as an injection vector — is **Day 25's** security seam, which is precisely why the store is closed and provenanced today. Empty negatives mean the day learned nothing; these have owners.

---

## 10. Documents

- `docs/concepts/memory.md` — the persistent-memory subsystem: episodic vs semantic shelves, **relevance-scored recall** (Day 8/9 on the agent's own past), the **distiller** and **`authored ⊆ grounded`**, **semantic expiry** vs the clock, deletion's derived aggregates and the **"you cannot un-distill"** limit, **privacy** as principal-keyed personal data with **user preferences as a data table**, and the "only if useful" measuring stick.
- `docs/evaluations/memory-day18.md` — the §9 memory table with `recalled ⊆ authored = ok` **and** `authored ⊆ grounded = ok`, the M1…M9 pre-registered fixtures and the M8 control, the scorer / τ / configs and defaults (`MEMORY_RECALL_TOP_K`, `MEMORY_RECALL_TAU`, `MEMORY_MIN_SOURCES`, `MEMORY_STRENGTH_DECAY`, `MEMORY_DISTILL_TTL`), the no-regression lines (Days 7–17 with memory on *and* off, plus an empty-store control), and the real-distillation `PENDING`.
- PROGRESS rollup: Day 18 mechanics move to `DONE` under the stub (episodic index + scored recall, distiller routing with the sample-size/groundedness/no-principal gate, semantic expiry + re-prompt routing, aggregates-inclusive deletion, preference table + keying, and the M8 off-switch verified offline); the semantic cells stay `PARTIAL`/`PENDING` until a real gateway run confirms a model can mint a concept worth recalling — Day 21's first move.

---

## 11. Daily Learning Record

### Problem

Seventeen days produced an agent that is **competent, honest, and trapped in its own present**: Day 12's transcript, Day 16's plans, and Day 17's stores all remember *what the agent did*, but nothing lets a *new question* profit from the past. The store holds finished investigations as closed records; the curriculum's own promise — "add *controlled* long-term memory" — is still a pledge ("only if useful") rather than a mechanism. The problem is to build Day 18's **basic persistent memory subsystem** on Day 17's store without breaking anything: the recursion must stay **additive** (memory off ≡ Days 12–17), the store must stay **closed** (`recalled ⊆ authored`), the new faculty must be **measurable** (relevance decides; "only if useful" becomes a number), and — the day's own tax — the *first* shelf that bills a chat (the distiller) must earn its meter. And it must route the curriculum's four candidate memories correctly, including the hardest: *user preferences* are a **data table, not a memory**.

### Concept

**Persistent memory is Day 17's store given a purpose under two invariants: you may only recall what you authored (`recalled ⊆ authored`), and you may only distill what is grounded (`authored ⊆ grounded`).** The subsystem is two consumers of the existing store. **Episodic recall** (`recall(question, principal)`) is Day 8/9's retrieval turned on the agent's own past: finished investigations get compact signatures, the scorer finds the *matching* one (never "most recent"), Day 7's threshold refuses below-τ, Day 9's MMR dedup drops near-duplicates, and the served episode is **cited as an episode** — a receipt, never a lesson. **The distiller** turns N agreeing episodes into one `kind=semantic` concept — `{claim, source_episodes[], strength, ttl, created_by}` — through one guarded chat: minted only if **≥2 agreeing sources** (a single episode is an anecdote), only if **`authored ⊆ grounded`** (every claim satisfiable against the union of source evidence — Day 14's assertion contract reconstruction), and only if **no principal is named** (a privacy check, not a prompt sentiment). **Expiration** becomes relevance-governed with Day 17's clock demoted to backstop: strength decay and a re-prompt loop (re-distill / decay / retire) decide what is served; TTL decides what is eventually retired. **Deletion** extends `forget` to the distiller's derived knowledge — every concept citing a deleted episode is re-grounded or retired ("you cannot un-distill" is the day's honest limit). **Privacy** makes every read principal-bounded and keeps *user preferences* as a **data table** — cited as data, never generalized, never cross-principal. And the curriculum's meter is finally real: M9 measures "does recall beat a fresh investigation," with **M8 as the control** — memory that changes a memory-free answer is a bug.

### Understanding

The unlock is that **Day 18 adds a *purpose*, not a subsystem: no new storage shape, no new retrieval engine — the scorer, the gate, and the loop are all known arithmetic, and the only genuinely new payment is the distiller's one chat.** The re-readings hit hardest in order: **(1) relevance is the "only if useful" meter, not a quality suggestion** — Day 7 could only refuse, Day 17 could only name the measurement, and Day 18 makes relevance a *threshold*: below it, recall returns nothing, and a concept that scores below it on real questions is a wasted chat, recorded as a miss. **(2) The distiller is safe the way Day 15's SQL was safe** — not by the model's good behavior but by a mint-time gate of code (sample size, groundedness, no-principal-names); a model that over-claims is simply *not written*. **(3) Semantic memory is the generalizing faculty, and the whole discipline is refusing to let anything else generalize** — the answer loop cites episodes; only a gate-checked distiller may mint a lesson; a receipt promoted inline is the day's cardinal sin (S8 with a search engine). **(4) Privacy is write-path and keying, not sentiment** — a preference table that keying isolates and the distiller cannot read into a lesson is privacy by construction. And the honest one: the read path stays **0 chats, 0 embeds** (the free tier Day 9 protected is preserved on the memory shelf), while the distiller bills **1 chat per batch** — the first billable shelf in the series, recorded per concept and justified only if M9's "useful?" rows say memory beats a fresh investigation often enough — Day 21's verdict.

### Implementation

- `ai_os/memory.py` (new) — `episodicize(task_record)` (builds the compact signature at run-end), `recall(question, principal, store)` = Day 8 `Retriever` over the principal's signatures + Day 9 dedup/MMR + Day 7 threshold + Day 6 top-k budget; serves episodes **as episodes** (evidence + `task_id` + provenance), refusing on empty/stale shelves.
- `ai_os/distill.py` (new) — `distill(episodes, gateway)` (one guarded chat), `validate_concept(candidate, source_episodes)` (the mint-time gates: sample-size ≥ `MEMORY_MIN_SOURCES`, `authored ⊆ grounded` set membership over the union of source evidence, no-principal-name check), `mint()` writing `kind=semantic` with full provenance through Day 17's closed write path.
- `ai_os/state.py` (extended) — `strength` and `recall_threshold` on semantic records; `recall()`/`list_for` refresh `accessed_at`; semantic expiry consulted alongside `expires_at`; `forget()` re-grounds every concept whose `source_episodes` include the deleted key.
- `ai_os/agent.py` (extended, minimally) — the answer loop gains the optional recall pre-step (with memory off or an empty store, the path is Days 12–17 unchanged — M8); preferences load as a cited `kind=preference` data table per principal.
- `ai_os/trace.py` (extended) — memory columns: `memory_kind`, `recalled (matching/below-τ/none)`, `score`, `strength`, `source_episodes`, `minted?`, `recalled_subset_authored`, `authored_subset_grounded`, `expired (clock/relevance)`, `deleted`, `principal_ok`, `memory_on_off`.
- `tools/memory_smoke.py` (new) — offline suite for M1–M9 (incl. the M8 on/off/empty-store control, hostile tiny-τ/tiny-strength configs), plus the Days 7–17 no-regression run through memory on *and* off.
- `docs/concepts/memory.md`, `docs/evaluations/memory-day18.md`.

### Failure

1. **The episode promoted (the day's cardinal sin).** Recall serves order-412's investigation and the answer absorbs its conclusion as a general truth ("checkout failures trace to payments") without the distiller. S8 with a search engine; the anti-fix is the **episode-as-citation** discipline (M7).
2. **Recency as relevance.** The most recent investigation is served *because it is recent*, and a question about an older, matching investigation gets the wrong past. M1 — the C1 ancestor, on the memory shelf.
3. **The ungrounded concept minted.** The distiller over-claims ("all checkout failures are payment failures") and the store writes it because the gate slept. `authored ⊆ grounded` is the mint-time check, and M3's rejection row is the signature.
4. **The one-run lesson.** A single failed investigation is distilled into doctrine. The sample-size rule (`≥2 agreeing episodes`) refuses it; the failure is Day 18's version of Day 13's fabrication's slow cousin — an anecdote wearing a pattern.
5. **The stale concept served.** A concept decays in relevance but is still recalled because neither its clock nor τ was consulted — fluent, confident, and grounding-free. Semantic expiry + the re-prompt loop are the guards (M4).
6. **The concept that survived its source's deletion.** `forget` drops the episode, but concepts citing it keep being recalled — "you cannot un-distill" becomes "you can't even delete." Re-grounding derived aggregates (M5) is the guard; the honest limit is about *past* answers, not future recall.
7. **Privacy as a prompt.** The distiller's prompt says "don't leak personal data" and the machine believes it. The no-principal-names *code* gate and principal-bounded keying are the guards — Day 15's "privileges beat parsers," inherited and extended (M6).
8. **Memory that changed a memory-free answer.** The store's presence nudges a fresh-investigation answer; the subsystem is no longer additive. The **M8 control** is the guard — over-memory is a bug, the S9 reflex on the memory shelf.

### Debugging

Everything Day 18 adds is `MECH` under the stub: `recall()` is the Day 8/9 arithmetic over stored signatures, the distiller's routing is a batch read + one (stub) chat + the mint gate, semantic expiry is strength-vs-τ math, `forget`'s re-grounding is set membership over `source_episodes`, and privacy is keying. The debug loop is Day 7's, re-rooted onto the memory owners:

- **Irrelevant recall** → **the scorer / τ** owns it — inspect the signature index and the fused scores, not the model's prose.
- **A concept that over-claims** → **the distiller's `authored ⊆ grounded` gate** owns it — it let an unsupported claim through, or the source union was misread.
- **A stale concept recalled** → **semantic expiry** owns it — `strength` vs the recall threshold wasn't consulted before serve.
- **A deleted episode's concept survives** → **`forget`'s derived-aggregate re-grounding** owns it (`source_episodes` wasn't walked).
- **A cross-principal read (or a leaked preference)** → **the keying** owns it — it is a Day 17 S8 failure arriving through the new read path.
- **Memory changed a memory-free answer** → **the M8 control** owns it — the addition is not additive, and the offense is the change, not the memory.

The trace says which, never the vibes; the record is the unit of attribution, and each has an owner from the day it was designed.

### Evaluation

The §9 memory table in `docs/evaluations/memory-day18.md`: per-fixture `minted/recalled`, `score/τ`, `strength`, `source_episodes`, `expired?` (clock vs relevance), `deleted?`, `privacy ok?`, and the three columns that carry the day — **`recalled ⊆ authored = ok`** on every row that recalls, **`authored ⊆ grounded = ok`** on every row that mints (M3's rejection proves the minter *can't* lie), and **the M8 row** (memory off ≡ Days 12–17, exactly as green). The numbers a future observer can carry: whether any answer was served from a memory no run authored (never), whether any concept was minted that its own evidence denied (never), whether a stale or deleted memory was ever recalled (never), whether a preference ever crossed a principal or became a lesson (never), and whether adding memory moved any of Days 7–17's numbers (it must not). The `PENDING` line, plainly: whether distilled memory makes *real* answers better — a model's grounded distillation and its recall's real usefulness — is Day 21's judgment on Day 18's subsystem, on Day 17's store.

### Architecture

The claim of the day is the series' claim, one faculty further: **the system took a new capability — carrying a lesson from one task to the next — and stayed small.** No new storage shape (both shelves are Day 17's), no new retrieval engine (the scorer is Day 8/9's, the gate Day 7's, the budget Day 6's), no new honesty contract (`recalled ⊆ authored` untouched, `authored ⊆ grounded` reconstructed from Day 14). The distiller is the one genuinely new payment — one chat per batch, gated by code — and the recall path preserves the free deterministic tier; the OFF switch (M8) proves the whole addition is optional, and the empty-shelf case proves it is *silently* optional. The seams are named and owned: whether distilled memory improves real answers is **Day 21**; (sub)agents — the next consumer of shared memory — are **Day 19** (assumed useful by no one); cross-machine/schema consolidation is named, not built; memory poisoning is **Day 25's** security seam, and the store is closed for exactly that reason. **Day 17 gave the past a home and a door; Day 18 taught the past to speak for itself — matching investigations answered for, agreeing episodes distilled under a gate, preferences kept as data, and a memory that cannot lie about its own sources or outlive its own relevance. The loop Day 12 built, the guards Day 13 hung, the compute Day 14 grounded, the privilege Day 15 relocated, the plans Day 16 wrote, and the store Day 17 closed are what made "remembering" a small change instead of a second system — and Day 19 inherits a memory its agents are allowed to disagree about, now that it actually works.**

### Questions

- Relevance scoring is Day 8/9's arithmetic over signatures today; when episodes grow, does the *episode index itself* need Day 20/21's evaluation (recall@K over past-not-fresh), or is a "did the new question use the recalled episode" probe the honest metric Day 21 can ship?
- The distiller generalizes over *episodes*, but the same mechanism could generalize over *user behavior*; is the line "no principal names survives the mint" enough to keep semantic memory from quietly becoming a user model — and is such a user model ever *legitimate* with consent (a Day 28-ish question)?
- Re-distill / decay / retire is a code decision today; when a concept keeps failing is the honest move to *surface it to the user* ("this pattern seems to no longer hold — is it?"), i.e. does semantic expiry become a Day 27 human-in-the-loop question?
- "You cannot un-distill" covers past answers; does *consolidation* (a concept that merged two episodes, one later deleted) need *re-derivation* (re-mint from survivors) or *conservative decay* (strength halved), and which keeps `authored ⊆ grounded` intact under time?
- Day 19 asks whether multiple agents are actually useful — does shared memory change that answer, or is a single agent with Day 18's memory strictly cheaper than N agents with N private stores? The curriculum says *do not assume*; Day 19 gets to measure.

### Tomorrow

Day 19 — Multi-Agent Architecture: the memory Day 18 made useful is the natural thing agents would *share*, and the curriculum is explicitly suspicious of the idea — *"determine whether multiple agents are actually useful; do not assume they are."* The comparison is **single agent vs specialized agents** (Knowledge / Data / Engineering), the deliverable is a *justified* architecture on either side, and the justification matters more than the number of agents. Day 18 hands over a store that already knows who wrote every fact and how old every lesson is; Day 19's question is whether splitting the loop, the planner, the retrieval, and the memory across roles earns its orchestration cost — or whether Day 18 just proved that *one* agent with real memory out-competes *three* with none. Day 16 was where the agent learned to *think before it acts*; Day 17 was where it learned to *remember what it did*; Day 18 was where it learned to *carry a lesson from one task to the next — and only if the lesson is worth carrying*; Day 19 is where it asks whether the lesson should be carried by **someone else**.