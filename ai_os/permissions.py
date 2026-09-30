"""Day 26 §6 — the permission model: grants, decisions, and a fail-closed loader.

Days 6–25 gave this repository **authority**, in four different files, for four
different reasons — `policy.yaml` + `SafeSql.inspect` (statements only),
`validate_args` (argument *shape* only), `ToolRegistry.scope()` (roles, not
principals) and the `saleor_read_only` role (a fact about the world). None of
them can answer the only question that matters at a new tool's registration:
*what is this thing allowed to do, and who is allowed to ask?* Day 26 answers it
with one **decision procedure** that runs on every call and every result.

The single idea, stated the way the day states it: **a threat model is a list of
worries and a permission system is a decision procedure.** Day 25 produced the
list. This module produces the procedure — `check_call` and `check_result` —
which reports a *fact* about the arguments and the result. It does **not** block:
the authority that refuses is `ToolRegistry` (`ai_os/tools.py`), and the record
that explains is `ai_os/audit.py`. Keeping the verbs separate is what stops this
file from growing into a god object that decides, blocks, logs, and reports all
at once.

Anatomy of a grant, kept small enough to read in one screen — six fields, each
answering a question that would otherwise be answered by whoever remembers to ask:

    tool   · effect (read|write|none) · data (public|internal|pii)
    args   · what may the argument look like (maxLength, pattern, enum, semantic)
    result · what may come back (declared columns, max_rows, evidence, redact)
    confirm· none | human  (a write may not exist without a human in its grant)

Four decisions carry the module:

1. **Deny by default, and make forgetting safe.** A missing grant, an unknown
   principal (which resolves to the `default` set, and whose *fallback is
   audited*), an unregistered tool, and a missing decision are all denials. The
   failure mode of forgetting is a denial you notice in a fixture, not a
   capability you discover in an incident.
2. **Fail closed and specific.** A *missing* grant is a denial; a *malformed*
   grant — a principal entry that is not a list, a grant for a tool nobody
   registered, an unknown `effect`/`data`, a `write` without `confirm: human` —
   is a **load-time error naming the key**. A permission file that half-parses
   is a permission file that silently grants half of what it looks like it
   grants, and the failure surfaces as an incident rather than a stack trace.
3. **Resolution order is a test, not a feature.** grant for the principal →
   the `default` set → deny `no_grant`. No inheritance, no wildcards, no prefix
   matching: a permission file with a pattern language in it is a permission file
   whose review becomes a proof obligation, and this repository builds before it
   abstracts.
4. **The result contract gives the half of the policy that had no subject a
   subject.** `columns_deny` is enforced on the *statement*; `get_product` /
   `get_order` / `get_customer` never call `SafeSql` at all. So every grant
   declares the columns its tool may return, `columns ∩ columns_deny = ∅` is
   checked, the row cap is checked, evidence tools are held to the *retrieval
   contract* (`doc_id`/`chunk_id` present on every row), and a violation keeps
   the **shape** and drops every **value** — because a partial answer is how a
   leak becomes untraceable, and Day 14's `validation: "rejected", executed:
   false` already showed that preserving a shape and removing values are
   different acts.

Everything here is `MECH`, offline, dependency-free, and clock-free: the `ts` is
stamped by the audit *writer*, not by the check, so a decision is a pure function
of `(principal, tool, args, perms)`. Cost: dictionary lookups, set membership,
comparisons the validator already made, and one `sha256` per decision. **0 chats,
0 embeds, 0 SQL, 0 rows.**
"""
from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

try:                                        # PyYAML ships with the repo's suites
    import yaml
except ImportError:                         # pragma: no cover — a missing dep is loud
    yaml = None

from ai_os.sql_safety import Policy, load_policy

# ============================================================ §3 — the vocabulary
# The two axes a tool declares about itself. `effect` says what it *does*; `data`
# says how sensitive what it *touches* is. They are independent: a read can touch
# PII (`get_customer` is read/pii), and a write can touch public data.
EFFECT_READ = "read"
EFFECT_WRITE = "write"
EFFECT_NONE = "none"
EFFECTS = frozenset({EFFECT_READ, EFFECT_WRITE, EFFECT_NONE})

