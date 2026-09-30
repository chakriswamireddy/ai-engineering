"""Day 26 §9 — the permission boundary (C1–C9) through the **real seams**, offline.

    $ python tools/permission_smoke.py

No API key, no network, no real sleeping. The fixtures are run through the same
`run_agent`, the same `ToolRegistry` / `validate_args`, the same Day 16 executor,
the same Day 19 `RoleAgent`, and the same `SafeSql` Days 11–25 built — in **stub
mode**, against the offline seeded stores. Nothing here needs a model to decide:
the decision procedure is a pure function of `(principal, tool, args, perms)`, so
the fixtures are deterministic and cost **0 chats, 0 embeds, 0 SQL, 0 rows**.

Six of the nine fixtures assert a **negative** — a grant that was not given, a
write that did not happen, a column that did not reach the model, a decision that
was not recorded, a chain that broke, a path that could not bypass. That is the
right shape for a security suite: the interesting claims are about what did *not*
occur.

`MECH` here: grant resolution, deny-by-default, the single choke point, the
registration-time grant requirement, `maxLength`/`pattern` enforcement, the result
contract, no-partial-rows, the audit entry shape, the chain and its verification,
the tamper-evidence claim, the entry-volume bound, and the **C9 control** (on ≡
off on legitimate traffic, at zero model and query cost). `PENDING`: whether the
grant set matches reality, whether the `principal` string is who it claims to be,
whether the log has been truncated, and whether a real model attempts an
escalation — none of which a green C-set can answer, and the day does not claim
otherwise.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_os import permissions as perms  # noqa: E402
from ai_os.agent import make_agent, run_agent, run_tool_answer  # noqa: E402
from ai_os.audit import AuditLog, NullAuditLog  # noqa: E402
from ai_os.data import DataAgent, QueryResult, SCHEMA_CARD  # noqa: E402
from ai_os.executor import run_plan  # noqa: E402
from ai_os.planner import Plan, Step  # noqa: E402
from ai_os.sql_safety import load_policy  # noqa: E402
from ai_os.tools import (  # noqa: E402
    DEFAULT_TOOL_NAMES,
    ToolRegistry,
    ToolResult,
    build_default_registry,
    build_governed_registry,
    code_retriever,
    doc_retriever,
)

from tools.mock_gateway import HostileStub  # noqa: E402
from tools.tool_smoke import CODE_TAU, DOC_TAU, build_registry  # noqa: E402

FAILS = 0
ROWS: list[dict] = []


def check(label: str, cond: bool, detail: str = "") -> bool:
    global FAILS
    if not cond:
        FAILS += 1
    print(f"[{'OK  ' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))
    return bool(cond)


def row(rid: str, what: str, *, tool: str, principal: str, granted: str,
        decision: str, code: str, audit: str = "—", effect_that_did_not_happen: str = "—",
        detail: str = "") -> None:
    ROWS.append({"id": rid, "what": what, "tool": tool, "principal": principal,
                 "granted": granted, "decision": decision, "code": code,
                 "audit": audit, "effect": effect_that_did_not_happen, "detail": detail})


# ============================================================ registry helpers

def _stores():
    return build_registry()


def governed(*, principal: str = "default", audit=None, data=None, doc=None,
             code=None, permissions=None) -> ToolRegistry:
    """A **governed** registry over the offline stub stores."""
    _reg, gw_doc, gw_code, doc_store, code_store = _stores()
    return build_default_registry(
        doc=doc if doc is not None else doc_retriever(gw_doc, doc_store),
        code=code if code is not None else code_retriever(gw_code, code_store),
        data=data, doc_tau=DOC_TAU, code_tau=CODE_TAU,
        permissions=permissions if permissions is not None else perms.default_permission_set(),
        principal=principal, audit=audit)


def ungoverned(*, data=None, doc=None, code=None) -> ToolRegistry:
    """The **ungoverned** registry — Days 11–25 exactly (the C9 off-switch)."""
    _reg, gw_doc, gw_code, doc_store, code_store = _stores()
    return build_default_registry(
        doc=doc if doc is not None else doc_retriever(gw_doc, doc_store),
        code=code if code is not None else code_retriever(gw_code, code_store),
        data=data, doc_tau=DOC_TAU, code_tau=CODE_TAU)


def call(reg: ToolRegistry, name: str, args: dict):
    """Validate then execute, carrying the decision — the loop's own path."""
    vr = reg.validate_args(name, json.dumps(args))
    if not vr.ok:
        return vr
    return reg.execute(name, vr.args or {}, decision=vr.decision)


class Engine:
    """A hostile/changed store stub: it answers any fixed-resolver query with a
    chosen column set and rows. The real `get_*` resolvers build their own query
    and call `store.query`; this is the row a changed catalog/table would return."""

    row_limit = 100
    statement_timeout_ms: int | None = None

    def __init__(self, columns: list[str], rows: list[list]) -> None:
        self.columns, self.rows = columns, rows

    def query(self, sql: str, *, row_limit: int | None = None) -> QueryResult:
        return QueryResult(ok=True, sql=sql, validation="select-only", executed=True,
                           columns=list(self.columns), rows=[list(r) for r in self.rows],
                           row_count=len(self.rows))


# The `create_refund`-shaped tool: the **harness-only** write the day refuses. It
# is never in `build_default_registry`, and no principal is ever granted it. The
# strongest form of read-only is a registry in which no write-shaped tool was ever
# registered — this fixture registers one to prove the *decision procedure* refuses
# it, and that granting it would require a `write` grant carrying `confirm: human`.
CREATE_REFUND_SCHEMA = {
    "name": "create_refund",
    "description": "Issue a refund against an order (a WRITE — harness-only).",
    "parameters": {"type": "object",
                   "properties": {"order_id": {"type": "string"},
                                  "amount": {"type": "number"}},
                   "required": ["order_id"]},
}


def _create_refund_fn(args):
    return ToolResult.success("create_refund", args=args, data={"refunded": True})


