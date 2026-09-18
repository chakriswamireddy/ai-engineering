"""End-to-end smoke test of the Day 3 loop against the local stub provider.

No API key, no network. Assumes tools/mock_openai.py is running on 8911.

    $ python tools/mock_openai.py --port 8911 &
    $ OPENAI_API_KEY=sk-test LLM_BASE_URL=http://127.0.0.1:8911/v1 python tools/smoke_loop.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_os import llm_client
from mock_openai import ANSWER

MSG = [{"role": "user", "content": "What is Saleor?"}]
FAIL = 0


def check(label: str, cond: bool, detail: str = ""):
    global FAIL
    status = "OK  " if cond else "FAIL"
    if not cond:
        FAIL += 1
    print(f"[{status}] {label}" + (f"  {detail}" if detail else ""))


def main():
    resp = llm_client.chat(MSG, temperature=0.0, max_tokens=512)
    check("non-stream: message returned", bool(resp["message"]), repr(resp["message"][:40]))
    check("non-stream: usage has token split", resp["usage"].get("prompt_tokens") == 12
          and resp["usage"].get("completion_tokens") == 24, str(resp["usage"]))
    check("non-stream: latency_ms present", resp["latency_ms"] >= 0)
    check("non-stream: model echoed", resp["model"] == "gpt-4o-mini-stub", resp["model"])

    chunks = list(llm_client.stream(MSG))
    text = "".join(c["delta"] for c in chunks)
    check("stream: assembled text matches", text.strip() == ANSWER, text.strip()[:40])
    check("stream: ttft_ms on first chunk", "ttft_ms" in chunks[0])
    check("stream: last chunk carries usage", bool(chunks[-1]["usage"]))

    saved_key = llm_client.API_KEY
    llm_client.API_KEY = "bogus"
    try:
        llm_client.chat(MSG)
        check("401 surfaces as LLMError", False)
    except llm_client.LLMError as e:
        check("401 surfaces as LLMError", e.status == 401, str(e))
    finally:
        llm_client.API_KEY = saved_key

    print("\n" + ("ALL CHECKS PASSED" if not FAIL else f"{FAIL} CHECK(S) FAILED"))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()