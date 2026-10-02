#!/usr/bin/env python3
"""WS8 — run pytest targets inside the sandbox (CLI).

    $ python tools/run_tests.py test_day7.py --timeout 120
    $ AI_OS_SANDBOX=docker SANDBOX_IMAGE=python:3.12-slim python tools/run_tests.py test_day7.py

Prints `{passed, failed, log_sha256, log_excerpt}` — never the full log.
Exit 0 on pass, 1 on fail/refusal/timeout.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_os.sandbox import run_tests  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("targets", nargs="+", help="pytest targets relative to the repo")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--memory-mb", type=int, default=512)
    ap.add_argument("--backend", default=None,
                    help="subprocess (default) | docker")
    args = ap.parse_args()
    out = run_tests(" ".join(args.targets), repo=ROOT, timeout_s=args.timeout,
                    memory_mb=args.memory_mb, backend=args.backend)
    print(json.dumps({k: out.get(k) for k in
                      ("passed", "failed", "outcome", "returncode", "timed_out",
                       "backend", "wall_ms", "log_sha256")}, indent=2))
    print("--- excerpt (capped) ---")
    print(out.get("log_excerpt", "")[:2000])
    if out.get("refusal"):
        print(f"refused: {out['refusal']}")
    return 0 if out.get("passed") else 1


if __name__ == "__main__":
    sys.exit(main())
