# Data Agent Day 14 — `asserted ⊆ returned`

> Generated from the run of `python tools/data_smoke.py` (offline: the Day-4
> scripted stub for the *chat* wire — scripted turn by turn so the "model" emits a
> `run_saleor_sql` call — the Day-7 doc corpus / Day-10 code corpus for
> *retrieval*, and the **seeded in-memory sqlite core subset** in `ai_os/data.py`
> for the *data* side, with `PRAGMA query_only` as the read-only engine).
> `MECH` = provable offline, here. `PENDING` = a real gateway is required. The
> headline invariant is the `asserted ⊆ returned` column: it reads **`ok`** on
> every answered row, and the one deliberate `FAIL` is the fabricated twin.

```
stores: 9 doc chunks (STUB) / 14 code symbols (STUB)   τ_doc=0.05 τ_code=0.75
data: seeded core subset (STUB, sqlite query_only)   card=3553 chars   row_limit=100
registry: search_saleor_docs, search_saleor_code, get_product, get_order,
          get_customer, run_saleor_sql, describe_saleor_schema
```

## The fixtures, pre-registered before the run (§7)

| ID | What it proves | Input | Expected |
|---|---|---|---|
| Q1 | aggregation (curriculum example) | "Which products generated the most revenue last month?" | generated SQL joins order lines → variants → products over the Aug window; rows returned; explanation numbers ⊆ result |
| Q2 | parameterized tool completed | "Fetch order 1" | `get_order(id="1")` hits the store; **no** `pending_resolver` |
| Q3 | multi-table join | "Which customers placed the most orders?" | `account_user ⋈ order_order`; the FK in the card is the join |
| Q4 | empty result | "How many orders were placed in 2099?" | `{ok: true, rows: []}`; answer says none; zero fabricated rows |
| Q5 | read-only enforced | injected `DELETE FROM order_order` | validator rejects **before** execution; with the validator bypassed, the engine refuses; nothing deleted |
| Q6 | SQL error repairs | query names a nonexistent column | `{error_kind: "unknown_column"}` → the loop self-corrects → fixed query runs |
| Q7 | truncation honesty | a query matching ≫ the row limit | `truncated: true`, `row_count` capped; explanation says "top N", not "all" |
| Q8 | grounding | any answered run | explanation numbers/entities ⊆ result set (`asserted ⊆ returned`) |

The `MECH`/`PENDING` split is exactly Day 7's. **MECH** — Q2, Q4, Q5, Q6's error
mapping, Q7, and Q8's check — are pure functions of the read-only connection, the
validator, the row-limit arithmetic, and stored SQL: deterministic, offline, free.
**PENDING** — Q1's and Q3's *real-model* SQL generation and Q8's grounding under a
*real* model — need the real gateway, and are **Day 21's** benchmark on Day 14's
schema card. The stub proves the **seam** (SQL in, rows out, read-only held); a
real key proves the **grounding**.

## §8 data table (every cell a real run's)

| Q | What | Gen SQL | Kind | Validation | Executed | row_count | truncated | `asserted ⊆ returned` | error_kind | Owner | db_ms | Cost |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Q1 | revenue by product | y | generated | select-only | y | 3 | no | **ok** | — | seam | 0.18 | $0.00003060 |
| Q2 | fetch order 1 | n | fixed | select-only | y | 1 | no | **ok** | — | seam | 0.14 | $0.00003060 |
| Q3 | top customers | y | generated | select-only | y | 3 | no | **ok** | — | seam | 0.13 | $0.00003060 |
| Q4 | orders in 2099 | y | generated | select-only | y | 0 | no | **ok** | — | seam | 0.10 | $0.00003060 |
| Q5 | DELETE injected | y | generated | **rejected** | **n** | 0 | no | **ok** | `invalid_sql` | validator + engine | 0.00 | $0.00003720 |
| Q6 | bad column | y | generated | select-only | y | 0 | no | **ok** | `unknown_column` | schema grounding | 0.00 | $0.00003720 |
| Q7 | lines ≫ limit | y | generated | select-only | y | 3 | **yes** | **ok** | — | seam | 0.03 | $0.00003060 |
| Q8 | grounding (fabricated twin) | y | generated | select-only | y | 3 | no | **FAIL** | — | generation | 0.08 | $0.00003060 |

