"""Day 4 §7 — scripted dual-provider stub for deterministic gateway tests.

No API key, no network. Two stdlib HTTP servers in one process:
  - OpenAI-compatible   on :8700, path POST /v1/chat/completions
  - Anthropic-shaped    on :8701, path POST /v1/messages

Every request consumes the next entry of that provider's *script*; a script
entry is one of:
  ok          -> 200 success (JSON or SSE, per the request `stream` flag)
  429         -> HTTP 429 + Retry-After (2s by default; override via reset)
  500 502 503 -> transient server error
  401         -> invalid key
  400         -> bad request
  malformed   -> HTTP 200 with garbage (non-JSON) body
  hang        -> sleep then answer => client-side timeout
  cut         -> SSE: send a chunk or two, then drop the connection (no [DONE])
  empty       -> 200 with `content: null` and no `tool_calls` (Day 6's glitch)
  tc_*        -> Day 11: a scripted `tool_calls` turn (see TOOL_SCENARIOS:
                 tc_code / tc_code_ooc / tc_docs / tc_order / tc_two /
                 tc_bad_args / tc_bad_kind / tc_unknown_key / tc_malformed /
                 tc_unknown_tool, plus Day 12's tc_d12_* multi-step turns and
                 Day 13's failure modes: tc_d13_repeat / tc_d13_osc_a|b /
                 tc_d13_docs_miss / tc_d13_contra_step1|2 / tc_d13_contra_agree)
  answer_*    -> Day 11: a scripted answer turn (see ANSWER_SCENARIOS:
                 answer_code / answer_fabricated / answer_refusal_restated)

Admin control (both ports):
  POST /admin/reset {"openai": [...], "anthropic": [...], "retry_after": 0.3}
  GET  /admin/state -> {hits: {openai: n, anthropic: n}, served: {...}, ...}

    $ python tools/mock_gateway.py             # both ports default
    $ python tools/mock_gateway.py 8700 8701
"""
from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ANSWER = (
    "Saleor is an open-source, headless commerce platform built on "
    "GraphQL and Django. This answer comes from the local stub server."
)
CHUNKS = ANSWER.split()

DEFAULTS: dict[str, list[str]] = {"openai": ["ok"], "anthropic": ["ok"]}

# Day 14 — the *generated* SQL the stub "model" emits. The offline store is
# seeded with Saleor's core tables (ai_os/data.py's SEED_SQL), "today" is
# 2026-09-25 so "last month" is 2026-08, and these queries are the ones a
# grounded generator would write from the schema card. Kept here (not in the
# suite) so the same SQL can be asserted on by both the fixture and the table.
D14_SQL_REVENUE = (
    "SELECT p.name AS product, SUM(l.total_price_net_amount) AS revenue\n"
    "FROM order_orderline l\n"
    "JOIN order_order o ON o.id = l.order_id\n"
    "JOIN product_productvariant v ON v.id = l.variant_id\n"
    "JOIN product_product p ON p.id = v.product_id\n"
    "WHERE o.status <> 'DRAFT'\n"
    "  AND o.created_at >= '2026-08-01' AND o.created_at < '2026-09-01'\n"
    "GROUP BY p.name\n"
    "ORDER BY revenue DESC"
)
D14_SQL_CUSTOMERS = (
    "SELECT u.email AS customer, COUNT(o.id) AS orders\n"
    "FROM account_user u\n"
    "JOIN order_order o ON o.user_id = u.id\n"
    "GROUP BY u.email\n"
    "ORDER BY orders DESC"
)
D14_SQL_EMPTY = ("SELECT id, status FROM order_order "
                 "WHERE created_at >= '2099-01-01'")
D14_SQL_BAD_COLUMN = "SELECT order_total FROM order_order"
D14_SQL_FIX = ("SELECT COUNT(*) AS orders FROM order_order "
               "WHERE status <> 'DRAFT'")
