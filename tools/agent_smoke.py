"""Day 12 §7/§8 — the agent question set (A1–A8) against the scripted stub.

No API key, no real network. Reuses Day 11's offline registry (the Day-7 mini
doc corpus + the Day-10 14-symbol code tree) so retrieval stays byte-identical
to the harness Days 7–10 grade, and scripts the *chat* wire turn by turn —
because the loop reads the script at iteration N, not turn 1:

    $ python tools/agent_smoke.py

`MECH` here: the loop (transcript-as-state, the three exits, parallel
observations, self-correction bound), the per-step trace, and grounding over
the transcript (`cited ⊆ ∪ observations`). `PENDING`: whether a *real* model
composes a good multi-step investigation — will it follow an import to a second
query it was never told, and keep citing each step — is Day 21's benchmark on
this harness. The stub proves the loop *carries* N turns; a real key proves the
model *drives* them.

The no-regression gate re-runs Day 11's `tools/tool_smoke.py` (T1…T7) as-is, in
a subprocess, so "run_agent(max_steps=1) is Day 11" is asserted by re-running
the unmodified Day 11 suite, not by trusting a copy of it. Day 7–10's fixture
lines ride behind the tools inside that suite.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STUB = Path(__file__).resolve().parent / "mock_gateway.py"
TOOL_SMOKE = Path(__file__).resolve().parent / "tool_smoke.py"
sys.path.insert(0, str(ROOT))

from ai_os.agent import run_agent, run_tool_answer  # noqa: E402
from ai_os.trace import AGENT_TERMINATIONS  # noqa: E402

import tools.code_debug as _cd  # noqa: E402
import tools.rag_debug as _rd  # noqa: E402
from tools.tool_smoke import build_registry, make_gateway, reset  # noqa: E402

FAILS = 0
STEP_ROWS: list[dict] = []
RUN_ROWS: list[dict] = []


def check(label: str, cond: bool, detail: str = ""):
    global FAILS
    if not cond:
        FAILS += 1
    print(f"[{'OK  ' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


def _stub_up() -> bool:
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:8700/admin/state", timeout=1):
            return True
    except Exception:
        return False


def _code_ids(step) -> set[str]:
    return set(step["retrieved"])


def record_run(name: str, res: dict, expected: str):
    RUN_ROWS.append({
        "id": name, "expected": expected, "termination": res["termination_reason"],
        "steps": res["tool_steps"], "turns": res["turns"],
        "refusals": res["observed_refusals"], "rejections": res["rejections"],
        "truncated": res["truncated"], "cost_usd": res["ledger"]["cost_usd"],
    })


# -------------------------------------------------------------------- A1–A8

def a1_multi_step(reg, gw, code_ids):
    print("\n== A1 — multi-step investigation (walking Day 10's map)")
    reset(openai=["tc_d12_step1", "tc_d12_step2", "answer_multistep"])
    res = run_agent("What happens after CheckoutComplete submits?", reg, gw, max_steps=5)
    record_run("A1", res, "≥2 search_saleor_code steps; cite ≥2 observations")

    check("≥2 tool steps (the loop, not the single turn)", res["tool_steps"] >= 2,
          str(res["tool_steps"]))
    check("the natural exit fired (an answer turn with no tool_calls)",
          res["termination_reason"] == "answer" and not res["truncated"],
          str(res["termination_reason"]))
    step1, step2 = res["steps"][0], res["steps"][1]
    check("step 1 retrieved the mutation (CheckoutComplete)",
          any(i.endswith("#CheckoutComplete") for i in step1["retrieved"]),
          str(step1["retrieved"]))
    check("step 2 retrieved the service it calls (complete_checkout)",
          any(i.endswith("#complete_checkout") for i in step2["retrieved"]),
          str(step2["retrieved"]))
    check("step 2's query names the symbol step 1's evidence carried (caused by step 1)",
          "complete_checkout" in json.loads(step2["calls"][0]["arguments"])["query"],
          step2["calls"][0]["arguments"])
    check("step 2's evidence is a *different* observation, not a replay of step 1",
          _code_ids(step1) != _code_ids(step2))
    check("every evidence id ⊆ the code store (cited ⊆ retrieved, per step)",
          _code_ids(step1) <= code_ids and _code_ids(step2) <= code_ids)
    check("the final answer cites symbols from ≥2 observations",
          bool(step1["cited"]) and bool(step2["cited"]),
          f"step1.cited={step1['cited']} step2.cited={step2['cited']}")
    check("grounded over the transcript (cited ⊆ ∪ observations)",
          res["cited_subset_retrieved"] and not res["fabricated"],
          str(res["fabricated"]))
    check("turns == tool_steps + 1 (one chat per step + one answer turn)",
          res["turns"] == res["tool_steps"] + 1)

    # The map was stored on Day 10; Day 12 walks it. Prove the second step's
    # target is *discoverable in* the first step's evidence (the import line).
    step1_tool = reg.execute("search_saleor_code",
                             {"query": "CheckoutComplete", "kind": "mutation",
                              "module": "saleor/graphql/checkout", "k": 4})
    body = (step1_tool.evidence[0]["text"] if step1_tool.evidence else "")
    check("the map is real: step 1's evidence text names complete_checkout",
          "complete_checkout" in body, body[:60].replace("\n", " "))

    for n, s in enumerate(res["steps"], start=1):
        STEP_ROWS.append({
            "run": "A1", "step": n, "expected": ["search_saleor_code"] if n <= 2 else ["answer"],
            "chosen": ", ".join(s["tool_calls"]), "args_valid": s["args_valid"],
            "result_ok": s["result_ok"], "refused": s["refused"],
            "cum_steps": n, "cost_usd": res["ledger"]["cost_usd"],
        })
    return res


def a2_self_correction(reg, gw):
    print("\n== A2 — self-correction (a rejection is a message, re-issued fixed)")
    reset(openai=["tc_d12_fix_step1", "tc_d12_fix_step2", "answer_code"])
    res = run_agent("Where is checkout completed?", reg, gw, max_steps=5)
    record_run("A2", res, "1 rejection + 1 fixed call, then answer")
    s1, s2 = res["steps"][0], res["steps"][1]
    check("step 1's args are rejected (kind enum violation)",
          not s1["calls"][0]["args_valid"] and not s1["calls"][0]["result_ok"],
          str(s1["calls"][0].get("error")))
    check("the rejection names the field", "kind" in (s1["calls"][0]["error"] or ""),
          str(s1["calls"][0].get("error")))
    check("step 2 re-issues with fixed args and succeeds",
          s2["calls"][0]["args_valid"] and s2["calls"][0]["result_ok"],
          str(s2["calls"][0]))
    check("the re-issue is a *different* call (one word fixed, not repeated louder)",
          json.loads(s1["calls"][0]["arguments"]) != json.loads(s2["calls"][0]["arguments"]),
          f"{s1['calls'][0]['arguments']} -> {s2['calls'][0]['arguments']}")
    check("the trace counts 1 rejection + 1 success", res["rejections"] == 1
          and res["observed_refusals"] == 0, f"rej={res['rejections']}")
    check("the loop answered after the fix",
          res["termination_reason"] == "answer" and res["tool_steps"] == 2)


def a3_pivot_after_refusal(reg, gw, doc_ids):
    print("\n== A3 — pivot after refusal (never a retried refusal, never a fabricated symbol)")
    reset(openai=["tc_code_ooc", "tc_d12_pivot_step2", "answer_docs"])
    res = run_agent("What do the docs say about creating a checkout?", reg, gw, max_steps=5)
    record_run("A3", res, "step 1 refused → step 2 pivots to docs")
    s1, s2 = res["steps"][0], res["steps"][1]
    check("step 1 (code tool) refused and manufactured nothing",
          s1["refused"] and s1["calls"][0]["refused"] and not s1["retrieved"],
          str(s1["retrieved"]))
    check("step 2 pivots to the docs tool (not the refused call again)",
          s2["tool_calls"] == ["search_saleor_docs"], str(s2["tool_calls"]))
    check("step 2's evidence ⊆ the doc store", set(s2["retrieved"]) <= doc_ids,
          str(sorted(set(s2["retrieved"]) - doc_ids)))
    check("the refusal is recorded, not retried", res["observed_refusals"] == 1
          and res["tool_steps"] == 2, f"refusals={res['observed_refusals']}")
    check("no fabricated symbol; grounded on the docs it pivoted to",
          res["cited_subset_retrieved"] and not res["fabricated"],
          str(res["fabricated"]))


def a4_parallel_calls(reg, gw):
    print("\n== A4 — parallel calls in one step (each its own observation)")
    reset(openai=["tc_two", "answer_parallel"])
    res = run_agent("Where is checkout handled, and what is order 1?", reg, gw, max_steps=3)
    record_run("A4", res, "two calls, one step, both observed")
    step = res["steps"][0]
    check("one step carried two tool_calls", res["tool_steps"] == 1
          and len(step["calls"]) == 2, str(step["tool_calls"]))
    check("both calls executed (no shortcut that drops the second)",
          step["result_ok"] and all(c["args_valid"] for c in step["calls"]))
    check("both names are in the step row",
          step["tool_calls"] == ["search_saleor_code", "get_order"],
          str(step["tool_calls"]))
    check("both are acknowledged in the answer path (grounded)",
          res["cited_subset_retrieved"] and not res["fabricated"])


def a5_termination(reg, gw, code_ids):
    print("\n== A5 — termination on max_steps (and the budget sibling, exit 3)")
    n = 3
    # The stub keeps asking *past* the cap: n tool turns execute, the (n+1)th is
    # the cap (recorded, not executed), then the forced answer turn. One extra
    # `tc_code` is the model's over-asking turn — proof the cap, not the script,
    # stopped the loop.
    reset(openai=["tc_code"] * (n + 1) + ["answer_limit"])
    res = run_agent("Where is checkout handled?", reg, gw, max_steps=n)
    record_run("A5", res, f"max_steps={n} → truncated, named")
    check("termination_reason == max_steps", res["termination_reason"] == "max_steps",
          str(res["termination_reason"]))
    check("the run is marked truncated", res["truncated"] is True)
    check("exactly max_steps tool steps ran", res["tool_steps"] == n,
          str(res["tool_steps"]))
    check("the final answer names the limit (not a silent confident non-answer)",
          "step limit" in res["answer"] and "max_steps" in res["answer"],
          res["answer"][:60])
    check("turns == max_steps + 1 (one forced final answer turn)",
          res["turns"] == n + 1, str(res["turns"]))
    check("the still-asking model produced no fabricated evidence",
          not res["fabricated"])
    check("the over-asking calls are *recorded, not executed*",
          len(res["capped_tool_calls"]) == 1
          and res["capped_tool_calls"][0]["name"] == "search_saleor_code",
          str(res["capped_tool_calls"]))
    check("no observation was appended for the capped turn (step count stays the cap)",
          res["tool_steps"] == n and len(res["steps"]) == n)

    reset(openai=["tc_code", "answer_budget"])
    res2 = run_agent("Where is checkout handled?", reg, gw, max_steps=5, budget_usd=1e-9)
    record_run("A5b", res2, "budget cap → exit 3")
    check("the budget cap fires exit 3 (money ends the loop visibly)",
          res2["termination_reason"] == "budget" and res2["truncated"],
          str(res2["termination_reason"]))
    check("the budget stop is surfaced, not silent",
          "budget" in res2["answer"].lower(), res2["answer"][:60])
    check("at least one step of evidence was gathered before the cap",
          res2["tool_steps"] >= 1 and set(res2["evidence"]) <= code_ids)


def a6_grounding_transcript(reg, gw):
    print("\n== A6 — grounding across the transcript (the multi-turn invariant)")
    reset(openai=["tc_d12_step1", "tc_d12_step2", "answer_multistep"])
    res = run_agent("What happens after CheckoutComplete submits?", reg, gw, max_steps=5)
    record_run("A6", res, "cited ⊆ ∪(step observations)")
    union = {i for s in res["steps"] for i in s["retrieved"]}
    check("every citation lies in the union of step observations",
          set(res["citations"]) <= union and union,
          str(sorted(set(res["citations"]) - union)))
    check("A6 holds on the positive run", res["cited_subset_retrieved"])

    reset(openai=["tc_d12_step1", "tc_d12_step2", "answer_fabricated"])
    res2 = run_agent("What happens after CheckoutComplete submits?", reg, gw, max_steps=5)
    check("a symbol outside every observation is caught as fabricated",
          not res2["cited_subset_retrieved"]
          and "saleor/ghost/checkout.py#CompleteCheckoutMagic" in res2["fabricated"],
          str(res2["fabricated"]))


def a7_zero_tool(reg, gw):
    print("\n== A7 — zero-tool question (the first answer turn is the answer)")
    reset(openai=["ok"])
    res = run_agent("Summarize checkout in one sentence.", reg, gw, max_steps=3)
    record_run("A7", res, "no tool_calls → tool_steps: 0")
    check("no tool call at all", res["tool_steps"] == 0 and res["tool_calls"] == [])
    check("termination is the natural exit", res["termination_reason"] == "answer")
    check("one turn: the plain chat reply", res["turns"] == 1)
    check("no_tool_call is recorded so the trace says so", res["no_tool_call"] is True)
    check("nothing fabricated against an empty observation set",
          res["cited_subset_retrieved"] and res["evidence"] == [])


def a8_parallel_capacity(reg, gw):
    print("\n== A8 — parallel capacity guard (three calls, none dropped)")
    reset(openai=["tc_three", "answer_parallel"])
    res = run_agent("Checkout, order 1, and the docs please.", reg, gw, max_steps=3)
    record_run("A8", res, "three calls, one step, all observed")
    step = res["steps"][0]
    check("the step carried all three tool_calls", len(step["calls"]) == 3,
          str(step["tool_calls"]))
    check("all three names preserved", step["tool_calls"] ==
          ["search_saleor_code", "get_order", "search_saleor_docs"],
          str(step["tool_calls"]))
    check("all three validated and executed", step["args_valid"] and step["result_ok"])
    check("the flat tool_ledger kept all three (no dropped observation)",
          len(res["tool_ledger"]) == 3, str(len(res["tool_ledger"])))
    check("records from all three tools entered one transcript (mixed corpora)",
          any(i.startswith("saleor/") for i in step["retrieved"])
          and any(not i.startswith("saleor/") for i in step["retrieved"]),
          str(step["retrieved"][:3]))


def ran_off_budget(reg, gw):
    print("\n== Day 12 — self-correction bound (gave_up, the narrower exit)")
    reset(openai=["tc_d12_fix_step1", "tc_d12_fix_step1", "answer_gaveup"])
    res = run_agent("Where is checkout completed?", reg, gw, max_steps=5, self_correct=1)
    record_run("gave_up", res, "self_correct=1 → gave_up")
    check("rejections exceeded the bound → gave_up recorded", res["gave_up"] is True,
          f"rejections={res['rejections']}")
    check("termination_reason names the stuck point",
          res["termination_reason"] == "gave_up", str(res["termination_reason"]))
    check("the answer names the stuck point honestly (no invented fix)",
          "self-correction" in res["answer"].lower(), res["answer"][:60])


def cost_shape(reg, gw, gw_doc, gw_code):
    print("\n== Day 12 — cost: the loop multiplies Day 11's shape by steps")
    reset(openai=["tc_d12_step1", "tc_d12_step2", "answer_multistep"])
    g = make_gateway()
    before_embed = gw_code.embed_calls
    before_chat = gw_code.chat_calls
    res = run_agent("What happens after CheckoutComplete submits?", reg, g, max_steps=5)
    ok_chats = sum(1 for r in g.ledger.records if r.ok)
    embeds = gw_code.embed_calls - before_embed
    check("chats = tool_steps + 1 (per-step tool turn + the answer turn)",
          ok_chats == res["tool_steps"] + 1, f"chats={ok_chats} steps={res['tool_steps']}")
    check("embeds = one per retrieval step (tool execution is free arithmetic)",
          embeds == res["tool_steps"] and gw_code.chat_calls == before_chat,
          f"embeds={embeds}")
    check("the ledger priced every turn (money learns no new shape)",
          sum(r.cost_usd for r in g.ledger.records) > 0.0,
          f"${sum(r.cost_usd for r in g.ledger.records):.8f}")


def day11_single_turn_is_an_answer(reg, gw):
    """The depth-1 loop *is* Day 11: one tool turn + one *natural* answer.

    The regression this guards: a loop that, at `max_steps=1`, executes the tool
    turn, calls that the cap, and injects the `max_steps` note — turning Day 11's
    grounded answer into a false "I reached the step limit". The loop must ask
    once more after the last allowed step and take the answer exit.
    """
    print("\n== Day 12 — run_agent(max_steps=1) reproduces Day 11 exactly")
    reset(openai=["tc_code", "answer_code"])
    res = run_tool_answer("Where is checkout handled?", reg, gw)
    check("termination_reason is the natural answer, not max_steps",
          res["termination_reason"] == "answer", str(res["termination_reason"]))
    check("no cap note leaked into the answer (a loop must not change a single-tool answer)",
          "step limit" not in (res["answer"] or "").lower()
          and "max_steps" not in (res["answer"] or "").lower(),
          (res["answer"] or "")[:70])
    check("Day 11's two-turn shape: tool_steps == 1, turns == 2",
          res["tool_steps"] == 1 and res["turns"] == 2,
          f"steps={res['tool_steps']} turns={res['turns']}")
    check("nothing was capped (a single tool turn never hits the cap)",
          res["capped_tool_calls"] == [] and not res["truncated"])
    check("the grounded answer is inside the retrieved set",
          res["cited_subset_retrieved"] and not res["fabricated"])
    record_run("depth1", res, "max_steps=1 → Day 11 exactly (answer exit)")


def no_regression_day11():
    print("\n== Day 12 — no regression: Day 11's T1…T7 re-run unchanged (max_steps=1)")
    proc = subprocess.run([sys.executable, str(TOOL_SMOKE)], cwd=str(ROOT),
                          capture_output=True, text=True)
    tail = ""
    for line in reversed(proc.stdout.splitlines()):
        if line.strip():
            tail = line.strip()
            break
    check("tools/tool_smoke.py still exits 0 (T1–T7 green as-is)",
          proc.returncode == 0, tail)
    if proc.returncode != 0:
        print(proc.stdout[-2000:])


# ----------------------------------------------------------------- reporting

def render():
    print("\n" + "=" * 100)
    print("   DAY 12 — THE LOOP AS A CONTRACT (§8 per-step + per-run tables)")
    print("=" * 100)
    print(f"{'run':<6}{'step':<6}{'expected action':<34}{'tool chosen':<34}"
          f"{'args':<6}{'ok':<5}{'gate':<8}cum")
    print("-" * 104)
    for r in STEP_ROWS:
        exp = ", ".join(r["expected"])
        print(f"{r['run']:<6}{r['step']:<6}{exp:<34}{r['chosen']:<34}"
              f"{'ok' if r['args_valid'] else 'rej':<6}{'ok' if r['result_ok'] else 'no':<5}"
              f"{'ref' if r['refused'] else 'pass':<8}{r['cum_steps']}")
    print("-" * 104)
    print(f"\n{'run':<8}{'expected':<46}{'termination':<12}{'steps':<7}"
          f"{'turns':<7}{'refuse':<8}{'reject':<8}{'trunc':<7}cost")
    print("-" * 104)
    for r in RUN_ROWS:
        print(f"{r['id']:<8}{r['expected'][:44]:<46}{r['termination']:<12}"
              f"{r['steps']:<7}{r['turns']:<7}{r['refusals']:<8}{r['rejections']:<8}"
              f"{str(r['truncated']):<7}${r['cost_usd']:.8f}")
    print("-" * 104)
    print("The `expected action` vs `tool chosen` column is Day 11's recall@1 "
          "harvested per step — stub-truth here, a real model's verdict Day 21.")
    print("Termination reasons:")
    for k in ("answer", "max_steps", "budget", "gave_up"):
        print(f"  {k:<10} {AGENT_TERMINATIONS[k]}")


def main() -> None:
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
        doc_ids = {c.doc_id for c in doc_store.chunks}
        code_ids = {c.doc_id for c in code_store.chunks}
        gw = make_gateway()

        print(f"stores: {len(doc_store)} doc chunks (STUB) / {len(code_store)} "
              f"code symbols (STUB)   registry: {', '.join(reg.names())}")

        a1_multi_step(reg, gw, code_ids)
        a2_self_correction(reg, gw)
        a3_pivot_after_refusal(reg, gw, doc_ids)
        a4_parallel_calls(reg, gw)
        a5_termination(reg, gw, code_ids)
        a6_grounding_transcript(reg, gw)
        a7_zero_tool(reg, gw)
        a8_parallel_capacity(reg, gw)
        ran_off_budget(reg, gw)
        cost_shape(reg, gw, gw_doc, gw_code)
        day11_single_turn_is_an_answer(reg, gw)
        no_regression_day11()
    finally:
        if proc is not None:
            proc.terminate()

    render()
    print("\n" + ("ALL DAY 12 (MECH) CHECKS PASSED" if not FAILS
                  else f"{FAILS} CHECK(S) FAILED"))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
