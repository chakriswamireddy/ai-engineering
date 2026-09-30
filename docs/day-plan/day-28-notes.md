# Day 28 Notes — Production Architecture

> Notes answering `docs/day-plan/day-28.md`.
> Twenty-seven days built a machine that reads (Days 6–10), acts (11–12), fails honestly (13), computes (14–15), plans (16), remembers (17–18), splits into specialists (19), learns how it will be judged (20–22), explains one request from the inside (23) and across its boundaries (24), learned to be attacked (25), learned who may open a door (26), and yesterday learned to stop and wait for a person (27). The curriculum now hands the day a review list rather than a build list: **Docker, queues, caching, rate limiting, async workers, secrets, model fallback, retries, failure recovery, database scaling, vector storage, object storage**, with the instruction *"map the local system to a production architecture"* and the deliverable **a production architecture diagram** — the first day in the series whose output is not a module. The finding that reorganizes the whole day is not about any of those twelve items. It is that **there is one process, and no code anywhere models the existence of a second one.** Not "is not containerized" — *does not exist as a concept*: a restart is simultaneously a total outage and a total amnesia, a scale-out is a correctness bug rather than a performance one, and a "retry" is a `time.sleep` inside a worker thread. So the day's real work is not a box list. It is to find which of the twelve items have an honest answer, which have a **broken** one, and which three are the only ones that can be answered truthfully today, and to notice that the broken ones are all the same bug wearing different hats: **a bound with no consequence, a store with no identity, and a cost with no failure term.**
>
> The single idea: **a production architecture is a set of claims about failure, not a set of boxes** — and the only property that makes a diagram more than a mood is that it is *falsifiable against the repository it describes*. Every edge gets a label from a closed vocabulary (`fails-loud`, `fails-quiet`, `fails-stale`, `fails-lost`, `fails-corrupt`, `fails-slow`, `duplicates`, `drops`), every label is either a mechanism that exists here or a `PENDING` with an owner, every box is a `file:line` that a fixture reads rather than trusts, and the day's deliverable therefore ships with a test suite — the first time in twenty-eight days that the *document* is the thing under test. Three rules make the day specific to this codebase. **A bound is a tuple, and four of the five parts are optional today** — `on`, `unit`, `rate`, `burst`, `consequence` — because every bound in the repository is of the form "one run may do at most N" (`AGENT_MAX_STEPS = 6`, `AGENT_TOOL_TIMEOUT_S = 30.0`, `AGENT_BUDGET_USD`, `APPROVAL_MAX_PER_RUN = 1`, `STATE_MAX_RECORDS = 1000`, `LLM_MAX_ATTEMPTS = 3`) and **not one of them is keyed on a principal over a window**, so a limit nobody can be refused by is a comment with a number in it. **A cache here is a correctness decision, not a performance one** — Day 20 pinned golden rows by content hash and made `judged ⊆ pre-registered` an invariant, Day 25 extended the pin to the evidence and made a changed-evidence row `unresolvable` rather than `wrong`, so a cache whose key is not the store's identity is a way to serve yesterday's evidence and call it today's answer, which makes the rule `sha256(file) + embedding_model + dimension` and the honest consequence that **a rebuilt-but-equal store is a cache miss**, because a content hash cannot prove equality without reading the file. And **the retries that already exist are correct, and that is exactly why admission control is not optional**: `gateway.py:38–48` honors the server's own `Retry-After` before applying exponential backoff with full jitter, which under load is a **load multiplier applied after admission** — bounded at `providers × max_attempts` ≈ 6, but multiplied on top of exactly the load admission control was supposed to prevent, and recorded at `$0.00` while it does it, because `ledger.py:99` computes `cost_usd = self.cost(model, usage) if ok else 0.0`.
>
> **The one line the day would die defending: a production architecture is not a picture of what you built, it is a list of what breaks first, in order, with the trigger that makes it break — and this repository already knows every one of those answers, has simply never written them down, which is why the single most useful artifact of Day 28 is not a box or a module but a census: 60 configuration names the code reads, 21 that a reader can find, and 39 that are invisible.**

---

## 0. Verification Status of This Document

This file is written **before** the day's implementation. Two kinds of sentence appear below and they are never mixed:

- **Verified inventory** — every statement about what the repository *is today*. Each one was checked by reading the cited line, and the citations in this file are mechanically validated: every `file:line` exists, the line is non-blank, and the line is inside the file. Section 9's method is reproducible.
- **Pre-registered** — every expectation about the day's own new artifacts (`capacity.py`, the cache, the census, the checker, N1–N9). These are written down *first*, on purpose, and are marked `PENDING` because nothing has been built and nothing has been run. They are not results. A reader who finds a number in §10 that is not traceable to a `Verified` sentence above is looking at an expectation, and the file says so.

One correction is recorded here rather than silently absorbed, because it is the failure mode the day is about: **Day 27's notes recorded a "verified defect" that does not exist.** It claimed `RoleAgent.run` (`agent.py:1208–1210`) passes `routed_to=` and `role=` to a `run_agent` whose signature accepts neither, raising `TypeError` at call time. It does not: `run_agent`'s signature takes `routed_to` and `role` (`agent.py:550–551`), and `tools/architecture_smoke.py` exercises `RoleAgent.run` on its G4 row and passes end to end. The plan for today repeated the claim uncritically; it is retracted here. The *lesson* it carried is still true and is kept — a second entry point must not pass a keyword the callee does not accept — but a document that inherits a refuted "verified" finding without re-checking it has acquired the first lie it was written to prevent, and that is the single strongest argument for the day's ratchet.

---

## 1. The Loaded Question — "Understand How the System Becomes Production-Ready"

The question sounds like a survey and is not one. "How does this system become production-ready" has a hidden premise — that production-readiness is a place on a spectrum this system is currently somewhere short of — and the honest answer to a survey is a list of missing boxes, and a list of missing boxes is the one artifact that cannot be wrong and therefore teaches nothing.

The real question is: **what is the unit of failure in this system, and what does that unit make impossible?** That question has an answer that is checkable in an afternoon, and the answer is *the process*, and the things it makes impossible are not "scale" and "performance" but a specific and enumerable list:

| # | The unit of failure makes this impossible | Where it lives | Verified |
|---|------------------------------------------|----------------|----------|
| 1 | Serving a second request while one is in flight | `server.py:114–116` reads `last_provider`/`last_latency_ms` off a module-level gateway global built at `server.py:33–37`, written at `gateway.py:197–199` | yes |
| 2 | Streaming without blocking the loop | `server.py:123–152`: `async def` at `:124` driving a **synchronous** generator `events()` at `:137` | yes |
| 3 | Surviving a restart with its memory | `ledger.py:65` keeps `self.records` in a per-process list, unbounded | yes |
| 4 | Reading state without writing it | `state.py:592–608`, `get()` calls `_maybe_save()` at `:607` with default `autosave=True` | yes |
| 5 | A trace anyone can fetch | `run_rag` returns `result["trace_id"]` (`rag.py:266`) from `obs.start_trace` (`rag.py:248`); `observability.py` has no sink, no exporter, no `to_dict` in its 62 functions | yes |
| 6 | Knowing a store was already loaded | `_rag_store()` (`server.py:177–183`) calls `RagStore.load` per request; the call site is `server.py:198` | yes |
| 7 | Being wrong *loudly* about a missing database | `data.py:454` is `sqlite3.connect(":memory:")` seeded with `SEED_SQL`, substituted with no warning when `SALEOR_READONLY_DB_URL` is unset — while the RAG path **does** fail loud at `server.py:202–204` | yes |
| 8 | Being charged for what it spent | `ledger.py:99`, `cost_usd = self.cost(model, usage) if ok else 0.0`, with failures recorded carrying no token counts (`gateway.py:177–180`) | yes |
| 9 | Being scaled without a migration | `data.py:528` is one `psycopg.connect` per store instance, `default_transaction_read_only`, rollback at `549`/`566`/`576` — the store is not too big, it is *not shared* | yes |
| 10 | Being configured from its own documentation | **39 of the 60 environment names the code reads are absent from `.env.example`** (see §5) | yes |