**`asserted ⊆ returned = ok` on every answered row** (Q8 is the deliberate
negative twin); a `fail` on any *other* answered row invalidates the run, exactly
as `fabricated?` did on Day 13. The **Gen SQL** column is the artifact Day 15
attacks. Q6's row is the *failed* first call (step 2 self-corrects and succeeds);
a failed query's `db_elapsed_ms` is not recorded — an honest gap noted below.

## What the run actually produced

**Q1 — aggregation (the curriculum example).** The generated SQL is a four-table
join — `order_orderline l JOIN order_order o … JOIN product_productvariant v …
JOIN product_product p …` — with `WHERE o.status <> 'DRAFT' AND o.created_at >=
'2026-08-01' AND o.created_at < '2026-09-01'` and `GROUP BY p.name`: the window and
the DRAFT exclusion are the **glossary's** ("last month", "order"), read from the
card. Three products return (Desk Lamp 96.0, Blue T-Shirt 59.97, Coffee Mug 38.0),
`res["gen_sql"]` carries the query, and the grounded answer's numbers are all in
the result set.

**Q2 — parameterized tool completed.** `get_order("1")` runs a **fixed** query
(`query_kind: fixed`) against the real store and returns order 1001 /
UNFULFILLED / 59.38 — and the record carries no `pending_resolver`: the Day 11
stub is *filled*, its interface unchanged. This is the safe-by-construction path;
the trace separates it from the generated one by `query_kind`.

**Q3 — multi-table join.** `FROM account_user u JOIN order_order o ON o.user_id =
u.id GROUP BY u.email` returns three customers (ada 2, grace 2, alan 1). The FK in
the card (`order_order.user_id → account_user.id`) is what made the join
writable; the answer's emails are returned cells.

**Q4 — empty result (a finding, not a failure).** The 2099 query returns
`{ok: true, rows: [], note: "no rows"}`, `row_count: 0`, and the answer says
"no orders … 0 rows" with **zero fabricated rows**. The day's rule, in SQL: an
empty result is the correct answer *zero*.

**Q5 — read-only enforced (two independent guards).**
- **Guard 1 (validator).** The injected `DELETE FROM order_order` is rejected
  *before* execution: `validation: rejected`, `executed: false`, typed
  `invalid_sql`. The DELETE is still recorded in `gen_sql` (visible and gradable).
- **Guard 2 (engine).** With `validate=False`, the same SQL reaches the engine and
  is refused — `attempt to write a readonly database` — `executed: true`, nothing
  deleted (`COUNT(*) = 5` before and after). The offline stand-in for the Postgres
  `saleor_read_only` role + read-only transaction: **layer 2 holds when layer 1
  is bypassed.**
- The validator matrix (10 cases) confirms the seed's shape: `SELECT`, `WITH`,
  and a `'delete me'` string literal are allowed; `INSERT` / `UPDATE` / `DELETE` /
  `DROP` / `TRUNCATE` / stacked `SELECT 1; DROP …` are rejected.

**Q6 — SQL error repairs (schema grounding owns it).** `SELECT order_total FROM
order_order` returns `{error_kind: "unknown_column", executed: true}` — a typed
observation, not a crash — and the loop *self-corrects* on step 2 with a
**different** query (`COUNT(*) … status <> 'DRAFT'`) that succeeds. No fabrication
on the failure path. A wrong column and a syntax error are different `error_kind`s
because they send the model to different places in the card.

**Q7 — truncation honesty.** With `row_limit=3`, the 7-row query returns
`row_count: 3, truncated: true` — the cap is **measured** (the store fetched
`limit + 1` and saw a 4th row), not guessed from a wrapped `LIMIT`. The
explanation says it is a page ("truncated … first 3"), so a `LIMIT`ed result is
never reported as the whole.

