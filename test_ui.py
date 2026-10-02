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
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from ai_os.server import _AUTH_PREFIXES, chat_ui  # noqa: E402


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


if __name__ == "__main__":
    for fn in (test_ui_serves_the_shipped_html,
               test_ui_is_public_but_its_data_calls_are_not,
               test_ui_missing_file_fails_loud):
        fn()
        print(f"[OK] {fn.__name__}")
    print("ALL UI CHECKS PASSED")
