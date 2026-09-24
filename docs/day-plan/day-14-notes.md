# Day 14 Notes — Data Agent

> Notes answering `docs/day-plan/day-14.md`.
> Days 6–13 made the OS retrieve text (docs and code) and act on it through a defended loop. But four docs-days and four code-days share one blind spot: neither can **add up a column**. "Which products generated the most revenue last month?" has no nearest chunk and no nearest symbol — it has a `SUM` over a `GROUP BY`. Day 14 adds the third corpus — **Saleor's structured data, the Postgres database behind the platform** — and the first tool that **writes its own query**: Text-to-SQL. The schema becomes the new corpus metadata (**schema grounding**), the model generates SQL through the Day 4 gateway, a first-cut validator keeps it **read-only** (the platform's `saleor_read_only` role already exists in `replica_user.sql`), execution returns rows, and the explanation must ground every number in the result — the SQL sibling of `cited ⊆ retrieved`, **`asserted ⊆ returned`**. The adversarial hardening is Day 15's; Day 14 makes the Data Agent *work* read-only.
> The single idea: **the loop is the pipeline.** `run_saleor_sql(sql)` is just another tool — generation is the loop's own chat, the observation is the rows, the explanation is the grounded final turn, and Day 13's self-correction is what *repairs a bad query* — so the day adds a corpus, a schema card, and one honest invariant (`asserted ⊆ returned`), and invents no new control flow.

---

## 1. The Third Corpus — Data, Not Text

Every prior "corpus" was text made findable: Day 6's prose pages, Day 10's source symbols. Both were served by the same machine — embed, compare vectors, rank. Day 14's corpus is **not** findable by similarity, and the distinction is not "harder" but **different in kind**:

| Question class | Example | Corpus | Mechanism | Evidence shape |
|---|---|---|---|---|
| what the docs say | "How do I create a checkout?" | markdown pages | vector + lexical fusion | top-K chunks (`doc_id`) |
| where it is implemented | "Where is checkout handled?" | source symbols | symbols + filters | `module#Symbol` records |
| **how much / how many / which rows** | "Which products generated the most revenue last month?" | **Postgres tables** | **exact set logic (SQL)** | **columns + rows** |

The reason the third class is *disjoint* — not merely another retrieval domain — is that its answer is a **computation**. "Revenue last month" is `SUM(quantity × unit_price)` filtered by a date window and grouped by product; no chunk contains that number, and the correct answer depends on the *rows*, not on the *text about the rows*. A retrieval tool can return the sentence that defines revenue; only SQL can return the revenue.

The honesty consequence is the day's spine. Day 6 established `cited ⊆ retrieved` — an answer may only cite evidence that was retrieved. Its SQL translation is **`asserted ⊆ returned`**: every number, entity, and ordering the explanation asserts must appear in (or be a stated aggregate of) the result set. And the failure it guards is *easier* to commit here than anywhere before, because a fabricated statistic looks exactly like a real one — "approximately 1,240 orders" is fluent whether or not a query produced it. Day 14's headline column is that invariant's verdict, and it must read `ok` on every row.

### Why this is the first generative-query tool

Every tool since Day 11 took a **query the model wrote for retrieval**: `search_saleor_code(query="where is checkout handled")` is a string handed to a vector scanner. `run_saleor_sql(sql="SELECT …")` is different in kind — the model is authoring an **executable program**. That is the day's power and the day's danger at once: the tool can compute anything the schema allows, *including* things it should not (Day 15), and the artifact — the SQL — is the thing to grade, repair, and attack. Keeping it visible (§3) is what makes all three possible.

---

## 2. Schema Grounding — the Schema Is the Corpus Metadata

Text-to-SQL accuracy is, for the most part, a **grounding** problem: the model cannot query a column it was never told exists. Day 14's schema card is the continuation of a discipline that now spans four days and four audiences:

| Day | Metadata written for… | The artifact |
|---|---|---|
| Day 8 | a **scorer** (retrieval) | the stopword-lean query |
| Day 11 | a **chooser** (tool selection) | the tool description |
| Day 14 | a **generator** (SQL) | the **schema card** (+ glossary) |

A schema card carries five things, and the fifth is the one that is not mechanical:

1. **table names** and a one-line purpose each;
2. **columns + types** (so `total` is known to be money-decimal, `created` a timestamp);
3. **primary/foreign keys** — the joins, which are what make "revenue *by product*" writable at all;
4. **the dialect** and the read-only constraints (what the model may assume about `SELECT`);
5. **the business-definition glossary** — the semantic half.

The glossary matters because Saleor is **multi-channel and multi-currency**: "revenue" is not a column, it is a *decision* — which channel, which currency, gross or net of refunds, and what "last month" means across timezones. Left to the model, every run would resolve these differently; the glossary resolves them **deterministically and separately from the model**, so two runs cannot mean two different things. It is a *corpus of definitions* — the schema's semantic half — and it is exactly what Day 15's "ambiguous business definitions" attack will try to pull apart.

### The two-schema split-brain

Day 10 taught that Saleor's code is split between the GraphQL layer (`saleor/graphql/`) and the service layer (`saleor/`), and that a double-hit is correct. Day 14 meets the same pattern one level down: Saleor exposes a **GraphQL schema** (`schema.graphql`, the API's public shape) *and* a **Postgres schema** (the Django tables' private shape), and **they are not the same artifact**. A mutation named `CheckoutComplete` is a GraphQL type; the rows it writes live in tables named like `order_order`, `order_orderline`, `product_product`, and the account tables. The Data Agent queries the DB, so it must be grounded on the **database schema** (read from `information_schema`), never on `schema.graphql` — otherwise the model writes `SELECT … FROM CheckoutComplete` and meets an unknown-table error that is a *grounding* bug, not a model bug. Naming which schema is half of grounding.

### Budgeting the schema

Saleor's database is hundreds of tables; a full dump neither fits the context nor helps — it drowns a two-table query in irrelevant columns and costs input tokens on every step. Day 14 curates a **core subset** (orders, order lines, products, accounts — the tables the curriculum's example and the Day 11 lookups need) and adds a `describe_saleor_schema(table)` tool for on-demand expansion. The honest seam: scaling the card to the whole DB is **schema retrieval** — a question answered by the same machinery Days 8–10 built, applied to tables instead of chunks — and it is named for Day 16/19, not built today.

---

## 3. The Text-to-SQL Pipeline — and Where It Lives

The curriculum states the pipeline; the day's design work is deciding **where each stage lives**, and the answer is the reason the day stays small:

```text
Question  →  Schema       →  SQL          →  Validation  →  Database     →  Result  →  Explanation
(user)       (schema card)   (model, via      (SELECT-only   (read-only      (columns   (grounded
             in prompt/       the Day 4        allow-list)    role + LIMIT    + rows)    final turn)
             tool desc)       gateway)                        + timeout wall)
```

Every stage maps onto something that already exists:

