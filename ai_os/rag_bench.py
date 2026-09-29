"""Day 21 §5 — the RAG bench metric core: measure retrieval, don't report it.

Day 7 pre-registered `expected` sets and `trace.expected_stats` has computed
**recall@K** and **MRR** from them since; Day 8 made ΣΔMRR the improvement
metric and Day 9 the 0-leak guard. Those two are inherited here verbatim — the
bench adds no new ranking arithmetic, it makes the arithmetic a *column*.

The two metrics the series never wrote down are the day's real work:

- **Precision@K** — "of the K slots, how many are relevant?" — needs a notion
  of *relevant* the ranking cannot supply, which is why nobody hand-collected
  it: a hand grader was Day 20's ban. The golden set supplies the missing
  premise as `relevant_also` (docs known to be on-topic that are *not
  required*), minted under the same oracle discipline as `expected`; precision
  then becomes membership arithmetic::

      precision@K = |retrieved ∩ (expected ∪ relevant_also)| / K

  Where no defensible relevance set exists (E-family: nothing is relevant;
  judge-hunting rows: relevance is the trap) the cell is **`n/a` by
  construction** — `None`, never `0.0`. A metric that refuses to be computed
  where its definition is empty is more honest than one that pretends.

- **Retrieval relevance** — Day 9's free pointwise `rel()` (fused + rare +
  heading + position) has been spent on every candidate since Day 9; the bench
  reports it as the mean `rel()` over the *kept* context. The paid
  cross-encoder lane (`rerank.relevance_cross_encoder`) stays shelved behind
  its `NotImplementedError` until this free column shows a leak it misses.

Every function here is a pure function of a retrieval call — 0 chats,
0 embeds beyond the one query embed — so the same trace graded twice is the
same row, byte for byte (Day 20's V2, carried).
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Sequence

from ai_os.lexicon import Lexicon
from ai_os.rag import Chunk, RagStore, build_context, k_from_budget
from ai_os.rerank import DEFAULT_RERANK_WEIGHTS, rel_from
from ai_os.trace import expected_stats


# ------------------------------------------------------------ the metrics

def relevance_set(expected: Iterable[str],
                  relevant_also: Iterable[str] = ()) -> set[str]:
    """`expected ∪ relevant_also` — the set a *relevant* retrieved slot may hit.

    `expected` is the required set (Day 7); `relevant_also` is the day's new,
    optional oracle-disciplined set of docs known to be on-topic but not
    required. An empty union means relevance has no definition for this row.
    """
    return set(expected) | set(relevant_also)


def precision_at_k(retrieved_ids: Sequence[str], expected: Iterable[str],
                   relevant_also: Iterable[str] = ()) -> float | None:
    """`|retrieved ∩ (expected ∪ relevant_also)| / K` — `None` when undefined.

    The denominator is the number of slots actually retrieved (≤ the pinned
    `k`), so on a small corpus a row that retrieved 5 slots is graded over 5.
    `None` (the honest `n/a`) when the relevance set is empty *or* nothing was
    retrieved — `0.0` would grade "refused to retrieve anything, pass."
    """
    rel = relevance_set(expected, relevant_also)
    if not rel:
        return None
    slots = list(retrieved_ids)
    if not slots:
        return None
    return sum(1 for d in slots if d in rel) / len(slots)


def retrieval_relevance(kept: Sequence[tuple[float, Chunk]], question: str,
                        lexicon: Lexicon | None = None, *,
                        weights: dict[str, float] | None = None,
                        position_floor: int | None = None) -> float | None:
    """Mean `rel()` over the kept context — the free relevance signal (Day 9).

    `rel()` is reused wholesale (`rerank.rel_from`); a second implementation of
    the same seam would be a second seam to drift. `position_floor` should be
    the max position over the *candidate* set (as `rerank` computes it), so the
    kept chunks are scored against the same floor the pipeline would use.
    `None` when nothing was kept.
    """
    if not kept:
        return None
    w = dict(DEFAULT_RERANK_WEIGHTS if weights is None else weights)
    floor = (position_floor if position_floor is not None
             else max((c.position for _, c in kept), default=0))
    vals = [rel_from(score, c, question, lexicon, weights=w,
                     position_floor=floor)["rel"] for score, c in kept]
    return sum(vals) / len(vals)


def build_lexicon(store: RagStore,
                  tokenizer: Callable[[str], list[str]] | None = None
                  ) -> Lexicon | None:
    """The Day-8 IDF scorer over the store's chunks (0 chats, 0 embeds).

    The `tokenizer` is Day 10's seam (code passes `retrieval.code_tokens`); the
    scorer itself does not change.
    """
    return Lexicon(store.chunks, tokenizer=tokenizer) if len(store) else None


# --------------------------------------------------------------- the trace

def retrieve_bench(gateway, store: RagStore, question: str, *,
                   expected: Iterable[str] = (),
                   relevant_also: Iterable[str] = (),
                   k: int | None = None, budget_chars: int = 1200,
                   tau: float | None = None, lexicon: Lexicon | None = None,
                   tokenizer: Callable[[str], list[str]] | None = None,
                   weights: dict[str, float] | None = None) -> dict[str, Any]:
    """One golden row's retrieval, measured — recall@K, precision@K, MRR,
    retrieval relevance, and the memberships the verdict needs.

    Retrieval reuses the *same seams* the trace does (`store.retrieve` +
    `rag.build_context`), so the numbers agree with `trace.retrieve_trace` by
    construction (the R9 control). Cost: one embed, zero chats.
    """
    q = question.strip()
    if not q:
        raise ValueError("question must not be empty")

    emb = gateway.embed([q])[0]
    if store.dimension and len(emb.vector) != store.dimension:
        from ai_os.rag import DimensionMismatch
        raise DimensionMismatch(
            f"query dim {len(emb.vector)} != store dim {store.dimension}")

    if k is None:
        k = k_from_budget(budget_chars, store.avg_chunk_len(), len(store))
    results = store.retrieve(emb.vector, k)
    context, used, kept = build_context(results, budget_chars)

    expected = list(expected)
    relevant_also = list(relevant_also)
    ret_ids = [c.doc_id for _, c in results]
    kept_ids = [c.doc_id for _, c in kept]
    ret_stats = expected_stats(expected, ret_ids)
    ctx_stats = expected_stats(expected, kept_ids)

    if lexicon is None:
        lexicon = build_lexicon(store, tokenizer)
    floor = max((c.position for _, c in results), default=0)

    precision = precision_at_k(ret_ids, expected, relevant_also)
    relevance = retrieval_relevance(kept, question, lexicon, weights=weights,
                                    position_floor=floor)

    return {
        "question": question,
        "k": k,
        "top1": (float(results[0][0]) if results else None),
        "scores": [float(s) for s, _ in results],
        "retrieved": [
            {"rank": i + 1, "score": float(s), "doc_id": c.doc_id,
             "chunk_id": c.chunk_id, "heading": c.heading}
            for i, (s, c) in enumerate(results)
        ],
        "retrieved_ids": ret_ids,
        "kept_ids": kept_ids,
        "context_chars": used,
        "context_texts": [c.text for _, c in kept],
        "expected": expected,
        "relevant_also": relevant_also,
        "expected_in_retrieved": ret_stats["in_list"],
        "expected_best_rank": ret_stats["best_rank"],
        "recall_at_k": ret_stats["recall_at_k"],
        "mrr": (ret_stats["mrr"] if expected else None),
        "expected_in_context": ctx_stats["in_list"],
        "precision_at_k": precision,
        "retrieval_relevance": relevance,
        "gate_refused": (None if tau is None
                         else (not results or results[0][0] < float(tau))),
    }


def control_agrees(bench: dict[str, Any], trace: dict[str, Any]) -> bool:
    """R9 — the bench's retrieval ≡ the same row's production trace.

    Compares the two retrieval facts a benchmark must not perturb: the
    retrieved order (doc_ids) and the kept order, plus recall@K and MRR. A
    disagreement is a **bench bug**, never a fixture to weaken.
    """
    bench_ids = list(bench["retrieved_ids"])
    trace_ids = [e["doc_id"] for e in trace.get("retrieved") or []]
    if bench_ids != trace_ids:
        return False
    if list(bench["kept_ids"]) != list(trace.get("kept_doc_ids") or []):
        return False
    if bench["recall_at_k"] != trace.get("recall_at_k"):
        return False
    return bench["mrr"] == (trace.get("mrr") if bench.get("expected") else None)


# ------------------------------------------------------------- tau sweep

def tau_sweep(rows: Sequence[dict[str, Any]], *, family_e: Sequence[str] = ()
              ) -> dict[str, Any]:
    """Calibrate τ on real fused top-1 scores: answerable vs E-family.

    `rows` is one measured row per golden entry (each carrying `id`, `family`,
    `top1`). Returns the separation (`min answerable − max E`), the τ that
    would maximize correct refusals without refusing an answerable row, and
    the leak that τ costs. Where the distributions overlap, the finding *is*
    the deliverable: a fused-score τ cannot separate E on this corpus.
    """
    e_ids = set(family_e)
    answerable = [r["top1"] for r in rows
                  if r.get("top1") is not None and r["id"] not in e_ids]
    e_scores = [r["top1"] for r in rows
                if r.get("top1") is not None and r["id"] in e_ids]
    out: dict[str, Any] = {
        "answerable": answerable,
        "e_family": e_scores,
        "min_answerable": min(answerable) if answerable else None,
        "max_e": max(e_scores) if e_scores else None,
    }
    if not answerable or not e_scores:
        out.update({"separation": None, "tau": None, "leaks": None,
                    "separated": False, "verdict": "insufficient data — n/a"})
        return out
    sep = min(answerable) - max(e_scores)
    # The τ that refuses every E row and no answerable row: just under the
    # weakest answerable score, and above the strongest E score.
    tau = min(answerable)
    leaks = sum(1 for s in answerable if s < tau) + sum(1 for s in e_scores
                                                        if s >= tau)
    out.update({
        "separation": sep,
        "tau": tau,
        "leaks": leaks,
        "separated": sep > 0.0,
        "verdict": ("a defensible τ exists (separation > 0)" if sep > 0.0
                    else "OVERLAP — a fused-score τ cannot separate E; the "
                         "refusal verdict moves to the semantic tier"),
    })
    return out