D14_SQL_LINES = "SELECT id, product_name, quantity FROM order_orderline"
D14_SQL_DELETE = "DELETE FROM order_order"

# Day 11 — scripted tool-call scenarios. The stub grows a voice: a script entry
# that names a tool scenario makes the model *ask* instead of type, and the
# next entry serves the answer turn (a `tool` role message cannot get one
# otherwise). `(name, args)` pairs are dialect-neutral; each provider renders
# them in its own wire format below.
#   args is a dict  → valid JSON arguments (or a deliberate schema violation)
#   args is None    → a malformed `arguments` string (an OpenAI-wire artifact)
TOOL_SCENARIOS: dict[str, list[tuple[str, dict | None]]] = {
    "tc_code": [("search_saleor_code", {"query": "where is checkout completed", "k": 4})],
    "tc_code_ooc": [("search_saleor_code",
                     {"query": "debug the shopify webhook handler", "k": 4})],
    "tc_docs": [("search_saleor_docs",
                 {"query": "what do the docs say about creating a checkout", "k": 4})],
    "tc_order": [("get_order", {"id": "1"})],
    "tc_two": [("search_saleor_code", {"query": "where is checkout completed", "k": 4}),
               ("get_order", {"id": "1"})],
    "tc_bad_args": [("search_saleor_code", {})],
    "tc_bad_kind": [("search_saleor_code", {"query": "checkout", "kind": "banana"})],
    "tc_unknown_key": [("search_saleor_code", {"query": "checkout", "colour": "blue"})],
    "tc_malformed": [("search_saleor_code", None)],
    "tc_unknown_tool": [("get_invoice", {"id": "1"})],
    # Day 12 — multi-step scenarios: one script entry is one *loop iteration*,
    # so a script reads turn N, not turn 1. Step 2 is chosen from step 1's
    # evidence ("walking Day 10's map"): the mutation's imports name the service.
    "tc_d12_step1": [("search_saleor_code",
                      {"query": "CheckoutComplete", "kind": "mutation",
                       "module": "saleor/graphql/checkout", "k": 4})],
    "tc_d12_step2": [("search_saleor_code",
                      {"query": "complete_checkout", "kind": "function",
                       "module": "saleor/checkout", "k": 4})],
    # Self-correction: step 1 violates the `kind` enum, step 2 fixes *one* word.
    "tc_d12_fix_step1": [("search_saleor_code",
                          {"query": "checkout", "kind": "mutations"})],
    "tc_d12_fix_step2": [("search_saleor_code",
                          {"query": "where is checkout completed", "k": 4})],
    # Pivot after refusal: the code tool cannot cover a docs question, step 2
    # legally switches corpus instead of retrying the refused call.
    "tc_d12_pivot_step2": [("search_saleor_docs",
                            {"query": "what do the docs say about creating a checkout",
                             "k": 4})],
    # Parallel capacity: three calls in one step — all must be observed.
    "tc_three": [("search_saleor_code", {"query": "where is checkout completed", "k": 4}),
                 ("get_order", {"id": "1"}),
                 ("search_saleor_docs", {"query": "how do I create a checkout", "k": 4})],
    # Day 13 — scripted failure modes. The *chat* wire can only script the
    # model's side (a wrong tool, a repeat, an oscillation, an unknown name);
    # tool-side failures (raise/timeout) are injected in the registry and the
    # clock by `tools/failure_smoke.py`.
    #   F3 repeat: the identical valid call, re-emitted (no_progress).
    "tc_d13_repeat": [("search_saleor_code",
                       {"query": "where is checkout completed", "k": 4})],
    #   F4 oscillation: A,B,A,B — every step legal, the sequence is not.
    "tc_d13_osc_a": [("search_saleor_code",
                      {"query": "where is checkout completed", "k": 4})],
    "tc_d13_osc_b": [("search_saleor_docs",
                      {"query": "how do I create a checkout", "k": 4})],
    #   F1/F5 pivot: the *wrong* corpus refuses this out-of-vocabulary query (no
    #   known words → a zero vector → the gate refuses), so the loop can switch.
    "tc_d13_docs_miss": [("search_saleor_docs",
                          {"query": "frobnicate zzqx vlim", "k": 4})],
    #   F6 contradiction: step 1 and step 2 assert opposite values for the same
    #   claim (the stub's `claim_probe` tool carries the `claims` mapping).
    "tc_d13_contra_step1": [("claim_probe",
                             {"field": "order_created_by_checkout", "value": True,
                              "subject":
                                  "saleor/checkout/complete_checkout.py#complete_checkout"})],
    "tc_d13_contra_step2": [("claim_probe",
                             {"field": "order_created_by_checkout", "value": False,
                              "subject":
                                  "saleor/order/order_created.py#OrderCreated"})],
    #   F6 negative twin: two sources *agree* — no contradiction to flag.
    "tc_d13_contra_agree": [("claim_probe",
                             {"field": "order_created_by_checkout", "value": True,
                              "subject":
                                  "saleor/order/order_created.py#OrderCreated"})],
    # Day 14 — the data tools. `run_saleor_sql` takes the *generated* SQL the
    # model wrote (the artifact the trace grades and Day 15 attacks); the
    # parameterized tools complete the Day 11 stubs against the real store.
    "tc_d14_revenue": [("run_saleor_sql", {"sql": D14_SQL_REVENUE})],
    "tc_d14_customers": [("run_saleor_sql", {"sql": D14_SQL_CUSTOMERS})],
    "tc_d14_empty": [("run_saleor_sql", {"sql": D14_SQL_EMPTY})],
    "tc_d14_bad_column": [("run_saleor_sql", {"sql": D14_SQL_BAD_COLUMN})],
    "tc_d14_fix": [("run_saleor_sql", {"sql": D14_SQL_FIX})],
    "tc_d14_lines": [("run_saleor_sql", {"sql": D14_SQL_LINES})],
    "tc_d14_delete": [("run_saleor_sql", {"sql": D14_SQL_DELETE})],
    "tc_d14_order": [("get_order", {"id": "1"})],
    "tc_d14_customer": [("get_customer", {"id": "1"})],
    "tc_d14_schema": [("describe_saleor_schema", {"table": "order_order"})],
}

