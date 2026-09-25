"""Day 14 §1/§4/§5 — the third corpus: Saleor's structured data, read-only.

Days 6–10 made text *findable*: a query is embedded and the nearest chunks win.
Day 14 adds the corpus that cannot be found by similarity — "which products
generated the most revenue last month" has no nearest chunk, it has a `SUM`
over a `JOIN`. The answer is a **computation**, so the mechanism changes from
`embed → compare` to **exact set logic (SQL)** and the evidence record changes
from `top-K chunks` to `columns + rows`.

Three decisions carry the module:

1. **The schema is the corpus metadata, and it is the *database* schema.**
   Day 8 wrote stopword-lean queries for a *scorer*, Day 11 wrote descriptions
   for a *chooser*; Day 14 writes the **schema card** for a *generator* — the
   tables, columns+types, foreign keys (the joins), and a business-definition
   **glossary** ("revenue", "last month", "customer", "channel"). Saleor has two
   schemas — `schema.graphql` (the API's public shape) and the Postgres tables
   (its private shape) — and they are not the same artifact: grounding on the
   GraphQL names produces SQL against types that are not tables, an
   unknown-table error that is a *grounding* bug, not a model bug.
2. **Read-only is defense in depth, not a hope.** Layer 1 is the validator
   (`SELECT`/`WITH` only — a keyword check, and this module says so honestly);
   layer 2 is an engine-enforced read-only *engine* (`PRAGMA query_only` on the
   offline store, `default_transaction_read_only` on Postgres); layer 3 is the
   platform's existing **`saleor_read_only` role** (`replica_user.sql`), which
   has no `INSERT`/`UPDATE`/`DELETE` grant to exercise. Every layer fails
   closed, and the cheapest check runs first. Day 15 hardens layer 1 into a
   real allow-list parser + policy (`ai_os/sql_safety.py`) and adds the missing
   *server-side* timeout beside Day 13's client wall; layer 3 stays the total
   guarantee, and the parser stays best-effort.
3. **The store is a seam, not a driver.** Two backends implement one interface:
   `SqliteDataStore` (stdlib, seeded, in-memory — the offline `MECH` engine) and
   `PostgresDataStore` (lazy `psycopg` — the real thing behind
   `SALEOR_READONLY_DB_URL`). `psycopg` is imported only when a real URL is
   configured, so the offline suite never needs the day's one new dependency.

Honesty about the seed, recorded rather than glossed: the offline store's rows
are a **curated stub** of Saleor's core tables (`order_*`, `product_*`,
`account_user`, `channel_channel`), not a dump. The schema card is matched to
that stub exactly; reconciling it against a live `information_schema` is the
Day 15/16 seam. Everything here is `MECH`: string arithmetic, a privilege the
engine enforces, and stored SQL.
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any

from ai_os import sql_safety
from ai_os.sql_safety import Policy, SafeSql, load_policy

# --------------------------------------------------------------- config knobs

DEFAULT_ROW_LIMIT = 100         # a page, never the whole table (Day 15 adds cost guards)
DEFAULT_SCHEMA_BUDGET = 4000    # chars of schema card (Day 6's budget reflex, on a schema)
READONLY_URL_ENV = "SALEOR_READONLY_DB_URL"     # beside Day 4's DATABASE_URL
DEFAULT_STATEMENT_TIMEOUT_MS = 2000             # Postgres-side wall; Day 13's wall is the outer one


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def row_limit_from_env() -> int:
    return max(1, _env_int("SALEOR_SQL_ROW_LIMIT", DEFAULT_ROW_LIMIT))


def schema_budget_from_env() -> int:
    return max(1, _env_int("SALEOR_SQL_SCHEMA_BUDGET", DEFAULT_SCHEMA_BUDGET))


def readonly_url_from_env() -> str:
    return os.getenv(READONLY_URL_ENV, "").strip()


# ============================================================ Day 14 — the taxonomy
# Day 13 named the third error family ("you asked right, the world broke"). Day
# 14 gives it the SQL vocabulary, and each kind maps to a *repair strategy* —
# which is why the taxonomy is worth keeping precise:
#
#   unknown_column / unknown_table → the schema card was misread  → REPAIR
#   invalid_sql                    → syntax, or the validator's refusal → REPAIR (or STOP)
#   timeout                        → slow query (Day 13's `timeout`)      → the wall
#
# None of these is retried with the *same* args: re-running `SELCT` is Day 12's
# "same wrong thing, louder". The fix is a *different* call — the loop's
# self-correction, applied to the most valuable case yet: query repair.
ERROR_INVALID_SQL = "invalid_sql"
ERROR_UNKNOWN_COLUMN = "unknown_column"
ERROR_UNKNOWN_TABLE = "unknown_table"

SQL_ERROR_KINDS = frozenset({ERROR_INVALID_SQL, ERROR_UNKNOWN_COLUMN, ERROR_UNKNOWN_TABLE})

# DML/DDL the seed validator rejects. A keyword list, not a parser — see
# `validate_readonly` for the honest limits of that claim.
DENY_KEYWORDS = frozenset({
    "insert", "update", "delete", "drop", "alter", "truncate", "grant",
    "revoke", "create", "copy", "merge", "call", "do", "comment", "refresh",
    "reindex", "vacuum", "analyze", "attach", "detach", "pragma", "set",
})


# --------------------------------------------------------------- result shape

@dataclass
class QueryResult:
    """The data tool's observation — parallel to the retrieval tools' top-K.

    One shape grades all four corpora: `columns + rows` sits beside Day 7's
    chunks, Day 10's `module#Symbol` records, and Day 11's data records, so
    `cited ⊆ retrieved` and `asserted ⊆ returned` stay the same kind of check.
    `truncated` is the honesty flag: a `LIMIT`ed page must never be reported as
    the whole (a truncated result is *stated*, like Day 9's dropped chunk).
    """

    ok: bool
    sql: str
    validation: str = "select-only"     # "select-only" | "rejected"
    executed: bool = False
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    elapsed_ms: float = 0.0
    note: str | None = None
    error: str | None = None
    error_kind: str | None = None
    retryable: bool = False
    # Day 15 — the safety layer's columns. `rejection_class` is the load-bearing
    # one (the loop routes on it); `stage` says *where* the choke point refused;
    # `executed_sql` is the normalized + LIMIT-rewritten statement actually run
    # (absent when it equals `sql`); `cost_estimate`/`statement_timeout_ms` make
    # the resource guards observable.
    rejection_class: str | None = None
    stage: str | None = None
    executed_sql: str | None = None
    cost_estimate: float | None = None
    statement_timeout_ms: int | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ok": self.ok, "sql": self.sql, "validation": self.validation,
            "executed": self.executed, "columns": self.columns, "rows": self.rows,
            "row_count": self.row_count, "truncated": self.truncated,
            "elapsed_ms": round(self.elapsed_ms, 3),
        }
        if self.note:
            out["note"] = self.note
        if self.error:
            out["error"] = self.error
        if self.error_kind:
            out["error_kind"] = self.error_kind
        if self.retryable:
            out["retryable"] = True
        if self.rejection_class:
            out["rejection_class"] = self.rejection_class
        if self.stage:
            out["stage"] = self.stage
        if self.executed_sql:
            out["executed_sql"] = self.executed_sql
        if self.cost_estimate is not None:
            out["cost_estimate"] = self.cost_estimate
        if self.statement_timeout_ms is not None:
            out["statement_timeout_ms"] = self.statement_timeout_ms
        return out


def failure(sql: str, *, error_kind: str, message: str,
            retryable: bool = False, validation: str = "select-only",
            executed: bool = False, rejection_class: str | None = None,
            stage: str | None = None,
            statement_timeout_ms: int | None = None) -> QueryResult:
    """A typed data failure — an *observation*, never an exception.

    `rejection_class` is derived from `error_kind` when not given
    (`ai_os.sql_safety.classify`), so a `timeout`/`policy_violation` cannot slip
    into the *repairable* path by forgetting a keyword argument.
    """
    return QueryResult(ok=False, sql=sql, validation=validation,
                       executed=executed, row_count=0, error=message,
                       error_kind=error_kind, retryable=retryable,
                       rejection_class=(rejection_class
                                        or sql_safety.classify(error_kind)),
                       stage=stage, statement_timeout_ms=statement_timeout_ms)


def sql_error_kind(exc: BaseException) -> str:
    """Map an engine exception to the §4 taxonomy — the repair signal's spelling.

    Matching on the message is deliberately crude and dialect-aware: it is the
    *seed* of the mapping (Day 15 replaces it with a parser + error codes). The
    point today is that a wrong column and a syntax error are *different*
    `error_kind`s, because they send the model to different places in the card.
    """
    text = f"{exc.__class__.__name__}: {exc}".lower()
    if "no such column" in text or "undefined column" in text or "does not exist" in text and "column" in text:
        return ERROR_UNKNOWN_COLUMN
    if "no such table" in text or "undefined table" in text or "relation" in text and "does not exist" in text:
        return ERROR_UNKNOWN_TABLE
    return ERROR_INVALID_SQL


# ==================================================== Day 14 §5 — the validator
# The seed. `validate_readonly` is a *keyword check*, not a parser, and it can
# be defeated (a `DELETE` inside a string literal, comment obfuscation, a CTE
# that writes in some dialects, dialect-specific escapes). Its job is to stop
# the obvious *before* the statement reaches the database, and to be the first
# of three layers — the engine (layer 2) and the role (layer 3) are what make
# the seed safe. Day 15 replaces this with a real parser + allow/deny.

_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")


def strip_comments(sql: str) -> str:
    return _LINE_COMMENT.sub(" ", _BLOCK_COMMENT.sub(" ", sql or ""))


def scan_body(sql: str) -> str:
    """Comments *and* string literals removed — the text the deny-scan reads.

    Dropping literals first is what keeps `WHERE product_name = 'delete me'`
    from tripping the deny list: the keyword is data, not an operation. It is
    also exactly the kind of shortcut a real parser makes unnecessary.
    """
    return _STRING_LITERAL.sub(" '' ", strip_comments(sql))


def first_keyword(body: str) -> str:
    match = _IDENT.search(body)
    return match.group(0).lower() if match else ""


def tokenize(body: str) -> list[str]:
    return [t.lower() for t in _IDENT.findall(body)]


def validate_readonly(sql: str) -> tuple[bool, str | None, str | None]:
    """§5 — the first layer: `(ok, error, error_kind)`.

    Three rules, in order of how cheap they are to check:

    1. a single statement (a stacked `SELECT 1; DROP …` is two statements);
    2. a `SELECT` or `WITH` head (the query must be a read);
    3. no DML/DDL keyword anywhere in the body.

    `WITH` is allowed because a CTE is how a legitimate revenue query is often
    written; it is also how a *writing* CTE could hide, which is the honest
    limit this function does not close (Day 15 does).
    """
    raw = (sql or "").strip()
    if not raw:
        return False, "empty SQL statement", ERROR_INVALID_SQL
    body = scan_body(raw).strip().rstrip(";").strip()
    if not body:
        return False, "empty SQL statement", ERROR_INVALID_SQL
    if ";" in body:
        return False, "only a single statement is allowed", ERROR_INVALID_SQL
    head = first_keyword(body)
    if head not in ("select", "with"):
        return False, f"only SELECT/WITH allowed, got {head or 'nothing'!r}", ERROR_INVALID_SQL
    denied = sorted({t for t in tokenize(body) if t in DENY_KEYWORDS})
    if denied:
        return False, f"write operation rejected: {', '.join(denied)}", ERROR_INVALID_SQL
    return True, None, None


# ================================================ Day 14 §2 — the schema card
# Written for a *generator* (the third audience after Day 8's scorer and Day
# 11's chooser). The card is a curated core subset: Saleor's database is
# hundreds of tables, and a full dump neither fits the context nor helps — it
# drowns a two-table query in irrelevant columns and bills the tokens on every
# step. Scaling it is schema *retrieval* (the Days 8–10 machinery, applied to
# tables), named for Day 16/19, not built today.

SCHEMA_CARD: dict[str, dict[str, Any]] = {
    "order_order": {
        "purpose": "one row per placed order (an Order is the *fact*; a Checkout is the intent)",
        "columns": {
            "id": "uuid PK",
            "number": "int — the human order number",
            "status": "text — UNFULFILLED | PARTIALLY_FULFILLED | FULFILLED | CANCELED | DRAFT",
            "currency": "text — order currency (ISO)",
            "user_id": "int FK → account_user.id",
            "channel_id": "int FK → channel_channel.id",
            "created_at": "timestamptz — when the order was created",
            "total_net_amount": "numeric — order total, net of tax",
            "total_gross_amount": "numeric — order total, tax included",
        },
    },
    "order_orderline": {
        "purpose": "one row per line item; carries the revenue numbers and denormalized names",
        "columns": {
            "id": "uuid PK",
            "order_id": "uuid FK → order_order.id",
            "variant_id": "uuid FK → product_productvariant.id (SET_NULL: the line survives variant deletion)",
            "product_name": "text — denormalized product name at order time",
            "variant_name": "text — denormalized variant name",
            "product_sku": "text",
            "quantity": "int",
            "quantity_fulfilled": "int",
            "currency": "text",
            "unit_price_net_amount": "numeric — unit price, net of tax",
            "unit_price_gross_amount": "numeric — unit price, tax included",
            "total_price_net_amount": "numeric — quantity × unit_price_net (the revenue column)",
            "total_price_gross_amount": "numeric — quantity × unit_price_gross",
        },
    },
    "product_product": {
        "purpose": "the catalog product (no price here — price lives on the variant × channel listing)",
        "columns": {
            "id": "int PK",
            "name": "text",
            "slug": "text",
            "product_type_id": "int FK → product_producttype.id",
            "category_id": "int FK → product_category.id",
            "created_at": "timestamptz",
        },
    },
    "product_productvariant": {
        "purpose": "a sellable variant of a product (size/colour)",
        "columns": {
            "id": "uuid PK",
            "product_id": "int FK → product_product.id",
            "sku": "text",
            "name": "text",
        },
    },
    "product_productvariantchannellisting": {
        "purpose": "the variant's price in one channel (price is per variant × channel, never on the product)",
        "columns": {
            "id": "int PK",
            "variant_id": "uuid FK → product_productvariant.id",
            "channel_id": "int FK → channel_channel.id",
            "currency": "text",
            "price_amount": "numeric — list price",
            "cost_price_amount": "numeric",
            "discounted_price_amount": "numeric",
        },
    },
    "account_user": {
        "purpose": "a user/customer account",
        "columns": {
            "id": "int PK",
            "email": "text — unique",
            "is_staff": "bool",
            "is_active": "bool",
            "number_of_orders": "int — denormalized order count",
            "date_joined": "timestamptz",
        },
    },
    "channel_channel": {
        "purpose": "a sales channel; money in the DB is per channel currency",
        "columns": {
            "id": "int PK",
            "slug": "text — 'default' is the store's default channel",
            "name": "text",
            "currency_code": "text",
            "is_active": "bool",
        },
    },
}

# §2 — the semantic half. Saleor is multi-channel and multi-currency, so
# "revenue" is not a column, it is a *decision*; left to the model, every run
# would resolve it differently. The glossary resolves it deterministically and
# *separately from the model*, so two runs cannot mean two different things —
# and it is exactly what Day 15's "ambiguous business definitions" attack tries
# to pull apart.
GLOSSARY: dict[str, str] = {
    "revenue": ("SUM(order_orderline.total_price_net_amount) over the order lines in the "
                "window — net of tax and discounts, in the order's currency, per channel. "
                "Gross revenue = SUM(total_price_gross_amount)."),
    "units sold": "SUM(order_orderline.quantity).",
    "order": ("a row in order_order. Exclude status = 'DRAFT' from revenue: a draft is not a sale."),
    "last month": ("the previous full calendar month in UTC. On 2026-09-25 that is "
                   "created_at >= '2026-08-01' AND created_at < '2026-09-01'."),
    "customer": ("an account_user with at least one order: JOIN order_order.user_id = "
                 "account_user.id (an account with no orders is not a customer yet)."),
    "channel": ("a row in channel_channel; slug = 'default' is the store's default channel. "
                "Amounts are already per-channel currency — do not convert or mix currencies."),
    "product performance": ("join order_orderline → product_productvariant → product_product "
                            "on variant_id/product_id; group by product_product.name."),
}

# The dialect + the read-only constraints the generator may assume. Stated in
# the card because "which SQL am I writing" is half of a correct query.
DIALECT_NOTE = ("Dialect: PostgreSQL. Read-only: only a single SELECT or WITH statement; "
                "no DML/DDL. Results are row-limited, so an unaggregated SELECT returns a page.")


def table_names() -> list[str]:
    return list(SCHEMA_CARD)


def describe_table(table: str) -> dict[str, Any] | None:
    entry = SCHEMA_CARD.get((table or "").strip().lower())
    if entry is None:
        return None
    return {"table": table, "purpose": entry["purpose"], "columns": entry["columns"]}


def schema_card_text(max_chars: int | None = None) -> str:
    """The core subset, serialized for a prompt — budget-clamped, never silent.

    Tables + columns + keys come first (a query cannot be written without them),
    the glossary last (it decides *what the numbers mean*). If the budget bites,
    the cut is marked, so a truncated card is never mistaken for a complete one.
    """
    budget = schema_budget_from_env() if max_chars is None else max(1, max_chars)
    lines: list[str] = [DIALECT_NOTE, "Tables:"]
    for name, entry in SCHEMA_CARD.items():
        lines.append(f"- {name}: {entry['purpose']}")
        for col, desc in entry["columns"].items():
            lines.append(f"    {col} {desc}")
    lines.append("Definitions (use these — do not invent others):")
    for term, definition in GLOSSARY.items():
        lines.append(f"- {term}: {definition}")
    text = "\n".join(lines)
    if len(text) <= budget:
        return text
    cut = text[:budget]
    if "\n" in cut:
        cut = cut[:cut.rfind("\n")]
    return cut + "\n…[schema card truncated by SALEOR_SQL_SCHEMA_BUDGET]"


# ================================================ Day 14 §4 — the read-only seam

class SqliteDataStore:
    """The offline engine: stdlib sqlite3, seeded, with `query_only` enforced.

    `PRAGMA query_only = ON` is the *engine's* refusal to write — the offline
    stand-in for the Postgres read-only transaction and for the
    `saleor_read_only` role's missing grants. A `DELETE` that slips past the
    validator meets `attempt to write a readonly database`, which is precisely
    the "layer 2 holds when layer 1 failed" assertion Day 14 makes.
    """

    dialect = "postgres"        # the *card's* dialect; the stub emulates the core subset
    statement_timeout_ms: int | None = None     # sqlite has no server-side wall

    def __init__(self, *, row_limit: int | None = None, seed: bool = True) -> None:
        self.row_limit = row_limit_from_env() if row_limit is None else max(1, row_limit)
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = None
        if seed:
            self._conn.executescript(SEED_SQL)
        self._conn.execute("PRAGMA query_only = ON")     # layer 2, engine-enforced
        self.queries = 0

    # ---- engine

    def query(self, sql: str, *, row_limit: int | None = None) -> QueryResult:
        """Run one read statement, capped at `row_limit` rows (fetch-capped).

        The cap is applied by fetching `limit + 1` rows, so `truncated` is a
        *measured* fact (there was one more row) rather than a guess from a
        wrapped `LIMIT`. A bomb query is still bounded in *time* only by Day
        13's wall; the cost guard is Day 15's.
        """
        limit = self.row_limit if row_limit is None else max(1, row_limit)
        started = time.monotonic()
        self.queries += 1
        try:
            cur = self._conn.execute(sql)
            columns = [d[0] for d in (cur.description or [])]
            rows = [list(r) for r in cur.fetchmany(limit + 1)]
            elapsed = (time.monotonic() - started) * 1000
            truncated = len(rows) > limit
            rows = rows[:limit]
            return QueryResult(ok=True, sql=sql, validation="select-only", executed=True,
                               columns=columns, rows=rows, row_count=len(rows),
                               truncated=truncated, elapsed_ms=elapsed,
                               note="no rows" if not rows else None)
        except Exception as exc:                        # engine refusal or a bad query
            # The statement *reached* the engine (validation already passed), so
            # `executed` is True: the failure is the engine's, not the validator's.
            # A read-only refusal lands here as `invalid_sql` — layer 2 holding.
            return failure(sql, error_kind=sql_error_kind(exc),
                           message=f"database error: {exc}", executed=True)

    def scalar(self, sql: str) -> Any:
        """A read helper for the suite (counts, etc.) — still read-only."""
        cur = self._conn.execute(sql)
        row = cur.fetchone()
        return row[0] if row else None

    def close(self) -> None:
        self._conn.close()


class PostgresDataStore:
    """The real thing: `psycopg` over `SALEOR_READONLY_DB_URL`, read-only twice.

    Layer 2 is `default_transaction_read_only` on the session; layer 3 is the
    `saleor_read_only` role from `replica_user.sql`, which holds no write grant.
    `psycopg` is imported here (not at module import) so the offline suite never
    needs the day's one new dependency — the same discipline that kept Day 4's
    providers out of the stub's path.
    """

    dialect = "postgres"

    def __init__(self, url: str, *, row_limit: int | None = None,
                 statement_timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS,
                 lock_timeout_ms: int = 1000,
                 idle_in_transaction_session_timeout_ms: int = 5000) -> None:
        try:
            import psycopg                      # noqa: PLC0415 — lazy: offline suites skip it
        except ImportError as exc:              # pragma: no cover — exercised only with a live DB
            raise RuntimeError(
                "SALEOR_READONLY_DB_URL is set but psycopg is not installed "
                "(pip install 'psycopg[binary]') — the offline stub needs no driver."
            ) from exc
        self.row_limit = row_limit_from_env() if row_limit is None else max(1, row_limit)
        self.statement_timeout_ms = int(statement_timeout_ms)
        self._psycopg = psycopg
        self._conn = psycopg.connect(url, autocommit=False)
        with self._conn.cursor() as cur:
            # Day 15 §6 — the *server-side* half of the timeout story. Day 13's
            # client wall abandons a slow call; these settings make the *server*
            # stop the work, which is the half a client timeout cannot do.
            cur.execute("SET default_transaction_read_only = on")    # layer 2
            cur.execute(f"SET statement_timeout = {self.statement_timeout_ms}")
            cur.execute(f"SET lock_timeout = {int(lock_timeout_ms)}")
            cur.execute("SET idle_in_transaction_session_timeout = "
                        f"{int(idle_in_transaction_session_timeout_ms)}")
        self.queries = 0

    def explain_cost(self, sql: str) -> float | None:
        """The real §6 cost pre-check: `EXPLAIN (FORMAT JSON)`'s `Total Cost`.

        A *heuristic* like every cost estimate — the server timeout remains the
        certain bound behind it — but a real plan beats the offline shape guess.
        """
        with self._conn.cursor() as cur:
            cur.execute("EXPLAIN (FORMAT JSON) " + sql)
            row = cur.fetchone()
        self._conn.rollback()
        plan = row[0] if row else None
        if isinstance(plan, list) and plan:
            plan = plan[0]
        if isinstance(plan, dict):
            return float((plan.get("Plan") or {}).get("Total Cost", 0.0))
        return None

    def query(self, sql: str, *, row_limit: int | None = None) -> QueryResult:
        limit = self.row_limit if row_limit is None else max(1, row_limit)
        started = time.monotonic()
        self.queries += 1
        try:
            with self._conn.cursor() as cur:
                cur.execute(sql)
                columns = [d[0] for d in (cur.description or [])]
                rows = [list(r) for r in cur.fetchmany(limit + 1)]
            self._conn.rollback()               # read-only: end the snapshot
            elapsed = (time.monotonic() - started) * 1000
            truncated = len(rows) > limit
            rows = rows[:limit]
            return QueryResult(ok=True, sql=sql, validation="select-only", executed=True,
                               columns=columns, rows=rows, row_count=len(rows),
                               truncated=truncated, elapsed_ms=elapsed,
                               note="no rows" if not rows else None)
        except Exception as exc:
            try:
                self._conn.rollback()
            except Exception:                   # pragma: no cover — broken connection
                pass
            elapsed = (time.monotonic() - started) * 1000
            kind = sql_error_kind(exc)
            if "cancel" in f"{exc}".lower() or "timeout" in f"{exc}".lower():
                # §6 — the server killed a runaway statement. A *policy*-class
                # resource rejection (not retryable: re-issuing the same runaway
                # query is exactly the loop helping itself around the guard).
                return failure(sql, error_kind="timeout",
                               message=f"query exceeded statement_timeout: {exc}",
                               retryable=False, executed=True,
                               stage=sql_safety.STAGE_EXECUTE,
                               statement_timeout_ms=self.statement_timeout_ms)
            return failure(sql, error_kind=kind, message=f"database error: {exc}",
                           executed=True, stage=sql_safety.STAGE_EXECUTE,
                           statement_timeout_ms=self.statement_timeout_ms)

    def scalar(self, sql: str) -> Any:
        cur = self._conn.cursor()
        cur.execute(sql)
        row = cur.fetchone()
        return row[0] if row else None

    def close(self) -> None:
        self._conn.close()


class DataAgent:
    """The §3/§5 seam: validate → execute → the evidence record. One place.

    The tool layer calls `run(sql)`; nothing here knows about the loop, and the
    loop's timeout wall wraps `registry.execute` *around* this — the narrowest
    wall, at the call, so the trace can say *which* query hung and with what SQL.
    """

    def __init__(self, store, *, row_limit: int | None = None,
                 policy: Policy | None = None) -> None:
        self.store = store
        self.row_limit = store.row_limit if row_limit is None else max(1, row_limit)
        # §5 — the policy is config, loaded from policy.yaml (or $SALEOR_SQL_POLICY).
        # The cost pre-check prefers the store's real EXPLAIN when it has one.
        self.policy = policy if policy is not None else load_policy()
        self.safety = SafeSql(
            self.policy,
            explain_cost=getattr(store, "explain_cost", None))

    def run(self, sql: str, *, row_limit: int | None = None,
            validate: bool = True) -> QueryResult:
        """The §3 choke point: normalize → parse → policy → cost → rewrite → execute.

        `validate=False` skips the layer entirely — the escape hatch Day 14's Q5
        uses to prove the *engine* (layer 2) refuses a write that bypasses the
        layer-1 rejection. Nothing else should call it.
        """
        limit = min(self.row_limit if row_limit is None else max(1, row_limit),
                    max(1, self.policy.row_limit))
        if not validate:
            return self.store.query(sql, row_limit=limit)

        verdict = self.safety.inspect(sql)
        if not verdict.ok:
            # A pre-execution refusal: `executed` stays False, and the *raw*
            # generated SQL is preserved on the record so the §8 attack table can
            # show what the model asked for. `validation: "rejected"` is the
            # Day 14 column; `rejection_class` is the Day 15 one.
            return failure(sql, error_kind=verdict.error_kind or ERROR_INVALID_SQL,
                           message=verdict.message or "rejected",
                           validation="rejected", executed=False,
                           rejection_class=verdict.rejection_class,
                           stage=verdict.stage)

        executed_sql = self.safety.rewrite(verdict, row_limit=limit)
        result = self.store.query(executed_sql, row_limit=limit)
        # `sql` stays the artifact the *model* wrote (the trace's `gen_sql`); the
        # normalized + LIMIT-rewritten form is recorded separately, so "what was
        # asked" and "what ran" cannot be confused.
        result.sql = sql
        if executed_sql != sql:
            result.executed_sql = executed_sql
        result.stage = sql_safety.STAGE_EXECUTE
        result.cost_estimate = verdict.cost_estimate
        result.statement_timeout_ms = getattr(self.store, "statement_timeout_ms", None)
        if result.error_kind:
            result.rejection_class = sql_safety.classify(result.error_kind)
        return result

    # --------------------------------------------------- parameterized resolvers
    # Safe by construction: one fixed query, one validated `id`, zero generation.
    # These complete the Day 11 stubs — the *interface* Day 11 shipped (schema,
    # `id` validation, result shape) is filled, unchanged, and the
    # `pending_resolver` tag comes off because the resolver is now real.

    def get_product(self, product_id: str) -> QueryResult:
        sql = (
            "SELECT p.id AS id, p.name AS name, p.slug AS slug, "
            "       pt.name AS product_type, l.currency AS currency, "
            "       l.price_amount AS price, "
            "       (SELECT COUNT(*) FROM product_productvariant v "
            "        WHERE v.product_id = p.id) AS variants "
            "FROM product_product p "
            "LEFT JOIN product_producttype pt ON pt.id = p.product_type_id "
            "LEFT JOIN product_productvariant v ON v.product_id = p.id "
            "LEFT JOIN product_productvariantchannellisting l ON l.variant_id = v.id "
            f"WHERE p.id = {_literal(product_id)} LIMIT 1"
        )
        return self.store.query(sql, row_limit=1)

    def get_order(self, order_id: str) -> QueryResult:
        sql = (
            "SELECT o.id AS id, o.number AS number, o.status AS status, "
            "       o.currency AS currency, o.total_gross_amount AS total, "
            "       (SELECT COUNT(*) FROM order_orderline l WHERE l.order_id = o.id) AS lines "
            "FROM order_order o "
            f"WHERE o.id = {_literal(order_id)} LIMIT 1"
        )
        return self.store.query(sql, row_limit=1)

    def get_customer(self, customer_id: str) -> QueryResult:
        sql = (
            "SELECT u.id AS id, u.email AS email, "
            "       u.number_of_orders AS orders, "
            "       (SELECT o.currency FROM order_order o WHERE o.user_id = u.id "
            "        ORDER BY o.created_at DESC LIMIT 1) AS currency "
            "FROM account_user u "
            f"WHERE u.id = {_literal(customer_id)} LIMIT 1"
        )
        return self.store.query(sql, row_limit=1)


def _literal(value: Any) -> str:
    """A quoted SQL literal for the parameterized resolvers only.

    The `id` never comes from a generated query — it is a validated string
    argument — but it is still escaped rather than interpolated raw, because
    "the argument was validated" is not a reason to concatenate.
    """
    return "'" + str(value).replace("'", "''") + "'"


def make_data_store(url: str | None = None, *, row_limit: int | None = None,
                    seed: bool = True):
    """Postgres when a URL is configured, the seeded stub otherwise.

    One seam, two backends: `SALEOR_READONLY_DB_URL` set → the real read-only
    role; unset → the offline engine, which is what every `MECH` check runs on.
    """
    target = (url if url is not None else readonly_url_from_env()).strip()
    if target:
        return PostgresDataStore(target, row_limit=row_limit)
    return SqliteDataStore(row_limit=row_limit, seed=seed)


# ================================================ Day 14 §7 — asserted ⊆ returned
# `cited ⊆ retrieved` translated from chunks to rows: every number and entity the
# explanation asserts must appear in (or be a stated aggregate of) the result set.
# A fabricated statistic looks exactly like a real one — "approximately 1,240
# orders" is fluent whether or not a query produced it — so the check is
# mechanical, not a reviewer's judgment.

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _to_float(token: str) -> float | None:
    try:
        return float(str(token).replace(",", ""))
    except (TypeError, ValueError):
        return None


def asserted_numbers(text: str) -> list[str]:
    return [m.group(0) for m in _NUMBER.finditer(text or "")]


def asserted_emails(text: str) -> list[str]:
    return _EMAIL.findall(text or "")


def returned_values(records: list[dict[str, Any]]) -> tuple[set[float], set[str]]:
    """The set a data answer may draw from: cell values + stated aggregates.

    Numbers come from every cell of every executed result (`row_count` and the
    column count included), plus the aggregates a headline may legitimately
    compute — sum / max / min per numeric column. Emails are checked as strings.
    """
    values: set[float] = set()
    strings: set[str] = set()
    for rec in records:
        if not rec.get("executed"):
            continue
        values.add(float(rec.get("row_count") or 0))
        values.add(float(len(rec.get("columns") or [])))
        columns = rec.get("columns") or []
        numeric: dict[int, list[float]] = {}
        for row in rec.get("rows") or []:
            for i, cell in enumerate(row):
                if isinstance(cell, str):
                    strings.add(cell.strip().lower())
                    if "@" in cell:
                        strings.add(cell.strip().lower())
                num = _to_float(cell) if not isinstance(cell, bool) else None
                if num is not None:
                    values.add(num)
                    numeric.setdefault(i, []).append(num)
        for nums in numeric.values():
            values.update({sum(nums), max(nums), min(nums), len(nums)})
    return values, strings


def _covered(token: str, values: set[float]) -> bool:
    num = _to_float(token)
    if num is None:
        return True
    return any(abs(num - v) <= max(1e-6, abs(v) * 1e-9) for v in values)


def asserted_subset_returned(text: str, *,
                             question: str = "",
                             records: list[dict[str, Any]] | None = None,
                             ) -> dict[str, Any]:
    """§7 — the day's headline invariant, as a set-membership test.

    A number that appears in the *question* is an echo, not a data claim, and is
    skipped; everything else the explanation asserts must be a returned cell or
    a stated aggregate of one. Recorded limits, honestly: this is number- and
    email-shaped (prose that ignores the evidence is not caught), and it
    *over-flags* derived figures (a percentage the rows imply but do not
    contain) — the conservative direction, which is the one a guard should err.
    """
    recs = records or []
    values, strings = returned_values(recs)
    asked = set(asserted_numbers(question or ""))
    unsupported = [n for n in asserted_numbers(text or "")
                   if n not in asked and not _covered(n, values)]
    unsupported_emails = [e for e in asserted_emails(text or "")
                          if e.strip().lower() not in strings]
    return {
        "ok": not unsupported and not unsupported_emails,
        "asserted": asserted_numbers(text or ""),
        "unsupported": unsupported,
        "unsupported_entities": unsupported_emails,
    }


# ==================================================== the offline seed (§6 stub)
# A curated core subset, matched to SCHEMA_CARD exactly: enough rows for an
# aggregation (Q1), a two-table join (Q3), an empty window (Q4), and a truncated
# page (Q7). Money is REAL in the stub and numeric in Postgres; the *shape* is
# what the harness grades. "Today" is 2026-09-25, so "last month" is 2026-08.

SEED_SQL = """
CREATE TABLE channel_channel (
    id INTEGER PRIMARY KEY, slug TEXT, name TEXT, currency_code TEXT, is_active INTEGER
);
CREATE TABLE account_user (
    id INTEGER PRIMARY KEY, email TEXT, is_staff INTEGER, is_active INTEGER,
    number_of_orders INTEGER, date_joined TEXT
);
CREATE TABLE product_producttype (id INTEGER PRIMARY KEY, name TEXT, slug TEXT);
CREATE TABLE product_category (id INTEGER PRIMARY KEY, name TEXT, slug TEXT);
CREATE TABLE product_product (
    id INTEGER PRIMARY KEY, name TEXT, slug TEXT, product_type_id INTEGER,
    category_id INTEGER, created_at TEXT
);
CREATE TABLE product_productvariant (
    id TEXT PRIMARY KEY, product_id INTEGER, sku TEXT, name TEXT
);
CREATE TABLE product_productvariantchannellisting (
    id INTEGER PRIMARY KEY, variant_id TEXT, channel_id INTEGER, currency TEXT,
    price_amount REAL, cost_price_amount REAL, discounted_price_amount REAL
);
CREATE TABLE order_order (
    id TEXT PRIMARY KEY, number INTEGER, status TEXT, currency TEXT, user_id INTEGER,
    channel_id INTEGER, created_at TEXT, total_net_amount REAL, total_gross_amount REAL
);
CREATE TABLE order_orderline (
    id TEXT PRIMARY KEY, order_id TEXT, variant_id TEXT, product_name TEXT,
    variant_name TEXT, product_sku TEXT, quantity INTEGER, quantity_fulfilled INTEGER,
    currency TEXT, unit_price_net_amount REAL, unit_price_gross_amount REAL,
    total_price_net_amount REAL, total_price_gross_amount REAL
);

INSERT INTO channel_channel VALUES (1, 'default', 'Default Channel', 'USD', 1);

INSERT INTO account_user VALUES
  (1, 'ada@example.com',    0, 1, 2, '2026-01-05 09:00:00'),
  (2, 'grace@example.com',  0, 1, 2, '2026-02-11 09:00:00'),
  (3, 'alan@example.com',   0, 1, 0, '2026-03-02 09:00:00');

INSERT INTO product_producttype VALUES (1, 'Apparel', 'apparel'), (2, 'Homeware', 'homeware');
INSERT INTO product_category VALUES (1, 'Clothing', 'clothing'), (2, 'Home', 'home');

INSERT INTO product_product VALUES
  (1, 'Blue T-Shirt', 'blue-t-shirt', 1, 1, '2026-01-01 00:00:00'),
  (2, 'Coffee Mug',   'coffee-mug',   2, 2, '2026-01-01 00:00:00'),
  (3, 'Desk Lamp',    'desk-lamp',    2, 2, '2026-01-01 00:00:00');

INSERT INTO product_productvariant VALUES
  ('v1', 1, 'BTS-1', 'Blue T-Shirt / M'),
  ('v2', 2, 'CM-1',  'Coffee Mug / Stand'),
  ('v3', 3, 'DL-1',  'Desk Lamp / White');

INSERT INTO product_productvariantchannellisting VALUES
  (1, 'v1', 1, 'USD', 19.99, 8.00,  19.99),
  (2, 'v2', 1, 'USD', 9.50,  4.00,  9.50),
  (3, 'v3', 1, 'USD', 24.00, 12.00, 24.00);

-- August 2026 is "last month"; July 2026 rows exist so the window has to be real.
INSERT INTO order_order VALUES
  ('1', 1001, 'UNFULFILLED', 'USD', 1, 1, '2026-08-04 10:00:00', 49.48,  59.38),
  ('2', 1002, 'FULFILLED',   'USD', 1, 1, '2026-08-12 09:30:00', 115.99, 139.19),
  ('3', 1003, 'FULFILLED',   'USD', 2, 1, '2026-08-21 15:45:00', 28.50,  34.20),
  ('4', 1004, 'FULFILLED',   'USD', 2, 1, '2026-07-15 12:00:00', 9.50,   11.40),
  ('5', 1005, 'DRAFT',       'USD', 3, 1, '2026-08-28 08:15:00', 19.99,  23.99);

INSERT INTO order_orderline VALUES
  ('1', '1', 'v1', 'Blue T-Shirt', 'Blue T-Shirt / M',    'BTS-1', 2, 0, 'USD', 19.99, 23.99, 39.98,  47.98),
  ('2', '1', 'v2', 'Coffee Mug',   'Coffee Mug / Stand',  'CM-1',  1, 0, 'USD', 9.50,  11.40, 9.50,   11.40),
  ('3', '2', 'v1', 'Blue T-Shirt', 'Blue T-Shirt / M',    'BTS-1', 1, 1, 'USD', 19.99, 23.99, 19.99,  23.99),
  ('4', '2', 'v3', 'Desk Lamp',    'Desk Lamp / White',   'DL-1',  4, 4, 'USD', 24.00, 28.80, 96.00,  115.20),
  ('5', '3', 'v2', 'Coffee Mug',   'Coffee Mug / Stand',  'CM-1',  3, 3, 'USD', 9.50,  11.40, 28.50,  34.20),
  ('6', '4', 'v2', 'Coffee Mug',   'Coffee Mug / Stand',  'CM-1',  1, 1, 'USD', 9.50,  11.40, 9.50,   11.40),
  ('7', '5', 'v1', 'Blue T-Shirt', 'Blue T-Shirt / M',    'BTS-1', 1, 0, 'USD', 19.99, 23.99, 19.99,  23.99);
"""
