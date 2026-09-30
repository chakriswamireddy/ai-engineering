# Agent Security — one decision procedure, deny by default, every decision recorded

> Day 26 concept. Twenty-five days built a machine that reads (Days 6–10), acts
> (11–12), fails honestly (13), computes (14–15), plans (16), remembers (17–18),
> splits into specialists (19), learns how it will be judged (20–22), explains one
> request from the inside (23) and across its boundaries (24), and yesterday learned
> to be attacked (25). Every one of those days added **authority**, and yesterday's
> finding was that authority held by a component which reads untrusted text is a
> *confused deputy's* authority — so the defense has to live somewhere the model
> cannot edit. Day 26 builds that mechanism: `Agent → Policy → Permission check →
> Tool`, with five rules — **read-only by default, explicit tool permissions, input
> validation, output validation, audit logs** — and the deliverable the **basic
> agent security layer**.
>
> **The one line the day would die defending: a boundary in the caller is a boundary
> the next caller forgets, so the boundary belongs in the tool, the default is deny,
> and a permission system that leaves no record is a rumour — which is also why
> "read-only" is not a claim this repo gets to make until somebody says who is
> asking.**

## 1. A threat model is a list of worries; a permission system is a decision procedure

Day 25 produced a good threat model: actors, entry points, authority, the controlling
layer, the residual risk. It is the most useful document in the repository, and nobody
consults it at three in the morning when somebody registers a new tool or when a result
comes back with a column nobody expected. **A list is read once by the people who wrote
it; a decision procedure is consulted on every call, by code, and it returns a verdict
with a name attached** — `no_grant`, `unknown_principal`, `result_violation` — that a
human can later grep for.

The difference between the two artifacts is not thoroughness. It is *when each one is
consulted*. So the day is not "add rules"; it is **"add the one place a rule must be
true, and make everything else inherit it."**

The starting surface is not hypothetical and it is not flattering. Here is the honest
inventory, with the distinction the day actually turns on — a **check** can say no, a
**declaration** says what something is, a **privilege** is the world saying no
regardless of our config:

| What existed before Day 26 | File / function | Kind | What it did not do |
|---|---|---|---|
| `statement_allow`, `tables_allow`, `columns_deny`, `definitions`, `row_limit`, `max_cost`, timeouts | `policy.yaml` → `sql_safety.Policy` | policy | applies to **statements only**; the fixed resolvers never call `SafeSql.inspect` |
| `normalize → parse → policy → cost` | `sql_safety.SafeSql.inspect` | check | knows nothing about who is calling |
| type / enum / minimum / maximum / required | `tools.validate_args` | check (input, shape) | no `maxLength`, no `pattern`, no output side at all |
| out-of-role denial, `role_violation` | `tools.ToolRegistry.scope`, `ERROR_ROLE` | check (role only) | roles are not principals; three names, not a grant model |
| `AUTHORED_SOURCES` + `_check_provenance` | `state.StateStore` | check (writes only) | nothing about reads, tools, or results |
| `tool_timeout_s`, `max_steps`, `budget_usd`, `detect_repeat`/`detect_oscillation` | `agent.execute_bounded`, `run_agent` | resource wall | bounds blast radius; decides nothing about authority |
| `saleor_read_only` role, `default_transaction_read_only` | `data.PostgresDataStore`, `replica_user.sql` | privilege | unavailable offline — `SqliteDataStore` has no such role |
| the tool's own effect, data class, declared columns, caps | — | declaration | **did not exist** |
| who may call what, with what arguments, keeping what columns | — | decision procedure | **did not exist** |
| a record of a decision, allow or deny | `ledger.Ledger` records *provider calls* and money; `trace` records a *request* | neither | no decision was ever recorded, so no incident review had anything to read |