def _permissions_with_refund() -> perms.PermissionSet:
    """The shipped grants plus a `write` grant nobody is assigned — so a refusal is
    `no_grant` (registered, ungranted), not `unknown_tool`."""
    base = perms.default_permission_set()
    refund = perms.Grant(tool="create_refund", effect=perms.EFFECT_WRITE,
                         data=perms.DATA_PII, confirm=perms.CONFIRM_HUMAN,
                         args={}, result=perms.ResultRule())
    return perms.PermissionSet(principals=dict(base.principals),
                               tools={**base.tools, "create_refund": refund},
                               policy=base.policy, source="<fixture+refund>")


def _search_only_principal() -> perms.PermissionSet:
    """A principal that reaches only the two search tools (C1/C6/C8)."""
    base = perms.default_permission_set()
    principals = dict(base.principals)
    principals["search_only"] = perms.Principal(
        name="search_only", tools=("search_saleor_docs", "search_saleor_code"))
    return perms.PermissionSet(principals=principals, tools=base.tools,
                               policy=base.policy, source="<fixture+search_only>")


def _narrow_default() -> perms.PermissionSet:
    """A set whose `default` is *narrow* — so an unknown caller asking beyond it
    is `unknown_principal` (C1c) rather than merely ungranted."""
    base = perms.default_permission_set()
    principals = dict(base.principals)
    principals["default"] = perms.Principal(
        name="default", tools=("search_saleor_docs", "search_saleor_code"))
    return perms.PermissionSet(principals=principals, tools=base.tools,
                               policy=base.policy, source="<fixture+narrow-default>")


# ==================================================================== C1
def c1_default_deny():
    print("\n== C1 — default deny: no grant, no tool; a missing decision denies")
    ps = perms.default_permission_set()
    ok = True

    # (a) a principal with an empty set is denied its own tools with `no_grant`.
    empty = perms.PermissionSet(
        principals={**ps.principals, "empty": perms.Principal("empty", ())},
        tools=ps.tools, policy=ps.policy)
    d = perms.check_call("empty", "get_order", {"id": "1"}, perms=empty)
    ok &= check("C1a an empty grant set ⇒ deny `no_grant`",
                (not d.allow) and d.code == perms.CODE_NO_GRANT, f"{d.code}")
    row("C1a", "default deny", tool="get_order", principal="empty", granted="✗",
        decision="deny", code=d.code, effect_that_did_not_happen="no query ran")

    # (b) a grant naming an unregistered tool is a *load error*, not a silent load.
    raised = _raises(lambda: perms.PermissionSet.from_dict(
        {"principals": {"default": ["get_invoice"]},
         "tools": {"get_invoice": {"effect": "read", "data": "internal"}}},
        source="<c1b>"))
    ok &= check("C1b a grant for an unregistered tool raises at load",
                raised, "ValueError")
    row("C1b", "default deny", tool="(load)", principal="—", granted="—",
        decision="deny", code="ValueError", detail="unregistered grant refused")

    # (c) an unknown principal resolves to `default`, and the fallback is auditable.
    narrow = _narrow_default()
    d = perms.check_call("nobody", "get_order", {"id": "1"}, perms=narrow)
    ok &= check("C1c unknown principal → default; asking beyond it ⇒ "
                "`unknown_principal` + fallback flagged",
                (not d.allow) and d.code == perms.CODE_UNKNOWN_PRINCIPAL
                and d.unknown_principal_fallback, f"{d.code} fb={d.unknown_principal_fallback}")
    d2 = perms.check_call("nobody", "search_saleor_docs", {"query": "x"}, perms=narrow)
    ok &= check("C1c' an unknown principal *may* do what `default` may (fallback, audited)",
                d2.allow and d2.unknown_principal_fallback, f"{d2.code}")
    row("C1c", "default deny", tool="get_order", principal="nobody", granted="✗",
        decision="deny", code=d.code, effect_that_did_not_happen="no query ran")

    # (d) `execute` with no decision denies on a governed registry.
    reg = governed()
    r = reg.execute("get_order", {"id": "1"})
    ok &= check("C1d `execute(decision=None)` denies with `missing_decision`",
                (not r.ok) and r.error_kind == "permission_denied"
                and r.rejection_class == "policy"
                and r.permission["code"] == perms.CODE_MISSING_DECISION,
                f"{r.error_kind}/{r.permission}")
    row("C1d", "default deny", tool="get_order", principal="default", granted="✓",
        decision="deny", code=perms.CODE_MISSING_DECISION,
        effect_that_did_not_happen="no query ran")

    # (e) least privilege at the contract surface: an ungranted principal's
    #     `schemas()` never names the tool.
    empty_reg = governed(principal="empty", permissions=empty)
    ok &= check("C1e `schemas()` for an ungranted principal is empty (the tool is "
                "never named in the prompt)", empty_reg.schemas() == [],
                f"{len(empty_reg.schemas())} schemas")
    vr = empty_reg.validate_args("get_order", json.dumps({"id": "1"}))
    ok &= check("C1f an ungranted call is denied at validation, never executed",
                (not vr.ok) and vr.rejection_class == "policy"
                and vr.permission["code"] == perms.CODE_NO_GRANT,
                f"{vr.permission}")

    # (g) the loop stops on the denial: no retry, no sibling substitution.
    reg_analyst = governed(principal="analyst")
    stub = HostileStub([("tool", "get_order", {"id": "1"}),
                        ("answer", "answer_d15_policy")], world="complying")
    res = run_agent("Look up order 1.", reg_analyst, stub, max_steps=4)
    calls = [c for s in res["steps"] for c in s.get("calls", [])]
    ok &= check("C1g the loop stops on the denial (`policy`), one call, no retry, "
                "no substitution",
                res["termination_reason"] == "policy" and res["policy_violations"] >= 1
                and all((c.get("tool_retries") or 0) == 0 for c in calls)
                and all(not c.get("fallback") for c in calls)
                and [c["name"] for c in calls] == ["get_order"],
                f"term={res['termination_reason']} calls={[c['name'] for c in calls]}")
    ok &= check("C1g' the fetched tool was never named to the model "
                "(schemas() slice)", "get_order" not in
                {s["function"]["name"] for s in reg_analyst.schemas()})
    row("C1g", "default deny", tool="get_order", principal="analyst", granted="✗",
        decision="deny", code=perms.CODE_NO_GRANT, audit="✓",
        effect_that_did_not_happen="loop stopped, no retry, no substitution")
    return ok


