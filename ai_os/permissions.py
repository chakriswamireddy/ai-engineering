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
# WS8 — the first executing tool: `run_tests` runs code (neither a read nor a
# write of data), so it declares `effect: execute`. Like `write`, `execute`
# requires `confirm: human` at load time — executing without an approval is a
# boot error, not a runtime surprise.
EFFECT_EXECUTE = "execute"
EFFECTS = frozenset({EFFECT_READ, EFFECT_WRITE, EFFECT_NONE, EFFECT_EXECUTE})

DATA_PUBLIC = "public"
DATA_INTERNAL = "internal"
DATA_PII = "pii"
DATA_CLASSES = frozenset({DATA_PUBLIC, DATA_INTERNAL, DATA_PII})

# §2 — the human-approval seam. Day 27 owns the other side of it: `confirm: human`
# is read as "whose approval is required" (a *role*, resolved as a grant), and the
# action it gates is classified by **reversibility** rather than by `effect`.
CONFIRM_NONE = "none"
CONFIRM_HUMAN = "human"
CONFIRMS = frozenset({CONFIRM_NONE, CONFIRM_HUMAN})

# ============================================================ Day 27 §2 — the axis
# `effect` says what an action *does*; `reversibility` says whether the prior world
# comes back, and the risk ordering and the effect ordering disagree — in this very
# repository. `get_customer` is `read`/`pii` and reversible (no external effect);
# a bulk **export** of the same rows is `read`/`pii` and **irreversible** (the rows
# left the process, and disclosure cannot be undone); an append to the store's own
# action log is a `write` and fully reversible (append a correcting record). So the
# load-time rule generalises from Day 26's `write ⇒ confirm: human` to
# **`reversible ⇒ nothing · `compensatable` ⇒ a declared offset · `irreversible`
# ⇒ confirm: human`**, whatever the `effect`, and Day 26's write rule survives
# verbatim as the derived case `effect: write, reversibility: irreversible`.
#
# The failure direction is the argument for making the field mandatory rather than
# defaulted: with `effect` as the axis, forgetting `confirm` on a new read meant
# *no gate and no failure* — the export ships silently, in a config diff nobody
# reads closely. With `reversibility` as the axis and a deny-by-default loader, a
# grant that declares no `reversibility` is not "reversible"; it is **invalid at
# load time**, naming the tool. Forgetting now produces a boot error rather than a
# disclosure — the same directional argument as Day 26's allow-list, one field over.
REVERSIBILITY_REVERSIBLE = "reversible"
REVERSIBILITY_COMPENSATABLE = "compensatable"
REVERSIBILITY_IRREVERSIBLE = "irreversible"
REVERSIBILITIES = frozenset({REVERSIBILITY_REVERSIBLE,
                             REVERSIBILITY_COMPENSATABLE,
                             REVERSIBILITY_IRREVERSIBLE})

# The reversibility classes that must **name the action that undoes them**. The
# operational test is the same in all three cases and it is not the label: *name
# the undo, name who performs it, name the deadline.* A `reversible` grant with no
# declared compensation is a wish, and Day 26's own lesson applies one field over:
# a declaration nobody cross-checks is a comment.
COMPENSATION_REQUIRED = (REVERSIBILITY_REVERSIBLE, REVERSIBILITY_COMPENSATABLE)

# The two roles the approval workflow resolves against this file. `approver_role`
# is per-grant (whose approval this action requires); `executor` is the principal
# that may apply a staged effect, which is deliberately **not** the requester —
# "who approved" and "who ran it" are two answerable questions with two answers.
ROLE_EXECUTOR = "executor"

# The declared-compensation vocabulary: `undo` (the named action that restores the
# prior world), `performer` (who runs it), and an optional `window_s`.
_COMPENSATION_KEYS = frozenset({"undo", "performer", "window_s"})

