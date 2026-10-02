#!/usr/bin/env python3
"""WS7 — trace sink, retention, metrics fixture (T1–T6), offline.

- T1 file sink roundtrip: put → get returns the doc
- T2 restart: a fresh TraceStore over the same sink resolves the old id
  (`GET /traces/{id}` works after restart)
- T3 retention: purge_before drops old rows, keeps fresh ones
- T4 per-route histogram: observe_route records route_latency_ms{route}
- T5 /metrics Prometheus exposition renders _sum/_count per route
- T6 local ring still evicts at 256 (cap kept for the fallback only)
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_os import metrics as metrics_mod  # noqa: E402
from ai_os import observability as obs  # noqa: E402
from ai_os.trace_sink import FileSink, run_retention  # noqa: E402

FAILS = 0


def check(label: str, cond: bool, detail: str = "") -> bool:
    global FAILS
    if not cond:
        FAILS += 1
    print(f"[{'OK  ' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))
    return bool(cond)


def _sample_trace(op: str = "t7"):
    return obs.start_trace(op, attributes={"route": "/t7", "component": "test"})


def t1_roundtrip(tmp: Path) -> str:
    sink = FileSink(str(tmp / "traces.jsonl"))
    tr = _sample_trace()
    obs.finish_trace(tr)
    sink.put(tr.trace_id, tr.as_dict())
    doc = sink.get(tr.trace_id)
    check("T1 file sink roundtrip (put → get)", isinstance(doc, dict)
          and doc.get("trace_id") == tr.trace_id, tr.trace_id[:12])
    check("T1 unknown id is None", sink.get("0" * 32) is None)
    return tr.trace_id


def t2_restart(tmp: Path, tid: str) -> None:
    sink = FileSink(str(tmp / "traces.jsonl"))
    fresh = obs.TraceStore(max_traces=256, sink=sink)
    hit = fresh.get(tid)
    check("T2 fresh store resolves the pre-restart id (sink fallback)",
          hit is not None and hit.as_dict().get("trace_id") == tid)


def t3_retention(tmp: Path) -> None:
    sink = FileSink(str(tmp / "ret.jsonl"))
    sink.put("old", {"trace_id": "old"})
    time.sleep(0.02)
    cutoff = time.time()  # after "old", before "new"
    time.sleep(0.02)
    sink.put("new", {"trace_id": "new"})
    removed = sink.purge_before(cutoff)
    check("T3 retention drops old rows, keeps fresh ones",
          removed == 1 and sink.get("new") is not None and sink.get("old") is None,
          f"removed={removed}")
    out = run_retention(sink=sink, days=30)
    check("T3 retention job reports purged count", out["purged"] == 0, str(out))


def t4_route_histogram() -> None:
    metrics_mod.reset()
    metrics_mod.observe_route("/rag", 12.5)
    metrics_mod.observe_route("/rag", 200.0)
    snap = metrics_mod.registry().snapshot()
    hists = [h for h in snap["histograms"] if h["name"] == "route_latency_ms"]
    check("T4 per-route histogram records route_latency_ms{route}",
          any(h["labels"].get("route") == "/rag" and h["count"] == 2 for h in hists))
    counts = [c for c in snap["counters"] if c["name"] == "route_requests_total"]
    check("T4 per-route request counter increments",
          any(c["labels"].get("route") == "/rag" and c["value"] == 2 for c in counts))


def t5_prometheus() -> None:
    text = metrics_mod.registry().render_prometheus()
    check("T5 Prometheus exposition renders _sum/_count per route",
          "route_latency_ms_sum" in text and "route_latency_ms_count" in text)
    # WS7 amendment: without `_bucket` lines `histogram_quantile()` in
    # prometheus/alerts.yaml has nothing to read — the p95 alert was dead.
    check("T5 renders cumulative _bucket lines incl. +Inf (p95 alerts readable)",
          "route_latency_ms_bucket" in text and 'le="+Inf"' in text)
    # t4 observed 12.5 (<=25) and 200 (<=250): cumulative count at le=250 = 2.
    # A per-bucket renderer would print 1 here, which is the bug this catches.
    check("T5 buckets are cumulative, not per-bucket counts",
          'le="250"} 2' in text,
          [ln for ln in text.splitlines() if 'le="250"' in ln])
    metrics_mod.set_gauge("trace_sink_last_write_epoch", 1727000000.0)
    text2 = metrics_mod.registry().render_prometheus()
    check("T5 gauges render as absolute values (SinkLag reads the epoch)",
          "trace_sink_last_write_epoch 1727000000.0" in text2
          or "trace_sink_last_write_epoch 1727000000" in text2,
          [ln for ln in text2.splitlines() if "epoch" in ln])



def t6_ring_cap() -> None:
    store = obs.TraceStore(max_traces=4)
    for _ in range(6):
        tr = _sample_trace("cap")
        obs.finish_trace(tr)
        store.put(tr)
    check("T6 local ring still evicts past the cap (fallback bound kept)",
          len(store) == 4 and store.evictions == 2, f"len={len(store)}")


def main() -> int:
    print("== WS7 — trace sink, retention, metrics (T1–T6) ==")
    with tempfile.TemporaryDirectory() as tmp:
        tid = t1_roundtrip(Path(tmp))
        t2_restart(Path(tmp), tid)
        t3_retention(Path(tmp))
    t4_route_histogram()
    t5_prometheus()
    t6_ring_cap()
    metrics_mod.reset()
    print("\n" + ("ALL TRACE-SINK CHECKS PASSED" if not FAILS else f"{FAILS} CHECK(S) FAILED"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