# Day 11 — scripted *answer* texts. The grounded one cites the tool's evidence;
# the fabricated one names a symbol no store holds (the harness must catch it);
# the restated one answers an out-of-corpus question after every tool refused.
ANSWER_SCENARIOS: dict[str, str] = {
    "answer_code": (
        "Checkout is completed by complete_checkout "
        "(saleor/checkout/complete_checkout.py#complete_checkout), reached from "
        "the CheckoutComplete mutation "
        "(saleor/graphql/checkout/mutations/checkout_complete.py#CheckoutComplete)."
    ),
    "answer_fabricated": (
        "Checkout is handled by saleor/ghost/checkout.py#CompleteCheckoutMagic."
    ),
    "answer_refusal_restated": (
        "Saleor does not appear to have a Shopify webhook handler; nothing in "
        "the corpus covers it."
    ),
    # Day 12 — answers that either ground a multi-step run or say *why* it ended.
    "answer_multistep": (
        "CheckoutComplete "
        "(saleor/graphql/checkout/mutations/checkout_complete.py#CheckoutComplete) "
        "calls complete_checkout "
        "(saleor/checkout/complete_checkout.py#complete_checkout), which creates "
        "the order — cited across both steps."
    ),
    "answer_docs": (
        "Per the docs, a checkout is created with checkoutCreate "
        "(quickstart/api#create-a-checkout) and completed with checkoutComplete "
        "(developer/checkout/api-guide#completing-checkout)."
    ),
    "answer_parallel": (
        "Checkout is completed by complete_checkout "
        "(saleor/checkout/complete_checkout.py#complete_checkout); order 1 is "
        "UNFULFILLED with total 19.99 (get_order)."
    ),
    "answer_limit": (
        "I reached the step limit before finishing: from the evidence gathered, "
        "CheckoutComplete "
        "(saleor/graphql/checkout/mutations/checkout_complete.py#CheckoutComplete) "
        "is the entry point, but the loop stopped at max_steps and I cannot "
        "confirm the rest."
    ),
    "answer_budget": (
        "The run budget was exhausted before finishing: from the evidence "
        "gathered, CheckoutComplete "
        "(saleor/graphql/checkout/mutations/checkout_complete.py#CheckoutComplete) "
        "is the entry point, but the budget ended the loop."
    ),
    "answer_gaveup": (
        "I could not fix the rejected call within the self-correction bound: the "
        "argument kept failing validation, so I am naming the stuck point "
        "instead of guessing."
    ),
    # Day 13 — answers that say *which* disease stopped the loop, from the
    # evidence only (the failure column stays `fabricated? = no`).
    "answer_noprogress": (
        "I stopped because I repeated an identical search call without making "
        "progress. From the evidence gathered, CheckoutComplete "
        "(saleor/graphql/checkout/mutations/checkout_complete.py#CheckoutComplete) "
        "is the entry point, but the loop stopped on the repeated call."
    ),
    "answer_oscillation": (
        "The loop stopped because I alternated the same two searches with no "
        "tie-breaker. From the evidence gathered, checkout is reached via "
        "CheckoutComplete "
        "(saleor/graphql/checkout/mutations/checkout_complete.py#CheckoutComplete)."
    ),
    "answer_tool_failure": (
        "The tool failed (a timeout or an execution error) and the recovery "
        "ladder was exhausted, so there is no evidence to answer from. I cannot "
        "confirm what happens after checkout completes, because both the code "
        "and documentation tools were unavailable."
    ),
    "answer_contradiction": (
        "Two observations disagree on whether the checkout creates the order: "
        "complete_checkout "
        "(saleor/checkout/complete_checkout.py#complete_checkout) says it does, "
        "while OrderCreated (saleor/order/order_created.py#OrderCreated) implies "
        "it does not. I am citing both sources and stating the conflict rather "
        "than picking a side."
    ),
    # Day 14 — answers grounded in the *result set*. Every number here is a
    # returned cell or a stated aggregate, so `asserted ⊆ returned` is ok; the
    # fabricated twin asserts a headline the rows never produced.
    "answer_d14_revenue": (
        "Desk Lamp led with 96.0 net revenue, then Blue T-Shirt at 59.97 and "
        "Coffee Mug at 38.0 — across 3 products in the window."
    ),
    "answer_d14_customers": (
        "ada@example.com and grace@example.com each placed 2 orders; "
        "alan@example.com placed 1."
    ),
    "answer_d14_empty": (
        "There were no orders in 2099 — the query matched 0 rows."
    ),
    "answer_d14_truncated": (
        "The result is truncated: these are the first 3 order lines and more "
        "rows exist beyond the page."
    ),
    "answer_d14_error": (
        "The query failed because that column does not exist, so there is no "
        "result to report."
    ),
    "answer_d14_orders": (
        "Order 1 is UNFULFILLED in USD; its gross total is 59.38 with 2 lines."
    ),
    "answer_d14_fabricated": (
        "Revenue for the period was roughly 250000 in total."
    ),
}