# Day 27 — the *harness-only* declarations. A grant whose tool is not registered is
# a load error (§3, C1b): a tool added tomorrow must arrive denied rather than
# pre-authorized. The one exception is declared **in the file itself** — a grant
# carrying `harness_only: true` names a tool the fixtures register and the shipped
# registry never does, so the day can ship the gate's real client (and the
# reviewable fact that its authority is a *read*) without registering an
# irreversible tool into the product. The trade is explicit and fail-closed: a
# harness-only grant may not be assigned to any principal in the same file, so the
# shipped system denies it to everyone with `no_grant` while the fixtures can grant
# it and exercise the gate.
HARNESS_ONLY_KEY = "harness_only"

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
    # Day 27 — where the result *goes*. `egress: external` is the declaration that
    # makes an irreversible read reviewable: the same rows `get_customer` returns
    # into a transcript leave the process from an export, and `reversibility` is
    # what makes that difference a load-time fact instead of a footnote.
    egress: str | None = None

    _ALLOWED = frozenset({"columns", "max_rows", "evidence", "redact", "egress"})

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
        egress = raw.get("egress")
        if egress is not None and (not isinstance(egress, str) or not egress.strip()):
            raise ValueError(f"{source}: tools.{tool}.result.egress must be a "
                             "non-empty string")
        return cls(columns=columns, max_rows=max_rows, evidence=evidence,
                   redact=tuple(redact), egress=egress)


