"""Day 9 §2/§5/§6 — context construction v2: dedup, compress, order.

Day 6's `build_context` had one job — fit chunks into `budget_chars`, rank-
order, floor-guarded. It is a budget enforcer. The other two jobs, **ordering**
(positional attention: best-first or lost-in-the-middle) and **compression**
(which chunks are redundant or droppable), were flagged `PENDING` at Day 6 §7
and handed to Day 8, which handed them here.

The policy inside this module is Day 9's, behind the *same seam* Day 6 had —
`build_context(results, budget_chars) -> (context, used, kept)`. That
divergence (seam survives, policy changes) is declared, not smuggled; the
notes §3 own it explicitly.

Three rules, in order, when a chunk must go:

1. **dedup already ran** — redundancy is judged as a *text* fact
   (`text_sim`, token Jaccard), deliberately not the retrieval vectors: two
   guides saying the same sentence are a text dupe, and using the vectors
   again would double-count what MMR already penalized.
2. **drop the lowest-relevance chunks that don't fit, never the budget-
   protected top chunk** — every drop is evidence-weighted, never
   character-counted (utilization is *reported*, not an objective).
3. **report every drop** so Day 7's trace keeps utilization honest instead of
   a guess.

The guard: the top-relevance chunk is never similarity-dropped — a near-
duplicate that *is* the answer is not removed (B1's design twin).
"""
from __future__ import annotations

from typing import Any, Callable, Sequence

from ai_os.lexicon import tokenize
from ai_os.rag import Chunk

# Default redundancy threshold for dedup (a *text* fact: token Jaccard).
DEFAULT_SIM_TAU = 0.55


def text_sim(a: str, b: str) -> float:
    """Jaccard over stopword-filtered token sets — a *text* similarity.

    Two chunks sharing 60% of tokens can still carry different facts; that is
    why dedup is redundancy *judged against the kept set*, not raw text-sim,
    and why the top chunk is guarded.
    """
    if not a or not b:
        return 0.0
    A, B = set(tokenize(a)), set(tokenize(b))
    if not A or not B:
        return 0.0
    inter = len(A & B)
    return inter / (len(A) + len(B) - inter)


def near_duplicate_ratio(texts: Sequence[str], *, tau: float,
                         sim: Callable[[str, str], float] | None = None
                         ) -> float:
    """Fraction of items that duplicate a previously-kept one at `tau` or more.

    The first item is never a duplicate (the Day-9 guard, mirrored here as a
    *measurement*: crowding is measured the way the pipeline decides, so the
    number tracks the policy). 0.0 for < 2 items. `sim` defaults to
    `text_sim`; the eval runner passes a cosine-based one over stored vectors
    for the D1 slot-crowding metric instead.
    """
    sim = text_sim if sim is None else sim
    if len(texts) < 2:
        return 0.0
    kept: list[str] = []
    dupes = 0
    for t in texts:
        if kept and any(sim(t, k) >= tau for k in kept):
            dupes += 1
            continue
        kept.append(t)
    return dupes / len(texts)


def dedup(results: Sequence[tuple[float, Chunk]], *, sim_tau: float,
          ) -> tuple[list[tuple[float, Chunk]], list[dict[str, Any]]]:
    """Context-level dedup: drop a chunk that text-repeats a kept one.

    Guard: the top-relevance chunk (`results[0]`, the answer-bearing one) is
    *never* similarity-dropped. Every drop is recorded with its reason and the
    kept chunk it was redundant against — the Day-7 trace's duplicate ratio
    becomes a field, not a guess.
    """
    if not results:
        return [], []
    kept: list[tuple[float, Chunk]] = [results[0]]
    drops: list[dict[str, Any]] = []
    for score, chunk in results[1:]:
        redundant = max((text_sim(chunk.text, k[1].text) for k in kept),
                        default=0.0)
        if redundant >= sim_tau:
            drops.append({
                "chunk_id": chunk.chunk_id, "doc_id": chunk.doc_id,
                "reason": "redundant",
                "against": max(kept, key=lambda k: text_sim(chunk.text,
                                                            k[1].text))[1].chunk_id,
                "sim": round(redundant, 4),
            })
        else:
            kept.append((score, chunk))
    return kept, drops


def budget_fill(results: Sequence[tuple[float, Chunk]], budget_chars: int,
                *, best_first: bool = True,
                ) -> tuple[str, int, list[tuple[float, Chunk]],
                           list[dict[str, Any]]]:
    """Compression under the budget — relevance order, never rank order.

    `results` is assumed already reranked + MMR'd + deduped (the evidence
    order). Rules: the top chunk always fits (Day-6 floor, kept); each later
    chunk that fits joins; a chunk that does not fit is dropped with `reason =
    "budget"` — never evicts an earlier one, and every drop is reported.
    `best_first` is the presentation knob: True leads with the strongest
    evidence (the default, and the dominant strategy under positional
    attention), False presents it last — the knob the lost-in-the-middle
    sweep turns.
    """
    parts: list[str] = []
    kept: list[tuple[float, Chunk]] = []
    drops: list[dict[str, Any]] = []
    used = 0
    for i, (score, chunk) in enumerate(results):
        if i > 0 and used + len(chunk.text) > budget_chars:
            drops.append({
                "chunk_id": chunk.chunk_id, "doc_id": chunk.doc_id,
                "reason": "budget",
                "against": None,
                "sim": None,
            })
            continue
        parts.append(chunk.text)
        used += len(chunk.text)
        kept.append((score, chunk))
    if not best_first:
        parts = list(reversed(parts))
    return "\n\n".join(parts), used, kept, drops


def build_context(results: Sequence[tuple[float, Chunk]],
                  budget_chars: int = 1200, *, best_first: bool = True,
                  ) -> tuple[str, int, list[tuple[float, Chunk]]]:
    """Day-6 seam, Day-9 policy: `(results, budget_chars) -> (context, used,
    kept)` — byte-for-byte the Day-6 signature, so nothing above this module
    notices the policy changed. The drop log lives in `budget_fill`.
    """
    context, used, kept, _ = budget_fill(results, budget_chars,
                                         best_first=best_first)
    return context, used, kept