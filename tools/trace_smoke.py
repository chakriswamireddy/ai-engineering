"""Day 23 §8 — the O1–O9 trace fixtures against the offline stubs.

    $ python tools/trace_smoke.py

No API key, no real network, no real sleeping (bar the tiny timeout fixture).
The suite proves the day's mechanics (`MECH`) — identity, parent paths,
monotonic duration, ledger arithmetic, prompt hashes, lookup, retention, and
redaction — and the **O9 control**: tracing off ≡ tracing on, byte-for-byte on
every observable output except the additive trace id.

It reuses the suites' own seams so a trace is graded against the *same* runs
Days 11–22 already grade: `tools/rag_debug.py`'s stub store + `tools/mock_gateway.py`
for chat/embeds, and `tools/tool_smoke.py`'s registry/agent helpers.

`PENDING` (named, never declared solved): whether a trace is *operationally
useful* and whether its attribute balance is right is a design judgment; raw
payload capture stays opt-in; durable storage/export and cross-process
correlation are Day 24 / deployment work.
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

from ai_os import observability as obs  # noqa: E402
from ai_os.agent import run_agent  # noqa: E402
from ai_os.contract import (  # noqa: E402
    BadRequestError, InvalidKeyError, ProviderTimeout, RateLimitError,
)
from ai_os.gateway import ModelGateway  # noqa: E402
from ai_os.ledger import Ledger  # noqa: E402
from ai_os.providers.anthropic import AnthropicProvider  # noqa: E402
from ai_os.providers.openai import OpenAIProvider  # noqa: E402
from ai_os.rag import (  # noqa: E402
    RAG_PROMPT_REF, SYSTEM_PROMPT, DimensionMismatch, run_rag,
)
from ai_os.tools import (  # noqa: E402
    build_default_registry, code_retriever, doc_retriever,
)

import tools.rag_debug as _rd  # noqa: E402
from tools.tool_smoke import (  # noqa: E402
    _stub_up, build_registry, make_gateway, reset,
)

OPENAI_URL = "http://127.0.0.1:8700/v1"
ANTHROPIC_URL = "http://127.0.0.1:8701/v1"

FAILS = 0
ROWS: list[dict] = []


def check(label: str, cond: bool, detail: str = "") -> bool:
    global FAILS
    if not cond:
        FAILS += 1
    print(f"[{'OK  ' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))
    return bool(cond)


def row(oid: str, what: str, ok: bool, detail: str = "") -> None:
    ROWS.append({"id": oid, "what": what, "ok": ok, "detail": detail})


# --------------------------------------------------------------- stub control

def _admin(port: int, provider: str, script, retry_after: float | None = None) -> None:
    payload = {provider: script}
    if retry_after is not None:
        payload["retry_after"] = retry_after
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/admin/reset",
        data=json.dumps(payload).encode(), method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 200


def both(openai, anthropic=None, *, retry_after: float = 0.05) -> None:
    _admin(8700, "openai", openai, retry_after)
    _admin(8701, "anthropic", anthropic or ["ok"], retry_after)


# -------------------------------------------------------------- trace helpers

def spans(trace, kind: str | None = None) -> list[dict]:
    out = trace.as_dict()["spans"]
    return [s for s in out if kind is None or s["kind"] == kind]


def find(trace, name: str) -> dict | None:
    for s in trace.as_dict()["spans"]:
        if s["name"] == name:
            return s
    return None


def events(trace, name: str) -> list[dict]:
    return [e for e in trace.as_dict()["events"] if e["name"] == name]


def parent_paths_ok(trace) -> bool:
    d = trace.as_dict()
    ids = {s["span_id"] for s in d["spans"]}
    if not d["spans"]:
        return False
    for s in d["spans"]:
        if s["span_id"] not in ids:
            return False
        p = s["parent_span_id"]
        if p is not None and p not in ids:
            return False
    return all(s["attributes"] is not None for s in d["spans"])


# ------------------------------------------------------- a tracing stub gateway

class TracingStub:
    """Wrap the offline `StubGateway` so a stub chat/embed still produces the
    production span shapes (`model.embed`, `model.chat`) and a linked ledger row.

    This is *test scaffolding*: production spans are emitted by `ModelGateway`.
    It exists so O2/O5 can be graded offline and deterministically without HTTP.
    """

    def __init__(self, inner, *, provider: str = "openai",
                 model: str = "gpt-4o-mini") -> None:
        self.inner = inner
        self.ledger = inner.ledger
        self.last_provider = provider
        self.last_attempts = 1
        self.last_latency_ms = 1.0
        self._model = model

    def _wrap(self, operation: str, fn, *, success_attrs, prompt_ref=None):
        tr = obs.current_trace()
        if tr is None or not getattr(tr, "recording", False):
            return fn()
        with tr.span(obs.KIND_MODEL, f"model.{operation}",
                     attributes={"operation": operation,
                                 "prompt": obs.prompt_attrs(prompt_ref)}) as lspan:
            t0 = time.perf_counter()
            value = fn()
            latency = (time.perf_counter() - t0) * 1000
            attrs = success_attrs(value)
            span = tr.start_span(obs.KIND_MODEL, f"model.{operation}.attempt",
                                 parent=lspan,
                                 attributes={"operation": operation,
                                             "provider": self.last_provider,
                                             "attempt": 1, "fallback_depth": 0})
            span.update(latency_ms=round(latency, 3), **attrs)
            obs.end_span(span)
            tr.leave(span)
            return value

    def embed(self, inputs, *, prompt_ref=None, operation="embed"):
        def call():
            return self.inner.embed(inputs)
        embs = self._wrap("embed", call, success_attrs=lambda e: {
            "actual_model": e[0].model, "inputs": len(inputs),
            "input_tokens": e[0].usage.input_tokens})
        usage = embs[0].usage
        rec = self.ledger.record(provider=self.last_provider, model=embs[0].model,
                                 input_tokens=usage.input_tokens, ok=True)
        tr = obs.current_trace()
        if tr is not None and getattr(tr, "recording", False):
            tr.link(rec)
        return embs

    def chat(self, messages, *, prompt_ref=None, operation="chat", **kw):
        def call():
            return self.inner.chat(messages, **kw)
        completion = self._wrap(operation, call, success_attrs=lambda c: {
            "actual_model": c.model, "finish_reason": c.finish_reason,
            "input_tokens": c.usage.input_tokens,
            "output_tokens": c.usage.output_tokens},
            prompt_ref=prompt_ref)
        rec = self.ledger.record(provider=self.provider_of(completion),
                                 model=completion.model,
                                 input_tokens=completion.usage.input_tokens,
                                 output_tokens=completion.usage.output_tokens,
                                 ok=True)
        tr = obs.current_trace()
        if tr is not None and getattr(tr, "recording", False):
            tr.link(rec)
        return completion

    def provider_of(self, completion) -> str:
        return self.last_provider


def stub_rag_gateway():
    inner = _rd.StubGateway(extra_vocab=_rd._STUB_EXTRA_VOCAB)
    store = _rd.build_stub_store(inner)
    return TracingStub(inner), store


# ------------------------------------------------------------------- fixtures

def o1_root_spine(reg, gw):
    print("\n== O1 — one request, one root, one parent path")
    both(["tc_code", "answer_code"])
    res = run_agent("Where is checkout completed (in code)?", reg, gw, max_steps=5)
    trace = obs.default_store().get(res.get("trace_id"))
    ok = trace is not None and res.get("trace_id")
    row("O1", "root spine", bool(ok), res.get("trace_id") or "no trace")
    if not ok:
        check("O1 root exists", False, "no trace id on the run")
        return
    d = trace.as_dict()
    roots = [s for s in d["spans"] if s["parent_span_id"] is None]
    check("exactly one root span", len(roots) == 1, f"{len(roots)} roots")
    check("every span shares the root trace id",
          all(s["span_id"].startswith(d["trace_id"][:10]) for s in d["spans"]))
    check("every parent path resolves", parent_paths_ok(trace))
    root = roots[0]
    check("root duration equals the trace duration",
          root["duration_ms"] == d["duration_ms"], str(d["duration_ms"]))
    children = sum(s["duration_ms"] for s in d["spans"] if s["parent_span_id"])
    check("root duration is NOT the sum of nested spans",
          children > root["duration_ms"],
          f"root={root['duration_ms']} sum_children={round(children, 3)}")
    check("step spans are children of the root",
          all(s["parent_span_id"] == root["span_id"] for s in spans(trace, "agent_step")))


def o2_rag_spine():
    print("\n== O2 — RAG spine: request → embed → retrieval → docs → chat → answer")
    gw, store = stub_rag_gateway()
    question = "how do I complete a checkout"
    res = run_rag(gw, store, question, k=5, budget_chars=220)
    trace = obs.default_store().get(res["trace_id"])
    ok = trace is not None
    row("O2", "RAG spine", ok, res.get("trace_id") or "no trace")
    if not ok:
        check("O2 trace exists", False)
        return
    names = [s["name"] for s in spans(trace)]
    check("has model.embed, retrieval, model.chat spans",
          find(trace, "model.embed") and find(trace, "retrieval")
          and find(trace, "model.chat"), str(names))
    docs = events(trace, "document")
    kept = [e for e in docs if e["attributes"].get("kept")]
    dropped = [e for e in docs if not e["attributes"].get("kept")]
    check("document events show kept AND dropped candidates",
          bool(kept) and bool(dropped), f"kept={len(kept)} dropped={len(dropped)}")
    check("document events carry id/rank/score/chars/hash",
          all(all(k in e["attributes"] for k in
                  ("doc_id", "rank", "score", "chars", "text_sha256"))
              for e in docs))
    blob = json.dumps(trace.as_dict())
    check("raw question text is absent by default", question not in blob)
    doc_text = store.chunks[0].text[:40]
    check("raw document text is absent by default", doc_text not in blob)
    fa = events(trace, "final_answer")
    check("final-answer event carries answer hash/size",
          fa and "answer_sha256" in fa[0]["attributes"]
          and "answer_chars" in fa[0]["attributes"])


def o3_agent_spine(reg, gw):
    print("\n== O3 — agent spine: steps, proposals, results, deferred")
    both(["tc_two", "answer_code"])
    res = run_agent("Where is checkout completed?", reg, gw, max_steps=5,
                    max_tool_calls_per_step=1)
    trace = obs.default_store().get(res.get("trace_id"))
    ok = trace is not None
    row("O3", "agent spine", ok, res.get("trace_id") or "no trace")
    if not ok:
        check("O3 trace exists", False)
        return
    step1 = spans(trace, "agent_step")
    check("at least one step span exists", bool(step1), str(len(step1)))
    calls = events(trace, "tool_call")
    check("proposals recorded (executed true)",
          any(e["attributes"].get("executed") for e in calls))
    check("deferred call recorded as NOT executed with a reason",
          any((not e["attributes"].get("executed"))
              and e["attributes"].get("reason") for e in calls),
          json.dumps([e["attributes"] for e in calls]))
    val = events(trace, "tool_validation")
    results = events(trace, "tool_result")
    check("validation and result events exist", bool(val) and bool(results))
    # every tool event lives on the step span it belongs to
    step_ids = {s["span_id"] for s in step1}
    check("every tool event is linked to a step span",
          all(e["span_id"] in step_ids for e in calls + val + results))
    check("model turn span is a child of the step",
          all(find(trace, "model.chat_with_tools")["parent_span_id"] in step_ids
              for _ in [0]))
    fa = events(trace, "final_answer")
    check("final_answer carries the termination", fa and
          fa[0]["attributes"].get("termination"))


def o4_model_lineage():
    print("\n== O4 — attempt lineage: retry siblings and provider fallback")
    both(["429", "ok"], retry_after=0.02)
    gw = make_gateway(only="openai")
    tr = obs.start_trace("chat")
    gw.chat([{"role": "user", "content": "hi"}])
    obs.finish_trace(tr)
    logical = find(tr, "model.chat")
    attempts = [s for s in spans(tr, "model") if s["name"] == "model.chat.attempt"]
    row("O4", "model lineage", len(attempts) == 2, f"{len(attempts)} attempts")
    check("retry creates sibling attempts under one logical call",
          len(attempts) == 2 and all(a["parent_span_id"] == logical["span_id"]
                                     for a in attempts))
    check("first attempt typed rate_limit",
          attempts[0]["status"] == obs.STATUS_ERROR
          and attempts[0]["attributes"].get("error_kind") == obs.ERR_RATE_LIMIT,
          str(attempts[0]["attributes"].get("error_kind")))
    check("second attempt ok with actual model + finish reason",
          attempts[1]["status"] == obs.STATUS_OK
          and attempts[1]["attributes"].get("actual_model")
          and attempts[1]["attributes"].get("finish_reason"))

    both(["401"], ["ok"])
    gw2 = make_gateway(only="both")
    tr2 = obs.start_trace("chat")
    gw2.chat([{"role": "user", "content": "hi"}])
    obs.finish_trace(tr2)
    provs = {s["attributes"].get("provider")
             for s in spans(tr2, "model") if s["name"] == "model.chat.attempt"}
    check("fallback names each provider attempted",
          {"openai", "anthropic"} <= provs, str(provs))
    check("the failing attempt is retained next to the success",
          any(s["status"] == obs.STATUS_ERROR for s in spans(tr2, "model")))


def o5_money():
    print("\n== O5 — money: trace totals equal the linked ledger delta")
    both(["429", "ok"], retry_after=0.02)
    gw = make_gateway(only="openai")
    start = len(gw.ledger.records)
    tr = obs.start_trace("chat")
    gw.chat([{"role": "user", "content": "hi"}])
    obs.finish_trace(tr)
    delta = gw.ledger.records[start:]
    tot = tr.totals()
    row("O5", "money reconciliation",
        tot["cost_usd"] == round(sum(r.cost_usd for r in delta), 8),
        f"${tot['cost_usd']}")
    check("trace tokens equal linked ledger rows",
          tot["tokens"] == sum(r.input_tokens + r.output_tokens for r in delta))
    check("trace cost equals linked ledger rows",
          tot["cost_usd"] == round(sum(r.cost_usd for r in delta), 8))
    check("retries follow ledger policy (failed attempt linked)",
          tot["records"] == 2 and tot["failed"] == 1, str(tot))

    provider = OpenAIProvider(api_key="sk-test", model="mystery-model",
                              base_url=OPENAI_URL, timeout_s=5.0)
    gw2 = ModelGateway([provider], max_attempts=1, timeout_s=5.0, ledger=Ledger())
    both(["ok"])
    tr2 = obs.start_trace("chat")
    gw2.chat([{"role": "user", "content": "hi"}])
    obs.finish_trace(tr2)
    check("unknown price is flagged, not silently free",
          tr2.totals()["price_status"] == "unknown",
          tr2.totals()["price_status"])


def o6_failure_closure(reg, gw, gw_doc, doc_store, gw_code, code_store):
    print("\n== O6 — typed errors close the right spans, ancestors preserved")
    # invalid key / bad request / timeout at the gateway
    cases = [(["401"], InvalidKeyError, "invalid_key"),
             (["400"], BadRequestError, "bad_request")]
    for script, exc_type, kind in cases:
        both(script)
        g = make_gateway(only="openai", max_attempts=1)
        tr = obs.start_trace("chat")
        try:
            g.chat([{"role": "user", "content": "hi"}])
            raised = None
        except Exception as e:  # noqa: BLE001
            raised = e
        obs.finish_trace(tr, status=obs.STATUS_ERROR)
        att = find(tr, "model.chat.attempt")
        check(f"{kind}: typed error, root closed, no open ok span",
              isinstance(raised, exc_type) and tr.closed
              and att["attributes"].get("error_kind") == kind
              and tr.status == obs.STATUS_ERROR,
              str(att and att["attributes"].get("error_kind")))
    # timeout
    both(["hang"])
    g = make_gateway(only="openai", max_attempts=1)
    g.timeout_s = 0.25
    for p in g.providers:
        p.timeout_s = 0.25
    tr = obs.start_trace("chat")
    try:
        g.chat([{"role": "user", "content": "hi"}])
    except Exception:  # noqa: BLE001
        pass
    obs.finish_trace(tr, status=obs.STATUS_TIMEOUT)
    att = find(tr, "model.chat.attempt")
    check("timeout closes with status=timeout",
          att["status"] == obs.STATUS_TIMEOUT and tr.status == obs.STATUS_TIMEOUT,
          att["status"])

    # dimension mismatch in RAG closes the trace
    class WrongDim:
        def __init__(self):
            self.ledger = Ledger()
            self.last_provider = "openai"
        def embed(self, inputs):
            from ai_os.contract import Embedding, Usage
            return [Embedding(text=inputs[0], vector=[0.1, 0.2, 0.3, 0.4],
                              model="wrong", usage=Usage(1, 0, 1))]
    _gw_stub, store = stub_rag_gateway()
    tr = obs.start_trace("rag")
    try:
        run_rag(WrongDim(), store, "how do I complete a checkout")
        raised = None
    except DimensionMismatch as e:
        raised = e
    obs.finish_trace(tr, status=obs.STATUS_ERROR)
    check("dimension mismatch is raised and the trace is closed",
          isinstance(raised, DimensionMismatch) and tr.closed and tr.status == obs.STATUS_ERROR)

    # a tool error inside the agent is an observation, not a crash
    broken_code = code_retriever(gw_code, code_store)

    def _boom(query, *, k=4, filter=None):
        raise RuntimeError("downstream resolver unavailable")

    broken_code.retrieve = _boom
    reg_broken = build_default_registry(
        doc=doc_retriever(gw_doc, doc_store), code=broken_code,
        doc_tau=0.05, code_tau=0.75)
    both(["tc_code", "answer_code"])
    res = run_agent("Where is checkout completed?", reg_broken, gw, max_steps=3)
    trace = obs.default_store().get(res.get("trace_id"))
    tres = events(trace, "tool_result")
    check("tool error is observed (ladder recovered), run still closes",
          res.get("observed_failures", 0) > 0 and trace.closed
          and any(e["attributes"].get("fallback_from") or e["attributes"].get("error_kind")
                  for e in tres),
          f"observed_failures={res.get('observed_failures')}")

    # budget stop
    both(["tc_code", "tc_code", "answer_code"])
    res = run_agent("Where is checkout completed?", reg_broken, gw, max_steps=5,
                    budget_usd=0.000001)
    trace = obs.default_store().get(res.get("trace_id"))
    check("budget stop closes the root and names the termination",
          trace.closed and res["termination_reason"] == "budget"
          and events(trace, "final_answer")[0]["attributes"]["termination"] == "budget")
    row("O6", "failure closure", True)


def o7_prompt_provenance(reg, gw):
    print("\n== O7 — prompt provenance: name/version/exact hash")
    gw_s, store = stub_rag_gateway()
    res = run_rag(gw_s, store, "how do I complete a checkout", k=4)
    trace = obs.default_store().get(res["trace_id"])
    chat_span = find(trace, "model.chat")
    pref = chat_span["attributes"].get("prompt")
    row("O7", "prompt provenance", bool(pref), json.dumps(pref))
    check("RAG model span carries name/version/hash",
          pref and pref["name"] == "rag-answer" and pref["version"] == RAG_PROMPT_REF.version
          and pref["sha256"] == obs.sha256_text(SYSTEM_PROMPT))
    check("no prompt body appears by default",
          "Answer ONLY from the context" not in json.dumps(trace.as_dict()))
    check("changing the exact template changes the hash",
          obs.PromptRef.of("rag-answer", "v1", SYSTEM_PROMPT + "x").sha256
          != RAG_PROMPT_REF.sha256)
    check("changing the version changes the provenance key",
          obs.PromptRef.of("rag-answer", "v2", SYSTEM_PROMPT).key != RAG_PROMPT_REF.key)

    both(["tc_code", "answer_code"])
    res = run_agent("Where is checkout completed?", reg, gw, max_steps=5)
    trace = obs.default_store().get(res["trace_id"])
    turn = find(trace, "model.chat_with_tools")
    check("agent reasoning turn carries agent-reasoning/v1",
          (turn["attributes"].get("prompt") or {}).get("name") == "agent-reasoning",
          json.dumps(turn["attributes"].get("prompt")))

    both(["tc_code", "tc_code", "answer_code"])
    res = run_agent("Where is checkout completed?", reg, gw, max_steps=1)
    trace = obs.default_store().get(res["trace_id"])
    forced = [s for s in spans(trace, "model")
              if (s["attributes"].get("prompt") or {}).get("name", "").startswith(
                  "agent-termination")]
    check("forced final turn carries its termination prompt ref",
          bool(forced), res.get("termination_reason"))


def o8_inspection_redaction():
    print("\n== O8 — lookup, unknown id, bounded retention, redaction, raw cap")
    tr = obs.start_trace("demo")
    tr.add_event("secret", api_key="sk-SECRET", authorization="Bearer abc123",
                 note="y" * 500)
    tr.finish()
    store = obs.default_store()
    check("lookup returns the same trace", store.get(tr.trace_id) is not None)
    check("unknown id fails cleanly (None)", store.get("tr-nope") is None)
    blob = json.dumps(store.get(tr.trace_id).as_dict())
    check("secrets redacted by key", "sk-SECRET" not in blob and "abc123" not in blob
          and "[redacted]" in blob)
    ev = events(tr, "secret")[0]["attributes"]
    check("over-long content becomes a hash + size",
          isinstance(ev["note"], dict) and "sha256" in ev["note"] and ev["note"]["chars"] == 500,
          str(ev["note"])[:60])

    # bounded retention, oldest-first
    original = obs.default_store()
    small = obs.TraceStore(max_traces=3)
    obs.set_default_store(small)
    ids = []
    for _ in range(5):
        t = obs.start_trace("demo")
        t.finish()
        ids.append(t.trace_id)
    kept = small.ids()
    obs.set_default_store(original)
    check("bounded store evicts oldest-first",
          len(small) == 3 and ids[0] not in kept and ids[-1] in kept,
          f"kept={len(kept)}")

    # raw debug: bounded capture, secrets still redacted
    obs.set_raw_debug(True, cap=8)
    t = obs.start_trace("demo")
    t.add_event("raw", long="z" * 50, api_key="sk-SECRET")
    t.finish()
    rawev = events(t, "raw")[0]["attributes"]
    obs.set_raw_debug(False)
    check("raw mode caps content and still redacts secrets",
          rawev["long"].get("prefix") == "z" * 8 and rawev["long"].get("truncated")
          and rawev["api_key"] == "[redacted]", str(rawev)[:80])


def o8b_stream(gw):
    print("\n== O8b — streaming is passed through; TTFT is measured, bytes unchanged")
    both(["ok"])
    tr = obs.start_trace("stream")
    traced = list(gw.stream([{"role": "user", "content": "hi"}]))
    obs.finish_trace(tr)
    both(["ok"])
    untraced = list(gw.stream([{"role": "user", "content": "hi"}]))
    # `ttft_ms` is the provider's own clocked field (it varies run to run); the
    # tracer must not change the *bytes* — same keys, same values elsewhere.
    def steady(chunks):
        return [{k: v for k, v in c.items() if k != "ttft_ms"} for c in chunks]
    check("stream chunks are passed through unchanged (timing field aside)",
          steady(traced) == steady(untraced)
          and [set(c) for c in traced] == [set(c) for c in untraced])
    attempt = find(tr, "model.stream.attempt")
    check("stream attempt span records ttft_ms and delivered chunks",
          attempt and attempt["attributes"].get("ttft_ms") is not None
          and attempt["attributes"].get("delivered", 0) > 0,
          str(attempt and attempt["attributes"].get("ttft_ms")))


def o9_control(reg, gw):
    print("\n== O9 — tracing off ≡ tracing on (only the id is additive)")
    gw_s, store = stub_rag_gateway()
    with obs.tracing(True):
        on = run_rag(gw_s, store, "how do I complete a checkout", k=4)
    with obs.tracing(False):
        off = run_rag(gw_s, store, "how do I complete a checkout", k=4)
    on_wo = {k: v for k, v in on.items() if k != "trace_id"}
    check("tracing off adds no trace_id", "trace_id" not in off)
    check("tracing on adds one trace_id", bool(on.get("trace_id")))
    check("answers, citations, retrieval and generation are identical",
          on_wo == off, "diff" if on_wo != off else "")
    row("O9", "control (off ≡ on)", on_wo == off and "trace_id" not in off)


# --------------------------------------------------------------------- reporting

def render_table() -> None:
    print("\n" + "=" * 92)
    print("   DAY 23 — TRACE FIXTURES (O1–O9)")
    print("=" * 92)
    print(f"{'ID':<6}{'what':<34}{'status':<8}detail")
    print("-" * 92)
    for r in ROWS:
        print(f"{r['id']:<6}{r['what']:<34}{'green' if r['ok'] else 'MOVED':<8}"
              f"{str(r['detail'])[:40]}")
    print("-" * 92)
    print("MECH: identity · parent paths · monotonic duration · ledger arithmetic · "
          "prompt hashes · lookup · retention · redaction · the O9 control.")
    print("PENDING: operational usefulness + attribute balance (design judgment); "
          "durable storage/export and cross-process propagation (Day 24).")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-stub", action="store_true",
                    help="assume the mock gateway is already running")
    args = ap.parse_args()

    proc = None
    if not (args.no_stub or _stub_up()):
        proc = subprocess.Popen([sys.executable, str(STUB)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            if _stub_up():
                break
            time.sleep(0.1)
    try:
        obs.set_tracing(True)
        reg, gw_doc, gw_code, doc_store, code_store = build_registry()
        gw = make_gateway(only="both", max_attempts=3)

        o1_root_spine(reg, gw)
        o2_rag_spine()
        o3_agent_spine(reg, gw)
        o4_model_lineage()
        o5_money()
        o6_failure_closure(reg, gw, gw_doc, doc_store, gw_code, code_store)
        o7_prompt_provenance(reg, gw)
        o8_inspection_redaction()
        o8b_stream(gw)
        o9_control(reg, gw)
    finally:
        if proc is not None:
            proc.terminate()

    render_table()
    print("\n" + ("ALL DAY 23 (MECH) CHECKS PASSED" if not FAILS
                  else f"{FAILS} CHECK(S) FAILED"))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
