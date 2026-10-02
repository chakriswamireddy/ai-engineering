#!/usr/bin/env python3
"""WS2 (landed) — the API-limit fixture (L1–L5), offline.

The two limits the roadmap asked for, plus the metrics consequences of the
identity middleware, through the real seams. No server, no network: the
middleware is called directly with a `Request` and a `call_next` that fails
loudly if the limit let the request through.

- L1 body cap  → 413 before the body is read (never calls downstream)
- L2 route templates → ids never become metric labels (`{id}`, not a value)
- L3 denial counter → 401 increments route_requests_denied_total (span-close
  never fires for a refused request, so counting it here is load-bearing)
- L4 route timeout → 504 at the deadline instead of an open socket
- L5 knob contract → both limits env-tunable, defaults sane, 0 disables
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starlette.requests import Request

from ai_os import identity as ident
from ai_os import metrics as metrics_mod
from ai_os import server as server_mod

FAILS = 0


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


def _request(path: str = "/chat", *, content_length: int | None = None,
             method: str = "POST") -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode()))
    return Request({"type": "http", "method": method, "path": path,
                    "headers": headers, "query_string": b""})


async def _never_called(_req: Request):
    raise AssertionError("downstream was called — the limit did not stop the request")


def l1_body_cap() -> None:
    async def run() -> object:
        return await server_mod._identity_middleware(
            _request("/chat", content_length=2 * 1024 * 1024), _never_called)
    resp = asyncio.run(run())
    check("L1 2 MB body -> 413 with the cap in the payload",
          getattr(resp, "status_code", None) == 413
          and "max_body_bytes" in resp.body.decode(),
          str(getattr(resp, "status_code", None)))

    async def under() -> object:
        # under the cap the middleware hands off downstream (which here
        # answers 200 so the hand-off itself is the assertion)
        async def ok(_req: Request):
            from fastapi.responses import PlainTextResponse
            return PlainTextResponse("ok")
        return await server_mod._identity_middleware(
            _request("/health", content_length=16), ok)
    resp = asyncio.run(under())
    check("L1 under the cap: request reaches the handler untouched",
          getattr(resp, "status_code", None) == 200,
          str(getattr(resp, "status_code", None)))


def l2_route_templates() -> None:
    check("L2 trace id -> {id} label",
          server_mod._route_template("/traces/abc123def") == "/traces/{id}",
          server_mod._route_template("/traces/abc123def"))
    check("L2 approval id + action -> {id}/decide label",
          server_mod._route_template("/approvals/req-1/decide")
          == "/approvals/{id}/decide",
          server_mod._route_template("/approvals/req-1/decide"))
    check("L2 static routes pass through byte-identical",
          server_mod._route_template("/chat") == "/chat"
          and server_mod._route_template("/audit/verify") == "/audit/verify"
          and server_mod._route_template("/approvals") == "/approvals")


def l3_denied_counter() -> None:
    prev = _env(OIDC_TEST_SECRET="limits-secret", ALLOW_ANON="0")
    metrics_mod.reset()
    try:
        async def run() -> object:
            return await server_mod._identity_middleware(_request("/chat"), _never_called)
        resp = asyncio.run(run())
        snap = metrics_mod.registry().snapshot()
        denied = [c for c in snap["counters"]
                  if c["name"] == "route_requests_denied_total"]
        hit = next((c for c in denied
                    if c["labels"].get("route") == "/chat"
                    and c["labels"].get("status") == "unauthorized"), None)
        check("L3 anonymous on a guarded route -> 401",
              getattr(resp, "status_code", None) == 401,
              str(getattr(resp, "status_code", None)))
        check("L3 the refusal increments route_requests_denied_total"
              "{route,status=unauthorized}",
              hit is not None and hit["value"] >= 1,
              str(denied))
        check("L3 no latency series for a refused request (it never ran)",
              not [h for h in snap["histograms"]
                   if h["name"] == "route_latency_ms"
                   and h["labels"].get("route") == "/chat"])
    finally:
        metrics_mod.reset()
        _restore(prev)


def l4_route_timeout() -> None:
    original = server_mod._ROUTE_TIMEOUT_S
    server_mod._ROUTE_TIMEOUT_S = 0.05
    try:
        async def slow(_req: Request):
            await asyncio.sleep(0.5)
            from fastapi.responses import PlainTextResponse
            return PlainTextResponse("too late")
        resp = asyncio.run(server_mod._identity_middleware(_request("/health"), slow))
        check("L4 handler past the deadline -> 504 naming the knob",
              getattr(resp, "status_code", None) == 504
              and "timeout_s" in resp.body.decode(),
              str(getattr(resp, "status_code", None)))
    finally:
        server_mod._ROUTE_TIMEOUT_S = original

    original = server_mod._ROUTE_TIMEOUT_S
    server_mod._ROUTE_TIMEOUT_S = 0  # disabled
    try:
        async def slow_ok(_req: Request):
            await asyncio.sleep(0.1)
            from fastapi.responses import PlainTextResponse
            return PlainTextResponse("ok")
        resp = asyncio.run(server_mod._identity_middleware(_request("/health"), slow_ok))
        check("L4 ROUTE_TIMEOUT_S=0 disables the deadline (0 = off, documented)",
              getattr(resp, "status_code", None) == 200,
              str(getattr(resp, "status_code", None)))
    finally:
        server_mod._ROUTE_TIMEOUT_S = original


def l5_knob_contract() -> None:
    check("L5 body cap default is 1 MiB",
          server_mod._MAX_BODY_BYTES == 1_048_576, str(server_mod._MAX_BODY_BYTES))
    check("L5 route timeout default (300s) covers LLM_TIMEOUT_S * attempts (60*3)",
          server_mod._ROUTE_TIMEOUT_S >= 180, str(server_mod._ROUTE_TIMEOUT_S))
    prev = _env(MAX_BODY_BYTES="0", ROUTE_TIMEOUT_S="not-a-number")
    try:
        check("L5 0 disables the body cap (parse-time, documented in .env.example)",
              server_mod._int_env("MAX_BODY_BYTES", 1_048_576) == 0,
              str(server_mod._int_env("MAX_BODY_BYTES", 1_048_576)))
        check("L5 garbage falls back to the default instead of crashing boot",
              server_mod._int_env("ROUTE_TIMEOUT_S", 300) == 300,
              str(server_mod._int_env("ROUTE_TIMEOUT_S", 300)))
    finally:
        _restore(prev)


def main() -> int:
    print("== WS2 — API limits (L1–L5), offline ==")
    l1_body_cap()
    l2_route_templates()
    l3_denied_counter()
    l4_route_timeout()
    l5_knob_contract()
    print("\n" + ("ALL LIMIT CHECKS PASSED" if not FAILS else f"{FAILS} CHECK(S) FAILED"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