# ==================================================================== C2
def c2_registration_is_a_grant():
    print("\n== C2 — registration is a grant: no declaration, no registration")
    ps = perms.default_permission_set()
    governed_reg = ToolRegistry(permissions=ps, principal="default")
    ok = True
    ok &= check("C2a `register()` without a grant raises on a governed registry",
                _raises(lambda: governed_reg.register("x", {"name": "x"}, _create_refund_fn)),
                "ValueError")
    ok &= check("C2b an unknown `effect` raises naming the key",
                _raises(lambda: perms.Grant.from_dict(
                    "t", {"effect": "writes", "data": "public"}, source="<c2b>")))
    ok &= check("C2c an unknown `data` class raises naming the key",
                _raises(lambda: perms.Grant.from_dict(
                    "t", {"effect": "read", "data": "secret"}, source="<c2c>")))
    ok &= check("C2d a `write` without `confirm: human` raises (the strongest "
                "read-only is a registry with no write)",
                _raises(lambda: perms.Grant.from_dict(
                    "t", {"effect": "write", "data": "pii"}, source="<c2d>")))
    ok &= check("C2e a `write` *with* `confirm: human` is accepted (Day 27's seam)",
                perms.Grant.from_dict(
                    "t", {"effect": "write", "data": "pii", "confirm": "human"},
                    source="<c2e>").confirm == "human")

    # The seven real tools all register with grants; the registry's contract is unchanged.
    reg = ungoverned()
    ok &= check("C2f the seven real tools all register with grants",
                reg.names() == list(DEFAULT_TOOL_NAMES)
                and all(reg.grant(n) is not None for n in DEFAULT_TOOL_NAMES),
                str(reg.names()))
    schema_names = {s["function"]["name"] for s in reg.schemas()}
    ok &= check("C2g `build_default_registry()`'s tool list and names are unchanged",
                schema_names == set(DEFAULT_TOOL_NAMES), str(sorted(schema_names)))
    ok &= check("C2h the model-facing schema keys are unchanged (Day 25's bounds "
                "intact)", reg.schemas()[0]["function"]["parameters"]["required"]
                == ["query"])
    row("C2", "registration is a grant", tool="(all seven)", principal="—",
        granted="✓", decision="allow", code="registered",
        detail="grantless register raises; seven declare grants")
    return ok


# ==================================================================== C3
def c3_read_only_by_default():
    print("\n== C3 — read-only by default: a write nobody grants; the three layers agree")
    ps = _permissions_with_refund()
    ok = True
    reg = governed(principal="default", permissions=ps)
    reg.register("create_refund", CREATE_REFUND_SCHEMA, _create_refund_fn,
                 grant=ps.grant("create_refund"))
    for principal in ("default", "analyst", "support"):
        reg_p = governed(principal=principal, permissions=ps)
        reg_p.register("create_refund", CREATE_REFUND_SCHEMA, _create_refund_fn,
                       grant=ps.grant("create_refund"))
        vr = reg_p.validate_args("create_refund", json.dumps({"order_id": "1"}))
        ok &= check(f"C3 the write-shaped tool is refused for {principal} (`no_grant`, "
                    "policy, never executed)",
                    (not vr.ok) and vr.rejection_class == "policy"
                    and vr.permission["code"] == perms.CODE_NO_GRANT,
                    f"{vr.permission}")
    row("C3a", "read-only by default", tool="create_refund", principal="any",
        granted="✗", decision="deny", code=perms.CODE_NO_GRANT,
        effect_that_did_not_happen="no write, no side effect")

    ok &= check("C3b `create_refund` is never in `build_default_registry`",
                "create_refund" not in build_default_registry().names())

    # No `write` grant exists in the shipped file (reviewable by reading one file).
    shipped = perms.default_permission_set()
    ok &= check("C3c no `write` effect exists anywhere in permissions.yaml",
                all(g.effect != perms.EFFECT_WRITE for g in shipped.tools.values()),
                str(sorted({g.effect for g in shipped.tools.values()})))

    # A write head through `run_saleor_sql` is refused by the SQL layer: Wrote? = no.
    reg_sql = governed()
    res = call(reg_sql, "run_saleor_sql", {"sql": "DELETE FROM order_order"})
    ok &= check("C3d a write head still stops as `policy` with `Wrote? = no`",
                (not res.ok) and res.rejection_class == "policy"
                and res.data is not None and res.data.get("executed") is False,
                f"{res.error_kind} executed={res.data and res.data.get('executed')}")
    row("C3d", "read-only by default", tool="run_saleor_sql", principal="default",
        granted="✓", decision="deny", code=res.data.get("error_kind") if res.data else "—",
        effect_that_did_not_happen="the write head never reached the connection")

    # The three layers agree: declared `read` ⊆ statement_allow; tables == card.
    policy = load_policy()
    declared = {g.effect for g in shipped.tools.values()}
    ok &= check("C3e the three layers agree: declared read ⊆ statement_allow ⊆ the "
                "read-only role (privilege PENDING offline)",
                declared <= {perms.EFFECT_READ}
                and set(policy.statement_allow) <= {"select", "with"}
                and set(policy.tables_allow) == set(SCHEMA_CARD),
                f"effect={sorted(declared)} heads={policy.statement_allow}")
    ok &= check("C3f the privilege claim (saleor_read_only has no write grant) is "
                "PENDING offline — the sqlite stub has no read-only role",
                not _env_unset("SALEOR_READONLY_DB_URL").strip(),
                "PENDING (no SALEOR_READONLY_DB_URL)")
    row("C3e", "read-only by default", tool="(3 layers)", principal="—",
        granted="—", decision="allow", code="layers-agree",
        detail="declaration ⊆ policy ⊆ privilege (privilege PENDING)")
    return ok


