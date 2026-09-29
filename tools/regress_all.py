"""Day 20 §6 — Rule 2's ratchet as a command: the no-regression gate.

The series has never allowed "better" to mean "different": Day 8 shipped a
retriever that had to keep the seven green rows green while fixing C1; Day 9's
whole report was a per-row delta with a "0 leaks" guard; Day 10 ran *two* corpora
and demanded the doc nine stay 9/9; Days 11–19 each re-ran the prior days'
fixtures unchanged. Day 20 names what that has always been — **regression
testing** — and gives it one command, whose output is a single sentence:

    every row exactly as previously green, or the diff, with owner.

The split is deliberate and must not blur: **improvement is measured on the
golden set** (`ai_os/golden.yaml`, graded by `tools/eval_harness.py`); **preservation
is measured here**. A change that improves a golden row while rolling back an
already-green fixture is a regression, not an improvement — one without the other
is not a Rule-4 result.

Three layers, all `MECH` (0 chats, 0 embeds — the stub stores are embedded offline):

1. **The retrieval fixture families** — `ai_os/retrieval.py`'s question sets
   (the doc nine A1…E2 and the code six CA1…CE1, read through
   `ai_os/fixtures.py`'s loaders) walked through the Day-8/9 retriever at the
   calibrated τ by `tools/retrieval_day10.py --stub`; the gate reads its
   `N/N green` lines, so the check is the day's own arithmetic, not a private
   copy of it.
2. **The day suites, replayed as subprocesses** — `test_day7.py` … `test_day10.py`
   and `tools/{tool,agent,failure,data,sql_attack,plan,state,memory,architecture}_smoke.py`
   (T1–T7 · A1–A8 · F1–F9 · Q1–Q8 · X1–X11 · P1–P9 · S1–S9 · M1–M9 · G1–G9) plus
   each required to exit 0, its failing rows echoed when it does not.
3. **The golden rows** — `tools/eval_harness.py --stub` (the doc nine + code six +
   the judge-hunting F/G/H), required to come back 18/18.

    $ python tools/regress_all.py            # the whole gate
    $ python tools/regress_all.py --fast     # fixtures + golden rows only

Any row a run cannot reproduce is a **gate bug to fix**, never a fixture to
weaken — the same refusal Day 19 made for a topology that failed where the
monolith succeeded.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FAILS = 0

# The day suites, each mapping to the fixture families it owns. The label is the
# row set the suite is the home of; the gate's job is that each still exits 0.
# `(label, script, argv)`.
SUITES: list[tuple[str, str, list[str]]] = [
    ("Day 7 decision tree (MECH)", "test_day7.py", []),
    ("Day 8 retrieval (A–E)", "test_day8.py", []),
    ("Day 9 rerank (A–E)", "test_day9.py", []),
    ("Day 10 doc nine + code six", "test_day10.py", []),
    ("T1–T7 tool calling", "tools/tool_smoke.py", []),
    ("A1–A8 agent loop", "tools/agent_smoke.py", []),
    ("F1–F9 failure modes", "tools/failure_smoke.py", []),
    ("Q1–Q8 data agent", "tools/data_smoke.py", []),
    ("X1–X11 SQL attack set", "tools/sql_attack_smoke.py", []),
    ("P1–P9 planning", "tools/plan_smoke.py", []),
    ("S1–S9 state", "tools/state_smoke.py", []),
    ("M1–M9 memory", "tools/memory_smoke.py", []),
    ("G1–G9 architecture", "tools/architecture_smoke.py", []),
]

# The family replay (Day 8/9 retriever over the fixtures.py question sets) and
# the golden-row replay (the day's own rows through the day's own harness).
RETRIEVAL = ("tools/retrieval_day10.py", ["--stub"])
GOLDEN = ("doc nine + code six + F/G/H (golden rows)", "tools/eval_harness.py", ["--stub"])

ROWS: list[dict] = []


def record(label: str, *, ok: bool, detail: str = "", owner: str = "—") -> None:
    global FAILS
    ROWS.append({"label": label, "ok": ok, "detail": detail, "owner": owner})
    if not ok:
        FAILS += 1


# ----------------------------------------------------- layer 1: the families

_COUNTS_RE = re.compile(r"(\d+)/(\d+) (code|doc) fixtures green")


def replay_fixtures() -> None:
    """The doc nine + code six, walked through the day's own retrieval harness."""
    print("\n== the retrieval fixture families (through tools/retrieval_day10.py)")
    proc = subprocess.run([sys.executable, str(ROOT / RETRIEVAL[0]), *RETRIEVAL[1]],
                          cwd=str(ROOT), capture_output=True, text=True)
    found = _COUNTS_RE.findall(proc.stdout)
    by_kind = {kind: (int(got), int(total)) for got, total, kind in found}
    if not found:
        record("A1–E2 + CA1–CE1 (retrieval fixtures)", ok=False,
               detail="retrieval_day10.py produced no green counts", owner="retrieval")
        print(f"[FAIL] retrieval_day10.py — {_tail(proc.stdout or proc.stderr)}")
        return
    for kind, label, ids in (("doc", "A1–E2 (doc nine)", "A1…E2"),
                             ("code", "CA1–CE1 (code six)", "CA1…CE1")):
        got, total = by_kind.get(kind, (0, 0))
        record(f"{label}", ok=(got == total and total > 0),
               detail=f"{got}/{total} green", owner="retrieval")
        print(f"[{'OK  ' if got == total and total else 'FAIL'}] {label:<32} "
              f"{got}/{total} green")