# Day 6 — scripted embeddings. A tiny vocabulary → vector table lets the
# stub do *deterministic, controllable* semantics: a text's vector is the
# normalized sum of its known words. Identical text → identical vector;
# texts sharing a term land nearer each other — enough for the smoke suite
# to assert on *which* chunk retrieves, without any API key.
EMBED_DIM = 8
EMBED_VOCAB: dict[str, list[float]] = {
    "checkout": [1.0, 0.2, 0.0, 0.0, 0.0, 0.1, 0.0, 0.0],
    "cart": [0.8, 0.1, 0.0, 0.1, 0.0, 0.0, 0.0, 0.0],
    "order": [0.9, 0.0, 0.1, 0.0, 0.1, 0.0, 0.0, 0.0],
    "product": [0.0, 0.0, 1.0, 0.0, 0.0, 0.9, 0.0, 0.0],
    "webhook": [0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.2, 0.0],
    "sync": [0.0, 0.0, 0.0, 0.0, 0.9, 0.0, 0.1, 0.0],
    "app": [0.0, 0.9, 0.0, 0.0, 0.0, 0.0, 0.0, 0.1],
    "payment": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.3],
    "fulfillment": [0.3, 0.0, 0.2, 0.0, 0.0, 0.0, 0.0, 1.0],
    "shipping": [0.0, 0.0, 0.0, 0.2, 0.0, 0.0, 0.6, 0.0],
}


