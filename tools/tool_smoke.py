"""Day 11 §7/§8 — the tool question set (T1–T7) against the scripted stub.

No API key, no real network. Spawns `tools/mock_gateway.py` if it is not
already running, points a Day-4 `ModelGateway` at it, and drives
`run_tool_answer` with a registry built over the *offline* stub stores (the
Day-7 mini-corpus and the Day-10 14-symbol code tree) — so retrieval stays
byte-identical to the harness Days 7–10 grade and only the *chat* wire is
scripted:

    $ python tools/tool_smoke.py

    $ python tools/tool_smoke.py --live       # T1–T3 against the real gateway

`--live` answers the `PENDING` line as early as the day's framing allows: it
keeps the *retrieval* side on the same in-process stub stores (deterministic,
free, and the exact seam the suite above grades) and swaps the *chat* wire for
the real gateway built from `.env` — so a real model must choose the right
tool, spell valid arguments, and write a final answer grounded in the tool's
evidence. That is Day 21's verdict on Day 11's plumbing, runnable any day a
free key has quota.

What is `MECH` here: the tool seam (schemas, validation, rejection-as-result,
refusal-as-return-value, the single-turn cap), the evidence contract
(`cited ⊆ retrieved` on the tool's result set), the answer contract (a
fabricated `module#Symbol` is *caught*), the Day-6 empty-content sibling, and
the Days 7–10 no-regression lines. What stays `PENDING`: whether a *real* model
chooses the right tool and spells the right args — now answered per run via
`--live`.
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
sys.path.insert(0, str(ROOT))

from ai_os.agent import run_tool_answer  # noqa: E402
from ai_os.gateway import ModelGateway  # noqa: E402
from ai_os.ledger import Ledger  # noqa: E402
from ai_os.providers.anthropic import AnthropicProvider  # noqa: E402
from ai_os.providers.openai import OpenAIProvider  # noqa: E402
from ai_os.tools import (  # noqa: E402
    ToolRegistry,
    build_default_registry,
    code_retriever,
    doc_retriever,
    validate_args,
)

import tools.code_debug as _cd  # noqa: E402
import tools.rag_debug as _rd  # noqa: E402
from tools.retrieval_day10 import run_code_all  # noqa: E402
from tools.retrieval_day9 import run_all as run_doc_all  # noqa: E402

OPENAI_URL = "http://127.0.0.1:8700/v1"
ANTHROPIC_URL = "http://127.0.0.1:8701/v1"
ANSWER = ("Saleor is an open-source, headless commerce platform built on "
          "GraphQL and Django. This answer comes from the local stub server.")

# The two stub corpora's demo gates (Day 7's 0.05 for the mini-corpus, Day 10's
# 0.75 to exercise the code refusal path). Neither is a calibration — that is
# Day 21's, and the eval doc records it as such.
DOC_TAU = 0.05
CODE_TAU = 0.75

FAILS = 0
ROWS: list[dict] = []


def check(label: str, cond: bool, detail: str = ""):
    global FAILS
    if not cond:
        FAILS += 1
    print(f"[{'OK  ' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


def near(a: float, b: float, eps: float = 1e-3) -> bool:
    return abs(a - b) < eps


# ---------------------------------------------------------------- stub control

def _admin(port: int, provider: str, script) -> None:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/admin/reset",
        data=json.dumps({provider: script}).encode(), method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 200


def reset(openai=None, anthropic=None) -> None:
    _admin(8700, "openai", openai or ["ok"])
    _admin(8701, "anthropic", anthropic or ["ok"])


def hits(provider: str) -> int:
    with urllib.request.urlopen("http://127.0.0.1:8700/admin/state", timeout=5) as r:
        return json.load(r)["hits"][provider]


def make_gateway(*, only: str = "both", max_attempts: int = 3):
    providers = []
    if only in ("both", "openai"):
        providers.append(OpenAIProvider(api_key="sk-test", model="gpt-4o-mini",
                                        base_url=OPENAI_URL, timeout_s=5.0))
    if only in ("both", "anthropic"):
        providers.append(AnthropicProvider(api_key="sk-ant-test",
                                           model="claude-3-5-haiku-latest",
                                           base_url=ANTHROPIC_URL, timeout_s=5.0))
    return ModelGateway(providers, max_attempts=max_attempts, timeout_s=5.0,
                        base_delay_s=0.05, max_delay_s=0.5, ledger=Ledger())


class CountingGateway:
    """Counts the retrieval-side calls so the cost story is asserted, not told."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.chat_calls = 0
        self.embed_calls = 0

    def embed(self, texts):
        self.embed_calls += 1
        return self.inner.embed(texts)

    def chat(self, *a, **k):
        self.chat_calls += 1
        return self.inner.chat(*a, **k)


