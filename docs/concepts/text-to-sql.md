# Concept: Structured Data Is a Third Corpus (Text-to-SQL)

> Day 14 of the AI-layer apprenticeship. Days 6–13 built retrieval (find by
> similarity), tools (declarative actions), and the loop that drives them — and
> left one class of question unanswerable: **aggregation over rows**. "Which
> products generated the most revenue last month?" has no nearest chunk and no
> nearest symbol; it has a `SUM` over a `JOIN`. Day 14 adds the third corpus —
> **Saleor's Postgres database** — and the first tool that **writes its own
> query**, grounded on a **schema card**, kept **read-only** by defense in depth,
> and honest by one invariant: **`asserted ⊆ returned`**.
> Implementation: `ai_os/data.py` (new), `ai_os/tools.py` (two data tools +
> real resolvers), `ai_os/trace.py` / `ai_os/agent.py` (data columns + the
> invariant), `tools/data_smoke.py`.

## Problem

Four docs-days and four code-days of retrieval share one blind spot: neither can
**add up a column**. The question classes are disjoint — not "harder", different
in kind:

| Question | Corpus | Mechanism | Evidence |
|---|---|---|---|
| what the docs say | markdown pages | vector + lexical fusion | top-K chunks (`doc_id`) |
| where it is implemented | source symbols | symbols + filters | `module#Symbol` |
| **how much / how many / which rows** | **Postgres tables** | **exact set logic (SQL)** | **columns + rows** |

The third class is disjoint because its answer is a **computation**: "revenue last
month" is `SUM(quantity × unit_price)` over a date window, grouped by product. No
chunk contains that number, and the correct answer depends on the *rows*, not on
the *text about the rows*. A retrieval tool can return the sentence that defines
revenue; only SQL can return the revenue.

The tools that could begin to answer it — `get_product` / `get_order` /
`get_customer` — have been seeded stubs since Day 11, each tagged
`pending_resolver: "day14"`. Day 14 fills them and adds the open tool.

## Schema grounding — the schema is the new corpus metadata

Text-to-SQL accuracy is mostly a **grounding** problem: the model cannot query a
column it was never told exists. Day 14 continues a discipline that now spans
three audiences:

| Day | Metadata written for… | The artifact |
|---|---|---|
| 8 | a **scorer** (retrieval) | the stopword-lean query |
| 11 | a **chooser** (tool selection) | the tool description |
| 14 | a **generator** (SQL) | the **schema card** (+ glossary) |

The card (`ai_os/data.py: SCHEMA_CARD`) carries a curated core subset — seven
tables: `order_order`, `order_orderline`, `product_product`,
`product_productvariant`, `product_productvariantchannellisting`,
`account_user`, `channel_channel` — with each table's purpose, its columns +
types, and its **foreign keys** (the joins). It fits the default budget (3,553
chars < 4,000) and, over budget, marks the cut rather than truncating silently.

**The business-definition glossary is the semantic half.** Saleor is
multi-channel and multi-currency, so "revenue" is not a column, it is a
*decision*. Left to the model, every run would resolve it differently; the
glossary (`GLOSSARY`) resolves it **deterministically and separately from the
model**:

- **revenue** = `SUM(order_orderline.total_price_net_amount)`, net of tax and
  discounts, in the order's currency, per channel;
- **last month** = the previous full calendar month **in UTC** (today is
  2026-09-25, so `>= '2026-08-01' AND < '2026-09-01'`);
- **customer** = an `account_user` with ≥ 1 order (via the join);
- **order** = a row in `order_order`; `status = 'DRAFT'` is excluded from revenue.

### The two-schema split-brain

