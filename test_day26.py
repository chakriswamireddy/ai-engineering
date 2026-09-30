"""Day 26 §9 — deterministic unit suite for the permission primitives.

    $ python test_day26.py

No API key, no network, no processes: pure mechanics. The *boundary* fixtures
(C1–C9, through the real loop/executor/role seams) live in
`tools/permission_smoke.py`; this is the arithmetic underneath them —

- the decision procedure: `principal grant → default → deny`, an unknown caller
  resolving to `default` with the fallback flagged, and one code per denial;
- the fail-closed loader: a missing file and every malformed grant name the key;
- `maxLength` / `pattern` / `maximum` in the hand-rolled validator (reject, never
  truncate);
- `check_result`: the declared-column contract, the deny-list intersection, the row
  cap, the retrieval contract, redaction — and no-partial-rows;
- the audit chain: canonical hashing, verification, and all three tamper cases
  (edit / interior delete / reorder) naming the first broken `seq`, with the
  truncation limit reported honestly;
- `PERMISSIONS=none` restoring the ungoverned path.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from ai_os import permissions as perms  # noqa: E402
from ai_os.audit import (  # noqa: E402
    AuditLog, NullAuditLog, args_hash, args_shape, canonical_json, entry_hash,
)
from ai_os.data import describe_table  # noqa: E402
from ai_os.tools import (  # noqa: E402
    DEFAULT_TOOL_NAMES, ToolRegistry, ToolResult, build_default_registry,
    build_governed_registry, validate_args,
)

FAILS = 0


def check(label: str, cond: bool, detail: str = "") -> bool:
    global FAILS
    if not cond:
        FAILS += 1
    print(f"[{'OK  ' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))
    return bool(cond)


def _raises(fn) -> bool:
    try:
        fn()
        return False
    except Exception:                                      # noqa: BLE001
        return True


# --------------------------------------------------------------- the loader

def test_loader_fails_closed(tmp: Path) -> None:
    print("\n== the loader fails closed and specific")
    check("a missing file raises naming the env var and the default path",
          _raises(lambda: perms.PermissionSet.load(tmp / "nope.yaml")))
    check("an unknown top-level key raises",
          _raises(lambda: perms.PermissionSet.from_dict(
              {"principals": {"default": []}, "tools": {"get_order": {"effect": "read", "data": "internal"}},
               "extra": 1}, source="<t>")))
    check("a principal entry that is not a list of strings raises",
          _raises(lambda: perms.PermissionSet.from_dict(
              {"principals": {"default": "get_order"},
               "tools": {"get_order": {"effect": "read", "data": "internal"}}}, source="<t>")))
    check("a grant for an unregistered tool raises naming the tool",
          _raises(lambda: perms.PermissionSet.from_dict(
              {"principals": {"default": ["get_invoice"]},
               "tools": {"get_invoice": {"effect": "read", "data": "internal"}}}, source="<t>")))
    check("a principal naming an ungranted tool raises",
          _raises(lambda: perms.PermissionSet.from_dict(
              {"principals": {"default": ["get_order", "get_invoice"]},
               "tools": {"get_order": {"effect": "read", "data": "internal"}}}, source="<t>")))
    check("an unknown `effect` raises naming the key",
          _raises(lambda: perms.Grant.from_dict(
              "get_order", {"effect": "readwrite", "data": "internal"}, source="<t>")))
    check("an unknown `data` class raises naming the key",
          _raises(lambda: perms.Grant.from_dict(
              "get_order", {"effect": "read", "data": "topsecret"}, source="<t>")))
    check("a `write` effect without `confirm: human` raises",
          _raises(lambda: perms.Grant.from_dict(
              "get_order", {"effect": "write", "data": "pii"}, source="<t>")))
    check("a `result.columns` that is not a list raises",
          _raises(lambda: perms.Grant.from_dict(
              "get_order", {"effect": "read", "data": "internal",
                            "result": {"columns": 3}}, source="<t>")))
    check("an unknown arg-rule key raises naming the key",
          _raises(lambda: perms.Grant.from_dict(
              "get_order", {"effect": "read", "data": "internal",
                            "args": {"id": {"bogus": 1}}}, source="<t>")))
    check("a missing `default` principal raises (the fallback must exist)",
          _raises(lambda: perms.PermissionSet.from_dict(
              {"principals": {"analyst": []},
               "tools": {"get_order": {"effect": "read", "data": "internal"}}}, source="<t>")))
    # ... and an unknown principal is NOT an error: it resolves to `default`.
    ps = perms.default_permission_set()
    check("an unknown principal resolves to `default` (not an error)",
          ps.for_principal("nobody").name == "default")


# --------------------------------------------------------------- the decision

def test_decision_procedure() -> None:
    print("\n== the decision procedure: grant → default → deny, one code each")
    ps = perms.default_permission_set()
    d = perms.check_call("default", "get_order", {"id": "1"}, perms=ps)
    check("a granted call is allowed", d.allow and d.code == perms.CODE_ALLOW)
    check("the decision carries the declared effect and data class",
          d.effect == "read" and d.data == "internal")
    check("`Decision.meta()` is the four-key view the trace promotes",
          d.meta() == {"decision": "allow", "code": "allowed",
                       "effect": "read", "data": "internal"})

    d = perms.check_call("analyst", "get_customer", {"id": "1"}, perms=ps)
    check("a known principal missing a grant ⇒ `no_grant`",
          (not d.allow) and d.code == perms.CODE_NO_GRANT
          and not d.unknown_principal_fallback)

    narrow = perms.PermissionSet(
        principals={**ps.principals,
                    "default": perms.Principal("default", ("search_saleor_docs",))},
        tools=ps.tools, policy=ps.policy)
    d = perms.check_call("nobody", "get_order", {"id": "1"}, perms=narrow)
    check("an unknown principal asking beyond `default` ⇒ `unknown_principal` + fallback",
          (not d.allow) and d.code == perms.CODE_UNKNOWN_PRINCIPAL
          and d.unknown_principal_fallback)
    d = perms.check_call("nobody", "search_saleor_docs", {"query": "x"}, perms=narrow)
    check("an unknown principal may do what `default` may (fallback, audited)",
          d.allow and d.unknown_principal_fallback)

    d = perms.check_call("default", "no_such_tool", {}, perms=ps)
    check("a tool no grant declares ⇒ `unknown_tool`",
          (not d.allow) and d.code == perms.CODE_UNKNOWN_TOOL)

    # `execute(decision=None)` denies on a governed registry, and allows on an
    # ungoverned one (the C9 off-switch).
    g = build_governed_registry()
    r = g.execute("get_order", {"id": "1"})
    check("`execute(decision=None)` denies with `missing_decision` (policy)",
          (not r.ok) and r.permission["code"] == perms.CODE_MISSING_DECISION
          and r.rejection_class == "policy")
    u = build_default_registry()
    check("an ungoverned registry allows `execute(name, args)` (Days 11–25)",
          u.execute("get_order", {"id": "1"}).ok and not u.permissions_on())

    # schemas() is the granted slice — least privilege at the contract surface.
    check("`schemas()` for the default principal is the seven-tool slice",
          [s["function"]["name"] for s in g.schemas()] == list(DEFAULT_TOOL_NAMES))
    check("the `default` grant set is exactly today's seven tools (C9's precondition)",
          set(ps.granted_tools("default")) == set(DEFAULT_TOOL_NAMES))
    check("no `write` effect exists in the shipped grant file",
          all(g_.effect != perms.EFFECT_WRITE for g_ in ps.tools.values()))

    # A result contract violation is `policy`, never `repairable`.
    hostile = ToolResult.success("get_customer", data={
        "columns": ["id", "email", "orders", "currency", "password_hash"],
        "rows": [[1, "ada@example.com", 2, "USD", "hunter2"]], "row_count": 1})
    v = perms.check_result("get_customer", hostile, perms=ps)
    check("a denied column in a result ⇒ `result_violation`",
          (not v.allow) and v.code == perms.CODE_RESULT_VIOLATION)


# --------------------------------------------------------------- validation

def test_argument_bounds() -> None:
    print("\n== maxLength / pattern / maximum: reject, never truncate")
    schema = {"parameters": {"type": "object", "properties": {
        "query": {"type": "string", "minLength": 1, "maxLength": 8},
        "id": {"type": "string", "pattern": "^[a-z]+$"},
        "n": {"type": "integer", "minimum": 1, "maximum": 5}},
        "required": ["query"]}}
    ok, msg = validate_args(schema, json.dumps({"query": "x" * 9}))
    check("over maxLength ⇒ rejected, naming the field and the bound",
          (not ok) and "maxLength" in msg and "query" in msg, msg)
    ok, msg = validate_args(schema, json.dumps({"query": "ok", "id": "AB"}))
    check("pattern mismatch ⇒ rejected, naming the field and the pattern",
          (not ok) and "pattern" in msg and "id" in msg, msg)
    ok, msg = validate_args(schema, json.dumps({"query": "ok", "n": 6}))
    check("over maximum ⇒ rejected naming the field and the bound",
          (not ok) and "'n'" in msg and "<= 5" in msg, msg)
    ok, _ = validate_args(schema, json.dumps({"query": "ok", "id": "abc", "n": 5}))
    check("a value inside every bound passes", ok)
    check("a value that fails a bound is never *truncated* into compliance",
          validate_args(schema, json.dumps({"query": "x" * 9}))[0] is False)

    # The grant can tighten via the merged spec, and the shipped caps are pinned.
    g = build_governed_registry()
    vr = g.validate_args("get_order", json.dumps({"id": "a" * 65}))
    check("the grant's `maxLength` is enforced through validate_args (one validator)",
          (not vr.ok) and vr.error_kind == "invalid_args")
    ps = perms.default_permission_set()
    check("the sql cap is semantic + measured (8000), the id cap is a 1..64 pattern",
          ps.grant("run_saleor_sql").args["sql"].maxLength == 8000
          and ps.grant("run_saleor_sql").args["sql"].semantic
          and ps.grant("get_order").args["id"].pattern == r"^[A-Za-z0-9_-]{1,64}$")
    check("describe_saleor_schema.table enum == the closed SCHEMA_CARD set",
          set(ps.grant("describe_saleor_schema").args["table"].enum)
          == set(__import__("ai_os.data", fromlist=["SCHEMA_CARD"]).SCHEMA_CARD))
    check("a table outside the card is refused at validation",
          not g.validate_args("describe_saleor_schema",
                              json.dumps({"table": "orders_v2"})).ok)


# --------------------------------------------------------------- result check

def test_result_contract() -> None:
    print("\n== check_result: declared columns, deny list, cap, retrieval, redaction")
    ps = perms.default_permission_set()

    declared = ToolResult.success("get_order", data={
        "columns": ["id", "number", "status", "currency", "total", "lines"],
        "rows": [["1", 1, "A", "USD", 1.0, 1]], "row_count": 1})
    check("a conforming result is allowed",
          perms.check_result("get_order", declared, perms=ps).allow)

    undeclared = ToolResult.success("get_order", data={
        "columns": ["id", "number", "status", "currency", "total", "lines", "secret_col"],
        "rows": [], "row_count": 0})
    check("an undeclared column ⇒ `result_violation`",
          not perms.check_result("get_order", undeclared, perms=ps).allow)

    over = ToolResult.success("get_order", data={
        "columns": ["id"], "rows": [["1"], ["2"]], "row_count": 2})
    check("`row_count > max_rows` ⇒ `result_violation`",
          not perms.check_result("get_order", over, perms=ps).allow)

    bad_ev = ToolResult.success("search_saleor_docs",
                                evidence=[{"heading": "h"}])
    check("an evidence row with no doc_id/chunk_id ⇒ `result_violation` "
          "(the retrieval contract)",
          not perms.check_result("search_saleor_docs", bad_ev, perms=ps).allow)

    # Redaction is an *allow* carrying the columns to drop.
    v = perms.check_result("get_customer", ToolResult.success(
        "get_customer", data={"columns": ["id", "email", "orders", "currency"],
                              "rows": [[1, "a@b.com", 2, "USD"]], "row_count": 1}),
        perms=ps)
    check("a declared-but-`redact`ed column is an allow carrying the drop list",
          v.allow and v.redacted == ("email",))

    # No partial rows: a violation keeps the shape and drops every value.
    reg = build_governed_registry(principal="support")
    from ai_os.data import DataAgent, QueryResult

    class _Engine:
        row_limit, statement_timeout_ms = 100, None
        def query(self, sql, *, row_limit=None):
            return QueryResult(ok=True, sql=sql, validation="select-only", executed=True,
                               columns=["id", "email", "orders", "currency", "password_hash"],
                               rows=[[1, "ada@example.com", 2, "USD", "hunter2"]],
                               row_count=1)

    reg = build_governed_registry(principal="support", data=DataAgent(_Engine()))
    vr = reg.validate_args("get_customer", json.dumps({"id": "1"}))
    res = reg.execute("get_customer", vr.args, decision=vr.decision)
    check("a violating result is a `policy` failure",
          (not res.ok) and res.rejection_class == "policy")
    check("the shape is kept and every value dropped (no partial rows)",
          res.data.get("rows") == [] and res.data.get("row_count") == 0
          and "password_hash" in res.data.get("columns", []))
    check("the value never reaches the model's message",
          "hunter2" not in res.as_message() and "ada@example.com" not in res.as_message())

    # A granted, declared-but-redacted column: the value leaves, the shape stays,
    # and the redaction is named.
    reg2 = build_governed_registry(principal="default")
    vr = reg2.validate_args("get_customer", json.dumps({"id": "1"}))
    ok_res = reg2.execute("get_customer", vr.args, decision=vr.decision)
    check("a redaction drops the value, keeps the shape, and records `redacted`",
          ok_res.ok and "email" not in ok_res.data["columns"]
          and ok_res.redacted == ["email"] and ok_res.data.get("redacted") == ["email"])


# --------------------------------------------------------------- the audit

def test_audit_chain(tmp: Path) -> None:
    print("\n== the audit chain: canonical, chained, tamper-evident, value-free")
    path = tmp / "audit.jsonl"
    log = AuditLog(path, clock=lambda: 1000.0)
    check("canonical JSON forbids floats",
          _raises(lambda: canonical_json({"x": 1.5})))
    check("canonical JSON is sorted and separator-fixed",
          canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}')

    for i in range(4):
        log.append({"principal": "default", "tool": "get_order", "effect": "read",
                    "data": "internal", "decision": "allow", "code": "allowed",
                    "args_hash": args_hash({"id": str(i)}),
                    "args_shape": args_shape({"id": str(i)}),
                    "result_shape": {"row_count": 1, "columns": ["id"]}})
    v = log.verify()
    check("an untouched log verifies (ok, entries, chain_ok, first_broken_seq)",
          v == {"ok": True, "entries": 4, "chain_ok": True, "first_broken_seq": None},
          str(v))
    first = json.loads(path.read_text().splitlines()[0])
    check("the chain is `sha256(canonical(entry − hash))` with prev_hash carried in",
          first["hash"] == entry_hash({k: val for k, val in first.items() if k != "hash"})
          and first["prev_hash"] == "")
    check("a forbidden key is refused at append (the record shape is a contract)",
          _raises(lambda: log.append({"principal": "x", "tool": "t",
                                      "decision": "allow", "secret": "leak"})))

    lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
    # edit
    row1 = json.loads(lines[1]); row1["tool"] = "get_everything"
    (tmp / "edit.jsonl").write_text("\n".join(
        [lines[0], json.dumps(row1, sort_keys=True, separators=(",", ":"))] + lines[2:]) + "\n")
    check("an edited value fails verification and names the first broken seq",
          AuditLog(tmp / "edit.jsonl").verify()["first_broken_seq"] == 2)
    # interior delete
    (tmp / "del.jsonl").write_text("\n".join([lines[0]] + lines[2:]) + "\n")
    check("an interior deletion fails verification at the orphaned seq",
          AuditLog(tmp / "del.jsonl").verify()["first_broken_seq"] == 3)
    # reorder
    (tmp / "reorder.jsonl").write_text("\n".join([lines[1], lines[0]] + lines[2:]) + "\n")
    check("a reorder fails verification",
          AuditLog(tmp / "reorder.jsonl").verify()["first_broken_seq"] == 2)
    # truncation is NOT detected — reported, not hidden
    (tmp / "trunc.jsonl").write_text("\n".join(lines[:-1]) + "\n")
    tv = AuditLog(tmp / "trunc.jsonl").verify()
    check("tail truncation is NOT detected (entries drop, chain_ok stays true)",
          tv["entries"] == 3 and tv["chain_ok"] is True
          and tv["first_broken_seq"] is None, str(tv))
    check("NullAuditLog writes nothing and verifies vacuously",
          NullAuditLog().append({"principal": "x"}) is None
          and NullAuditLog().verify()["entries"] == 0)


# --------------------------------------------------------------- on/off

def test_governance_switch() -> None:
    print("\n== PERMISSIONS=none restores the ungoverned path")
    check("the layer is on by default", perms.enabled() is True)
    with perms.governance(False):
        check("`PERMISSIONS=none` turns the layer off", perms.enabled() is False)
        reg = build_governed_registry()
        check("with the layer off the registry is ungoverned",
              reg.permissions_on() is False)
        check("with the layer off `execute(name, args)` runs (no decision needed)",
              reg.execute("get_order", {"id": "1"}).ok)
        check("with the layer off `schemas()` is the full seven-tool set",
              len(reg.schemas()) == len(DEFAULT_TOOL_NAMES))
    check("`PERMISSIONS` is restored after the scoped switch", perms.enabled() is True)

    # The same legitimate question is byte-identical on and off.
    from ai_os.agent import run_agent
    from tools.mock_gateway import HostileStub

    def legit(reg):
        return run_agent("Where is checkout completed?", reg,
                         HostileStub([("tool", "search_saleor_code",
                                       {"query": "where is checkout completed", "k": 4}),
                                      ("answer", "answer_code")], world="complying"),
                         max_steps=3)

    on = legit(build_governed_registry())
    off = legit(build_default_registry())
    check("a legitimate request is byte-identical with the layer on and off",
          on["answer"] == off["answer"]
          and [c["name"] for c in on["tool_calls"]] == [c["name"] for c in off["tool_calls"]]
          and on["ledger"] == off["ledger"])
    check("only the additive permission columns differ",
          "granted_tools" in on and "granted_tools" not in off)


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="d26-unit-"))
    test_loader_fails_closed(tmp)
    test_decision_procedure()
    test_argument_bounds()
    test_result_contract()
    test_audit_chain(tmp)
    test_governance_switch()
    print("\n" + ("ALL DAY 26 (MECH) UNIT CHECKS PASSED" if not FAILS
                  else f"{FAILS} CHECK(S) FAILED"))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