def _embed_sum(text: str) -> list[float]:
    vec = [0.0] * EMBED_DIM
    for word in text.lower().split():
        w = word.strip(".,;:!?()[]{}'\"`*#-")
        if w in EMBED_VOCAB:
            for i, x in enumerate(EMBED_VOCAB[w]):
                vec[i] += x
    return vec


def embed_vector(text: str) -> list[float]:
    """Normalized scripted vector for `text` (zero vector if no known words)."""
    m = sum(x * x for x in _embed_sum(text)) ** 0.5
    v = _embed_sum(text)
    return [x / m for x in v] if m else v


def embed_prompt_tokens(inputs: list[str]) -> int:
    return sum(max(1, len(s) // 4) for s in inputs)


def embeddings_body(req: dict, inputs: list[str]) -> dict:
    model = req.get("model") or "text-embedding-3-small"
    return {
        "object": "list",
        "model": model,
        "data": [
            {"object": "embedding", "index": i, "embedding": embed_vector(t)}
            for i, t in enumerate(inputs)
        ],
        "usage": {"prompt_tokens": embed_prompt_tokens(inputs),
                  "total_tokens": embed_prompt_tokens(inputs)},
    }


class World:
    """Shared script + hit accounting, modifiable via /admin."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.scripts: dict[str, list[str]] = dict(DEFAULTS)
        self.embed_scripts: dict[str, list[str]] = dict(DEFAULTS)
        self.retry_after = 2.0
        self.hang_s = 30.0
        self.hits: dict[str, int] = {"openai": 0, "anthropic": 0}
        self.embed_hits: dict[str, int] = {"openai": 0, "anthropic": 0}
        self.served: dict[str, list[str]] = {"openai": [], "anthropic": []}
        self.embed_served: dict[str, list[str]] = {"openai": [], "anthropic": []}

    def reset(self, provider: str, script: list[str], retry_after: float,
              hang_s: float | None = None, embed_script: list[str] | None = None) -> None:
        with self.lock:
            self.scripts[provider] = [s.strip() for s in script if s.strip()] or ["ok"]
            if embed_script is not None:
                self.embed_scripts[provider] = ([s.strip() for s in embed_script
                                                 if s.strip()] or ["ok"])
            self.retry_after = retry_after
            if hang_s is not None:
                self.hang_s = hang_s
            self.hits = {"openai": 0, "anthropic": 0}
            self.embed_hits = {"openai": 0, "anthropic": 0}
            self.served = {"openai": [], "anthropic": []}
            self.embed_served = {"openai": [], "anthropic": []}

    def next(self, provider: str) -> str:
        with self.lock:
            script = self.scripts[provider]
            step = self.hits[provider]
            outcome = script[step] if step < len(script) else script[-1]
            self.hits[provider] += 1
            self.served[provider].append(outcome)
            return outcome

    def next_embed(self, provider: str) -> str:
        with self.lock:
            script = self.embed_scripts[provider]
            step = self.embed_hits[provider]
            outcome = script[step] if step < len(script) else script[-1]
            self.embed_hits[provider] += 1
            self.embed_served[provider].append(outcome)
            return outcome

    def state(self) -> dict:
        with self.lock:
            return {
                "hits": dict(self.hits),
                "served": dict(self.served),
                "scripts": dict(self.scripts),
                "embed_hits": dict(self.embed_hits),
                "embed_served": dict(self.embed_served),
                "embed_scripts": dict(self.embed_scripts),
                "retry_after": self.retry_after,
                "hang_s": self.hang_s,
            }


WORLD = World()


def openai_body(req: dict) -> dict:
    model = req.get("model") or "gpt-4o-mini"
    return {
        "id": "chatcmpl-stub-1",
        "object": "chat.completion",
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": ANSWER},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 12, "completion_tokens": 24, "total_tokens": 36},
    }


def anthropic_body(req: dict) -> dict:
    model = req.get("model") or "claude-3-5-haiku-latest"
    return {
        "id": "msg_stub_1",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": ANSWER}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }


def openai_tool_body(req: dict, scenario: list[tuple[str, dict | None]]) -> dict:
    """The OpenAI dialect of "the model asked": content null + `tool_calls`."""
    model = req.get("model") or "gpt-4o-mini"
    calls = []
    for i, (name, args) in enumerate(scenario):
        arguments = json.dumps(args) if isinstance(args, dict) else '{"query": '
        calls.append({"id": f"call_stub_{i + 1}", "type": "function",
                      "function": {"name": name, "arguments": arguments}})
    return {
        "id": "chatcmpl-stub-tools",
        "object": "chat.completion",
        "model": model,
        "choices": [{"index": 0, "finish_reason": "tool_calls",
                     "message": {"role": "assistant", "content": None,
                                 "tool_calls": calls}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
    }


def anthropic_tool_body(req: dict, scenario: list[tuple[str, dict | None]]) -> dict:
    """The Anthropic dialect: a `tool_use` content block (input is an object)."""
    model = req.get("model") or "claude-3-5-haiku-latest"
    blocks = [
        {"type": "tool_use", "id": f"toolu_stub_{i + 1}", "name": name,
         "input": args if isinstance(args, dict) else {}}
        for i, (name, args) in enumerate(scenario)
    ]
    return {
        "id": "msg_stub_tools", "type": "message", "role": "assistant",
        "model": model, "content": blocks, "stop_reason": "tool_use",
        "usage": {"input_tokens": 12, "output_tokens": 8},
    }


def openai_answer_body(req: dict, text: str) -> dict:
    model = req.get("model") or "gpt-4o-mini"
    return {
        "id": "chatcmpl-stub-answer", "object": "chat.completion", "model": model,
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 40, "completion_tokens": 30, "total_tokens": 70},
    }


def anthropic_answer_body(req: dict, text: str) -> dict:
    model = req.get("model") or "claude-3-5-haiku-latest"
    return {
        "id": "msg_stub_answer", "type": "message", "role": "assistant",
        "model": model, "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn", "usage": {"input_tokens": 40, "output_tokens": 30},
    }


def openai_empty_body(req: dict) -> dict:
    """Day 6's regression hazard, scriptable: a 200 with `content: null` and
    **no** `tool_calls` — the retryable glitch, distinct from a tool turn."""
    model = req.get("model") or "gpt-4o-mini"
    return {
        "id": "chatcmpl-stub-empty", "object": "chat.completion", "model": model,
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": None}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5},
    }


def anthropic_empty_body(req: dict) -> dict:
    model = req.get("model") or "claude-3-5-haiku-latest"
    return {
        "id": "msg_stub_empty", "type": "message", "role": "assistant",
        "model": model, "content": [], "stop_reason": "end_turn",
        "usage": {"input_tokens": 5, "output_tokens": 0},
    }


def openai_sse(req: dict) -> str:
    model = req.get("model") or "gpt-4o-mini"
    events = []
    for i, token in enumerate(CHUNKS):
        events.append("data: " + json.dumps({
            "id": "chatcmpl-stub-1", "object": "chat.completion.chunk",
            "model": model,
            "choices": [{"index": 0, "delta": {"content": token + " "},
                         "finish_reason": None}],
        }) + "\n\n")
    events.append("data: " + json.dumps({
        "id": "chatcmpl-stub-1", "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 24, "total_tokens": 36},
    }) + "\n\n")
    events.append("data: [DONE]\n\n")
    return "".join(events)


def anthropic_sse(req: dict) -> str:
    model = req.get("model") or "claude-3-5-haiku-latest"
    out = []
    out.append('event: message_start\ndata: ' + json.dumps({
        "type": "message_start",
        "message": {"id": "msg_stub_1", "type": "message", "role": "assistant",
                    "model": model, "content": [],
                    "usage": {"input_tokens": 10, "output_tokens": 0}},
    }) + "\n\n")
    for token in CHUNKS:
        out.append('event: content_block_delta\ndata: ' + json.dumps({
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": token + " "},
        }) + "\n\n")
    out.append('event: message_delta\ndata: ' + json.dumps({
        "type": "message_delta",
        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
        "usage": {"output_tokens": 20},
    }) + "\n\n")
    out.append('event: message_stop\ndata: {"type":"message_stop"}\n\n')
    return "".join(out)


class Handler(BaseHTTPRequestHandler):
    provider = "openai"

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return {}

    def do_GET(self):
        if self.path == "/admin/state":
            body = json.dumps(WORLD.state()).encode()
            self._raw(200, body, "application/json")
        else:
            self.send_error(404)

    def do_POST(self):
        req = self._read_json()
        if self.path.startswith("/admin/reset"):
            retry_after = float(req.get("retry_after", WORLD.retry_after))
            hang_s = req.get("hang_s")
            script = req.get(self.provider, ["ok"])
            embed_script = req.get(f"{self.provider}_embed")
            WORLD.reset(self.provider, script, retry_after,
                        float(hang_s) if hang_s is not None else None,
                        embed_script)
            self._raw(200, json.dumps({"ok": True}).encode(), "application/json")
            return

        is_embed = self.path.rstrip("/").endswith("/embeddings")
        if is_embed:
            outcome = WORLD.next_embed(self.provider)
            inputs = req.get("input") or []
            if not isinstance(inputs, list):
                inputs = [inputs]
            if outcome == "ok":
                self._json(200, embeddings_body(req, inputs))
            elif outcome == "429":
                self._json_raw(429, {"error": {"message": "Rate limit reached"}},
                               extra={"Retry-After": str(WORLD.retry_after)})
            elif outcome == "401":
                self._json_raw(401, {"error": {"message": "Invalid API key provided."}})
            elif outcome == "400":
                self._json_raw(400, {"error": {"message": "Invalid request: unknown parameter"}})
            elif outcome in ("500", "502", "503"):
                self._json_raw(int(outcome), {"error": {"message": f"server {outcome}"}})
            elif outcome == "malformed":
                self._raw(200, b"{not valid json", "text/plain")
            elif outcome == "hang":
                time.sleep(WORLD.hang_s)
                self._json(200, embeddings_body(req, inputs))
            else:
                self._json(200, embeddings_body(req, inputs))
            return

        outcome = WORLD.next(self.provider)
        stream = bool(req.get("stream"))

        # Day 11 — scripted tool-call and answer turns, ahead of the plain
        # outcomes, so one script entry can make the model *ask* and the next
        # can serve the grounded (or fabricated) answer to the tool result.
        if outcome in TOOL_SCENARIOS:
            body = (openai_tool_body if self.provider == "openai"
                    else anthropic_tool_body)(req, TOOL_SCENARIOS[outcome])
            self._json(200, body)
            return
        if outcome in ANSWER_SCENARIOS:
            body = (openai_answer_body if self.provider == "openai"
                    else anthropic_answer_body)(req, ANSWER_SCENARIOS[outcome])
            self._json(200, body)
            return
        if outcome == "empty":
            body = (openai_empty_body if self.provider == "openai"
                    else anthropic_empty_body)(req)
            self._json(200, body)
            return

        if outcome == "ok":
            if stream:
                body = openai_sse(req) if self.provider == "openai" else anthropic_sse(req)
                self._raw(200, body.encode(), "text/event-stream")
            else:
                resp = openai_body(req) if self.provider == "openai" else anthropic_body(req)
                self._json(200, resp)
        elif outcome == "429":
            if stream:
                self._raw(429, json.dumps({"error": {"message": "Rate limit reached"}}).encode(),
                          "application/json", extra={"Retry-After": str(WORLD.retry_after)})
            else:
                self._json_raw(429, {"error": {"message": "Rate limit reached"}},
                               extra={"Retry-After": str(WORLD.retry_after)})
        elif outcome == "401":
            self._json_raw(401, {"error": {"message": "Invalid API key provided."}})
        elif outcome == "400":
            self._json_raw(400, {"error": {"message": "Invalid request: unknown parameter"}})
        elif outcome in ("500", "502", "503"):
            self._json_raw(int(outcome), {"error": {"message": f"server {outcome}"}})
        elif outcome == "malformed":
            self._raw(200, b"{not valid json", "text/plain")
        elif outcome == "hang":
            time.sleep(WORLD.hang_s)
            resp = openai_body(req) if self.provider == "openai" else anthropic_body(req)
            self._json(200, resp)
        elif outcome == "cut":
            # SSE: send two chunks, then drop the connection before [DONE]
            if self.provider == "openai":
                model = req.get("model") or "gpt-4o-mini"
                body = "".join("data: " + json.dumps({
                    "model": model, "choices": [{"delta": {"content": t + " "}}]
                }) + "\n\n" for t in CHUNKS[:2])
            else:
                body = ''.join(
                    'event: content_block_delta\ndata: ' + json.dumps({
                        "type": "content_block_delta", "index": 0,
                        "delta": {"type": "text_delta", "text": t + " "},
                    }) + "\n\n" for t in CHUNKS[:2])
            self._raw(200, body.encode(), "text/event-stream", close=True)
        else:
            resp = openai_body(req) if self.provider == "openai" else anthropic_body(req)
            self._json(200, resp)

    def _json(self, status: int, body: dict):
        self._raw(status, json.dumps(body).encode(), "application/json")

    def _json_raw(self, status: int, body: dict, extra: dict | None = None):
        self._raw(status, json.dumps(body).encode(), "application/json", extra=extra)

    def _raw(self, status: int, data: bytes, content_type: str,
             extra: dict | None = None, close: bool = False):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
            if close:
                self.wfile.flush()
                self.close_connection = True
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # client timed out / dropped mid-write (stub stays quiet)

    def log_message(self, fmt: str, *args):
        print(f"[stub:{self.provider}] {fmt % args}")


def _server(provider: str, port: int, daemon: bool = True):
    handler = type(f"{provider.title()}Handler", (Handler,), {"provider": provider})
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    httpd.daemon_threads = daemon
    t = threading.Thread(target=httpd.serve_forever, daemon=daemon)
    t.start()
    return httpd, t


def main() -> None:
    base = 8700
    if len(sys.argv) > 1:
        base = int(sys.argv[1])
    servers = [_server("openai", base), _server("anthropic", base + 1)]
    print(f"mock gateway: openai :{base} / anthropic :{base+1}")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        for httpd, _ in servers:
            httpd.shutdown()


if __name__ == "__main__":
    main()