# Day 14 — Data Agent

## Objective

Days 6–13 built retrieval (find by similarity), tools (declarative actions), and the loop that drives them. But two of the five tools — `get_product` / `get_order` / `get_customer` — have been seeded stubs since Day 11, each tagged `pending_resolver: "day14"`, and an entire class of questions is unanswerable by anything built so far: **aggregation over rows**. "Which products generated the most revenue last month?" has no nearest chunk and no nearest symbol — it has a **sum over a join**. Today the third corpus arrives — **Saleor's structured data, the Postgres database behind the platform** — and with it the first tool that *writes its own query* instead of matching a vector: **Text-to-SQL**. The curriculum's pipeline is the day: **Question → Schema → SQL → Validation → Database → Result → Explanation.** The schema becomes the new corpus metadata (**schema grounding** — the tables, columns, foreign keys, and business definitions the model is told it may query); the model generates SQL through the Day 4 gateway; a first-cut validator keeps it **read-only** (the curriculum's *learn* bullet, and the platform's own `saleor_read_only` Postgres role, already provisioned in `replica_user.sql`); execution returns rows; and the explanation must ground every number in the result — the SQL-shaped sibling of `cited ⊆ retrieved`, **`asserted ⊆ returned`**. The adversarial hardening — DELETE/UPDATE, expensive queries, nonexistent tables, ambiguous definitions, parser, allow/deny, cost guards — is **Day 15's**; Day 14 makes the Data Agent *work* read-only, completes the Day 11 data stubs with real resolvers, and hands Day 15 an agent to attack. Everything Day 11–13 built (registry, validation, the loop, the timeout wall, the typed `ToolError`) is exercised **as-is**: `run_saleor_sql` is just another tool, the loop's self-correction is what *repairs a bad query*, and the Day 13 timeout wall is what stops a slow one. The deliverable is the **read-only Saleor Data Agent**.

## Duties Outline

### 1. Learn: Structured Data Is a Third Corpus (45 min)

- [ ] Accept the premise: retrieval finds by **similarity**, SQL finds by **set logic**. Days 6–10 optimized "which chunk is closest to the question"; "which products generated the most revenue" has no closest chunk — it has a `SUM` over a `GROUP BY`. The question classes are disjoint: *"what do the docs say"* (doc RAG), *"where is it implemented"* (code RAG), *"how much / how many / which rows"* (SQL). Name the third corpus and its question class.
- [ ] Learn the third corpus's shape: not chunks with headings, but **tables with columns, types, keys, and relations** — a schema is a graph of sets, and the answer is a **computation**, not a passage. The evidence record changes from `top-K chunks` to `columns + rows`.
- [ ] Learn the honesty difference: a retrieval answer is graded by `cited ⊆ retrieved`; a SQL answer is graded by **`asserted ⊆ returned`** — every number and entity in the explanation must appear in (or be a stated aggregate of) the result set. Fabrication here is the Day 8 sin wearing figures: a plausible "≈ 1,240 orders" the query never produced.
- [ ] See why this is the first **generative-query** tool: every prior tool took a query the model wrote for *retrieval*; `run_saleor_sql` takes a query the model wrote for a *database* — the model is now authoring an executable program, and the day's whole safety question is born there.
- [ ] State the boundary: Day 14 makes it *work* read-only; **Day 15** makes it *safe* against an adversary (DELETE/UPDATE, bomb queries, nonexistent tables, ambiguous definitions). Name the seam; do not build ahead.

### 2. Learn: Schema Grounding — the Schema Is the New Corpus Metadata (45 min)