Saleor exposes a **GraphQL schema** (`schema.graphql`, the API's public shape)
*and* a **Postgres schema** (the Django tables' private shape), and they are
**not the same artifact**. A mutation named `CheckoutComplete` is a GraphQL type;
its rows live in tables named `order_order`, `order_orderline`, `product_*`. The
Data Agent queries the DB, so it is grounded on the **database schema** — never
on `schema.graphql`, or the model writes `SELECT … FROM CheckoutComplete` and
meets an unknown-table error that is a *grounding* bug, not a model bug. This is
Day 10's split-brain lesson one level down; the suite asserts the card names DB
tables and contains no GraphQL type names.

## The pipeline is the loop

The curriculum's pipeline — Question → Schema → SQL → Validation → Database →
Result → Explanation — maps **stage for stage** onto machinery that already
exists, so Day 14 invents no control flow:

```text
Question   → the loop's user message
Schema     → the tool description (the schema card, budget-clamped)
SQL        → the model's tool_calls.arguments  (the loop's own chat generates it)
Validation → inside the tool executor (Day 11's registry seam)
Database   → the read-only connection, inside Day 13's execute_bounded wall
Result     → {ok, sql, columns, rows, row_count, truncated, elapsed_ms}
Explanation→ the loop's final turn, grounded by asserted ⊆ returned
```

`run_saleor_sql(sql)` registers like `search_saleor_code`; the observation is the
rows; the explanation is the final answer. **The loop is the pipeline.**

### Two shapes of data tool

| Tool | Query | Safety | Role |
|---|---|---|---|
| `get_product/order/customer(id)` | **fixed** (one parameterized query, one validated `id`) | safe by construction | the common lookups; fills the Day 11 stubs |
| `run_saleor_sql(sql)` | **generated** by the model | open — Day 15's target | aggregation, joins, arbitrary set logic |

The trace distinguishes them: both records carry `query_kind` (`fixed` /
`generated`), and `res["gen_sql"]` lists only the *generated* queries.

### Why the query stays visible

A tempting design hides Text-to-SQL behind one black-box tool —
`ask_the_database(question)` — that does schema → SQL → execute → explain
internally. Day 14 rejects it: **visibility unlocks self-correction.** If the SQL
is the model's own tool argument, then a SQL error (`unknown_column`) is a typed
**observation** the model reads and *repairs* with a fixed query — Day 12/13's
self-correction bound applied to query repair. A hidden query cannot be repaired,
graded, or attacked.

## Read-only — defense in depth, first cut

Three layers, cheapest first, most certain last:

| Layer | Mechanism | Holds when… | Owner |
|---|---|---|---|
| 1 (first) | the **validator** — `SELECT`/`WITH` only | the model tried to write | the executor |
| 2 | a **read-only engine** (`PRAGMA query_only` on the stub; `default_transaction_read_only` on Postgres) | layer 1 was bypassed | the connection |
| 3 (last) | the **`saleor_read_only` role** — no write grant exists (`replica_user.sql`) | everything above failed | the database |

The ordering is deliberate: reject a `DELETE` before it reaches the DB (fast,
cheap), and let the *certain* guard be a database **privilege** the platform
already provisions — so read-only is not a hope that the validator is correct.

### The validator is a seed, and says so

`validate_readonly` is a **keyword check, not a parser.** It strips comments and
string literals, rejects stacked statements on `;`, requires a `SELECT`/`WITH`
head, and denies DML/DDL keywords. It can be defeated (dialect tricks, a writing
CTE), and the honest record is that the **role** (layer 3) is what keeps the seed
safe. The real parser, allow/deny rules, row-cost guards, and `EXPLAIN` are
**Day 15's**; Day 14 ships validation strong enough to keep the *read-only agent
honest*, not strong enough to survive an *attacker*.

### Latency — the first real use of Day 13's wall

The database is the first tool with **real latency**. The query runs inside
`execute_bounded`, so a slow query becomes `{error_kind: "timeout", retryable:
true}` — an observation, never a hung run — with `elapsed_ms` recording the true
cost. Postgres additionally enforces a `statement_timeout`.

### The error taxonomy, in SQL

| DB condition | `error_kind` | The loop's move |
|---|---|---|
| wrong table/column | `unknown_column` / `unknown_table` | **repair** (a grounding fix) |
| syntax error / validator refusal | `invalid_sql` | **repair** (or **stop** if it was a write) |
| permission denied (a write) | `invalid_sql` (validator) / DB error | **stop** — Day 15's adversary |
| slow query | `timeout` | **fallback** — narrow or skip |
| zero rows | **not an error** — `{ok: true, rows: [], note: "no rows"}` | **explain honestly** |

The last row is the one people get wrong: an empty result is a **finding**, not a
failure. "How many orders in 2099?" has the correct answer *zero*.

## `asserted ⊆ returned`

The final turn is where a Data Agent lies most convincingly, because numbers
*look* authoritative. The rule is the day's invariant: **`asserted ⊆ returned`** —
every number, entity, and ordering the explanation asserts must appear in (or be
a stated aggregate of) the result set. It is `cited ⊆ retrieved` translated from
chunks to rows, and the failure it guards — "approximately 1,240 orders" the
query never produced — is *harder to catch by eye* than a bad citation, because a
fabricated statistic is indistinguishable from a correct one by inspection.

Three honest outcomes, each with a required shape:

| Result | The answer must | The failure it prevents |
|---|---|---|
| rows present | cite values from `rows` | a headline the query never produced |
| rows empty | say "no data for that period" | an invented row |
| `truncated: true` | say "top N of more" | an unwarranted "all products…" |

The guard is mechanical (`ai_os/data.py: asserted_subset_returned`): parse the
numbers and emails from the explanation and assert membership in the returned
cells (plus the stated aggregates sum/max/min/count per numeric column). Recorded
limits, honestly: it is number/email-shaped (prose that ignores the evidence is
not caught) and it *over-flags* derived figures (a percentage the rows imply but
do not contain) — the conservative direction, which is the one a guard should err.

## Documents and seams

- New: `ai_os/data.py` — the read-only seam (`SqliteDataStore` /
  `PostgresDataStore`, `schema_card_text`, `GLOSSARY`, `validate_readonly`,
  `DataAgent`, `asserted_subset_returned`, `SEED_SQL`).
- Extended: `ai_os/tools.py` (the three resolvers + `run_saleor_sql` +
  `describe_saleor_schema`), `ai_os/trace.py` (`data_columns`, `generated_sqls`),
  `ai_os/agent.py` (the run's `gen_sql` / `data_records` /
  `asserted_subset_returned`).
- Suite: `tools/data_smoke.py` (Q1–Q8 + the read-only guards + no-regression).
- Evaluation: `docs/evaluations/data-day14.md`.

**Named seams, not built:** the hardened parser + allow/deny + cost guards are
**Day 15**; scaling the card to the whole DB is **schema retrieval** (the Days
8–10 machinery applied to tables), **Day 16/19**; and whether a real model writes
a correct, grounded query from the card is **Day 21's** benchmark on Day 14's
card and tool.
