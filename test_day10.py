"""Day 10 — deterministic tests for code ingestion + symbol retrieval (`MECH`).

Day 10's claims, per the notes: the corpus changed, the system did not. Symbol
extraction is a pure function of source text (AST); the split test ("no chunk
starts mid-`def`") is a structural assertion; `retrieve` over code is arithmetic
on stored vectors and identifier tokens; `cited ⊆ retrieved` is set membership;
and the *same* harness proves the code fixtures the way it proved the doc nine.
Nothing here needs a model or a network:

    $ python test_day10.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from ai_os.contract import Embedding, Usage  # noqa: E402
from ai_os.fixtures import check_fixture  # noqa: E402
from ai_os.gate import should_refuse  # noqa: E402
from ai_os.ingest_code import (  # noqa: E402
    ingest_code,
    interface_text,
    symbols_from_source,
)
from ai_os.rag import Chunk, RagStore, citation  # noqa: E402
from ai_os.retrieval import (  # noqa: E402
    CODE_REWRITES,
    Retriever,
    code_tokens,
    rewrite,
)
import tools.code_debug as _cd  # noqa: E402
import tools.rag_debug as _rd  # noqa: E402
from tools.build_code_store import build_code_store  # noqa: E402
from tools.retrieval_day10 import (  # noqa: E402
    code_pipeline,
    run_code_all,
    run_doc_all,
)

FAILS = 0


def check(label: str, cond: bool, detail: str = ""):
    global FAILS
    if not cond:
        FAILS += 1
    print(f"[{'OK  ' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


def near(a: float, b: float, eps: float = 1e-6) -> bool:
    return abs(a - b) < eps


class RecordingGateway:
    """Wraps the hash gateway and counts every embed/chat call (the ledger)."""

    def __init__(self, inner):
        self.inner = inner
        self.embed_calls = 0
        self.chat_calls = 0

    def embed(self, texts):
        self.embed_calls += 1
        return self.inner.embed(texts)

    def chat(self, *a, **k):
        self.chat_calls += 1
        return self.inner.chat(*a, **k)


# ------------------------------------------------------------ tokenizer

def tokenizer_tests():
    print("\n== retrieval.py — code_tokens (identifier tokenization, MECH)")
    check("camelCase splits: checkoutComplete → {checkout, complete}",
          set(code_tokens("checkoutComplete")) == {"checkout", "complete"},
          str(code_tokens("checkoutComplete")))
    check("snake_case splits: complete_checkout → {complete, checkout}",
          set(code_tokens("complete_checkout")) == {"complete", "checkout"})
    check("PascalCase splits: CheckoutComplete → {checkout, complete}",
          set(code_tokens("CheckoutComplete")) == {"checkout", "complete"})
    check("the phrase collides with both spellings (the whole point)",
          set(code_tokens("complete checkout"))
          == set(code_tokens("checkoutComplete"))
          == set(code_tokens("complete_checkout")))
    check("stopwords dropped in code too", code_tokens("where is the order") == ["order"])
    check("single chars dropped", code_tokens("a b c") == [])
    check("digits survive", "v2" in code_tokens("checkout v2 api"))
    check("generic 3-char verbs are kept (MIN_DISCRIMINANT_LEN is a rerank rule)",
          "set" in code_tokens("set email"))

    check("CODE_REWRITES: natural phrase → identifiers",
          "complete_checkout" in rewrite("checkout complete", CODE_REWRITES)
          and "CheckoutComplete" in rewrite("checkout complete", CODE_REWRITES))
    check("CODE_REWRITES fires inside a longer question (CA1's phrasing)",
          rewrite("Where is checkout completed?", CODE_REWRITES)
          == "complete_checkout CheckoutComplete")
    check("no rule → pass-through",
          rewrite("Which module fires the webhook?", CODE_REWRITES)
          == "Which module fires the webhook?")


# ------------------------------------------------------------ extractor

_SRC = '''"""A module docstring."""
from saleor.order.utils import create_order_from_checkout
import os


def complete_checkout(checkout, payment_data) -> Order:
    """Complete a checkout into an order."""
    order = create_order_from_checkout(checkout)
    return order


class CheckoutComplete(BaseMutation):
    """A GraphQL mutation."""

    def perform_mutation(self, token):
        return complete_checkout(token)


TIMEOUT = 30
'''


def extractor_tests():
    print("\n== ingest_code.py — the AST symbol extractor (MECH)")
    syms = symbols_from_source(_SRC, "saleor/checkout/complete_checkout.py")
    by = {s.symbol: s for s in syms}
    check("every definition becomes a symbol",
          {"complete_checkout", "CheckoutComplete",
           "CheckoutComplete.perform_mutation", "module"} <= set(by),
          str(sorted(by)))
    fn = by["complete_checkout"]
    check("kind: function", fn.kind == "function")
    check("signature carries args + return hint",
          fn.signature == "def complete_checkout(checkout, payment_data) -> Order",
          fn.signature)
    check("docstring captured", fn.docstring == "Complete a checkout into an order.")
    check("imports captured (module-level, dotted)",
          "saleor.order.utils.create_order_from_checkout" in fn.imports
          and "os" in fn.imports, str(fn.imports))
    check("dependencies: called name + imported leaf",
          "create_order_from_checkout" in fn.dependencies, str(fn.dependencies))
    check("body slice is the source lines (signature..last)",
          fn.body.startswith("def complete_checkout")
          and "return order" in fn.body)
    check("line span is real source positions",
          fn.first_line == 6 and fn.last_line >= fn.first_line, str((fn.first_line, fn.last_line)))

    cls = by["CheckoutComplete"]
    check("kind: mutation (class whose base names a Mutation)", cls.kind == "mutation",
          cls.kind)
    check("method qualname is Class.method",
          by["CheckoutComplete.perform_mutation"].kind == "method")

    run = by["module"]
    check("top-level statement run grouped as kind=module",
          run.kind == "module" and "TIMEOUT = 30" in run.body)

    # the split test: no chunk starts mid-definition.
    bad = [s.symbol for s in syms if s.kind != "module"
           and not s.body.lstrip().startswith(("def ", "async def ", "class "))]
    check("split test: no symbol chunk starts mid-def", not bad, str(bad))

    tst = symbols_from_source(_SRC, "saleor/checkout/tests/test_x.py")
    check("test module → definitions are kind=test (module run stays module)",
          {s.kind for s in tst if s.kind != "module"} == {"test"},
          str({s.kind for s in tst}))

    big = symbols_from_source("x = 1\n" * 9000, "saleor/generated.py")
    check("a single oversized chunk → windowed fallback (kind=module, window ids)",
          big and all(s.kind == "module" and s.symbol.startswith("window")
                      for s in big), f"n={len(big)}")
    broken = symbols_from_source("def :\n  ??", "saleor/broken.py")
    check("unparseable file → windowed fallback (never a crash)",
          broken and broken[0].kind == "module")

    # the bound is per-*chunk*, not per-file: a big module keeps its symbols.
    many = "\n".join(f"def f{i}():\n    return {i}\n" for i in range(60))
    many_syms = symbols_from_source(many, "saleor/many.py")
    check("many defs (>40) → still one chunk per symbol (bound is per-chunk)",
          sum(1 for s in many_syms if s.kind == "function") == 60,
          f"n={sum(1 for s in many_syms if s.kind == 'function')}")

    huge = "def big():\n" + "\n".join(f"    x{i} = {i}" for i in range(5000)) + "\n"
    huge_syms = symbols_from_source(huge, "saleor/huge.py")
    check("one symbol whose *own* body overflows → windows tagged with the symbol",
          huge_syms and all(s.kind == "module"
                            and s.symbol.startswith("big#window")
                            for s in huge_syms), f"n={len(huge_syms)}")

    nested = ("class Outer:\n    class Inner:\n        def m(self):\n"
              "            return 1\n")
    nids = {s.doc_id for s in symbols_from_source(nested, "saleor/n.py")}
    check("nested classes carry their scope (Outer.Inner, Outer.Inner.m)",
          {"saleor/n.py#Outer", "saleor/n.py#Outer.Inner",
           "saleor/n.py#Outer.Inner.m"} <= nids, str(sorted(nids)))

    dup = ("class A:\n    class Meta:\n        pass\n"
           "class B:\n    class Meta:\n        pass\n")
    dids = [s.doc_id for s in symbols_from_source(dup, "saleor/d.py")]
    check("same-named inner classes get distinct ids (A.Meta != B.Meta)",
          "saleor/d.py#A.Meta" in dids and "saleor/d.py#B.Meta" in dids
          and len(dids) == len(set(dids)), str(dids))

    check("interface text excludes the body, keeps signature + docstring",
          "return order" not in interface_text(fn)
          and "def complete_checkout" in interface_text(fn)
          and "Complete a checkout" in interface_text(fn))
    check("module-window interface is the body itself (must stay embeddable)",
          interface_text(big[0]) == big[0].body)


def ingest_dir_tests(tmp: Path):
    print("\n== ingest_code() over a directory (MECH)")
    (tmp / "pkg").mkdir(parents=True)
    (tmp / "pkg" / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (tmp / "pkg" / "b.py").write_text("class C:\n    pass\n", encoding="utf-8")
    (tmp / "pkg" / "__pycache__").mkdir()
    (tmp / "pkg" / "__pycache__" / "c.py").write_text("def hidden(): pass\n",
                                                      encoding="utf-8")
    syms = ingest_code(tmp)
    ids = {s.doc_id for s in syms}
    check("walks .py files, module path is the relative posix path",
          "pkg/a.py#f" in ids and "pkg/b.py#C" in ids, str(sorted(ids)))
    check("__pycache__ is skipped", not any("hidden" in i for i in ids))


# ------------------------------------------------------------ chunk metadata

def metadata_tests(tmp: Path):
    print("\n== rag.py — code metadata rides the Chunk without a dialect (MECH)")
    gw = _cd.HashEmbedGateway()
    store = _cd.build_stub_code_store(gw)
    store.save(tmp / "code.json")
    loaded = RagStore.load(tmp / "code.json")
    check("load round-trips code fields",
          loaded.chunks[0].symbol is not None
          and loaded.chunks[0].kind is not None
          and isinstance(loaded.chunks[0].imports, list))
    sym = next(c for c in loaded.chunks
               if c.doc_id == "saleor/checkout/complete_checkout.py#complete_checkout")
    check("doc_id is module#Symbol", sym.doc_id.endswith("#complete_checkout"))
    check("body rides as text; signature stored separately",
          sym.text.startswith("def complete_checkout")
          and sym.signature.startswith("def complete_checkout"))
    cit = citation(sym, 0.9)
    check("code citation is module#Symbol + line",
          cit["doc_id"] == sym.doc_id and cit["symbol"] == "complete_checkout"
          and cit["kind"] == "function" and cit["line"] == sym.position,
          str({k: cit[k] for k in ("doc_id", "symbol", "kind", "line")}))
    prose = Chunk("d#0", "d#h", "s", "h", 0, "text", [1.0, 0.0])
    check("prose citation is unchanged (no symbol key)",
          "symbol" not in citation(prose) and "line" not in citation(prose))


# ------------------------------------------------------------ filters

def filter_tests():
    print("\n== retrieval.py — kind / module filters separate the senses (MECH)")
    gw = _cd.HashEmbedGateway()
    store = _cd.build_stub_code_store(gw)
    ret = Retriever(gw, store, alpha=0.5, tokenizer=code_tokens,
                    rewrites=CODE_REWRITES, sections={}, section_prefixes={})

    muts = ret.retrieve("checkout complete", k=14, filter={"kind": "mutation"})
    check("kind=mutation narrows to the GraphQL mutation",
          muts and all(c.kind == "mutation" for _, c in muts), str([c.symbol for _, c in muts]))

    gql = ret.retrieve("checkout", k=14,
                       filter={"module_prefix": "saleor/graphql"})
    check("module_prefix narrows to the GraphQL layer",
          bool(gql) and all(c.source.startswith("saleor/graphql") for _, c in gql),
          str([c.source for _, c in gql]))

    ret.retrieve("checkout", k=5, filter={"kind": "does-not-exist"})
    check("empty filter degrades to full corpus + annotated miss",
          ret.filter_misses == 1 and ret.last_query["filter_miss"] is True)
    check("explicit filter spec is reported for the trace",
          ret.last_query["filter_spec"] == {"kind": "does-not-exist"})


# ------------------------------------------------------------ store build guard

class _SpaceSwitchingGateway:
    """First batch embeds at dim 768, the next at 2048 — the Cloudflare-cap
    fall-through to a different embed model, simulated."""

    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        dim = 768 if self.calls == 1 else 2048
        return [Embedding(text=t, vector=[0.01] * dim, model=f"fake-{dim}",
                          usage=Usage(1, 0, 1)) for t in texts]

    def chat(self, *a, **k):  # pragma: no cover - a store build never chats
        raise AssertionError("no chat during a store build")


def store_space_guard_test(tmp: Path):
    print("\n== build_code_store — a mid-build embedding-space change preserves the store (MECH)")
    corpus = tmp / "corpus"
    (corpus / "pkg").mkdir(parents=True)
    (corpus / "pkg" / "a.py").write_text(
        "def a():\n    return 1\n\ndef b():\n    return 2\n", encoding="utf-8")
    out = tmp / "code.json"
    aborted = False
    try:
        build_code_store(_SpaceSwitchingGateway(), corpus, batch=1,
                         resume_from=str(out))
    except SystemExit as e:
        aborted = "space changed" in str(e)
    check("a mid-build model/dim switch aborts instead of wiping", aborted)
    check("the partial store is preserved for resume",
          out.exists() and len(RagStore.load(out)) >= 1)


# ------------------------------------------------------------ fixtures

def fixture_mechanics_tests():
    print("\n== fixtures — module#Symbol membership is plain set algebra (MECH)")
    gw = _cd.HashEmbedGateway()
    store = _cd.build_stub_code_store(gw)
    c = store.chunks[0]
    ok, reason = check_fixture(
        {"id": "X", "expected": [c.doc_id], "must_not": [], "must_refuse": False},
        [(0.9, c)])
    check("expected module#Symbol ⊆ retrieved → pass", ok, reason)
    ok2, _ = check_fixture(
        {"id": "X", "expected": ["ghost#absent"], "must_not": [], "must_refuse": False},
        [(0.9, c)])
    check("expected ∉ retrieved → fail (the pre-registration rule)", not ok2)
    ok3, _ = check_fixture(
        {"id": "X", "expected": [], "must_not": [c.doc_id], "must_refuse": False},
        [(0.9, c)])
    check("must_not wrong sense retrieved → fail", not ok3)
    ok4, _ = check_fixture(
        {"id": "E", "expected": [], "must_not": [], "must_refuse": True, "tau": 0.75},
        [(0.70, c)])
    check("must_refuse under tau → pass (fused-score gate)", ok4)
    ok5, _ = check_fixture(
        {"id": "E", "expected": [], "must_not": [], "must_refuse": True, "tau": 0.55},
        [(0.70, c)])
    check("must_refuse but top1 ≥ tau → fail (gate honesty)", not ok5)


# ------------------------------------------------------------ end-to-end

def stub_code_fixtures_test():
    print("\n== day10 — the code question set on the stub (6/6 MECH)")
    gw = _cd.HashEmbedGateway()
    store = _cd.build_stub_code_store(gw)
    rows = run_code_all(gw, store, tau=0.75)
    fails = [r["id"] for r in rows if not r["ok"]]
    check("all six code fixtures green", not fails, str(fails))
    ca1 = next(r for r in rows if r["id"] == "CA1")
    check("CA1 surfaces the split-brain double-hit (mutation + service)",
          "saleor/graphql/checkout/mutations/checkout_complete.py#CheckoutComplete"
          in ca1["d9_ids"]
          and "saleor/checkout/complete_checkout.py#complete_checkout"
          in ca1["d9_ids"], str(ca1["d9_ids"][:3]))
    check("CA1's rerank moved a row (Δrank ≠ 0)", ca1["any_delta"])
    ce1 = next(r for r in rows if r["id"] == "CE1")
    check("CE1 refuses out-of-corpus (no forged identifier)", ce1["gate_refused"] is True)
    cc1 = next(r for r in rows if r["id"] == "CC1")
    check("CC1 keeps both senses of `checkout`, excludes the test twin",
          len(cc1["expected_in_kept"]) == 2, str(cc1["expected_in_kept"]))
    cd1 = next(r for r in rows if r["id"] == "CD1")
    check("CD1's test symbol reaches the evidence (MMR kept it over factories)",
          cd1["expected_in_kept"], str(cd1["kept_ids"][:3]))


def doc_no_regression_test():
    print("\n== day10 — the doc nine stay green (additive, not a rewrite)")
    gw = _rd.StubGateway(extra_vocab=_rd._STUB_EXTRA_VOCAB)
    store = _rd.build_stub_store(gw)
    rows, agg = run_doc_all(gw, store, budget_chars=1200, tau=None)
    fails = [r["id"] for r in rows if not r["ok"]]
    check("all nine doc fixtures still green", not fails, str(fails))
    check("Day 9's Sigma ΔMRR is unchanged (+1.000)",
          near(agg["mrr_delta"], 1.0), f"{agg['mrr_delta']}")


def cost_test():
    print("\n== day10 — code retrieval is arithmetic (zero chat calls)")
    inner = _cd.HashEmbedGateway()
    store = _cd.build_stub_code_store(inner)
    gw = RecordingGateway(inner)
    pipe = code_pipeline(gw, store)
    pipe.run("Where is checkout completed?",
             expected=["saleor/checkout/complete_checkout.py#complete_checkout"])
    check("rerank+MMR+dedup+budget make zero chat calls", gw.chat_calls == 0,
          f"chat={gw.chat_calls}")
    check("one embed per sub-query (rewrite, no decompose here)",
          gw.embed_calls == 1, f"embeds={gw.embed_calls}")


def gate_tests():
    print("\n== day10 — the gate reads the fused score over symbols (MECH)")
    gw = _cd.HashEmbedGateway()
    store = _cd.build_stub_code_store(gw)
    ret = Retriever(gw, store, alpha=0.5, tokenizer=code_tokens,
                    rewrites=CODE_REWRITES, sections={}, section_prefixes={})
    res = ret.retrieve("Where is the Shopify webhook handler?", k=8)
    check("out-of-corpus identifier question still returns neighbours (gate's job)",
          len(res) > 0)
    check("a calibrated tau refuses it", should_refuse(res, tau=0.75))
    check("the same fused list passes an in-corpus question",
          not should_refuse(ret.retrieve("Where is checkout completed?", k=8), tau=0.75))


def main() -> None:
    tokenizer_tests()
    extractor_tests()
    with tempfile.TemporaryDirectory() as d:
        ingest_dir_tests(Path(d))
    with tempfile.TemporaryDirectory() as d:
        metadata_tests(Path(d))
    with tempfile.TemporaryDirectory() as d:
        store_space_guard_test(Path(d))
    filter_tests()
    fixture_mechanics_tests()
    stub_code_fixtures_test()
    doc_no_regression_test()
    cost_test()
    gate_tests()

    print()
    if not FAILS:
        print("ALL DAY 10 (MECH) CHECKS PASSED")
        raise SystemExit(0)
    print(f"{FAILS} CHECK(S) FAILED")
    raise SystemExit(1)


if __name__ == "__main__":
    main()
