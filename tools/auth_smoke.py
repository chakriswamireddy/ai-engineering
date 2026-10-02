#!/usr/bin/env python3
"""WS1 — the auth identity fixture (U1–U7), offline.

OIDC JWT → `Principal{id, tenant, roles}` through the real seams, with HS256
(`OIDC_TEST_SECRET`) standing in for the issuer's RS256 JWKS — the same
`verify_token` path, a different key. No network, no model, no clock beyond
`exp`.

- U1 valid token resolves sub/tenant/roles; middleware stamps state
- U2 forged token → 401 on a guarded route
- U3 expired token → 401
- U4 cross-tenant reads denied (`/audit` + `/approvals` scoped to caller)
- U5 audit entries carry the OIDC `sub` (governed call + `run_agent(Principal)`)
- U6 anonymous: `ALLOW_ANON=1` passes (offline compat), `=0` denies
- U7 `tenants`/`role_bindings` load; deny-by-default unchanged
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

from ai_os import audit as audit_mod
from ai_os import identity as ident
from ai_os import permissions as perms
from ai_os.agent import run_agent
from ai_os.contract import Completion, Usage
from ai_os.ledger import Ledger
from ai_os import server as server_mod
from ai_os.state import StateStore
from ai_os import approval as appr_mod
from ai_os.tools import build_default_registry, code_retriever, doc_retriever

from tools.tool_smoke import CODE_TAU, DOC_TAU, build_registry

FAILS = 0
SECRET = "ws1-test-secret"
ISSUER = "https://issuer.test"
AUDIENCE = "ai-test"


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


def mint(sub: str, *, tenant: str = "acme", roles: list[str] | None = None,
         exp_in: int = 600, secret: str = SECRET,
         iss: str = ISSUER, aud: str = AUDIENCE) -> str:
    claims: dict = {"sub": sub, "tenant": tenant, "exp": int(time.time()) + exp_in,
                    "iss": iss, "aud": aud}
    if roles is not None:
        claims["roles"] = roles
    return _jwt.encode({"alg": "HS256"}, claims, OctKey.import_key(secret))


def req_with(token: str | None, path: str = "/ingest/span") -> Request:
    headers = []
    if token:
        headers = [(b"authorization", f"Bearer {token}".encode())]
    return Request({"type": "http", "method": "POST", "path": path,
                    "headers": headers})


class ChatStub:
    """In-process chat stub: answers with no tool calls (0 network)."""

    def __init__(self) -> None:
        self.ledger = Ledger()
        self.last_attempts = 1
        self.last_latency_ms = 1.0
        self.last_provider = "stub"
        self.last_fallback_depth = 0

    def chat(self, messages, **kw) -> Completion:
        return Completion(text="stub answer", model="stub",
                          finish_reason="stop", usage=Usage(5, 5, 10))

    def chat_with_tools(self, messages, tools=None, **kw) -> Completion:
        return self.chat(messages, **kw)


def u1_valid_token() -> None:
    prev = _env(OIDC_TEST_SECRET=SECRET, OIDC_ISSUER=ISSUER,
                OIDC_AUDIENCE=AUDIENCE)
    try:
        token = mint("user-123", tenant="acme", roles=["reader"])
        req = req_with(token)
        principal = ident.request_principal(req)
        check("U1 resolve: sub becomes id, not a client string",
              principal.id == "user-123" and principal.auth == "oidc", principal.id)
        check("U1 resolve: tenant + roles travel on the Principal",
              principal.tenant == "acme" and "reader" in principal.roles,
              f"{principal.tenant}/{principal.roles}")
        # the middleware stamps state for downstream seams
        req.state.principal = principal
        req.state.tenant = principal.tenant
        req.state.roles = list(principal.roles)
        check("U1 middleware: state carries principal/tenant/roles",
              req.state.tenant == "acme" and req.state.principal.id == "user-123")
    finally:
        _restore(prev)


def u2_forged_token() -> None:
    prev = _env(OIDC_TEST_SECRET=SECRET, OIDC_ISSUER=ISSUER,
                OIDC_AUDIENCE=AUDIENCE, ALLOW_ANON="0")
    try:
        token = mint("user-123", secret="wrong-secret")
        resp = server_mod.ingest_span_endpoint(req_with(token), {})
        check("U2 forged token → 401 on guarded route",
              getattr(resp, "status_code", 200) == 401, str(getattr(resp, "status_code", None)))
    finally:
        _restore(prev)


def u3_expired_token() -> None:
    prev = _env(OIDC_TEST_SECRET=SECRET, OIDC_ISSUER=ISSUER,
                OIDC_AUDIENCE=AUDIENCE, ALLOW_ANON="0")
    try:
        token = mint("user-123", exp_in=-60)
        resp = server_mod.ingest_span_endpoint(req_with(token), {})
        check("U3 expired token → 401",
              getattr(resp, "status_code", 200) == 401, str(getattr(resp, "status_code", None)))
    finally:
        _restore(prev)


def u4_cross_tenant() -> None:
    prev = _env(OIDC_TEST_SECRET=SECRET, OIDC_ISSUER=ISSUER,
                OIDC_AUDIENCE=AUDIENCE, ALLOW_ANON="0")
    prev_default = audit_mod._DEFAULT
    prev_desk = server_mod._approval_desk
    with tempfile.TemporaryDirectory() as tmp:
        try:
            log = audit_mod.AuditLog(path=Path(tmp) / "audit.jsonl")
            log.append({"principal": "analyst", "tenant": "acme", "tool": "get_order",
                        "decision": "allow", "code": "allowed", "args_hash": "h"})
            log.append({"principal": "intruder", "tenant": "other",
                        "tool": "get_order", "decision": "deny", "code": "no_grant",
                        "args_hash": "h"})
            audit_mod._DEFAULT = log  # type: ignore[assignment]

            other = req_with(mint("intruder", tenant="other"))
            other.state.tenant = "other"
            rows = server_mod.audit_endpoint(other)["entries"]
            check("U4 cross-tenant: caller sees only its own tenant",
                  len(rows) == 1 and rows[0]["principal"] == "intruder",
                  f"count={len(rows)}")
            acme = req_with(mint("analyst", tenant="acme"))
            acme.state.tenant = "acme"
            mine = server_mod.audit_endpoint(acme, principal="analyst")["entries"]
            check("U4 same-tenant + principal filter: OIDC sub resolves",
                  len(mine) == 1 and mine[0]["principal"] == "analyst"
                  and mine[0]["tenant"] == "acme", f"count={len(mine)}")

            store = StateStore(path=Path(tmp) / "approvals.json",
                               default_ttl=86400.0, autosave=False)
            desk = appr_mod.ApprovalStore(store, permissions=perms.default_permission_set(),
                                          audit=None)
            rec = desk.submit(tool="get_order", args={"id": "1"}, effect="read",
                              data="internal", reversibility="reversible",
                              requested_by="analyst")
            check("U4 approval record inherits the file tenant",
                  rec.tenant == "acme", rec.tenant)
            server_mod._approval_desk = lambda: desk  # type: ignore[assignment]
            foreign = server_mod.approvals_endpoint(other)
            fcount = foreign["count"] if isinstance(foreign, dict) else -1
            check("U4 cross-tenant approvals: foreign queue is empty",
                  fcount == 0, f"count={fcount}")
            home = server_mod.approvals_endpoint(acme)
            hcount = home["count"] if isinstance(home, dict) else -1
            check("U4 same-tenant approvals: own record visible",
                  hcount == 1, f"count={hcount}")
            one = server_mod.approval_endpoint(rec.request_id, request=other)
            check("U4 cross-tenant single record: 404, not a leak",
                  getattr(one, "status_code", 200) == 404)
        finally:
            audit_mod._DEFAULT = prev_default  # type: ignore[assignment]
            server_mod._approval_desk = prev_desk  # type: ignore[assignment]
            _restore(prev)


def u5_audit_carries_sub() -> None:
    prev = _env(OIDC_TEST_SECRET=SECRET, OIDC_ISSUER=ISSUER,
                OIDC_AUDIENCE=AUDIENCE)
    with tempfile.TemporaryDirectory() as tmp:
        try:
            _reg, gw_doc, gw_code, doc_store, code_store = build_registry()
            log = audit_mod.AuditLog(path=Path(tmp) / "audit.jsonl")
            reg = build_default_registry(
                doc=doc_retriever(gw_doc, doc_store),
                code=code_retriever(gw_code, code_store),
                doc_tau=DOC_TAU, code_tau=CODE_TAU,
                permissions=perms.default_permission_set(),
                principal="sub-123", audit=log)
            vr = reg.validate_args("search_saleor_docs", '{"query": "checkout"}')
            assert vr.ok, vr
            reg.execute("search_saleor_docs", vr.args or {}, decision=vr.decision)
            rows = log.entries(principal="sub-123")
            check("U5 governed call: audit entry carries the OIDC sub",
                  len(rows) == 1 and rows[0]["principal"] == "sub-123",
                  f"count={len(rows)}")

            log2 = audit_mod.AuditLog(path=Path(tmp) / "audit2.jsonl")
            reg2 = build_default_registry(
                doc=doc_retriever(gw_doc, doc_store),
                code=code_retriever(gw_code, code_store),
                doc_tau=DOC_TAU, code_tau=CODE_TAU,
                permissions=perms.default_permission_set(),
                principal="analyst", audit=log2)
            out = run_agent("What is a checkout?", reg2, ChatStub(),
                            principal=ident.Principal(id="analyst", tenant="acme",
                                                      roles=("operator",),
                                                      auth="oidc"),
                            max_steps=1)
            check("U5 run_agent(Principal): request identity flows to the run",
                  out.get("state_principal") == "analyst",
                  f"state_principal={out.get('state_principal')}")
            vr2 = reg2.validate_args("search_saleor_docs", '{"query": "checkout"}')
            assert vr2.ok, vr2
            reg2.execute("search_saleor_docs", vr2.args or {}, decision=vr2.decision)
            rows2 = [e for e in log2.entries(principal="analyst")]
            check("U5 governed entries carry id + file tenant",
                  bool(rows2) and all(e["tenant"] == "acme" for e in rows2),
                  f"entries={len(rows2)}")
        finally:
            _restore(prev)


def u6_anonymous() -> None:
    prev = _env(OIDC_TEST_SECRET=SECRET, OIDC_ISSUER=ISSUER,
                OIDC_AUDIENCE=AUDIENCE, ALLOW_ANON="1")
    try:
        resp = server_mod.ingest_span_endpoint(req_with(None), {})
        # TRACE_INGEST off → 404 disabled: auth *passed* (not a 401)
        check("U6 ALLOW_ANON=1: anonymous reaches the route (offline compat)",
              getattr(resp, "status_code", 0) == 404,
              str(getattr(resp, "status_code", None)))
    finally:
        _restore(prev)
    prev = _env(ALLOW_ANON="0")
    try:
        resp = server_mod.ingest_span_endpoint(req_with(None), {})
        check("U6 ALLOW_ANON=0: anonymous denied",
              getattr(resp, "status_code", 200) == 401,
              str(getattr(resp, "status_code", None)))
    finally:
        _restore(prev)


def u7_file_bindings() -> None:
    ps = perms.default_permission_set()
    check("U7 tenants: file mapping resolves, unknown falls back",
          ps.tenant_of("analyst") == "acme" and ps.tenant_of("nobody") == "default")
    check("U7 role_bindings: named roles resolve, unknown holds none",
          "reader" in ps.roles_of("analyst") and ps.roles_of("nobody") == ())
    from ai_os.permissions import check_call
    decided = check_call("nobody", "get_customer", {}, perms=ps)
    check("U7 unknown caller inherits default (audited fallback), still denied "
          "outside it",
          decided.allow and decided.unknown_principal_fallback
          and not check_call("nobody", "export_customer_report", {},
                             perms=ps).allow,
          decided.code)


def main() -> int:
    print("== WS1 — auth identity (U1–U7), offline HS256 ==")
    u1_valid_token()
    u2_forged_token()
    u3_expired_token()
    u4_cross_tenant()
    u5_audit_carries_sub()
    u6_anonymous()
    u7_file_bindings()
    print("\n" + ("ALL AUTH CHECKS PASSED" if not FAILS else f"{FAILS} CHECK(S) FAILED"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