def build_registry():
    """The five tools over the offline stub stores (retrieval side, in-process)."""
    gw_doc = CountingGateway(_rd.StubGateway(extra_vocab=_rd._STUB_EXTRA_VOCAB))
    doc_store = _rd.build_stub_store(gw_doc.inner)
    gw_code = CountingGateway(_cd.HashEmbedGateway())
    code_store = _cd.build_stub_code_store(gw_code.inner)
    reg = build_default_registry(
        doc=doc_retriever(gw_doc, doc_store),
        code=code_retriever(gw_code, code_store),
        doc_tau=DOC_TAU, code_tau=CODE_TAU)
    return reg, gw_doc, gw_code, doc_store, code_store


def run(question: str, registry: ToolRegistry, gateway, *, expected_tool: str,
        mode: str = "tool", live: bool = False) -> dict:
    """One tool answer, recorded as a §8 row."""
    res = run_tool_answer(question, registry, gateway)
    chosen = res["tool_calls"][0]["name"] if res["tool_calls"] else None
    valid = bool(res["tool_ledger"]) and all(r["valid"] for r in res["tool_ledger"])
    if mode == "rejection":
        gate = "—"                      # nothing executed, so nothing was gated
    elif res["refused"]:
        gate = "refused"
    elif chosen:
        gate = "pass"
    else:
        gate = "—"
    ROWS.append({
        "question": question, "expected_tool": expected_tool, "tool_chosen": chosen,
        "args_valid": valid, "evidence": res["evidence"],
        "gate": gate, "turns": res["turns"], "cost_usd": res["ledger"]["cost_usd"],
        "mode": mode, "live": live, "result": res,
    })
    return res


# -------------------------------------------------------------------- T1–T7

def t1_tool_selection_code(reg, gw, code_ids):
    print("\n== T1 — tool selection, obvious (search_saleor_code)")
    reset(openai=["tc_code", "answer_code"])
    res = run("Where is checkout handled?", reg, gw,
              expected_tool="search_saleor_code")
    check("the model asked for search_saleor_code",
          res["tool_calls"][0]["name"] == "search_saleor_code",
          str(res["tool_calls"]))
    evidence = res["evidence"]
    check("evidence cites the domain service (complete_checkout)",
          "saleor/checkout/complete_checkout.py#complete_checkout" in evidence,
          str(evidence))
    check("evidence cites the mutation (CheckoutComplete) — split-brain double-hit",
          "saleor/graphql/checkout/mutations/checkout_complete.py#CheckoutComplete"
          in evidence, str(evidence))
    check("every evidence id ⊆ the code store (cited ⊆ retrieved)",
          set(evidence) <= code_ids, str(sorted(set(evidence) - code_ids)))
    check("the grounded answer is inside the retrieved set",
          res["cited_subset_retrieved"] and not res["fabricated"],
          str(res["fabricated"]))
    check("two turns: tool turn + answer turn", res["turns"] == 2)
    return res