@dataclass(frozen=True)
class Grant:
    """One tool's declaration — six fields, and the two that get argued about are
    `data` and `result`, because they are what make the result check possible.

    Day 27 adds three, additively, and each answers a question a human-in-the-loop
    workflow would otherwise answer by accident: `reversibility` (does the prior
    world come back — the axis `confirm` now keys on), `approver_role` (whose
    approval this action requires, resolved as a *grant* rather than asserted as an
    event), and `compensation` (the named action that undoes it, required for any
    reversibility claim). `harness_only` marks a declaration the *fixtures* register
    and the shipped registry never does.
    """

    tool: str
    effect: str
    data: str
    args: dict[str, ArgRule] = field(default_factory=dict)
    result: ResultRule = field(default_factory=ResultRule)
    confirm: str = CONFIRM_NONE
    # Day 27 — the axis. The empty default is a *sentinel for "absent"*, never an
    # implied class: a directly constructed grant with `reversibility=""` cannot be
    # mistaken for a reversible one (nothing on the gate path reads it as a class).
    reversibility: str = ""
    approver_role: str | None = None
    compensation: dict[str, Any] | None = None
    harness_only: bool = False

    _ALLOWED = frozenset({"effect", "data", "args", "result", "confirm",
                          "reversibility", "approver_role", "compensation",
                          HARNESS_ONLY_KEY})

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
        reversibility = _reversibility_of(tool, raw, confirm=confirm, source=source)
        approver_role = raw.get("approver_role")
        if approver_role is not None:
            if not isinstance(approver_role, str) or not approver_role.strip():
                raise ValueError(f"{source}: tools.{tool}.approver_role must be a "
                                 "non-empty string")
            approver_role = approver_role.strip()
        if confirm != CONFIRM_HUMAN and approver_role:
            raise ValueError(f"{source}: tools.{tool} declares approver_role without "
                             "confirm: human — a role with no gate is a comment")
        # NOTE (Day 27): "`confirm: human` requires an `approver_role`" is *not*
        # checked here. A role is only meaningful against the file's
        # `approver_roles` table, so the check lives at the file loader
        # (`PermissionSet._check_roles`) where that table exists — a refusal at the
        # only level that can be complete, rather than a half-check on a bare grant
        # that Day 26's own suite (and its forward-looking C2e) constructs by hand.
        if effect == EFFECT_WRITE and "confirm" not in raw:
            # §2's second clause, and the generalization of Day 26's write rule: a
            # `write` may not be *downgraded* to `reversible` by assertion alone
            # without that assertion being visible in the reviewable artifact.
            raise ValueError(f"{source}: tools.{tool} declares effect: write without "
                             "an explicit confirm — a write states `confirm: human` "
                             "(irreversible) or `confirm: none` (reversible, an "
                             "acknowledged downgrade) in the file itself")
        if effect == EFFECT_EXECUTE and "confirm" not in raw:
            # WS8 — executing code is never ungated by omission: an `execute`
            # grant states its confirm in the file, like a write.
            raise ValueError(f"{source}: tools.{tool} declares effect: execute without "
                             "an explicit confirm — execution states "
                             "`confirm: human` in the file itself")
        if reversibility == REVERSIBILITY_IRREVERSIBLE and confirm != CONFIRM_HUMAN:
            # Day 26's rule, re-expressed as the *derived case*: with reversibility
            # as the axis, `effect: write ⇒ confirm: human` is the instance
            # `effect: write, reversibility: irreversible`, and yesterday's rule
            # was never thrown away — only subsumed.
            raise ValueError(f"{source}: tools.{tool} declares reversibility: "
                             "irreversible without confirm: human (an action nothing "
                             "restores arrives carrying a human in its grant)")
        args_raw = raw.get("args") or {}
        if not isinstance(args_raw, dict):
            raise ValueError(f"{source}: tools.{tool}.args must be a mapping")
        args = {a: ArgRule.from_dict(a, r, tool=tool, source=source)
                for a, r in args_raw.items()}
        result = ResultRule.from_dict(raw.get("result"), tool=tool, source=source)
        harness_only = raw.get(HARNESS_ONLY_KEY, False)
        if not isinstance(harness_only, bool):
            raise ValueError(f"{source}: tools.{tool}.{HARNESS_ONLY_KEY} must be a "
                             "boolean")
        return cls(tool=tool, effect=effect, data=data, args=args, result=result,
                   confirm=confirm, reversibility=reversibility,
                   approver_role=approver_role,
                   compensation=cls._compensation(tool, raw, reversibility, source),
                   harness_only=harness_only)

    @staticmethod
    def _compensation(tool: str, raw: dict[str, Any], reversibility: str,
                      source: str) -> dict[str, Any] | None:
        """The declared undo — required by any reversibility claim, refused on an
        irreversible action.

        `undo` names the action *of this system* that restores the prior world,
        `performer` names who runs it, and `window_s` bounds the deadline. An
        ordinary in-process read declares "no external effect occurred" — which is
        the honest content of its reversibility claim, and exactly what an export
        cannot say, which is why the export is `irreversible`.
        """
        comp = raw.get("compensation")
        if comp is None:
            if reversibility in COMPENSATION_REQUIRED:
                raise ValueError(
                    f"{source}: tools.{tool} declares reversibility: "
                    f"{reversibility} with no declared compensation — name the "
                    "action that undoes this, who performs it, and within what time "
                    "(a declaration nobody cross-checks is a comment)")
            return None
        if not isinstance(comp, dict):
            raise ValueError(f"{source}: tools.{tool}.compensation must be a mapping")
        unknown = [k for k in comp if k not in _COMPENSATION_KEYS]
        if unknown:
            raise ValueError(f"{source}: tools.{tool}.compensation: unknown key(s) "
                             f"{unknown} (known: {sorted(_COMPENSATION_KEYS)})")
        if not str(comp.get("undo") or "").strip():
            raise ValueError(f"{source}: tools.{tool}.compensation.undo must name the "
                             "action that restores the prior world")
        if not str(comp.get("performer") or "").strip():
            raise ValueError(f"{source}: tools.{tool}.compensation.performer must "
                             "name who performs the undo")
        window = comp.get("window_s")
        if window is not None and (not isinstance(window, int)
                                   or isinstance(window, bool) or window < 0):
            raise ValueError(f"{source}: tools.{tool}.compensation.window_s must be "
                             "a non-negative integer")
        if reversibility == REVERSIBILITY_IRREVERSIBLE:
            raise ValueError(
                f"{source}: tools.{tool} is irreversible and declares a "
                "compensation — nothing restores an irreversible action, and a "
                "pretend-undo in a config file is the exact claim the refusal "
                "`approval_reversal_impossible` exists to prevent")
        return {"undo": str(comp["undo"]), "performer": str(comp["performer"]),
                "window_s": window}

    # ------------------------------------------------------------------ gates
    def is_irreversible(self) -> bool:
        return self.reversibility == REVERSIBILITY_IRREVERSIBLE

    def requires_human(self) -> bool:
        """The one question the choke point asks: is a person in this grant?"""
        return self.confirm == CONFIRM_HUMAN

    def as_dict(self) -> dict[str, Any]:
        return {"tool": self.tool, "effect": self.effect, "data": self.data,
                "confirm": self.confirm, "reversibility": self.reversibility,
                "approver_role": self.approver_role,
                "compensation": (dict(self.compensation) if self.compensation
                                 else None),
                HARNESS_ONLY_KEY: self.harness_only,
                "args": {a: {k: v for k, v in r.__dict__.items() if v is not None
                             and v is not False}
                         for a, r in self.args.items()},
                "result": {"columns": self.result.columns,
                           "max_rows": self.result.max_rows,
                           "evidence": self.result.evidence,
                           "redact": list(self.result.redact),
                           "egress": self.result.egress}}


