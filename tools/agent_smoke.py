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

    $ python tools/agent_smoke.py --live      # A1/A6/A7 against the real gateway

`--live` answers the Day 12 `PENDING` line as early as the framing allows: it
keeps *retrieval* on the same in-process stub stores (deterministic, free, the
exact seam the suite above grades) and swaps the *chat* wire for the real gateway
built from `.env` — so a real model must compose a multi-step investigation
(follow an import it was never told to a second query, cite each step's evidence)
or fail to. Whether it *composes* is a measured cell, not a hard gate (`measure`,
never `check`); the follow is read from earlier evidence *text* (the map lives in
a chunk's body, not only its id). The harness contract (valid args,
`cited ⊆ ∪ observations`, a real exit) is hard-checked whatever the model does. A
provider outage (free-tier `429`/empty completion) reports `[SKIP]` and exits **2**,
so "could not measure" is never confused with "measured and wrong" (exit 1).
"""
from __future__ import annotations

import argparse
import json
import re
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


# ------------------------------------------------------------- live (real chat)
# The retrieval side stays on the in-process stub stores (the seam the MECH
# suite grades); only the *chat* wire is real, so a real model must compose the
# investigation or fail to. The composition cells are MEASURED, never gated.

def build_live_gateway():
    """Real gateway from `.env` — chat is the thing under test."""
    from ai_os.config import build_gateway_from_env
    gw = build_gateway_from_env()
    if not any(getattr(p, "chat", None) for p in gw.providers):
        raise SystemExit("no chat-capable provider in .env (LLM_PROVIDERS)")
    return gw


def live_probe(gw, tries: int = 3) -> bool:
    print("\n== LIVE — probe the real gateway (one plain chat, no tools)")
    err = "not attempted"
    for attempt in range(1, tries + 1):
        try:
            c = gw.chat([{"role": "user",
                          "content": "Reply with exactly the word ok."}], max_tokens=16)
            if c.text and c.text.strip():
                check("live gateway reachable", True, repr(c.text[:80]))
                print(f"    model={c.model} provider={gw.last_provider} "
                      f"attempts={gw.last_attempts}")
                return True
            err = "empty completion (no content returned)"
        except Exception as e:
            err = f"{e.__class__.__name__}: {e}"
        if attempt < tries:
            time.sleep(min(2.0 * attempt, 5.0))
    skip("live gateway reachable — provider unavailable; re-run when it has quota", err)
    return False


def measure(label: str, cond: bool, detail: str = ""):
    """A *measured* cell (Day 21's semantic verdict) — printed, never a gate.

    The harness contract is hard-checked; whether a real model *composes* is the
    thing being measured, so a weak model must be recorded, not turned into a
    false harness failure.
    """
    print(f"[{'OK  ' if cond else 'MEAS'}] {label}" + (f"  ({detail})" if detail else ""))


def skip(label: str, detail: str = ""):
    """Not measured — provider/network unavailability, not a harness failure.

    A free-tier model that returns an empty completion or 429s is an environment
    fact, not a Day 12 defect; the run reports `SKIP` and exits 2 (incomplete), so
    "could not measure" is never confused with "measured and wrong" (exit 1).
    """
    print(f"[{'SKIP'}] {label}" + (f"  ({detail})" if detail else ""))


def _live_answer(res) -> None:
    ans = (res.get("answer") or "").strip().replace("\n", " ")
    print(f"    answer: {ans[:300]}")
    if res.get("fabricated"):
        print(f"    ⚠ fabricated ids: {res['fabricated']}")


_SYM_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")


def _ident_tokens(text: str) -> set[str]:
    """Distinctive identifier-like tokens: underscored, camelCase, or long.

    Filtering to these keeps generic words (`checkout`, `order`, `mutation`) out,
    so a "follow" is a real symbol, not shared vocabulary.
    """
    return {t for t in _SYM_RE.findall(text or "")
            if "_" in t or any(c.isupper() for c in t[1:]) or len(t) >= 14}


def _step_evidence_text(reg, step) -> str:
    """A step's evidence *text* — the map the model walks (re-executed, in-process).

    The map lives in the returned chunks' bodies (a mutation's body names the
    service it calls), not only in the `module#Symbol` ids, so this re-runs the
    step's calls against the deterministic stub stores to read the text the model
    actually saw.
    """
    chunks: list[str] = []
    for c in step["calls"]:
        args = c.get("args_parsed")
        if not c.get("args_valid") or args is None:
            continue
        try:
            r = reg.execute(c["name"], args)
        except Exception:
            continue
        for ev in (r.evidence or []):
            chunks.append(ev.get("text", ""))
    return "\n".join(chunks)


def live_a1_a6(reg, gw, doc_ids, code_ids):
    print("\n== LIVE A1/A6 — real model composes a multi-step investigation (PENDING)")
    question = "What happens after CheckoutComplete submits?"
    res = run_agent(question, reg, gw, max_steps=5)
    record_run("L-A1", res, "real model → ≥2 steps; cite ≥2 observations")

    # The harness contract — hard checks (must hold whatever the model does).
    corpora = doc_ids | code_ids
    check("the real model used a tool at least once", res["tool_steps"] >= 1,
          str(res["tool_steps"]))
    check("every emitted call passed argument validation",
          bool(res["tool_ledger"]) and all(r["valid"] for r in res["tool_ledger"]),
          str(res["tool_ledger"]))
    check("grounding over the transcript holds (cited ⊆ ∪ observations)",
          res["cited_subset_retrieved"] and not res["fabricated"],
          str(res["fabricated"]))
    check("every observation ⊆ a known store (docs ∪ code)",
          all(set(s["retrieved"]) <= corpora for s in res["steps"]),
          str(sorted({i for s in res["steps"] for i in s["retrieved"]} - corpora)))
    check("a legitimate exit fired",
          res["termination_reason"] in AGENT_TERMINATIONS,
          str(res["termination_reason"]))

    # The composition cells — MEASURED (Day 21's verdict, recorded not gated).
    # The claim under test: a later step's query names a symbol that appeared in
    # an *earlier observation* — its evidence text or ids — and was NOT in the
    # user's question. That is "read the map and walked it" rather than "re-asked
    # the question". A symbol merely echoed from the question does not count.
    q_tokens = _ident_tokens(question)
    follows: list[str] = []
    seen: set[str] = set()
    for i, s in enumerate(res["steps"]):
        query = " ".join(str((c.get("args_parsed") or {}).get("query", ""))
                         for c in s["calls"])
        fresh = _ident_tokens(query) - q_tokens
        hits = sorted(t for t in fresh if t in seen)
        if i > 0 and hits:
            follows.append(f"step {i + 1} ← {hits}")
        seen |= _ident_tokens(_step_evidence_text(reg, s))
    measure("COMPOSITION: ≥2 steps, the second symbol never named by the user",
            res["tool_steps"] >= 2, f"tool_steps={res['tool_steps']}")
    measure("COMPOSITION: a later step's query names a symbol from earlier "
            "evidence (and not from the question)", bool(follows),
            "; ".join(follows) or "no such follow")
    measure("COMPOSITION: the answer cites from ≥2 observations",
            sum(1 for s in res["steps"] if s["cited"]) >= 2,
            str([s["cited"] for s in res["steps"]]))
    measure("A6 note: the answer cited ≥1 id-shaped symbol at all (grounding is "
            "id-shaped; prose that ignores evidence is not caught)",
            bool(res["citations"]), str(res["citations"][:4]))

    for n, s in enumerate(res["steps"], start=1):
        STEP_ROWS.append({
            "run": "L-A1", "step": n, "expected": ["search_saleor_code"],
            "chosen": ", ".join(s["tool_calls"]), "args_valid": s["args_valid"],
            "result_ok": s["result_ok"], "refused": s["refused"],
            "cum_steps": n, "cost_usd": res["ledger"]["cost_usd"],
        })
    _live_answer(res)
    return res


def live_a7_zero_tool(reg, gw):
    print("\n== LIVE A7 — a real model answers a trivial question without tools")
    res = run_agent(
        "Reply with exactly the word ok and nothing else. Do not call any tool.",
        reg, gw, max_steps=3)
    record_run("L-A7", res, "no tool_calls → tool_steps: 0")
    check("no tool call at all", res["tool_steps"] == 0 and res["tool_calls"] == [],
          str(res["tool_calls"]))
    check("termination is the natural exit", res["termination_reason"] == "answer",
          str(res["termination_reason"]))
    _live_answer(res)
    return res


def run_live(reg, doc_ids, code_ids) -> bool:
    print("LIVE mode: real chat gateway from .env, retrieval on the stub stores "
          "(the seam the MECH suite grades).")
    gw = build_live_gateway()
    print(f"providers: {[p.name for p in gw.providers]} / "
          f"models: {[p.model for p in gw.providers]}")
    if not live_probe(gw):
        print("\nLIVE probe failed — not running A1/A6/A7 against a dead wire.")
        return False
    try:
        live_a1_a6(reg, gw, doc_ids, code_ids)
        live_a7_zero_tool(reg, gw)
    except Exception as e:
        skip(f"live A1/A6/A7 aborted by provider unavailability — "
             f"{type(e).__name__}: {e}")
        return False
    return True


# ----------------------------------------------------------------- reporting

def render(live: bool = False):
    print("\n" + "=" * 100)
    print("   DAY 12 — THE LOOP AS A CONTRACT (§8 per-step + per-run tables)"
          + ("   [LIVE]" if live else ""))
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
    if live:
        print("The `expected action` vs `tool chosen` column is Day 11's recall@1 "
              "harvested per step — this run's verdict, by a real model over "
              "OpenRouter. Retrieval stayed on the stub stores, so evidence rows "
              "are the deterministic ones the suite grades. `[MEAS]` rows are the "
              "composition cells (Day 21's), recorded not gated.")
    else:
        print("The `expected action` vs `tool chosen` column is Day 11's recall@1 "
              "harvested per step — stub-truth here, a real model's verdict Day 21.")
    print("Termination reasons:")
    for k in ("answer", "max_steps", "budget", "gave_up"):
        print(f"  {k:<10} {AGENT_TERMINATIONS[k]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", action="store_true",
                    help="run A1/A6/A7 against the real chat gateway from .env "
                         "(retrieval stays on the stub stores — the model is the "
                         "thing under test)")
    args = ap.parse_args()

    if args.live:
        reg, _gw_doc, _gw_code, doc_store, code_store = build_registry()
        doc_ids = {c.doc_id for c in doc_store.chunks}
        code_ids = {c.doc_id for c in code_store.chunks}
        print(f"stores: {len(doc_store)} doc chunks (STUB) / {len(code_store)} "
              f"code symbols (STUB)   registry: {', '.join(reg.names())}")
        ok = run_live(reg, doc_ids, code_ids)
        render(live=True)
        if not ok:
            print("\nLIVE RUN INCOMPLETE — the provider was unavailable (not a "
                  "harness failure); re-run when the free model has quota.")
            sys.exit(2)
        print("\n" + ("ALL DAY 12 (LIVE) HARNESS CHECKS PASSED" if not FAILS
                      else f"{FAILS} LIVE HARNESS CHECK(S) FAILED"))
        sys.exit(1 if FAILS else 0)

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