def t2_tool_selection_docs(reg, gw, doc_ids):
    print("\n== T2 — tool selection, by corpus (search_saleor_docs)")
    reset(openai=["tc_docs", "ok"])
    res = run("What do the docs say about creating a checkout?", reg, gw,
              expected_tool="search_saleor_docs")
    check("the model asked for search_saleor_docs",
          res["tool_calls"][0]["name"] == "search_saleor_docs",
          str(res["tool_calls"]))
    check("every result ⊆ the doc store",
          bool(res["evidence"]) and set(res["evidence"]) <= doc_ids,
          str(sorted(set(res["evidence"]) - doc_ids)))
    return res


def t3_data_tool(reg, gw):
    print("\n== T3 — the data tool (get_order)")
    reset(openai=["tc_order", "ok"])
    res = run("Fetch order 1", reg, gw, expected_tool="get_order")
    row = res["tool_ledger"][0]
    check("the model asked for get_order", res["tool_calls"][0]["name"] == "get_order")
    check("args validated (id present)", row["valid"] and row["ok"], str(row))
    check("the seeded record came back",
          isinstance(row.get("data"), dict) and row["data"].get("number") == "1001",
          str(row.get("data")))
    check("the fixture says so: pending_resolver = day14",
          row["data"].get("pending_resolver") == "day14")
    return res


def t4_argument_matrix(reg, gw):
    print("\n== T4 — the argument validity matrix (rejection names the field)")
    cases = [
        ("tc_bad_args", "missing required argument", "query"),
        ("tc_bad_kind", "must be one of", "kind"),
        ("tc_unknown_key", "unknown argument", "colour"),
        ("tc_malformed", "malformed arguments", None),
    ]
    for scenario, needle, field in cases:
        reset(openai=[scenario, "ok"])
        res = run(f"<{scenario}>", reg, gw, expected_tool="search_saleor_code",
                  mode="rejection")
        row = res["tool_ledger"][0]
        err = row["error"] or ""
        check(f"{scenario}: rejection names the problem",
              needle in err, err)
        if field:
            check(f"{scenario}: message names the field {field!r}", field in err, err)
        check(f"{scenario}: no crash, the turn still completed", res["turns"] == 2)

    # A malformed `arguments` string is an OpenAI-wire artifact (Anthropic sends
    # an object), so the validator is also exercised directly — the check that
    # must hold whatever the dialect is.
    ok, err = validate_args(reg.schemas()[0]["function"], '{"query": ')
    check("validator: malformed JSON → named rejection", not ok and "malformed" in err)
    ok, err = validate_args({"parameters": {"type": "object", "properties": {
        "query": {"type": "string"}}, "required": ["query"]}}, '{"query": 5}')
    check("validator: wrong type → named rejection", not ok and "query" in err, err)
    ok, err = validate_args({"parameters": {"type": "object", "properties": {
        "q": {"type": "string"}}, "required": ["q"]}}, '{"q": "x", "extra": 1}')
    check("validator: unknown key → named rejection", not ok and "extra" in err, err)


def t5_unknown_tool(reg, gw):
    print("\n== T5 — an unknown tool is a rejection result, not an exception")
    reset(openai=["tc_unknown_tool", "ok"])
    res = run("<get_invoice>", reg, gw, expected_tool="get_invoice", mode="rejection")
    err = res["tool_ledger"][0]["error"] or ""
    check("unknown tool → rejection result", "unknown tool" in err, err)
    check("the rejection names the tool it refused", "get_invoice" in err, err)
    check("the run still completed (no crash)", res["turns"] == 2)


def t6_refusal(reg, gw, code_ids):
    print("\n== T6 — out-of-corpus refusal (never a fabricated symbol)")
    reset(openai=["tc_code_ooc", "answer_refusal_restated"])
    res = run("Debug the Shopify webhook handler", reg, gw,
              expected_tool="search_saleor_code")
    check("the gate refused the out-of-corpus call",
          res["refused"] and res["tool_ledger"][0]["refused"],
          str(res["tool_ledger"][0]))
    check("no evidence was manufactured", res["evidence"] == [], str(res["evidence"]))
    check("the answer restating the refusal is flagged (not believed)",
          res["refusal_restated"], f"answer={res['answer']!r}")
    check("nothing fabricated (no symbol outside the empty evidence)",
          not res["fabricated"], str(res["fabricated"]))

    # The guard itself, on a *grounded* call with a fabricated answer: the
    # harness must catch a symbol that never appeared in the evidence.
    reset(openai=["tc_code", "answer_fabricated"])
    res2 = run("Where is checkout handled?", reg, gw,
               expected_tool="search_saleor_code", mode="fabrication")
    check("a fabricated module#Symbol is caught by cited ⊆ retrieved",
          not res2["cited_subset_retrieved"]
          and "saleor/ghost/checkout.py#CompleteCheckoutMagic" in res2["fabricated"],
          str(res2["fabricated"]))
    return res


