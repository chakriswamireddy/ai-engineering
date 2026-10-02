#!/usr/bin/env python3
"""WS8 — test execution sandbox fixture (B1–B6), offline.

- B1 honest pass/fail: a generated green suite passes, a red suite fails
- B2 destructive payload refused before execution (rm -rf / never runs)
- B3 exfil gets nothing: secrets never cross the boundary (sentinel absent)
- B4 infinite loop is killed by the timeout (no hang)
- B5 huge output is capped (excerpt bounded, sha names the full log)
- B6 shape gate: absolute/.. targets and over-long args refused, not truncated
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_os import sandbox as sb  # noqa: E402

FAILS = 0


def check(label: str, cond: bool, detail: str = "") -> bool:
    global FAILS
    if not cond:
        FAILS += 1
    print(f"[{'OK  ' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))
    return bool(cond)


def _write(path: Path, text: str) -> str:
    path.write_text(text, encoding="utf-8")
    return path.name


def b1_honest(tmp: Path) -> None:
    ok = _write(tmp / "test_ws8_ok.py", "def test_green():\n    assert 1 + 1 == 2\n")
    out = sb.run_tests(ok, repo=tmp, timeout_s=60)
    check("B1 green suite passes with measured result",
          out["passed"] and out["outcome"] == "pass" and out["log_sha256"], out["outcome"])
    bad = _write(tmp / "test_ws8_bad.py", "def test_red():\n    assert 1 + 1 == 3\n")
    out2 = sb.run_tests(bad, repo=tmp, timeout_s=60)
    check("B1 red suite fails (no false pass)",
          not out2["passed"] and out2["outcome"] == "fail", out2["outcome"])


def b2_destructive_refused(tmp: Path) -> None:
    evil = _write(tmp / "test_ws8_evil.py",
                  "import os\ndef test_evil():\n    os.system('rm -rf /')\n    assert True\n")
    out = sb.run_tests(evil, repo=tmp, timeout_s=60)
    check("B2 rm -rf / refused before execution (never runs)",
          out["outcome"] == "refused" and out["backend"] == "none",
          str(out.get("refusal"))[:80])
    check("B2 repo intact after refusal", (ROOT / "policy.yaml").exists())


def b3_exfil_empty(tmp: Path) -> None:
    spy = _write(tmp / "test_ws8_spy.py",
                 "import os\ndef test_spy():\n    print('KEYS=' + ','.join(sorted(os.environ)))\n"
                 "    assert True\n")
    os.environ["WS8_SENTINEL_SECRET_KEY"] = "s3cr3t-sentinel"
    try:
        out = sb.run_tests(spy, repo=tmp, timeout_s=60)
    finally:
        del os.environ["WS8_SENTINEL_SECRET_KEY"]
    blob = (out.get("log_excerpt") or "")
    check("B3 secrets never cross the boundary (sentinel absent)",
          "s3cr3t-sentinel" not in blob and "WS8_SENTINEL" not in blob,
          f"backend={out.get('backend')}")


def b4_timeout(tmp: Path) -> None:
    loop = _write(tmp / "test_ws8_loop.py",
                  "import time\ndef test_loop():\n    time.sleep(30)\n")
    out = sb.run_tests(loop, repo=tmp, timeout_s=5)
    check("B4 infinite loop killed by the timeout (no hang)",
          out["timed_out"] and out["outcome"] == "timeout",
          f"{out['outcome']} {out.get('wall_ms')}ms")


def b5_log_cap(tmp: Path) -> None:
    noisy = _write(tmp / "test_ws8_noisy.py",
                   "def test_noisy():\n    print('x' * 100000)\n    assert True\n")
    out = sb.run_tests(noisy, repo=tmp, timeout_s=60)
    check("B5 huge output capped (excerpt bounded, sha names full log)",
          len(out.get("log_excerpt") or "") <= sb.LOG_EXCERPT_CHARS
          and len(out.get("log_sha256") or "") == 64,
          f"excerpt={len(out.get('log_excerpt') or '')}")


def b6_shape_gate(tmp: Path) -> None:
    out = sb.run_tests("/etc/passwd", repo=tmp)
    check("B6 absolute target refused", out["outcome"] == "refused")
    out2 = sb.run_tests("../outside.py", repo=tmp)
    check("B6 parent-escape refused", out2["outcome"] == "refused")
    out3 = sb.run_tests("x" * 3000, repo=tmp)
    check("B6 over-long args refused, never truncated", out3["outcome"] == "refused")


def main() -> int:
    print("== WS8 — test execution sandbox (B1–B6) ==")
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        b1_honest(tmp)
        b2_destructive_refused(tmp)
        b3_exfil_empty(tmp)
        b4_timeout(tmp)
        b5_log_cap(tmp)
        b6_shape_gate(tmp)
    print("\n" + ("ALL SANDBOX CHECKS PASSED" if not FAILS else f"{FAILS} CHECK(S) FAILED"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