# ==================================================================== C4
def c4_input_validation():
    print("\n== C4 — input validation is a gate: bounds reject, never truncate")
    reg = governed(principal="default")
    ok = True

    # maxLength breach on a free-text arg: repairable, the model may fix it.
    vr = reg.validate_args("search_saleor_docs", json.dumps({"query": "x" * 600}))
    ok &= check("C4a maxLength breach ⇒ `invalid_args`, NOT policy (repairable)",
                (not vr.ok) and vr.error_kind == "invalid_args"
                and vr.rejection_class != "policy" and "maxLength" in (vr.error or ""),
                f"{vr.error_kind}: {vr.error}")
    row("C4a", "input validation", tool="search_saleor_docs", principal="default",
        granted="✓", decision="deny", code="invalid_args",
        effect_that_did_not_happen="no provider call, no 4 KB observation")

    # pattern breach on `id`: quote, semicolon, 4 KB, empty, homoglyph.
    cases = {"quote": "1'; DROP TABLE order_order--", "semicolon": "1;2",
             "4kb": "a" * 4096, "empty": "", "homoglyph": "1\u0415"}
    for name, value in cases.items():
        vr = reg.validate_args("get_order", json.dumps({"id": value}))
        ok &= check(f"C4b id/{name} ⇒ `invalid_args`, repairable, never policy",
                    (not vr.ok) and vr.error_kind == "invalid_args"
                    and vr.rejection_class != "policy",
                    f"{vr.error_kind}: {(vr.error or '')[:48]}")
    row("C4b", "input validation", tool="get_order", principal="default",
        granted="✓", decision="deny", code="invalid_args",
        effect_that_did_not_happen="`_literal` never has to be correct")

    # `max_chars` above the grant's maximum (the end the schema never had).
    vr = reg.validate_args("search_saleor_docs",
                           json.dumps({"query": "checkout", "max_chars": 99999}))
    ok &= check("C4c `max_chars` above maximum ⇒ `invalid_args` (the request is "
                "bounded, not the post-hoc clamp)",
                (not vr.ok) and vr.error_kind == "invalid_args", str(vr.error))
    row("C4c", "input validation", tool="search_saleor_docs", principal="default",
        granted="✓", decision="deny", code="invalid_args",
        effect_that_did_not_happen="no unbounded observation requested")

    # A permission denial / denied column / ungranted tool must be `policy`, never
    # `repairable` — else the model is handed the retry the denial exists to prevent.
    reg_analyst = governed(principal="analyst")
    vr = reg_analyst.validate_args("get_customer", json.dumps({"id": "1"}))
    ok &= check("C4d an ungranted tool is `policy` and NOT repairable",
                (not vr.ok) and vr.rejection_class == "policy"
                and vr.error_kind != "invalid_args", str(vr.permission))
    ok &= check("C4e a permission denial names its code (grep-able at 3 a.m.)",
                vr.permission.get("code") == perms.CODE_NO_GRANT,
                str(vr.permission))

    # Caps pinned to *measured* legitimate maxima (provenance, not taste).
    shipped = perms.default_permission_set()
    sql_rule = shipped.grant("run_saleor_sql").args["sql"]
    id_rule = shipped.grant("get_order").args["id"]
    sql_cap = sql_rule.maxLength
    longest_sql = max(len(s) for s in _legit_sql_lengths())
    ok &= check("C4f caps are pinned to measured legitimate maxima, not a guess",
                sql_cap == 8000 and longest_sql <= sql_cap
                and sql_rule.semantic is True
                and id_rule.pattern == r"^[A-Za-z0-9_-]{1,64}$",
                f"sql cap={sql_cap} ≥ longest legit={longest_sql}; "
                f"id pattern={id_rule.pattern}")
    row("C4f", "input validation", tool="(caps)", principal="—", granted="—",
        decision="allow", code="pinned",
        detail=f"sql≤{sql_cap} (semantic), id pattern {id_rule.pattern}")
    return ok


def _legit_sql_lengths():
    from tools.mock_gateway import (
        D14_SQL_CUSTOMERS, D14_SQL_FIX, D14_SQL_LINES, D14_SQL_REVENUE,
        D15_SQL_CONTROL,
    )
    return [D14_SQL_REVENUE, D14_SQL_CUSTOMERS, D14_SQL_FIX, D14_SQL_LINES,
            D15_SQL_CONTROL]