def t7_single_turn_cap(reg, gw):
    print("\n== T7 — the single-turn cap (loop_deferred: day12)")
    reset(openai=["tc_two", "ok"])
    res = run("<two tool_calls>", reg, gw, expected_tool="search_saleor_code",
              mode="cap")
    check("only one tool executed (the cap)", len(res["tool_ledger"]) == 1,
          str(res["tool_ledger"]))
    check("the violation is recorded as loop_deferred: day12",
          res["loop_deferred"] == "day12")
    check("the deferred call is kept for Day 12 (not silently dropped)",
          len(res["deferred_tool_calls"]) == 1, str(res["deferred_tool_calls"]))
    check("the conversation still completed", res["turns"] == 2)


def dialect_parity(reg):
    print("\n== Day 11 — dialect parity: the same turn over Anthropic's wire")
    reset(anthropic=["tc_code", "answer_code"])
    gw = make_gateway(only="anthropic")
    res = run_tool_answer("Where is checkout handled?", reg, gw)
    call = res["tool_calls"][0]
    check("tool_use folded into the same ToolCall shape",
          call["name"] == "search_saleor_code", str(call))
    check("arguments normalized to a JSON string from `input`",
          isinstance(call["arguments"], str) and "checkout" in call["arguments"],
          str(call["arguments"]))
    check("the tool result carried back to the model (2 turns, evidence present)",
          res["turns"] == 2 and bool(res["evidence"]), str(res["evidence"]))


def empty_content_sibling(gw):
    print("\n== Day 11 — the Day-6 empty-content rule gains its sibling")
    reset(openai=["empty", "ok"])
    c = gw.chat([{"role": "user", "content": "hi"}])
    check("empty content with NO tool_calls → still the retryable glitch (retried)",
          c.text == ANSWER and gw.last_attempts == 2, f"attempts={gw.last_attempts}")

    reset(openai=["tc_code"])
    gw2 = make_gateway()
    c2 = gw2.chat_with_tools([{"role": "user", "content": "Where is checkout handled?"}],
                             tools=[])
    check("empty content WITH tool_calls → a valid tool turn, never retried",
          c2.tool_calls is not None and gw2.last_attempts == 1,
          f"attempts={gw2.last_attempts} calls={c2.tool_calls}")
    check("the tool turn's text is legitimately empty", c2.text == "", repr(c2.text))


def cost_story(reg, gw_doc, gw_code):
    print("\n== Day 11 — cost: tools run retrieval, not generation")
    reset(openai=["tc_code", "answer_code"])
    gw = make_gateway()
    before_chat, before_embed = gw_code.chat_calls, gw_code.embed_calls
    run_tool_answer("Where is checkout handled?", reg, gw)
    ok_chats = sum(1 for r in gw.ledger.records if r.ok)
    check("3 bills: 1 tool-turn chat + 1 answer chat + 1 embed",
          ok_chats == 2 and gw_code.embed_calls - before_embed == 1,
          f"ok_chats={ok_chats} embeds={gw_code.embed_calls - before_embed}")
    check("tool *execution* makes zero chat calls (arithmetic over vectors)",
          gw_code.chat_calls == before_chat,
          f"chat={gw_code.chat_calls - before_chat}")
    check("the ledger priced the second chat turn (money learns no new shape)",
          sum(r.cost_usd for r in gw.ledger.records) > 0.0,
          f"${sum(r.cost_usd for r in gw.ledger.records):.8f}")