The load-bearing row is the second-to-last. Everything above it works; none of it is
*the* permission check, because none of it can say "this caller may not." And the reason
that matters is not elegance: **a boundary is only as strong as the number of places you
have to remember to put it.** This repository had four. A new tool added tomorrow is
governed by exactly as many of them as its author happened to think about, and the
failure is silent, because nothing in the code said "you forgot."

## 2. The three layers — declaration, policy, privilege — and which one is falsifiable by us

"Read-only by default" is the rule everyone nods at and nobody defines, so the day makes
it three specific claims, because the three fail differently and **only one of them is a
fact about the world**:

| Layer | What it is | Where it lives | Whose fact |
|---|---|---|---|
| **declaration** | the tool says what it is: `effect` (`read`/`write`/`none`), `data` (`public`/`internal`/`pii`) | `permissions.yaml`, enforced by `ToolRegistry.register()` | a fact about *our code* |
| **policy** | config says what is permitted: `statement_allow`, `tables_allow`, `columns_deny`, `row_limit`, `max_cost`, and the per-tool grants | `policy.yaml` + `permissions.yaml` | a fact about *our rules* |
| **privilege** | `saleor_read_only` holds no write grant; `default_transaction_read_only` is set on the session | `data.PostgresDataStore` / the database role | a fact about the **world** |

The third is the strongest precisely because no config file, no prompt, and no
hallucinated function call can overrule a missing grant — and Day 14 built Q5 to prove it
(`DataAgent.run(validate=False)` bypasses layer 1 and the **engine** still refuses). But
it is also the one layer we cannot falsify ourselves: offline, `SqliteDataStore` has no
read-only role, so the C3 fixture reports the privilege claim **`PENDING` until the suite
runs against Postgres with `SALEOR_READONLY_DB_URL` set** rather than pretending a
SQLite stub proves a database privilege.

The weak layer is layer 1, the declaration, because that is the layer a **new tool**
arrives on. A tool registered tomorrow is read-only because of what it declares and
because of what it happens to call, and nothing in the registry notices if it opens a
socket, writes a file, or posts to an HTTP endpoint. Before today that was invisible.
After today it is a `ValueError` at import.

The day's cross-check turns "we were careful" into a test: `run_saleor_sql` declaring
`effect: read` is worth nothing if `policy.yaml` can be edited to
`statement_allow: [select, with, insert]` and nothing notices, so C3 asserts **all three
layers agree** — declared `effect: read` ⊆ `statement_allow ⊆` the read-only role — and
fails, naming the layer that stopped agreeing, if someone edits the policy. A declaration
nobody cross-checks is a comment; a declaration a test cross-checks is an invariant. The
same move converts one happy accident into an invariant: `describe_saleor_schema`'s
closed `table` enum and `policy.yaml`'s `tables_allow` are *exactly* `SCHEMA_CARD`'s
seven tables, so `PermissionSet._cross_check()` makes that a load-time error instead of a
coincidence a future edit can break silently.

## 3. Deny by default vs a deny-list — the failure direction decides the bug class

"Default" has two possible meanings and the difference is the whole bug class:

```text
  deny-list   (start from everything, subtract the forbidden)     ← how "read-only" is usually built
     new tool      arrives with FULL authority
     new table     arrives VISIBLE
     new argument  arrives UNBOUNDED
     forgetting    ⇒ capability you did not mean to grant          (silent)

  allow-list  (start from nothing, grant what is needed)           ← what today builds
     new tool      arrives DENIED until a grant names it
     new table     arrives INVISIBLE
     new argument  arrives UNCAPPED
     forgetting    ⇒ a denial you will notice in the first fixture that fails
```

Day 15's `columns_deny` is a deny-list and it is fine — because its list was complete for
a seven-table schema card. The mistake would have been to add a table to `tables_allow`
and forget the columns. Allow-lists invert that: adding a table is a deliberate act, and
forgetting a grant produces a *denial* you discover in a test rather than a *grant* you
discover in an incident. **The failure direction decides the class of bug.** Day 26's
rule is the allow-list one: **a tool has no authority until a grant names it.** The grant
file is small on purpose — seven tools, three principals — because an allow-list that must
be edited to add capability can be reviewed, and a deny-list that must be edited to remove
capability is reviewed only after the incident.

