#!/usr/bin/env python3
"""WS4 — approval execution + compensation (F1–F7), offline except via Docker.

- F1 decide is idempotent + attributed (replay 200, flip 409, role 403)
- F2 digest mismatch refused (decide pre-check 409, apply re-hash refusal)
- F3 approve-twice = 1 apply (replay returns stored outcome, rows move once)
- F4 compensation: reversible compensates by append; irreversible refuses;
  HTTP apply writes the saga record with a derived `reverse_sql`
- F5 executor: gated step pauses `awaiting_approval` (no rung, no wall),
  resumes to done after external apply without re-executing
- F6 audit chain intact across submit → decide → apply
- F7 decide/apply require auth (401 anonymous with `ALLOW_ANON=0`)

W4's live-Postgres style Docker use is inherited from write_smoke only for
reference — this fixture is stdlib-sqlite + file policy throughout.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from joserfc import jwt as _jwt
from joserfc.jwk import OctKey
from starlette.requests import Request

from ai_os import approval as appr_mod
from ai_os import audit as audit_mod
from ai_os import identity as ident
from ai_os import permissions as perms
from ai_os.contract import Completion, ToolCall, Usage
from ai_os.executor import run_plan
from ai_os.ledger import Ledger
from ai_os.planner import Plan, Step
from ai_os import server as server_mod
from ai_os.state import ExecutionState, StateStore
from ai_os.tools import ToolRegistry, ToolResult

FAILS = 0
SECRET = "ws4-test-secret"
ISSUER = "https://issuer.test"
AUDIENCE = "ai-test"
SQL = "UPDATE product_product SET name = 'WS4' WHERE id = 2"


def check(label: str, cond: bool, detail: str = "") -> bool:
    global FAILS
    if not cond:
        FAILS += 1
    print(f"[{'OK  ' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))
    return bool(cond)


def _env(**kw: str) -> dict[str, str | None]:
    prev = {k: os.environ.get(k) for k in kw}
    for k, v in kw.items():
        if v == "":
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    return prev


def _restore(prev: dict[str, str | None]) -> None:
    for k, v in prev.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def mint(sub: str, exp_in: int = 600) -> str:
    return _jwt.encode({"alg": "HS256"},
                       {"sub": sub, "tenant": "acme",
                        "exp": int(time.time()) + exp_in,
                        "iss": ISSUER, "aud": AUDIENCE},
                       OctKey.import_key(SECRET))


def req_with(token: str | None, path: str, method: str = "POST") -> Request:
    headers = []
    if token:
        headers = [(b"authorization", f"Bearer {token}".encode())]
    return Request({"type": "http", "method": method, "path": path,
                    "headers": headers})


def stamped(token: str | None, path: str, method: str = "POST") -> Request:
    """A request as the middleware leaves it: identity resolved + stamped."""
    req = req_with(token, path, method)
    principal = ident.request_principal(req)
    req.state.principal = principal
    req.state.tenant = principal.tenant
    req.state.roles = list(principal.roles)
    req.state.auth = principal.auth
    return req


def make_desk(tmp: Path, *, audit=None):
    store = StateStore(path=tmp / "approvals.json", default_ttl=86400.0,
                       autosave=False)
    return appr_mod.ApprovalStore(store, permissions=perms.default_permission_set(),
                                  audit=audit)


def submit_sql(desk, *, by: str = "analyst"):
    return desk.submit(tool="run_saleor_sql", args={"sql": SQL}, effect="read",
                       data="internal", reversibility="reversible",
                       requested_by=by, approver_role="support_lead")


def write_policy_file(tmp: Path) -> Path:
    import yaml
    raw = {"statement_allow": ["select", "with"],
           "statement_allow_write": ["insert", "update", "delete"],
           "write_tables_allow": ["product_product"],
           "require_approval": True,
           "tables_allow": ["order_order", "order_orderline", "product_product",
                            "product_productvariant",
                            "product_productvariantchannellisting",
                            "account_user", "channel_channel"],
           "columns_deny": {"account_user": ["password"]},
           "definitions": {}, "row_limit": 100}
    path = tmp / "policy-write.yaml"
    path.write_text(yaml.safe_dump(raw))
    return path


def f1_decide_idempotent() -> None:
    prev = _env(OIDC_TEST_SECRET=SECRET, OIDC_ISSUER=ISSUER,
                OIDC_AUDIENCE=AUDIENCE, ALLOW_ANON="0")
    prev_desk = server_mod._approval_desk
    with tempfile.TemporaryDirectory() as tmp:
        try:
            desk = make_desk(Path(tmp))
            server_mod._approval_desk = lambda: desk  # type: ignore[assignment]
            rec = submit_sql(desk)
            lead = stamped(mint("support_lead"), f"/approvals/{rec.request_id}/decide")
            first = server_mod.approval_decide_endpoint(
                rec.request_id,
                server_mod.DecideRequest(decision="approve"), lead)
            check("F1 approve records the decider",
                  first["state"] == "approved"
                  and first["decided_by"] == "support_lead",
                  f"{first['state']}/{first.get('decided_by')}")
            replay = server_mod.approval_decide_endpoint(
                rec.request_id,
                server_mod.DecideRequest(decision="approve"), lead)
            check("F1 replaying the decision is idempotent (200, same state)",
                  replay["state"] == "approved", replay["state"])
            flip = server_mod.approval_decide_endpoint(
                rec.request_id,
                server_mod.DecideRequest(decision="reject", reason="changed mind"),
                lead)
            check("F1 deciding the other way conflicts (409)",
                  getattr(flip, "status_code", 200) == 409)
            rec2 = submit_sql(desk)
            analyst = stamped(mint("analyst"),
                                f"/approvals/{rec2.request_id}/decide")
            norole = server_mod.approval_decide_endpoint(
                rec2.request_id,
                server_mod.DecideRequest(decision="approve"), analyst)
            check("F1 decider without the grant role is refused (403)",
                  getattr(norole, "status_code", 200) == 403)
        finally:
            server_mod._approval_desk = prev_desk  # type: ignore[assignment]
            _restore(prev)


def f2_digest_mismatch() -> None:
    prev = _env(OIDC_TEST_SECRET=SECRET, OIDC_ISSUER=ISSUER,
                OIDC_AUDIENCE=AUDIENCE, ALLOW_ANON="0")
    prev_desk = server_mod._approval_desk
    with tempfile.TemporaryDirectory() as tmp:
        try:
            desk = make_desk(Path(tmp))
            server_mod._approval_desk = lambda: desk  # type: ignore[assignment]
            rec = submit_sql(desk)
            lead = stamped(mint("support_lead"), f"/approvals/{rec.request_id}/decide")
            bad = server_mod.approval_decide_endpoint(
                rec.request_id,
                server_mod.DecideRequest(decision="approve", digest="0" * 64),
                lead)
            check("F2 decide with a foreign digest is refused (409)",
                  getattr(bad, "status_code", 200) == 409)
            desk.decide(rec.request_id, decided_by="support_lead", approve=True)
            try:
                desk.apply(rec.request_id, applied_by="executor",
                           args={"sql": "UPDATE product_product SET name = 'evil'"})
                check("F2 apply with tampered args is refused", False)
            except appr_mod.ApprovalRefusal as refusal:
                check("F2 apply re-hashes: digest mismatch refused",
                      refusal.code == "approval_digest_mismatch", refusal.code)
        finally:
            server_mod._approval_desk = prev_desk  # type: ignore[assignment]
            _restore(prev)


def f3_apply_twice_is_once() -> None:
    prev = _env(OIDC_TEST_SECRET=SECRET, OIDC_ISSUER=ISSUER,
                OIDC_AUDIENCE=AUDIENCE, ALLOW_ANON="0")
    prev_desk = server_mod._approval_desk
    with tempfile.TemporaryDirectory() as tmp:
        policy_path = write_policy_file(Path(tmp))
        prev2 = _env(SALEOR_SQL_POLICY=str(policy_path))
        try:
            desk = make_desk(Path(tmp))
            server_mod._approval_desk = lambda: desk  # type: ignore[assignment]
            rec = submit_sql(desk)
            desk.decide(rec.request_id, decided_by="support_lead", approve=True)
            doer = stamped(mint("executor"),
                            f"/approvals/{rec.request_id}/apply")
            first = server_mod.approval_apply_endpoint(rec.request_id, doer)
            check("F3 apply commits with measured rows",
                  first["applied"] is True and first["rows_changed"] == 1
                  and first["compensated"] is False,
                  f"rows={first.get('rows_changed')}")
            again = server_mod.approval_apply_endpoint(rec.request_id, doer)
            check("F3 replay returns the stored outcome without re-executing",
                  again.get("replayed") is True
                  and again["rows_changed"] == first["rows_changed"],
                  f"rows={again.get('rows_changed')}")
        finally:
            server_mod._approval_desk = prev_desk  # type: ignore[assignment]
            _restore(prev2)
            _restore(prev)


def f4_compensation() -> None:
    prev = _env(OIDC_TEST_SECRET=SECRET, OIDC_ISSUER=ISSUER,
                OIDC_AUDIENCE=AUDIENCE, ALLOW_ANON="0")
    prev_desk = server_mod._approval_desk
    with tempfile.TemporaryDirectory() as tmp:
        policy_path = write_policy_file(Path(tmp))
        prev2 = _env(SALEOR_SQL_POLICY=str(policy_path))
        try:
            desk = make_desk(Path(tmp))
            server_mod._approval_desk = lambda: desk  # type: ignore[assignment]
            rec = submit_sql(desk)
            desk.decide(rec.request_id, decided_by="support_lead", approve=True)
            doer = stamped(mint("executor"),
                            f"/approvals/{rec.request_id}/apply")
            out = server_mod.approval_apply_endpoint(rec.request_id, doer)
            assert out["applied"] is True, out
            shelf = appr_mod.state_store_for_approvals()
            comp = shelf.get("executor", "compensation",
                             f"compensation/{rec.request_id}")
            payload = comp.payload if comp is not None else {}
            check("F4 saga record carries a derived reverse_sql",
                  bool(payload) and payload.get("reverse_sql") is not None
                  and "UPDATE product_product" in payload["reverse_sql"],
                  str(payload.get("reverse_sql"))[:80])
            undone = desk.compensate(rec.request_id, compensated_by="executor",
                                     reason="customer asked to revert")
            check("F4 reversible apply compensates by append (original untouched)",
                  undone.state == "compensated"
                  and undone.compensates == rec.request_id
                  and desk.get(rec.request_id).state == "applied",
                  undone.state)
            irr = desk.submit(tool="run_saleor_sql", args={"sql": SQL},
                              effect="read", data="internal",
                              reversibility="irreversible",
                              requested_by="analyst", approver_role="support_lead")
            desk.decide(irr.request_id, decided_by="support_lead", approve=True)
            desk.apply(irr.request_id, applied_by="executor")
            try:
                desk.compensate(irr.request_id, compensated_by="executor")
                check("F4 irreversible refuses compensation", False)
            except appr_mod.ApprovalRefusal as refusal:
                check("F4 irreversible refuses compensation loudly",
                      refusal.code == "approval_reversal_impossible",
                      refusal.code)
        finally:
            server_mod._approval_desk = prev_desk  # type: ignore[assignment]
            _restore(prev2)
            _restore(prev)


class PlanGateway:
    """Scripted plan gateway: one gated call, then an answer."""

    def __init__(self, calls) -> None:
        self.calls = list(calls)
        self.ledger = Ledger()
        self.tool_turns = 0
        self.chat_turns = 0

    def chat_with_tools(self, messages, tools=None, **kw) -> Completion:
        self.tool_turns += 1
        if self.calls:
            name, args = self.calls.pop(0)
            return Completion(text="", model="stub", finish_reason="tool_calls",
                              usage=Usage(5, 5, 10),
                              tool_calls=[ToolCall(name=name, arguments=args)])
        return Completion(text="done", model="stub", finish_reason="stop",
                          usage=Usage(5, 5, 10))

    def chat(self, messages, **kw) -> Completion:
        self.chat_turns += 1
        return Completion(text="done", model="stub", finish_reason="stop",
                          usage=Usage(5, 5, 10))


def gated_registry(tmp: Path, log):
    from ai_os import permissions as _perms
    raw = {"version": 1,
           "principals": {"default": [], "analyst": ["stub_write"],
                          "support_lead": [], "executor": []},
           "tools": {"stub_write": {"effect": "read", "data": "internal",
                                    "reversibility": "irreversible",
                                    "confirm": "human",
                                    "approver_role": "support_lead"}},
           "approver_roles": {"support_lead": ["support_lead"],
                              "executor": ["executor"]}}
    ps = _perms.PermissionSet.from_dict(raw, source="<ws4>",
                                        registered=["stub_write"])
    store = StateStore(path=tmp / "approvals.json", default_ttl=86400.0,
                       autosave=False)
    desk = appr_mod.ApprovalStore(store, permissions=ps, audit=log)
    reg = ToolRegistry(permissions=ps, principal="analyst", audit=log,
                       approval_store=desk)
    reg.register("stub_write", {"name": "stub_write", "type": "object",
                                "properties": {}},
                 lambda args: ToolResult.success("stub_write", data={}),
                 grant=ps.grant("stub_write"))
    return reg, desk


def f5_executor_resume() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        log = audit_mod.AuditLog(path=Path(tmp) / "audit.jsonl")
        reg, desk = gated_registry(Path(tmp), log)
        plan = Plan(question="run the gated step", source="stored",
                    steps=[Step(id="s1", tool="stub_write", args={})])
        gw = PlanGateway([("stub_write", "{}")])
        first = run_plan(plan, reg, gw)
        req_ids = first.get("approval_request_ids") or []
        check("F5 gated step pauses awaiting_approval (no wall, request named)",
              first["termination_reason"] == "awaiting_approval"
              and len(req_ids) == 1
              and first["policy_violations"] == 0,
              f"{first['termination_reason']} req={req_ids}")
        req_id = req_ids[0]
        desk.decide(req_id, decided_by="support_lead", approve=True)
        desk.apply(req_id, applied_by="executor")
        st = ExecutionState.from_dict(first["state"])
        gw2 = PlanGateway([])
        second = run_plan(plan, reg, gw2, state=st, resume=True)
        steps = {s["id"]: s for s in second["state"]["steps"]}
        check("F5 resume completes the applied step without re-executing",
              steps["s1"]["status"] == "done"
              and gw2.tool_turns == 0
              and second["termination_reason"] != "awaiting_approval",
              f"{steps['s1']['status']}/{second['termination_reason']}")


def f6_chain_intact() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        log = audit_mod.AuditLog(path=Path(tmp) / "audit.jsonl")
        desk = make_desk(Path(tmp), audit=log)
        rec = submit_sql(desk)
        desk.decide(rec.request_id, decided_by="support_lead", approve=True)
        desk.apply(rec.request_id, applied_by="executor")
        verdict = log.verify()
        kinds = {e.get("decision") for e in log.entries()}
        check("F6 submit → decide → apply chain verifies, decisions recorded",
              verdict["chain_ok"] is True and kinds == {"pending", "allow"},
              f"chain_ok={verdict['chain_ok']} kinds={sorted(kinds)}")


def f7_auth_required() -> None:
    prev = _env(ALLOW_ANON="0", OIDC_TEST_SECRET="", OIDC_ISSUER="",
                OIDC_AUDIENCE="")
    try:
        anon = req_with(None, "/approvals/apr-1/decide")
        denied = server_mod.approval_decide_endpoint(
            "apr-1", server_mod.DecideRequest(decision="approve"), anon)
        check("F7 decide denies anonymous (401)",
              getattr(denied, "status_code", 200) == 401)
        anon_apply = req_with(None, "/approvals/apr-1/apply")
        denied2 = server_mod.approval_apply_endpoint("apr-1", anon_apply)
        check("F7 apply denies anonymous (401)",
              getattr(denied2, "status_code", 200) == 401)
    finally:
        _restore(prev)


def main() -> int:
    print("== WS4 — approval execution + compensation (F1–F7) ==")
    f1_decide_idempotent()
    f2_digest_mismatch()
    f3_apply_twice_is_once()
    f4_compensation()
    f5_executor_resume()
    f6_chain_intact()
    f7_auth_required()
    print("\n" + ("ALL APPROVAL CHECKS PASSED" if not FAILS else f"{FAILS} CHECK(S) FAILED"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
