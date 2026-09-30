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

# ================================================ Day 25 §6 — evidence framing
# Today a retrieved chunk and a system instruction are the *same kind of text in
# the same string*; `budget_fill` ends with a bare `"\n\n".join`. Day 25 gives each
# kept chunk a **position**: a delimited, source-tagged block carrying its `doc_id`,
# chunk id, rank and score, plus a **per-block byte cap** that truncates and
# *records* the truncation exactly the way `_tool_message` records clamping. That
# turns "which document is this, and how much of it did the model see?" from a
# guess into a fact — and it is a *representation* change, not a policy change, so
# the retriever, the reranker, the dedup and the budget floor above it are untouched.
#
# Framing is **opt-in** (`frame=False` is the Day-6 seam byte-for-byte), because a
# representation change that silently moved a retrieval number would be a
# regression wearing a feature's clothes.
BLOCK_OPEN = "<<<EVIDENCE"
BLOCK_CLOSE = ">>>"
BLOCK_END = "<<<END EVIDENCE>>>"
DEFAULT_BLOCK_CAP = 2000            # chars per framed block; a cap, not a budget
TRUNCATION_MARK = " …[block truncated by cap]"


def frame_block(chunk: "Chunk", *, rank: int, score: float, cap: int,
                taint: str | None = None) -> tuple[str, bool]:
    """One delimited, source-tagged evidence block. `(block, truncated)`.

    The delimiter is deliberately explicit and greppable so the model can see where
    a document *starts and ends* — the structural half of "position over
    politeness". `cap` truncates the body and the truncation is *recorded* by the
    caller (never silent); a `taint` source, when present, rides in the tag so the
    block that carried an instruction says so.
    """
    text = chunk.text or ""
    truncated = len(text) > cap
    body = text[:cap] + (TRUNCATION_MARK if truncated else "")
    tag = (f"{BLOCK_OPEN} doc={chunk.doc_id} chunk={chunk.chunk_id} "
           f"rank={rank} score={round(float(score), 4)}")
    if taint:
        tag += f" taint={taint}"
    return f"{tag}{BLOCK_CLOSE}\n{body}\n{BLOCK_END}", truncated


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
                *, best_first: bool = True, frame: bool = False,
                per_block_cap: int | None = None,
                tainted_ids: "set[str] | None" = None,
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

    Day 25 §6 — `frame=True` emits each kept chunk as a delimited, source-tagged
    block (Day-25 §6) with a `per_block_cap` that truncates and records the
    truncation as a `reason="block_cap"` drop row (the `_tool_message` clamping
    discipline). `frame=False` is the Day-6 seam byte-for-byte, so nothing above
    this module moved; the return arity is unchanged either way.
    """
    cap = DEFAULT_BLOCK_CAP if per_block_cap is None else max(1, int(per_block_cap))
    tainted = tainted_ids or set()
    parts: list[str] = []
    kept: list[tuple[float, Chunk]] = []
    drops: list[dict[str, Any]] = []
    used = 0
    for i, (score, chunk) in enumerate(results):
        piece = chunk.text
        trunc_row: dict[str, Any] | None = None
        if frame:
            taint = next((s for s in ("document",) if chunk.doc_id in tainted), None)
            piece, truncated = frame_block(chunk, rank=i + 1, score=score, cap=cap,
                                           taint=("document" if taint else None))
            if truncated:
                trunc_row = {
                    "chunk_id": chunk.chunk_id, "doc_id": chunk.doc_id,
                    "reason": "block_cap", "against": None, "sim": None,
                    "original_chars": len(chunk.text), "kept_chars": cap,
                }
        if i > 0 and used + len(piece) > budget_chars:
            drops.append({
                "chunk_id": chunk.chunk_id, "doc_id": chunk.doc_id,
                "reason": "budget",
                "against": None,
                "sim": None,
            })
            continue
        parts.append(piece)
        used += len(piece)
        kept.append((score, chunk))
        if trunc_row is not None:
            drops.append(trunc_row)
    if not best_first:
        parts = list(reversed(parts))
    return "\n\n".join(parts), used, kept, drops


def frame_evidence(results: Sequence[tuple[float, Chunk]], budget_chars: int,
                   *, best_first: bool = True, per_block_cap: int | None = None,
                   tainted_ids: "set[str] | None" = None,
                   ) -> tuple[str, int, list[tuple[float, Chunk]],
                              list[dict[str, Any]], list[dict[str, Any]]]:
    """Day 25's framing path: the same policy, plus the truncation log split out.

    `budget_fill(frame=True)` folds a block-cap truncation into its `drops` list so
    its return arity is unchanged for the Day-6/9 callers. This function is the one
    an evidence-hygiene caller uses when it wants the truncations *as* their own
    record (I9's "a truncated block records it").
    """
    context, used, kept, drops = budget_fill(
        results, budget_chars, best_first=best_first, frame=True,
        per_block_cap=per_block_cap, tainted_ids=tainted_ids)
    truncations = [d for d in drops if d.get("reason") == "block_cap"]
    return context, used, kept, drops, truncations


def build_context(results: Sequence[tuple[float, Chunk]],
                  budget_chars: int = 1200, *, best_first: bool = True,
                  frame: bool = False, per_block_cap: int | None = None,
                  ) -> tuple[str, int, list[tuple[float, Chunk]]]:
    """Day-6 seam, Day-9 policy: `(results, budget_chars) -> (context, used,
    kept)` — byte-for-byte the Day-6 signature, so nothing above this module
    notices the policy changed. The drop log lives in `budget_fill`. Day 25's
    `frame` knob is additive: the default stays the bare `"\\n\\n".join`.
    """
    context, used, kept, _ = budget_fill(results, budget_chars,
                                         best_first=best_first, frame=frame,
                                         per_block_cap=per_block_cap)
    return context, used, kept