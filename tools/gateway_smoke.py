"""Day 4 §7 — deliberate-failure test suite against the scripted stub.

No API key, no real network. Spawns tools/mock_gateway.py (:8700 openai /
:8701 anthropic) if it is not already running, then walks every row of the
day-4.md §7 table and asserts on the *ledger* (records, attempts, cost) plus
stub hit counts.

    $ python tools/gateway_smoke.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STUB = Path(__file__).resolve().parent / "mock_gateway.py"
sys.path.insert(0, str(ROOT))

from ai_os.contract import (  # noqa: E402
    BadRequestError, InvalidKeyError, NetworkError, ProviderError,
    ProviderTimeout,
)
from ai_os.gateway import ModelGateway  # noqa: E402
from ai_os.ledger import Ledger, cost_of  # noqa: E402
from ai_os.providers.anthropic import AnthropicProvider  # noqa: E402
from ai_os.providers.openai import OpenAIProvider  # noqa: E402

MSG = [{"role": "user", "content": "What is Saleor?"}]
OPENAI_URL = "http://127.0.0.1:8700/v1"
ANTHROPIC_URL = "http://127.0.0.1:8701/v1"
ANSWER = ("Saleor is an open-source, headless commerce platform built on "
          "GraphQL and Django. This answer comes from the local stub server.")
FAILS = 0
ALL_LEDGERS: list[Ledger] = []


def check(label: str, cond: bool, detail: str = ""):
    global FAILS
    status = "OK  " if cond else "FAIL"
    if not cond:
        FAILS += 1
    print(f"[{status}] {label}" + (f"  ({detail})" if detail else ""))


def _admin(port: int, provider: str, script, retry_after=None, hang_s=None) -> None:
    body: dict = {provider: script}
    if retry_after is not None:
        body["retry_after"] = retry_after
    if hang_s is not None:
        body["hang_s"] = hang_s
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/admin/reset",
        data=json.dumps(body).encode(), method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 200


def reset(openai=None, anthropic=None, retry_after=None, hang_s=None) -> None:
    _admin(8700, "openai", openai or ["ok"], retry_after, hang_s)
    _admin(8701, "anthropic", anthropic or ["ok"], retry_after, hang_s)


def hits(provider: str) -> int:
    req = urllib.request.Request("http://127.0.0.1:8700/admin/state")
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)["hits"][provider]


def make_gateway(*, max_attempts: int = 3, timeout_s: float = 5.0,
                 base: float = 0.05, cap: float = 0.5, budget: float = 0.0):
    led = Ledger()
    gw = ModelGateway(
        [
            OpenAIProvider(api_key="sk-test", model="gpt-4o-mini",
                           base_url=OPENAI_URL, timeout_s=timeout_s),
            AnthropicProvider(api_key="sk-ant-test", model="claude-3-5-haiku-latest",
                              base_url=ANTHROPIC_URL, timeout_s=timeout_s),
        ],
        max_attempts=max_attempts, timeout_s=timeout_s,
        base_delay_s=base, max_delay_s=cap, total_budget_s=budget, ledger=led)
    return gw, led


def run(label: str, fn) -> None:
    print(f"\n== {label}")
    ALL_LEDGERS.append(fn())


# -------------------------------------------------------------------- §7 rows

def case_ok():
    reset(openai=["ok"])
    gw, led = make_gateway()
    c = gw.chat(MSG)
    check("200 ok: text matches stub", c.text == ANSWER)
    check("200 ok: 1 attempt, 1 ledger record",
          gw.last_attempts == 1 and len(led.records) == 1)
    check("200 ok: usage normalized 12/24/36",
          c.usage.total_tokens == 36 and c.usage.input_tokens == 12
          and c.usage.output_tokens == 24, str(c.usage))
    expect = round(cost_of("gpt-4o-mini", c.usage), 8)
    check("200 ok: ledger cost == price table",
          abs(led.total_cost() - expect) < 1e-9, f"${led.total_cost():.8f}")
    return led


def case_stream_ok():
    reset(openai=["ok"])
    gw, led = make_gateway()
    chunks = list(gw.stream(MSG))
    text = "".join(c.get("delta", "") for c in chunks)
    check("stream ok: assembled text matches", text.strip() == ANSWER)
    check("stream ok: ttft on first chunk", "ttft_ms" in chunks[0])
    check("stream ok: usage on final chunk",
          chunks[-1].get("usage", {}).get("total_tokens") == 36)
    check("stream ok: ledger ok", len(led.records) == 1 and led.records[0].ok)
    return led


def case_retry_after():
    reset(openai=["429", "ok"], retry_after=0.4)
    gw, led = make_gateway(base=5.0)   # huge base; honoring Retry-After is the only fast path
    t0 = time.perf_counter()
    c = gw.chat(MSG)
    elapsed = time.perf_counter() - t0
    check("429→200: succeeds via openai", c.text == ANSWER)
    check("429→200: 2 attempts, 1 failure + 1 ok",
          gw.last_attempts == 2 and len(led.failures()) == 1
          and led.records[-1].ok)
    check("429→200: honored Retry-After (ran fast, not base)",
          elapsed < 2.0, f"{elapsed:.2f}s")
    return led


def case_backoff_growth():
    reset(openai=["500", "500", "ok"])
    gw, led = make_gateway()
    t0 = time.perf_counter()
    c = gw.chat(MSG)
    elapsed = time.perf_counter() - t0
    check("500,500,200: 3 attempts, 2 failures", gw.last_attempts == 3
          and len(led.failures()) == 2)
    check("500,500,200: backoff sleeps happened",
          elapsed >= 2 * 0.05 * 0.5, f"{elapsed:.2f}s")
    check("500,500,200: final ok", c.text == ANSWER)
    return led


def case_retry_cap_then_fallback():
    reset(openai=["500"] * 5, anthropic=["ok"])
    gw, led = make_gateway(max_attempts=3)
    c = gw.chat(MSG)
    check("500x5 (max 3): capped at 3 hits", hits("openai") == 3,
          f"openai hits={hits('openai')}")
    check("500x5 (max 3): fell back to anthropic",
          gw.last_provider == "anthropic" and c.text == ANSWER)
    check("500x5 (max 3): 4 attempts, 3 failures, 1 ok",
          gw.last_attempts == 4 and len(led.failures()) == 3
          and led.records[-1].ok)
    return led


def case_401_no_retry():
    reset(openai=["401"], anthropic=["401"])
    gw, led = make_gateway()
    try:
        gw.chat(MSG)
        check("401: raised", False)
    except InvalidKeyError:
        check("401: raised InvalidKeyError", True)
    check("401: 0 retries (1 openai hit)", hits("openai") == 1)
    check("401: 0 retries (1 anthropic hit)", hits("anthropic") == 1)
    check("401: both failures in ledger", len(led.failures()) == 2)
    return led


def case_401_fallback():
    reset(openai=["401"], anthropic=["ok"])
    gw, led = make_gateway()
    c = gw.chat(MSG)
    check("401 on primary → succeeds via fallback",
          gw.last_provider == "anthropic" and c.text == ANSWER
          and gw.last_attempts == 2)
    check("401 on primary → 1 failure in ledger", len(led.failures()) == 1)
    return led


def case_400_no_retry():
    reset(openai=["400"], anthropic=["400"])
    gw, led = make_gateway()
    try:
        gw.chat(MSG)
        check("400: raised", False)
    except BadRequestError:
        check("400: raised BadRequestError", True)
    check("400: 0 retries", hits("openai") == 1 and hits("anthropic") == 1)
    return led


def case_timeout_fallback():
    reset(openai=["hang"], anthropic=["ok"], hang_s=2.0)
    gw, led = make_gateway(timeout_s=1.0, base=0.05, cap=0.3)
    t0 = time.perf_counter()
    c = gw.chat(MSG)
    elapsed = time.perf_counter() - t0
    check("timeout: retried 3x then fell back",
          gw.last_provider == "anthropic" and c.text == ANSWER
          and gw.last_attempts == 4)
    check("timeout: 3 timeout failures recorded",
          len(led.failures()) == 3
          and all("timed out" in f.error for f in led.failures()))
    check("timeout: waited >= 3 x 1s", elapsed >= 3.0, f"{elapsed:.2f}s")
    return led


def case_malformed():
    reset(openai=["malformed"], anthropic=["ok"])
    gw, led = make_gateway()
    c = gw.chat(MSG)
    check("malformed JSON in 200: retried as NetworkError → fallback ok",
          gw.last_provider == "anthropic" and c.text == ANSWER
          and gw.last_attempts == 4 and len(led.failures()) == 3)
    check("malformed: failure names the parse error",
          all("malformed" in f.error or "JSON" in f.error
              for f in led.failures()))
    return led


def case_sse_cut():
    reset(openai=["cut"], anthropic=["ok"])
    gw, led = make_gateway()
    chunks, err = [], None
    try:
        for ch in gw.stream(MSG):
            chunks.append(ch)
    except NetworkError as e:
        err = e
    text = "".join(c.get("delta", "") for c in chunks)
    check("SSE mid-cut: partial text delivered", text == "Saleor is ", repr(text))
    check("SSE mid-cut: NetworkError raised", err is not None
          and "before [DONE]" in str(err))
    check("SSE mid-cut: 1 failure recorded", len(led.failures()) == 1)
    return led


def case_budget():
    reset(openai=["500", "500", "500"], anthropic=["ok"])
    gw, led = make_gateway(max_attempts=3, budget=0.02)
    try:
        gw.chat(MSG)
        check("total budget: raised", False)
    except ProviderError as e:
        check("total budget: ProviderError", isinstance(e, ProviderTimeout))
        check("total budget: message names budget", "budget" in str(e))
    check("total budget: failure recorded", len(led.failures()) >= 1
          and any("budget" in f.error for f in led.failures()))
    return led


def case_from_env():
    os.environ["LLM_PROVIDERS"] = "openai:gpt-4o-mini,anthropic:claude-3-5-haiku-latest"
    os.environ["OPENAI_API_KEY"] = "sk-test"
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test"
    os.environ["OPENAI_BASE_URL"] = OPENAI_URL
    os.environ["ANTHROPIC_BASE_URL"] = ANTHROPIC_URL
    os.environ["LLM_MAX_ATTEMPTS"] = "3"
    os.environ["LLM_TIMEOUT_S"] = "5"
    os.environ["LLM_BASE_DELAY_S"] = "0.05"
    os.environ["LLM_MAX_DELAY_S"] = "0.5"
    from ai_os.config import build_gateway_from_env
    gw = build_gateway_from_env()
    reset(openai=["ok"], anthropic=["ok"])
    c = gw.chat(MSG)
    check("config from env: gateway built + chat ok",
          c.text == ANSWER and [p.name for p in gw.providers]
          == ["openai", "anthropic"])
    return gw.ledger


# --------------------------------------------------------------------- footer

def economics() -> None:
    print("\n== Day 4 token economics (all calls, failures included)")
    for led in ALL_LEDGERS:
        s = led.summary()
        print(f"  records={s['records']:<3} ok={s['ok']:<3} "
              f"failed={s['failed']:<3} tokens={s['tokens']:<5} "
              f"cost=${s['cost_usd']:.8f}  {s['by_model']}")


def _stub_up() -> bool:
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:8700/admin/state", timeout=1):
            return True
    except Exception:
        return False


def main() -> None:
    proc = None
    if not _stub_up():
        proc = subprocess.Popen([sys.executable, str(STUB)],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        for _ in range(50):
            if _stub_up():
                break
            time.sleep(0.1)
        assert _stub_up(), "stub did not come up"

    try:
        reset(openai=["ok"], anthropic=["ok"])
        run("1. chat 200 ok + usage + cost", case_ok)
        run("2. stream 200 ok + ttft + usage", case_stream_ok)
        run("3. 429 then 200 (honors Retry-After)", case_retry_after)
        run("4. 500, 500, 200 (exponential backoff)", case_backoff_growth)
        run("5. 500 x5, max 3 (retry cap → fallback)", case_retry_cap_then_fallback)
        run("6. 401 (never retried)", case_401_no_retry)
        run("7. 401 on primary → fallback", case_401_fallback)
        run("8. 400 (never retried)", case_400_no_retry)
        run("9. timeout x3 → fallback", case_timeout_fallback)
        run("10. malformed JSON in 200 → fallback", case_malformed)
        run("11. mid-SSE cut: partial then NetworkError", case_sse_cut)
        run("12. total budget spans whole chain", case_budget)
        run("13. build_gateway_from_env() end-to-end", case_from_env)
    finally:
        if proc is not None:
            proc.terminate()

    economics()
    print("\n" + ("ALL CHECKS PASSED" if not FAILS else f"{FAILS} CHECK(S) FAILED"))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()