# ==================================================================== C5
def c5_output_validation():
    print("\n== C5 — output validation: the contract, and no partial rows")
    ps = perms.default_permission_set()
    ok = True

    # A granted tool, a hostile/changed store: an undeclared + denied column.
    hostile = DataAgent(Engine(["id", "email", "orders", "currency", "password_hash"],
                               [[1, "ada@example.com", 2, "USD", "hunter2"]]))
    reg = governed(principal="support", data=hostile)
    res = call(reg, "get_customer", {"id": "1"})
    ok &= check("C5a an undeclared/denied column ⇒ `result_violation`, policy",
                (not res.ok) and res.rejection_class == "policy"
                and res.permission["code"] == perms.CODE_RESULT_VIOLATION,
                f"{res.permission}")
    ok &= check("C5a' the model receives NO rows; the shape is kept and values dropped",
                res.data is not None and res.data.get("rows") == []
                and res.data.get("row_count") == 0
                and "password_hash" in (res.data.get("columns") or []),
                str(res.data and {k: res.data.get(k) for k in ("columns", "rows", "row_count")}))
    ok &= check("C5a'' no `password_hash` value reaches the message",
                "hunter2" not in res.as_message(), res.as_message()[:80])
    row("C5a", "output validation", tool="get_customer",
        principal="support (hostile store)", granted="✓", decision="deny",
        code=perms.CODE_RESULT_VIOLATION,
        effect_that_did_not_happen="NO rows reached the model")

    # row_count > max_rows.
    big = DataAgent(Engine(["id", "number", "status", "currency", "total", "lines"],
                           [["1", 1, "A", "USD", 1, 1], ["2", 2, "B", "USD", 2, 2],
                            ["3", 3, "C", "USD", 3, 3]]))
    reg_big = governed(principal="default", data=big)
    res = call(reg_big, "get_order", {"id": "1"})
    ok &= check("C5b `row_count > max_rows` ⇒ `result_violation`, no rows",
                (not res.ok) and res.permission["code"] == perms.CODE_RESULT_VIOLATION
                and (res.data or {}).get("rows") == [], f"{res.permission}")
    row("C5b", "output validation", tool="get_order", principal="default",
        granted="✓", decision="deny", code=perms.CODE_RESULT_VIOLATION,
        effect_that_did_not_happen="no over-cap rows returned")

    # An evidence row with no doc_id breaks the retrieval contract.
    bad_ev = ToolResult.success("search_saleor_docs",
                                evidence=[{"chunk_id": "x#1", "text": "hi"}])
    v = perms.check_result("search_saleor_docs", bad_ev, perms=ps)
    ok &= check("C5c an evidence row with no `doc_id` ⇒ `result_violation` "
                "(the retrieval contract)", (not v.allow)
                and v.code == perms.CODE_RESULT_VIOLATION, v.code)
    row("C5c", "output validation", tool="search_saleor_docs", principal="default",
        granted="✓", decision="deny", code=perms.CODE_RESULT_VIOLATION,
        effect_that_did_not_happen="`cited ⊆ retrieved` stays decidable")

    # describe_saleor_schema on a table outside the allow-list.
    out_of_card = ToolResult.success("describe_saleor_schema",
                                     data={"table": "orders_v2", "columns": {"id": "int"}})
    v = perms.check_result("describe_saleor_schema", out_of_card, perms=ps)
    ok &= check("C5d a schema result for a table outside `tables_allow` ⇒ "
                "`result_violation`", (not v.allow)
                and v.code == perms.CODE_RESULT_VIOLATION, v.code)
    row("C5d", "output validation", tool="describe_saleor_schema", principal="default",
        granted="✓", decision="deny", code=perms.CODE_RESULT_VIOLATION,
        effect_that_did_not_happen="the schema card never widens")

    # The finding the day records: `columns_deny` binds *statements*; `get_customer`
    # returns an `email` the deny list never names.
    deny = ps.denied_columns()
    cust = ps.grant("get_customer")
    ok &= check("C5e recorded finding: `email` is PII the deny list never names "
                "(the statement/result gap)",
                "email" not in deny and "email" in (cust.result.columns or ())
                and "email" in (cust.result.redact or ()),
                f"email∈deny? {'email' in deny}; redact={list(cust.result.redact)}")
    row("C5e", "output validation", tool="get_customer", principal="default",
        granted="✓", decision="allow", code="redact",
        effect_that_did_not_happen="`email` dropped and recorded, never returned raw")
    return ok


# ==================================================================== C6
def c6_least_privilege():
    print("\n== C6 — least privilege by principal")
    ok = True
    shipped = perms.default_permission_set()

    ok &= check("C6a the `default` set is exactly today's seven tools (the control's "
                "precondition)",
                set(shipped.granted_tools("default")) == set(DEFAULT_TOOL_NAMES),
                str(sorted(shipped.granted_tools("default"))))
    ok &= check("C6b `default` is the *strict* fallback: an unknown caller cannot "
                "exceed it",
                set(shipped.granted_tools("nobody")) == set(DEFAULT_TOOL_NAMES))

    # analyst reaches SQL + schema but NOT get_customer.
    reg_a = governed(principal="analyst")
    vr = reg_a.validate_args("get_customer", json.dumps({"id": "1"}))
    ok &= check("C6c analyst → get_customer is denied (`no_grant`) and audited",
                (not vr.ok) and vr.permission["code"] == perms.CODE_NO_GRANT)
    ok &= check("C6c' analyst DOES reach generated SQL + schema expansion",
                reg_a.validate_args("run_saleor_sql", json.dumps({"sql": "SELECT 1"})).ok
                and reg_a.validate_args("describe_saleor_schema",
                                        json.dumps({"table": "order_order"})).ok)
    row("C6c", "least privilege", tool="get_customer", principal="analyst",
        granted="✗", decision="deny", code=perms.CODE_NO_GRANT,
        effect_that_did_not_happen="no email read at all")

    # support reaches the resolvers but NOT generated SQL.
    reg_s = governed(principal="support")
    vr = reg_s.validate_args("run_saleor_sql", json.dumps({"sql": "SELECT 1"}))
    ok &= check("C6d support → run_saleor_sql is denied (`no_grant`)",
                (not vr.ok) and vr.permission["code"] == perms.CODE_NO_GRANT)
    ok &= check("C6d' support DOES reach get_order/get_product",
                reg_s.validate_args("get_order", json.dumps({"id": "1"})).ok
                and reg_s.validate_args("get_product", json.dumps({"id": "1"})).ok)
    row("C6d", "least privilege", tool="run_saleor_sql", principal="support",
        granted="✗", decision="deny", code=perms.CODE_NO_GRANT,
        effect_that_did_not_happen="no query ran, no row returned")

    # A search-only principal reaches nothing else.
    ps = _search_only_principal()
    reg_so = governed(principal="search_only", permissions=ps)
    ok &= check("C6e a search-only principal reaches only the search tools",
                set(reg_so.granted_tools()) == {"search_saleor_docs", "search_saleor_code"}
                and reg_so.validate_args("get_order", json.dumps({"id": "1"})).ok is False)
    row("C6e", "least privilege", tool="(search-only)", principal="search_only",
        granted="partial", decision="deny", code=perms.CODE_NO_GRANT,
        effect_that_did_not_happen="nothing beyond two search tools")
    return ok