What a *write* would even look like here is the point. A refund, an order cancellation, an
address change — **none exists in this repository**, and the day does not build one. That
is not a gap; it is the reason the day can claim read-only **without a single write to
undo**. The harness registers a `create_refund`-shaped tool deliberately (fixtures only,
never in `build_default_registry`) and asserts that every real principal is refused it —
and that granting it would require an explicit `effect: write` grant carrying
`confirm: human`, a field whose implementation does not exist yet. That is exactly how
Day 26 hands Day 27 its first client: **the first time this repository considers a write,
its permission model has already insisted the write carries a human in its grant.**

## 4. The choke point, and the `execute(decision=None) ⇒ deny` clause

This is the day's central architectural claim, and it is worth stating in the form a
reviewer can check:

> There are exactly five ways this system reaches a tool, and all five go through
> `registry.validate_args` and `registry.execute`.

`run_agent`'s loop validates then calls `execute_bounded` → `registry.execute`.
`run_tool_answer` *is* `run_agent(max_steps=1, max_tool_calls_per_step=1)`. Day 16's
`executor.run_plan` walks a plan through the same atoms. The fallback ladder's sibling
substitution re-validates and re-executes. Day 19's `RoleAgent.run` is `run_agent` with a
narrower registry. So the check belongs in the registry, and — the clause worth arguing
for in review — it belongs in **both** halves:

- `validate_args` produces the `Decision` and carries it **on the `ToolResult`** (a new
  `permission` field: `{"decision", "code", "effect", "data"}`), so the decision travels
  *with the value* and a future call site cannot drop it the way it could drop a
  side-channel return.
- `execute(name, args, decision=None)` **denies when it is handed no decision**
  (`missing_decision`, `policy`), so a hypothetical sixth call site is **safe by
  omission** rather than dangerous by default.

That last clause is the difference between a boundary and a convention. A check in
`validate_args` alone is a check every caller must remember to route through. Day 16
already taught the lesson in a different costume: `DataAgent.run(validate=False)` is a
deliberate escape hatch marked "nothing else should call it," which is a comment, not a
mechanism. A comment is a request; a `decision=None` denial is a fact.

The denial routes into vocabulary that already exists and is already tested:
`ToolResult.failure(..., error_kind="permission_denied", rejection_class="policy")`. That
single choice means Day 12's rule-5 STOP, Day 13's guard ring, Day 15's classification,
the fallback ladder's policy exclusion, the trace, and the failure ledger all handle it
with code that has a fixture history — **no new control plane.** And the claim is
verified by reading the loop rather than by trusting the shape: the sibling substitution
is already gated on `result.rejection_class != "policy"`, so a denied call can neither
retry nor substitute into a sibling, and `step_policy` is checked *before*
`no_progress`/`oscillation`, so a policy hit owns the terminal. The three files that gain
**no** permission code — `executor.py`, `RoleAgent`, and `agent.py`'s control flow — are
the day's architectural result rather than an omission: they are *inheritors*.

## 5. Grants: effects, data classes, and least privilege at the contract surface

A grant is the day's central noun, kept small enough to read in one screen — six fields,
each answering a question that would otherwise be answered by whoever remembers to ask:

| Field | Question it answers | Example |
|---|---|---|
| `tool` | which surface | `run_saleor_sql` |
| `effect` | what does it *do* — `read` / `write` / `none` | `read` |
| `data` | how sensitive is what it *touches* — `public` / `internal` / `pii` | `pii` for `get_customer` |
| `args` | what may this argument look like — `maxLength`, `pattern`, `enum`, `minimum`, `maximum`, `semantic` | `id: {pattern: "^[A-Za-z0-9_-]{1,64}$"}` |
| `result` | what may come *back* — declared `columns`, `max_rows`, `evidence`, `redact` | `columns: [id, email, orders, currency], redact: [email]` |
| `confirm` | does a human have to approve — `none` or `human` | `human` (only legal with `effect: write`) |

