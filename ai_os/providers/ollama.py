"""WS5 — OllamaProvider: self-hosted embeddings (and optional chat).

Self-hosted alternative to the paid embedding tier for the 29,156-symbol
Saleor code store: Ollama `bge-base-en-v1.5` (dim 768).

Wire format (Ollama REST):
  POST {base}/api/embed  {"model": ..., "input": [...]} -> {"embeddings": [[...]]}
  (older servers: POST /api/embeddings {"model","prompt"} per input)

No API key is required — local daemon. `config.provider_from_spec`
therefore allows `ollama` with an empty key (see config.py).
"""
from __future__ import annotations

import json
from typing import Any, Iterator
from urllib import error as urlerror, request
from urllib.parse import urljoin

from ai_os import observability as obs
from ai_os.contract import (
    Completion, Embedding, NetworkError, ProviderError, ProviderTimeout, Usage,
)
from ai_os.providers import register
from ai_os.providers import raise_for_status, retry_after_from, try_parse_json_bytes


@register
class OllamaProvider:
    name = "ollama"
    DEFAULT_BASE_URL = "http://127.0.0.1:11434"
    DEFAULT_MODEL = "llama3.1"
    DEFAULT_EMBED_MODEL = "bge-base-en-v1.5"
    EXPECTED_DIM = 768

    def __init__(self, api_key: str = "", model: str = DEFAULT_MODEL,
                 base_url: str = DEFAULT_BASE_URL, timeout_s: float = 60.0,
                 embedding_model: str = DEFAULT_EMBED_MODEL):
        # No key: local daemon. Accept anything (including "").
        self.api_key = api_key or ""
        self.model = model or self.DEFAULT_MODEL
        self.embedding_model = embedding_model or self.DEFAULT_EMBED_MODEL
        self.base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self.timeout_s = float(timeout_s)

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "User-Agent": "ai-layer/0.2 (ollama provider)",
            **obs.outbound_headers(),
        }

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        req = request.Request(
            urljoin(self.base_url + "/", path),
            data=json.dumps(body).encode(),
            headers=self._headers(),
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout_s) as resp:
                try:
                    return json.load(resp)
                except json.JSONDecodeError as e:
                    raise NetworkError(self.name, f"malformed JSON in 200: {e}") from e
        except urlerror.HTTPError as e:
            parsed = try_parse_json_bytes(e)
            raise_for_status(self.name, e.code, parsed, retry_after_from(e.headers))
            return parsed
        except TimeoutError as e:
            raise ProviderTimeout(self.name, f"timed out after {self.timeout_s}s: {e}") from e
        except urlerror.URLError as e:
            raise NetworkError(self.name, f"network error: {e.reason}") from e

    def embed(self, inputs: list[str]) -> list[Embedding]:
        data = self._post("api/embed", {"model": self.embedding_model, "input": inputs})
        rows = data.get("embeddings")
        if not isinstance(rows, list) or not rows:
            raise NetworkError(self.name, "embedding response had no data rows")
        if len(rows) != len(inputs):
            raise NetworkError(
                self.name, f"returned {len(rows)} vectors for {len(inputs)} inputs")
        return [
            Embedding(text=text, vector=[float(x) for x in vec],
                      model=self.embedding_model, usage=Usage(0, 0, 0))
            for text, vec in zip(inputs, rows)
        ]

    def chat(self, messages: list[dict[str, Any]], *, temperature: float = 0.0,
             max_tokens: int = 512,
             tools: list[dict[str, Any]] | None = None) -> Completion:
        # Minimal Ollama chat via /api/chat (non-stream). Tools are ignored:
        # self-hosted chat is a fallback, not the tool wire.
        data = self._post("api/chat", {
            "model": self.model, "messages": messages,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        })
        text = ((data.get("message") or {}).get("content")) or ""
        if not text.strip():
            raise ProviderError(self.name, "empty completion (no content returned)",
                                retryable=True)
        return Completion(text=text, model=self.model, finish_reason="stop",
                          usage=Usage(0, 0, 0), raw=data)

    def stream(self, messages: list[dict[str, Any]], *, temperature: float = 0.0,
               max_tokens: int = 512,
               tools: list[dict[str, Any]] | None = None) -> Iterator[dict[str, Any]]:
        completion = self.chat(messages, temperature=temperature, max_tokens=max_tokens)
        yield {"delta": completion.text, "finish_reason": "stop",
               "usage": {}, "model": completion.model}
