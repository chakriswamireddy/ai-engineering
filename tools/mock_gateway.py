"""Day 4 §7 — scripted dual-provider stub for deterministic gateway tests.

No API key, no network. Two stdlib HTTP servers in one process:
  - OpenAI-compatible   on :8700, path POST /v1/chat/completions
  - Anthropic-shaped    on :8701, path POST /v1/messages

Every request consumes the next entry of that provider's *script*; a script
entry is one of:
  ok          -> 200 success (JSON or SSE, per the request `stream` flag)
  429         -> HTTP 429 + Retry-After (2s by default; override via reset)
  500 502 503 -> transient server error
  401         -> invalid key
  400         -> bad request
  malformed   -> HTTP 200 with garbage (non-JSON) body
  hang        -> sleep then answer => client-side timeout
  cut         -> SSE: send a chunk or two, then drop the connection (no [DONE])

Admin control (both ports):
  POST /admin/reset {"openai": [...], "anthropic": [...], "retry_after": 0.3}
  GET  /admin/state -> {hits: {openai: n, anthropic: n}, served: {...}, ...}

    $ python tools/mock_gateway.py             # both ports default
    $ python tools/mock_gateway.py 8700 8701
"""
from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ANSWER = (
    "Saleor is an open-source, headless commerce platform built on "
    "GraphQL and Django. This answer comes from the local stub server."
)
CHUNKS = ANSWER.split()

DEFAULTS: dict[str, list[str]] = {"openai": ["ok"], "anthropic": ["ok"]}

# Day 6 — scripted embeddings. A tiny vocabulary → vector table lets the
# stub do *deterministic, controllable* semantics: a text's vector is the
# normalized sum of its known words. Identical text → identical vector;
# texts sharing a term land nearer each other — enough for the smoke suite
# to assert on *which* chunk retrieves, without any API key.
EMBED_DIM = 8
EMBED_VOCAB: dict[str, list[float]] = {
    "checkout": [1.0, 0.2, 0.0, 0.0, 0.0, 0.1, 0.0, 0.0],
    "cart": [0.8, 0.1, 0.0, 0.1, 0.0, 0.0, 0.0, 0.0],
    "order": [0.9, 0.0, 0.1, 0.0, 0.1, 0.0, 0.0, 0.0],
    "product": [0.0, 0.0, 1.0, 0.0, 0.0, 0.9, 0.0, 0.0],
    "webhook": [0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.2, 0.0],
    "sync": [0.0, 0.0, 0.0, 0.0, 0.9, 0.0, 0.1, 0.0],
    "app": [0.0, 0.9, 0.0, 0.0, 0.0, 0.0, 0.0, 0.1],
    "payment": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.3],
    "fulfillment": [0.3, 0.0, 0.2, 0.0, 0.0, 0.0, 0.0, 1.0],
    "shipping": [0.0, 0.0, 0.0, 0.2, 0.0, 0.0, 0.6, 0.0],
}


def _embed_sum(text: str) -> list[float]:
    vec = [0.0] * EMBED_DIM
    for word in text.lower().split():
        w = word.strip(".,;:!?()[]{}'\"`*#-")
        if w in EMBED_VOCAB:
            for i, x in enumerate(EMBED_VOCAB[w]):
                vec[i] += x
    return vec


def embed_vector(text: str) -> list[float]:
    """Normalized scripted vector for `text` (zero vector if no known words)."""
    m = sum(x * x for x in _embed_sum(text)) ** 0.5
    v = _embed_sum(text)
    return [x / m for x in v] if m else v