`effect` and `data` are independent axes: a *read* can touch PII (`get_customer` is
`read/pii`), and a *write* can touch public data. The two that get argued about are `data`
and `result`, because they are what make §7 possible.

**Least privilege lives at the contract surface, not only at the call.** `schemas()` is
what the gateway sends the model, so when it returns only the principal's granted slice,
an ungranted tool is not merely refused when called — it is **never named in the prompt**,
so the model cannot spend five turns and three cents discovering that it exists. Day 19
already did this for roles via `scope()`; Day 26 generalizes it from roles to principals
and then makes `scope()` a **derived view** of the grant file (`keep &= principal's
tools`), so `ROLE_TOOLS` and the grants can no longer disagree — a fixture asserts that
even a deliberately wrong role list cannot reach a tool the principal was never granted.

The resolution order stays deliberately primitive: **grant for the principal → the
`default` set → deny `no_grant`.** No inheritance, no wildcards, no prefix matching — a
permission file with a pattern language in it is a permission file whose review becomes a
proof obligation, and this repository builds before it abstracts. A **missing** grant is a
denial (normal, expected, audited, and why an unknown principal resolves to `default`
rather than raising); a **malformed** grant — a principal entry that is not a list, a
grant for a tool nobody registered, a `result.columns` that is not a list, an unknown
`effect`/`data`, a `write` without `confirm: human` — is a **load-time error naming the
key**. A permission file that half-parses is a permission file that silently grants half of
what it looks like it grants.

## 6. Exactly one check per argument

A `sql` argument's check is **semantic** (`SafeSql.inspect`) with a byte cap as a resource
bound; an `id` argument's check is a **shape** (`pattern`) because its only semantic layer
is `_literal()`'s quote-doubling and pretending a pattern is a substitute for escaping (or
vice versa) misdescribes what is standing where; a `table` argument's check is the closed
set derived from the schema card. Two checks on one argument is how a policy ends up
disagreeing with itself — the schema says `sql` is a string, `SafeSql` says it is a query,
and the next reader has to work out which one binds. Zero checks is how `maxLength` never
gets added.

| Argument | Check | Why that one and not the other |
|---|---|---|
| `run_saleor_sql.sql` | **semantic** — `SafeSql.inspect` + a byte cap (8000) as a resource bound | it *is* a query; a shape check cannot know a `SELECT` from a `SELECT` with a write head, and re-parsing it here is Day 15's problem arriving twice |
| `get_*.id` | **shape** — `pattern: ^[A-Za-z0-9_-]{1,64}$` | it is a *literal*; its only semantic layer is `_literal`'s escaping, and a pattern means `_literal` is never the thing standing between the request and the query |
| `describe_saleor_schema.table` | **shape** — the closed set derived from `SCHEMA_CARD` | the card's seven tables *are* `tables_allow`'s seven tables, so the enum **is** the allow-list; a load-time check makes that an invariant instead of a coincidence |
| `search_*.query`, `module` | **shape** — `maxLength`, measured | free text with no semantics; the cap bounds the exfiltration channel and the observation size |
| `k`, `max_chars` | **shape** — existing `minimum`, new `maximum` | already ranges; they just needed the other end |

The two missing JSON-Schema keys (`maxLength`, `pattern`) go into the *same* hand-rolled
`validate_args` in `ai_os/tools.py`, with the same message discipline (**name the field,
name the bound**) and the same refusal to coerce: a value that fails a bound is
**rejected, never truncated into compliance**. A silently shortened query would make
`columns`, `row_count`, and `asserted ⊆ returned` mean something other than what they say.
And every cap is pinned to a **measured** legitimate maximum, not a taste: the longest
legitimate query in the Day 14/15 fixtures (449 chars) sits under the `sql` cap of 8000;
`id` is the longest real identifier in the corpus. A cap tuned to a guess breaks Q1–Q8 and
I1–I9, and the fix is the cap's *provenance*, never the fixture.

