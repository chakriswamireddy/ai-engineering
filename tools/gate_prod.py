#!/usr/bin/env python3
"""WS0 — production gate: green baselines + no unowned PENDING.

Runs (all must exit 0):
  1. ``python tools/regress_all.py --fast``
  2. ``python tools/demo_smoke.py --regress`` (includes Z8 pending-owner check)
  3. ``python -m pytest -q`` (pyproject scoping excludes corpus/corpus_code/data)

Then audits PENDING ownership (fails on any PENDING without owner):
  A. ``ai_os/scenarios.yaml`` — every ``pending`` entry must carry an owner
     signal: ``owner:`` (case-insensitive) or a ``(...)`` parenthetical naming
     the owning day/workstream (e.g. ``(day20 L2)``, ``(owner: ...)``).
  B. ``docs/deployment.md`` residual register — every table row must have a
     non-empty owner cell (``--``, ``-`` and empty count as missing).

Exit 0 only when every gate and every audit passes. Output is a short
per-check table plus the first failure's detail.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = ROOT / "ai_os" / "scenarios.yaml"
DEPLOYMENT_MD = ROOT / "docs" / "deployment.md"

CHECKS: list[tuple[str, bool, str]] = []


def record(label: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((label, ok, detail))
    print(f"[{'OK  ' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail and not ok else ""))


def run_gate(label: str, cmd: list[str]) -> bool:
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    ok = proc.returncode == 0
    tail = ""
    if not ok:
        out = (proc.stdout + "\n" + proc.stderr).strip().splitlines()
        fails = [line.strip() for line in out if "[FAIL]" in line or "FAILED" in line or "ERROR" in line]
        tail = "; ".join(fails[:4]) or (out[-1].strip() if out else f"exit {proc.returncode}")
    record(label, ok, tail)
    return ok


def audit_scenarios_pending() -> bool:
    """Every pending entry in scenarios.yaml names its owner."""
    try:
        import yaml  # noqa: E402
    except ImportError as e:
        record("PENDING audit: scenarios.yaml", False, f"pyyaml missing: {e}")
        return False
    try:
        with SCENARIOS.open() as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        record("PENDING audit: scenarios.yaml", False, f"missing {SCENARIOS}")
        return False
    bad: list[str] = []
    for sc in data.get("scenarios", []):
        for p in sc.get("pending", []) or []:
            owned = "owner:" in p.lower() or ("(" in p and ")" in p)
            if not owned:
                bad.append(f"{sc.get('id')}: {p!r}")
    record("PENDING audit: scenarios.yaml", not bad,
           f"{len(bad)} unowned: {bad[0]}" if bad else "")
    return not bad


def audit_residual_register() -> bool:
    """Every residual-register row in docs/deployment.md has an owner."""
    try:
        text = DEPLOYMENT_MD.read_text()
    except FileNotFoundError:
        record("PENDING audit: residual register", False, f"missing {DEPLOYMENT_MD}")
        return False
    m = re.search(r"## Residual Register.*?\n((?:\|.*\n)+)", text, re.DOTALL)
    if not m:
        record("PENDING audit: residual register", False, "table not found")
        return False
    rows = [line for line in m.group(1).splitlines() if line.startswith("|")]
    data_rows = [row for row in rows if "---" not in row][1:]  # skip header
    bad: list[str] = []
    for row in data_rows:
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        owner = cells[2] if len(cells) >= 3 else ""
        if owner in ("", "-", "--", "—", "TBD", "tbd"):
            bad.append(cells[0] if cells else row)
    record("PENDING audit: residual register", not bad,
           f"{len(bad)} unowned: {bad[0]}" if bad else "")
    return not bad


def main() -> int:
    py = sys.executable
    ok = True
    ok &= run_gate("regress_all --fast", [py, "tools/regress_all.py", "--fast"])
    ok &= run_gate("demo_smoke --regress", [py, "tools/demo_smoke.py", "--regress"])
    ok &= run_gate("pytest -q", [py, "-m", "pytest", "-q"])
    ok &= audit_scenarios_pending()
    ok &= audit_residual_register()
    passed = sum(1 for _, o, _ in CHECKS if o)
    print(f"\nGATE: {passed}/{len(CHECKS)} checks green — "
          + ("PROD-GATE GREEN" if ok else "PROD-GATE RED (fix rows above)"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