**Q8 — `asserted ⊆ returned` (the headline).** The fabricated answer ("revenue was
roughly 250000") fails: `250000 ∉ returned` → the run's
`asserted_subset_returned` is **False** with `250000` in `asserted_unsupported`.
The grounded answer on the *same* query is **True** — the guard is not vacuous.

## Schema grounding assertions

| Assertion | Result |
|---|---|
| the seven core tables are named in the card | ok |
| the join FKs are stated (`FK → product_product.id`, `FK → account_user.id`) | ok |
| the glossary is present (revenue · units sold · order · last month · customer · channel · product performance) | ok |
| it grounds on DB tables, **not** GraphQL types (`CheckoutComplete` / `schema.graphql` absent) | ok |
| the curated card fits the default budget (3,553 < 4,000 — no silent truncation) | ok |
| over budget, the cut is **marked** | ok |
| `describe_saleor_schema` expands a known table; an unknown table is not invented | ok |

## Cost shape (two currencies, both named)

| Event | Currency that moved |
|---|---|
| a data answer | **0 embeds + 2 chats** (generate SQL, explain rows) — the DB does exact set logic, so no vector is needed to *find* the data |
| a repaired query (Q6) | **+1 chat** (the loop's self-correction re-issues a fixed query) — $0.00003060 → $0.00003720 |
| the schema card | a **long, stable input prefix** on every step (3,553 chars) — the day's new recurring cost, beside the embed counts |
| a slow query | `db_elapsed_ms` against Day 13's `tool_timeout_s` (a wall, not a bill) |

The data answer's shape **differs from every retrieval day**: retrieval is
**1 embed + 1 chat**; a data answer is **0 embeds + 2 chats + schema tokens**.

## No-regression line (Days 11–13 ride the same harness)

| Line | Result |
|---|---|
| `tools/tool_smoke.py` unchanged in a subprocess | **exits 0 — `ALL DAY 11 (MECH) CHECKS PASSED`** |
| `tools/agent_smoke.py` unchanged in a subprocess | **exits 0 — `ALL DAY 12 (MECH) CHECKS PASSED`** |
| `tools/failure_smoke.py` unchanged in a subprocess | **exits 0 — `ALL DAY 13 (MECH) CHECKS PASSED`** |

One deliberate Day 11 edit, recorded: T3's assertion changed from "the fixture
says `pending_resolver: day14`" to "the real resolver returned order 1, no
`pending_resolver`" (the stub tag is *supposed* to come off), and the registry
now has **seven** tools, not five. A data tool that moved a *retrieval* number, or
a *guard* number (`fabricated?`, the timeout wall), would be a bug wearing a
feature — none moved.

## Config knobs (Day 14)

| Env | Default | Meaning |
|---|---|---|
| `SALEOR_READONLY_DB_URL` | unset | the real `saleor_read_only` DSN; unset → the offline seeded stub |
| `SALEOR_SQL_ROW_LIMIT` | `100` | rows per query; truncation is reported |
| `SALEOR_SQL_SCHEMA_BUDGET` | `4000` | chars of schema card (the budget reflex, on a schema) |

## Negatives (honest, with owners)

1. **The validator is a keyword check, not a parser** — defeatable (dialect
   tricks, a writing CTE). The certain guard is the **role** (layer 3). Owner:
   **Day 15** (real parser + allow/deny + cost guards + `EXPLAIN`).
2. **`asserted ⊆ returned` is number/email-shaped** — it over-flags derived
   figures (a percentage the rows imply but do not contain) and does not catch
   *prose* that ignores the evidence. Conservative by design. Recorded, not hidden.
3. **The stub cannot represent real latency** — a real slow query and real DB
   errors arrive only with the live role. Owner: **re-run with
   `SALEOR_READONLY_DB_URL` set** (the seam is built; the driver is declared).
4. **A failed query's `db_elapsed_ms` is not recorded** (only a successful one's)
   — cosmetic, fixable in Day 15's executor.
5. **Whether a real model writes the right join and the right window** for Q1/Q3,
   and whether it grounds Q8 under a real model, is `PENDING` until a real
   gateway. Owner: **Day 21** — the benchmark on Day 14's card and tool.
6. **The schema card cannot scale to the whole DB** — a full dump neither fits
   the context nor helps. Owner: **schema retrieval, Day 16/19**.

## Re-verify, in one line

```bash
python tools/data_smoke.py     # Q1–Q8 + the read-only guards + A1…A8 / T1…T7 / F1…F9 re-run
```

`PENDING`: whether a *real* model writes a correct, grounded query from the schema
card — Day 21's judgment on Day 14's data agent. The one column that never varies
is `asserted ⊆ returned = ok` (the fabricated twin excepted).