DATA_PUBLIC = "public"
DATA_INTERNAL = "internal"
DATA_PII = "pii"
DATA_CLASSES = frozenset({DATA_PUBLIC, DATA_INTERNAL, DATA_PII})

# §2 — the human-approval seam. It does not exist yet (Day 27 owns it), and that
# is exactly why a `write` may not be declared without it: the first write-shaped
# tool this repository considers arrives already carrying a human in its grant.
CONFIRM_NONE = "none"
CONFIRM_HUMAN = "human"
CONFIRMS = frozenset({CONFIRM_NONE, CONFIRM_HUMAN})

# §5 decision 3 — deny by default at four levels, each with its own code. The
# code is part of the contract because `error_kind` and `rejection_class` already
# are: a security decision without a name is a decision nobody can grep for.
CODE_ALLOW = "allowed"
CODE_NO_GRANT = "no_grant"                       # registered, not granted to this principal
CODE_UNKNOWN_TOOL = "unknown_tool"               # not registered at all
CODE_UNKNOWN_PRINCIPAL = "unknown_principal"     # an unknown caller, ungranted via `default`
CODE_MISSING_DECISION = "missing_decision"       # execute() reached with no decision
CODE_RESULT_VIOLATION = "result_violation"       # a result that broke its contract

# ============================================================ §6 — the config knobs
PERMISSIONS_ENV = "PERMISSIONS"                  # `none` restores the ungoverned path
PERMISSIONS_PATH_ENV = "PERMISSIONS_PATH"        # default permissions.yaml (beside policy.yaml)
PRINCIPAL_ENV = "AGENT_PRINCIPAL"                # beside Day 17's STATE_PRINCIPAL
DEFAULT_PRINCIPAL = "default"                    # the strict fallback for an unknown caller
DEFAULT_PERMISSIONS_PATH = (Path(__file__).resolve().parent.parent
                            / "permissions.yaml")

_OFF_VALUES = {"0", "off", "false", "no", "none", "disabled"}


def enabled() -> bool:
    """The layer's single switch. Off ⇒ no check, no result contract, no audit.

    Reads `PERMISSIONS`; anything in `{"0","off","false","no","none","disabled"}`
    turns the layer off, everything else (including unset) leaves it on. The
    switch exists so the C9 control can prove the layer is *passive*: with it off
    a legitimate request is byte-identical and the audit log is empty.
    """
    return os.getenv(PERMISSIONS_ENV, "1").strip().lower() not in _OFF_VALUES


@contextmanager
def governance(on: bool) -> Iterator[None]:
    """Scoped on/off switch (the C9 control), mirroring Day 25's `firewall`."""
    prev = os.environ.get(PERMISSIONS_ENV)
    os.environ[PERMISSIONS_ENV] = "1" if on else "none"
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop(PERMISSIONS_ENV, None)
        else:
            os.environ[PERMISSIONS_ENV] = prev


def permissions_path_from_env() -> Path:
    configured = os.getenv(PERMISSIONS_PATH_ENV, "").strip()
    return Path(configured) if configured else DEFAULT_PERMISSIONS_PATH


def principal_from_env() -> str:
    return os.getenv(PRINCIPAL_ENV, "").strip() or DEFAULT_PRINCIPAL


def _default_registered_tools() -> tuple[str, ...]:
    """The tool names a grant may name — imported lazily to avoid a cycle.

    `ai_os/tools.py` imports this module at top level; this module must therefore
    not import `tools` at top level. The import happens only when a file is
    loaded, by which point `tools` is fully constructed.
    """
    from ai_os.tools import DEFAULT_TOOL_NAMES
    return DEFAULT_TOOL_NAMES


# ============================================================ §3 — the grant shapes