Seven of these ten are not performance problems. Six of them are correctness problems that only appear when there is a second writer, a second reader, or a restart, and one of them (#7) is a correctness problem that appears **right now, in a single process, with no concurrency at all** — which is why the day's residual register leads with it rather than with the concurrency ceiling. A list of missing *features* would have ranked Docker first. A list of *claims* ranks a stub database that answers a customer question from a fixture first, which is the correct order, because that is the one that produces a confidently wrong answer instead of an outage.

The other thing the loaded question assumes — that the system is "not yet production" — is false in a specific and worth-stating way. The system has a provenance-checked state store with TTL and bounded capacity, a hash-chained audit log, a permission layer with an admit-list, a cost ledger with a real cost function, dimension-locked retrieval, and an answer format that refuses unsupported claims. Those are production-shaped. They are also *all* per-process properties of one address space. The synthesis the day reaches is one line: **this repository has been production-shaped for twenty-eight days in the places that matter and single-process-shaped in the places that decide**, so the correct first production change is not "go multi-region" but "make three files safe for a second writer."

---

## 2. A Diagram Is a Set of Claims About Failure, Not a Set of Boxes

Every architecture diagram in this genre is drawn as components and connections, and the connections are the part nobody checks, so the result is an inventory with arrows. A box list is unfalsifiable by construction: you cannot fail a box for being wrong about a box, and "we have none of these" is the sentence a box list invites and the one this repository has to refuse.

So the day's artifact is an **edge-labelled diagram**, and the label is the whole content. Every edge answers exactly one question — *if this link is severed, what does the user see?* — and the answer must come from a **closed vocabulary** of eight labels, because the moment a diagram is allowed prose on an arrow it will contain "under load, performance may degrade":

| label | means | an instance in this repo, verified |
|-------|-------|----------------------------------|
| `fails-loud` | a structured error naming what is missing | `server.py:202–204`, the RAG path with no store file |
| `fails-quiet` | a wrong or fabricated answer, no error | `data.py:454`, `:memory:` + `SEED_SQL` answering a customer question |
| `fails-stale` | correct-shaped data from an old version of the truth | any cache not keyed on the store's identity — the thing today does not have |
| `fails-lost` | correct data, no way to retrieve it | `rag.py:266` returns a `trace_id` for a record with no sink |
| `fails-corrupt` | wrong data that looks right | two writers to `state.json`, tmp + `os.replace`, no lock (`state.py:838–845`) |
| `fails-slow` | works, too late | `server.py:123–152`, a sync generator on the event loop |
| `duplicates` | work done twice | `--workers 4` producing four gateways and four ledgers |
| `drops` | work lost | last-writer-wins on the state file; ledger amnesia on restart |

The labels are not evenly distributed, and the distribution is the useful part. **Four are live in a single process right now** — `fails-loud` (`server.py:202–204`), `fails-quiet` (`data.py:454`), `fails-lost` (`rag.py:266`), `fails-slow` (`server.py:123–152`). **Three activate at the first trigger**, which is precisely why Plate II exists: `fails-corrupt` and `drops` need a second writer on a lock-free `state.json`, and `duplicates` needs `--workers 4` to turn one module-level gateway (`server.py:33–37`) and one per-process ledger (`ledger.py:65`) into four of each — the *mechanisms* are verified, the *occurrence* is conditional. And one — `fails-stale` — **does not exist yet at all**, because the day has no cache. That last one is the label worth pausing on: the diagram's most dangerous category is the one the system has not earned, and the cheapest way to earn it is precisely the mistake the day is refusing to make.

**A label outside the vocabulary is an unfinished claim.** Not a wrong claim — an unfinished one. `fails-gracefully` is not a label, it is a hope. And the vocabulary is deliberately small enough that the day can assert every arrow against it, which is what makes N7 a bijection rather than a coverage percentage.

Two consequences follow, and both are decisions rather than observations. **Plate II is a separate plate**, because a diagram showing what *is* has no room to say what *breaks in what order*, and the order is the entire argument: at the first trigger, `state.json` breaks first (last-writer-wins, `state.py:838–845`), then the ledger (N ledgers, N amnesias, `ledger.py:65`), then the hash chain and the approval records (two appenders, and `seq` stops meaning anything). Everything else in the diagram — the corpora, the store *contents*, `policy.yaml`, `permissions.yaml` — is already safe to share because it is read-only after ingest. **So the migration order is: make the state store a database, then make the ledger durable, then make the chain append-only with a real lock**, and the day draws that order rather than drawing a cluster. And the **trigger column is mandatory**, because a diagram with a status but no trigger is a backlog: the five triggers are conditions, not dates — *a request that must be served while another is in flight* (→ the process splits), *corpus × 10* (→ retrieval), *a corpus refresh slower than the deploy* (→ object storage), *a second provider configured* (→ fallback grading), *an authenticated caller* (→ the limiter attaches). The day refuses to write a roadmap, because a roadmap is a list of hopes with quarters attached, and this series has never had a quarter.

---

## 3. Queues, Async Workers, and Rate Limiting — Three Different Bounds

The curriculum lists these three together and that grouping is the single most common source of bad production designs, because they answer three different questions and a system that answers one of them has drawn a box labelled "async" and stopped.

**A queue** answers *what happens to this unit of work while nobody is working on it*. It must be durable, it must have a depth, and it must be possible to ask how deep it is without draining it. **An async worker** answers *who drains that queue, and what happens if the drainer dies* — it must stop without losing work, and the work must be safe to hand to a second drainer. **A rate limit** answers *how fast may any single caller ask* — and it must be keyed on something the system can *authenticate*, which is the trap, because the only key available today is a caller-supplied `principal` string, which Day 26 established is not an identity and Day 27 established can approve an irreversible action.

Where does each one stand in this repository, verified?

- **Queue** — a record with no consumer and no depth bound. Day 27's `approval.py` has `requested` records that a person is supposed to clear, and nothing in the code counts them. So the queue *exists as data* and does not exist as a *mechanism*: a pending decision is indistinguishable from a forgotten one, and there is no bound on how many can accumulate.
- **Async worker** — absent, completely. No worker, no scheduler, no broker. `requirements.txt` holds exactly four dependencies (`fastapi`, `uvicorn`, `pyyaml`, `psycopg`) and not one of them is a queue. The nearest thing in the codebase is a synchronous generator on the event loop (`server.py:137`), which is the *opposite* of a worker: it makes one slow provider stall `/health` and every other request, and the honest label for that edge is `fails-slow`, not "async."
- **Rate limit** — absent, and this is the item where absence is a *design* statement rather than an oversight. Nothing anywhere is keyed on a principal over a window. The repository has about twenty bounds (`AGENT_MAX_STEPS = 6` at `agent.py:100–108`, `AGENT_TOOL_TIMEOUT_S = 30.0`, `AGENT_BUDGET_USD`, `AGENT_MAX_REPLANS`, Day 27's `APPROVAL_MAX_PER_RUN = 1`, `STATE_MAX_RECORDS = 1000`, `LLM_MAX_ATTEMPTS = 3`) and every one is per-run or per-call. A per-run constant is not a rate limit: it says "one run may do at most N," which is a property of the *work*, not a property of the *caller*, and it resets to infinity the moment the caller starts a new run.

**Why the existing retries make admission control mandatory, not optional.** The retry policy here is good work from Day 4 and the day says so without qualification: `_backoff` (`gateway.py:38–48`) honors the server's own `Retry-After` before applying exponential backoff, and the jitter is full rather than equal (`random.uniform(0.5, 1.5)`), which is the correct choice and the one people usually get wrong. `gateway.py:151–152` walks an ordered provider chain with `max_attempts` per provider, and `LLM_TOTAL_BUDGET_S` bounds the whole chain when set. Under no load that is a good policy.

Under load it is a **load multiplier**, and the multiplication happens in the wrong order. A provider returning 429 with `Retry-After: 30` keeps N concurrent callers asleep for 30 s each, and the next wave arrives on top of them. The multiplier is bounded — `providers × max_attempts` ≈ 6 with the shipped chain — but it is applied *after* the request is admitted, so it multiplies precisely the load that admission control was supposed to prevent. Therefore the ordering is fixed: **admission control before retries, not after**, and the limiter goes in the position Day 26 put `check_call` — before the work, at the boundary, keyed on the caller. A limit applied after the work is an audit, and an audit is a report about an outage you have already had.

Worse, the ledger cannot see the multiplier happen. `ledger.py:99` computes `cost_usd = self.cost(model, usage) if ok else 0.0` and `gateway.py:177–180` records a failed attempt with no token counts, so **a timed-out or 429'd attempt costs real money at the provider and `$0.00` in the ledger** — and the ledger is precisely the one number Day 4 existed to build, so the dashboard that would show a retry storm is structurally blind to the half of the storm that costs money. The day **records** this and does **not** fix it, and the reason is worth stating because it is a constraint rather than an excuse: changing failure cost from `$0.00` to a real number would move every pinned cost figure in Days 7, 20, 21 and 22, and a series whose entire credibility rests on `judged ⊆ pre-registered` by content hash cannot invalidate its own baselines to make one number look better. It is `PENDING` with an owner.

**The shape of a correct limiter here is a tuple of five named parts**, and the day refuses to ship a limiter that answers four:

- **`on`** — a key the system can attribute. Today that is `principal`, marked *unattributable in fact and usable in practice*, which is the same two-word verdict Day 26 used for the permission layer. The consequence is not abstract: the limiter is therefore **built but not attached**, with the attachment point named in the diagram as Day 29, because a rate limit is only as trustworthy as the key it counts, and the key here is a header a caller chooses. That is Day 27's refusal to put an approval route on the surface, one level down, and the fact that the same shape appears twice is evidence it is the repository's pattern rather than a coincidence.
- **`unit`** — a budget with named units. Requests, or input tokens, or *dollars*. This repository already has the last one: `AGENT_BUDGET_USD` and the ledger's cost function, so "cost" is the unit the series has always measured in, and a limit denominated in the unit the ledger already prices is a limit the ledger can later be asked about.
- **`rate` and `burst`, as two separate numbers** — a token bucket is the right structure precisely because a cheap-but-bursty caller and an expensive-but-slow one need the same shape and different numbers, and a single "N per minute" constant forces you to pick one and lie about the other.
- **`consequence`** — a *refusal with a code and a `retry_after`*, in the shape of Day 15's `ProviderError` taxonomy and the existing 429 mapping at `server.py:58–68`, so the day adds no new HTTP concept; it adds one error kind to an error taxonomy that already knows how to render one.

A limit missing `on` or `consequence` is a `ValueError` at load time naming the bound — and that refusal is the mechanism, not a validation nicety, because **a limit nobody can be refused by is a comment with a number in it**. This is Day 26's refusal in a new venue, and saying so is the point: the series keeps arriving at the same shape from different directions, and the shape is "a decision is a value with a consequence, not a number in a docstring."

One honest wrinkle the day states rather than hides. The dollar-denominated bound is denominated in a unit that is only *estimated* before the call: a caller with a large prompt and a small `rate` can overshoot within a single call, because the pre-flight cost is an estimate. The handling is to **debit on completion and refuse on arrival**, which makes the bound approximate in the direction of safety, and the day says "approximate" instead of claiming a precision it does not have. A limit that is honest about its own error is more useful than one that is exactly wrong.

---

## 4. Caching, Model Fallback, and Retries — the Three That Change Answers

Eleven of the twelve review items, if implemented badly, produce an outage. These three, implemented badly, produce an **answer that looks right**, and that is why the day treats them as one section even though the curriculum separates them.

**Caching is the correctness item.** The argument is not general best practice; it is the series' own invariant, arrived at twice. Day 20 pinned every golden row by content hash and made `judged ⊆ pre-registered` an invariant a hash enforces. Day 25 extended the pin from the answer to the *evidence* the answer resolves against, and made a row whose evidence hash changed `unresolvable` rather than `wrong`, because comparing a new answer against an old expectation is a category error. A cache whose key is not the store's identity reintroduces exactly that category error one layer down and **silently**: the answer is computed from *yesterday's* store and graded against *today's* expectation, and the harness reports a pass. Nothing in the series would catch it, because every existing invariant is satisfied.

So the rule is not "cache the store." It is: **the cache key is the store's identity, and identity is `sha256(file) + embedding_model + dimension`** — the two locked fields are already enforced on load (`rag.py:95–124`) and the version is already checked (`rag.py:25`, `STORE_VERSION = 1`, refusing a mismatched store), so the day is not inventing a new discipline, it is reading the discipline the retrieval layer has had since Day 5 and applying it to the one thing that was never keyed on it. And there is a consequence worth stating because it is counter-intuitive: **a store that cannot produce its identity is not cacheable**, and a *rebuilt-but-equal* store is therefore a **cache miss** — a content hash cannot tell you it is equal without reading the file, and reading the file is the thing being avoided. The trade is real and the day prices it: one hash per process start (~0.2 s at 262,182,307 bytes), then zero parses per request. That is the entire win, and it is a win of 262 MB per request, not 5%.

What the cache is **not** allowed to be, stated here because the temptation is specific to this repository: it does not cache *embeddings*. A cached embedding of a repeated question is a cache whose key is a 200-character string a model wrote, and Day 25's laundering rule applies to cache keys exactly as it applies to prompts. A cache that launders a model-produced string into an identity is the retrieval-layer version of the indirect-injection path the series already catalogued.

And the day's cache is honestly labelled as **step 1 and step 2 of a five-step vector migration, not a shortcut past it**: (1) stop parsing per request, (2) an in-process cache keyed on identity, (3) float32 and a memory-mapped layout, (4) an ANN index, (5) a real vector store. Steps 3–5 are named, owned, and **not started**, with the trigger *corpus × 10*, because a day that builds step 5 has thrown away the only control that makes steps 1–4 measurable. The migration is genuinely close — `RagStore.retrieve` (`rag.py:140–153`) is a full `sorted(dot(q, c.vector) … for c in self.chunks)` over 11,061 vectors, so an order-of-magnitude corpus increase hits retrieval before it hits anything else — and "genuinely close" is exactly the state in which a series that has spent thirteen days refusing to skip steps is most likely to skip one.

**Model fallback is a measurement event, and today it is silent.** `config.py:41–51` reads one global ordered chain from `LLM_PROVIDERS` with no per-tenant and no per-cost policy. `gateway.py:104` already puts `fallback_depth` on every attempt span — and `fallback_depth` appears in exactly two places in this repository, both inside `gateway.py`, and nowhere in the eval harness and nowhere in the docs. The number is recorded, never consumed, and that produces a specific and undetected error: **a golden verdict can today be issued on an answer produced by a different model than the one the row was pinned against.** The day closes that hole with the word Day 25 already chose — `unresolvable`, not `wrong` — because a run whose `fallback_depth > 0` was not the run the pin described, and grading it as `wrong` would be as much a category error as grading it `pass`. The rule is one pre-check in the bench's judge path, no model call, no new lane, no semantic grade.

It belongs on this day rather than on Day 25 because it is a *deployment-topology* concern: a single-provider deployment has `fallback_depth = 0` always, so the rule is invisible until somebody configures a second provider, which is a Day 29 event. The metadata-safe default is unchanged and restated: **the answering model and the depth are facts about the deployment and may be promoted; the prompt and the arguments may not.**

**Retries are the load multiplier** and are covered in §3 because the three words — cache, fallback, retry — share a shape: each one is a place where the system can *substitute a different thing* for the thing that was asked for, and in each case the substitution is invisible unless something downstream records what was actually used. The cache substitutes yesterday's store; the fallback substitutes another model; the retry substitutes another attempt. So the day's single unifying question for all three is: **what does the response have to carry so that a reader can tell which one actually answered?** For the cache that is the store's identity; for the fallback that is `fallback_depth` and the answering model; for the retry that is the attempt count, which `gateway.py:151–152` already records and `server.py:114–116` already returns — and which is the one place the repository already gets this right, which is worth saying out loud on a day whose subject is a document nobody checks.

---

## 5. Design: the Bound Tuple, the Two Plates, and the Ratchet

### 5.1 The bound is a tuple, and today's twenty bounds are four-fifths of one

The record the day designs is small enough to write in one file, and the shape is the design:

```yaml
# capacity.yaml — a bound is a TUPLE, and all five parts are required.
bounds:
  requests_per_minute:
    on: principal          # a key the system can attribute — today: a header
    unit: requests
    rate: 20.0             # sustained refill
    burst: 5               # bucket depth, separate from rate on purpose
    consequence: rate_limited        # → 429 with retry_after
  spend_per_hour_usd:
    on: principal
    unit: usd              # the ledger's own unit, not a request count
    rate: 0.50
    burst: 0.25
    consequence: budget_exhausted
  approval_queue_depth:
    on: principal
    unit: pending_records
    rate: 0                # not a rate at all — a depth, declared as such
    burst: 3
    consequence: queue_depth_exceeded
  in_flight_runs:
    on: process
    unit: concurrent_runs
    rate: 0
    burst: 1               # the truth about this system today
    consequence: server_busy
```

The last entry is the one a reader should stop on. `in_flight_runs: 1` is not a chosen limit; it is a **measured fact about today**, asserted as a fact with a comment recording that the day is not claiming it is correct. A reader who learns from one line of YAML that this system can serve exactly one request at a time has learned more about its production readiness than any paragraph of prose, and the fixture asserts it *as observed* so that the day where it changes is the day the assertion fires.

The token bucket is arithmetic over an **injected clock** — refill is `min(burst, tokens + rate × max(0, now − last))` — which is why the day costs zero seconds of wall time and zero chats, and why N1–N4 are `MECH` at all rather than observations about timing. One property gets a unit assertion of its own because it is the bug the naive implementation ships: **a refusal debits nothing.** A partial debit would make a refusal reduce the caller's own budget, which is a way to make a limit look effective while being trivially bypassed by repeated attempts — the caller loses budget by trying and is thereby *rewarded* for trying. Similarly, a `consume` larger than `burst` is refused outright rather than partially satisfied, and `retry_after` is exactly `(cost − tokens) / rate` so a caller that obeys the number it is handed is guaranteed to be admitted.

### 5.2 Two plates, one table, one trigger column

Plate I answers *what is*, in one address space, every box a `file:line`. The verified inventory behind it: the FastAPI app at `server.py:40`; the gateway and its ledger built once at import (`server.py:33–37`, `ledger.py:65`); the RAG store when loaded (`rag.py:95`); the state store and every approval record in it (`state.py:493`); the loop with its six-step and 30-second bounds (`agent.py:100–108`); the trace (`observability.py`). Four things vanish at a restart and are named as such: **the ledger, the trace, the gateway's `last_*`, and the parsed store.** Everything else survives because it was written to disk — which is the *good* half of the day's inventory, and the reason the diagram does not read as a horror story.

Plate II answers *what breaks, in order, at the first trigger* (§2). The twelve-item table carries status, where, owner, trigger. The status column is drawn from a vocabulary that cannot be flattered: `exists-and-good`, `exists-and-broken`, `record-without-consumer`, `absent-by-refusal`. Nine of the twelve have *some* answer; four of those nine are broken; three have none at all, and the three with no answer are the three with the most honest one available.

### 5.3 The ratchet — why the deliverable gets a test suite

A diagram drifts toward optimism, because optimism is the direction in which a document is easier to approve. The series has made two things falsifiable before: Day 20 made the *judgment* falsifiable (`judged ⊆ pre-registered`, enforced by a hash pin), and Day 19 made the *architecture comparison* falsifiable (G1–G9 as a contract with a winner named per row). Day 28 makes the **architecture itself** falsifiable, with three assertions:

- **`N-diagram`** — every component id resolves to a file that exists, and every file the diagram names is one the code actually uses. Both directions, because a diagram with a phantom box and a diagram missing a box are the same defect seen from opposite ends.
- **`N-claims`** — every failure claim on every arrow is a fixture id, and every fixture id appears on an arrow. An unfalsifiable claim cannot enter the diagram; an unclaimed fixture cannot hide in the harness. The day is not interested in *coverage*; it is interested in the absence of unfalsifiable sentences.
- **`N-config`** — every name the code reads appears in `.env.example` or in a named exception list. Impossible to satisfy by editing the document alone, which is the property that makes it a ratchet rather than a promise.

`N-config` is the cheapest and it is the day's most quotable artifact, so it is worth being precise about how the count is produced, because the first version of this number was wrong. An AST scan of `ai_os/` for `os.getenv` / `os.environ.get` / `_env_int` / `_env_float` calls, resolving module-level string constants used as the first argument, finds **52 distinct literal names**, read across **15 files**. That scan alone is *not* the census, because `config.py:60–66` builds credential names by f-string from the provider name — `f"{provider.upper()}_API_KEY"`, `_BASE_URL`, `_EMBEDDING_MODEL` — for the three providers in `PROVIDER_REGISTRY` (`anthropic`, `cloudflare`, `openai`), which is **9 more possible names, 8 of them new** (`OPENAI_API_KEY` is already read literally). So the honest count is:

| | count | notes |
|---|---|---|
| names the code can read | **60** | 52 literal/constant-resolved + 8 provider-template expansions |
| documented in `.env.example` | **21** | of 22 active (non-comment) assignments |
| read but undocumented | **39** | including all 8 provider-template names |
| documented but read by nothing | **1** | `CMD_ZDR` — and only this one |

The `CMD_ZDR` row is the interesting one, because it is the residue of a census that is *almost* right. A literal-only scan reports **four** documented-but-unread names: `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `OPENAI_BASE_URL`, `CMD_ZDR`. Three of those four are **not** exceptions — they are exactly the f-string names the literal scan cannot see, and calling them inert would have put three false exceptions into a document whose whole claim is that it is not full of false claims. Only `CMD_ZDR`, which no file in any path reads, survives as a genuine exception. Day 27 flagged it; the day makes it a fixture rather than a footnote, because "a flag in the template that reads nothing" is the cheapest documentation bug in a repository and it had survived two days. The ratchet target is **`60 / 60 / 0` + one named exception**, and the before/after pair `60 / 21 / 39` → `60 / 60 / 0` is the smallest honest measurement the day produces: the second number is not a new mechanism, it is a documentation change that a fixture makes permanent.

### 5.4 The three things the day refuses, and where the refusal is written

A refusal nobody can find is a refusal nobody believes, so each is written at the seam where it would have gone.

1. **The limiter is not middleware.** No authentication, so the key is a header. Owner: **Day 29**, with identity and TLS.
2. **The trace gets no sink.** `observability.py` has no exporter; adding a sink is a *storage* decision — where, how long, who may read it, what happens to a trace containing argument shapes — and that belongs with identity, not with a diagram. The diagram marks the trace's exit `fails-lost` with the reason inline.
3. **No `Dockerfile`.** The deliverable is a map; Day 29 produces an image. Writing one today would be a deployment decision made inside a document day. The honest content of that box is the **list of what an image must contain** — four dependencies, two corpora, two store files, `policy.yaml`, `permissions.yaml`, `capacity.yaml`, and the secret *path* — with the count of knobs and the size of the stores as the numbers that decide whether the image is 300 MB or 4 GB.

### 5.5 What the diagram carries forward

Three claims stay visible in the diagram, not only in the report, because a production map that omitted them would describe a system the series has spent thirteen days proving cannot write: **`Wrote? = no`** (unchanged, and now with a second reason — there is still no write path, so there is nothing to scale), **`Saleor rows changed: 0`**, and **`0 chats`** for every `MECH` fixture in the series, which is the reason twenty-eight days of this were affordable at all.

---

## 6. Build: `ai_os/capacity.py` — the One Place a Bound Is Named

**Status: `PENDING` — not written, not run.** The design is §5.1; what follows is the specification the implementation must satisfy, not a report of what it does.

One file, in the register of `permissions.py` and `approval.py`: decisions, not infrastructure. `Bound(name, on, unit, rate, burst, consequence)` with `as_dict` and a load-time refusal for a missing `on` or `consequence`. `RateLimited(ValueError)` carrying `bound`, `retry_after`, and the code. `TokenBucket(rate, burst, clock=time.time)` with `consume`, `tokens`, `retry_after` — **no thread, no lock, no async**, because a limiter whose correctness depends on a lock is a limiter whose correctness depends on a deployment detail, and the diagram's Plate II would then have to draw it. `AdmissionController(bounds, clock)` with `check(principal, bound, cost=1) -> Decision`, where `Decision` is Day 26's shape (`allow`, `code`, `detail`) so the server's existing error taxonomy can render it. Plus exactly two Day 27 record helpers: `queue_depth(store, principal) -> int` and `lease(store, request_id, consumer, ttl)`, the latter refused when another consumer holds an unexpired lease.

The order inside `check` is itself the design and is the same fixed-precondition-order argument as Day 27: bound resolution → bucket refill → `consume` → a `Decision` with `code="rate_limited"`, `retry_after`, and the bound's name. **A limit applied after the work is an audit, not a limit.** And the asymmetry with the rest of the series is stated plainly: the admission decision is made by the layer, from a record, and there is **no prompt text anywhere in the path** — Day 25's L0 principle arriving in its third venue.

Explicitly **not** built, and the absence goes in the diagram's trigger column so it cannot be mistaken for an oversight: a broker, a consumer loop, a reaper thread, a priority field, a "who is waiting" dashboard, and any notion of fairness *between callers* — because fairness is a scheduling policy and the day has one consumer, so there is nothing to be fair between. N3 asserts the multi-principal property as an *observation* (per-principal counts non-zero for more than one principal) rather than claiming a scheduling guarantee the module does not make.

Determinism requirements: the only clock is the injected one, there is no `time.sleep` anywhere in the module, no I/O beyond the store handed to it, and **no model call anywhere in the module or the path that reaches it** — which is the reason the day's cost line is `0 chats, 0 embeds, 0 SQL, 0 rows` for the limiter, the cache, the diagram, and the checkers combined.

---

## 7. Build: the Identity-Keyed Cache, the Census, and the Trace Column

**Status: `PENDING` — not written, not run.** Three additive changes, and the discipline is that *none of them touches an existing invariant*.

**The cache** is one function in `ai_os/rag.py`: `load_store_cached(path, *, clock=time.time)`, keyed on `(mtime, size, sha256(file), embedding_model, dimension)`. The store itself is **not modified** — no new field, no new constructor argument, no serialization change — so Days 6/10/21's fixtures and the `data/*.json` on disk stay byte-compatible and the 262,182,307-byte file does not need rebuilding. The cache holds **one** docs store and **one** code store, matching the two corpora that actually exist, and a third is refused with a named error rather than silently evicting, because an LRU here would make "which store answered this question" a question about timing. Note the honest inventory detail: `data/code_store.json` **does not exist** on disk — only `data/code_store.prev-5domains.json` (190,543,227 bytes) — so a `corpus=code` request fails loud today at `server.py:202–204`, and the cache's second slot is a forward-looking one. That asymmetry with the data path (`data.py:454`, which substitutes silently) is one of the day's named claims.

Exactly **one** call site changes: `_rag_store()` at `server.py:177–183`. The diff is one function body, and the three numbers that justify it go in a comment at that seam: 262,182,307 bytes, 11,061 chunks, one parse per request, and the identity hash that makes the cache safe rather than stale. Nothing else in the request path changes behavior.

**The census** is `.env.example` growing from 22 active assignments to 61: the 39 missing names written in the documented style, `CAPACITY_PATH`, `RATE_LIMIT=none` (N9's first off-switch), `STORE_CACHE=0` (the second), `RATE_LIMITS` documented as the file the diagram points at, and exactly **one** exception with a reason — `CMD_ZDR`, read by no code in any file. The direction of the change is the day's argument for its own timing: 39 of 60 names — **65%** of the configuration surface — were invisible, and a diagram whose configuration surface is 65% undocumented cannot claim to be a map.

**The trace column** is one additive field on the model-attempt columns in `ai_os/trace.py`: `fallback_depth`, defaulting to `0` so every pre-Day-28 trace is unchanged, asserted by a fixture that a no-fallback run reports `0` and a hand-constructed fallback run reports `depth > 0` together with the answering model.

---

## 8. Build: the Diagram, the Checker, and the Two Reports

**Status: `PENDING` — not written, not run.**

`docs/production-architecture.md` is the deliverable, in this order and no other: **Plate I — what is**; **Plate II — what breaks, in order**; **the twelve-item table**; **the five triggers** as conditions; **the cost shape** (per-request parse, per-read state rewrite, per-attempt ledger row, and the one number that is wrong — `$0.00` for failed provider attempts); and the residual register with owners.

The checker is a **fixture, not a linter**: `tools/production_smoke.py` parses the component ids out of the fenced block, resolves each against the filesystem, asserts both directions; walks the claims table and asserts every claim id exists in the fixture registry and every registered fixture is claimed; then runs the knob census by scanning `ai_os/` and comparing against `.env.example` plus the exception list. A linter reports and exits; a fixture **fails the day's own control when the document drifts**, and that is the only thing that makes a document trustworthy six weeks from now.

`docs/evaluations/production-day28.md` carries the N1–N9 table, the load-shedding table, the census, the diagram↔repo check output, the fallback-`unresolvable` fixtures, the cost shape, and the `PENDING` lines. The report is unusual in one respect and the notes must say why: **its headline number is a count of what does not exist** — no container, no worker, no broker, no cache-before-today, no sink, no pool, no ANN index, no object store, no authentication — because that is the honest measurement of a production architecture on day 28 of a 30-day series.

---

## 9. Test: N1–N9, Pre-Registered

**Status: every row `PENDING` — not run.** Expectations were written before implementation, which is the series' standard and the reason the rest of this file is worth anything. Each row names the assertion *and* the way it is falsified, because an assertion with no falsification route is a wish.

| id | proves | assertion | falsified by |
|----|--------|-----------|--------------|
| **N1** | bucket arithmetic | refill over time; burst cap; **a refusal debits nothing**; repeated refusals do not move `tokens`; `retry_after == (cost − tokens) / rate`; `burst = 0` refuses after the instant; a `consume > burst` is refused, not partially satisfied | any observed token movement on a refused call, or a partial debit |
| **N2** | the tuple | every bound has `on`/`unit`/`rate`/`burst`/`consequence`; a missing `on` or `consequence` is a `ValueError` **naming the bound**; a `consequence` outside the refusal-code set is refused; `in_flight_runs == 1` asserted as a fact about today | a bound that loads without a consequence, or a `ValueError` that does not name its bound |
| **N3** | load shedding | three principals, `burst = 5, rate = 1/s`, fixed injected clock; accepted prefix and refused tail both printed; per-principal counts non-zero for more than one principal; every refusal carries a `retry_after`; ledger shows **0 chats, 0 embeds, 0 SQL, 0 rows** for the refused tail; control run proves the same requests are *not* free when admitted | any non-zero cost on a refused request, or a single principal taking the whole bucket and being called fair |
| **N4** | admission precedes work | a refused call reaches none of: gateway, retriever, tool registry, state store — counted at **four** seams; `RATE_LIMIT=none` restores the old path exactly | a counter above zero at any seam for a refused request |
| **N5** | configuration census | code-read names = documented + named exceptions; prints `60 / 21 / 39` before and `60 / 60 / 0` after, where 60 = 52 literal/constant-resolved + 8 provider-template expansions; the single `CMD_ZDR` exception is justified in a comment | any undocumented readable name, or any *bogus* exception — the `ANTHROPIC_API_KEY` case in §5.3 is the canonical trap |
| **N6** | the diagram is the repository | every component id resolves to an existing file; every cited `file:line` exists **and the line is inside the file, read rather than trusted**; every edge label is in the closed vocabulary; every arrow exists in the code as a call or an import | a line number past EOF, a blank line, a label outside the vocabulary, or an arrow with no corresponding call |
| **N7** | the claims are claimed | every failure claim maps to a fixture id **and** every fixture id appears on an arrow; both missing-pledge cases (claim with no fixture, fixture with no claim) are *refused* | either orphan surviving |
| **N8** | the cache is keyed on identity | the second load in one process does not re-read the file (**asserted by an `open()` counter, not a timer**); a changed `(mtime, size)` triggers re-hash and re-parse; a same-size in-place edit is detected by the content hash **on the next process start** — the boundary stated, not hidden; `STORE_CACHE=0` restores per-request parsing; the **answer to a fixed question is byte-identical with the cache on and off** | an `open()` count above one per process, or a differing answer between cache-on and cache-off |
| **N9** | the control | `RATE_LIMIT=none` and `STORE_CACHE=0` versus both on, plus the full prior regression (gateway, RAG, Days 8/9/10, T1–T7, A1–A8, F1–F9, Q1–Q8, X1–X11, P1–P9, S1–S9, M1–M9, G1–G9, O1–O9, D1–D9, I1–I9, C1–C9, H1–H9) — every one unchanged, answers byte-identical, same tool sequence, same SQL text hash, same rows, same tokens, same cents, **0 chats, 0 embeds, 0 SQL, 0 rows added**; the diagram check passes **identically** in both configurations | any prior fixture moving, or the diagram check passing only in one configuration |

N9 carries the day's most important honesty clause, and it belongs in the report **verbatim** in the same table as the pass: with both off-switches set, the store parses 262,182,307 bytes per request, so the control's "identical" row is also the **worst** configuration. A control that proves equivalence while describing the worst case, with both numbers visible in one table, is the difference between a regression test and a reassurance — which is the same distinction the series has drawn on Day 19 and Day 20 and is drawn again here for the fourth time.

The second artifact is the **single-process restart narrative**, seven rows, seven consequences, and **zero of them a crash**:

| t | event | consequence | label |
|---|-------|-------------|-------|
| 0 | `/rag` request arrives | store parses 262,182,307 bytes | `fails-slow` |
| 1 | a second request arrives | it reads `gateway.last_provider` **written by the first** (`server.py:114–116`) | `duplicates` |
| 2 | a slow stream is in flight | `/health` does not return (`server.py:123–152`) | `fails-slow` |
| 3 | a `get()` on the state store | the whole file is rewritten (`state.py:607`, `:838–845`) | `fails-slow` |
| 4 | a provider attempt times out | the ledger records `$0.00` (`ledger.py:99`) | `fails-quiet` |
| 5 | the process restarts | ledger, trace, `last_*`, parsed store all gone | `fails-lost` |
| 6 | a trace id is returned | nothing can be fetched with it (`rag.py:266`) | `fails-lost` |
| 7 | `SALEOR_READONLY_DB_URL` is unset | a customer question is answered from `SEED_SQL` (`data.py:454`) | `fails-quiet` |

The last row is the only one that produces a **confidently wrong answer** rather than an error or an outage, and that is why the residual register leads with it rather than with the concurrency ceiling. A production system that is *down* is a system whose users know; a system that answers from a fixture is a system whose users do not, and that is the failure mode a "production-ready" checklist is least likely to catch and this diagram catches first.

**MECH vs not.** The token bucket arithmetic, the tuple validation, the load-shedding table, admission-before-work, the census, the diagram↔repo check, the claim↔fixture bijection, the cache's identity key, the cache on/off byte-equality, the fallback-`unresolvable` rule, and the two off-switches are `MECH`. Whether 20 requests/minute is the right rate, whether a 262 MB parse is acceptable on the target hardware, whether an operator could clear a queue of three, whether a container is the right packaging, whether the five-step vector migration is the right order, and every claim about a second process's runtime behaviour are **not** — a green N-set proves the **map and the bounds**; it does not prove the **policy**, the **sizing**, or the **deployment**.

---

## 10. Measure: the Report, the Cost Shape, and What Is Still `PENDING`

**Status: `PENDING` — nothing measured yet.** The shape of the report is specified in §8. Two things belong here regardless, because they are true before and after the day and the day is honest about which.

**The two numbers the day changes.** *Before*: every `/rag` request paid a 262,182,307-byte `json.load`, and every state read paid a full file rewrite. *After*: a process pays one hash and one parse at start and zero thereafter, and **a state read still pays a rewrite** — unchanged, and named as unchanged rather than quietly improved. The machine cost of everything else built is **0 chats, 0 embeds, 0 SQL, 0 rows**. And the cost the series has never measured and still cannot: **operator time.** Day 27 put a human in the path; Day 28 gives that human a depth bound; neither day can say what the bound should be, and the report states the number the system does not control instead of pretending otherwise.

**The questions the day exists to answer**, each with its honest status today:

| question | answerable today? | answer |
|---|---|---|
| What runs in this process, and what is the unit of failure? | yes | one address space; the process |
| What survives a restart, and what does not? | yes | files survive; ledger, trace, `last_*`, parsed store do not |
| What happens when a store is parsed twice? | yes | two independent in-memory copies, last write wins silently |
| What happens when a provider attempt fails after it was paid for? | yes | `$0.00` in the ledger (`ledger.py:99`) |
| What is the real concurrency ceiling? | yes | `1`, asserted as an observed fact in `capacity.yaml` |
| Which of the twelve items exist, in what state, which are honest refusals? | yes | 9 of 12 have an answer, 4 of those broken, 3 absent by refusal |
| What is the *first* trigger, and which component breaks? | yes | "a request that must be served while another is in flight" → `state.json` |
| How many knobs does the configuration surface really have? | yes | 60 read, 21 documented, 39 invisible, 1 inert |
| Is the failure-cost blind spot fixable today? | **no** | `PENDING` — a cost change invalidates every pinned figure in Days 7/20/21/22 |
| Would a second process actually corrupt the state file? | **no** | `PENDING` — reasoned from the absence of a lock, deliberately not run; there is no isolation story for a two-process test on these files |
| Is 20 req/min the right rate? | **no** | `PENDING`, owner: whoever deploys this |
| Is the five-step vector migration the right order? | **no** | `PENDING`, owner: Day 29+ |

The tenth row is the one that deserves defending, because a reader may reasonably think the day should have *demonstrated* the last-writer-wins bug by running two processes. The day declines, for a reason that is a rule rather than an excuse: **a two-process fixture written against a file with no lock and no isolation would demonstrate that the test can corrupt the developer's own `state.json`**, and a demonstration that damages the repository it is measuring is not evidence. So the claim is carried as *reasoned from the absence of a lock*, at the same MECH/not-MECH boundary the series has used since Day 19, and it is drawn in the diagram as a **trigger with a named owner** rather than as an observed failure.

---

## 11. Documents

- `docs/production-architecture.md` — **the deliverable**, `PENDING`. Plate I, Plate II, the twelve-item table with the five triggers as a mandatory column, the cost shape, the eight-label closed vocabulary, the three refusals written at their seams, and the carried claims (`Wrote? = no`, `Saleor rows: 0`, `0 chats`) visible in the diagram rather than only in a report.
- `docs/evaluations/production-day28.md` — **the report**, `PENDING`. The N1–N9 table with the "effect that did not happen" column carried from Day 26; the load-shedding table; the seven-row restart narrative; the census before and after (`60 / 21 / 39` → `60 / 60 / 0` + the one `CMD_ZDR` exception); the diagram↔repo check output; and the `PENDING` lines with owners.
- `ai_os/capacity.py` — `PENDING`. The bound tuple, the token bucket, the admission controller, the two record helpers.
- `capacity.yaml` — `PENDING`. Five bounds, one comment per bound naming the existing constant it generalizes.
- `tools/production_smoke.py` — `PENDING`. The checker, as a fixture.
- `.env.example` — `PENDING`. 22 → 61 active lines; the 39 missing names; `RATE_LIMIT`, `STORE_CACHE`, `CAPACITY_PATH`, `RATE_LIMITS`; one exception with a reason.
- `ai_os/rag.py`, `ai_os/trace.py`, `ai_os/server.py` — `PENDING`. One cache function, one column defaulting to `0`, one call site.
- **Not written, on purpose, with an owner each**: no `Dockerfile` (Day 29), no broker or worker (no trigger yet), no trace sink (Day 29, with identity), no connection pool or read replica (trigger: two processes — Day 29), no ANN index or vector database (step 4 of 5, trigger: corpus × 10), no object store (trigger: corpus refresh > deploy), no middleware attaching the limiter (Day 29).
- `docs/PROGRESS.md` — **deliberately not touched today, and the reason is on the record.** The rollup table's last row is Day 23; Days 24–27 have notes but no rollup row, so the gap is four days old and predates this file. Adding a Day 28 row now would either claim `DONE` for artifacts that do not exist or claim `PENDING` for a day whose plan is not finished — and a rollup that is one row per day is a ratchet of exactly the kind §5.3 describes, so it is worth waiting until the day has something true to say. The rollup is `PENDING` with **no owner yet**; that is a real gap, named rather than quietly widened.
- **Corrections to the record**: this file retracts Day 27's "verified defect" at `RoleAgent.run` (`agent.py:1208–1210`), which does not reproduce — `run_agent` accepts `routed_to` and `role` at `agent.py:550–551`, and `tools/architecture_smoke.py` passes end to end. It also corrects the Day 28 plan's own citations (a per-request RAG load attributed to the wrong function, `state.json`'s shared-global read attributed to the wrong lines, and an `async def` endpoint attributed to a range that does not contain it) and replaces a `45 / 19 / 26` census with the measured `60 / 21 / 39`. **A plan written by the same series that spent nine days making golden sets falsifiable should not be allowed to carry a wrong `file:line`; the day's own ratchet exists precisely because documents drift, and the first drift is always the author's.**

---

## 12. Daily Learning Record

### Problem
Production-readiness is normally a checklist, and a checklist cannot be wrong. The problem the day actually solved is that *this repository's* production story had never been written down, and the inventory turned out to contain three genuinely broken items and one confidently-wrong-answer path that a feature checklist would have ranked below Docker. Concretely: a 262,182,307-byte JSON store parsed per request, a shared mutable gateway global read after the call, an `async def` endpoint wrapping a synchronous generator, a state store where reads write, a ledger that records failures as `$0.00`, a `:memory:` stub that substitutes for Postgres with no warning, a trace id with no sink, and 39 of 60 configuration names invisible in the documentation.

### Concept
**A production architecture is a set of claims about failure, not a set of boxes.** Three supporting ideas did the real work. *A bound is a tuple* (`on`, `unit`, `rate`, `burst`, `consequence`) and a per-run constant is not a rate limit, because it bounds the work rather than the caller and resets to infinity on the next run. *A cache is a correctness decision*, because the series already made content-hash identity the invariant for golden rows and for evidence; a cache keyed on anything else re-grades yesterday's retrieval against today's expectation and passes. *A diagram is falsifiable or it is a mood* — every box a real `file:line`, every edge label from a closed vocabulary, every failure claim a fixture, and a checker that fails the day when the document and the repository disagree.

### Understanding
The synthesis is that this repository is **production-shaped in the places that matter and single-process-shaped in the places that decide**. The choke point is real, the guarantees are real, the audit chain is real — and every one of them is a property of one address space. So the first production change is not a cluster, a region, or a container: it is **make three files safe for a second writer**, in a specific order — state store, then ledger, then the chain — because that order is derivable from the code and every other ordering is a guess. And the second insight is that the scariest item is not the concurrency ceiling but the `:memory:` stub: outages announce themselves, and a fixture that answers a customer's revenue question with `SEED_SQL` does not.

### Implementation
`PENDING` — the day's implementation is specified in §6–§8 and has not been written. What is already true of the work: the diff is small by construction (one module, one cache function, one column, one config file, one harness, two documents), the only behavior change in shipped code is the store parse moving from per-request to per-process, and `agent.py`, `tools.py`, `planner.py`, `executor.py`, `orchestrator.py`, `memory.py`, `sql_safety.py`, `permissions.py`, `approval.py`, the seven tools and `REASONING_PROMPT` all gain nothing. The measurement that will justify the design is 0 chats, 0 embeds, 0 SQL, 0 rows.

### Failure
Two failures, one mine and one inherited, and they are the same failure. Day 27's notes recorded a "verified defect" that does not exist — `RoleAgent.run` does not raise `TypeError`, because `run_agent` accepts both keywords. Day 28's plan repeated it uncritically, four days after the series made falsifiability its standard. I also found and corrected, in the same pass: a per-request RAG load attributed to the wrong function, `state.json`'s shared-global read attributed to the wrong lines, an `async def` endpoint attributed to a range that does not contain it, and a `45 / 19 / 26` configuration census that was wrong by 15 names. The lesson is not "be careful." It is that **a document inherits unverified claims with total confidence, and the failure rate of a citation rises with the number of days between writing and reading** — which is exactly the drift the day's ratchet is built to catch, and the day that discovered the problem is the day that proposed the fix.

### Debugging
The census was the hardest thing to get right, and the interesting part is that a *reasonable* scan was wrong. An AST walk for `os.getenv`/`_env_int`/`_env_float` with module-level constant resolution gives 52 names, 21 of them documented — a clean, quotable, wrong answer, because `config.py:60–66` builds credential names by f-string from the provider name, so eight more names are reachable at runtime and three of the four "documented but unread" names are false positives that a literal scan invents. The fix was to ask *what can the process actually read* rather than *what does a regex find*, and to expand the templates over `PROVIDER_REGISTRY` before comparing. The same distinction — intent versus reachability — is why the second half of the same problem (`CMD_ZDR`) has exactly one true instance instead of four.

### Evaluation
Pre-registered N1–N9 with falsification routes for each (§9), including the two that matter most for honesty: **N8** asserts byte-identical answers with the cache on and off using an `open()` counter rather than a timer, and **N9** asserts the full prior regression plus the explicit note that the control's "identical" row is also the worst configuration. A green N-set proves the map and the bounds. It does not prove the policy, the sizing, or the deployment, and the day will not write a line claiming otherwise.

### Architecture
Plate I is one address space and every box a `file:line`; Plate II is the same system at the first trigger with three things breaking in a derivable order. The seven absences are the day — no container, no worker, no broker, no pool, no ANN index, no trace sink, no authentication — and each has an owner and a trigger, none of them a wish. The one production-shaped conclusion: **do not go multi-region; make three files safe for a second writer, in the order the code already implies.**

### Questions
Does a bounded queue change the *shape* of the human's decision in Day 27, or only its count? What is the right key for admission control once identity exists — user, tenant, or API key — and does the answer change the bound's unit? Is the `$0.00` failure cost ever right to change, or is a ledger that prices only successes the correct object and the retry storm belongs in a separate counter? At what corpus size does brute-force `sorted(dot(...))` over 11,061 vectors actually become the bottleneck rather than the parse? And is a document-under-test the right answer to diagram drift, or is a generated diagram from the import graph the only version of this that survives a year?

### Tomorrow
Day 29 is **deployment** — the container, identity, transport and TLS, the authenticated surface the limiter and the decision route and the trace sink all need, and the external anchor for Day 26's chain so truncation is detectable. It inherits four named absences with triggers already written down, and it inherits the caution that the first thing a deployment day should do is refuse to make three of them easier. The items that stay `PENDING` and are *not* Day 29's: whether 20 req/min is the right rate, whether a 262 MB parse is acceptable on the target hardware, whether the five-step vector migration is the right order, and whether the ledger should ever price a failure.