def no_regression(gw_doc, gw_code, doc_store, code_store):
    print("\n== Day 11 — no regression: the tools call the same retrieve()")
    rows, agg = run_doc_all(gw_doc.inner, doc_store, budget_chars=1200, tau=None)
    fails = [r["id"] for r in rows if not r["ok"]]
    check("the doc nine stay 9/9 through the tool layer", not fails, str(fails))
    check("Day 9's ΣΔMRR is unchanged (+1.000)", near(agg["mrr_delta"], 1.0),
          f"{agg['mrr_delta']}")

    code_rows = run_code_all(gw_code.inner, code_store, tau=CODE_TAU)
    cfails = [r["id"] for r in code_rows if not r["ok"]]
    check("the code six stay 6/6", not cfails, str(cfails))

    # And the tool's own top-1 is the retriever's top-1 — the tools are a new
    # way to *ask*, not a new way to *answer*.
    reg = build_default_registry(doc=doc_retriever(gw_doc, doc_store),
                                 code=code_retriever(gw_code, code_store),
                                 doc_tau=DOC_TAU, code_tau=CODE_TAU)
    tool_row = reg.execute("search_saleor_code",
                           {"query": "where is checkout completed", "k": 4})
    stored = {c.doc_id for c in code_store.chunks}
    check("the code tool returns store evidence, ranked (score desc)",
          bool(tool_row.evidence)
          and all(e["doc_id"] in stored for e in tool_row.evidence)
          and tool_row.evidence[0]["rank"] == 1,
          str([e["doc_id"] for e in tool_row.evidence[:2]]))
    check("the doc tool returns the doc store's own ids",
          all(e["doc_id"] in {c.doc_id for c in doc_store.chunks}
              for e in reg.execute("search_saleor_docs",
                                   {"query": "how do I create a checkout"}).evidence))


def tool_schemas(reg):
    print("\n== Day 11 — the schemas are the model's interface (MECH)")
    names = reg.names()
    check("all five tools registered, curriculum names exact",
          names == ["search_saleor_docs", "search_saleor_code", "get_product",
                    "get_order", "get_customer"], str(names))
    schemas = reg.schemas()
    check("schemas() speaks the neutral function dialect",
          all(s["type"] == "function" and "name" in s["function"] for s in schemas))
    code = next(s["function"] for s in schemas
                if s["function"]["name"] == "search_saleor_code")
    check("search_saleor_code's description carries Day 10's split-brain lesson",
          "double-hit" in code["description"])
    check("the description is retrieval for the chooser: names the evidence shape",
          "module#Symbol" in code["description"])
    check("kind is an enum the model can express",
          code["parameters"]["properties"]["kind"]["enum"]
          == ["function", "class", "mutation", "test", "module"])
    check("query is the one required argument",
          code["parameters"]["required"] == ["query"])
    rejected = reg.validate_args("nope", "{}")
    check("unknown tool name → rejection result, never an exception",
          not rejected.ok and "unknown tool" in (rejected.error or ""))


def build_live_gateway():
    """Real gateway from `.env` — chat is the thing under test."""
    from ai_os.config import build_gateway_from_env
    gw = build_gateway_from_env()
    if not any(getattr(p, "chat", None) for p in gw.providers):
        raise SystemExit("no chat-capable provider in .env (LLM_PROVIDERS)")
    return gw


def live_probe(gw):
    print("\n== LIVE — probe the real gateway (one plain chat, no tools)")
    try:
        c = gw.chat([{"role": "user", "content": "Reply with exactly the word ok."}],
                    max_tokens=16)
    except Exception as e:
        check("live gateway reachable", False,
              f"{e.__class__.__name__}: {e} (quota? wrong key?)")
        return False
    check("live gateway reachable", bool(c.text and c.text.strip()),
          repr((c.text or "")[:80]))
    print(f"    model={c.model} provider={gw.last_provider} "
          f"attempts={gw.last_attempts}")
    return bool(c.text and c.text.strip())


