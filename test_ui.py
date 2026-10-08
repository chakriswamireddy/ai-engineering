"""The /ui shell — served without a token, wired to the guarded surface.

    $ python test_ui.py

No network, no provider: the page is a static file, so the suite pins the
two properties that matter —

- `GET /ui` renders the shipped HTML (the file is in the image, the route
  returns 200 with text/html);
- the route is NOT inside `_AUTH_PREFIXES`. The page itself is public like
  /healthz; its data calls (/chat/stream, /rag, /traces) stay guarded. If a
  later change blanket-authenticates /ui, or worse moves a data call out of
  the guarded surface, one of these assertions breaks.

Plus the console login (`POST /ui/login`): the one unauthenticated write.
It must stay unguarded (it mints the token the guard demands), mint through
identity.mint_token (one writer of claims), reject bad credentials, and
throttle repeated failures per IP.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from ai_os.server import _AUTH_PREFIXES, chat_ui  # noqa: E402


def _env(**overrides: str | None) -> dict[str, str | None]:
    """Apply env overrides, return the previous values for restore."""
    prev = {k: os.environ.get(k) for k in overrides}
    for k, v in overrides.items():
        if v is None:
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


def _login_request(ip: str = "10.9.9.9"):
    """A minimal ASGI Request — the route only reads x-real-ip/client/host."""
    from fastapi import Request
    return Request({
        "type": "http", "method": "POST", "path": "/ui/login",
        "headers": [(b"x-real-ip", ip.encode())],
        "client": (ip, 4242), "server": ("test", 80),
        "scheme": "http", "query_string": b"",
    })


def _login(username: str, password: str, ip: str = "10.9.9.9"):
    from ai_os.server import UiLoginRequest, ui_login
    return ui_login(UiLoginRequest(username=username, password=password),
                    _login_request(ip))


def test_ui_serves_the_shipped_html() -> None:
    resp = chat_ui()
    assert resp.status_code == 200, resp.status_code
    assert resp.media_type == "text/html"
    body = resp.body.decode("utf-8")
    # Every hook the page needs — endpoints, auth header, both corpora.
    for marker in ("/chat/stream", "/rag", "/traces",
                   "Authorization", "docs", "code",
                   'id="log"', "localStorage"):
        assert marker in body, f"missing marker: {marker}"
    # The two features this file pins beyond the endpoints themselves:
    # the console login gate and the trace-driven RAG pipeline panel.
    for marker in ('id="login-form"', "/ui/login", "traceparent", 'id="btn-logout"'):
        assert marker in body, f"missing marker: {marker}"


def test_ui_renders_the_answer_and_explains_the_pipeline() -> None:
    # Three properties of the console that only exist in the shipped HTML:
    # 1. markdown is RENDERED (a preview), never printed as raw **source**;
    # 2. every pipeline step carries a ground layer (what actually runs);
    # 3. each step names the day of docs/AI_Engineering_OS_30_Day_Apprenticeship.md
    #    it IS, and the answer lists the days that run exercised.
    body = chat_ui().body.decode("utf-8")
    for marker in ("renderMarkdown", "mdExtractCites", "mdCiteIndex", "cite-ref",
                   "cite-note", "deepView", '"pipe-under"', "flowDaysFor",
                   "PLAN_DAYS", "AI_Engineering_OS_30_Day_Apprenticeship.md"):
        assert marker in body, f"missing marker: {marker}"


def test_ui_is_public_but_its_data_calls_are_not() -> None:
    # /ui itself: not guarded (it is a static shell with zero data).
    assert not any("/ui".startswith(p) for p in _AUTH_PREFIXES)
    # The calls the page makes: all guarded — a token-less browser gets 401s.
    for route in ("/chat/stream", "/rag", "/traces"):
        assert any(route.startswith(p) for p in _AUTH_PREFIXES), route


def test_ui_missing_file_fails_loud() -> None:
    # A broken image (file stripped by a bad COPY) must 404 with a pointer,
    # never a 500 with a traceback. Manual swap so the test also runs as a
    # plain script (`python test_ui.py`), without pytest's monkeypatch.
    import ai_os.server as srv
    original = srv._CHAT_UI_PATH
    try:
        srv._CHAT_UI_PATH = original + ".missing"
        resp = srv.chat_ui()
        assert resp.status_code == 404
        assert "chat UI not installed" in resp.body.decode()
    finally:
        srv._CHAT_UI_PATH = original


def test_login_route_is_unguarded_but_data_calls_are_not() -> None:
    # /ui/login must NOT be guarded — it mints the token the guard demands.
    # The console's data calls stay guarded, so a forged login page cannot
    # read anything by itself.
    assert not any("/ui/login".startswith(p) for p in _AUTH_PREFIXES)
    for route in ("/chat/stream", "/rag", "/traces"):
        assert any(route.startswith(p) for p in _AUTH_PREFIXES), route


def test_login_mints_a_verifiable_token() -> None:
    import ai_os.server as srv
    from ai_os import identity
    prev = _env(OIDC_HS256_SECRET="test-secret-for-ui-login",
                UI_ADMIN_USER=None, UI_ADMIN_PASSWORD=None,
                UI_LOGIN_TTL_S=None)
    srv._login_fails.clear()
    try:
        out = _login("admin", "admin@1234")
        assert isinstance(out, dict), getattr(out, "status_code", out)
        claims = identity.verify_token(out["token"])
        assert claims["sub"] == "admin"
        assert "admin" in (claims.get("roles") or [])
        assert claims["exp"] > claims["iat"]
        assert out["expires_in"] > 0
    finally:
        _restore(prev)
        srv._login_fails.clear()


def test_login_rejects_bad_credentials_with_a_throttle() -> None:
    import ai_os.server as srv
    prev = _env(OIDC_HS256_SECRET="test-secret-for-ui-login",
                UI_ADMIN_USER=None, UI_ADMIN_PASSWORD=None)
    srv._login_fails.clear()
    try:
        # Wrong password: 401, never a token.
        resp = _login("admin", "wrong-password", ip="10.9.9.1")
        assert getattr(resp, "status_code", None) == 401, resp
        # Failures accumulate per IP: the 5th locks the window …
        for _ in range(4):
            resp = _login("admin", "still-wrong", ip="10.9.9.1")
            assert getattr(resp, "status_code", None) == 401, resp
        # … and even the CORRECT password is refused while locked.
        resp = _login("admin", "admin@1234", ip="10.9.9.1")
        assert getattr(resp, "status_code", None) == 429, resp
        # Other IPs are unaffected (the window is per client).
        out = _login("admin", "admin@1234", ip="10.9.9.2")
        assert isinstance(out, dict), getattr(out, "status_code", out)
    finally:
        _restore(prev)
        srv._login_fails.clear()


def test_login_credentials_are_env_tunable() -> None:
    import ai_os.server as srv
    prev = _env(OIDC_HS256_SECRET="test-secret-for-ui-login",
                UI_ADMIN_USER="operator", UI_ADMIN_PASSWORD="s3cret-pass")
    srv._login_fails.clear()
    try:
        resp = _login("admin", "admin@1234", ip="10.9.9.3")
        assert getattr(resp, "status_code", None) == 401, resp
        out = _login("operator", "s3cret-pass", ip="10.9.9.3")
        assert isinstance(out, dict), getattr(out, "status_code", out)
        assert out["sub"] == "operator"
    finally:
        _restore(prev)
        srv._login_fails.clear()


if __name__ == "__main__":
    for fn in (test_ui_serves_the_shipped_html,
               test_ui_renders_the_answer_and_explains_the_pipeline,
               test_ui_is_public_but_its_data_calls_are_not,
               test_ui_missing_file_fails_loud,
               test_login_route_is_unguarded_but_data_calls_are_not,
               test_login_mints_a_verifiable_token,
               test_login_rejects_bad_credentials_with_a_throttle,
               test_login_credentials_are_env_tunable):
        fn()
        print(f"[OK] {fn.__name__}")
    print("ALL UI CHECKS PASSED")