def _reversibility_of(tool: str, raw: dict[str, Any], *, confirm: str,
                      source: str) -> str:
    """The grant's reversibility class, and the one place forgetting is forgiven.

    Absent `reversibility` is a **load error** — with `effect` as the axis,
    forgetting `confirm` on a new read meant no gate *and no failure*, so forgetting
    now must be a boot error naming the tool rather than a silent disclosure.

    The single exception is the failure-direction argument applied one step
    further: a grant that already declares `confirm: human` has taken the
    **conservative** position, and the only thing missing is a label — the only
    class a human-gated action may receive is the conservative one. So an absent
    `reversibility` beside `confirm: human` is filled with `irreversible` rather
    than refused. (That is also why Day 26's own forward-looking fixture — a
    `write` + `confirm: human` grant with no reversibility — stays green for the
    right reason instead of by being exempted.) Every other omission is a refusal.
    """
    value = raw.get("reversibility")
    if value is None or not str(value).strip():
        if confirm == CONFIRM_HUMAN:
            return REVERSIBILITY_IRREVERSIBLE
        raise ValueError(
            f"{source}: tools.{tool} declares no reversibility — the field is "
            "required (reversible | compensatable | irreversible), because with "
            "`effect` as the axis forgetting it meant no gate AND no failure. Only a "
            "grant that already names `confirm: human` may omit it, and then it gets "
            "the conservative class")
    value = str(value).strip().lower()
    if value not in REVERSIBILITIES:
        raise ValueError(f"{source}: tools.{tool}.reversibility must be one of "
                         f"{sorted(REVERSIBILITIES)}, got {value!r}")
    return value


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
    # Day 27 §4 — `role → the principals that hold it`, declared in the same file
    # that says who may call. "Who may approve" is therefore a **grant**, not a
    # boolean: a gate that says only "human" is a value anyone can set in a config
    # file, and the two roles the approval workflow resolves (`approver_role` per
    # grant, and `executor`) both live here.
    approver_roles: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # WS1 — `principal → tenant` (reads scope to one tenant; absent ⇒ "default")
    # and `role → members` (route-level roles for WS2; a grant supplement, never
    # a grant replacement — deny-by-default is unchanged).
    tenants: dict[str, str] = field(default_factory=dict)
    role_bindings: dict[str, tuple[str, ...]] = field(default_factory=dict)

    # ------------------------------------------------------------------ loaders
    @classmethod
    def from_dict(cls, raw: dict[str, Any], *, source: str = "<dict>",
                  registered: Iterable[str] | None = None,
                  policy: Policy | None = None) -> "PermissionSet":
        if not isinstance(raw, dict):
            raise ValueError(f"{source}: permissions must be a mapping")
        allowed_top = {"version", "principals", "tools", "approver_roles",
                       "tenants", "role_bindings"}
        unknown_top = [k for k in raw if k not in allowed_top]
        if unknown_top:
            raise ValueError(f"{source}: unknown key(s) {unknown_top} "
                             f"(known: {sorted(allowed_top)})")
        reg = set(registered) if registered is not None else set(_default_registered_tools())

        tools_raw = raw.get("tools")
        if not isinstance(tools_raw, dict) or not tools_raw:
            raise ValueError(f"{source}: 'tools' must be a non-empty mapping")
        grants: dict[str, Grant] = {}
        harness_only: list[str] = []
        for tname, traw in tools_raw.items():
            if tname not in reg:
                if not (isinstance(traw, dict) and traw.get(HARNESS_ONLY_KEY)):
                    raise ValueError(
                        f"{source}: grant for unregistered tool {tname!r} "
                        f"(registered: {sorted(reg)}) — a tool the product does not "
                        f"register is a tool this file may not pre-authorize; declare "
                        f"`{HARNESS_ONLY_KEY}: true` if it is a fixture-only client")
                harness_only.append(str(tname))
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
            ho = [t for t in plist if t in harness_only]
            if ho:
                # Fail-closed in the direction that matters: the fixture-only client
                # is granted by the *fixtures*, never by the shipped file, so the
                # shipped system refuses it for every principal with `no_grant`.
                raise ValueError(
                    f"{source}: principals.{pname} grants harness-only tool(s) {ho} "
                    "— a harness-only declaration is exercised by a fixture's own "
                    "PermissionSet, never assigned in the shipped file")
            principals[pname] = Principal(name=pname, tools=tuple(plist))
        if DEFAULT_PRINCIPAL not in principals:
            raise ValueError(f"{source}: principals must declare a "
                             f"{DEFAULT_PRINCIPAL!r} set (the strict fallback)")

        approver_roles = cls._approver_roles(raw.get("approver_roles"), principals,
                                              source=source)
        tenants = cls._tenants(raw.get("tenants"), principals, source=source)
        role_bindings = cls._role_bindings(raw.get("role_bindings"), principals,
                                           source=source)
        ps = cls(principals=principals, tools=grants,
                 policy=policy if policy is not None else load_policy(), source=source,
                 approver_roles=approver_roles, tenants=tenants,
                 role_bindings=role_bindings)
        ps._cross_check()
        ps._check_roles()
        return ps

    @staticmethod
    def _approver_roles(raw: Any, principals: dict[str, Principal], *,
                        source: str) -> dict[str, tuple[str, ...]]:
        """`role → holders`, each holder a declared principal (fail closed)."""
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise ValueError(f"{source}: approver_roles must be a mapping of role → "
                             "principals")
        out: dict[str, tuple[str, ...]] = {}
        for role, members in raw.items():
            if not isinstance(role, str) or not role.strip():
                raise ValueError(f"{source}: approver_roles keys must be non-empty "
                                 "role names")
            if not isinstance(members, list) or not all(isinstance(m, str) for m in members):
                raise ValueError(f"{source}: approver_roles.{role} must be a list of "
                                 "principal names")
            unknown = [m for m in members if m not in principals]
            if unknown:
                raise ValueError(f"{source}: approver_roles.{role} names undeclared "
                                 f"principal(s) {unknown} — a role held by nobody the "
                                 "file knows is a role nobody can ever hold")
            out[str(role).strip()] = tuple(str(m) for m in members)
        return out

    @staticmethod
    def _tenants(raw: Any, principals: dict[str, Principal], *,
                 source: str) -> dict[str, str]:
        """`principal → tenant`. Absent ⇒ `"default"`; unknown principal ⇒ load
        error (a tenant for a caller the file does not know scopes nothing)."""
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise ValueError(f"{source}: tenants must be a mapping of principal → "
                              "tenant")
        out: dict[str, str] = {}
        for name, tenant in raw.items():
            if name not in principals:
                raise ValueError(f"{source}: tenants.{name} names an undeclared "
                                  "principal — a tenant for an unknown caller "
                                  "scopes nothing")
            if not isinstance(tenant, str) or not tenant.strip():
                raise ValueError(f"{source}: tenants.{name} must be a non-empty "
                                  "tenant name")
            out[str(name)] = str(tenant).strip()
        return out

    @staticmethod
    def _role_bindings(raw: Any, principals: dict[str, Principal], *,
                       source: str) -> dict[str, tuple[str, ...]]:
        """`role → members`, the route-level bindings WS2 enforces. Members must
        be declared principals; a binding grants no tool by itself."""
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise ValueError(f"{source}: role_bindings must be a mapping of role → "
                              "principals")
        out: dict[str, tuple[str, ...]] = {}
        for role, members in raw.items():
            if not isinstance(role, str) or not role.strip():
                raise ValueError(f"{source}: role_bindings keys must be non-empty "
                                  "role names")
            if not isinstance(members, list) or not all(isinstance(m, str)
                                                       for m in members):
                raise ValueError(f"{source}: role_bindings.{role} must be a list "
                                  "of principal names")
            unknown = [m for m in members if m not in principals]
            if unknown:
                raise ValueError(f"{source}: role_bindings.{role} names undeclared "
                                  f"principal(s) {unknown}")
            out[str(role).strip()] = tuple(str(m) for m in members)
        return out

    def _check_roles(self) -> None:
        """The two role rules a *file* can answer, and only a file can answer.

        `confirm: human` without an `approver_role` is refused here rather than on a
        bare `Grant`, because the question "whose approval?" is only answerable
        against the file's own `approver_roles` table: a gate that says only "human"
        is a boolean with no list behind it, and a boolean is a value anyone can set
        in a config file. And every `approver_role` a grant names must be a declared
        role — fail closed, because a gate whose role appears nowhere can never be
        answered: every decision would be `approval_role_not_permitted`, which is a
        workflow that exists in the config and cannot run.
        """
        for grant in self.tools.values():
            if grant.confirm == CONFIRM_HUMAN and not grant.approver_role:
                raise ValueError(
                    f"{self.source}: tools.{grant.tool} declares confirm: human "
                    "without approver_role — whose approval is required is a *role*, "
                    "resolved as a grant by this same file")
            if not grant.approver_role:
                continue
            if grant.approver_role not in self.approver_roles:
                raise ValueError(
                    f"{self.source}: tools.{grant.tool}.approver_role "
                    f"{grant.approver_role!r} is not declared in `approver_roles` — a "
                    "gate the file cannot answer is a gate, not a workflow")

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

    # ------------------------------------------------------------ Day 27 roles
    def role_holders(self, role: str) -> tuple[str, ...]:
        return tuple(self.approver_roles.get(str(role), ()))

    # ------------------------------------------------------------ WS1 tenancy
    def tenant_of(self, name: str | None) -> str:
        """The caller's tenant for read-scoping. Unknown ⇒ `"default"` — the
        same fallback direction as the grants (less privilege, never more)."""
        if name is None:
            return "default"
        return self.tenants.get(str(name), "default")

    def roles_of(self, name: str | None) -> tuple[str, ...]:
        """Route-level roles held via `role_bindings` (WS2 enforces them)."""
        if name is None:
            return ()
        return tuple(r for r, members in self.role_bindings.items()
                     if str(name) in members)

    def holds_approver_role(self, principal: str | None, role: str | None) -> bool:
        """Does this principal **hold** this role, as a grant?

        A plain membership test against the file, and deliberately the *only* place
        the question is answered: an unknown principal does not inherit a role, a
        role nobody is listed under is held by nobody, and the model's identity is
        not a special case because it never gets here at all.
        """
        if not principal or not role:
            return False
        return str(principal) in set(self.role_holders(role))

    def executor_principals(self) -> tuple[str, ...]:
        """Who may apply a staged effect — a separate, separately-granted act."""
        return self.role_holders(ROLE_EXECUTOR)

    def harness_only_tools(self) -> tuple[str, ...]:
        """The declared fixture-only clients (never assigned in the shipped file)."""
        return tuple(sorted(t for t, g in self.tools.items() if g.harness_only))

    def denied_columns(self) -> frozenset[str]:
        if self.policy is None:
            return frozenset()
        return frozenset(c.lower() for cols in self.policy.columns_deny.values()
                         for c in cols)

    def as_dict(self) -> dict[str, Any]:
        return {"source": self.source,
                "principals": {n: p.as_dict() for n, p in self.principals.items()},
                "approver_roles": {r: list(m) for r, m in self.approver_roles.items()},
                "tenants": dict(self.tenants),
                "role_bindings": {r: list(m)
                                  for r, m in self.role_bindings.items()},
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
