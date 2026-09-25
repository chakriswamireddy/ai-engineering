"""Day 15 §3–§6 — the safe SQL execution layer: one ordered, fail-closed choke point.

Day 14 made the Data Agent *work* and was honest about the seed: a keyword
validator is **not** a parser, and it is defeatable. Day 15 attacks the seed
with the curriculum's adversary and hardens it. Three ideas carry the module,
and they are worth stating because each inverts a tempting instinct:

1. **Allow-lists beat deny-lists.** You cannot enumerate every dangerous SQL
   construct (dialects, comment obfuscation, homoglyphs, stacked statements,
   writing CTEs, side-effecting functions). You *can* enumerate the finite set
   you intend to permit — **one statement**, **`SELECT`/`WITH` only**, over an
   **allow-listed schema**, with a **bounded cost** — and reject the complement.
   `normalize()` and `parse_readonly()` prove membership in that set; `Policy`
   defines it. Everything outside it is refused by default, which is what makes
   the layer *fail closed*: a construct nobody thought of is rejected because
   it is not on the list, not permitted because it is missing from a list that
   was never complete.

2. **Privileges beat parsers.** A hand-rolled grammar's correctness is a
   *claim*; the `saleor_read_only` role's inability to `INSERT` is a *fact the
   database enforces*. So this module is **defense-in-depth**: the invariant
   lives in the role, and the parser/policy/cost guards above it are best-effort
   and provably incomplete. `docs/concepts/safe-sql.md` says this plainly rather
   than implying a green table means soundness.

3. **Fail closed but stay honest.** Every uncertainty rejects — but a layer that
   blocks everything is worthless, so the day measures a **false-reject rate**
   against a legitimate control (X11). Over-blocking is a bug, not a win.

The day's sharpest new idea is the **two rejection classes**. Every refusal is
one of:

- `repairable` — a syntax error, an unknown/misspelled column: the model asked
  *wrong* and a fixed query is the right response, so the loop's self-correction
  (Day 12/13) applies. `error_kind: invalid_sql | unknown_column | unknown_table`.
- `policy` — a write attempt, a denied/sensitive table or column, an ambiguous
  definition, an over-cost query, a runaway statement: the model asked for
  something it may **not** have, so self-correction **does not apply**. The loop
  refuses, surfaces it, and does not help the model route around it.
  `error_kind: policy_violation | cost_exceeded | timeout`.

Note the axes are independent: a `DELETE` is `error_kind: invalid_sql` (it is
not a valid read statement) *and* `rejection_class: policy` (it is a forbidden
request). `error_kind` names the disease; `rejection_class` names the follow-up.
Day 12's "a refusal is a finding, not a retry signal" is this rule one layer
down: a *policy-rejected query* must not be retried into a bypass.

The ordered pipeline every generated query passes through, cheap checks first::

    sql → [normalize] → [parse] → [policy] → [cost] → [rewrite LIMIT] → execute

`SafeSql.inspect()` runs the first four; `SafeSql.rewrite()` injects the
`LIMIT`; execution (the read-only transaction + server `statement_timeout`)
belongs to the connection in `ai_os/data.py`; the guarantee belongs to the role.

Everything here is `MECH`: string/grammar arithmetic over the token stream, set
membership, and one optional `EXPLAIN` round-trip. Zero chats, zero embeds.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

try:                                        # PyYAML ships with the repo's suites
    import yaml
except ImportError:                         # pragma: no cover — a missing dep is loud, not silent
    yaml = None

# --------------------------------------------------------------- rejection classes
# The load-bearing field. Opposite follow-ups on the same `{ok: false}` surface.
REPAIRABLE = "repairable"
POLICY = "policy"

# --------------------------------------------------------------- pipeline stages
# Where a rejection was raised — the `detected at` column of the §8 attack table.
STAGE_NORMALIZE = "normalize"
STAGE_PARSE = "parse"
STAGE_POLICY = "policy"
STAGE_COST = "cost"
STAGE_EXECUTE = "execute"

# --------------------------------------------------------------- error kinds
# `error_kind` names the *disease*; `rejection_class` names the *follow-up*.
ERROR_INVALID_SQL = "invalid_sql"           # syntax garbage OR a write head (class decides)
ERROR_UNKNOWN_COLUMN = "unknown_column"     # a DB-level grounding error → repairable
ERROR_UNKNOWN_TABLE = "unknown_table"       # (policy owns the allow-list case)
ERROR_POLICY = "policy_violation"           # a denied table/column/definition
ERROR_COST = "cost_exceeded"                # the plan's estimate is over the cap
ERROR_TIMEOUT = "timeout"                   # the server killed a runaway statement

POLICY_KINDS = frozenset({ERROR_POLICY, ERROR_COST, ERROR_TIMEOUT})
REPAIRABLE_KINDS = frozenset({ERROR_INVALID_SQL, ERROR_UNKNOWN_COLUMN, ERROR_UNKNOWN_TABLE})


def classify(error_kind: str | None) -> str | None:
    """`error_kind` → `rejection_class`. The routing function both the loop and
    the trace read; `None` for an error kind this layer does not own (a plain
    tool failure, a Day 13 timeout at the call boundary, …)."""
    if error_kind in POLICY_KINDS:
        return POLICY
    if error_kind in REPAIRABLE_KINDS:
        return REPAIRABLE
    return None


# --------------------------------------------------------------- tokens
@dataclass
class Token:
    kind: str                               # word | num | str | punct
    text: str
    pos: int


# --------------------------------------------------------------- shape
@dataclass
class Shape:
    """What the parser proved about a statement — the policy's input.

    `tables`/`columns` aggregate across nesting (a subquery naming a denied
    table is still a violation), while the *outer* statement's clause flags
    (`has_where`, `has_group_by`, …) drive the cost heuristic and the shape-aware
    `LIMIT` rewrite: those must not be set by a nested block, or an unbounded
    outer join would look bounded.
    """

    tables: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    ctes: list[str] = field(default_factory=list)
    has_where: bool = False
    has_group_by: bool = False
    has_aggregate: bool = False
    has_distinct: bool = False
    has_union: bool = False


@dataclass
class Verdict:
    """The choke point's output: `ok` (carrying the normalized SQL + shape) or a
    typed, *classified* rejection. Never an exception."""

    ok: bool
    raw: str
    sql: str = ""                           # normalized, single statement (when ok)
    stage: str | None = None
    error_kind: str | None = None
    rejection_class: str | None = None
    message: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    shape: Shape | None = None
    cost_estimate: float | None = None
    max_cost: float | None = None


def _reject(raw: str, *, stage: str, error_kind: str, cls: str, message: str,
            sql: str = "", detail: dict[str, Any] | None = None) -> Verdict:
    return Verdict(ok=False, raw=raw, sql=sql, stage=stage, error_kind=error_kind,
                   rejection_class=cls, message=message, detail=detail or {})


# ============================================================ §4 — normalization
# Every evasion hides in the gap between *what a human reads* and *what the
# engine runs*. Normalization closes the gap before the grammar sees the text:
# comments stripped (`DE/**/LETE` → `DELETE`), whitespace collapsed, non-ASCII
# homoglyphs refused (a Cyrillic `Е` is not an ASCII `E`), and the statement
# count forced to one by splitting on `;`. The normalized form is the artifact
# that is parsed *and* executed, so the two cannot diverge.

# A write/DDL/utility keyword, used two ways: a *head* in this set is a policy
# rejection (a write attempt), and the residual scan refuses it anywhere the
# grammar might have skipped it. Deliberately excludes read-only constructs
# (`values` as a FROM source, `set` inside a function) so the residual scan
# cannot over-block a legitimate query.
WRITE_KEYWORDS = frozenset({
    "insert", "update", "delete", "merge", "truncate", "drop", "alter", "create",
    "grant", "revoke", "vacuum", "reindex", "copy", "comment", "refresh", "call",
    "do", "attach", "detach", "pragma", "begin", "commit", "rollback", "savepoint",
    "release", "replace", "upsert", "into", "returning", "analyze",
})

STATEMENT_HEADS = frozenset({"select", "with"})


def _split_statements(raw: str) -> tuple[list[str], str | None]:
    """Strip comments and split on `;`, tracking string literals so a `;` or
    `--` *inside a string* is data, not syntax. Returns the statement bodies
    (pre-normalization) and a fatal reason, if any."""
    out: list[str] = []
    buf: list[str] = []
    i, n = 0, len(raw)
    in_str = False
    while i < n:
        ch = raw[i]
        if in_str:
            buf.append(ch)
            if ch == "'":
                if i + 1 < n and raw[i + 1] == "'":     # escaped quote
                    buf.append("'")
                    i += 2
                    continue
                in_str = False
            i += 1
            continue
        if ch == "'":
            in_str = True
            buf.append(ch)
            i += 1
            continue
        if ch == "-" and i + 1 < n and raw[i + 1] == "-":
            while i < n and raw[i] != "\n":
                i += 1
            buf.append(" ")
            continue
        if ch == "/" and i + 1 < n and raw[i + 1] == "*":
            # A block comment is removed by *deletion*, not replaced by a space:
            # that is what collapses the obfuscation `DE/**/LETE` → `DELETE` so
            # the grammar sees the same keyword a comment-stripping engine would.
            # The cost is that `SELECT/**/1` glues to `SELECT1` and fails closed.
            i += 2
            while i + 1 < n and not (raw[i] == "*" and raw[i + 1] == "/"):
                i += 1
            i = min(i + 2, n)
            continue
        if ch == ";":
            out.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    if in_str:
        return out, "unterminated string literal"
    out.append("".join(buf))
    return out, None


def _first_non_ascii_outside_string(text: str) -> str | None:
    in_str = False
    for ch in text:
        if ch == "'":
            in_str = not in_str
            continue
        if in_str:
            continue
        if ord(ch) > 126 or (ord(ch) < 32 and ch not in "\t\n\r"):
            return ch
    return None


def normalize(sql: str) -> Verdict:
    """Stage 1 — comments, whitespace, homoglyphs, single statement.

    A parse that runs on un-normalized text is a parse the engine can disagree
    with; this is where the disagreement is removed.
    """
    raw = sql or ""
    if not raw.strip():
        return _reject(raw, stage=STAGE_NORMALIZE, error_kind=ERROR_INVALID_SQL,
                       cls=REPAIRABLE, message="empty SQL statement")
    parts, fatal = _split_statements(raw)
    if fatal:
        return _reject(raw, stage=STAGE_NORMALIZE, error_kind=ERROR_INVALID_SQL,
                       cls=REPAIRABLE, message=fatal)
    statements = [re.sub(r"\s+", " ", p).strip() for p in parts]
    statements = [s for s in statements if s]
    if not statements:
        return _reject(raw, stage=STAGE_NORMALIZE, error_kind=ERROR_INVALID_SQL,
                       cls=REPAIRABLE, message="empty SQL statement")
    if len(statements) > 1:
        # X7 — a stacked statement is how a harmless first word fronts a write.
        # Policy, not syntax: the model tried to smuggle a second operation.
        return _reject(raw, stage=STAGE_NORMALIZE, error_kind=ERROR_INVALID_SQL,
                       cls=POLICY,
                       message="a second statement is not allowed (single-statement rule)",
                       detail={"statements": len(statements)})
    body = statements[0]
    bad = _first_non_ascii_outside_string(body)
    if bad is not None:
        # X8 — a homoglyph identifier is how a keyword scanner is fooled into
        # reading a different string than the engine parses.
        return _reject(raw, stage=STAGE_NORMALIZE, error_kind=ERROR_INVALID_SQL,
                       cls=POLICY,
                       message=(f"non-ASCII character {bad!r} outside a string literal "
                                "is not allowed (obfuscation guard)"),
                       detail={"char": bad})
    return Verdict(ok=True, raw=raw, sql=body)


# --------------------------------------------------------------- tokenizer
_TOKEN_RE = re.compile(r"""
      (?P<str>'(?:[^']|'')*')
    | (?P<num>\d+(?:\.\d+)?)
    | (?P<word>[A-Za-z_][A-Za-z_0-9]*)
    | (?P<punct><>|<=|>=|!=|\|\||::|[-+*/%<>=.,()"'^|~\[\]{}:])
""", re.VERBOSE)


def tokenize(sql: str) -> list[Token]:
    """A flat token stream: strings and numbers are single tokens (so a keyword
    inside a literal is *data*, and the deny scans cannot see it), identifiers
    are `word`, everything else is `punct`."""
    tokens: list[Token] = []
    i, n = 0, len(sql)
    while i < n:
        if sql[i].isspace():
            i += 1
            continue
        m = _TOKEN_RE.match(sql, i)
        if m is None:                       # a stray char the grammar cannot classify
            tokens.append(Token("punct", sql[i], i))
            i += 1
            continue
        kind = m.lastgroup or "punct"
        kind = {"str": "str", "num": "num", "word": "word"}.get(kind, "punct")
        tokens.append(Token(kind, m.group(0), i))
        i = m.end()
    return tokens


# --------------------------------------------------------------- grammar guards
_AGGREGATES = frozenset({
    "sum", "count", "avg", "min", "max", "stddev", "stddev_pop", "stddev_samp",
    "variance", "var_pop", "var_samp", "array_agg", "string_agg", "bool_and",
    "bool_or", "every", "json_agg", "jsonb_agg",
})

# Words a table alias cannot be, and words that are never a column *candidate*
# (clause keywords, operators-as-words, function-ish names). This is only used
# to *narrow* the column-deny check, so a miss costs a missed deny and an
# over-broad set costs a false deny; the deny list is narrow enough that broad
# is the safe direction on neither side, so keep it to real keywords.
_SQL_KEYWORDS = frozenset({
    "select", "from", "where", "group", "by", "having", "order", "limit",
    "offset", "union", "all", "distinct", "as", "join", "left", "right", "full",
    "inner", "outer", "cross", "on", "using", "with", "recursive", "and", "or",
    "not", "in", "is", "null", "true", "false", "between", "like", "ilike",
    "case", "when", "then", "else", "end", "asc", "desc", "nulls", "first",
    "last", "over", "partition", "window", "filter", "rows", "range",
    "preceding", "following", "current", "row", "unbounded", "exists", "cast",
    "interval", "extract", "date_trunc", "sum", "count", "avg", "min", "max",
    "coalesce", "nullif", "greatest", "least", "round", "abs", "now", "rank",
    "dense_rank", "row_number", "lag", "lead", "ntile", "stddev", "variance",
    "array_agg", "string_agg", "bool_and", "bool_or", "every", "values",
})

_NON_ALIAS = frozenset({
    "where", "join", "left", "right", "full", "inner", "cross", "outer",
    "group", "having", "order", "limit", "offset", "union", "on", "using",
    "as", "natural", "window",
})

# Words that can never be a bare table/alias/CTE name. Rejecting them is what
# turns `SELECT FROM WHERE` into a *repairable* syntax error instead of a table
# named "where" (a policy violation) — the grammar must not launder a typo into
# a policy decision.
_RESERVED_NAMES = frozenset({
    "select", "from", "where", "group", "by", "having", "order", "limit",
    "offset", "union", "all", "distinct", "as", "join", "left", "right", "full",
    "inner", "outer", "cross", "on", "using", "with", "recursive", "and", "or",
    "not", "in", "is", "null", "between", "like", "ilike", "case", "when",
    "then", "else", "end", "asc", "desc", "over", "partition", "window",
    "values", "exists", "cast",
})

# Where a select list / expression scan must stop at depth 0.
_SELECT_STOP = frozenset({"from", "where", "group", "having", "union", "order",
                          "limit", "offset", "window"})
_WHERE_STOP = frozenset({"group", "having", "union", "order", "limit", "offset",
                         "window"})
_GROUP_STOP = frozenset({"having", "union", "order", "limit", "offset", "window"})
_HAVING_STOP = frozenset({"union", "order", "limit", "offset", "window"})
_JOIN_STOP = frozenset({"join", "left", "right", "full", "inner", "cross",
                        "outer", "natural", "where", "group", "having", "order",
                        "limit", "offset", "union", "window"})


class _ParseError(Exception):
    def __init__(self, message: str, cls: str = REPAIRABLE):
        super().__init__(message)
        self.cls = cls


class _Parser:
    """A conservative recursive-descent grammar over the read-only subset.

    The grammar has **no production** for `INSERT`/`UPDATE`/`DELETE`/`MERGE`/
    `TRUNCATE`/DDL/`GRANT`/`CALL`/`DO`, so read-only-ness is a property of the
    grammar's *shape* rather than a keyword check: a write can only parse if the
    parser has a bug. That is exactly why the role (§ the module docstring) is
    the guarantee and this is the front. Expressions are scanned shallowly —
    balanced parentheses, recursion into subqueries, no clause keyword consumed
    by accident — because the day needs *membership proof*, not a full AST.
    """

    def __init__(self, tokens: list[Token]):
        self.t = tokens
        self.i = 0
        self.nest = 0                       # >0 inside a subquery/CTE
        self.shape = Shape()
        self.aliases: set[str] = set()

    # -- token helpers
    def eof(self) -> bool:
        return self.i >= len(self.t)

    def peek(self, k: int = 0) -> Token | None:
        j = self.i + k
        return self.t[j] if 0 <= j < len(self.t) else None

    def at(self, text: str) -> bool:
        tok = self.peek()
        return tok is not None and tok.kind == "punct" and tok.text == text

    def at_word(self, *words: str) -> bool:
        tok = self.peek()
        return tok is not None and tok.kind == "word" and tok.text.lower() in words

    def eat_word(self, word: str) -> bool:
        if self.at_word(word):
            self.i += 1
            return True
        return False

    def expect(self, text: str) -> None:
        if not self.at(text):
            raise _ParseError(f"expected {text!r} near token {self.i}")
        self.i += 1

    def expect_word(self, word: str) -> None:
        if not self.eat_word(word):
            raise _ParseError(f"expected {word!r} near token {self.i}")

    def expect_name(self) -> str:
        tok = self.peek()
        if tok is None or tok.kind != "word":
            raise _ParseError(f"expected a name near token {self.i}")
        if tok.text.lower() in _RESERVED_NAMES:
            raise _ParseError(
                f"expected a name, got the reserved word {tok.text.lower()!r} "
                f"at token {self.i}")
        self.i += 1
        parts = [tok.text.lower()]
        while self.at(".") and self.peek(1) is not None and self.peek(1).kind == "word":
            self.i += 1
            parts.append(self.peek().text.lower())
            self.i += 1
        return parts[-1]

    # -- statement
    def parse(self) -> Shape:
        self.parse_statement()
        if not self.eof():
            raise _ParseError(f"unexpected token {self.peek().text!r} at {self.i}")
        self.shape.columns = _collect_columns(self.t, self.shape, self.aliases)
        return self.shape

    def parse_statement(self) -> None:
        first = self.peek()
        if first is None:
            raise _ParseError("empty statement")
        if first.kind != "word":
            raise _ParseError("only SELECT/WITH statements are allowed")
        head = first.text.lower()
        if head in WRITE_KEYWORDS:
            # A write head is a *policy* rejection: the model asked for
            # something it may not have, so self-correction must not apply.
            raise _ParseError(
                "write/DDL statements are not allowed (read-only layer)", POLICY)
        if head not in STATEMENT_HEADS:
            raise _ParseError(f"only SELECT/WITH statements are allowed, got {head!r}")
        if head == "with":
            self.parse_with()
            if not self.at_word("select"):
                raise _ParseError("a WITH clause must be followed by a SELECT")

        is_top = self.nest == 0
        self.parse_select_core(is_top)
        while self.at_word("union"):
            self.i += 1
            self.eat_word("all") or self.eat_word("distinct")
            if is_top:
                self.shape.has_union = True
            if not self.at_word("select"):
                raise _ParseError("UNION must be followed by SELECT")
            self.parse_select_core(is_top)

        # Trailing ORDER BY / LIMIT / OFFSET belong to the statement.
        if self.at_word("order"):
            self.i += 1
            self.expect_word("by")
            self.parse_expr_list(_SELECT_STOP | {"limit", "offset"}, is_top)
        if self.at_word("limit"):
            self.i += 1
            self.parse_expr({"offset", ")"}, is_top)
        if self.at_word("offset"):
            self.i += 1
            self.parse_expr({")"}, is_top)

    def parse_with(self) -> None:
        self.expect_word("with")
        self.eat_word("recursive")
        while True:
            name = self.expect_name()
            self.shape.ctes.append(name)
            # `WITH x (col, …) AS (…)` — an optional column list before AS.
            if self.at("(") and not self._at_subquery_start(1):
                self.i += 1
                self._skip_balanced(open_seen=True)
            self.expect_word("as")
            self.expect("(")
            self.nest += 1
            self.parse_statement()
            self.nest -= 1
            self.expect(")")
            if self.at(","):
                self.i += 1
                continue
            break

    def _at_subquery_start(self, k: int = 0) -> bool:
        tok = self.peek(k)
        return (tok is not None and tok.kind == "word"
                and tok.text.lower() in STATEMENT_HEADS)

    def _skip_balanced(self, *, open_seen: bool) -> None:
        """Consume tokens to the matching `)` (the `(` already consumed if
        `open_seen`). Only used for a CTE's column-name list."""
        depth = 1 if open_seen else 0
        while not self.eof():
            tok = self.peek()
            if tok.kind == "punct" and tok.text == "(":
                depth += 1
            elif tok.kind == "punct" and tok.text == ")":
                depth -= 1
                if depth == 0:
                    self.i += 1
                    return
            self.i += 1
        raise _ParseError("unbalanced parentheses")

    # -- select core
    def parse_select_core(self, is_top: bool) -> None:
        self.expect_word("select")
        if self.at_word("distinct", "all"):
            if self.at_word("distinct") and is_top:
                self.shape.has_distinct = True
            self.i += 1
        self.parse_expr_list(_SELECT_STOP, is_top)
        if self.at_word("from"):
            self.i += 1
            self.parse_from()
        if self.at_word("where"):
            self.i += 1
            if is_top:
                self.shape.has_where = True
            self.parse_expr(_WHERE_STOP, is_top)
        if self.at_word("group"):
            self.i += 1
            self.expect_word("by")
            if is_top:
                self.shape.has_group_by = True
            self.parse_expr_list(_GROUP_STOP, is_top)
        if self.at_word("having"):
            self.i += 1
            self.parse_expr(_HAVING_STOP, is_top)
        if self.at_word("window"):
            self.i += 1
            self.parse_expr(_SELECT_STOP, is_top)

    # -- from clause
    def parse_from(self) -> None:
        self.parse_from_item()
        while True:
            if self.at_word("natural"):
                self.i += 1
            if self.at_word("left", "right", "full", "inner", "cross", "outer"):
                self.i += 1
                self.eat_word("outer")
            if not self.at_word("join"):
                break
            self.i += 1
            self.parse_from_item()
            if self.at_word("on"):
                self.i += 1
                self.parse_expr(_JOIN_STOP, self.nest == 0)
            elif self.at_word("using"):
                self.i += 1
                self.expect("(")
                self._skip_balanced(open_seen=True)
        # A trailing comma-join (`FROM a, b`) is legal SQL; handle it.
        while self.at(","):
            self.i += 1
            self.parse_from_item()

    def parse_from_item(self) -> None:
        if self.at("("):
            if self._at_subquery_start(1):
                self.parse_subquery()
            else:                                   # a parenthesized join group
                self.i += 1
                self.parse_from()
                self.expect(")")
        else:
            name = self.expect_name()
            if name not in self.shape.ctes:
                self.shape.tables.append(name)
        if self.at_word("as"):
            self.i += 1
            if self.peek() is not None and self.peek().kind == "word":
                self.aliases.add(self.expect_name())
        elif self.peek() is not None and self.peek().kind == "word" \
                and self.peek().text.lower() not in _NON_ALIAS:
            self.aliases.add(self.expect_name())

    def parse_subquery(self) -> None:
        self.expect("(")
        self.nest += 1
        self.parse_statement()
        self.nest -= 1
        self.expect(")")

    # -- shallow expression scanning
    def parse_expr_list(self, stop: frozenset[str] | set[str], is_top: bool) -> None:
        self.parse_expr(set(stop) | {","}, is_top)
        while self.at(","):
            self.i += 1
            self.parse_expr(set(stop) | {","}, is_top)

    def parse_expr(self, stop: frozenset[str] | set[str], is_top: bool) -> None:
        start = self.i
        depth = 0
        while not self.eof():
            tok = self.peek()
            if tok.kind == "punct" and tok.text == "(":
                if self._at_subquery_start(1):
                    self.parse_subquery()
                    continue
                depth += 1
                self.i += 1
                continue
            if tok.kind == "punct" and tok.text == ")":
                if depth == 0:
                    break
                depth -= 1
                self.i += 1
                continue
            if tok.kind == "punct" and tok.text == ";":
                raise _ParseError("statement separator is not allowed")
            if depth == 0:
                if tok.kind == "punct" and tok.text == "," and "," in stop:
                    break
                if tok.kind == "word" and tok.text.lower() in stop:
                    break
                if is_top and tok.kind == "word" and tok.text.lower() in _AGGREGATES:
                    nxt = self.peek(1)
                    if nxt is not None and nxt.kind == "punct" and nxt.text == "(":
                        self.shape.has_aggregate = True
            self.i += 1
        # An empty expression is a syntax error: `SELECT FROM t` must fail the
        # grammar (repairable), not be laundered into a query with no columns.
        if self.i == start:
            near = self.peek()
            raise _ParseError(
                f"expected an expression near token {self.i}"
                + (f" ({near.text!r})" if near is not None else ""))


def _collect_columns(tokens: list[Token], shape: Shape,
                     aliases: Iterable[str] = ()) -> list[str]:
    """Column *candidates* — words that are not keywords, tables, CTE names,
    aliases, or function calls. Used only for the policy's *deny* check, so a
    miss is a missed deny and over-collection is a false deny; both directions
    are kept in view (the deny list is narrow, and every keyword is excluded)."""
    skip_names = ({t.lower() for t in shape.tables}
                  | {c.lower() for c in shape.ctes}
                  | {a.lower() for a in aliases})
    out: list[str] = []
    seen: set[str] = set()
    for i, tok in enumerate(tokens):
        if tok.kind != "word":
            continue
        w = tok.text.lower()
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if w in _SQL_KEYWORDS or w in skip_names:
            continue
        if nxt is not None and nxt.kind == "punct" and nxt.text in {"(", "."}:
            continue
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out


def sql_terms(sql: str) -> set[str]:
    """Every word and string-literal *value* the SQL contains, lowercased.

    String values are included (quotes stripped) so a definition guard can see
    `status <> 'DRAFT'` as the term `draft` — a discriminator written as a
    literal is still the discriminator."""
    terms: set[str] = set()
    for tok in tokenize(sql):
        if tok.kind == "word":
            terms.add(tok.text.lower())
        elif tok.kind == "str":
            terms.add(tok.text.strip("'").replace("''", "'").lower())
    return terms


def parse_readonly(sql_or_verdict: str | Verdict) -> Verdict:
    """Stage 2 — prove the statement is a single read-only `SELECT`/`WITH`.

    Accepts a normalized `Verdict` (from `normalize`) or a raw string. On
    success the verdict carries the `Shape` (tables, columns, clause flags) the
    policy and the rewriter consume.
    """
    v = sql_or_verdict if isinstance(sql_or_verdict, Verdict) else normalize(sql_or_verdict)
    if not v.ok:
        return v
    tokens = tokenize(v.sql)
    if not tokens:
        return _reject(v.raw, stage=STAGE_PARSE, error_kind=ERROR_INVALID_SQL,
                       cls=REPAIRABLE, message="empty SQL statement", sql=v.sql)
    # Residual defense-in-depth: the grammar is the allow-list, but a write
    # keyword the shallow expression scan skipped (inside a function call, an
    # odd position) is still refused here.
    for tok in tokens:
        if tok.kind == "word" and tok.text.lower() in WRITE_KEYWORDS:
            return _reject(v.raw, stage=STAGE_PARSE, error_kind=ERROR_INVALID_SQL,
                           cls=POLICY,
                           message=("write/DDL keywords are not allowed anywhere in a "
                                    "read-only statement"),
                           sql=v.sql, detail={"keyword": tok.text.lower()})
    try:
        shape = _Parser(tokens).parse()
    except _ParseError as exc:
        return _reject(v.raw, stage=STAGE_PARSE, error_kind=ERROR_INVALID_SQL,
                       cls=exc.cls, message=str(exc), sql=v.sql)
    v.shape = shape
    return v


def check_readonly(sql: str) -> Verdict:
    """`normalize` → `parse` in one call (the parser half of the choke point)."""
    return parse_readonly(normalize(sql))


# ================================================ §5 — policy: allow/deny in config
POLICY_ENV = "SALEOR_SQL_POLICY"
DEFAULT_POLICY_PATH = Path(__file__).resolve().parent.parent / "policy.yaml"

_BUILTIN_POLICY: dict[str, Any] = {
    "statement_allow": ["select", "with"],
    "tables_allow": [
        "order_order", "order_orderline", "product_product",
        "product_productvariant", "product_productvariantchannellisting",
        "account_user", "channel_channel",
    ],
    "columns_deny": {"account_user": ["password", "password_hash", "last_login",
                                      "auth_token", "token", "session_key",
                                      "api_key", "secret"]},
    "definitions": {},
    "row_limit": 100,
    "max_cost": 1_000_000,
    "statement_timeout_ms": 2_000,
    "lock_timeout_ms": 1_000,
    "idle_in_transaction_session_timeout_ms": 5_000,
}


@dataclass
class Policy:
    """The reviewable allow/deny document, loaded from `policy.yaml`.

    Three layers of denial, from §5: **statement**-level (the grammar from §4),
    **table**-level (a table outside `tables_allow` is invisible), and
    **column**-level (an allow-listed table may still hide PII). Plus the
    **definition** guard that turns Day 14's glossary into an enforcement
    artifact. Rules in config, not code — changeable without touching the
    executor.
    """

    tables_allow: list[str]
    columns_deny: dict[str, list[str]]
    definitions: dict[str, dict[str, Any]]
    statement_allow: list[str] = field(default_factory=lambda: ["select", "with"])
    row_limit: int = 100
    max_cost: float = 1_000_000.0
    statement_timeout_ms: int = 2_000
    lock_timeout_ms: int = 1_000
    idle_in_transaction_session_timeout_ms: int = 5_000
    source: str = "<builtin>"

    # ------------------------------------------------------------------ loaders
    @classmethod
    def from_dict(cls, raw: dict[str, Any], *, source: str = "<dict>") -> "Policy":
        if not isinstance(raw, dict):
            raise ValueError(f"{source}: policy must be a mapping")
        tbl = raw.get("tables_allow")
        if not tbl:
            raise ValueError(f"{source}: tables_allow must be a non-empty list")
        return cls(
            tables_allow=[str(t).strip().lower() for t in tbl],
            columns_deny={str(k).lower(): [str(c).lower() for c in (v or [])]
                          for k, v in (raw.get("columns_deny") or {}).items()},
            definitions={str(k): dict(v or {})
                         for k, v in (raw.get("definitions") or {}).items()},
            statement_allow=[str(s).lower() for s in
                             (raw.get("statement_allow") or ["select", "with"])],
            row_limit=int(raw.get("row_limit", 100)),
            max_cost=float(raw.get("max_cost", 1_000_000)),
            statement_timeout_ms=int(raw.get("statement_timeout_ms", 2_000)),
            lock_timeout_ms=int(raw.get("lock_timeout_ms", 1_000)),
            idle_in_transaction_session_timeout_ms=int(
                raw.get("idle_in_transaction_session_timeout_ms", 5_000)),
            source=source,
        )

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Policy":
        """Load from `path`, else `$SALEOR_SQL_POLICY`, else `policy.yaml` at the
        repo root. A missing file falls back to the built-in policy *by name*
        (the source records which, so a test can tell config from code)."""
        target = Path(path) if path is not None else _policy_path_from_env()
        if target is not None and target.exists():
            if yaml is None:                # pragma: no cover
                raise RuntimeError("PyYAML is required to load a policy file")
            with open(target, encoding="utf-8") as fh:
                raw = yaml.safe_load(fh) or {}
            return cls.from_dict(raw, source=str(target))
        return cls.from_dict(_BUILTIN_POLICY, source="<builtin>")

    # ------------------------------------------------------------------ checks
    def check(self, v: Verdict) -> Verdict:
        """Table → column → definition, fail-closed, *before* the database.

        Policy messages name the **class**, never the schema an attacker probed
        (the offending names ride in `detail` for the trace/operator only).
        """
        if not v.ok or v.shape is None:
            return v
        allowed = set(self.tables_allow)
        unknown = sorted({t for t in v.shape.tables if t not in allowed})
        if unknown:
            # X5 — a table outside the card is a policy decision the layer owns,
            # not an unknown-table error the engine happens to return.
            return _reject(v.raw, stage=STAGE_POLICY, error_kind=ERROR_POLICY,
                           cls=POLICY,
                           message=("the query references a table outside the "
                                    "allow-listed schema (policy: unknown table)"),
                           sql=v.sql, detail={"tables": unknown})

        columns = {c.lower() for c in v.shape.columns}
        denied: list[str] = []
        for table, cols in self.columns_deny.items():
            denied.extend(sorted(columns & {c.lower() for c in cols}))
        if denied:
            # X9 — PII is never readable, even where the table is allowed, and
            # even where the column does not exist: the deny is a property of
            # the column, not of the schema.
            return _reject(v.raw, stage=STAGE_POLICY, error_kind=ERROR_POLICY,
                           cls=POLICY,
                           message=("the query selects a column the policy denies "
                                    "(sensitive data is never readable)"),
                           sql=v.sql, detail={"columns": sorted(set(denied))})

        tokens = sql_terms(v.sql)
        for term, spec in self.definitions.items():
            metric_cols = {str(c).lower() for c in spec.get("columns", [])}
            if not (metric_cols & columns):
                continue
            require = [str(r).lower() for r in spec.get("require_all", [])]
            missing = [r for r in require if r not in tokens]
            if missing:
                # X6 — a silent pick is the correctness failure this guard
                # prevents; the fix is a *definition*, not a retry.
                return _reject(v.raw, stage=STAGE_POLICY, error_kind=ERROR_POLICY,
                               cls=POLICY,
                               message=str(spec.get("message") or (
                                   f"the definition of {term!r} is ambiguous; resolve "
                                   "it via the glossary")),
                               sql=v.sql, detail={"term": term, "missing": missing})
        return v

    # ------------------------------------------------------------------ cost
    def estimate_cost(self, v: Verdict, *,
                      explain_cost: Callable[[str], float] | None = None) -> float:
        """`EXPLAIN` when the store can provide one; a *shape* heuristic offline.

        The heuristic is recorded as exactly that — a heuristic, not a proof.
        On Postgres the store supplies `EXPLAIN (FORMAT JSON)`'s `Total Cost`;
        offline, an unbounded multi-table scan (no predicate, no grouping, no
        cap) is treated as over-budget, and everything else as a small nominal
        cost. The server `statement_timeout` remains the certain bound behind it.
        """
        if explain_cost is not None:
            try:
                est = explain_cost(v.sql)
                if est is not None:
                    return float(est)
            except Exception:               # an EXPLAIN failure must not become a crash
                pass
        if v.shape is None:
            return 0.0
        n = max(1, len(set(v.shape.tables)))
        unbounded_join = (n >= 2 and not v.shape.has_where
                          and not v.shape.has_group_by
                          and not v.shape.has_aggregate)
        if unbounded_join:
            return float("inf")
        return float(n * 10_000)

    def check_cost(self, v: Verdict, *,
                   explain_cost: Callable[[str], float] | None = None) -> Verdict:
        """X4 — catch the expensive query as a *plan*, before it runs."""
        if not v.ok:
            return v
        est = self.estimate_cost(v, explain_cost=explain_cost)
        v.cost_estimate = est
        v.max_cost = self.max_cost
        if est > self.max_cost:
            return _reject(v.raw, stage=STAGE_COST, error_kind=ERROR_COST,
                           cls=POLICY,
                           message=("the query's estimated cost exceeds the policy cap "
                                    "(resource guard) — narrow the query"),
                           sql=v.sql,
                           detail={"cost_estimate": est, "max_cost": self.max_cost})
        return v


def _policy_path_from_env() -> Path | None:
    import os
    configured = os.getenv(POLICY_ENV, "").strip()
    if configured:
        return Path(configured)
    return DEFAULT_POLICY_PATH


def load_policy(path: str | Path | None = None) -> Policy:
    """The §5 seam: one call, config in, a reviewable `Policy` out."""
    return Policy.load(path)


# ================================================ §6 — shape-aware LIMIT rewrite
def rewrite_limit(normalized: str, shape: Shape | None, row_limit: int) -> tuple[str, bool]:
    """Inject `LIMIT` into the *outermost* statement, respecting query shape.

    The trap this avoids, and a fixture: a `LIMIT` applied before an aggregation
    silently changes the answer from "total revenue" to "revenue of N arbitrary
    rows". So the rewrite is **skipped** for a `GROUP BY`/aggregate/`UNION`
    statement — the fetch cap in the store still bounds the rows.

    The injected value is `row_limit + 1` on purpose: the store fetches
    `limit + 1` rows so `truncated` stays a **measured** fact (Day 14's rule),
    and the extra row is dropped. The result never returns more than `row_limit`
    rows. Returns `(sql, injected)`.
    """
    if shape is None:
        return normalized, False
    if shape.has_group_by or shape.has_aggregate or shape.has_union:
        return normalized, False
    cap = max(1, int(row_limit)) + 1
    m = re.search(r"(?is)\blimit\s+(\d+)(\s+offset\s+\d+)?\s*$", normalized)
    if m is None:
        return normalized + f" LIMIT {cap}", True
    if int(m.group(1)) <= row_limit:
        return normalized, False
    return normalized[:m.start()] + f"LIMIT {cap}" + (m.group(2) or ""), True


# ================================================ §3 — the orchestrator
class SafeSql:
    """The ordered choke point, cheap checks before expensive ones.

    `inspect()` is normalize → parse → policy → cost, fail-closed at every step
    (a rejection short-circuits). `rewrite()` injects the shape-aware `LIMIT`.
    Execution and the server-side timeouts live on the connection; the total
    guarantee lives in the role.
    """

    def __init__(self, policy: Policy, *,
                 explain_cost: Callable[[str], float] | None = None) -> None:
        self.policy = policy
        self.explain_cost = explain_cost

    def inspect(self, sql: str) -> Verdict:
        v = normalize(sql)
        if not v.ok:
            return v
        v = parse_readonly(v)
        if not v.ok:
            return v
        head = tokenize(v.sql)[0].text.lower()
        if head not in set(self.policy.statement_allow):
            return _reject(v.raw, stage=STAGE_POLICY, error_kind=ERROR_POLICY,
                           cls=POLICY, message="the statement type is not allowed by policy",
                           sql=v.sql, detail={"head": head})
        v = self.policy.check(v)
        if not v.ok:
            return v
        return self.policy.check_cost(v, explain_cost=self.explain_cost)

    def rewrite(self, v: Verdict, *, row_limit: int) -> str:
        if not v.ok:
            return v.sql
        sql, _ = rewrite_limit(v.sql, v.shape, row_limit)
        return sql