@dataclass(frozen=True)
class ArgRule:
    """What one argument may look like — **exactly one** kind of check.

    Decision 4: a shape check *or* a semantic check, never both. `semantic=True`
    marks the argument whose authority is a real semantic layer (`run_saleor_sql`
    .sql is a query — `SafeSql.inspect` owns it, and re-parsing it here would be
    Day 15's problem arriving twice); a `pattern`/`maxLength`/`enum` marks an
    argument whose authority is a shape, because its only semantic layer is
    `_literal()`'s escaping and pretending a pattern is a substitute for escaping
    (or vice versa) misdescribes what is standing where.
    """

    maxLength: int | None = None
    pattern: str | None = None
    enum: tuple[str, ...] | None = None
    minimum: int | None = None
    maximum: int | None = None
    semantic: bool = False

    _ALLOWED = frozenset({"maxLength", "pattern", "enum", "minimum", "maximum",
                          "semantic"})

    @classmethod
    def from_dict(cls, arg: str, raw: Any, *, tool: str, source: str) -> "ArgRule":
        if raw is None:
            return cls()
        if not isinstance(raw, dict):
            raise ValueError(f"{source}: tools.{tool}.args.{arg} must be a mapping")
        unknown = [k for k in raw if k not in cls._ALLOWED]
        if unknown:
            raise ValueError(f"{source}: tools.{tool}.args.{arg}: unknown key(s) "
                             f"{unknown} (known: {sorted(cls._ALLOWED)})")
        max_len = raw.get("maxLength")
        if max_len is not None and (not isinstance(max_len, int)
                                    or isinstance(max_len, bool) or max_len < 1):
            raise ValueError(f"{source}: tools.{tool}.args.{arg}.maxLength must be "
                             f"a positive integer, got {max_len!r}")
        pattern = raw.get("pattern")
        if pattern is not None:
            if not isinstance(pattern, str):
                raise ValueError(f"{source}: tools.{tool}.args.{arg}.pattern must "
                                 "be a string")
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"{source}: tools.{tool}.args.{arg}.pattern is not "
                                 f"a valid regular expression: {exc}") from exc
        enum = raw.get("enum")
        if enum is not None:
            if not isinstance(enum, list) or not all(isinstance(e, str) for e in enum):
                raise ValueError(f"{source}: tools.{tool}.args.{arg}.enum must be a "
                                 "list of strings")
            enum = tuple(enum)
        for bound in ("minimum", "maximum"):
            val = raw.get(bound)
            if val is not None and (not isinstance(val, int) or isinstance(val, bool)):
                raise ValueError(f"{source}: tools.{tool}.args.{arg}.{bound} must be "
                                 f"an integer, got {val!r}")
        semantic = raw.get("semantic", False)
        if not isinstance(semantic, bool):
            raise ValueError(f"{source}: tools.{tool}.args.{arg}.semantic must be a "
                             "boolean")
        return cls(maxLength=max_len, pattern=pattern, enum=enum,
                   minimum=raw.get("minimum"), maximum=raw.get("maximum"),
                   semantic=semantic)


@dataclass(frozen=True)
class ResultRule:
    """What may come *back*. `columns="*"` means "any column the query declares
    (still intersected with `columns_deny`)". `columns=None` with `evidence=True`
    means the tool returns *evidence records*, not columns, and is held to the
    retrieval contract instead (`doc_id`/`chunk_id` present on every row)."""

    columns: tuple[str, ...] | str | None = None
    max_rows: int | None = None
    evidence: bool = False
    redact: tuple[str, ...] = ()

    _ALLOWED = frozenset({"columns", "max_rows", "evidence", "redact"})

    @classmethod
    def from_dict(cls, raw: Any, *, tool: str, source: str) -> "ResultRule":
        if raw is None:
            return cls()
        if not isinstance(raw, dict):
            raise ValueError(f"{source}: tools.{tool}.result must be a mapping")
        unknown = [k for k in raw if k not in cls._ALLOWED]
        if unknown:
            raise ValueError(f"{source}: tools.{tool}.result: unknown key(s) "
                             f"{unknown} (known: {sorted(cls._ALLOWED)})")
        columns = raw.get("columns")
        if columns is not None and columns != "*":
            if not isinstance(columns, list) or not all(isinstance(c, str) for c in columns):
                raise ValueError(f"{source}: tools.{tool}.result.columns must be a "
                                 "list of strings or \"*\"")
            columns = tuple(columns)
        max_rows = raw.get("max_rows")
        if max_rows is not None and (not isinstance(max_rows, int)
                                     or isinstance(max_rows, bool) or max_rows < 0):
            raise ValueError(f"{source}: tools.{tool}.result.max_rows must be a "
                             "non-negative integer")
        evidence = raw.get("evidence", False)
        if not isinstance(evidence, bool):
            raise ValueError(f"{source}: tools.{tool}.result.evidence must be a boolean")
        redact = raw.get("redact", [])
        if not isinstance(redact, list) or not all(isinstance(c, str) for c in redact):
            raise ValueError(f"{source}: tools.{tool}.result.redact must be a list "
                             "of column names")
        return cls(columns=columns, max_rows=max_rows, evidence=evidence,
                   redact=tuple(redact))


