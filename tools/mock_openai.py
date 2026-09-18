"""Minimal OpenAI-compatible chat/completions stub for local verification.

Stdlib only. Serves non-stream and SSE stream responses, plus opt-in
failure modes (401 / 429 / 400) so Day 3 §6 "break it deliberately"
can be exercised without a real provider.

    $ python tools/mock_openai.py --port 8911
"""
from __future__ import annotations

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = "gpt-4o-mini-stub"

ANSWER = (
    "Saleor is an open-source, headless commerce platform built on "
    "GraphQL and Django. This answer comes from the local stub server."
)


def non_stream_body(req: dict) -> dict:
    return {
        "id": "chatcmpl-stub-1",
        "object": "chat.completion",
        "model": MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": ANSWER},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 12,
            "completion_tokens": 24,
            "total_tokens": 36,
        },
    }


def stream_body(req: dict) -> str:
    events = []
    for token in ANSWER.split():
        events.append(
            "data: "
            + json.dumps(
                {
                    "id": "chatcmpl-stub-1",
                    "object": "chat.completion.chunk",
                    "model": MODEL,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": token + " "},
                            "finish_reason": None,
                        }
                    ],
                }
            )
            + "\n\n"
        )
    events.append(
        "data: "
        + json.dumps(
            {
                "id": "chatcmpl-stub-1",
                "model": MODEL,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 24,
                    "total_tokens": 36,
                },
            }
        )
        + "\n\n"
    )
    events.append("data: [DONE]\n\n")
    return "".join(events)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        req = json.loads(self.rfile.read(length) or b"{}")
        auth = self.headers.get("Authorization", "")

        mode = json.loads(self.headers.get("X-Stub-Mode", "null"))
        if mode == "401" or auth != f"Bearer sk-test":
            self._json(
                401,
                {"error": {"message": "Invalid API key provided."}},
            )
            return
        if mode == "429":
            self.send_response(429)
            self.send_header("Retry-After", "2")
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps({"error": {"message": "Rate limit reached"}}).encode()
            )
            return
        if mode == "400":
            self._json(
                400,
                {"error": {"message": "Unknown model: nonsense"}},
            )
            return
        if mode == "hang":
            time.sleep(30)
            return

        if req.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(stream_body(req).encode())
        else:
            self._json(200, non_stream_body(req))

    def _json(self, status: int, body: dict):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args):
        print(f"[mock] {self.command} {self.path} -> {args[0]}")


def main():
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8911
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"mock OpenAI listening on http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()