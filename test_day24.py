"""Day 24 §9 — deterministic unit suite for the crossing primitives.

    $ python test_day24.py

No API key, no network, no processes: pure mechanics. The *distributed* fixtures
(two real processes, one trace id) live in `tools/distributed_smoke.py`; this is
the arithmetic underneath them —

- the W3C `traceparent` grammar (parse/format/validate, the invalid-vs-unusual
  cases), continuation vs a fresh root with a recorded reason;
- baggage as bounded, allow-listed, non-secret, non-authoritative data;
- the assembler: idempotent on `span_id`, order-free, duplicates counted, late
  arrivals recorded, missing children materialized as named gaps;
- the collector's fail-open contract;
- the metrics label allow-list and fixed buckets;
- the structured-log spine and its pre-serialization redaction.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from ai_os import observability as obs  # noqa: E402

FAILS = 0


def check(label: str, cond: bool, detail: str = "") -> bool:
    global FAILS
    if not cond:
        FAILS += 1
        # gate_prod leg 3 is `pytest -q`: a failed check must fail the run
        # there, while as a script this keeps printing and reports at the end.
        if os.environ.get("PYTEST_CURRENT_TEST"):
            raise AssertionError(label + (f"  ({detail})" if detail else ""))
    print(f"[{'OK  ' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))
    return bool(cond)


# ------------------------------------------------------------- the wire format

def test_traceparent() -> None:
    print("\n== the W3C traceparent grammar")
    header = ("00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01")
    ctx, reason = obs.TraceContext.parse(header)
    check("a valid header parses and continues",
          ctx is not None and reason == obs.REASON_CONTINUED)
    check("trace id is 32 hex, span id is 16 hex",
          len(ctx.trace_id) == 32 and len(ctx.span_id) == 16)
    check("it formats back to the same bytes", ctx.as_traceparent() == header)
    check("the sampled flag is read from the low bit",
          ctx.sampled and not obs.TraceContext.parse(
              header[:-2] + "00")[0].sampled)

    check("absent -> no_header",
          obs.TraceContext.parse(None)[1] == obs.REASON_NO_HEADER
          and obs.TraceContext.parse("   ")[1] == obs.REASON_NO_HEADER)
    check("wrong width / non-hex -> malformed",
          obs.TraceContext.parse("00-abc-def-01")[1] == obs.REASON_MALFORMED)
    check("uppercase hex is illegal -> malformed",
          obs.TraceContext.parse(header.upper())[1] == obs.REASON_MALFORMED)
    check("all-zero ids -> all_zero",
          obs.TraceContext.parse(
              "00-" + "0" * 32 + "-" + "0" * 16 + "-01")[1] == obs.REASON_ALL_ZERO)
    check("a well-formed future version -> version_unsupported",
          obs.TraceContext.parse("01-" + "a" * 32 + "-" + "b" * 16 + "-01")[1]
          == obs.REASON_VERSION_UNSUPPORTED)

    child = ctx.child()
    check("a child keeps the trace id and mints a fresh span id",
          child.trace_id == ctx.trace_id and child.span_id != ctx.span_id)
    check("remote_child is a fresh span under the same trace",
          ctx.remote_child().trace_id == ctx.trace_id)


# ------------------------------------------------------------------ baggage

def test_baggage() -> None:
    print("\n== baggage is bounded, allow-listed, non-secret data")
    carrier = {"traceparent": "00-" + "a" * 32 + "-" + "b" * 16 + "-01",
               "baggage": "tenant=acme,surface=cli,index=users,api_key=sk-SECRET,"
                          "request_kind=chat"}
    res = obs.extract(carrier)
    check("only allow-listed keys survive",
          set(res.baggage) == {"tenant", "surface", "request_kind"},
          str(res.baggage))
    check("a forged selection key (index) is dropped", "index" not in res.baggage)
    check("a secret-bearing key (api_key) is dropped", "api_key" not in res.baggage)
    long_bag = obs.extract({"baggage": "tenant=" + "x" * 500})
    check("values are length-capped",
          len(long_bag.baggage["tenant"]) <= obs.BAGGAGE_MAX_VALUE)
    check("format round-trips through parse",
          obs.extract({"baggage": obs.format_baggage(res.baggage)}).baggage
          == res.baggage)


# -------------------------------------------------------- propagation switches

def test_inject_extract() -> None:
    print("\n== inject / extract and the off switch")
    ctx = obs.TraceContext.new_root()
    headers = obs.inject({}, ctx, {"tenant": "acme"})
    check("inject writes a parseable traceparent",
          obs.TraceContext.parse(headers["traceparent"])[0].trace_id == ctx.trace_id)
    check("inject carries only allow-listed baggage",
          headers.get("baggage") == "tenant=acme")
    check("extract reconstructs the same identity",
          obs.extract(headers).context.trace_id == ctx.trace_id)

    with obs.propagation(False):
        check("propagation off injects nothing", obs.inject({}, ctx) == {})
        check("propagation off yields no outbound headers",
              obs.outbound_headers() == {})


# ----------------------------------------------------------- the assembler

def _report(trace_id: str, span_id: str, name: str, *, parent=None,
            duration=5.0, component="worker") -> obs.SpanReport:
    return obs.SpanReport(trace_id=trace_id, span={
        "span_id": span_id, "parent_span_id": parent, "name": name,
        "kind": "tool", "span_kind": obs.SPAN_SERVER, "status": obs.STATUS_OK,
        "started_at": 1.0, "ended_at": 1.0 + duration / 1000,
        "duration_ms": duration, "attributes": {}, "component": component,
        "events": []}, origin=component)


def test_assembler() -> None:
    print("\n== assembly: idempotent, order-free, loss-aware")
    tid = "c" * 32
    asm = obs.TraceAssembler(tid)
    asm.expect("worker", "saleor.graphql")
    # child arrives BEFORE its parent (order-free)
    check("a child before its parent still lands",
          asm.ingest(_report(tid, "child1", "saleor.graphql", parent="root1"))
          == "delivered")
    check("the parent then lands", asm.ingest(
        _report(tid, "root1", "worker.saleor_catalog", component="worker"))
        == "delivered")
    # duplicate is counted, not merged twice
    check("a duplicate report is counted",
          asm.ingest(_report(tid, "child1", "saleor.graphql",
                             parent="root1")) == "duplicate")
    check("duplicates never inflate the span count", len(asm.spans) == 2)
    # late arrival, after finalize
    asm.finalize()
    check("a report after finalize is marked late",
          asm.ingest(_report(tid, "late1", "extra")) == "late" and "late1" in asm.late)
    check("assembly completes when every declared child arrived",
          asm.complete() is True and asm.gaps() == [])
    # a declared-but-absent child becomes a named gap
    asm2 = obs.TraceAssembler("d" * 32)
    asm2.expect("worker", "saleor.graphql")
    asm2.ingest(_report("d" * 32, "r", "worker.saleor_catalog"))
    asm2.finalize()
    gaps = asm2.gaps()
    check("a missing declared child is a named gap",
          len(gaps) == 1 and gaps[0]["component"] == "worker"
          and gaps[0]["span"] == "saleor.graphql"
          and gaps[0]["reason"] == "never_arrived", json.dumps(gaps))
    check("a gap makes the trace incomplete",
          asm2.complete() is False and asm2.as_dict()["complete"] is False)

    # no gaps + all closed -> complete, and duration is the root's, not the sum
    asm3 = obs.TraceAssembler("e" * 32)
    asm3.ingest(_report("e" * 32, "root", "op", duration=10.0))
    asm3.ingest(_report("e" * 32, "kid", "hop", parent="root", duration=40.0))
    asm3.finalize()
    check("complete only when nothing is missing", asm3.complete() is True)
    check("end-to-end duration is the root, never the sum of hops",
          asm3.duration_ms() == 10.0, str(asm3.duration_ms()))


def test_collector_fail_open() -> None:
    print("\n== the collector is fail-open")
    col = obs.Collector(store=obs.TraceStore(max_traces=4))
    status = col.submit(_report("f" * 32, "s1", "op"))
    check("a healthy submit is delivered", status == "delivered")

    # Sabotage the *internal* path (not the wrapper): the fail-open contract is
    # that `submit` swallows a raising assembler and reports failure by value.
    def boom(*_a, **_k):
        raise RuntimeError("collector down")
    col.assembler = boom  # type: ignore[assignment]
    status = col.submit(_report("f" * 32, "s2", "op"))
    check("a raising internal path returns None instead of raising",
          status is None and col.failures >= 1, f"failures={col.failures}")


# ------------------------------------------------------------- metrics / logs

def test_metrics() -> None:
    print("\n== metrics: bounded labels, fixed buckets, span-close derivation")
    from ai_os import metrics as m
    m.reset()
    # drive one span through the hook
    tr = obs.start_trace("unit")
    sp = tr.start_span(obs.KIND_MODEL, "model.chat", span_kind=obs.SPAN_CLIENT)
    obs.end_span(sp, status=obs.STATUS_OK)
    tr.finish()
    snap = m.registry().snapshot()
    total = sum(c["value"] for c in snap["counters"] if c["name"] == "spans_total")
    check("counters derive from span closes",
          total == len(tr.spans), f"spans_total={total} spans={len(tr.spans)}")
    hist = [h for h in snap["histograms"] if h["name"] == "span_duration_ms"]
    check("latency histograms use declared fixed buckets",
          bool(hist) and "+Inf" in hist[0]["buckets"])
    for bad in ("trace_id", "user", "question", "sql"):
        raised = False
        try:
            m.check_labels({bad: "x"})
        except m.LabelError:
            raised = True
        check(f"label allow-list rejects {bad!r}", raised)
    check("an unknown (uncatalogued) label is also rejected",
          _raises(m.check_labels, {"whatever": "x"}))
    m.reset()
    check("reset clears the registry",
          m.registry().snapshot()["series"] == 0)


def _raises(fn, *a, **k) -> bool:
    try:
        fn(*a, **k)
        return False
    except Exception:                                      # noqa: BLE001
        return True


def test_structured_logging() -> None:
    print("\n== structured logging: fixed spine, redaction before serialization")
    from ai_os import structured_logging as slog
    ring = slog.setup(level="info", ring_size=32)
    ring.clear()
    tr = obs.start_trace("unit")
    slog.emit("gateway", "chat_ok", "chat ok", provider="openai",
              api_key="sk-SECRET", sql="SELECT password FROM users",
              prompt="secret prompt", latency_ms=12.0)
    tr.finish()
    lines = ring.lines(trace_id=tr.trace_id)
    check("a line lands in the ring bound to the active trace", bool(lines))
    line = lines[0]
    check("every spine key is present", all(k in line for k in slog.SPINE),
          str(sorted(line)))
    check("forbidden keys are dropped before serialization",
          "api_key" not in line and "sql" not in line and "prompt" not in line)
    check("safe attributes survive", line.get("provider") == "openai")
    check("a generated line is valid JSON",
          isinstance(json.loads(json.dumps(line)), dict))
    # a line outside any request has no trace id (legitimate for boot/cron)
    slog.emit("boot", "startup", "app started")
    boot = [r for r in ring.lines() if r["event"] == "startup"][0]
    check("a line outside a request carries no trace id", boot["trace_id"] is None)
    ring.clear()


def main() -> None:
    obs.set_tracing(True)
    test_traceparent()
    test_baggage()
    test_inject_extract()
    test_assembler()
    test_collector_fail_open()
    test_metrics()
    test_structured_logging()
    print("\n" + ("ALL DAY 24 (MECH) UNIT CHECKS PASSED" if not FAILS
                  else f"{FAILS} CHECK(S) FAILED"))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
