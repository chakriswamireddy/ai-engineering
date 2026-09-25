"""Day 13 §7/§8 — the failure question set (F1–F9) against the scripted stub.

No API key, no real network, no real sleeping. Reuses Day 11's offline registry
(the Day-7 mini doc corpus + the Day-10 code tree) so retrieval stays
byte-identical to the harness Days 7–12 grade, scripts the *chat* wire turn by
turn, and injects the two things Day 13 owns that a script cannot reach:

  - a **broken/flaky retriever** (a tool that raises) to exercise the error
    taxonomy and the fallback ladder, and
  - an **injected clock** (a deterministic wall) to exercise the timeout wall
    without spending real seconds.

    $ python tools/failure_smoke.py

`MECH` here: the failure taxonomy (does every mode get a typed result and a
`failure_kind`?), the timeout wall (does `elapsed_ms` prove it, on the exposed
clock?), the fingerprint (does repeat/oscillation fire *before* the cap?), the
fallback ladder (retry → sibling → degrade/stop, bounded and disclosed?), the
contradiction flag (does it fire on conflicting values and *not* on Day 10's
split-brain?), and `fabricated? = no` on every row. `PENDING`: whether a *real*
model ever trips F1/F5 or contradicts itself — Day 21's judgment on these
guards; the stub proves the guards *fire*.

The no-regression gate re-runs Day 11's `tools/tool_smoke.py` (T1…T7) and Day
12's `tools/agent_smoke.py` (A1…A8) as-is, in subprocesses, so "the guards are
*around* the loop" is asserted by re-running both untouched suites, not by
trusting a copy of them.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STUB = Path(__file__).resolve().parent / "mock_gateway.py"
TOOL_SMOKE = Path(__file__).resolve().parent / "tool_smoke.py"
AGENT_SMOKE = Path(__file__).resolve().parent / "agent_smoke.py"
sys.path.insert(0, str(ROOT))

from ai_os.agent import execute_bounded, run_agent  # noqa: E402
from ai_os.tools import (  # noqa: E402
    ToolResult,
    build_default_registry,
    code_retriever,
    doc_retriever,
)

from tools.tool_smoke import (  # noqa: E402
    DOC_TAU,
    CODE_TAU,
    _stub_up,
    build_registry,
    make_gateway,
    reset,
)

FAILS = 0
ROWS: list[dict] = []


def check(label: str, cond: bool, detail: str = ""):
    global FAILS
    if not cond:
        FAILS += 1
    print(f"[{'OK  ' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


def record(fid: str, mode: str, injected: str, res: dict, *, owner: str,
           guard: str, recovered: bool | None = None):
    ROWS.append({
        "id": fid, "mode": mode, "injected": injected,
        "detected": res.get("failure_kind") is not None or bool(res.get("contradiction")),
        "failure_kind": res.get("failure_kind"),
        "guard": guard, "owner": owner,
        "recovered": recovered, "fallback": bool(res.get("fallback_used")),
        "fabricated": bool(res.get("fabricated")),
        "termination": res.get("termination_reason"),
        "elapsed_ms": max((c.get("elapsed_ms") or 0.0)
                          for s in res.get("steps", []) for c in s.get("calls", [])),
        "cost_usd": res.get("ledger", {}).get("cost_usd", 0.0),
    })


# ----------------------------------------------------- injected tool failures

class FlakyRetriever:
    """A retriever that raises for its first `fail_times` calls, then works.

    The tool *ran and broke* — the third error family Day 13 names — so the
    registry turns the raise into a typed, retryable `tool_error` the ladder
    owns, instead of an exception the loop must catch.
    """

    def __init__(self, inner, fail_times: int = 1):
        self.inner = inner
        self.fail_times = fail_times
        self.calls = 0

    def retrieve(self, query, *, k=4, filter=None):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("downstream resolver 503")
        return self.inner.retrieve(query, k=k, filter=filter)


class AlwaysBrokenRetriever:
    def __init__(self) -> None:
        self.calls = 0

    def retrieve(self, query, *, k=4, filter=None):
        self.calls += 1
        raise RuntimeError("downstream resolver unavailable")


class FakeClock:
    """A deterministic wall: `first` on the first reading, `then` thereafter.

    `execute_bounded` reads the clock twice per call (before/after). With
    `first=0`, `then=1000`, the first call measures a 1000 s wall (a timeout)
    and the next measures zero (a success) — so the wall is *proven* on an
    injected clock, with no real sleeping and full determinism.
    """

    def __init__(self, first: float = 0.0, then: float = 1000.0):
        self.first = first
        self.then = then
        self.reads = 0

    def __call__(self) -> float:
        self.reads += 1
        return self.first if self.reads == 1 else self.then


# ---------------------------------------------------- the claim_probe fixture

def claim_probe_schema() -> dict:
    return {
        "name": "claim_probe",
        "description": ("Fixture tool: return one structured claim (field=value) "
                        "attributed to a subject, so the contradiction detector "
                        "has values to compare."),
        "parameters": {
            "type": "object",
            "properties": {
                "field": {"type": "string"},
                "value": {"type": "boolean"},
                "subject": {"type": "string"},
            },
            "required": ["field", "value", "subject"],
        },
    }


def build_claim_registry(reg_base):
    """The default registry + a fixture tool that carries `claims`."""
    def claim_probe(args: dict) -> ToolResult:
        field, value, subject = args["field"], args["value"], args["subject"]
        return ToolResult.success("claim_probe", args=args, evidence=[{
            "rank": 1, "score": 1.0, "doc_id": subject,
            "chunk_id": f"{subject}#claim", "heading": field,
            "chars": len(str(value)), "text": f"{field}={value}",
            "claims": {field: value},
        }])
    reg_base.register("claim_probe", claim_probe_schema(), claim_probe)
    return reg_base


# ------------------------------------------------------------------ F1–F9

def f1_wrong_tool(reg, gw):
    print("\n== F1 — wrong tool selection (description owns it, the loop pivots)")
    reset(openai=["tc_d13_docs_miss", "tc_code", "answer_code"])
    res = run_agent("Where is checkout completed (in code)?", reg, gw, max_steps=5)
    record("F1", "wrong tool", "docs tool for a code question", res,
           owner="description", guard="gate → pivot", recovered=True)
    s1, s2 = res["steps"][0], res["steps"][1]
    check("step 1 (the wrong corpus) refused — no answer from the wrong corpus",
          s1["refused"] and s1["retrieved"] == [], str(s1["retrieved"]))
    check("step 2 pivots to the *other* tool and succeeds",
          s2["tool_calls"] == ["search_saleor_code"] and s2["result_ok"],
          str(s2["tool_calls"]))
    check("failure_kind names the miss (tool_miss), not the symptom",
          res["failure_kind"] == "tool_miss", str(res["failure_kind"]))
    check("fabricated? = no", not res["fabricated"] and res["cited_subset_retrieved"],
          str(res["fabricated"]))


def f2_invalid_args(reg, gw):
    print("\n== F2 — invalid arguments (validation owns it, self-correction fixes)")
    reset(openai=["tc_bad_kind", "tc_d12_fix_step2", "answer_code"])
    res = run_agent("Where is checkout completed?", reg, gw, max_steps=5, self_correct=2)
    record("F2", "invalid args", "kind='banana' (enum)", res,
           owner="validation", guard="validate_args + self-correct", recovered=True)
    s1, s2 = res["steps"][0], res["steps"][1]
    check("step 1 rejected, message names the field", "kind" in (s1["calls"][0]["error"] or ""),
          str(s1["calls"][0].get("error")))
    check("the rejection is typed invalid_args (a rejection, not a tool error)",
          s1["calls"][0]["error_kind"] == "invalid_args"
          and s1["calls"][0]["result_ok"] is False, str(s1["calls"][0].get("error_kind")))
    check("step 2 re-issues fixed args and succeeds (A2 stays green)",
          s2["result_ok"] and json.loads(s1["calls"][0]["arguments"])
          != json.loads(s2["calls"][0]["arguments"]))
    check("failure_kind names the disease (invalid_args)", res["failure_kind"] == "invalid_args",
          str(res["failure_kind"]))
    check("fabricated? = no", not res["fabricated"])


def f3_repeat(reg, gw):
    print("\n== F3 — infinite loop: the identical call re-emitted (before the cap)")
    reset(openai=["tc_d13_repeat", "tc_d13_repeat", "answer_noprogress"])
    res = run_agent("Where is checkout completed?", reg, gw, max_steps=6)
    record("F3", "infinite loop", "identical call repeated", res,
           owner="loop state", guard="fingerprint (no_progress)", recovered=False)
    check("the repeat detector fired (no_progress)", res["failure_kind"] == "no_progress",
          str(res["failure_kind"]))
    check("it fired at step 2 — before max_steps=6 (a saving, not a cap)",
          res["no_progress_at_step"] == 2 and res["tool_steps"] == 2,
          f"at={res['no_progress_at_step']} steps={res['tool_steps']}")
    check("termination_reason names the disease, not max_steps",
          res["termination_reason"] == "no_progress", str(res["termination_reason"]))
    check("distinct_step_fingerprints = 1 (the same call, twice)",
          res["distinct_step_fingerprints"] == 1, str(res["distinct_step_fingerprints"]))
    check("the fingerprint cost 0 calls (arithmetic over strings)",
          res["ledger"]["records"] == res["tool_steps"] + 1,
          f"records={res['ledger']['records']} steps={res['tool_steps']}")
    check("fabricated? = no", not res["fabricated"])


def f4_oscillation(reg, gw):
    print("\n== F4 — oscillation: A,B,A,B, every step legal, the sequence is not")
    reset(openai=["tc_d13_osc_a", "tc_d13_osc_b", "tc_d13_osc_a", "tc_d13_osc_b",
                  "answer_oscillation"])
    res = run_agent("Where is checkout completed?", reg, gw, max_steps=6, repeat_window=4)
    record("F4", "oscillation", "A,B,A,B", res,
           owner="loop state", guard="fingerprint (oscillation)", recovered=False)
    check("the cycle detector fired (oscillation)",
          res["failure_kind"] == "oscillation" and res["oscillation_detected"],
          str(res["failure_kind"]))
    check("it fired within the window (step 4) — before max_steps=6",
          res["tool_steps"] == 4, str(res["tool_steps"]))
    check("no repeat fired (adjacent calls differ — the detectors are distinct)",
          res["no_progress_at_step"] is None)
    check("distinct_step_fingerprints = 2 (the two legal calls)",
          res["distinct_step_fingerprints"] == 2, str(res["distinct_step_fingerprints"]))
    check("fabricated? = no", not res["fabricated"])


def f5_missing_info(reg, gw):
    print("\n== F5 — missing information (the gate owns it; refusal is the answer)")
    reset(openai=["tc_code_ooc", "tc_d13_docs_miss", "answer_refusal_restated"])
    res = run_agent("Debug the Shopify webhook handler", reg, gw, max_steps=5)
    record("F5", "missing information", "all tools refuse", res,
           owner="gate", guard="refusal-as-value + one pivot", recovered=False)
    check("every executed call refused (both corpora)", res["refused"],
          str(res["tool_ledger"]))
    check("failure_kind = refused", res["failure_kind"] == "refused",
          str(res["failure_kind"]))
    check("zero evidence was manufactured", res["evidence"] == [], str(res["evidence"]))
    check("the final answer is the gate's 'not covered' (restated, flagged)",
          res["refusal_restated"], repr(res["answer"])[:70])
    check("fabricated? = no", not res["fabricated"])


def f6_contradiction(reg, gw):
    print("\n== F6 — contradictory results (flagged, not adjudicated)")
    reset(openai=["tc_d13_contra_step1", "tc_d13_contra_step2", "answer_contradiction"])
    res = run_agent("Does the checkout create the order?", reg, gw, max_steps=5)
    record("F6", "contradiction", "two conflicting claim values", res,
           owner="reconciliation", guard="trace flag + cite both", recovered=None)
    rows = res["contradiction"]
    check("the trace flagged a contradiction", len(rows) == 1 and rows[0]["contradiction"],
          str(rows))
    check("it names the conflicting field and both values",
          bool(rows) and rows[0]["field"] == "order_created_by_checkout"
          and set(rows[0]["values"]) == {True, False}, str(rows))
    check("the answer cites BOTH contentious observations (completeness clause)",
          res["contradiction_covered"], str(res["citations"]))
    check("failure_kind = contradiction", res["failure_kind"] == "contradiction",
          str(res["failure_kind"]))
    check("fabricated? = no", not res["fabricated"])

    # Negative twin (a): two sources that AGREE must not fire.
    reset(openai=["tc_d13_contra_step1", "tc_d13_contra_agree", "answer_code"])
    res2 = run_agent("Does the checkout create the order?", reg, gw, max_steps=5)
    check("no false positive when two sources agree",
          res2["contradiction"] == [] and res2["contradiction_covered"],
          str(res2["contradiction"]))

    # Negative twin (b): Day 10's split-brain — two correct hits, two layers,
    # overlapping topic, no conflicting claim → the detector must stay silent.
    reset(openai=["tc_d12_step1", "tc_d12_step2", "answer_multistep"])
    res3 = run_agent("What happens after CheckoutComplete submits?", reg, gw, max_steps=5)
    check("no false positive on the split-brain (complementarity, not conflict)",
          res3["contradiction"] == [], str(res3["contradiction"]))

    # The failure mode: an answer that silently picks ONE side is caught.
    reset(openai=["tc_d13_contra_step1", "tc_d13_contra_step2", "answer_code"])
    res4 = run_agent("Does the checkout create the order?", reg, gw, max_steps=5)
    check("an answer citing only one side fails the completeness clause",
          bool(res4["contradiction"]) and not res4["contradiction_covered"],
          str(res4["citations"]))


def f7_unknown_tool(reg, gw):
    print("\n== F7 — hallucinated tool (the registry owns it; no execution)")
    reset(openai=["tc_unknown_tool", "tc_code", "answer_code"])
    res = run_agent("Where is checkout completed?", reg, gw, max_steps=5)
    record("F7", "hallucinated tool", "get_invoice()", res,
           owner="registry", guard="unknown-tool rejection", recovered=True)
    s1 = res["steps"][0]
    check("the unknown tool is a typed rejection (unknown_tool)",
          s1["calls"][0]["error_kind"] == "unknown_tool"
          and "unknown tool" in (s1["calls"][0]["error"] or ""),
          str(s1["calls"][0].get("error")))
    check("no execution was attempted (no evidence for the phantom tool)",
          s1["retrieved"] == [] and s1["calls"][0]["result_ok"] is False,
          str(s1["retrieved"]))
    check("the loop self-corrects (or gave_up) — it did not crash",
          res["termination_reason"] in ("answer", "gave_up"),
          str(res["termination_reason"]))
    check("failure_kind = unknown_tool", res["failure_kind"] == "unknown_tool",
          str(res["failure_kind"]))
    check("fabricated? = no", not res["fabricated"])


def f8_tool_failure(gw, doc_store, code_store):
    print("\n== F8 — tool failure (the executor owns it; retry then fall back)")
    # (a) transient: the tool raises once, the same-arg retry recovers.
    _reg0, gw_doc, gw_code, _ds0, _cs0 = build_registry()
    real_code = code_retriever(gw_code, code_store)
    reg_a = build_default_registry(
        doc=doc_retriever(gw_doc, doc_store), code=FlakyRetriever(real_code, 1),
        doc_tau=DOC_TAU, code_tau=CODE_TAU)
    reset(openai=["tc_code", "answer_code"])
    res = run_agent("Where is checkout completed?", reg_a, gw, max_steps=5)
    record("F8a", "tool failure", "tool raises once", res,
           owner="executor", guard="retry once, same args", recovered=True)
    c0 = res["steps"][0]["calls"][0]
    check("the raise became a typed tool_error observation (never an exception)",
          c0.get("tool_failures", 0) >= 1 and res["observed_failures"] >= 1,
          str(c0.get("error_kind")))
    check("rung 1 retried the SAME call once and recovered",
          c0.get("tool_retries") == 1 and res["termination_reason"] == "answer",
          f"retries={c0.get('tool_retries')} term={res['termination_reason']}")
    check("failure_kind = tool_error", res["failure_kind"] == "tool_error",
          str(res["failure_kind"]))
    check("fabricated? = no", not res["fabricated"])

    # (b) persistent: the retry also fails, so rung 2 substitutes the sibling.
    reg_b = build_default_registry(
        doc=doc_retriever(gw_doc, doc_store), code=AlwaysBrokenRetriever(),
        doc_tau=DOC_TAU, code_tau=CODE_TAU)
    reset(openai=["tc_code", "answer_docs"])
    res2 = run_agent("Where is checkout completed?", reg_b, gw, max_steps=5)
    record("F8b", "tool failure", "tool always raises", res2,
           owner="executor", guard="retry → sibling (disclosed)", recovered=True)
    c0b = res2["steps"][0]["calls"][0]
    check("rung 2 fell back to the sibling tool and disclosed it",
          c0b.get("fallback_from") == "search_saleor_code"
          and bool(res2["fallback_used"]),
          str(res2["fallback_used"]))
    check("the fallback evidence is the docs store (the substitution is real)",
          res2["steps"][0]["result_ok"] and bool(res2["steps"][0]["retrieved"]),
          str(res2["steps"][0]["tool_calls"]))
    check("failure_kind = tool_error; the run survived and answered",
          res2["failure_kind"] == "tool_error"
          and res2["termination_reason"] == "answer", str(res2["failure_kind"]))
    check("fabricated? = no", not res2["fabricated"])

    # (c) ladder exhausted: both corpora down → stop honestly.
    reg_c = build_default_registry(
        doc=AlwaysBrokenRetriever(), code=AlwaysBrokenRetriever(),
        doc_tau=DOC_TAU, code_tau=CODE_TAU)
    reset(openai=["tc_code", "answer_tool_failure"])
    res3 = run_agent("Where is checkout completed?", reg_c, gw, max_steps=5)
    record("F8c", "tool failure", "both corpora down", res3,
           owner="executor", guard="ladder exhausted → stop", recovered=False)
    check("termination_reason = tool_failure (rung 4: stop honestly)",
          res3["termination_reason"] == "tool_failure", str(res3["termination_reason"]))
    check("the original + retry + fallback attempts are all counted",
          res3["observed_failures"] >= 3, str(res3["observed_failures"]))
    check("failure_kind = tool_error", res3["failure_kind"] == "tool_error",
          str(res3["failure_kind"]))
    check("the answer names the failure and cites only real evidence",
          "failed" in (res3["answer"] or "").lower() and not res3["fabricated"],
          repr(res3["answer"])[:70])


def f9_timeout(reg, gw):
    print("\n== F9 — timeout (the wall at the tool-call boundary, injected clock)")
    reset(openai=["tc_code", "answer_code"])
    clock = FakeClock(first=0.0, then=1000.0)
    res = run_agent("Where is checkout completed?", reg, gw, max_steps=5,
                    tool_timeout_s=5.0, clock=clock)
    record("F9", "timeout", "hanging tool (fake clock +1000s)", res,
           owner="executor", guard="execute_bounded wall", recovered=True)
    c0 = res["steps"][0]["calls"][0]
    check("the timeout fired as a typed observation (not a crash)",
          c0.get("timeouts") == 1 and res["tool_timeouts"] == 1,
          f"timeouts={res['tool_timeouts']}")
    check("elapsed_ms proves the wall held (>= tool_timeout_s * 1000)",
          (c0.get("elapsed_ms") or 0) >= 5000, str(c0.get("elapsed_ms")))
    check("the run survived and recovered via the same-arg retry",
          res["termination_reason"] == "answer" and c0.get("tool_retries") == 1,
          f"term={res['termination_reason']} retries={c0.get('tool_retries')}")
    check("failure_kind = timeout", res["failure_kind"] == "timeout",
          str(res["failure_kind"]))
    check("fabricated? = no", not res["fabricated"])

    # The wall itself, in isolation: a fast call under a generous wall is not a
    # timeout, and the same call under a tight wall is — the comparison is the
    # clock's, deterministic and real.
    r_fast, ms_fast = execute_bounded(reg, "get_order", {"id": "1"},
                                      tool_timeout_s=5.0, clock=FakeClock(0.0, 0.001))
    check("a 1ms call under a 5s wall is not a timeout",
          r_fast.ok and ms_fast < 5000, f"ms={ms_fast:.3f}")


# ---------------------------------------------------------------- regression

def no_regression():
    print("\n== Day 13 — no regression: A1…A8 and T1…T7 ride the guarded loop")
    for name, script in (("tools/agent_smoke.py", AGENT_SMOKE),
                         ("tools/tool_smoke.py", TOOL_SMOKE)):
        proc = subprocess.run([sys.executable, str(script)], cwd=str(ROOT),
                              capture_output=True, text=True)
        tail = ""
        for line in reversed(proc.stdout.splitlines()):
            if line.strip():
                tail = line.strip()
                break
        check(f"{name} still exits 0", proc.returncode == 0, tail)
        if proc.returncode != 0:
            print(proc.stdout[-2000:])


# ----------------------------------------------------------------- reporting

def render():
    print("\n" + "=" * 118)
    print("   DAY 13 — FAILURES AS A CONTRACT (§8: the fabricated? column never varies)")
    print("=" * 118)
    print(f"{'id':<6}{'mode':<20}{'failure_kind':<16}{'guard':<34}{'owner':<15}"
          f"{'recov':<7}{'fallb':<7}{'fab?':<6}{'term':<13}elapsed_ms")
    print("-" * 118)
    for r in ROWS:
        rec = "yes" if r["recovered"] else ("—" if r["recovered"] is None else "no")
        print(f"{r['id']:<6}{r['mode']:<20}{str(r['failure_kind']):<16}"
              f"{r['guard']:<34}{r['owner']:<15}{rec:<7}"
              f"{'yes' if r['fallback'] else '—':<7}"
              f"{'YES!' if r['fabricated'] else 'no':<6}{str(r['termination']):<13}"
              f"{r['elapsed_ms']:.0f}")
    print("-" * 118)
    fabrications = [r["id"] for r in ROWS if r["fabricated"]]
    print(f"fabricated? = no on every row: "
          f"{'YES' if not fabrications else 'NO — ' + ', '.join(fabrications)}")
    print("`failure_kind` is the taxonomy's payoff: the trace names the disease "
          "(no_progress / oscillation / timeout / tool_error / refused / "
          "tool_miss / unknown_tool / invalid_args / contradiction), not max_steps.")
    print("The fingerprint costs 0 calls; a timeout costs 0 calls (a wall, not a "
          "bill); the fallback ladder costs calls when it descends.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.parse_args()

    proc = None
    if not _stub_up():
        proc = subprocess.Popen([sys.executable, str(STUB)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            if _stub_up():
                break
            time.sleep(0.1)
        assert _stub_up(), "stub did not come up"

    try:
        reg, gw_doc, gw_code, doc_store, code_store = build_registry()
        gw = make_gateway()
        claim_reg = build_claim_registry(build_registry()[0])

        print(f"stores: {len(doc_store)} doc chunks (STUB) / {len(code_store)} "
              f"code symbols (STUB)   τ_doc={DOC_TAU} τ_code={CODE_TAU}")

        f1_wrong_tool(reg, gw)
        f2_invalid_args(reg, gw)
        f3_repeat(reg, gw)
        f4_oscillation(reg, gw)
        f5_missing_info(reg, gw)
        f6_contradiction(claim_reg, gw)
        f7_unknown_tool(reg, gw)
        f8_tool_failure(gw, doc_store, code_store)
        f9_timeout(reg, gw)
        no_regression()
    finally:
        if proc is not None:
            proc.terminate()

    render()
    print("\n" + ("ALL DAY 13 (MECH) CHECKS PASSED" if not FAILS
                  else f"{FAILS} CHECK(S) FAILED"))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