def embed_prompt_tokens(inputs: list[str]) -> int:
    return sum(max(1, len(s) // 4) for s in inputs)


def embeddings_body(req: dict, inputs: list[str]) -> dict:
    model = req.get("model") or "text-embedding-3-small"
    return {
        "object": "list",
        "model": model,
        "data": [
            {"object": "embedding", "index": i, "embedding": embed_vector(t)}
            for i, t in enumerate(inputs)
        ],
        "usage": {"prompt_tokens": embed_prompt_tokens(inputs),
                  "total_tokens": embed_prompt_tokens(inputs)},
    }


class World:
    """Shared script + hit accounting, modifiable via /admin."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.scripts: dict[str, list[str]] = dict(DEFAULTS)
        self.embed_scripts: dict[str, list[str]] = dict(DEFAULTS)
        self.retry_after = 2.0
        self.hang_s = 30.0
        self.hits: dict[str, int] = {"openai": 0, "anthropic": 0}
        self.embed_hits: dict[str, int] = {"openai": 0, "anthropic": 0}
        self.served: dict[str, list[str]] = {"openai": [], "anthropic": []}
        self.embed_served: dict[str, list[str]] = {"openai": [], "anthropic": []}

    def reset(self, provider: str, script: list[str], retry_after: float,
              hang_s: float | None = None, embed_script: list[str] | None = None) -> None:
        with self.lock:
            self.scripts[provider] = [s.strip() for s in script if s.strip()] or ["ok"]
            if embed_script is not None:
                self.embed_scripts[provider] = ([s.strip() for s in embed_script
                                                 if s.strip()] or ["ok"])
            self.retry_after = retry_after
            if hang_s is not None:
                self.hang_s = hang_s
            self.hits = {"openai": 0, "anthropic": 0}
            self.embed_hits = {"openai": 0, "anthropic": 0}
            self.served = {"openai": [], "anthropic": []}
            self.embed_served = {"openai": [], "anthropic": []}

    def next(self, provider: str) -> str:
        with self.lock:
            script = self.scripts[provider]
            step = self.hits[provider]
            outcome = script[step] if step < len(script) else script[-1]
            self.hits[provider] += 1
            self.served[provider].append(outcome)
            return outcome

    def next_embed(self, provider: str) -> str:
        with self.lock:
            script = self.embed_scripts[provider]
            step = self.embed_hits[provider]
            outcome = script[step] if step < len(script) else script[-1]
            self.embed_hits[provider] += 1
            self.embed_served[provider].append(outcome)
            return outcome

    def state(self) -> dict:
        with self.lock:
            return {
                "hits": dict(self.hits),
                "served": dict(self.served),
                "scripts": dict(self.scripts),
                "embed_hits": dict(self.embed_hits),
                "embed_served": dict(self.embed_served),
                "embed_scripts": dict(self.embed_scripts),
                "retry_after": self.retry_after,
                "hang_s": self.hang_s,
            }


WORLD = World()


def openai_body(req: dict) -> dict:
    model = req.get("model") or "gpt-4o-mini"
    return {
        "id": "chatcmpl-stub-1",
        "object": "chat.completion",
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": ANSWER},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 12, "completion_tokens": 24, "total_tokens": 36},
    }


def anthropic_body(req: dict) -> dict:
    model = req.get("model") or "claude-3-5-haiku-latest"
    return {
        "id": "msg_stub_1",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": ANSWER}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }


def openai_sse(req: dict) -> str:
    model = req.get("model") or "gpt-4o-mini"
    events = []
    for i, token in enumerate(CHUNKS):
        events.append("data: " + json.dumps({
            "id": "chatcmpl-stub-1", "object": "chat.completion.chunk",
            "model": model,
            "choices": [{"index": 0, "delta": {"content": token + " "},
                         "finish_reason": None}],
        }) + "\n\n")
    events.append("data: " + json.dumps({
        "id": "chatcmpl-stub-1", "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 24, "total_tokens": 36},
    }) + "\n\n")
    events.append("data: [DONE]\n\n")
    return "".join(events)


def anthropic_sse(req: dict) -> str:
    model = req.get("model") or "claude-3-5-haiku-latest"
    out = []
    out.append('event: message_start\ndata: ' + json.dumps({
        "type": "message_start",
        "message": {"id": "msg_stub_1", "type": "message", "role": "assistant",
                    "model": model, "content": [],
                    "usage": {"input_tokens": 10, "output_tokens": 0}},
    }) + "\n\n")
    for token in CHUNKS:
        out.append('event: content_block_delta\ndata: ' + json.dumps({
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": token + " "},
        }) + "\n\n")
    out.append('event: message_delta\ndata: ' + json.dumps({
        "type": "message_delta",
        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
        "usage": {"output_tokens": 20},
    }) + "\n\n")
    out.append('event: message_stop\ndata: {"type":"message_stop"}\n\n')
    return "".join(out)


class Handler(BaseHTTPRequestHandler):
    provider = "openai"

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return {}

    def do_GET(self):
        if self.path == "/admin/state":
            body = json.dumps(WORLD.state()).encode()
            self._raw(200, body, "application/json")
        else:
            self.send_error(404)

    def do_POST(self):
        req = self._read_json()
        if self.path.startswith("/admin/reset"):
            retry_after = float(req.get("retry_after", WORLD.retry_after))
            hang_s = req.get("hang_s")
            script = req.get(self.provider, ["ok"])
            embed_script = req.get(f"{self.provider}_embed")
            WORLD.reset(self.provider, script, retry_after,
                        float(hang_s) if hang_s is not None else None,
                        embed_script)
            self._raw(200, json.dumps({"ok": True}).encode(), "application/json")
            return

        is_embed = self.path.rstrip("/").endswith("/embeddings")
        if is_embed:
            outcome = WORLD.next_embed(self.provider)
            inputs = req.get("input") or []
            if not isinstance(inputs, list):
                inputs = [inputs]
            if outcome == "ok":
                self._json(200, embeddings_body(req, inputs))
            elif outcome == "429":
                self._json_raw(429, {"error": {"message": "Rate limit reached"}},
                               extra={"Retry-After": str(WORLD.retry_after)})
            elif outcome == "401":
                self._json_raw(401, {"error": {"message": "Invalid API key provided."}})
            elif outcome == "400":
                self._json_raw(400, {"error": {"message": "Invalid request: unknown parameter"}})
            elif outcome in ("500", "502", "503"):
                self._json_raw(int(outcome), {"error": {"message": f"server {outcome}"}})
            elif outcome == "malformed":
                self._raw(200, b"{not valid json", "text/plain")
            elif outcome == "hang":
                time.sleep(WORLD.hang_s)
                self._json(200, embeddings_body(req, inputs))
            else:
                self._json(200, embeddings_body(req, inputs))
            return

        outcome = WORLD.next(self.provider)
        stream = bool(req.get("stream"))

        if outcome == "ok":
            if stream:
                body = openai_sse(req) if self.provider == "openai" else anthropic_sse(req)
                self._raw(200, body.encode(), "text/event-stream")
            else:
                resp = openai_body(req) if self.provider == "openai" else anthropic_body(req)
                self._json(200, resp)
        elif outcome == "429":
            if stream:
                self._raw(429, json.dumps({"error": {"message": "Rate limit reached"}}).encode(),
                          "application/json", extra={"Retry-After": str(WORLD.retry_after)})
            else:
                self._json_raw(429, {"error": {"message": "Rate limit reached"}},
                               extra={"Retry-After": str(WORLD.retry_after)})
        elif outcome == "401":
            self._json_raw(401, {"error": {"message": "Invalid API key provided."}})
        elif outcome == "400":
            self._json_raw(400, {"error": {"message": "Invalid request: unknown parameter"}})
        elif outcome in ("500", "502", "503"):
            self._json_raw(int(outcome), {"error": {"message": f"server {outcome}"}})
        elif outcome == "malformed":
            self._raw(200, b"{not valid json", "text/plain")
        elif outcome == "hang":
            time.sleep(WORLD.hang_s)
            resp = openai_body(req) if self.provider == "openai" else anthropic_body(req)
            self._json(200, resp)
        elif outcome == "cut":
            # SSE: send two chunks, then drop the connection before [DONE]
            if self.provider == "openai":
                model = req.get("model") or "gpt-4o-mini"
                body = "".join("data: " + json.dumps({
                    "model": model, "choices": [{"delta": {"content": t + " "}}]
                }) + "\n\n" for t in CHUNKS[:2])
            else:
                body = ''.join(
                    'event: content_block_delta\ndata: ' + json.dumps({
                        "type": "content_block_delta", "index": 0,
                        "delta": {"type": "text_delta", "text": t + " "},
                    }) + "\n\n" for t in CHUNKS[:2])
            self._raw(200, body.encode(), "text/event-stream", close=True)
        else:
            resp = openai_body(req) if self.provider == "openai" else anthropic_body(req)
            self._json(200, resp)

    def _json(self, status: int, body: dict):
        self._raw(status, json.dumps(body).encode(), "application/json")

    def _json_raw(self, status: int, body: dict, extra: dict | None = None):
        self._raw(status, json.dumps(body).encode(), "application/json", extra=extra)

    def _raw(self, status: int, data: bytes, content_type: str,
             extra: dict | None = None, close: bool = False):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
            if close:
                self.wfile.flush()
                self.close_connection = True
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # client timed out / dropped mid-write (stub stays quiet)

    def log_message(self, fmt: str, *args):
        print(f"[stub:{self.provider}] {fmt % args}")


def _server(provider: str, port: int, daemon: bool = True):
    handler = type(f"{provider.title()}Handler", (Handler,), {"provider": provider})
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    httpd.daemon_threads = daemon
    t = threading.Thread(target=httpd.serve_forever, daemon=daemon)
    t.start()
    return httpd, t


def main() -> None:
    base = 8700
    if len(sys.argv) > 1:
        base = int(sys.argv[1])
    servers = [_server("openai", base), _server("anthropic", base + 1)]
    print(f"mock gateway: openai :{base} / anthropic :{base+1}")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        for httpd, _ in servers:
            httpd.shutdown()


if __name__ == "__main__":
    main()