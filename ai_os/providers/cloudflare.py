"""Day 6 §7 — CloudflareProvider: Workers AI embeddings through the gateway.

Same normalized contract as the OpenAI provider, different wire format:
`POST /accounts/{account_id}/ai/run/{model}` with `{"text": [...]}` →
`{result: {data: [[…]], shape, pooling, usage}, success, errors, messages}`.

The API token is a Cloudflare user API token (`CLOUDFLARE_API_KEY`) and the
account id comes from `CLOUDFLARE_ACCOUNT_ID` (the hex string after
`dash.cloudflare.com/`). Workers AI runs chat models too, but this provider
firmly implements embeddings only: `chat()`/`stream()` raise a non-retryable
`ProviderError` so the gateway falls through to a chat-capable provider.
"""
from __future__ import annotations

import json
from typing import Any, Iterator
from urllib import error as urlerror, request
from urllib.parse import urljoin

from ai_os.contract import (
    Completion, Embedding, InvalidKeyError, ProviderError, ProviderTimeout,
    NetworkError, Usage, usage_from_dict,
)
from ai_os.providers import register
from ai_os.providers import parse_sse_line, raise_for_status, retry_after_from, try_parse_json_bytes


@register
class CloudflareProvider:
    name = "cloudflare"
    DEFAULT_BASE_URL = "https://api.cloudflare.com/client/v4/accounts"
    DEFAULT_MODEL = ""                       # chat models need a cf-model spec
    DEFAULT_EMBED_MODEL = "@cf/baai/bge-base-en-v1.5"

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL,
                 base_url: str = DEFAULT_BASE_URL, timeout_s: float = 60.0,
                 embedding_model: str = DEFAULT_EMBED_MODEL,
                 account_id: str = ""):
        if not api_key:
            raise InvalidKeyError(self.name, "no api_key supplied")
        if not account_id:
            raise InvalidKeyError(self.name,
                                 "no account_id supplied — set CLOUDFLARE_ACCOUNT_ID "
                                 "to the hex id from dash.cloudflare.com")
        self.api_key = api_key
        self.model = model
        self.embedding_model = embedding_model
        self.account_id = account_id
        self.base_url = base_url.rstrip("/")
        if not self.base_url.rstrip("/").endswith(f"/{account_id}"):
            self.base_url = f"{self.base_url}/{account_id}"
        self.timeout_s = float(timeout_s)

    # -- private: wire format lives only here --------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": "ai-layer/0.2 (cloudflare provider)",
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

    # -- interface: embeddings (the whole point of this provider) ------------

    def embed(self, inputs: list[str]) -> list[Embedding]:
        data = self._post(f"ai/run/{self.embedding_model}", {"text": inputs})
        if data.get("success") is False or data.get("errors"):
            errs = data.get("errors") or []
            msg = (errs[0].get("message") if errs else "workers ai error")
            raise NetworkError(self.name, f"{msg}")
        result = data.get("result") or {}
        rows = [r for r in result.get("data") or [] if isinstance(r, list)]
        if not rows:
            raise NetworkError(self.name, "embedding response had no data rows")
        if len(rows) != len(inputs):
            raise NetworkError(
                self.name,
                f"returned {len(rows)} vectors for {len(inputs)} inputs")
        usage = usage_from_dict(result.get("usage") or {})
        return [
            Embedding(
                text=text,
                vector=[float(x) for x in vec],
                model=self.embedding_model,
                usage=Usage(usage.input_tokens, 0, usage.total_tokens),
            )
            for text, vec in zip(inputs, rows)
        ]

    # -- interface: chat/stream are out of scope (embeddings only) -----------

    def _no_chat(self) -> Completion:
        raise ProviderError(
            self.name,
            f"provider is embeddings-only (model {self.embedding_model}); "
            "the gateway should fall through to a chat-capable provider",
            retryable=False,
        )

    def chat(self, messages: list[dict[str, Any]], *, temperature: float = 0.0,
             max_tokens: int = 512,
             tools: list[dict[str, Any]] | None = None) -> Completion:
        return self._no_chat()

    def stream(self, messages: list[dict[str, Any]], *, temperature: float = 0.0,
               max_tokens: int = 512,
               tools: list[dict[str, Any]] | None = None) -> Iterator[dict[str, Any]]:
        self._no_chat()
        yield {}                               # unreachable; satisfies iterator