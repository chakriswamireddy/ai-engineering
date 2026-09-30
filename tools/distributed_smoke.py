"""Day 24 §9 — the D1–D9 distributed-trace fixtures against the offline stubs.

    $ python tools/distributed_smoke.py

No API key. Two real processes are involved (this runner and the worker service
`ai_os/worker.py` on `:8710`), plus the scripted dual-provider stub on
`:8700`/`:8701`; the trace-context propagation is proven end-to-end, not asserted
in a docstring.

The suite proves the day's mechanics (`MECH`): the wire format, continuation,
outbound injection, idempotent/order-free/loss-aware assembly, gap honesty,
structured-log validity, metric arithmetic, cross-boundary money/latency — and
the **D9 control**: propagation, logging, and metrics on vs off change no
answer, no attempts, no SQL, no tokens, and no cost, and a collector that raises
changes nothing about the request.

`PENDING` (named, never declared solved): a production collector / log shipping /
metrics backend / scrape alerting; real sampling and retention policy; the
internals of Postgres, Saleor, and the providers; client-side spans; and
authentication on the ingest route (Day 26).
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
from ai_os.rag import run_rag  # noqa: E402
from ai_os import worker  # noqa: E402

import tools.rag_debug as _rd  # noqa: E402
from tools.tool_smoke import _stub_up, make_gateway  # noqa: E402

WORKER_PORT = 8710
DEAD_PORT = 8799

FAILS = 0
ROWS: list[dict] = []


def check(label: str, cond: bool, detail: str = "") -> bool:
    global FAILS
    if not cond:
        FAILS += 1
    print(f"[{'OK  ' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))
    return bool(cond)


def row(did: str, what: str, ok: bool, detail: str = "") -> None:
    ROWS.append({"id": did, "what": what, "ok": ok, "detail": detail})


# --------------------------------------------------------------- stub control

def _admin(port: int, provider: str, script, retry_after: float = 0.02) -> None:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/admin/reset",
        data=json.dumps({provider: script, "retry_after": retry_after}).encode(),
        method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 200


def both(openai, anthropic=None, *, retry_after: float = 0.02) -> None:
    _admin(8700, "openai", openai, retry_after)
    _admin(8701, "anthropic", anthropic or ["ok"], retry_after)


def captured(provider: str = "openai") -> dict:
    with urllib.request.urlopen("http://127.0.0.1:8700/admin/state", timeout=5) as r:
        return json.load(r).get("captured", {}).get(provider, {})


def _worker_up(port: int = WORKER_PORT) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as r:
            return r.status == 200
    except Exception:                                      # noqa: BLE001
        return False


# -------------------------------------------------------------- trace helpers

def spans(d: dict, kind: str | None = None) -> list[dict]:
    return [s for s in d["spans"] if kind is None or s["span_kind"] == kind]


def by_name(d: dict, name: str) -> dict | None:
    for s in d["spans"]:
        if s["name"] == name:
            return s
    return None


def parent_paths_resolve(d: dict) -> bool:
    ids = {s["span_id"] for s in d["spans"]}
    return all(s["parent_span_id"] is None or s["parent_span_id"] in ids
               for s in d["spans"])


def _read(trace_id: str) -> dict:
    obj = obs.default_store().get(trace_id)
    return obj.as_dict()


# ================================================================== D1

def d1_inbound_continuation():
    print("\n== D1 — inbound continuation: adopt the trace, or name why not")
    valid = "00-" + "a" * 32 + "-" + "b" * 16 + "-01"
    tr, ex = obs.start_server_trace("chat", {
        "traceparent": valid, "baggage": "tenant=acme,index=x,api_key=sk-S"})
    root = tr.as_dict()["spans"][0]
    check("a valid traceparent is adopted, not replaced",
          tr.trace_id == "a" * 32 and ex.reason == obs.REASON_CONTINUED)
    check("a fresh span id is minted under the caller's span",
          root["span_id"] != "b" * 16 and root["parent_span_id"] == "b" * 16)
    check("the continued root is a remote server edge",
          root["remote"] is True and root["span_kind"] == obs.SPAN_SERVER)
    check("forged baggage keys are dropped, not obeyed",
          ex.baggage == {"tenant": "acme"}, str(ex.baggage))
    tr.finish()

    cases = [({}, obs.REASON_NO_HEADER),
             ({"traceparent": "nonsense"}, obs.REASON_MALFORMED),
             ({"traceparent": "00-" + "0" * 32 + "-" + "0" * 16 + "-01"},
              obs.REASON_ALL_ZERO),
             ({"traceparent": "01-" + "c" * 32 + "-" + "d" * 16 + "-01"},
              obs.REASON_VERSION_UNSUPPORTED)]
    ok = True
    for carrier, reason in cases:
        t, e = obs.start_server_trace("chat", carrier)
        cont = t.as_dict().get("continuation", {})
        ok = ok and e.reason == reason and not cont.get("continued") \
            and cont.get("parent_span_id") is None
        t.finish()
    check("absent/malformed/all-zero/future-version each mint a fresh root "
          "with the reason recorded", ok)
    row("D1", "inbound continuation", ok and True)


# ================================================================== D2

def d2_outbound_injection():
    print("\n== D2 — every outbound call carries a parseable traceparent")
    both(["ok"])
    gw = make_gateway(only="openai")
    tr = obs.start_trace("chat")
    gw.chat([{"role": "user", "content": "hi"}])
    obs.finish_trace(tr)
    cap = captured("openai")
    ctx, reason = obs.TraceContext.parse(cap.get("traceparent"))
    check("the provider call carried a parseable traceparent",
          ctx is not None and reason == obs.REASON_CONTINUED, json.dumps(cap))
    check("its trace id is the request's trace id",
          ctx is not None and ctx.trace_id == tr.trace_id)
    check("only trace-context headers crossed (no key/token)",
          set(cap) <= {"traceparent", "baggage"}, str(set(cap)))

    asm_ok = True
    wres = None
    tr2 = obs.start_trace("request")
    asm = obs.default_collector().assembler(tr2.trace_id)
    wres = worker.dispatch("saleor_catalog", {"id": "1"}, port=WORKER_PORT, timeout=2.0)
    asm_ok = wres is not None
    obs.finish_trace(tr2)
    obs.finalize_request(tr2)
    if wres:
        wecho, wreason = obs.TraceContext.parse(wres["received_traceparent"])
        client = by_name(_read(tr2.trace_id), "worker.job")
        check("the worker echoes the header it received, same trace id",
              wecho is not None and wecho.trace_id == tr2.trace_id)
        check("the worker's remote parent is our client span",
              wres["parent_span_id"] == (client or {}).get("span_id"),
              f"{wres['parent_span_id']} vs {(client or {}).get('span_id')}")
    else:
        check("worker reachable", False, "worker not up")
    row("D2", "outbound injection", asm_ok)


# ================================================================== D3

def _rep(tid, sid, name, *, parent=None, dur=5.0, component="worker"):
    return obs.SpanReport(trace_id=tid, span={
        "span_id": sid, "parent_span_id": parent, "name": name, "kind": "tool",
        "span_kind": obs.SPAN_SERVER, "status": obs.STATUS_OK, "started_at": 1.0,
        "ended_at": 1.0 + dur / 1000, "duration_ms": dur, "attributes": {},
        "component": component, "events": []}, origin=component)


def d3_assembly():
    print("\n== D3 — assembly is idempotent, order-free, loss-aware")
    tid = "1" * 32
    asm = obs.TraceAssembler(tid)
    asm.ingest(_rep(tid, "kid", "saleor.graphql", parent="root", dur=40.0))
    asm.ingest(_rep(tid, "root", "worker.saleor_catalog", dur=10.0))
    d1 = asm.ingest(_rep(tid, "kid", "saleor.graphql", parent="root", dur=40.0))
    check("a duplicate report is counted, not double-counted",
          d1 == "duplicate" and asm.duplicates["kid"] == 1 and len(asm.spans) == 2)
    asm.finalize()
    d2 = asm.ingest(_rep(tid, "late", "extra"))
    check("a report after assembly is marked late", d2 == "late" and "late" in asm.late)
    check("the end-to-end number is the root, never the sum of hops",
          asm.duration_ms() == 10.0)
    check("assembly serializes to valid JSON", bool(json.dumps(asm.as_dict())))
    row("D3", "assembly", True)


# ================================================================== D4

def _build_gateway_over_stub():
    gw = make_gateway(only="openai")
    store = _rd.build_stub_store(gw)
    return gw, store


def _run_request(gw, store, *, jobs=(("saleor_catalog", {"id": "1"}),),
                 question="how do I create a checkout"):
    tr, ex = obs.start_server_trace("rag", {}, attributes={
        "route": "/rag", "component": "api"})
    asm = obs.default_collector().assembler(tr.trace_id)
    asm.expect("worker", "worker.saleor_catalog")
    asm.expect("worker", "saleor.graphql")
    res = run_rag(gw, store, question, k=4)
    wresults = []
    for job, payload in jobs:
        wres = worker.dispatch(job, payload, port=WORKER_PORT, timeout=2.0)
        wresults.append(wres)
        if wres:
            for rep in wres.get("reports", []):
                obs.submit_report(obs.SpanReport.from_dict(rep))
    obs.finish_trace(tr)
    obs.finalize_request(tr)
    return tr, ex, res, wresults


def d4_end_to_end_spine():
    print("\n== D4 — one request through the real chain is one tree")
    both(["ok"])
    gw, store = _build_gateway_over_stub()
    tr, ex, res, wresults = _run_request(gw, store)
    d = _read(tr.trace_id)
    roots = [s for s in d["spans"] if s["parent_span_id"] is None]
    ok = (len(roots) == 1 and parent_paths_resolve(d) and res.get("answer"))
    check("exactly one root, a server edge over the whole request",
          len(roots) == 1 and roots[0]["span_kind"] == obs.SPAN_SERVER,
          str(len(roots)))
    check("every parent path resolves (local + reported spans)",
          parent_paths_resolve(d))
    check("the retrieval hop is an internal span, not a fake client",
          (by_name(d, "retrieval") or {}).get("span_kind") == obs.SPAN_INTERNAL)
    attempt = by_name(d, "model.chat.attempt")
    check("the provider hop is a client span naming the remote host",
          attempt is not None and attempt["span_kind"] == obs.SPAN_CLIENT
          and attempt["remote"] is True and bool(attempt.get("remote_endpoint")),
          json.dumps(attempt and attempt.get("remote_endpoint")))
    check("the worker's server span is present and continues the trace",
          any(s["span_kind"] == obs.SPAN_SERVER and s.get("component") == "worker"
              for s in d["spans"]))
    check("the worker's Saleor hop is a client span under the worker span",
          (by_name(d, "saleor.graphql") or {}).get("span_kind") == obs.SPAN_CLIENT)
    check("components_seen names every component that did work",
          set(d["components_seen"]) >= {"api", "rag", "gateway", "worker"},
          str(d["components_seen"]))
    row("D4", "end-to-end spine", bool(ok), str(d["components_seen"]))


# ================================================================== D5

def d5_gap_honesty():
    print("\n== D5 — an absent hop is a named gap, never a fast success")
    both(["ok"])
    gw, store = _build_gateway_over_stub()
    # (a) a healthy request whose declared hops all arrive → complete
    tr, ex, res, wresults = _run_request(
        gw, store, jobs=(("saleor_catalog", {"id": "1"}),))
    # (b) a dead worker: the dispatch fails open, the expected hop is a gap
    tr2 = obs.start_trace("request")
    asm = obs.default_collector().assembler(tr2.trace_id)
    asm.expect("worker", "worker.saleor_catalog")
    missing = worker.dispatch("saleor_catalog", {"id": "1"}, port=DEAD_PORT, timeout=0.4)
    obs.finish_trace(tr2)
    obs.finalize_request(tr2)
    d2 = _read(tr2.trace_id)
    check("a dead worker makes dispatch fail open (None, not an exception)",
          missing is None)
    check("the missing hop is a named gap, not a silent short tree",
          d2["gap_count"] >= 1 and any(g["component"] == "worker"
                                       for g in d2["gaps"]), json.dumps(d2["gaps"]))
    check("the trace reports complete: false", d2["complete"] is False)
    check("no fabricated zero-duration success appears for the dead hop",
          not any(s["name"] == "worker.saleor_catalog" and s["status"] == obs.STATUS_OK
                  for s in d2["spans"]))

    # the healthy request is complete: true
    d1 = _read(tr.trace_id)
    check("a request whose declared hops all arrived reports complete: true",
          d1["complete"] is True and d1["gap_count"] == 0, str(d1["gaps"]))
    row("D5", "gap honesty", d2["complete"] is False and d1["complete"] is True)


# ================================================================== D6

def d6_structured_logs():
    print("\n== D6 — one JSON spine, ids that resolve, one grep by trace_id")
    from ai_os import structured_logging as slog
    slog.setup(level="info", ring_size=256, stream=False)
    ring = slog.get_ring()
    ring.clear()
    both(["429", "ok"])                                    # force a gateway retry line
    gw, store = _build_gateway_over_stub()
    tr, ex, res, wresults = _run_request(gw, store)
    d = _read(tr.trace_id)
    span_ids = {s["span_id"] for s in d["spans"]}
    lines = ring.lines(trace_id=tr.trace_id)
    check("grep by trace_id returns this request's lines", bool(lines), str(len(lines)))
    check("every line is one JSON object with the fixed spine",
          all(all(k in ln for k in slog.SPINE) for ln in lines))
    check("ids on log lines resolve to real spans in the tree",
          all(ln["span_id"] is None or ln["span_id"] in span_ids for ln in lines)
          and all(ln["trace_id"] == tr.trace_id for ln in lines))
    # the worker's own lines (a second process) share the trace id
    wlogs = (wresults[0] or {}).get("logs", []) if wresults else []
    components = ({ln["component"] for ln in lines}
                  | {ln["component"] for ln in wlogs})
    check("lines came from more than one component (across both processes)",
          len(components) >= 2, str(sorted(components)))
    check("redaction finds no forbidden keys in any line",
          slog.scan_for_secrets(lines) == [])
    check("the worker process's lines carry the same trace id",
          bool(wlogs) and all(ln["trace_id"] == tr.trace_id for ln in wlogs),
          f"{len(wlogs)} worker lines")
    check("the worker's log ids resolve in the assembled tree",
          all(ln["span_id"] is None or ln["span_id"] in span_ids for ln in wlogs))
    ring.clear()
    row("D6", "structured logs", bool(lines) and bool(wlogs))


# ================================================================== D7

def d7_metrics():
    print("\n== D7 — metrics derive from spans and agree with the trace")
    from ai_os import metrics as m
    both(["ok"])
    gw, store = _build_gateway_over_stub()
    m.reset()
    res = run_rag(gw, store, "how do I create a checkout", k=4)
    trace = obs.default_store().get(res["trace_id"])
    snap = m.registry().snapshot()
    merged = m.derive_from_trace(trace)
    check("counters derive from span closes (spans_total == span count)",
          m.registry().counter_total("spans_total") == merged["spans"],
          f'{m.registry().counter_total("spans_total")} vs {merged["spans"]}')
    check("ledger totals reconcile with the trace for the same window",
          m.registry().counter_total("ledger_tokens_total") == merged["tokens"],
          f'{m.registry().counter_total("ledger_tokens_total")} vs {merged["tokens"]}')
    hist = [h for h in snap["histograms"] if h["name"] == "span_duration_ms"]
    check("latency histograms use declared fixed buckets",
          bool(hist) and "+Inf" in hist[0]["buckets"])
    rejected = all(_raises(m.check_labels, {k: "x"})
                   for k in ("trace_id", "user", "question", "sql"))
    check("the label allow-list rejects trace_id / user / question / SQL", rejected)
    check("the snapshot states that metrics cannot identify an instance",
          "cannot name an instance" in snap["note"])
    m.reset()
    row("D7", "metrics", True)


def _raises(fn, *a, **k) -> bool:
    try:
        fn(*a, **k)
        return False
    except Exception:                                      # noqa: BLE001
        return True


# ================================================================== D8

def d8_money_latency():
    print("\n== D8 — cross-boundary money is exact; latency is the critical path")
    both(["429", "ok"])
    gw = make_gateway(only="openai")
    start = len(gw.ledger.records)
    tr = obs.start_trace("chat")
    gw.chat([{"role": "user", "content": "hi"}])
    obs.finish_trace(tr)
    delta = gw.ledger.records[start:]
    tot = tr.totals()
    check("tokens equal the exact linked ledger delta across attempts",
          tot["tokens"] == sum(r.input_tokens + r.output_tokens for r in delta))
    check("cost equals the exact linked ledger delta",
          tot["cost_usd"] == round(sum(r.cost_usd for r in delta), 8))
    check("the failed retry is linked, not forgotten", tot["records"] == 2)

    # a real cross-boundary request: client span + worker server span both exist,
    # and the end-to-end number is the root — never their sum.
    both(["ok"])
    gw2, store = _build_gateway_over_stub()
    tr2, ex, res, _ = _run_request(gw2, store)
    d = _read(tr2.trace_id)
    hop_sum = round(sum(s["duration_ms"] or 0 for s in d["spans"]), 3)
    root = [s for s in d["spans"] if s["parent_span_id"] is None][0]
    check("the client/server pair is not summed into the end-to-end number",
          d["duration_ms"] == root["duration_ms"] and d["duration_ms"] != hop_sum,
          f"root={d['duration_ms']} sum_hops={hop_sum}")

    both(["ok"])
    gw3 = make_gateway(only="openai")
    trs = obs.start_trace("stream")
    list(gw3.stream([{"role": "user", "content": "hi"}]))
    obs.finish_trace(trs)
    att = by_name(trs.as_dict(), "model.stream.attempt")
    check("TTFT is present for streams",
          att is not None and att["attributes"].get("ttft_ms") is not None)
    row("D8", "cross-boundary money/latency", True)


# ================================================================== D9

class _ExplodingCollector(obs.Collector):
    def submit(self, report):                              # noqa: D401
        raise RuntimeError("collector is down")


def _stable(result: dict) -> dict:
    """A run result with wall-clock fields removed (Latency is not part of D9)."""
    out = {k: v for k, v in result.items()
           if k not in ("trace_id", "continuation_reason")}
    if isinstance(out.get("generation"), dict):
        out["generation"] = {k: v for k, v in out["generation"].items()
                             if k != "latency_ms"}
    return out


def d9_control():
    print("\n== D9 — telemetry on/off is behaviourally and financially identical")
    both(["ok"])
    gw, store = _build_gateway_over_stub()
    with obs.tracing(True):
        on = run_rag(gw, store, "how do I create a checkout", k=4)
    with obs.tracing(False):
        off = run_rag(gw, store, "how do I create a checkout", k=4)
    check("tracing off adds no trace id", "trace_id" not in off)
    check("answers, citations, retrieval and generation are identical "
          "(wall-clock latency aside)",
          _stable(on) == _stable(off))

    # off means genuinely off: no header is injected even while a trace runs
    both(["ok"])
    captured("openai")
    with obs.tracing(False):
        t = obs.start_trace("chat")
        gw.chat([{"role": "user", "content": "hi"}])
        obs.finish_trace(t)
    check("tracing off injects no provider header",
          "traceparent" not in captured("openai"))

    # a collector that raises changes nothing about the request
    both(["ok"])
    gw4, store4 = _build_gateway_over_stub()
    original = obs.default_collector()
    obs.set_default_collector(_ExplodingCollector(store=obs.TraceStore(max_traces=4)))
    try:
        tr, ex, res, wres = _run_request(gw4, store4)
        survived = bool(res.get("answer"))
    except Exception as e:                                 # noqa: BLE001
        survived = False
        print(f"  collector raise escaped: {e}")
    finally:
        obs.set_default_collector(original)
    check("a collector that raises changes nothing about the request", survived)
    row("D9", "control (off ≡ on)", survived and _stable(on) == _stable(off))


# ================================================================== runner

def render():
    print("\n" + "=" * 92)
    print("   DAY 24 — DISTRIBUTED TRACE FIXTURES (D1–D9)")
    print("=" * 92)
    print(f"{'ID':<6}{'what':<34}{'status':<8}detail")
    print("-" * 92)
    for r in ROWS:
        print(f"{r['id']:<6}{r['what']:<34}{'green' if r['ok'] else 'MOVED':<8}"
              f"{str(r['detail'])[:40]}")
    print("-" * 92)
    print("MECH: wire format · continuation · injection · idempotent assembly · "
          "gap honesty · log validity · metric arithmetic · money/latency · D9.")
    print("PENDING: production collector/shipping/backend · sampling policy · remote "
          "internals · client-side spans · ingest auth (Day 26).")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-stub", action="store_true",
                    help="assume the mock gateway and worker are already running")
    args = ap.parse_args()

    stub_proc = worker_proc = None
    if not (args.no_stub or _stub_up()):
        stub_proc = subprocess.Popen([sys.executable, str(STUB)],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            if _stub_up():
                break
            time.sleep(0.1)
    if not (args.no_stub or _worker_up()):
        worker_proc = subprocess.Popen(
            [sys.executable, "-m", "ai_os.worker", "--port", str(WORKER_PORT)],
            cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            if _worker_up():
                break
            time.sleep(0.1)
    try:
        obs.set_tracing(True)
        obs.set_propagation(True)
        d1_inbound_continuation()
        d2_outbound_injection()
        d3_assembly()
        d4_end_to_end_spine()
        d5_gap_honesty()
        d6_structured_logs()
        d7_metrics()
        d8_money_latency()
        d9_control()
    finally:
        for proc in (worker_proc, stub_proc):
            if proc is not None:
                proc.terminate()

    render()
    print("\n" + ("ALL DAY 24 (MECH) CHECKS PASSED" if not FAILS
                  else f"{FAILS} CHECK(S) FAILED"))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