The class attached to a refusal is load-bearing because Day 12's `REASONING_PROMPT` grants
exactly one retry: a **`repairable`** rejection invites "fix the argument and retry once,"
a **`policy`** rejection is a STOP. An over-long query is `invalid_args`/`repairable` —
the model may shorten it, which is correct. A permission denial, a denied column in a
result, an ungranted tool, and a write attempt are **policy** and must never arrive
dressed as repairable: **a denial that arrives as `repairable` hands the model the retry
the denial exists to prevent.**

## 7. Output validation — the statement/result gap, the `email` finding, and no partial rows

Input validation existed and was decent. Output validation **did not exist**, and the
reason is structural rather than accidental, which makes it a much better lesson than a
missing feature usually is. Three verifiable facts:

1. `columns_deny` is enforced **on the statement**, inside `SafeSql.inspect`, which only
   `DataAgent.run` calls.
2. `get_product`, `get_order`, and `get_customer` do **not** call it; they build a fixed,
   parameterized query and hand it to `self.store.query(sql, row_limit=1)`. "Safe by
   construction" is a statement about how the query was *written*, not about what it
   *returns*.
3. `get_customer` selects `u.email`, and `email` is **not** on `columns_deny` — which
   names the credential-shaped columns (`password`, `password_hash`, `auth_token`,
   `session_key`, `api_key`, `secret`) and has no vocabulary for PII that is not a secret.

The conclusion is not "PII is leaking." It is sharper and it generalizes:

> **The deny list describes a class of statement, and the system also emits results — so
> half of the policy has no subject.**

Day 26 gives results a subject. `check_result` runs on every `ToolResult` **after** the
tool returns and **before** the result becomes a `tool` message: declared-column
membership, `columns ∩ columns_deny = ∅`, `row_count ≤ grant.max_rows`, the **retrieval
contract** for evidence rows (`doc_id`/`chunk_id` present on every row, text within the
grant's cap, so `cited ⊆ retrieved` and `conclusion ⊆ evidence` stay decidable after a
security decision has run), and redaction of the grant's `redact:` columns with the
redaction **named**. A violation is `rejection_class: policy`.

Three designs are available for a violation, and the choice is the day's most
consequential one:

```text
  (a) return the safe columns      "redact and continue"
  (b) return a truncated page      "row cap as a silent trim"
  (c) return nothing, keep the shape, record the violation            ← this
```

The reason is not tidiness. **A partial answer is how a leak becomes untraceable.** Hand
the model three of five columns and it will narrate the three; the trace will show a
successful call; the audit log will show an `allow`; and the two missing columns will be
nobody's decision, in a system whose entire claim is that every decision is recorded.
Option (a) has a second failure too — redaction the model does not know about is
redaction the model will happily contradict from its own priors. So (c):
`ToolResult.failure(..., rejection_class="policy")` carrying `columns`, `query_kind`,
`executed`, and `redacted`, and **zero values** (`rows: []`, `row_count: 0`). Day 14's
`_data_result` already established the move one layer out, where a *rejected query* keeps
`gen_sql`, `validation: "rejected"`, and `executed: false`; Day 26 reuses the instinct:
**keep the shape, drop the values.**

Redaction is an **`allow`** carrying the columns to drop — never a silent edit. The
`email` question is settled in writing rather than in a config diff: three moves are
available (deny `get_customer` outright; redact `email` at the result boundary and record
it; grant the column to one principal only), the day implements all three mechanisms and
ships the strictest as the default (`redact: [email]`), and the *choice* is **`PENDING`
with an owner** — because **nobody has been asked.** A security day that silently edits a
deny list to make a fixture green has taught the wrong lesson, and one that silently
grants a column to make a fixture pass has invented a mandate nobody gave it.

