"""Day 6 §3 — deterministic invariants of the Chunker (no network, no model).

Sweeps the 12 size × overlap combos from the day plan over synthetic docs
with *known* properties, and asserts the properties the plan demands:

- every original word survives into some chunk;
- no empty chunks; every chunk non-empty and terminates;
- overlap = 0 → adjacent chunks share nothing;
- overlap > 0 → the seam (tail of the previous chunk) is present in the next;
- `overlap >= size` is rejected eagerly (no infinite loop);
- unbroken tokens (URLs/base64) are capped by the grace bound.

    $ python test_chunk.py
"""
from __future__ import annotations

import re
import sys

sys.path.insert(0, ".")
from ai_os.chunk import chunk_text  # noqa: E402

FAILS = 0


def check(label: str, cond: bool, detail: str = ""):
    global FAILS
    if not cond:
        FAILS += 1
    print(f"[{'OK  ' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


def words(text: str) -> list[str]:
    return re.findall(r"\S+", text)


DOC = (
    "A checkout is created with checkoutCreate, then addresses, shipping "
    "method and email are updated before checkoutComplete turns it into an "
    "order. Variants carry inventory and pricing, so checkout accepts variant "
    "IDs, not products. Once a checkout exists, you can add lines, remove them, "
    "apply vouchers, and set a shipping method. When the checkout is complete, "
    "checkoutComplete validates everything and creates the order. The order then "
    "moves through fulfillment, which reserves stock and can trigger webhooks. "
    "Webhooks are how Saleor notifies external systems about order creation, "
    "payment, and fulfillment. " * 4
)

SIZES = [100, 200, 500]
OVERLAPS = [0, 10, 40, 99]


def sweep():
    for size in SIZES:
        for overlap in OVERLAPS:
            cs = chunk_text(DOC, size=size, overlap=overlap)
            label = f"size={size} ov={overlap}"
            tag = f"({label}: {len(cs)} chunks)"
            check(f"{label}: no empty chunks", all(c for c in cs), len(cs))
            ws = words(DOC)
            missing = [w for w in ws if not any(w in c for c in cs)]
            check(f"{label}: every original word survives into some chunk",
                  not missing, f"missing={missing[:3]}")
            if len(cs) > 1:
                if overlap == 0:
                    check(f"{label}: overlap=0 shares nothing",
                          not (cs[0][-overlap:].strip() and cs[0][-overlap:] in cs[1])
                          and not (cs[0][-39:] in cs[1] and cs[0][-39:].strip()),
                          "no shared tail expected")
                else:
                    check(f"{label}: seam re-served in the next chunk",
                          cs[0][-overlap:] in cs[1], f"{cs[0][-overlap:]!r}")


def edges():
    check("empty doc → []", chunk_text("") == [])
    check("doc shorter than size → one chunk",
          chunk_text("hello world", size=200) == ["hello world"])
    check("split boundary exactly → one chunk",
          chunk_text("x" * 200, size=200) == ["x" * 200])
    check("every chunk non-empty terminates", all(chunk_text(DOC, size=50, overlap=20)))


def guards():
    try:
        chunk_text(DOC, size=50, overlap=50)
        check("overlap >= size rejected", False)
    except ValueError:
        check("overlap >= size rejected", True)
    try:
        chunk_text(DOC, size=200, overlap=0, grace=-1)
        check("negative grace rejected", False)
    except ValueError:
        check("negative grace rejected", True)


def unbroken_token():
    big = "x" * 5000 + " done"
    cs = chunk_text(big, size=100, overlap=10, grace=50)
    check("unbroken token capped at size+grace",
          all(len(c) <= 150 for c in cs),
          f"max={max(len(c) for c in cs)}")
    check("unbroken token still chunked (no infinite loop)",
          len(cs) > 1 and cs[-1].endswith("done"))


def boundaries():
    # a fact straddling a boundary is present in BOTH neighbors
    cs = chunk_text(DOC, size=200, overlap=40)
    seam = cs[0][-40:]
    check("overlap 40: seam present in chunk 1", seam in cs[1])
    check("overlap 40: seam present in chunk 0", seam in cs[0])
    # and every chunk is a substring of the original (same surface)
    check("all chunks are substrings of the original",
          all(c in DOC for c in cs))


if __name__ == "__main__":
    sweep()
    boundaries()
    edges()
    guards()
    unbroken_token()
    print("\n" + ("ALL CHUNK CHECKS PASSED" if not FAILS else f"{FAILS} CHECK(S) FAILED"))
    sys.exit(1 if FAILS else 0)