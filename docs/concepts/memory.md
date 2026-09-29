# Persistent Memory — Relevance as the Meter, the Distiller Under a Gate, Preferences as Data

> Day 18 of the AI-layer apprenticeship. Seventeen days built a machine that *reads*
> (6–10), *acts* (11), *drives itself* (12), *survives failure* (13), *computes*
> (14), *defends itself* (15), *thinks before acting* (16), and *remembers what it
> did* (17). But every memory until now was the agent's own work held in a closed,
> provenanced, expiring store — and Day 17 was explicit about the seam: it built the
> shelves and named the consumers (the **distiller**, the **relevance scorer**, the
> **expiry/re-prompt loop**) as **Day 18's, only if useful**. Day 18 builds them. The
> curriculum is literally titled *Persistent Memory*; its objective is the one word
> that changes everything — *add **controlled** long-term memory* — and its five
> learn items are *memory retrieval, memory relevance, expiration, deletion,
> privacy*. The deliverable is the **basic persistent memory subsystem**: a
> **relevance-scored recall** of the agent's own past (Day 8/9's retrieval turned on
> previous investigations — the *matching* one, scored, not the most recent), a
> **distiller** that turns agreeing episodes into one grounded concept under its own
> mint-time gates, **semantic expiry** with Day 17's clock demoted to a backstop,
> deletion extended to the distiller's derived knowledge, and **privacy** as a
> first-class duty — with *user preferences* honestly demoted to **a data table, not
> a memory**.
> Implementation: `ai_os/memory.py` (episodic recall), `ai_os/distill.py` (the
> distiller + gates), `ai_os/state.py` (the `strength` meter + `forget`'s
> re-grounding), `ai_os/agent.py` (the recall pre-step), `ai_os/trace.py` (the memory
> columns), `tools/memory_smoke.py` (M1–M9).

The single idea: **Day 17 said "you may only recall what you authored"; Day 18 adds
"and you may only distill what is grounded" — memory stays closed, and now it also
earns its shelf.** Recall is Day 8/9's scorer over the agent's own receipts; the
distiller is one chat whose *output* is gated by code (sample size, `authored ⊆
grounded`, no principal names); expiry is relevance-governed with the clock demoted
to a backstop; deletion reaches every concept that cites a deleted source; and the
curriculum's "only if useful" — the meter Day 17 could only *name* — becomes a
measured number with a control (**M8**) proving that memory off ≡ Days 12–17. Memory
costs its first chat today (the distiller); the recall path stays free, and both are
metered, not assumed.

## 1. Memory retrieval — Day 8/9's engine, turned on the agent's own past

Day 17 made "remember what the agent did" *true*; Day 18 makes it *useful*. Everything
the agent needs to retrieve already exists — task records, transcripts, checkpoints,
all `principal`-keyed, all carrying provenance — so the curriculum's first learn item,
**memory retrieval**, is not a new engine. It is the series' retrieval machinery,
pointed at the agent's own finished work: index the episodes, score them against a new
question, serve the ones that **match**. Day 8's `Retriever` (lexicon + fusion), Day
9's `rel()` + MMR dedup, Day 7's threshold gate, Day 6's budget floor — every engine is
reused; only the *catalogue* changes (from Saleor chunks to the principal's
investigations).

The discipline that keeps this small is the boundary Day 17 drew and Day 18 must not
blur: **episodes are recalled as episodes.** A finished investigation is a *receipt* —
returned with its `task_id`, its evidence, its conclusion, its provenance — to be
*cited* like any Day 6 source, never to be re-worded, merged, or generalized by the
answer loop. Generalization is the distiller's job (§5) and only its. The moment the
recall path lets a receipt read as a lesson ("order 412 failed at checkout, therefore
checkout failures trace to payments") it has crossed S8's boundary with a search engine
on top — the retrieval version of Day 9's D2 leak.

Day 18 is also the day retrieval learns it is not the whole story. The catalogue is
small by construction (sized, expiring, principal-keyed), so `recall()` is the **free
deterministic tier** — the exact arithmetic Day 8/9 verified, costing **0 chats and 0
embeds**. That is not a boast, it is Day 9's ledger preserved: the memory *read* path
never bills until one opts into the `PENDING` cross-encoder, and the meter for that is
recorded when (if) it is ever paid.

## 2. Memory relevance — the "only if useful" meter, now a scorer

The curriculum's second learn item, **memory relevance**, is the fulfillment of a
pledge that has been running since Day 7: *"introduce persistent memory only if
useful."* Day 17 could only *name* that measurement — an S-fixture that demands memory
be load-bearing (S3) and a control that demands it vanish when it isn't (S9). Day 18
owns the actual number. Relevance **is** the meter: a memory is worth recalling only if
it beats the alternative — a fresh investigation — and below threshold it is **not
served**. The RAG gate Day 7 put in front of retrieval (refuse when nothing covers the
question) is reborn on the memory shelf: refuse to *recall* when nothing covers the
episode.

Two relevance judgments, kept distinct:

- **Episodic relevance** — does this *past investigation* answer this *new question*?
  Score the episode's signature (question paraphrase, tools used, entities touched,
  conclusion) against the question.
