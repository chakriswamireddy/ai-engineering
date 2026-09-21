"""Day 6 §7 — deliberate-failure suite for the RAG pipeline against the stub.

The stub scripts **both** halves — embeddings (vocab vectors, deterministic)
and chat (openai :8700 / anthropic :8701). No API key, no real network.

What is provable here is *mechanics*: retrieval ordering, k/budget clamping,
the overlap seam, the citation set, the refusal path, and the Day 4 retry /
fallback rules transferred to embedding. What stays `PENDING` is *semantics*
— whether a real embedder ranks the right chunk, and whether a real model
honors "not covered" — that needs a live API and is Day 7's job.

    $ python tools/rag_smoke.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STUB = Path(__file__).resolve().parent / "mock_gateway.py"
sys.path.insert(0, str(ROOT))

from ai_os.contract import InvalidKeyError, ProviderError  # noqa: E402
from ai_os.gateway import ModelGateway  # noqa: E402
from ai_os.ledger import Ledger  # noqa: E402
from ai_os.providers.anthropic import AnthropicProvider  # noqa: E402
from ai_os.providers.openai import OpenAIProvider  # noqa: E402
from ai_os.rag import (  # noqa: E402
    Chunk, RagStore, run_rag, k_from_budget,
)
from ai_os.chunk import chunk_text  # noqa: E402

OPENAI_URL = "http://127.0.0.1:8700/v1"
ANTHROPIC_URL = "http://127.0.0.1:8701/v1"
FAILS = 0
ALL_LEDGERS: list[Ledger] = []


def check(label: str, cond: bool, detail: str = ""):
    global FAILS
    if not cond:
        FAILS += 1
    print(f"[{'OK  ' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


def make_gateway(*, max_attempts: int = 3, timeout_s: float = 5.0,
                 base: float = 0.02, cap: float = 0.3):
    led = Ledger()
    gw = ModelGateway(
        [OpenAIProvider(api_key="sk-test", model="gpt-4o-mini",
                        base_url=OPENAI_URL, timeout_s=timeout_s),
         AnthropicProvider(api_key="sk-ant-test", model="claude-3-5-haiku-latest",
                           base_url=ANTHROPIC_URL, timeout_s=timeout_s)],
        max_attempts=max_attempts, timeout_s=timeout_s,
        base_delay_s=base, max_delay_s=cap, ledger=led)
    return gw, led


def reset(openai=None, anthropic=None, openai_embed=None, retry_after=None) -> None:
    def post(body):
        req = urllib.request.Request("http://127.0.0.1:8700/admin/reset",
                                     data=json.dumps(body).encode(), method="POST")
        urllib.request.urlopen(req, timeout=5)
    body: dict = {"openai": openai or ["ok"], "anthropic": anthropic or ["ok"],
                  "openai_embed": openai_embed or ["ok"],
                  "retry_after": retry_after or 0.2}
    post(body)


def embed_hits() -> int:
    req = urllib.request.Request("http://127.0.0.1:8700/admin/state")
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)["embed_hits"]["openai"]


# ------------------------------------------------------------- the corpus
# A tiny, deterministic corpus: two docs whose vectors *share* vocabulary
# with each other but not with the third — enough for rank assertions.
CORPUS = [
    ("developer/checkout/overview", "checkout.md",
     "Checkout Lifecycle",
     "A checkout is created with checkoutCreate. The checkout accepts findings. "
     "Then addresses shipping method and email are updated before "
     "checkoutComplete turns it into an order. The order is the result. "
     "Variants carry inventory and pricing so checkout accepts variant IDs."),
    ("developer/order/overview", "order.md",
     "Order Overview",
     "An order is created by completing a checkout. Orders flow through "
     "fulfillment which reserves stock and can trigger webhooks. Payment "
     "gateways handle the transaction for the order."),
    ("developer/extending/webhooks", "webhooks.md",
     "Webhooks Overview",
     "Webhooks notify external systems about events such as order creation "
     "and payment. A sync webhook can modify Saleor's response. Apps use "
     "webhooks to integrate with backend services."),
]


def build_store(gw) -> RagStore:
    store = RagStore()
    for doc_id, _, heading, text in CORPUS:
        vec = gw.embed([text])[0].vector
        if not store.embedding_model:
            store.embedding_model = gw.last_embeddings[0].model
        for position, piece in enumerate(chunk_text(text)):
            store.add(Chunk(chunk_id=f"{doc_id}#{position}", doc_id=doc_id,
                            source=f"https://docs.saleor.io/{doc_id}.md",
                            heading=heading, position=position,
                            text=piece, vector=vec))
    return store


# ------------------------------------------------------------------- §7 rows

def run(label: str, fn):
    print(f"\n== {label}")
    gw, led = make_gateway()
    ALL_LEDGERS.append(led)
    fn(gw, led)


def case_grounded_answer(gw, led):
    reset()
    store = build_store(gw)
    res = run_rag(gw, store, "how do I complete a checkout into an order?")
    check("docs-answer: answer produced (stub chat)", bool(res["answer"]))
    check("docs-answer: citations are real chunks",
          len(res["citations"]) > 0 and all(c["score"] is not None
                                            for c in res["citations"]))
    check("docs-answer: checkout doc ranked first",
          res["citations"][0]["doc_id"] == "developer/checkout/overview",
          res["citations"][0]["doc_id"])
    check("docs-answer: retrieval cost accounted separately",
          res["retrieval"]["model"] == "text-embedding-3-small"
          and res["retrieval"]["cost_usd"] > 0)


def case_out_of_corpus(gw, led):
    reset()
    store = build_store(gw)
    res = run_rag(gw, store, "gibberish zzz qqq")
    # nearest neighbors are returned anyway; the *semantic* refusal is PENDING
    check("out-of-corpus: retrieval still returns candidates",
          res["retrieval"]["candidates"] > 0)
    check("out-of-corpus: citations non-empty", len(res["citations"]) > 0)


def case_two_chunk_seam(gw, led):
    reset()
    # A seam fact straddles a chunk boundary. Overlap preserves it in both
    # neighbors; K>1 must surface both sides. Unknown words embed to zero,
    # so only the two "checkout" chunks score above 0 — deterministic.
    pad = " alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron"
    # one "checkout" run exactly straddling the 40..60 seam; pure pad elsewhere,
    # and 60 < run-start+30 < 80 so only chunk 0 and chunk 1 see the word.
    run = " checkout checkout checkout"
    text = (pad * 2)[:40] + run + (pad * 2)[30:] + pad
    pieces = chunk_text(text, size=60, overlap=20)
    seam = pieces[0][-20:]
    check("seam: overlap keeps the tail in both neighbors",
          seam == pieces[1][:20] and seam in pieces[1])
    store = RagStore()
    for i, p in enumerate(pieces):
        vec = gw.embed([p])[0].vector
        store.add(Chunk(f"seam#{i}", "seam", "s", "seam", i, p, vec))
    qvec = gw.embed(["checkout"])[0].vector
    results = store.retrieve(qvec, k=2)
    positions = {c.position for _, c in results}
    check("seam: K=2 retrieves both boundary chunks (0 and 1)",
          {0, 1} <= positions, f"{positions}")
    check("seam: boundary fact still intact in the retrieved neighbor",
          any(seam in c.text for _, c in results))


def case_empty_corpus(gw, led):
    reset()
    empty = RagStore()
    res = run_rag(gw, empty, "anything at all")
    check("empty corpus: honest refusal, no chat call",
          res["refused"] is True and res["answer"] == "not covered by the documentation"
          and res["generation"] is None)


def case_empty_query(gw, led):
    reset()
    store = build_store(gw)
    try:
        run_rag(gw, store, "   ")
        check("empty query: rejected", False)
    except ValueError as e:
        check("empty query: rejected", "empty" in str(e).lower())


def case_k_and_budget(gw, led):
    reset()
    store = build_store(gw)
    check("k beyond corpus: clamped", len(store.retrieve([1.0] * 8, k=999)) == len(store))
    res = run_rag(gw, store, "checkout order", budget_chars=1)
    check("budget < one chunk: empty-safe refusal",
          res["refused"] is True and res["generation"] is None)
    k = k_from_budget(10 ** 9, store.avg_chunk_len(), len(store))
    check("k_from_budget clamps to n", k == len(store), f"k={k}")


def case_embed_retry(gw, led):
    reset(openai_embed=["500", "ok"])
    before = embed_hits()
    embs = gw.embed(["checkout cart"])
    check("embed 500→200: retried through the gateway",
          embed_hits() - before == 2 and len(embs) == 1)
    check("embed 500→200: 1 failure + ok in ledger",
          len(led.failures()) == 1 and led.records[-1].ok)


def case_embed_401(gw, led):
    reset(openai_embed=["401"])
    before = embed_hits()
    try:
        gw.embed(["checkout"])
        check("embed 401: raised", False)
    except InvalidKeyError:
        check("embed 401: InvalidKeyError, 0 retries", embed_hits() - before == 1)
    check("embed 401: failure recorded in ledger", len(led.failures()) == 1)


def case_chat_fallback_and_rag(gw, led):
    reset(openai=["500"], anthropic=["ok"])
    store = build_store(gw)
    res = run_rag(gw, store, "what are webhooks in saleor?")
    check("chat 500 on primary: fell back to anthropic",
          res["generation"]["provider"] == "anthropic")
    check("chat 500: attempt recorded as failure",
          any(not r.ok for r in led.records))


def case_citation_trace(gw, led):
    reset()
    store = build_store(gw)
    res = run_rag(gw, store, "how do payments work with checkout and order?")
    cited = {c["chunk_id"] for c in res["citations"]}
    qvec = gw.embed(["how do payments work with checkout and order?"])[0].vector
    retrieved = {c.chunk_id for _, c in store.retrieve(qvec, res["retrieval"]["k"])}
    check("citation trace: every cited chunk ∈ retrieved set", cited <= retrieved)
    check("citation trace: citations == retrieved set (K candidates)",
          cited == retrieved)


def case_ledger_separation(gw, led):
    reset()
    store = build_store(gw)
    run_rag(gw, store, "checkout order webhook")
    by_model = led.totals_by_model()
    check("ledger: retrieval tokens billed under the embed model",
          by_model.get("text-embedding-3-small", {}).get("tokens", 0) > 0)
    check("ledger: generation tokens billed under a chat model",
          sum(v["tokens"] for k, v in by_model.items()
              if k != "text-embedding-3-small") > 0)
    check("ledger: two distinct cost lines",
          len(by_model) == 2, str(list(by_model)))


def case_identical_vectors(gw, led):
    reset()
    a = gw.embed(["checkout cart"])[0]
    b = gw.embed(["checkout cart"])[0]
    check("identical texts → identical vectors", a.vector == b.vector)
    c = gw.embed(["webhook sync app"])[0]
    from ai_os.search import dot, normalize
    sa = dot(normalize(a.vector), normalize(b.vector))
    sc = dot(normalize(a.vector), normalize(c.vector))
    check("self-similarity 1.0, cross-theme lower", abs(sa - 1.0) < 1e-9 and sc < sa,
          f"self={sa:.3f} cross={sc:.3f}")


def case_lost_in_middle(gw, led):
    reset()
    # 8 chunks; all fit the budget; the 7th-ranked must still be in the prompt
    store = RagStore()
    for i in range(8):
        vec = gw.embed([f"chunk {i} checkout terms"])[0].vector
        store.add(Chunk(f"mid#{i}", "mid", "s", f"heading {i}", i,
                        f"chunk number {i} with checkout terms", vec))
    res = run_rag(gw, store, "checkout")
    check("lost-in-middle mechanics: all 8 chunks retrieved",
          res["retrieval"]["candidates"] == 8)
    check("lost-in-middle mechanics: all 8 chunks reach the prompt",
          len(res["citations"]) == 8
          and {c["position"] for c in res["citations"]} == {0, 1, 2, 3, 4, 5, 6, 7})
    scores = [c["score"] for c in res["citations"]]
    check("lost-in-middle mechanics: citations in retrieval order",
          all(scores[i] >= scores[i + 1] for i in range(len(scores) - 1)))
    # semantics (answer correct at slot 2 vs 7) is PENDING — needs a real model


def economics():
    print("\n== Day 6 token economics (retrieval vs generation, per run)")
    for led in ALL_LEDGERS:
        s = led.summary()
        emb = s["by_model"].get("text-embedding-3-small", {}).get("tokens", 0)
        print(f"  records={s['records']:<3} ok={s['ok']:<3} failed={s['failed']:<3} "
              f"embed_tokens={emb:<4} total_tokens={s['tokens']:<5} "
              f"cost=${s['cost_usd']:.8f}  {s['by_model']}")


def _stub_up() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8700/admin/state", timeout=1):
            return True
    except Exception:
        return False


def main() -> None:
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
        run("1. answer in docs → grounded + citation + cost", case_grounded_answer)
        run("2. question not in corpus → neighbors anyway", case_out_of_corpus)
        run("3. answer spanning two chunks (overlap seam)", case_two_chunk_seam)
        run("4. empty corpus → refusal", case_empty_corpus)
        run("5. empty query → rejected", case_empty_query)
        run("6. k beyond corpus / budget below one chunk", case_k_and_budget)
        run("7a. embed 500 → retried (Day 4 rule)", case_embed_retry)
        run("7b. embed 401 → never retried", case_embed_401)
        run("7c. chat 500 → fallback provider (via /rag)", case_chat_fallback_and_rag)
        run("8. citation trace (cited ⊆ retrieved)", case_citation_trace)
        run("9. ledger: retrieval ≠ generation bills", case_ledger_separation)
        run("10. identical texts → identical vectors", case_identical_vectors)
        run("11. lost-in-the-middle mechanics (8 chunks)", case_lost_in_middle)
    finally:
        if proc is not None:
            proc.terminate()

    economics()
    print("\n" + ("ALL RAG CHECKS PASSED" if not FAILS else f"{FAILS} CHECK(S) FAILED"))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()