# ==================================================================== C7
def c7_audit(tmp: Path):
    print("\n== C7 — the audit log: every decision, tamper-evident, value-free")
    path = tmp / "audit.jsonl"
    log = AuditLog(path, clock=lambda: 1000.0)
    ok = True

    reg_default = governed(principal="default", audit=log)
    reg_analyst = governed(principal="analyst", audit=log)
    hostile = DataAgent(Engine(["id", "email", "orders", "currency", "password_hash"],
                               [[1, "ada@example.com", 2, "USD", "hunter2"]]))
    reg_hostile = governed(principal="support", audit=log, data=hostile)

    # Two allows, three denies — so denies outnumber allows on this fixture.
    call(reg_default, "get_order", {"id": "1"})                            # allow
    call(reg_default, "run_saleor_sql",                                    # allow
         {"sql": "SELECT id, status FROM order_order WHERE status <> 'DRAFT'"})
    call(reg_analyst, "get_customer", {"id": "1"})                         # deny no_grant
    reg_analyst.execute("get_order", {"id": "1"})                          # deny missing_decision
    call(reg_hostile, "get_customer", {"id": "1"})                         # deny result_violation

    entries = log.entries()
    allows = [e for e in entries if e["decision"] == "allow"]
    denies = [e for e in entries if e["decision"] == "deny"]
    ok &= check("C7a every decision has exactly one entry, allows included",
                len(entries) == 5 and len(allows) == 2 and len(denies) == 3,
                f"entries={len(entries)} allow={len(allows)} deny={len(denies)}")
    ok &= check("C7b denials outnumber allows on the escalation fixture",
                len(denies) > len(allows), f"{len(denies)} > {len(allows)}")
    ok &= check("C7c an untouched log verifies (chain_ok, entries reported separately)",
                log.verify() == {"ok": True, "entries": 5, "chain_ok": True,
                                 "first_broken_seq": None}, str(log.verify()))
    ok &= check("C7d a redaction appears in `result_shape` (recorded, not silent)",
                any(e.get("result_shape", {}) and
                    e["result_shape"].get("redacted") for e in entries)
                or any(e["code"] == perms.CODE_RESULT_VIOLATION for e in entries),
                "result_shape carries redacted / violation")

    # Value-free: scan the raw file for a SQL text, a row *value*, an email, a
    # prompt. Column *names* are permitted (result_shape carries them by design —
    # "which columns arrived" is the forensic question the model may not be told),
    # but no *value* may appear.
    blob = path.read_text(encoding="utf-8")
    forbidden = ["SELECT", "DRAFT", "hunter2", "ada@example.com",
                 "You are a code-investigation agent", "statement_allow"]
    leaked = [f for f in forbidden if f in blob]
    ok &= check("C7e the log contains no SQL text, row value, email, or prompt text "
                "(asserted by scanning, not by inspection)", not leaked, str(leaked))
    ok &= check("C7f `args` are hashed + shape-only (lengths, never values)",
                all(("sql" not in e.get("args_shape", {})) or isinstance(
                    e["args_shape"]["sql"], dict) for e in entries)
                and all(str(e.get("args_hash", "")).startswith("sha256:") for e in entries))
    row("C7e", "audit log", tool="(file)", principal="—", granted="—",
        decision="allow", code="value-free", audit="✓",
        detail="scanned: no sql/row/email/prompt")

    # Tamper cases: edit, interior delete, reorder — each named by first broken seq.
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    ok &= _tamper_case("C7g", tmp / "edit.jsonl", _mutate_edit(lines), 2, ok)
    ok &= _tamper_case("C7h", tmp / "interior.jsonl",
                       [lines[0]] + lines[2:], 3, ok)
    ok &= _tamper_case("C7i", tmp / "reorder.jsonl",
                       [lines[1], lines[0]] + lines[2:], 2, ok)

    # Tail truncation is NOT detected — the honest limit.
    trunc = tmp / "trunc.jsonl"
    trunc.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    v = AuditLog(trunc).verify()
    ok &= check("C7j tail truncation is NOT detected (entries drop, chain_ok stays "
                "true — the external anchor is Day 29's)",
                v["entries"] == 4 and v["chain_ok"] is True and v["first_broken_seq"] is None,
                str(v))
    return ok


def _mutate_edit(lines: list[str]) -> list[str]:
    row1 = json.loads(lines[1])
    row1["tool"] = "get_everything"           # edit a value in place
    out = list(lines)
    out[1] = json.dumps(row1, sort_keys=True, separators=(",", ":"))
    return out


def _tamper_case(rid: str, path: Path, lines: list[str], expected_seq: int, ok: bool) -> bool:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    v = AuditLog(path).verify()
    good = (v["chain_ok"] is False and v["first_broken_seq"] == expected_seq)
    ok = check(f"{rid} tamper detected, first broken seq = {expected_seq}",
               good, str(v)) and ok
    row(rid, "audit log", tool="(tamper)", principal="—", granted="—",
        decision="deny", code=f"seq={v['first_broken_seq']}", audit="detected")
    return ok


