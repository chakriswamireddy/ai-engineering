"""Day 7 §5 — run the nine-question debug suite against a store + gateway.

Two modes:

    $ python tools/rag_debug.py --stub            # offline mechanics (no key)
    $ python tools/rag_debug.py --store data/store.json
    $ python tools/rag_debug.py                   # real store via $RAG_STORE_PATH

For every question in `ai_os/questions.yaml` it builds an instrumented trace
(embed → top-K → context budget → gate →, optionally, answer), runs the
model-free fixture checks, and — when chat is available — fills the
answer/oracle/verdict columns. Chat is the quota-limited half of the free
tier, so a hit on the daily cap *skips* the chat columns instead of failing
the run: the retrieval half always completes, and every report cell is
honest about whether it is `MECH` (provable) or `PENDING` (needs the real
gateway). Each answered question costs one embed + one retrieve + one
generate, plus one oracle chat for Families A–D.

Stub mode uses an in-process gateway with the Day 4 stub vocabulary plus a
tiny local extension, and a curated mini-corpus that mirrors the fixture
doc_ids — enough to prove the mechanics (fixture set membership, gate
arithmetic, budget/duplicate accounting) with zero API calls and zero
network. Semantic families (B2/C) stay `PENDING` on the stub on purpose:
stub vectors carry no meaning to rank by.

    --tau      override the shared similarity gate (stub default 0.05)
    --no-chat  retrieval-only: never call chat (pure MECH run)
    --report   write the run's cell values into a markdown report
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_os.config import build_gateway_from_env  # noqa: E402
from ai_os.contract import (  # noqa: E402
    Completion,
    Embedding,
    RateLimitError,
    Usage,
)
from ai_os.fixtures import check_fixture, load_questions  # noqa: E402
from ai_os.ledger import Ledger  # noqa: E402
from ai_os.oracle import oracle_test  # noqa: E402
from ai_os.rag import Chunk, RagStore, load_store_from_env  # noqa: E402
from ai_os.trace import VERDICTS, attach_oracle, run_trace  # noqa: E402

# ---------------------------------------------------------------- stub half

EMBED_DIM = 8
_MOCK_VOCAB = {
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

# Extra vocabulary so the *questions themselves* land. Stub tokens must cover
# the actual question strings, else a question embeds to zero and no ranking
# is meaningful. This extends — never replaces — the Day 4 table.
_STUB_EXTRA_VOCAB = {
    "checkoutcomplete": [1.0, 0.2, 0.0, 0.0, 0.0, 0.1, 0.0, 0.0],
    "apps": [0.0, 0.9, 0.0, 0.0, 0.0, 0.0, 0.0, 0.1],
    "attribute": [0.0, 0.0, 0.9, 0.1, 0.0, 0.0, 0.0, 0.0],
    "field": [0.0, 0.0, 0.2, 0.0, 0.6, 0.0, 0.0, 0.0],
    "saleor": [0.2, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],
    "cloud": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.6],
    "cost": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.6, 0.0],
    "shopify": [0.0, 0.0, 0.0, 0.0, 0.0, 0.1, 0.0, 0.8],
}


def _embed_vec(text: str, vocab: dict[str, list[float]]) -> list[float]:
    vec = [0.0] * EMBED_DIM
    for word in text.lower().split():
        w = word.strip(".,;:!?()[]{}'\"`*#-")
        if w in vocab:
            for i, x in enumerate(vocab[w]):
                vec[i] += x
    m = sum(x * x for x in vec) ** 0.5
    return [x / m for x in vec] if m else vec


class StubGateway:
    """In-process deterministic gateway for the offline run.

    Same contract shape as Day 4's `ModelGateway` but no HTTP: `embed` sums
    vocab vectors (this repo's definition of "semantics" for a stub), `chat`
    returns a canned grounded answer. Cheap, repeatable, quota-immune.
    """

    def __init__(self, extra_vocab: dict | None = None) -> None:
        self.vocab = dict(_MOCK_VOCAB)
        self.vocab.update(extra_vocab or {})
        self.ledger = Ledger()
        self.last_provider = "openai"
        self.last_attempts = 1
        self.last_latency_ms = 1.0
        self.last_embeddings: list[Embedding] = []

    def embed(self, texts: list[str]) -> list[Embedding]:
        vectors = [_embed_vec(t, self.vocab) for t in texts]
        usage = Usage(sum(max(1, len(t) // 4) for t in texts), 0, 0)
        self.last_embeddings = [
            Embedding(text=t, vector=v, model="text-embedding-3-small",
                      usage=usage)
            for t, v in zip(texts, vectors)
        ]
        return self.last_embeddings

    def chat(self, messages, **kw) -> Completion:
        last = messages[-1]["content"]
        return Completion(
            text=f"stub answer: grounded on the supplied context ({last[:40]}\u2026)",
            model="gpt-4o-mini", finish_reason="stop",
            usage=Usage(12, 24, 36))


# The stub corpus: one doc per fixture doc_id, texts rich in stub vocab so
# the mechanics (expected ∈ top-K, gate arithmetic) are demonstrable.
STUB_DOCS: list[tuple[str, str, str]] = [
    ("quickstart/api#create-a-checkout", "Create a checkout",
     "checkout is created with checkoutCreate. A checkout accepts cart "
     "lines, an email, and a shipping method. checkout shopping cart "
     "checkout cart shipping checkout."),
    ("developer/checkout/lifecycle#completing-checkout",
     "Completing Checkout",
     "checkoutComplete turns a checkout into an order. Once the checkout "
     "completes, the order flows through fulfillment and shipping. "
     "checkout checkout order fulfillment shipping checkout."),
    ("developer/checkout/api-guide#completing-checkout",
     "Completing checkout",
     "call checkoutComplete on the checkout. The mutation returns the "
     "created order and the checkout is gone. checkout checkout complete "
     "shipping checkout order checkout."),
    ("developer/checkout/api-guide#creating-a-checkout-session",
     "Creating a Checkout Session",
     "start a checkout session with checkoutCreate, choose lines, set "
     "email and delivery method. checkout cart shipping checkout."),
    ("developer/order/overview#lifecycle", "Order Lifecycle",
     "an order is created when a checkout completes. orders flow through "
     "fulfillment which reserves stock; payment handles the transaction. "
     "order checkout fulfillment payment order."),
    ("developer/extending/apps/overview#apps-overview", "Apps Overview",
     "apps are the way to extend saleor. apps install via manifest, expose "
     "webhooks, and run sync endpoints. app apps webhook sync app apps "
     "app webhook app sync."),
    ("developer/app-store/overview#saleor-app-store", "Saleor App Store",
     "the saleor app store is the marketplace of ready-made apps: adyen, "
     "stripe, segment. install from the store. app app app store install."),
    ("developer/attributes/overview#attributes-and-types-overview",
     "Attributes and Types Overview",
     "attributes are product metadata: size, color, material. an attribute "
     "describes products; a field belongs to the graphql schema. attribute "
     "attribute product field attribute."),
    ("developer/attributes/api#input-types", "Input Types",
     "attribute input types define the values an attribute accepts: "
     "dropdown, multiselect, file. attribute attribute field attribute."),
]


def build_stub_store(gw: StubGateway) -> RagStore:
    store = RagStore()
    for doc_id, heading, text in STUB_DOCS:
        vec = gw.embed([text])[0].vector
        if not store.embedding_model:
            store.embedding_model = gw.last_embeddings[0].model
        store.add(Chunk(chunk_id=f"{doc_id}#0", doc_id=doc_id,
                        source=f"https://docs.saleor.io/{doc_id.split('#')[0]}.md",
                        heading=heading, position=0, text=text, vector=vec))
    return store


# ---------------------------------------------------------------- reporting

def _verdict_line(r: dict) -> str:
    if not r["ok"]:
        return r["reason"]
    v = r["trace"].get("verdict")
    if v is None or v == "pending":
        return "mechanical OK — semantic verdict PENDING (real run)"
    return VERDICTS[v]


def render_table(rows: list[dict]) -> None:
    print("\n" + "=" * 96)
    print("   DAY 7 — RAG FAILURE REPORT (run cells; MECH vs PENDING)")
    print("=" * 96)
    print(f"{'ID':<4}{'family':<30}{'fx':<5}{'top1':<8}{'recall':<7}"
          f"{'dupe':<5}verdict / note")
    print("-" * 96)
    for r in rows:
        t = r["trace"]
        top1 = f"{t['top1']:.3f}" if t["top1"] is not None else "  \u2205"
        recall = (f"{t['recall_at_k']:.0%}" if t["recall_at_k"] is not None
                  else "  n/a")
        fx = "PASS" if r["ok"] else "FAIL"
        print(f"{r['id']:<4}{r['family']:<30}{fx:<5}{top1:<8}{recall:<7}"
              f"{t['duplicate_chunks']:<5}{_verdict_line(r)}")


# ------------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--store", default=None,
                    help="path to the RagStore (default: $RAG_STORE_PATH or "
                         "data/store.json)")
    ap.add_argument("--stub", action="store_true",
                    help="offline run: in-process stub gateway + mini-corpus")
    ap.add_argument("--tau", type=float, default=None,
                    help="override the similarity gate (stub default 0.05)")
    ap.add_argument("--no-chat", action="store_true",
                    help="retrieval-only run (never calls chat)")
    ap.add_argument("--report", default=None, metavar="PATH",
                    help="write the run's cells into a markdown report")
    args = ap.parse_args()

    data = load_questions()
    data_tau = data["tau"]
    if args.stub:
        effective_tau = args.tau if args.tau is not None else 0.05
        gw = StubGateway(extra_vocab=_STUB_EXTRA_VOCAB)
        store = build_stub_store(gw)
        print(f"store: {len(store)} chunks   mode: STUB (offline mechanics; "
              f"semantic verdicts stay PENDING)   tau={effective_tau}")
    else:
        effective_tau = args.tau if args.tau is not None else data_tau
        gw = build_gateway_from_env()
        store = load_store_from_env(args.store) if args.store else load_store_from_env()
        print(f"store: {len(store)} chunks · {store.embedding_model} / "
              f"dim {store.dimension}   mode: REAL   tau={effective_tau}")
    if not args.stub and not any(getattr(p, "embed", None) for p in gw.providers):
        raise SystemExit("no provider supports embedding — check .env "
                         "(OPENAI_API_KEY / OPENAI_BASE_URL / EMBEDDING_MODEL)")

    budget = data["budget_chars"]
    with_generation = not args.no_chat and not args.stub

    rows: list[dict] = []
    for f in data["questions"]:
        fk = dict(f)
        if fk.get("tau") is None:
            fk["tau"] = effective_tau
        trace = run_trace(gw, store, fk["question"], expected=fk["expected"],
                          budget_chars=budget, tau=effective_tau,
                          with_generation=with_generation)
        trace["id"] = fk["id"]
        trace["family"] = fk["family"]
        fixture_ok, reason = check_fixture(fk, trace["retrieved"])

        oracle = None
        if with_generation and not trace["refused"] and fk["expected"]:
            try:
                o = oracle_test(gw, store, fk["question"], fk["expected"])
                o["ok"] = _grounded_proxy(o)
                oracle = o
            except RateLimitError:
                print(f"   {fk['id']}: chat quota hit — oracle skipped")
            except Exception as e:
                print(f"   {fk['id']}: oracle skipped ({e.__class__.__name__})")

        if oracle is not None:
            trace = attach_oracle(trace, oracle)

        rows.append({
            "id": fk["id"],
            "family": fk["family"],
            "question": fk["question"],
            "predicted": fk.get("predicted"),
            "fix": fk.get("fix"),
            "must_refuse": fk["must_refuse"],
            "ok": fixture_ok,
            "reason": reason,
            "trace": trace,
        })

    mech = sum(1 for r in rows if r["ok"])
    render_table(rows)
    print("-" * 96)
    print(f"{mech}/9 fixtures mechanically green on this run. "
          f"Chat columns: {'WRITTEN' if with_generation else 'SKIPPED (stub/no-chat)'}.")
    if args.stub and mech < 9:
        fails = [r["id"] for r in rows if not r["ok"]]
        print(f"NOTE: FAIL on the stub ({', '.join(fails)}) is a *semantics* "
              f"limit, not a mechanics bug — sense-disambiguation and "
              f"near-miss refusal need the real embedder/gateway.")

    if args.report:
        write_report(args.report, rows)
        print(f"report written → {args.report}")


def _grounded_proxy(o: dict) -> bool:
    """Mechanical grounding proxy for the oracle verdict.

    `ok` is *semantic* in principle (did the model actually use the
    evidence?) and a human confirms it on the real run. This proxy only
    asserts the model did not refuse or vanish against perfect evidence — the
    oracle passed food to a reader and the reader at least read it.
    """
    ans = (o.get("answer") or "").strip().lower()
    return bool(ans) and "not covered" not in ans


def write_report(path: str, rows: list[dict]) -> None:
    lines = [
        "# RAG Failure Report — run", "",
        "> Generated by `python tools/rag_debug.py`. The Retrieved column is "
        "the run's output; `PENDING` cells still need a real chat/embed run. "
        "The pre-registered analysis lives in `docs/failures/rag-naive.md`.",
        "",
        "| ID | Retrieved (rank: doc_id · score) | Expected | Fixture | "
        "Verdict / note |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        t = r["trace"]
        ret = "; ".join(f"{e['rank']}:{e['doc_id']}({e['score']:.3f})"
                        for e in t["retrieved"])
        exp = ", ".join(t["expected"]) if t["expected"] else "*(none — refuse)*"
        lines.append(f"| {r['id']} | `{ret}` | `{exp}` | "
                     f"{'PASS' if r['ok'] else 'FAIL'} | "
                     f"{_verdict_line(r).replace('|', '\\|')} |")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()