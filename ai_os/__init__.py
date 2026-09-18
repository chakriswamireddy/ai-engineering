"""AI OS — Day 4: the reusable model interface (ModelGateway).

    from ai_os import build_gateway_from_env
    gateway = build_gateway_from_env()
    completion = gateway.chat([{"role": "user", "content": "hi"}])

`ai_os.llm_client` still exists as the Day-3 single-provider client.
"""
from __future__ import annotations

# importing providers registers them in PROVIDER_REGISTRY
from ai_os import providers as _providers  # noqa: F401
from ai_os.providers.openai import OpenAIProvider
from ai_os.providers.anthropic import AnthropicProvider
from ai_os.gateway import ModelGateway
from ai_os.ledger import Ledger, MODEL_PRICES, cost_of, CallRecord
from ai_os.contract import (
    Usage, Completion,
    ProviderError, RateLimitError, ProviderTimeout, NetworkError,
    InvalidKeyError, BadRequestError,
)
from ai_os.config import build_gateway_from_env, provider_from_spec, _provider_specs

__all__ = [
    "OpenAIProvider", "AnthropicProvider",
    "ModelGateway", "Ledger", "MODEL_PRICES", "cost_of", "CallRecord",
    "Usage", "Completion",
    "ProviderError", "RateLimitError", "ProviderTimeout", "NetworkError",
    "InvalidKeyError", "BadRequestError",
    "build_gateway_from_env", "provider_from_spec",
]