# ------------------------------------------------- layer 2: the day suites

def _tail(text: str, n: int = 1) -> str:
    for line in reversed(text.splitlines()):
        if line.strip():
            return line.strip()
    return "(no output)"


def _fail_lines(text: str, limit: int = 8) -> list[str]:
    out = []
    for line in text.splitlines():
        if "[FAIL]" in line or "FAILED" in line or "DRIFTED" in line:
            out.append(line.strip())
        if len(out) >= limit:
            break
    return out


def replay_suite(label: str, script: str, argv: list[str] | None = None) -> None:
    """Run one suite as a subprocess; green iff it exits 0 (its own checks)."""
    proc = subprocess.run([sys.executable, str(ROOT / script), *(argv or [])],
                          cwd=str(ROOT), capture_output=True, text=True)
    ok = proc.returncode == 0
    tail = _tail(proc.stdout)
    record(label, ok=ok, detail=f"{script} {tail}", owner="regression")
    print(f"[{'OK  ' if ok else 'FAIL'}] {label:<32} {script:<28} {tail}")
    if not ok:
        for line in _fail_lines(proc.stdout + "\n" + proc.stderr):
            print(f"        {line}")


def replay_golden() -> None:
    """The golden rows through the day's harness — must come back all green."""
    label, script, argv = GOLDEN
    replay_suite(label, script, argv)


# ---------------------------------------------------------------- reporting

def render() -> None:
    print("\n" + "=" * 108)
    print("   DAY 20 — THE REGRESSION GATE (§6; improvement on the golden set, "
          "preservation here)")
    print("=" * 108)
    print(f"{'family / row':<46}{'status':<8}{'owner':<14}{'detail':<38}")
    print("-" * 108)
    for r in ROWS:
        print(f"{r['label']:<46}{'green' if r['ok'] else 'MOVED':<8}"
              f"{r['owner']:<14}{str(r['detail'])[:38]:<38}")
    print("-" * 108)
    moved = [r["label"] for r in ROWS if not r["ok"]]
    print(f"rows: {len(ROWS)}   moved: {len(moved)}")
    print("Verdict: every row exactly as previously green" if not moved
          else f"REGRESSION(S) TO FIX (never a fixture to weaken): {moved}")
    print("Cost: the gate is 0 chats, 0 embeds — the stub stores are embedded "
          "offline; the suites are deterministic.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fast", action="store_true",
                    help="fixtures + golden rows only (skip the day-suite replay)")
    args = ap.parse_args()

    replay_fixtures()
    replay_golden()
    if not args.fast:
        print("\n== the day suites, replayed unchanged (Days 7–21)")
        for label, script, argv in SUITES:
            replay_suite(label, script, argv)

    render()
    print("\n" + ("REGION GREEN — ALL ROWS EXACTLY AS BEFORE" if not FAILS
                  else f"{FAILS} ROW(S) MOVED"))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