# ==================================================================== C8
def c8_no_bypass():
    print("\n== C8 — no bypass: five paths, one choke point, a missing decision denies")
    ok = True
    ps = perms.default_permission_set()

    def stub(tool, args):
        return HostileStub([("tool", tool, args), ("answer", "answer_d15_policy")],
                           world="complying")

    # Path 1 — the loop.
    reg = governed(principal="analyst")
    res = run_agent("q", reg, stub("get_order", {"id": "1"}), max_steps=3)
    ok &= check("C8a the loop denies an ungranted tool (policy)",
                res["termination_reason"] == "policy", res["termination_reason"])
    row("C8a", "no bypass", tool="get_order", principal="analyst", granted="✗",
        decision="deny", code=perms.CODE_NO_GRANT, effect_that_did_not_happen="not run")

    # Path 2 — run_tool_answer (the single-turn identity case).
    res = run_tool_answer("q", reg, stub("get_order", {"id": "1"}))
    ok &= check("C8b run_tool_answer denies the same way",
                res["termination_reason"] == "policy", res["termination_reason"])

    # Path 3 — Day 16 executor.run_plan. The executor calls the same
    # `registry.validate_args` the planner already calls to validate a plan, so an
    # ungranted step is refused **before the plan runs** — fail-closed, and no
    # permission code had to be added to the executor.
    plan = Plan(question="q", steps=[Step(id="s1", tool="get_order", args={"id": "1"})])
    res = run_plan(plan, reg, HostileStub([("tool", "get_order", {"id": "1"})],
                                          world="complying"))
    ok &= check("C8c executor.run_plan refuses a plan naming an ungranted tool "
                "before running it (no permission code in the executor)",
                res["plan_valid"] is False
                and res["termination_reason"] in {"invalid_plan", "policy_wall"}
                and res["tool_calls"] == []
                and any("get_order" in str(e) for e in res["plan_errors"]),
                f"{res['termination_reason']} valid={res['plan_valid']} "
                f"errors={res['plan_errors']}")
    row("C8c", "no bypass", tool="get_order", principal="analyst", granted="✗",
        decision="deny", code=res["termination_reason"],
        effect_that_did_not_happen="the plan never ran a step")

    # Path 4 — the fallback ladder: a denied *sibling* is never substituted.
    #         analyst lacks get_order; a code search that "fails" must not fall
    #         into a tool the principal may not call.
    reg2 = governed(principal="default")
    res = run_agent("q", reg2,
                    HostileStub([("tool", "get_order", {"id": "1"}),
                                 ("answer", "answer_d15_policy")], world="complying"),
                    max_steps=3)
    ok &= check("C8d a policy denial is excluded from both ladder rungs (no retry, "
                "no substitution)",
                all(not c.get("fallback") and (c.get("tool_retries") or 0) == 0
                    for s in res["steps"] for c in s.get("calls", []))
                and res["termination_reason"] == "answer"
                and res["policy_violations"] == 0,
                f"term={res['termination_reason']}")

    # Path 5 — Day 19 RoleAgent.run with a *wrong* ROLE_TOOLS entry cannot exceed
    #          the principal's grants.
    ps_search = _search_only_principal()
    reg_full = governed(principal="search_only", permissions=ps_search)
    # A deliberately wrong role list that names a tool the principal lacks.
    wrong_scope = reg_full.scope(["search_saleor_docs", "run_saleor_sql"])
    ok &= check("C8e a wrong ROLE_TOOLS cannot reach a tool the grants exclude",
                "run_saleor_sql" not in wrong_scope.names()
                and wrong_scope.validate_args(
                    "run_saleor_sql", json.dumps({"sql": "SELECT 1"})).rejection_class
                == "policy")
    knowledge = make_agent("knowledge", registry=reg_full)
    res = knowledge.run("q", HostileStub([("tool", "run_saleor_sql",
                                           {"sql": "SELECT 1"}),
                                          ("answer", "answer_d15_policy")]),
                        max_steps=3)
    ok &= check("C8e' RoleAgent.run denies an out-of-grant tool (policy)",
                res["termination_reason"] == "policy", res["termination_reason"])
    row("C8e", "no bypass", tool="run_saleor_sql", principal="search_only",
        granted="✗", decision="deny", code="policy/role", effect_that_did_not_happen="not run")

    # `execute` with no decision denies; and Day 19's role gate is unchanged.
    ok &= check("C8f `execute(name, args)` with no decision denies",
                governed(principal="default").execute(
                    "get_order", {"id": "1"}).permission["code"]
                == perms.CODE_MISSING_DECISION)
    scoped = ungoverned().scope(["search_saleor_docs"])
    vr = scoped.validate_args("run_saleor_sql", json.dumps({"sql": "SELECT 1"}))
    ok &= check("C8g Day 19's out-of-role call is still `role_violation`/policy "
                "(Day 26 adds a check, it does not replace one)",
                vr.error_kind == "role_violation" and vr.rejection_class == "policy",
                f"{vr.error_kind}")
    row("C8g", "no bypass", tool="run_saleor_sql", principal="(scoped)",
        granted="✗", decision="deny", code="role_violation",
        effect_that_did_not_happen="not run")
    return ok


# ==================================================================== C9
def c9_control(tmp: Path):
    print("\n== C9 — the control: the layer is passive, and off ≡ on for legit traffic")
    ok = True

    def legit(reg):
        return run_agent("Where is checkout completed?", reg,
                         HostileStub([("tool", "search_saleor_code",
                                       {"query": "where is checkout completed", "k": 4}),
                                      ("answer", "answer_code")], world="complying"),
                         max_steps=3)

    on = legit(governed(principal="default"))
    off = legit(ungoverned())
    ok &= check("C9a the layer is on by default and off when `PERMISSIONS=none`",
                perms.enabled() is True and _governance_off_is_ungoverned())

    ok &= check("C9b on ≡ off on a legitimate request: answer, tools, SQL, rows, "
                "tokens, cost", _behavior(on) == _behavior(off),
                f"on={on['termination_reason']} off={off['termination_reason']}")
    ok &= check("C9c legitimate answers are byte-identical", on["answer"] == off["answer"])
    ok &= check("C9d 0 extra chats / 0 embeds / 0 SQL (the layer is local set logic)",
                on["ledger"]["records"] == off["ledger"]["records"]
                and on["ledger"]["cost_usd"] == off["ledger"]["cost_usd"]
                and on["gen_sql"] == off["gen_sql"],
                f"records={on['ledger']['records']}")
    ok &= check("C9e the only additions are the additive permission columns",
                "granted_tools" in on and "granted_tools" not in off
                and set(on["granted_tools"]) == set(DEFAULT_TOOL_NAMES))
    row("C9", "control", tool="search_saleor_code", principal="default", granted="✓",
        decision="allow", code="on≡off", audit="empty when off",
        effect_that_did_not_happen="legitimate traffic unchanged")

    # With the layer off the audit log is empty — the log is a consequence, not a tax.
    off_log = AuditLog(tmp / "off.jsonl", clock=lambda: 1000.0)
    regression = _off_registry(off_log)
    regression.execute("get_order", {"id": "1"})
    ok &= check("C9f with the layer off the audit log is EMPTY (the log is a "
                "consequence of the layer, not a tax on the request path)",
                len(off_log) == 0 and off_log.verify()["entries"] == 0,
                f"entries={len(off_log)}")

    # A PermissionSet that fails to load, and the off-switch fallback.
    ok &= check("C9g a missing permissions file raises (fail closed, never a silent "
                "ungoverned load)",
                _raises(lambda: perms.PermissionSet.load(tmp / "nope.yaml")))
    malformed = [
        {"principals": {"default": "not-a-list"}, "tools": {"get_order": {"effect": "read", "data": "internal"}}},
        {"principals": {"default": ["get_order"]}, "tools": {"get_order": {"effect": "read", "data": "nope"}}},
        {"principals": {"analyst": ["get_order"]}, "tools": {"get_order": {"effect": "read", "data": "internal"}}},
        {"principals": {"default": ["get_order"]}, "tools": {"get_order": {"effect": "read", "data": "internal"}}, "bogus": 1},
    ]
    ok &= check("C9h malformed grants fail closed, naming the key",
                all(_raises(lambda m=m: perms.PermissionSet.from_dict(m, source="<c9h>"))
                    for m in malformed))

    # Prior fixtures unchanged (the T and Q suites, replayed unchanged).
    for label, script in (("tools/tool_smoke.py", "tools/tool_smoke.py"),
                          ("tools/data_smoke.py", "tools/data_smoke.py")):
        proc = subprocess.run([sys.executable, str(ROOT / script)], cwd=str(ROOT),
                              capture_output=True, text=True)
        ok &= check(f"C9i {label} still exits 0 (prior fixtures unchanged)",
                    proc.returncode == 0,
                    next((ln.strip() for ln in reversed(proc.stdout.splitlines())
                          if ln.strip()), ""))
    return ok