## 8. The audit record — append-only, hash-chained, metadata-safe

A permission system that cannot be audited is a rumour. `Ledger` records provider calls
and money; the trace records a request; **nothing recorded a decision.** `ai_os/audit.py`
adds one, and the design question is never "how much do we log" — it is **"what may a
security record contain"**:

```json
{"seq": 41, "ts": …, "run_id": …, "trace_id": …, "principal": "analyst",
 "tool": "run_saleor_sql", "effect": "read", "data": "internal",
 "decision": "deny", "code": "no_grant", "args_hash": "sha256:…",
 "args_shape": {"sql": {"chars": 412}}, "result_shape": null,
 "latency_ms": 0, "redacted": [], "prev_hash": "sha256:…", "hash": "sha256:…"}
```

Permitted: identifiers, the principal string, the tool name, the declared effect and data
class, the decision and its code, `args_hash` plus `args_shape` (key names and value
*lengths*), `result_shape` (row count, column names, `truncated`, `redacted`), and
latency. Forbidden, as rules rather than habits: **no SQL text, no row values, no column
*values*, no `email`, no document text, no system prompt, no policy or permission file
contents, no API key, and no `ToolResult.error` string that might echo a payload.** A
reviewer can prove the call happened, prove which arguments were used without storing
them, and reconstruct the decision — and cannot read a single row of customer data out of
the file. The chain is `hash = sha256(canonical_json(entry − hash))` with `prev_hash`
carried *into* the hashed body; canonical means `sort_keys`, fixed separators, UTF-8, and
**no floats** (refused outright, so the hash is reproducible by anyone with the file and a
shell). `append()` is the only mutator: append mode, one `write()` per line, no update, no
delete, and **no rotation**.

Crucially, **allows are recorded too.** A log of only denials answers "what failed" and
never "what is this principal doing" — which is the question an incident review actually
opens with.

The tamper model is stated exactly, because a security artifact whose guarantee is vague
is a liability:

| Attack | Detected | How |
|---|---|---|
| a value edited in place | **yes** | the entry's `hash` no longer matches its content |
| an interior entry deleted | **yes** | every subsequent `prev_hash` mismatches |
| two entries reordered | **yes** | same — the chain breaks |
| the tail truncated | **no** | nothing inside the process can notice a file that simply ends |

So the claim the day may make is **tamper-evident for edits, reorders, and interior
deletions**, never "tamper-proof." `verify()` reports `entries` (how many lines survived)
**separately** from `chain_ok` (whether the chain holds) and names the `first_broken_seq`;
truncation drops `entries` while `chain_ok` stays `true`. The fix for the fourth row is an
**external anchor** — a remote sink, an immutable store, a periodic published head — and
that is **Day 29's**, named in the same sentence as the guarantee.

## 9. Authorization without authentication — the `principal` is a string

This is the most uncomfortable fact and it belongs in the deliverable, not a footnote.
`run_agent` already accepts a `principal`, and `StateStore` already keys **every record**
by one — the memory system has done per-principal isolation since Day 17 without ever
asking what a principal *is*. The plumbing exists; the identity does not. There is no
session, no token, no signature, and `server.py` has no agent route to authenticate
against. So the honest description of the whole day is **authorization without
authentication**:

> Day 26 decides *what may be done*. Day 29 decides *who is asking.*

This is not a weakness of the design; it is the correct decomposition. Authentication
without authorization is a system that knows who you are and does not care what you may
do. Authorization without authentication is a system that knows exactly what each kind of
caller may do and is trusting a label — useless against an attacker, and *precisely
correct* against the confused deputy, a mis-scoped specialist, a plan that emitted the
wrong step, or a future engineer adding a tool. The confused deputy is the threat this
system actually has, and for that threat a grant model is the whole defense. The audit log
inherits the same limit: it records a **decision**, not a **person** — it is an audit log
in the sense of *record*, not *forensics*. And `server.py`'s `GET /audit` is the first
thing in the repository that is meaningfully per-caller — *per-caller only because someone
put a string in a request*, which is a Day 29 fact wearing today's clothes.