@dataclass(frozen=True)
class Grant:
    """One tool's declaration — six fields, and the two that get argued about are
    `data` and `result`, because they are what make the result check possible."""

    tool: str
    effect: str
    data: str
    args: dict[str, ArgRule] = field(default_factory=dict)
    result: ResultRule = field(default_factory=ResultRule)
    confirm: str = CONFIRM_NONE

    _ALLOWED = frozenset({"effect", "data", "args", "result", "confirm"})

    @classmethod
    def from_dict(cls, tool: str, raw: Any, *, source: str) -> "Grant":
        if not isinstance(raw, dict):
            raise ValueError(f"{source}: tools.{tool} must be a mapping")
        unknown = [k for k in raw if k not in cls._ALLOWED]
        if unknown:
            raise ValueError(f"{source}: tools.{tool}: unknown key(s) {unknown} "
                             f"(known: {sorted(cls._ALLOWED)})")
        effect = str(raw.get("effect") or "").strip().lower()
        if effect not in EFFECTS:
            raise ValueError(f"{source}: tools.{tool}.effect must be one of "
                             f"{sorted(EFFECTS)}, got {effect!r}")
        data = str(raw.get("data") or "").strip().lower()
        if data not in DATA_CLASSES:
            raise ValueError(f"{source}: tools.{tool}.data must be one of "
                             f"{sorted(DATA_CLASSES)}, got {data!r}")
        confirm = str(raw.get("confirm") or CONFIRM_NONE).strip().lower()
        if confirm not in CONFIRMS:
            raise ValueError(f"{source}: tools.{tool}.confirm must be one of "
                             f"{sorted(CONFIRMS)}, got {confirm!r}")
        if effect == EFFECT_WRITE and confirm != CONFIRM_HUMAN:
            # §2/§3 — the strongest form of read-only is a registry in which no
            # write-shaped tool was ever registered; the second strongest is a
            # grant for one that cannot be written without naming a human.
            raise ValueError(f"{source}: tools.{tool} declares effect: write without "
                             "confirm: human (a write carries a human in its grant)")
        args_raw = raw.get("args") or {}
        if not isinstance(args_raw, dict):
            raise ValueError(f"{source}: tools.{tool}.args must be a mapping")
        args = {a: ArgRule.from_dict(a, r, tool=tool, source=source)
                for a, r in args_raw.items()}
        result = ResultRule.from_dict(raw.get("result"), tool=tool, source=source)
        return cls(tool=tool, effect=effect, data=data, args=args, result=result,
                   confirm=confirm)

    def as_dict(self) -> dict[str, Any]:
        return {"tool": self.tool, "effect": self.effect, "data": self.data,
                "confirm": self.confirm,
                "args": {a: {k: v for k, v in r.__dict__.items() if v is not None
                             and v is not False}
                         for a, r in self.args.items()},
                "result": {"columns": self.result.columns,
                           "max_rows": self.result.max_rows,
                           "evidence": self.result.evidence,
                           "redact": list(self.result.redact)}}


@dataclass(frozen=True)
class Principal:
    """A caller and the tools it may reach. Deliberately a list, not a rule."""

    name: str
    tools: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "tools": list(self.tools)}


@dataclass
class Decision:
    """A verdict with a name attached. `allow=False` is the default posture.

    `unknown_principal_fallback` records that the caller's name appeared in no
    entry and the `default` set was substituted — the fallback is a fact the trace
    and the audit log carry, so a fallback *storm* is visible rather than silent.
    """

    allow: bool
    code: str
    reason: str
    grant: Grant | None = None
    effect: str | None = None
    data: str | None = None
    unknown_principal_fallback: bool = False
    arg_keys: tuple[str, ...] = ()
    redacted: tuple[str, ...] = ()

    @property
    def tool(self) -> str | None:
        return self.grant.tool if self.grant else None

    def meta(self) -> dict[str, Any]:
        """The decision *on* the `ToolResult` — `{"decision","code","effect","data"}`.

        Kept to four keys on purpose: the field travels with the value so no
        caller can drop it, and it is the seam the trace promotes.
        """
        return {"decision": "allow" if self.allow else "deny", "code": self.code,
                "effect": self.effect, "data": self.data}