def _live_answer(res) -> None:
    """Print the real model's final answer so grounding is readable."""
    ans = (res.get("answer") or "").strip().replace("\n", " ")
    print(f"    answer: {ans[:300]}")
    if res.get("fabricated"):
        print(f"    ⚠ fabricated ids: {res['fabricated']}")


def live_t1_tool_selection_code(reg, gw, code_ids):
    print("\n== LIVE T1 — real model picks search_saleor_code (stub retrieval)")
    res = run("Where is checkout handled?", reg, gw,
              expected_tool="search_saleor_code", live=True)
    calls = res["tool_calls"]
    check("the real model asked for search_saleor_code",
          bool(calls) and calls[0]["name"] == "search_saleor_code", f"{calls}")
    check("arguments were emitted as JSON and passed validation",
          bool(res["tool_ledger"]) and all(r["valid"] for r in res["tool_ledger"]),
          f"calls={calls} ledger={res['tool_ledger']}")
    evidence = res["evidence"]
    check("evidence cites the domain service (complete_checkout)",
          "saleor/checkout/complete_checkout.py#complete_checkout" in evidence,
          str(evidence))
    check("evidence cites the mutation (CheckoutComplete) — split-brain double-hit",
          "saleor/graphql/checkout/mutations/checkout_complete.py#CheckoutComplete"
          in evidence, str(evidence))
    check("evidence ⊆ the code store (cited ⊆ retrieved)",
          set(evidence) <= code_ids, str(sorted(set(evidence) - code_ids)))
    check("the answer is grounded in the tool's evidence (no fabrication)",
          res["cited_subset_retrieved"] and not res["fabricated"],
          f"fabricated={res['fabricated']} citations={res['citations']}")
    check("two turns: tool turn + grounded answer turn", res["turns"] == 2,
          str(res["turns"]))
    _live_answer(res)
    return res


def live_t2_tool_selection_docs(reg, gw, doc_ids):
    print("\n== LIVE T2 — real model picks search_saleor_docs (stub retrieval)")
    res = run("What do the docs say about creating a checkout?", reg, gw,
              expected_tool="search_saleor_docs", live=True)
    check("the real model asked for search_saleor_docs",
          bool(res["tool_calls"]) and res["tool_calls"][0]["name"] == "search_saleor_docs",
          f"{res['tool_calls']}")
    check("arguments passed validation",
          bool(res["tool_ledger"]) and all(r["valid"] for r in res["tool_ledger"]),
          f"{res['tool_ledger']}")
    check("every result ⊆ the doc store",
          bool(res["evidence"]) and set(res["evidence"]) <= doc_ids,
          str(sorted(set(res["evidence"]) - doc_ids)))
    check("the answer is grounded in the doc evidence (no fabrication)",
          res["cited_subset_retrieved"] and not res["fabricated"],
          f"fabricated={res['fabricated']} citations={res['citations']}")
    _live_answer(res)
    return res


def live_t3_data_tool(reg, gw):
    print("\n== LIVE T3 — real model drives the data tool (get_order)")
    res = run("Fetch order 1 and tell me its status and total", reg, gw,
              expected_tool="get_order", live=True)
    check("the real model asked for get_order",
          bool(res["tool_calls"]) and res["tool_calls"][0]["name"] == "get_order",
          f"{res['tool_calls']}")
    row = res["tool_ledger"][0] if res["tool_ledger"] else {}
    check("the id argument was spelled valid (string id)",
          bool(row) and row["valid"] and row["ok"], str(row))
    check("the seeded record came back",
          isinstance(row.get("data"), dict) and row["data"].get("number") == "1001",
          str(row.get("data")))
    check("the fixture says so: pending_resolver = day14",
          bool(row.get("data")) and row["data"].get("pending_resolver") == "day14")
    check("the answer used the record (status/total)",
          res["cited_subset_retrieved"] and not res["fabricated"]
          and any(k in (res["answer"] or "") for k in ("UNFULFILLED", "19.99", "1001")),
          f"fabricated={res['fabricated']} answer={res['answer']!r}")
    _live_answer(res)
    return res