## 10. The entry-volume bound

Entries per run are bounded by the turn wall: the number of calls is `≤ max_steps ×
max_tool_calls_per_step` (Day 11 already caps calls per step), so **the model cannot flood
the audit log.** A caller looping at the HTTP layer is a different problem — a *rate
limiting* problem — and rate limiting is **Day 28's**. The bound protects against the
model, not the caller, and the day says which is which.

## 11. The control (C9)

With the permission layer on or off (`PERMISSIONS=none`), every legitimate request must be
bit-identical — same answer, same tool sequence, same SQL text hash, same row counts, same
tokens, same cents — because the whole layer is deterministic, local, and offline: **0
chats, 0 embeds, 0 SQL, 0 rows.** The only behavioral differences may appear on traffic
that is *supposed* to be denied (and on the additive trace columns
`permission`/`permission_code`/`granted_tools`/`result_redacted`), and that asymmetry is
the entire point. With the layer off the audit log is **empty**, which is what proves the
log is a consequence of the layer rather than a tax on the request path. The constraint
that keeps the control honest is easy to violate: **the `default` grant set must be exactly
today's seven tools**, or C9 is not a control but an unannounced behavior change. The
temptation to ship a "safer" default — no `get_customer`, no `run_saleor_sql` for the
anonymous principal — is precisely what a security day must refuse, because a permission
layer that ships tighter than the evidence in front of it is a policy decision made by a
fixture instead of by an owner.

## 12. The residual register

`PENDING`, with owners. Every limit named with an owner, and **no line claiming the agent
is secure**:

- **The grants' correspondence to reality.** Nobody who owns this data has been asked who
  may read `account_user.email`; the grant file is a **proposal, not a mandate**, and the
  `email` decision ships the strict default with the choice left open. A green C-set
  proves the **decision procedure**; it does not prove the **policy**.
- **The `principal` string is unauthenticated.** An unauthenticated `principal=admin` is
  not a weaker admin, it is an admin. Identity, transport, secrets, and egress are **Day
  29**.
- **No external anchor.** Tail truncation of the audit chain is undetectable inside the
  process; the anchor is **Day 29's**.
- **No rate limiting.** A caller looping at the HTTP layer is not bounded by the per-run
  wall; production rate limits and queues are **Day 28**.
- **No real-model escalation observation.** The stub is scripted to do what the fixture
  says and cannot surprise us, so whether a real model ever attempts an escalation is a
  question no fixture can answer.
- **Human approval for consequential actions** is **Day 27** — the `confirm: human` field
  in every write grant is its first client, and an allow-list can decide what is
  *permitted* but never what is *wise*.

## 13. Configuration

| Var | Default | Meaning |
|---|---|---|
| `PERMISSIONS` | `1` (on) | `none`/`0`/`off`/`false`/`no`/`disabled` restores the ungoverned path (the C9 off-switch) |
| `PERMISSIONS_PATH` | `permissions.yaml` (beside `policy.yaml`) | the grant file the layer reads |
| `AGENT_PRINCIPAL` | `default` | the caller identity, beside Day 17's `STATE_PRINCIPAL` (and it authorizes nothing yet) |
| `AUDIT_LOG_PATH` | `data/audit.jsonl` | the append-only decision log |

**Off must be genuinely off:** `PERMISSIONS=none` means no check, no result contract, and
no audit entry — C9 asserts a legitimate request is byte-identical and the log stays empty.
The whole layer is dependency-free and offline, and the decision path takes no clock (the
`ts` is stamped by the audit *writer*), so a decision is a pure function of
`(principal, tool, args, perms)` and cost is **dictionary lookups, set membership, and one
`sha256` per decision**.