- **Semantic relevance** — does this *distilled concept* explain this *new question*?
  Score the concept's claim against the question.

Both feed one recall path; both share one refusal rule. The scoring choice wires in the
day's honest architecture: memory serves the loop the way retrieval served the RAG
answer — it improves the answer's *starter set*, never its honesty contract.

The failure shapes relevance exists to prevent are Day 9's, re-described on the memory
shelf:

- **Irrelevant recall** — the most recent investigation served *because* it is recent
  (the ancestor of C1's refund-doc trap: recency dressed as relevance).
- **Half-relevant recall** — a concept that matches one term of a two-intent question
  and is served whole (Day 8's decomposition problem, on the memory shelf).
- **Confidently wrong recall** — a *stale* concept that sounds like the answer (the
  memory-flavored version of Day 13's fabricated citation: fluent, confident, and
  grounding-free).

Each becomes a fixture. And the ordering is deliberate: **retrieval before
distillation**, because the scorer is what decides whether any distilled concept is
worth its minting budget at all — a concept recall never matches was a wasted chat.

## 3. The subsystem — two tranches, one store, four invariants

Day 17 named the seam precisely so this day's design is mostly archaeology: the
**distiller** and the **relevance-scored recall** are not new stores, they are
*consumers* of the store that already exists. The design rule is **no new storage
shape** — episodic recall reads `kind=episodic`, the distiller writes `kind=semantic`,
both into the same file-backed, schema-carrying, provenance-enforcing store; S1–S9's
writes, expiry, and deletion apply to the new kinds untouched. Day 18 adds a *purpose*,
not a schema.

Three design decisions give the day its shape:

**Decision 1 — episodic recall is a read-path function, not a feature.**
`recall(question, principal)` = retrieve over `past_investigations_of(principal)` →
top-k above τ → attach each episode's evidence + `task_id` + provenance into the
running context **as an episode**, cited like any citation. Nothing new is called; the
answer loop gains a "consult the past" needle it never had.

**Decision 2 — the distiller is a chat, gated by code.** One batch job collects N
finished task records; one model call runs under a strict contract (distill only what
the evidence supports; name the supporting episodes; generalize only across episodes
that agree; never mint from a single receipt) and produces a candidate concept:

```text
{ claim, source_episodes[], strength, ttl, created_by, schema }
```

Minted only if: **≥2 agreeing sources** (a one-episode "lesson" is an anecdote), **no
source contradicts the claim**, and the claim is general — the assertion-check of Day
14 (`asserted ⊆ returned`) reconstructed for distillation as **`authored ⊆ grounded`**.

**Decision 3 — expiration becomes relevance-governed, the clock demoted to backstop.**
Day 17's `expires_at` stays as the hard floor; Day 18 adds the strength metric. A
concept is served only while its strength (supporting episodes, recency, recall
hit-rate) clears the recall threshold; decay is *re-prompt-driven* (each served recall
refreshes `accessed_at`; a concept that stops matching decays by relevance first, then
by the clock). This is Day 17's own question from its §6 — *"is semantic expiry
(relevance + confidence instead of a clock) the real Day 18 work?"* — answered in code,
with the clock left honest as the backstop.

**Decision 4 — privacy is a data-table duty, baked into the design.** The curriculum's
*user preferences within the application* are not a memory: they are a small
`principal`-keyed data table — facts the answer loop may *cite as data*, readable only
by their principal, never distilled, never generalized into "the user always…". Privacy
covers the whole subsystem because memory is the agent's most personal surface:
principal-bounded reads on every path, no generalization from personal data,
consent-visible provenance, and first-class deletion are *design* decisions, not
features.

The day's four invariants, pre-committed:

1. **`recalled ⊆ authored`** (Day 17, untouched) — nothing recallable that the system
   did not write, with provenance.
2. **`authored ⊆ grounded`** (Day 18's headline) — a distilled claim is grounded in its
   source episodes, or it is not minted.
3. **Recall is scored and above τ, or absent** — relevance is the meter; recency and
   vibes are not.
4. **Memory off ≡ Days 12–17** (M8) — over-memory is a bug, Day 17's S9 reflex on the
   memory shelf.

## 4. Episodic recall — relevant past investigations, scored

The build has four parts, each a known reflex.

**The index.** At a run's end, the finished task record becomes `kind=episodic` with a
compact signature — the question's paraphrase, the tools used, the entities/order ids
touched, the conclusion — enough for lexical scoring, small enough to keep the shelf
sized. The signature is Day 8's lexicon-friendly corpus, minus the corpus. It is
**wired into the loop**: with a memory store, `run_agent` compacts the finished run
into an *authored* `kind=episodic` record at run-end, so the deliverable's
"finished run → episodic record" arrow is literal — memory on indexes exactly one
record, memory off writes none (M8).

**The scorer.** `recall()` = Day 8's `Retriever` over the principal's episode
signatures → Day 9's dedup/MMR to drop near-duplicate investigations → Day 7's gate →
top-k enforced by Day 6's context budget. Zero new models; the arithmetic is verified
arithmetic. The M1 fixture is the point: two past investigations, one matching the new
question — the matching one serves **above** the other, *scored*, because a recalled
memory is worthless if serving the *most recent* investigation is what "memory"
degrades into (Day 9's C1 read its own lesson).

**The citation discipline.** The recalled episode enters the answer path carrying its
evidence, its conclusion, and its provenance — a block the answer may *cite*, never
absorb as asserted fact. `cited ⊆ retrieved` holds over episode records exactly as it
held over chunks; the difference is the retrieved set is the agent's own receipts. M7
is the enforcement: no run-to-run bleed except through a distiller-minted concept.

**The honest refusal.** An empty shelf returns **nothing** — not a fabricated "past,"
not a most-recent-anyway fallback (M8's empty-store control). A stale episode acts
absent (Day 17's S5 applies to the recall path too). Retrieval refuses rather than
guesses, the Day 7 reflex, once more.

The cost column is why this is the day's *cheap* tranche: the whole recall path is
lexicon + fusion + MMR over stored signatures — **0 chats, 0 embeds**, the same ledger
Day 9 protected. The `PENDING` cross-encoder stays `PENDING`, and if it is ever paid,
the chat per candidate-pair lands in the table before it lands in the code.

## 5. Semantic memory — the distiller, episodes → grounded concepts

The distiller is where Day 18's cost honesty starts, because it is the first shelf that
**bills a chat** — one per batch, and only under code's permission.

The prompt's contract is short and strict: *distill only what the evidence supports;
name each supporting episode; generalize only across episodes that agree; never mint
from a single receipt.* The output, a candidate concept, is then gated not by the model
but by **code**, at three checkpoints:

1. **Sample size ≥ 2.** A lesson from one run is an anecdote. The distiller may *store*
   a single episode (on the episodic shelf, recallable as a receipt); it may not *mint*
   a general truth from it. This is the "receipt vs lesson" boundary made computable.
2. **`authored ⊆ grounded`.** Every claim in the candidate must be satisfiable against
   the **union of its source episodes' evidence** — each episode carries its evidence,
   so the check is set membership over that union, exactly Day 14's `asserted ⊆
   returned` reconstructed for distillation. An over-claim is **rejected at mint
   time**, never stored "softened." A claim a source episode *contradicts* is rejected
   too — a concept asserting a pattern its own evidence denies is the semantic card of
   Day 13's fabrication.
3. **No principal names.** A concept that names a customer, an order, or a payment id
   is a **privacy failure** (§7) and is rejected. Data can be the *source* of a lesson
   ("checkout failures trace to the payment"); an individual can never *be* the lesson.

What is minted carries full provenance: `created_by = {run_id, step, source:
"distiller"}`, `source_episodes = [task_ids]`, `strength = |agreeing episodes| /
|episodes considered|`, `ttl` from policy. The store accepts it **because it carries
provenance** — the distiller is only another authorized writer, and Day 17's S7
write-time check is its own tab-keep. `recalled ⊆ authored` was not weakened to admit
the distiller; the distiller *is* an author.

The curriculum's candidate list routes through this single mechanism:

| Curriculum memory | Real shelf | How it is minted |
|---|---|---|
| Previous investigations | episodic | no minting — recalled verbatim, scored, cited as an episode |
| Past successful workflows | semantic | distilled over **verified** investigations (plans that ran green to `conclusion ⊆ evidence`) |
| Known Saleor concepts | semantic | distilled the same way, with the doc corpus (Days 6–10) corroborating the claim |
| User preferences | **data table** | never minted — stored per-principal, cited as data |

The control on this list: a "known Saleor concept" can never be minted from one
customer's checkout story — because the sample-size rule wouldn't let it, and because
the concept's shelf-mates (workflows) trace to verified runs, not anecdotes.

The honest `PENDING` sails with the rest of the days: the **routing** of the distiller
(when it fires, the sample-size gate, the groundedness check, provenance minting) is
`MECH` — deterministic functions of stored episodes, offline and free. Whether a *real*
model distills a concept that is actually correct, general, and useful is **Day 21's**
benchmark on Day 18's distiller — the same day that owns the planner's quality and the
scorer's real answer.

## 6. Semantic expiry, deletion, and the re-prompt loop

Day 17 proved the **clock** (expiry consulted before every read, compaction physically
reclaiming). Day 18's learn item *expiration* is the harder half: **a fact's truth does
not live on a clock.** A fact that stays true deserves to outlive `expires_at`; a fact
that was always wrong should never have been stored — Day 17's own question, delivered
as a duty.

The answer is **semantic expiry with the clock as backstop.** Every concept carries
`strength` (supporting episodes, recency, recall hit-rate) and a recall threshold;
every `recall()` records `accessed_at` and a successful re-serving refreshes it. A
concept whose strength falls below τ — its sources expired, or recall stopped matching,
or a fresh investigation contradicted it — is **demoted below threshold: alive, but
unserved**. Then, and only then, the clock retires it. M4 is the fixture that proves
the ordering: a concept that decays in *relevance* acts absent **before** its TTL bites.

The **re-prompt loop** closes the circuit the curriculum's "expiration" implies. When a
served concept stops earning its keep — unmatched recalls over N turns, or a
contradiction (a fresh investigation's grounded evidence now denies it) — the loop
chooses, by code:

- **re-distill** — feed the *larger* episode set back to the distiller and re-mint;
- **decay** — lower strength below τ, the concept unserves itself;
- **retire** — delete.

One rule keeps this honest: a concept is **never refreshed by re-serving alone**.
Recency is not truth — the Day 9 lesson, found again on the memory shelf. A concept
that keeps being *read* is not a concept that keeps being *right*.

**Deletion** extends Day 17's first-class operation to the distiller, where the derived
aggregate now means *the concepts.* `forget(principal, key)` on an episode must
**re-ground every concept that cites it**: re-check the concept against its remaining
sources, and if it now fails the sample-size rule (it had 2 sources, one is deleted),
**retire it too**. M5 proves unreachability end-to-end. And the day states its most
honest boundary as a result, not an apology: **"you cannot un-distill."** A concept
already recalled and absorbed into a past answer cannot be un-remembered *from that
answer* — deletion means future recall ends, not that history is rewritten. That is Day
18's privacy footnote and its epistemic humility in one sentence.

## 7. Privacy — memory is personal data, preferences are a table

The curriculum's fifth learn item, *privacy*, is the easiest to skip and the hardest to
fake. Memory is the agent's **most personal surface**: it holds what users asked, what
the agent investigated about them, and their stated preferences. Day 18 treats it as
personal data of a *principal*, full stop.

Three mechanisms make that real:

1. **Every read is principal-bounded** (Day 17's `principal` keying, extended to *every*
   path — including the distiller's batch source selection, which may only read its
   owner's episodes). M6 proves a cross-principal preference or memory read returns
   **nothing**.

2. **User preferences remain a data table, not a memory.** A `kind=preference` record
   per principal — `{principal, key: "currency", value: "EUR", source: "user-stated",
   created_at, ttl}` — is served to its own answer loop as **cited data** and nothing
   else. It is never distilled, never generalized, never merged across principals. Day
   17 tagged this by analysis ("a preference is a data table, never a lesson"); Day 18
   codes it. The moment the agent says "the user always pays in EUR" it has turned a
   datum into a doctrine — the privacy failure that looks like helpfulness.

3. **The distiller's wall is a mint-time check.** Distillation runs over task records
   the runs authored; a record that contains personal data (an order, a customer id —
   Day 14/15's data surface) is **seed, not lesson**. The distiller may form "checkout
   failures → inspect the payment" from order data; it may not form "customer X always
   fails." A concept that names a principal is rejected by the no-principal-names gate,
   which is not a prompt sentiment but a code check on the candidate parse.

Two closing duties keep it auditable: **deletion as a right** (`forget` on a preference
is the same aggregates-included operation — M5), and **inspectability as the privacy
answer** — `list_for(principal, *)` can show a principal everything the subsystem holds
about *them*, and nothing about anyone else (Day 17's inspectable store, now the answer
to "what do you know about me?").

The tension is recorded Day 15's way: **privacy is enforced by the write path and the
keying, not by the prompt.** The distiller is a model; its output is gated by code
(groundedness, sample-size, no-principal-names), exactly as Day 15 gated the SQL by role
rather than by asking nicely. When the store is the attacker's target (Day 25's
security), the *closed store* — not a sanitizer's hope — is what makes the injected
memory unreachable.

## What this is not

Stated plainly, because a green memory table implies more than it proves:

- **The distiller's quality is named, not measured.** The stub proves the *subsystem*
  (recall, the mint gates, expiry, deletion, privacy); whether a **real** model distills
  a concept that is correct, general, and grounded is **Day 21's** benchmark. A
  green M2 row proves the *gate*, not the *model*.
- **"Only if useful" is a mechanism, not a verdict.** M9 measures that recall is free
  and can beat a fresh investigation's cost; whether recalled memory makes *real*
  answers better is a **Day 21** question, not a fixture's.
- **The episode scorer is lexical.** `recall()` fuses the semantic half at **0** — no
  embeddings — to keep the read path free. The `PENDING` cross-encoder
  (`ai_os/rerank.py`) would be a chat per candidate-pair and is **named, not paid**.
- **Cross-machine / cross-schema consolidation is named, not built.** The store is
  single-writer; syncing memories between agents or machines is a recorded seam.
- **"You cannot un-distill" is an honest limit, not a hidden failure.** A concept
  already absorbed into a past answer cannot be un-remembered from that answer; deletion
  ends *future* recall. That boundary is stated as a duty, not an apology.
- **Memory *poisoning* is Day 25's.** Using the store as an injection vector is the
  reason the store is **closed** and provenanced today — the guarantee is the write
  path, not a sanitizer.
- **`recalled ⊆ authored` is provenance-shaped.** It proves *who wrote* a record, not
  that it is *true*; grounding the distilled claim is Day 18's `authored ⊆ grounded`,
  and adjudicating the truth is Day 21's.

## Config (env-overridable)

| Key | Default | Meaning |
|---|---|---|
| `MEMORY_ENABLED` | `1` | the memory subsystem's switch; `0`/`off` → recall serves nothing (M8) |
| `MEMORY_RECALL_TOP_K` | `5` | the served set's size (Day 6's budget floor) |
| `MEMORY_RECALL_TAU` | `0.05` | Day 7's gate, on the memory shelf — below it, absent |
| `MEMORY_MIN_SOURCES` | `2` | the distiller's sample-size rule (a receipt is not a lesson) |
| `MEMORY_DISTILL_TTL` | `604800` (7d) | a concept's clock backstop (the relevance meter is the policy) |
| `MEMORY_STRENGTH_DECAY` | `0.5` | the re-prompt loop's decay step (strength × (1 − rate)) |

`MEMORY_RECALL_ALPHA`, `MEMORY_MMR_LAMBDA`, `MEMORY_DEDUP_TAU` are module constants
(the read path is lexical-only: `alpha = 0.0`, so **0 chats, 0 embeds**).

## Negatives (honest, with owners)

1. **Real-model distillation quality** — does a real model mint a concept that is
   correct, general, and grounded? Owner: **Day 21's** benchmark on Day 18's distiller.
2. **Real "only if useful" verdict** — does recall beat a fresh investigation on a real
   question? M9 measures the *cost* half offline; the *quality* half is **Day 21's**.
3. **`PENDING` cross-encoder relevance** — the paid (chat-per-candidate) semantic
   scorer is `PENDING`; the read path stays free until it is knowingly paid.
4. **Cross-machine / cross-schema consolidation** — single-writer store, no sync seam.
5. **Memory poisoning** — an injection through the store is **Day 25's** security seam;
   the closed write path is why it is unreachable today.
6. **"You cannot un-distill"** — deletion ends future recall, not the past answers a
   concept was absorbed into. Stated, with an owner.

## Re-verify, in one line

```bash
python tools/memory_smoke.py    # M1–M9 + the Days 7–17 no-regression re-run (memory on and off)
```

**`recalled ⊆ authored = ok` on every row that recalls** and **`authored ⊆ grounded =
ok` on every row that mints** — with the **M8** control holding the line: memory off ≡
Days 12–17. See `docs/evaluations/memory-day18.md` for the §9 memory table.
