"""Day 4 §6 — configuration: every knob via env, keys never in code.

```
LLM_PROVIDERS=openai:gpt-4o-mini,anthropic:claude-3-5-haiku-latest
OPENAI_API_KEY=...      ANTHROPIC_API_KEY=...
OPENAI_BASE_URL=...     ANTHROPIC_BASE_URL=...
LLM_MAX_ATTEMPTS=3      LLM_TIMEOUT_S=60
LLM_BASE_DELAY_S=1.0    LLM_MAX_DELAY_S=30.0
LLM_TOTAL_BUDGET_S=0    # seconds for whole chain; 0 = unlimited
```

"which model, in what order" is runtime configuration, not code.
"""
from __future__ import annotations

import os

from ai_os.gateway import ModelGateway
from ai_os.providers import PROVIDER_REGISTRY


def _load_dotenv(path: str = ".env") -> None:
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip().strip('"').strip("'")
                if key and key not in os.environ and not key.startswith("export"):
                    os.environ[key] = value
                elif key.startswith("export"):
                    os.environ[key[len("export"):].strip()] = value
    except FileNotFoundError:
        pass


def _provider_specs() -> list[tuple[str, str]]:
    raw = os.getenv("LLM_PROVIDERS", "").strip()
    if not raw:
        return []
    specs: list[tuple[str, str]] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        provider, _, model = item.partition(":")
        specs.append((provider.strip().lower(), model.strip() or ""))
    return specs


def provider_from_spec(provider: str, model: str):
    cls = PROVIDER_REGISTRY.get(provider)
    if cls is None:
        raise ValueError(f"unknown provider {provider!r} (known: {sorted(PROVIDER_REGISTRY)})")
    default_model = getattr(cls, "DEFAULT_MODEL", "gpt-4o-mini")
    default_url = getattr(cls, "DEFAULT_BASE_URL", None)
    api_key = os.getenv(f"{provider.upper()}_API_KEY", "").strip() or os.getenv("OPENAI_API_KEY", "").strip()
    base_url = (os.getenv(f"{provider.upper()}_BASE_URL", "").strip()
                or (default_url or "https://api.openai.com/v1"))
    return cls(api_key=api_key, model=model or default_model,
               base_url=base_url, timeout_s=float(os.getenv("LLM_TIMEOUT_S", "60")))


def build_gateway_from_env() -> ModelGateway:
    _load_dotenv()
    specs = _provider_specs()
    if not specs:
        specs = [("openai", "gpt-4o-mini")]
    providers = [
        provider_from_spec(p, m)
        for p, m in specs
        if os.getenv(f"{p.upper()}_API_KEY", "") or os.getenv("OPENAI_API_KEY", "")
    ]
    missing = [p for p, _ in specs
               if not (os.getenv(f"{p.upper()}_API_KEY", "") or os.getenv("OPENAI_API_KEY", ""))]
    if missing:
        raise ValueError(
            f"missing API key(s) for providers: {', '.join(missing)} — "
            f"set {', '.join(f'{p.upper()}_API_KEY' for p in missing)} in .env"
        )
    if not providers:
        raise ValueError("no providers could be built from env (check LLM_PROVIDERS and keys)")
    return ModelGateway(
        providers,
        max_attempts=int(os.getenv("LLM_MAX_ATTEMPTS", "3")),
        timeout_s=float(os.getenv("LLM_TIMEOUT_S", "60")),
        base_delay_s=float(os.getenv("LLM_BASE_DELAY_S", "1.0")),
        max_delay_s=float(os.getenv("LLM_MAX_DELAY_S", "30.0")),
        total_budget_s=float(os.getenv("LLM_TOTAL_BUDGET_S", "0")),
    )