# ============================================================ §6 — the permission set

@dataclass
class PermissionSet:
    """The reviewable allow/deny document, loaded from `permissions.yaml`.

    `principals` is the grant *assignment* (`name → the tools it may reach`);
    `tools` is the tool *declaration* (effect, data class, argument rules, result
    contract). `policy` carries the SQL allow-list and PII deny-list the result
    check intersects against, so `check_result` needs nothing else.
    """

    principals: dict[str, Principal]
    tools: dict[str, Grant]
    policy: Policy | None = None
    source: str = "<dict>"

    # ------------------------------------------------------------------ loaders
    @classmethod
    def from_dict(cls, raw: dict[str, Any], *, source: str = "<dict>",
                  registered: Iterable[str] | None = None,
                  policy: Policy | None = None) -> "PermissionSet":
        if not isinstance(raw, dict):
            raise ValueError(f"{source}: permissions must be a mapping")
        allowed_top = {"version", "principals", "tools"}
        unknown_top = [k for k in raw if k not in allowed_top]
        if unknown_top:
            raise ValueError(f"{source}: unknown key(s) {unknown_top} "
                             f"(known: {sorted(allowed_top)})")
        reg = set(registered) if registered is not None else set(_default_registered_tools())

        tools_raw = raw.get("tools")
        if not isinstance(tools_raw, dict) or not tools_raw:
            raise ValueError(f"{source}: 'tools' must be a non-empty mapping")
        grants: dict[str, Grant] = {}
        for tname, traw in tools_raw.items():
            if tname not in reg:
                raise ValueError(f"{source}: grant for unregistered tool {tname!r} "
                                 f"(registered: {sorted(reg)})")
            grants[tname] = Grant.from_dict(tname, traw, source=source)

        principals_raw = raw.get("principals")
        if not isinstance(principals_raw, dict) or not principals_raw:
            raise ValueError(f"{source}: 'principals' must be a non-empty mapping")
        principals: dict[str, Principal] = {}
        for pname, plist in principals_raw.items():
            if not isinstance(plist, list) or not all(isinstance(t, str) for t in plist):
                raise ValueError(f"{source}: principals.{pname} must be a list of "
                                 "tool names")
            bad = [t for t in plist if t not in grants]
            if bad:
                raise ValueError(f"{source}: principals.{pname} names ungranted "
                                 f"tool(s) {bad}")
            principals[pname] = Principal(name=pname, tools=tuple(plist))
        if DEFAULT_PRINCIPAL not in principals:
            raise ValueError(f"{source}: principals must declare a "
                             f"{DEFAULT_PRINCIPAL!r} set (the strict fallback)")

        ps = cls(principals=principals, tools=grants,
                 policy=policy if policy is not None else load_policy(), source=source)
        ps._cross_check()
        return ps

    @classmethod
    def load(cls, path: str | Path | None = None, *,
             registered: Iterable[str] | None = None,
             policy: Policy | None = None) -> "PermissionSet":
        """Load `permissions.yaml`, else `$PERMISSIONS_PATH`. A missing file is a
        `ValueError` naming the env var and the default path — never a silent
        ungoverned load."""
        target = Path(path) if path is not None else permissions_path_from_env()
        if not target.exists():
            raise ValueError(f"{PERMISSIONS_PATH_ENV} / default "
                             f"{DEFAULT_PERMISSIONS_PATH}: permissions file not found "
                             f"at {target}")
        if yaml is None:                    # pragma: no cover
            raise RuntimeError("PyYAML is required to load a permissions file")
        with open(target, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        return cls.from_dict(raw, source=str(target), registered=registered,
                             policy=policy)

    def _cross_check(self) -> None:
        """One rule table, not two that can disagree.

        §4 — the schema card's seven tables *are* `tables_allow`'s seven tables,
        and `describe_saleor_schema`'s closed `table` enum must be exactly that
        set. The coincidence was already true; today it becomes a load-time
        invariant, and a future edit that breaks it fails here, naming the layer
        that stopped agreeing.
        """
        from ai_os.data import SCHEMA_CARD
        card = set(SCHEMA_CARD)
        allow = self.allow_tables()
        if card != set(allow):
            raise ValueError(f"{self.source}: SCHEMA_CARD and policy.tables_allow "
                             f"disagree — card∖policy={sorted(card - set(allow))}, "
                             f"policy∖card={sorted(set(allow) - card)}")
        grant = self.tools.get("describe_saleor_schema")
        if grant is not None:
            rule = grant.args.get("table")
            if rule is not None and rule.enum is not None and set(rule.enum) != card:
                raise ValueError(f"{self.source}: describe_saleor_schema.table enum "
                                 f"must equal the closed SCHEMA_CARD set "
                                 f"(missing: {sorted(card - set(rule.enum))})")

    # ------------------------------------------------------------------ lookups
    def is_known(self, name: str | None) -> bool:
        return str(name) in self.principals

    def for_principal(self, name: str | None) -> Principal:
        """The principal's entry, or the `default` set for an unknown caller.

        An unknown principal is **not** an error: refusing a brand-new caller
        outright is a policy decision nobody asked for, and crashing on one turns
        a config typo into an outage. It resolves to `default` and the resolution
        is audited by the caller.
        """
        key = str(name) if self.is_known(name) else DEFAULT_PRINCIPAL
        return self.principals[key]

    def grant(self, tool: str) -> Grant | None:
        return self.tools.get(str(tool))

    def tools_for(self, name: str | None) -> tuple[str, ...]:
        return self.for_principal(name).tools

    def granted_tools(self, name: str | None) -> list[str]:
        """The principal's granted slice, in registration order (the contract
        surface `schemas()` returns). An ungranted tool is never named."""
        wanted = set(self.tools_for(name))
        return [t for t in self.tools if t in wanted]

    def allow_tables(self) -> list[str]:
        return list(self.policy.tables_allow) if self.policy is not None else []

    def denied_columns(self) -> frozenset[str]:
        if self.policy is None:
            return frozenset()
        return frozenset(c.lower() for cols in self.policy.columns_deny.values()
                         for c in cols)

    def as_dict(self) -> dict[str, Any]:
        return {"source": self.source,
                "principals": {n: p.as_dict() for n, p in self.principals.items()},
                "tools": {t: g.as_dict() for t, g in self.tools.items()}}


def default_permission_set() -> PermissionSet:
    """The shipped grant file — the one `build_default_registry` reads for grants."""
    return PermissionSet.load()


# ============================================================ §5 — the two checks

def _arg_keys(args: Any) -> tuple[str, ...]:
    if isinstance(args, dict):
        return tuple(sorted(str(k) for k in args))
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
        except (json.JSONDecodeError, TypeError):
            return ()
        return tuple(sorted(str(k) for k in parsed)) if isinstance(parsed, dict) else ()
    return ()


def check_call(principal: str | None, name: str, args: Any = None, *,
               perms: PermissionSet) -> Decision:
    """May *this* principal call *this* tool? A **fact**, not a refusal.

    Resolution order is one line and is a test: **grant for the principal → the
    `default` set → deny `no_grant`.** `args` is accepted so the decision can
    describe the call without the caller re-deriving it; the check itself is
    authorization-only, because argument *shape* is `validate_args`' single job
    (decision 4: exactly one check per argument).
    """
    known = perms.is_known(principal)
    effective = perms.for_principal(principal)
    grant = perms.grant(name)
    keys = _arg_keys(args)
    if grant is None:
        return Decision(False, CODE_UNKNOWN_TOOL,
                        f"no grant declares tool {name!r}", None,
                        unknown_principal_fallback=not known, arg_keys=keys)
    if name not in effective.tools:
        # A known caller missing a grant is `no_grant`; an unknown caller missing
        # it (via the `default` fallback) is `unknown_principal` — the two are
        # different facts with different owners.
        code = CODE_NO_GRANT if known else CODE_UNKNOWN_PRINCIPAL
        return Decision(False, code,
                        f"tool {name!r} is not granted to principal "
                        f"{effective.name!r}",
                        grant, effect=grant.effect, data=grant.data,
                        unknown_principal_fallback=not known, arg_keys=keys)
    return Decision(True, CODE_ALLOW,
                    f"granted to {effective.name!r} "
                    f"(effect={grant.effect}, data={grant.data})",
                    grant, effect=grant.effect, data=grant.data,
                    unknown_principal_fallback=not known, arg_keys=keys)


def _columns_of(data: dict[str, Any]) -> list[str]:
    """The result's column *names* — from a list (data records) or a dict
    (the schema card's `columns`)."""
    cols = data.get("columns")
    if isinstance(cols, dict):
        return list(cols)
    if isinstance(cols, list):
        return [str(c) for c in cols]
    return []


def check_result(name: str, result: Any, *, perms: PermissionSet,
                 decision: Decision | None = None) -> Decision:
    """May this *result* reach the model? Declared columns, the deny-list
    intersection, the row cap, the retrieval contract, and the redactions.

    A violation is a **policy** verdict with the values dropped downstream; a
    redaction is an `allow` carrying the column names to drop. This function
    reports; `ToolRegistry.execute` is what refuses and what keeps the shape.
    """
    grant = perms.grant(name)
    if grant is None:
        return Decision(False, CODE_UNKNOWN_TOOL, f"no grant declares tool {name!r}",
                        None)
    rule = grant.result
    data = getattr(result, "data", None)
    evidence = list(getattr(result, "evidence", None) or [])
    fallback = bool(decision and decision.unknown_principal_fallback)
    keys = tuple(decision.arg_keys) if decision else ()
    redacted: tuple[str, ...] = ()

    # --- the data record's contract (declared columns, deny intersection, cap).
    if isinstance(data, dict) and ("columns" in data or "table" in data):
        cols = _columns_of(data)
        if rule.columns != "*" and rule.columns is not None:
            declared = set(rule.columns)
            undeclared = [c for c in cols if c not in declared]
            if undeclared:
                return Decision(False, CODE_RESULT_VIOLATION,
                                f"result carries undeclared column(s) {undeclared}",
                                grant, effect=grant.effect, data=grant.data,
                                unknown_principal_fallback=fallback, arg_keys=keys)
        denied = sorted(set(c.lower() for c in cols) & perms.denied_columns())
        if denied:
            return Decision(False, CODE_RESULT_VIOLATION,
                            f"result carries policy-denied column(s) {denied}",
                            grant, effect=grant.effect, data=grant.data,
                            unknown_principal_fallback=fallback, arg_keys=keys)
        table = data.get("table")
        if table is not None and str(table) not in set(perms.allow_tables()):
            return Decision(False, CODE_RESULT_VIOLATION,
                            f"schema result names a table outside the allow-list "
                            f"({table!r})",
                            grant, effect=grant.effect, data=grant.data,
                            unknown_principal_fallback=fallback, arg_keys=keys)
        row_count = int(data.get("row_count") if data.get("row_count") is not None
                        else len(data.get("rows") or []))
        if rule.max_rows is not None and row_count > rule.max_rows:
            return Decision(False, CODE_RESULT_VIOLATION,
                            f"result returned {row_count} rows > max_rows="
                            f"{rule.max_rows}",
                            grant, effect=grant.effect, data=grant.data,
                            unknown_principal_fallback=fallback, arg_keys=keys)
        redacted = tuple(c for c in rule.redact if c in set(cols))

    # --- the retrieval contract (evidence rows, not columns).
    if rule.evidence or evidence:
        if rule.max_rows is not None and len(evidence) > rule.max_rows:
            return Decision(False, CODE_RESULT_VIOLATION,
                            f"result returned {len(evidence)} evidence rows > "
                            f"max_rows={rule.max_rows}",
                            grant, effect=grant.effect, data=grant.data,
                            unknown_principal_fallback=fallback, arg_keys=keys)
        for row in evidence:
            if not isinstance(row, dict) or "doc_id" not in row or "chunk_id" not in row:
                return Decision(False, CODE_RESULT_VIOLATION,
                                "an evidence row is missing doc_id/chunk_id (the "
                                "retrieval contract)",
                                grant, effect=grant.effect, data=grant.data,
                                unknown_principal_fallback=fallback, arg_keys=keys)

    return Decision(True, CODE_ALLOW, "result contract satisfied", grant,
                    effect=grant.effect, data=grant.data,
                    unknown_principal_fallback=fallback, arg_keys=keys,
                    redacted=redacted)