- [ ] Transfer the discipline: Day 8 wrote stopword-lean queries for a scorer, Day 11 wrote descriptions for a chooser — Day 14 writes the **schema card** for a generator. Text-to-SQL accuracy is mostly schema grounding: the model cannot query a column it was never told exists.
- [ ] Learn what a schema card must carry: table names; **column names + types**; **primary/foreign keys** (the joins); a one-line purpose per table; and — the semantic half — a **business-definition glossary** ("revenue" = `SUM(order_line.quantity × unit_price)` net of refunds, per channel; "last month" = which timezone, which channel?).
- [ ] Confront the two-schema split-brain, Day 10's lesson in a new costume: Saleor has a **GraphQL schema** (`schema.graphql` — the API's public shape) and a **Postgres schema** (the Django tables' private shape), and they are *not the same*. The Data Agent queries the DB, so it must be grounded on `information_schema`, never on `schema.graphql` — or the model writes SQL against GraphQL names that do not exist as tables.
- [ ] Budget the schema: Saleor's DB is hundreds of tables; a full dump neither fits the context nor helps (it drowns the query in noise). Day 14 curates a **core subset** (orders, order lines, products, accounts) plus a `describe_saleor_schema(table)` tool for on-demand expansion; automatic schema *retrieval* is a seam to name (Day 16/19), not to build.
- [ ] Pre-commit: a query is only *grounded* if every table and column it names appears in the schema card the model was handed — an unknown-column SQL error is a **schema-grounding** failure, not a model failure, and the trace must be able to say so.

### 3. Design: The Text-to-SQL Pipeline (30 min)

- [ ] Write the pipeline exactly as the curriculum states it, then decide **where each stage lives** in the codebase: Question → Schema → SQL → Validation → Database → Result → Explanation.
- [ ] Design decision #1 — **the pipeline is a tool shape, not a new loop.** `run_saleor_sql(sql)` registers like any tool; the *generation* turn is the loop's own chat (the model emits SQL as the tool argument), the *observation* is the result rows, and the *explanation* is the loop's final answer. Schema rides in the tool description / system context, SQL in `tool_calls.arguments`, validation inside the executor, execution at the read-only connection. Nothing new is invented — **the loop is the pipeline**.
- [ ] Design decision #2 — **parameterized tools vs generated SQL.** `get_product/order/customer(id)` are safe by construction (a fixed query, one validated `id`); `run_saleor_sql(sql)` is open. Keep both: the parameterized tools *complete the Day 11 stubs* (the common lookups, zero generation risk); the open tool is the agent's power and Day 15's target. The trace distinguishes "parameterized answer" from "generated-query answer", the way Day 11 tagged a data stub.
- [ ] Design decision #3 — **self-correction is the SQL repair loop.** A SQL error (unknown column, syntax) is a Day 11 rejection / Day 13 `tool_error` **message**, so the loop's Day 12/13 self-correction re-issues a *fixed* query — the most valuable use of the self-correction bound so far. This is *why* the query must stay **visible** (model-written), not hidden inside a black-box "ask the data" tool: a hidden query cannot be graded, repaired, or attacked.
- [ ] Pre-commit the two invariants: **(a)** the connection is read-only and validation rejects non-`SELECT` *before* execution (Day 14's first cut; Day 15 hardens); **(b)** the final answer's numbers ⊆ the result set (`asserted ⊆ returned`), enforced by the trace.

### 4. Build: The Read-Only Data Seam (1 hour)

- [ ] Connect with the platform's existing read-only role: `replica_user.sql` already provisions **`saleor_read_only`** (`GRANT CONNECT` + `GRANT USAGE` + `GRANT SELECT ON ALL TABLES` + default privileges). Day 14 *uses* it — a new `SALEOR_READONLY_DB_URL` beside the Day 4 DB URL, one `psycopg` connection (the day's one new dependency: a driver, not a framework — Rule-1).
- [ ] Build **defense in depth**, seeded (Day 15 deepens): **(1)** the read-only *role* — the last line, even if bad SQL arrives; **(2)** a read-only transaction (`SET default_transaction_read_only = on`) — the second line; **(3)** the validator's `SELECT`-only allow-list (§6) — the first line. Record all three and which one Day 15 hardens.
- [ ] Give the connection a **query timeout**, measured like Day 13's `tool_timeout_s`: the DB is the first tool with *real* latency, so the Day 13 wall earns its keep here for real. Timeout → `{error_kind: "timeout"}`, an observation, never a hung run.
- [ ] Complete the Day 11 stubs: `get_product` / `get_order` / `get_customer(id)` now run their real parameterized queries and drop `pending_resolver` from their results — the interface Day 11 shipped is *filled*, unchanged.
- [ ] Carry the Day 13 taxonomy: a DB error (syntax, permission, unknown column/table, timeout) is a `ToolError` with `error_kind` (`invalid_sql` | `unknown_column` | `timeout` | `tool_error`), an observation the loop can read and repair against — never an exception that unwinds the run.

### 5. Build: Text-to-SQL — Schema Card → Generated SQL (1 hour)

- [ ] Build the **schema card**: a curated core subset serialized for the prompt (tables, columns + types, FK joins, purpose lines) plus the business-definition glossary, budget-clamped like any context (Day 6's `budget_chars` reflex, applied to a schema). Hand-list the core tables; note that scaling it is schema *retrieval*, a named seam.
- [ ] Ground the model with the pipeline order made concrete: the tool description says *which* tables exist and *what the definitions mean*; the question supplies intent; the chat turn produces SQL. Schema grounding is the accuracy lever — a model given the FK from `order_orderline` to `product_product` writes the join; one given bare names guesses.
- [ ] Seed the glossary **deterministically and separately** from the model: "revenue", "last month", "customer", "channel" resolve to explicit expressions/timezones so two runs cannot mean two different things. The glossary is a **corpus of definitions** — the schema's semantic half — and it is exactly what Day 15's "ambiguous business definitions" attack will try to break.
- [ ] Write the honest `PENDING`: whether a real model writes *correct* SQL for "which products generated the most revenue last month" is a semantic cell — Day 21's benchmark on Day 14's schema card. The stub proves the *plumbing*; a real key proves the *grounding*.
- [ ] Keep the generated query **visible**: it is the tool argument in the transcript, so the trace can grade it and the loop can repair it. The day's central artifact must not be hidden behind a summarized tool.

### 6. Build: SQL Validation + Execution (1 hour)

- [ ] First-cut validation (the seed Day 15 hardens): **single statement**; **`SELECT` / `WITH` only**; deny DML/DDL (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `TRUNCATE`, `GRANT`, `CREATE`); reject stacked statements on `;`. Hand-rolled (Rule-1) — and record **honestly** that a keyword/regex check is **not** a parser and is defeatable (comments, nested statements). Day 15 builds the real parser + allow/deny; do not pretend the seed is a wall.
- [ ] Enforce the **row limit** and the **truncation flag**: every query is wrapped with a `LIMIT` and the result records `row_count` + `truncated` — an aggregation over 100k rows must not silently pour 100k rows into the budget, and a truncated result must be *reported* so the explanation cannot claim completeness it did not have.
- [ ] Execute inside the Day 13 `execute_bounded` wall over the read-only connection: timeout → `{error_kind: "timeout"}`; DB error → `{error_kind: "invalid_sql"/"unknown_column"}`; empty result → `{ok: true, rows: [], note: "no rows"}` (**`no_data` is a finding, not a failure** — Day 13's rule, in SQL).
- [ ] Return a consistent **evidence record**: `{ok, sql, columns, rows, row_count, truncated, elapsed_ms}` — the data tool's observation shape, parallel to the retrieval tools' top-K and the Day 11 data stub's record, so the harness grades all four corpora with **one** shape.
- [ ] Name the seam plainly: allow/deny rules, a real parser, cost estimation, and an `EXPLAIN`/cost guard are **Day 15's**; Day 14 ships validation strong enough to keep the *read-only agent honest*, not strong enough to survive an *attacker* — and says so.

### 7. Build: Result → Explanation — `asserted ⊆ returned` (45 min)

- [ ] Ground the final turn: the explanation states only what the result supports; every number, entity, and ordering it asserts must appear in (or be a stated count/sum of) `rows`. This is Day 6's `cited ⊆ retrieved` translated to rows: **`asserted ⊆ returned`** — the day's headline invariant, checked by the trace.
- [ ] Handle the three honest outcomes explicitly: **rows present** → explain them (numbers ⊆ result); **empty** → "no data for that period" (Day 13's `no_data`, never an invented row); **truncated** → "top N of more" (never an unwarranted "all").
- [ ] Keep the model from *re-deriving* the answer: it may compute a headline (a sum of returned rows, a top-k of returned rows) but may not invent a row it did not receive. The guard is mechanical — parse the explanation's numbers/entities and assert membership in the result — the SQL sibling of the citation check.
- [ ] Name the failure this guards, in Day 8's terms: a fluent, confident, **numeric** answer the database never produced — the fabrication warning file, now with columns of figures attached.

### 8. Test: The Data-Agent Question Set (1 hour)

- [ ] Pre-register the data fixtures (**Q1…Q8** — *query* fixtures, distinct from Day 7's A–E question families) with expectations written **before** any run:
  - **Q1** aggregation (the curriculum example): "Which products generated the most revenue last month?" → SQL joining order lines → products over a date window; result rows; explanation numbers ⊆ result.
  - **Q2** parameterized tool completed: "Fetch order 1" → `get_order(id="1")` hits the **real** DB; no `pending_resolver` in the result.
  - **Q3** multi-table join: "Which customers placed the most orders?" → customers ⋈ orders; the FK in the schema card is what makes it writable.
  - **Q4** empty result: "How many orders in 2099?" → `rows: []`; answer says "no data"; zero fabricated rows (Day 13 `no_data`).
  - **Q5** read-only enforced: an injected `DELETE FROM order_order` → validator rejects **before** execution; and with the validator bypassed, the `saleor_read_only` role refuses → nothing deleted. Two independent guards, both asserted.
  - **Q6** SQL error repairs: the query names a nonexistent column → `{error_kind: "unknown_column"}` observation → the loop self-corrects → the fixed query runs; no fabrication on failure.
  - **Q7** truncation honesty: a query matching far more rows than the limit → `truncated: true`, `row_count` capped; the explanation says "top N", not "all".
  - **Q8** grounding: the explanation's numbers/entities ⊆ the result set (`asserted ⊆ returned`) — the invariant, per row and in aggregate.
- [ ] Split the day honestly, Day 7's way: **Q2/Q4/Q5/Q6-mapping/Q7/Q8 are `MECH`** — pure functions of the read-only connection, the validator, the row-limit arithmetic, and stored SQL. **Q1/Q3's real-model SQL generation** (does the model write the right join and window from the schema card) and **Q8's explanation grounding under a real model** are `PENDING` until the real gateway. The stub proves the *seam*; a real key proves the *grounding*.
- [ ] Re-run Days 7–13's fixtures as the no-regression gate: the Data Agent is **additive** — a fourth corpus behind the same harness — so the doc nine, the code fixtures, T1…T7, A1…A8, and F1…F9 must stay exactly as green. A data tool that changes a retrieval number is a bug wearing a feature.
- [ ] Apply Day 7's isolation: wrong table/column → **schema grounding** owns it; valid SQL, wrong numbers → **generation** owns it; SQL fine, no rows → **the data** owns it (a legitimate `no_data` finding); validator too strict → **the validator** owns it (a false rejection). One owner per symptom, in the trace.

### 9. Measure + Documentation (45 min + 20 min)

- [ ] Rule-4 as a data table, one row per fixture, every cell a real run's: `question | generated SQL (y/n) | validation verdict | executed? | row_count | truncated | asserted ⊆ returned | error_kind | elapsed_ms | cost` — the **`asserted ⊆ returned`** column is the day's headline, and the **generated SQL** column is the artifact Day 15 attacks.
- [ ] Record the cost shape honestly — it *differs from retrieval*: a data answer costs **0 embeds + 2 chats** (generate SQL, explain rows; +1 chat if the loop repairs), versus retrieval's **1 embed + 1 chat**. The schema card is a new, recurring **input-token** cost per step — put it next to the embeds so both currencies are visible; the DB does exact set logic, so it needs no vectors.
- [ ] Keep the negatives column with owners: what the Data Agent cannot yet do safely (DELETE/UPDATE, bomb queries, nonexistent tables, ambiguous definitions — **Day 15**), what the schema card cannot yet scale to (the whole DB — **schema retrieval, Day 16/19**), and whether a real model grounds its SQL and its explanation — **Day 21**. Empty negatives mean the day learned nothing; these have owners.
- [ ] Write `docs/concepts/text-to-sql.md` — structured data as a third corpus, schema grounding, the schema card + glossary, the pipeline-as-a-tool, read-only defense in depth (first cut), `asserted ⊆ returned`.
- [ ] Write `docs/evaluations/data-day14.md` — the §8 data table, the Q1…Q8 pre-registered fixtures, the read-only guard assertions, the cost shape (0 embeds + 2 chats + schema tokens), and the no-regression lines.
- [ ] Fill the Day 14 Daily Learning Record (Problem / Concept / Understanding / … / Tomorrow).
- [ ] Record token economics: the schema card's per-step input tokens, the generate+explain chat pair, the **zero-embed** claim (SQL is exact, not similarity), and the repair chat's cost when a query is corrected.

## Deliverable

The read-only Saleor Data Agent — a question answered by a generated query over real data, grounded in the result:

```text
Question ("Which products generated the most revenue last month?")
   ↓  Schema      — schema card (tables · columns · FKs · glossary), budget-clamped   (grounding)
   ↓  SQL         — model writes it through the Day 4 gateway (visible in the transcript)
   ↓  Validation  — single SELECT/WITH · no DML/DDL · no stacked ';'   (Day 14 seed → Day 15 parser)
   ↓  Database    — read-only: saleor_read_only role · READ ONLY txn · LIMIT + timeout wall
   ↓  Result      — {ok, sql, columns, rows, row_count, truncated, elapsed_ms}
   ↓  Explanation — asserted ⊆ returned (every number traces to a row)   ← the day's invariant
   ▼
  "Product X led with 412 orders / €18,240 revenue (top 10 of 87 rows) —
   from order_orderline ⋈ product_product, last calendar month, default channel"
```

Days 6–13 answered "what does the text say" and "where is it implemented". Day 14 answers **"how much / how many / which rows"** — a generated query over real Postgres, read-only by role and by validator, with every number traceable to the result. The adversarial hardening (DELETE/UPDATE, expensive queries, nonexistent tables, ambiguous definitions, parser, allow/deny) is Day 15's; the schema that does not fit is Day 16/19's schema retrieval; and whether a real model writes the right SQL is Day 21's measure.

## Exit Check — answer in your own words

- What can a SQL tool answer that no retrieval tool can, and why is the question class disjoint rather than merely harder?
- What does schema grounding hand the model, and why is the *DB* schema the right one to ground on rather than `schema.graphql`?
- Why does the query have to stay *visible* (model-written) rather than hidden inside an "ask the data" tool — and which earlier day's guard does visibility unlock?
- Name the three layers of read-only defense and say which one holds even if the other two fail.
- What does `asserted ⊆ returned` forbid, and how does it differ from `cited ⊆ retrieved`?