- **Question** is the loop's user message.
- **Schema** rides in the tool description and system context — the schema card, budget-clamped like any context.
- **SQL** is the model's `tool_calls.arguments` — the loop's own chat *is* the generation turn. No extra "SQL-writer" call.
- **Validation** lives inside the tool executor (Day 11's registry seam).
- **Database** is the read-only connection (§4), wrapped in Day 13's `execute_bounded` wall.
- **Result** is the day's new evidence record — columns + rows.
- **Explanation** is the loop's final answer, grounded by `asserted ⊆ returned`.

**The loop is the pipeline.** Nothing new is invented: `run_saleor_sql` registers like `search_saleor_code`, the loop drives it, and the pipeline's arrows are the loop's own steps. This is the same claim Day 11 made about tools ("the old seams made declarative") and Day 12 made about the loop ("the missing else branch"), one corpus later.

### Two shapes of data tool

The registry gains two kinds, and the difference is the day's other central design decision:

| Tool | Query | Safety | Role |
|---|---|---|---|
| `get_product(id)` / `get_order(id)` / `get_customer(id)` | **fixed** (one parameterized query each) | safe by construction — one validated `id`, no generation | completes the Day 11 stubs; the common lookups |
| `run_saleor_sql(sql)` | **generated** by the model | open — this is Day 15's target | the agent's power: aggregation, joins, arbitrary set logic |

The parameterized tools are where the Day 11 `pending_resolver: "day14"` tag finally comes off: the interface Day 11 shipped (schema, `id` validation, result shape) is *filled* with a real query, unchanged. The open tool is where the day's safety question is born — and where the trace must distinguish a *parameterized* answer from a *generated-query* answer, exactly as Day 11 tagged a stub so a fixture lookup could never pass for live data.

### Why the query stays visible

A tempting design hides Text-to-SQL behind one black-box tool — `ask_the_database(question)` — that does schema → SQL → execute → explain internally. Day 14 rejects it, and the reason is a concrete guard: **visibility unlocks Day 12/13's self-correction.** If the SQL is the model's own tool argument, then a SQL error ("column `order.order_total` does not exist") is a rejection *message* the model reads and repairs with a *fixed* query — the loop's self-correction bound applied to the single most valuable case yet: query repair. If the SQL is hidden, the model never sees its own mistake, the trace cannot grade the query, and Day 15 has nothing to attack. The artifact must be on the table.

---

## 4. Read-Only Access — Defense in Depth, First Cut

The curriculum's *learn* bullet is "read-only access", and the platform has already done the hard part: `saleor-platform/replica_user.sql` provisions a **`saleor_read_only`** Postgres role on DB init —

```sql
CREATE USER saleor_read_only WITH PASSWORD 'saleor';
GRANT CONNECT ON DATABASE saleor TO saleor_read_only;
GRANT USAGE ON SCHEMA public TO saleor_read_only;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO saleor_read_only;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO saleor_read_only;
```

Day 14 *uses* this role (a new `SALEOR_READONLY_DB_URL` beside the existing `DATABASE_URL=postgres://saleor:saleor@db/saleor`), and it is the **last line** of a three-layer defense:

| Layer | Mechanism | Holds when… | Owner |
|---|---|---|---|
| 3 (last) | the `saleor_read_only` **role** — no write grants exist | everything above it failed | the database |
| 2 | a **read-only transaction** (`SET default_transaction_read_only = on`) | the validator was bypassed | the connection |
| 1 (first) | the **validator** — `SELECT`/`WITH` only (§5) | the model tried to write | the executor |

The ordering is deliberate: the cheapest, fastest check runs first (reject a `DELETE` before it reaches the DB), and the *most certain* check runs last (even if layers 1 and 2 are both wrong, the role has no `INSERT` privilege to exercise). This is Day 12's "termination is a config, not luck" reflex applied to safety: read-only is not a hope that the validator is correct, it is a *privilege the database enforces* as well.

Day 14 **seeds** this layer and labels it as such: the real parser, allow/deny rules, cost estimation, and the `EXPLAIN` guard are **Day 15's**. The honest statement is that a keyword/regex validator is defeatable (comments, nested statements, dialect tricks), and the role is what keeps the *seed* honest until Day 15 hardens the front.

### Latency — the first real use of Day 13's wall

The database is the first tool with **real latency**: a missing index or an unconstrained `JOIN` can run for seconds. Day 13's `tool_timeout_s` wall was built against a stubbed `hang`; Day 14 is where it protects a *real* user experience. The query runs inside `execute_bounded`, and a slow query returns `{ok: false, error_kind: "timeout", retryable: true}` — an observation the loop can respond to (narrow the query, add a filter) — while `elapsed_ms` records the true cost. Day 15 adds row limits and cost guards on top; Day 14 proves the wall holds against a real slow query.

### The error taxonomy, in SQL

Day 13's third family (tool errors) gets its SQL vocabulary, and each maps to a repair strategy — which is *why* the taxonomy is worth keeping precise:

| DB condition | `error_kind` | The loop's move |
|---|---|---|
| wrong table/column name | `unknown_column` / `unknown_table` | **repair** — the schema card was misread (a grounding fix) |
| syntax error | `invalid_sql` | **repair** — re-issue corrected SQL |
| permission denied (a write) | `invalid_sql` (validator) / DB error | **stop** — this is Day 15's adversary, not a repair |
| slow query | `timeout` | **fallback** — narrow or skip |
| zero rows | *not an error* — `{ok: true, rows: []}` | **explain honestly** ("no data", Day 13's `no_data`) |

The last row is the one people get wrong: an empty result is a **finding**, not a failure. "How many orders in 2099?" has the correct answer *zero*, and the honest agent says so.

---

## 5. Generating and Validating SQL

### Generation — grounding in, query out

The model receives the schema card and the question and emits one `SELECT`. The accuracy levers are all in the card:

- **Foreign keys are the joins.** Told that `order_orderline.product_id → product_product.id`, the model writes the join for "revenue by product"; given bare names, it guesses. Day 10 recorded the dependency map and Day 12 walked it; Day 14 writes the *join* from the same kind of metadata.
- **Types prevent category errors.** Knowing `total` is a decimal money field (with a channel currency) stops `SUM` over a text column.
- **Definitions pin the semantics.** The glossary's "revenue" expression and "last month" window are what make Q1's answer *the same* across runs.

The `PENDING` line is sharp here: whether a *real* model writes correct SQL for the curriculum's example — the right join, the right window, the right definition — is a **semantic** cell. The stub supplies stored SQL and the harness grades everything downstream; a real key is what proves the *generation* half. Day 21 is the benchmark.

### Validation — the seed, and its honest limits

Day 14's validator is a **first cut** and says so:

```python
DENY = {"insert", "update", "delete", "drop", "alter", "truncate", "grant", "create", "revoke"}

def validate_readonly(sql: str) -> ToolResult:
    body = strip_comments(sql).strip().rstrip(";")
    if ";" in body:                       # stacked statements
        return ToolResult.failure("only a single statement is allowed", error_kind="invalid_sql")
    head = first_keyword(body)
    if head not in ("select", "with"):
        return ToolResult.failure(f"only SELECT/WITH allowed, got '{head}'", error_kind="invalid_sql")
    if any(kw in tokenize(body) for kw in DENY):
        return ToolResult.failure("write operation rejected", error_kind="invalid_sql")
    return ToolResult.ok(sql)
```

The honest assessment, recorded rather than glossed: **this is not a parser.** It is a keyword check that can be defeated (a `DELETE` in a string literal, a CTE that writes in some dialects, comment-obfuscation, dialect-specific escapes), and the day must not pretend otherwise. Its job is to stop the *obvious* and to be the first of three layers; the **role** (§4) is what makes the seed safe, and the real parser + allow/deny is **Day 15**.

### Execution — limit, truncate, report

Two mechanical rules make the result *usable in the loop*:

1. **Row limit.** Every query is wrapped or checked against a `LIMIT` (config), so an aggregation that would return 100k rows returns a bounded page instead of pouring the table into the context budget.
2. **Truncation is reported.** The result carries `row_count` and `truncated: true|false`. A truncated result must be *stated* as truncated — because otherwise the explanation can claim completeness ("the top products are…") from a partial page. This is the same reflex as Day 9's compression honesty: a dropped thing must be *reported*, never silently lost.

The result is the day's evidence record, and it is shaped to sit beside the others:

```python
{"ok": True, "sql": "SELECT …",           # the visible artifact
 "columns": ["product", "orders", "revenue"],
 "rows": [["Product X", 412, "18240.00"], …],
 "row_count": 10, "truncated": True,      # honesty about completeness
 "elapsed_ms": 43.7}                      # the wall's measurement
```

One shape, four corpora — the harness grades top-K chunks, `module#Symbol` records, seeded data rows, and generated SQL rows with the same conventions, which is what keeps the contract decidable across the whole OS.

---

## 6. Result → Explanation — `asserted ⊆ returned`

The final turn is where a Data Agent lies most convincingly, because numbers *look* authoritative. The rule is the day's invariant: **`asserted ⊆ returned`** — every number, entity, and ordering the explanation asserts must appear in (or be a stated aggregate of) the result set. It is `cited ⊆ retrieved` translated from chunks to rows.

Three honest outcomes, each with a required shape:

| Result | The answer must | The failure it prevents |
|---|---|---|
| rows present | cite values from `rows` (X led with Z) | a headline figure the query never produced |
| rows empty | say "no data for that period" | an invented row (Day 13's `no_data`) |
| `truncated: true` | say "top N of more" | an unwarranted "all products…" |

The model may **compute a headline** — a sum over returned rows, a top-k of returned rows — but may not **invent a row** it did not receive. The guard is mechanical: parse the numbers/entities from the explanation and assert membership in the result (or membership in the set of aggregates over the result). It is the SQL sibling of the citation check, and it is `MECH` — a set-membership test over stored data, not a judgment call.

Why this matters more here than in RAG: in RAG a fabricated citation is often *plausible-but-wrong* text; in a Data Agent a fabricated number is *indistinguishable from a correct one* by inspection. The trace's `asserted ⊆ returned` column is the only thing that tells a reader whether the figure came from the database or from the model's imagination — and it is the whole reason the day insists the query stay visible and the rows stay in the transcript.

---

## 7. Test: The Data-Agent Question Set

Expected behavior written **before** the run, like every day since Day 7. The **Q** fixtures (for *query* — distinct from Day 7's A–E question families):

| ID | What it proves | Input | Expected |
|---|---|---|---|
| Q1 | aggregation (curriculum example) | "Which products generated the most revenue last month?" | generated SQL joins order lines → products over a window; rows returned; explanation numbers ⊆ result |
| Q2 | parameterized tool completed | "Fetch order 1" | `get_order(id="1")` hits the real DB; **no** `pending_resolver` in the result |
| Q3 | multi-table join | "Which customers placed the most orders?" | customers ⋈ orders; the FK in the schema card is what makes it writable |
| Q4 | empty result | "How many orders in 2099?" | `{ok: true, rows: []}`; answer says "no data"; zero fabricated rows |
| Q5 | read-only enforced | injected `DELETE FROM order_order` | validator rejects **before** execution; with the validator bypassed, the role refuses; nothing deleted |
| Q6 | SQL error repairs | query names a nonexistent column | `{error_kind: "unknown_column"}` observation → loop self-corrects → fixed query runs; no fabrication |
| Q7 | truncation honesty | query matching ≫ limit | `truncated: true`, `row_count` capped; explanation says "top N", not "all" |
| Q8 | grounding | any answered run | explanation numbers/entities ⊆ result set (`asserted ⊆ returned`) |

The `MECH`/`PENDING` split is exactly Day 7's. **MECH** — Q2, Q4, Q5, Q6's error mapping, Q7, and Q8's check — are pure functions of the read-only connection, the validator, the row-limit arithmetic, and stored SQL: deterministic, offline, free. **PENDING** — Q1's and Q3's *real-model* SQL generation and Q8's grounding under a *real* model — need the real gateway, and are Day 21's benchmark on Day 14's schema card. The stub proves the **seam** (SQL in, rows out, read-only held); a real key proves the **grounding** (does the model write the right query from the card).

Three obligations, unchanged in spirit:

1. **No regression on Days 7–13.** The Data Agent is **additive** — a fourth corpus behind the same harness. The doc nine, the code fixtures, T1…T7, A1…A8, and F1…F9 run unchanged and must stay exactly green. A data tool that moves a retrieval number is a bug wearing a feature; a data tool that moves a *guard* number (Day 13's `fabricated?`, timeout wall) is worse.
2. **Day 13's guards apply to the new tool by construction.** The DB query runs inside `execute_bounded` (timeout wall), SQL errors are typed `tool_error`s (the taxonomy), an empty result is `no_data` (not a failure), and a failed query never licenses a fabricated answer (the cardinal rule). F-fixtures are the proof that the *new* tool inherits the *old* defenses.
3. **Isolation, one owner per symptom.** Wrong table/column → **schema grounding** owns it. Valid SQL, wrong numbers → **generation** owns it (the model mis-read the rows). SQL fine, zero rows → **the data** owns it (a legitimate `no_data` finding, not a bug). Validator rejects a legal query → **the validator** owns it (a false rejection — the Day 15 seed being too strict). Say which, never vibes.

---

## 8. Measure: The Data Agent as a Contract

Rule-4 as a data table, one row per fixture, every cell a real run's:

| Question | Gen SQL | Validation | Executed | row_count | truncated | asserted ⊆ returned | error_kind | elapsed_ms | Cost |
|---|---|---|---|---|---|---|---|---|---|
| Q1 revenue by product | y | `select-only` | y | … | … | **ok** | — | … | … |
| Q2 fetch order 1 | n (param) | — | y | 1 | false | **ok** | — | … | … |
| Q3 top customers | y | `select-only` | y | … | … | **ok** | — | … | … |
| Q4 orders in 2099 | y | `select-only` | y | 0 | false | **ok** | — | … | … |
| Q5 DELETE injected | — | **rejected** | **n** | — | — | **ok** | `invalid_sql` | 0 | 0 |
| Q6 bad column | y | `select-only` | n→y | … | … | **ok** | `unknown_column` | … | … |
| Q7 huge result | y | `select-only` | y | limit | **true** | **ok** | — | … | … |
| Q8 grounding | — | — | — | — | — | **ok** | — | — | — |

The **`asserted ⊆ returned`** column is the day's headline and the one that never varies: a `fail` anywhere invalidates the run, exactly as `fabricated?` did on Day 13. The **Gen SQL** column is the artifact Day 15 attacks, and the **truncated** column is where an honest agent distinguishes a page from a whole.

The cost shape, recorded honestly because it **differs from every retrieval day**: a data answer costs **0 embeds + 2 chats** (generate SQL, explain rows; +1 chat if the loop repairs a query), versus retrieval's **1 embed + 1 chat**. The reason is architectural, not incidental — the DB does **exact set logic**, so no embedding is needed to *find* the data; the two chats are the query and the prose. The new recurring cost is the **schema card's per-step input tokens** (a long, stable prefix), which belongs beside the embed counts so both currencies — dollars and context — are visible on the same page.

The negatives column, with owners: the Data Agent cannot yet do safely (DELETE/UPDATE, bomb queries, nonexistent tables, ambiguous definitions — **Day 15**); the schema card cannot yet scale to the whole DB (schema *retrieval* — **Day 16/19**); and whether a real model grounds its SQL and its explanation is **Day 21**. Empty negatives mean the day learned nothing; these have owners, not wishes.

---

## 9. Documents

- `docs/concepts/text-to-sql.md` — structured data as a third corpus, schema grounding, the schema card + glossary, the pipeline-as-a-tool (generation = the loop's chat), read-only defense in depth (role + read-only txn + validator, first cut), and `asserted ⊆ returned`.
- `docs/evaluations/data-day14.md` — the §8 data table, the Q1…Q8 pre-registered fixtures, the read-only guard assertions (validator + role independently), the cost shape (0 embeds + 2 chats + schema tokens), and the no-regression lines (Days 7–13 fixtures unchanged).
- PROGRESS rollup: Day 14 mechanics move to `DONE` on the stub (read-only seam, schema card, validator seed, result shape, row-limit/truncation, `asserted ⊆ returned` check verified offline); the real-model SQL-generation cells stay `PARTIAL` until a real gateway run confirms the model writes grounded queries from the card.

---

## 10. Daily Learning Record

### Problem

Four docs-days and four code-days of retrieval share a blind spot: neither can **add up a column**. "Which products generated the most revenue last month?" has no nearest chunk and no nearest symbol; the answer is a `SUM` over a join, and it is *computable*, not *findable*. The two data tools that could begin to answer it — `get_product/order/customer` — have been seeded stubs since Day 11, each tagged `pending_resolver: "day14"`. The problem is to add the third corpus (Saleor's Postgres data), give the model enough schema to **write its own query**, keep that query **read-only** without pretending a keyword check is a wall, and make the explanation **grounded in the rows** — because a fabricated statistic is indistinguishable from a correct one by inspection.

### Concept

**The loop is the pipeline, and the schema is the new corpus metadata.** Text-to-SQL's curriculum pipeline (Question → Schema → SQL → Validation → Database → Result → Explanation) maps *stage for stage* onto machinery that already exists: generation is the loop's own chat (SQL as `tool_calls.arguments`), validation is the Day 11 registry seam, execution sits inside Day 13's timeout wall, and self-correction (Day 12/13) becomes **query repair** — which is *why* the SQL must stay visible rather than hidden in a black-box tool. Grounding is the accuracy lever, and it is the continuation of one discipline across four audiences (Day 8 wrote for a scorer, Day 11 for a chooser, Day 14 for a generator): the **schema card** (tables · columns+types · FKs · dialect · a **business-definition glossary**) is what lets a model write the right join and the right window, and the glossary is what stops two runs from meaning two different "revenues". Read-only is **defense in depth** — the validator (`SELECT`/`WITH` only, first cut), a read-only transaction, and the platform's existing `saleor_read_only` role as the certain last line. And the honesty invariant is **`asserted ⊆ returned`**: every number in the explanation traces to a row (or a stated aggregate), the SQL sibling of `cited ⊆ retrieved`. The day's genuine novelty is small and stated: a driver dependency, a schema card, and one new invariant.

### Understanding

The unlock is that **Day 14 changes the corpus and the evidence shape, not the control flow.** The agent loop, the registry, the guards, and the gateway are untouched; `run_saleor_sql` is just another tool, and the "pipeline" is the loop's existing steps read from a new angle. The second understanding is that **visibility is a safety property**: keeping the query on the table is not a debugging nicety — it is what lets the Day 13 self-correction *repair* SQL, what lets the trace *grade* it, and what gives Day 15 something real to *attack*. The third, the honest one: the mechanical half (read-only holds at the role, the validator rejects writes, rows/truncation/`no_data` behave, `asserted ⊆ returned` passes) is green under the stub, while whether a *real* model writes a correct, grounded query from the schema card — the curriculum's example — is `PENDING` until a real gateway, and is Day 21's measure. And the day's most dangerous mistake is not a loose validator but a *confident number* the database never produced.

### Implementation

- `ai_os/data.py` (new) — the read-only seam: `SALEOR_READONLY_DB_URL`, `psycopg` connection, read-only transaction, `schema_card()` (curated tables + FKs), the business-definition glossary, `validate_readonly(sql)` (first cut), and `execute_readonly(sql)` returning `{ok, sql, columns, rows, row_count, truncated, elapsed_ms}`; wraps `execute_bounded` (Day 13).
- `ai_os/tools.py` (extended) — `run_saleor_sql(sql)` and `describe_saleor_schema(table)` registered; `get_product/order/customer(id)` re-pointed at real parameterized queries and their `pending_resolver` result field removed.
- `ai_os/agent.py` — **unchanged**; the loop drives the data tools with the same Day 11–13 machinery (the pipeline is the loop).
- `ai_os/trace.py` (extended) — data columns: `gen_sql`, `validation`, `executed`, `row_count`, `truncated`, `asserted_subset_returned`, plus the existing `failure_kind`/`elapsed_ms`.
- `tools/data_smoke.py` (new) — offline suite for Q1–Q8 (mechanical half), the two read-only guard assertions, and the Days 7–13 no-regression run.
- `docs/concepts/text-to-sql.md`, `docs/evaluations/data-day14.md`.

### Failure

1. **The fabricated statistic (the day's cardinal sin).** A query returns 10 rows; the explanation adds a headline the rows do not support ("revenue was roughly €250k", where the rows sum to €18k, or a number from nowhere). It is the Day 8 fabrication bug wearing figures, and it is *harder to catch by eye* than a bad citation — which is exactly why `asserted ⊆ returned` is a trace column and not a reviewer's judgment.
2. **The keyword validator mistaken for a parser.** The seed rejects `DELETE` and everyone relaxes — until a comment-obfuscated or nested statement passes. The guard is the *role* (layer 3); the honest record is that the seed is defeatable and Day 15 replaces it.
3. **The hidden query.** A summarizer tool that returns only "the answer" hides the SQL, so self-correction cannot repair it, the trace cannot grade it, and Day 15 has nothing to attack. Visibility is a design requirement, not a preference.
4. **Truncation read as completeness.** A `LIMIT`ed page is reported as if it were the whole answer ("your top customers are…" from 10 of 4,000 rows). The `truncated` flag exists so the explanation is *forced* to say "top N of more".
5. **`no_data` treated as failure.** Zero rows sends the agent into retries or a fabricated answer, when "no orders in 2099" is the correct, honest result (Day 13's `no_data` is a finding, not an error).
6. **The wrong schema grounded on.** Grounding on `schema.graphql` (GraphQL types) instead of the DB schema produces SQL against types that are not tables — an unknown-table error that looks like a model failure and is a *grounding* failure.

### Debugging

Everything Day 14 adds is `MECH` under the stub: the validator is string arithmetic, the read-only refusal is a privilege assertion, the row-limit/truncation is a slice, the error mapping is a lookup, and `asserted ⊆ returned` is set membership over stored rows. The debug loop is Day 7's, re-rooted onto the data owners:

- **Unknown table/column error** → **schema grounding** owns it (the card is missing a table/FK, or the model read it wrong) — read the card, not the model.
- **Valid SQL, wrong numbers** → **generation** owns it (the model mis-stated the rows) — the `asserted ⊆ returned` check localizes it.
- **SQL fine, zero rows** → **the data** owns it (a legitimate `no_data` finding) — not a bug; the answer must say so.
- **Legal query rejected** → **the validator** owns it (too strict — a Day 15 tuning problem, and a false-positive on the *safety* side is a real bug too).
- **A write got through** → the **role** should have stopped it; if it did not, the connection is not read-only — the one failure that must be impossible.
- **The query was slow** → the **timeout wall** owns it (Day 13), measured by `elapsed_ms` against `tool_timeout_s`.

The trace says which, never the vibes; the data column is the unit of attribution, and it has an owner from the day it was designed.

### Evaluation

The §8 data table in `docs/evaluations/data-day14.md`: validation verdict rate (did non-`SELECT` get rejected?), read-only assertions (validator *and* role, independently), row-limit/truncation behavior, `no_data` handling (must not fabricate), SQL-error-to-`error_kind` mapping accuracy, and — the headline — `asserted ⊆ returned = ok` on every answered row. The numbers a future observer can carry: whether any write ever executed (it must not), whether any truncated result was reported as complete (it must not), whether the validator ever falsely rejected a legal query, and whether adding a fourth corpus moved any of Days 7–13's numbers (it must not). The `PENDING` line, plainly: whether a real model writes correct SQL from the schema card, and whether it grounds its explanation in the rows, is Day 21's judgment on Day 14's card and tool.

### Architecture

The claim of the day is the same claim as Days 10–13, one corpus later: **the system took a new capability — computation over structured data — and stayed small.** The pipeline is the loop; the tool is the registry's seam; the guard is Day 13's wall; the evidence is one more shape beside the ones already graded; and the honesty invariant is `cited ⊆ retrieved` translated to rows. The genuinely new pieces are a driver dependency, a **schema card**, and a first-cut validator — all of which are *data*, not architecture. Read-only is defense in depth because the day knows a keyword check is not a wall: the *certain* guard is a database *privilege* the platform already provisioned, which is the strongest possible form of "fail closed". The seams are named and owned: Day 15 hardens the validator into a parser with allow/deny, row limits, and cost guards; Day 16/19 add schema *retrieval* for a card that cannot fit the whole DB; and Day 21 rules on whether a real model writes grounded SQL. **Day 14 added a corpus and an invariant; the loop Day 12 built and the guards Day 13 hung are what made a database tool a small change instead of a subsystem.**

### Questions

- Is `asserted ⊆ returned` checkable in general — can every number in prose be traced to a row — or does an honest answer legitimately *derive* figures the check will flag, and where is that line?
- A keyword allow-list is not a parser; is a real SQL parser (Rule-1 vs a vetted library) the right Day 15 move, or is the *read-only role* genuinely sufficient so the parser is defense-in-depth rather than the load-bearing wall?
- The schema card is a long, stable prefix billed on every step; should schema grounding move to a retrieved subset (schema RAG) even for Day 14's core tables, and at what table count does curation stop scaling?
- Two chats per data answer (generate + explain) — should the *glossary* live in the schema card (paid every step) or be retrieved on demand, and does that change the cost shape enough to matter across Day 30's scenarios?
- When a query returns zero rows, is "no data" always right, or is it sometimes a *grounding* failure (the model queried the wrong table and found emptiness honestly) — and can the trace tell those apart?

### Tomorrow

Day 15 — Safe Text-to-SQL: the day attacks what Day 14 built. The curriculum's adversary is explicit — **DELETE queries, UPDATE queries, invalid SQL, expensive queries, nonexistent tables, ambiguous business definitions** — and the build is the hardened **safe SQL execution layer**: a real parser/validator, the read-only connection enforced deeply, query timeouts, row limits, and allow/deny rules. Day 14 hands it the target: a *visible* generated query, a *seed* validator whose limits are honestly recorded, a read-only role as the last line, and a schema card whose glossary the ambiguity attack will try to pry apart. Day 13's guards (timeout wall, `ToolError` taxonomy, `fabricated?`) and Day 14's invariant (`asserted ⊆ returned`) run as-is; the hardening lands in the execution layer, not the loop. Day 14 was where the OS learned to *compute*; Day 15 is where the computation learns to *defend itself*.