def run_live(reg, doc_ids, code_ids) -> None:
    print(f"LIVE mode: real chat gateway from .env, retrieval on the stub "
          f"stores (the seam the MECH suite grades). Tools: {reg.names()}")
    gw = build_live_gateway()
    print(f"providers: {[p.name for p in gw.providers]} / "
          f"models: {[p.model for p in gw.providers]}")
    if not live_probe(gw):
        print("\nLIVE probe failed — not running T1–T3 against a dead wire.")
        return
    live_t1_tool_selection_code(reg, gw, code_ids)
    live_t2_tool_selection_docs(reg, gw, doc_ids)
    live_t3_data_tool(reg, gw)


def render_rows(live: bool = False):
    print("\n" + "=" * 96)
    print("   DAY 11 — TOOL CALLS AS A CONTRACT (§8 rule-4 table)"
          + ("   [LIVE]" if live else ""))
    print("=" * 96)
    print(f"{'question':<40}{'expected tool':<20}{'chosen':<20}{'args':<9}"
          f"{'gate':<9}{'turns':<7}cost")
    print("-" * 104)
    for r in ROWS:
        q = r["question"][:38]
        chosen = r["tool_chosen"] or "—"
        args = "ok" if r["args_valid"] else "rejected"
        print(f"{q:<40}{r['expected_tool']:<20}{chosen:<20}{args:<9}"
              f"{r['gate']:<9}{r['turns']:<7}${r['cost_usd']:.8f}")
    print("-" * 104)
    if live:
        print("The `chosen` vs `expected tool` column is Day 11's recall@1 for "
              "tool selection — this run's verdict, by a real model over "
              "OpenRouter. Retrieval stayed on the stub stores, so evidence "
              "rows are the deterministic ones the suite grades.")
    else:
        print("The `chosen` vs `expected tool` column is Day 11's recall@1 for "
              "tool selection — stub-truth here, a real model's verdict Day 21.")


# --------------------------------------------------------------------- main

def _stub_up() -> bool:
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:8700/admin/state", timeout=1):
            return True
    except Exception:
        return False


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", action="store_true",
                    help="run T1–T3 against the real chat gateway from .env "
                         "(retrieval stays on the stub stores — the model is "
                         "the thing under test)")
    args = ap.parse_args()

    proc = None
    if args.live:
        reg, gw_doc, gw_code, doc_store, code_store = build_registry()
        doc_ids = {c.doc_id for c in doc_store.chunks}
        code_ids = {c.doc_id for c in code_store.chunks}
        run_live(reg, doc_ids, code_ids)
        render_rows(live=True)
        print("\n" + ("ALL DAY 11 (LIVE) CHECKS PASSED" if not FAILS
                      else f"{FAILS} LIVE CHECK(S) FAILED"))
        sys.exit(1 if FAILS else 0)
    elif not _stub_up():
        proc = subprocess.Popen([sys.executable, str(STUB)],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
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
              f"code symbols (STUB)   τ_doc={DOC_TAU} τ_code={CODE_TAU}")

        t1_tool_selection_code(reg, gw, code_ids)
        t2_tool_selection_docs(reg, gw, doc_ids)
        t3_data_tool(reg, gw)
        t4_argument_matrix(reg, gw)
        t5_unknown_tool(reg, gw)
        t6_refusal(reg, gw, code_ids)
        t7_single_turn_cap(reg, gw)
        dialect_parity(reg)
        empty_content_sibling(gw)
        cost_story(reg, gw_doc, gw_code)
        no_regression(gw_doc, gw_code, doc_store, code_store)
        tool_schemas(reg)
    finally:
        if proc is not None:
            proc.terminate()

    render_rows()
    print("\n" + ("ALL DAY 11 (MECH) CHECKS PASSED" if not FAILS
                  else f"{FAILS} CHECK(S) FAILED"))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