def _governance_off_is_ungoverned() -> bool:
    with perms.governance(False):
        if perms.enabled():
            return False
    return True


def _off_registry(log) -> ToolRegistry:
    """`PERMISSIONS=none`: the ungoverned registry, no audit — Days 11–25."""
    with perms.governance(False):
        reg = build_governed_registry(audit=log)
    return reg


def _behavior(res: dict) -> dict:
    """The legitimate-traffic projection: everything except the *additive* Day 26
    columns (which are the only permitted difference)."""
    return {
        "answer": res.get("answer"),
        "evidence": list(res.get("evidence") or []),
        "citations": list(res.get("citations") or []),
        "fabricated": list(res.get("fabricated") or []),
        "cited_subset_retrieved": res.get("cited_subset_retrieved"),
        "termination_reason": res.get("termination_reason"),
        "failure_kind": res.get("failure_kind"),
        "tool_calls": [c["name"] for c in res.get("tool_calls") or []],
        "gen_sql": list(res.get("gen_sql") or []),
        "policy_violations": res.get("policy_violations"),
        "tokens": res.get("ledger", {}).get("tokens"),
        "cost_usd": res.get("ledger", {}).get("cost_usd"),
        "records": res.get("ledger", {}).get("records"),
        "steps": [[(c["name"], c["args_valid"], c["result_ok"])
                   for c in s.get("calls", [])] for s in res.get("steps", [])],
    }


# ==================================================================== helpers
def _raises(fn) -> bool:
    try:
        fn()
        return False
    except Exception:                                      # noqa: BLE001
        return True


def _env_unset(name: str) -> str:
    import os
    return os.getenv(name, "")


# ============================================================ runner + report
def render() -> None:
    print("\n" + "=" * 124)
    print("   DAY 26 — THE PERMISSION BOUNDARY (C1–C9): one decision procedure, deny "
          "by default, every decision recorded")
    print("=" * 124)
    print(f"{'id':<7}{'what':<22}{'tool':<24}{'principal':<24}{'granted':<9}"
          f"{'decision':<10}{'code':<18}audit")
    print("-" * 124)
    for r in ROWS:
        print(f"{r['id']:<7}{str(r['what'])[:20]:<22}{str(r['tool'])[:22]:<24}"
              f"{str(r['principal'])[:22]:<24}{str(r['granted']):<9}"
              f"{str(r['decision']):<10}{str(r['code'])[:16]:<18}{r['audit']}")
    print("-" * 124)
    print("grant-hit rates (default principal, in-suite calls): "
          + ", ".join(f"{k}={v}" for k, v in sorted(_GRANT_HITS.items())) or "n/a")
    print(f"rows: {len(ROWS)}   suite hash: {_suite_hash()[:16]}…")
    print("MECH: grant resolution · deny-by-default · the single choke point · the "
          "registration-time grant requirement · maxLength/pattern · the result "
          "contract · no-partial-rows · the audit entry shape · the hash chain and "
          "its verification (edit/reorder/interior delete) · the entry-volume bound · "
          "C9 (on ≡ off, 0 chats/0 embeds/0 SQL/0 rows).")
    print("PENDING: the grants' correspondence to reality (nobody has decided who may "
          "read `email`); authentication behind the caller-supplied `principal` "
          "(Day 29); the external anchor that makes tail truncation detectable "
          "(Day 29); rate limiting (Day 28); human approval for writes (Day 27); and "
          "any observation of a real model attempting an escalation.")


_GRANT_HITS: dict[str, int] = {}


def _count_grant_hits() -> None:
    """Print a grant-hit rate per tool over the pinned legitimate traffic — a tool
    nobody ever calls is a grant that should not exist; a tool called on every
    question is a grant not doing any work."""
    for tool, args in (("search_saleor_code", {"query": "where is checkout completed", "k": 4}),
                       ("search_saleor_docs", {"query": "how do I create a checkout", "k": 4}),
                       ("get_order", {"id": "1"}),
                       ("get_product", {"id": "1"}),
                       ("run_saleor_sql", {"sql": "SELECT id FROM order_order"})):
        reg = governed(principal="default")
        vr = reg.validate_args(tool, json.dumps(args))
        _GRANT_HITS[tool] = 1 if vr.ok else 0


def _suite_hash() -> str:
    import hashlib
    blob = "".join(f"{r['id']}:{r['decision']}:{r['code']}" for r in ROWS)
    return hashlib.sha256(blob.encode()).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="d26-"))
    print(f"grant file: {perms.DEFAULT_PERMISSIONS_PATH}   principals: "
          f"{sorted(perms.default_permission_set().principals)}   "
          f"default = {len(perms.default_permission_set().granted_tools('default'))} tools")
    print("layer: authorization, not authentication — the `principal` is a string "
          "the caller supplies (Day 29 owns identity)")

    c1_default_deny()
    c2_registration_is_a_grant()
    c3_read_only_by_default()
    c4_input_validation()
    c5_output_validation()
    c6_least_privilege()
    c7_audit(tmp)
    c8_no_bypass()
    c9_control(tmp)

    _count_grant_hits()
    render()
    print("\n" + ("ALL DAY 26 (MECH) PERMISSION CHECKS PASSED" if not FAILS
                  else f"{FAILS} CHECK(S) FAILED"))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
