"""Day 9 §4–§5 — the pointwise reranker + MMR diversity (the judge + diversifier).

Day 8's fused score (`α·cos + (1−α)·lex`) is a *find* score: it is tuned to
put the expected chunk into top-K, which is a recall claim. Whether the chunk
then deserves **rank 1** is a different judgement, and the two conflict
exactly in Family D: a near-duplicate twin shares tokens *and* vector region
with the real page, so the find-score loves it and the wrong one wins the
budget. This module is the second judgement, judged at the *candidate level*
where we can spend signals the whole-corpus pass couldn't afford:

    rel(c) = w_fused·fused(c) + w_rare·rare(c) + w_heading·heading(c) + w_pos·pos(c)

The seams (both intentionally narrow, both `[(score, Chunk)] → [(score, Chunk)]`):

    retrieve (Day 8) ─▶ rerank(candidates, q) ─▶ mmr(λ) ─▶ dedup(sim_tau)
                                                     └─▶ build_context(budget)

`rerank` re-orders by `rel`; `mmr` trades rel against redundancy over the
*stored* Day-5 vectors (zero new embeds); `dedup` and the budget live in
`ai_os/context.py`. Everything here is arithmetic over artifacts the pipeline
already paid for, so **Day 9 adds zero embed calls and zero chat calls** —
the `PENDING` cross-encoder at the bottom is the only place that ever changes.

The reciprocal boundary, recorded so nobody "improves" the gate later: the
refusal gate (`gate.should_refuse`) reads the *fused* score, never the rel
score, so a reranker's opinion can never manufacture evidence for an
out-of-corpus question.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Sequence

from ai_os.lexicon import Lexicon, tokenize
from ai_os.rag import Chunk
from ai_os.search import dot

# ------------------------------------------------------------- the knobs
# Every number Day 9's sweep can move (notes §3). Weights default to a unit
# sum so the bands are comparable; the sweep records rows per setting.
DEFAULT_RERANK_WEIGHTS: dict[str, float] = {
    "fused": 0.5,
    "rare": 0.25,
    "heading": 0.2,
    "pos": 0.05,
}

# A query token must be this long to count as *discriminating*. This is what
# keeps generic 3-char verbs ("set", "get", "use") out of the rare/heading
# signals — "set email" would otherwise out-IDF "checkoutComplete" by pure
# corpus rarity and lift the wrong doc (B2's exact trap, measured in the notes).
MIN_DISCRIMINANT_LEN = 4

# Position is a tie-break, never a verdict: the last chunk of a document can
# be penalized at most this much, so a real signal gap always wins over it.
POS_PENALTY = 0.1

POSITION = 1.0 - POS_PENALTY  # max pos signal


def _match(term: str, candidate_term: str) -> bool:
    """One-way substring match: the *query* term appears in the candidate term.

    Handles plurals and inflected forms without a stemmer ("attribute" in
    "attributes", "checkoutcomplete" in "checkoutComplete") while never
    letting a short generic corpus term match a long discriminating query term
    ("checkout" must not satisfy the "checkoutComplete" discriminator).
    """
    return (
        len(term) >= MIN_DISCRIMINANT_LEN
        and len(candidate_term) >= MIN_DISCRIMINANT_LEN
        and term in candidate_term
    )


def discriminants(question: str, lexicon: Lexicon | None) -> dict[str, float]:
    """{query token: IDF weight} for the tokens the signals may spend.

    IDF comes from the Day-8 text Lexicon when the term is known; a term that
    only lives in section *headings* (metadata, `create` in the quickstart)
    still gets weight 1.0 so the heading signal can spend it. Length >= 4
    filters generic verbs like `set`. Stopwords never reach here.
    """
    out: dict[str, float] = {}
    for t in tokenize(question):
        if len(t) < MIN_DISCRIMINANT_LEN:
            continue
        idf = lexicon.idf.get(t) if lexicon is not None else None
        out[t] = 1.0 if idf is None else float(idf)
    return out


def _mass_hit(candidate_terms: set[str], disc: dict[str, float]) -> float:
    """Fraction of discriminating-IDF mass a candidate's terms cover."""
    if not disc:
        return 0.0
    total = sum(disc.values())
    hit = sum(v for t, v in disc.items()
              if any(_match(t, ct) for ct in candidate_terms))
    return hit / total


def rare_score(chunk: Chunk, disc: dict[str, float]) -> float:
    """IDF-weighted overlap of the discriminating query tokens with the chunk
    *text* — concentrated on the tokens that discriminate (`checkoutComplete`,
    `attribute`), not the scaffolding (`how`, `do`). [0, 1]."""
    return _mass_hit(set(tokenize(chunk.text)), disc)


def heading_score(chunk: Chunk, disc: dict[str, float]) -> float:
    """IDF-weighted overlap with the chunk *metadata* (`heading` + `doc_id`).

    This is the wrong-entity fixer (C2): when the discriminating token sits in
    a section heading, that is a section-level match that cosine averages
    away. It is deliberately the same one-way substring matcher — "attribute"
    in "Attributes and Types Overview" fires where "Attributes" vs "attribute"
    token equality would not.
    """
    head = set(tokenize(chunk.heading)) | set(tokenize(chunk.doc_id))
    return _mass_hit(head, disc)


def position_score(position: int, position_floor: int) -> float:
    """Mild tie-break from the chunk's in-document slot; never a verdict.

    `position_floor` is the max position among the candidates, so the penalty
    is relative to *this* candidate set, not an absolute page offset. A chunk
    at the very end of its document is worth `POSITION` (0.9), the start 1.0.
    """
    if position_floor <= 0:
        return 1.0
    return POSITION + POS_PENALTY * (1.0 - position / (position_floor + 1))


def rel_from(score: float, chunk: Chunk, question: str,
             lexicon: Lexicon | None, *, weights: dict[str, float],
             position_floor: int) -> dict[str, float]:
    """The four signals + their weighted sum for one candidate.

    Every signal is clamped to [0, 1] so the weighted sum is a static bound
    rather than a hidden accident of scale.
    """
    disc = discriminants(question, lexicon)
    s = {
        "fused": min(1.0, max(0.0, float(score))),
        "rare": rare_score(chunk, disc),
        "heading": heading_score(chunk, disc),
        "pos": position_score(chunk.position, position_floor),
    }
    s["rel"] = sum(weights[name] * value for name, value in s.items())
    return s


@dataclass
class RerankRow:
    """One candidate's judgement, with its Δrank — the reranker's fingerprint.

    `pre_rank` is the Day-8 (fused) slot, `post_rank` the rel slot. A budget
    where every `pre_rank == post_rank` is a reranker that did nothing; the
    day's eval table reads Δrank as proof of existence.
    """

    chunk_id: str
    doc_id: str
    heading: str
    fused: float
    rare: float
    heading_signal: float
    pos: float
    rel: float
    pre_rank: int
    post_rank: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def rerank(results: Sequence[tuple[float, Chunk]], question: str,
           lexicon: Lexicon | None = None, *,
           weights: dict[str, float] | None = None,
           ) -> tuple[list[tuple[float, Chunk]], list[RerankRow]]:
    """Seam 1 — `[(score, Chunk)] → [(score, Chunk)]` ordered by rel(c).

    Same shape in and out so it can be inserted, swapped for the cross-encoder,
    or bypassed without touching `retrieve` or `/rag`. Returns the re-ranked
    list plus one `RerankRow` per candidate carrying the signal breakdown and
    Δrank (post − pre). Deterministic, stdlib, no network.
    """
    w = dict(DEFAULT_RERANK_WEIGHTS if weights is None else weights)
    floor = max((c.position for _, c in results), default=0)
    rows: list[RerankRow] = []
    for pre, (score, chunk) in enumerate(results):
        s = rel_from(score, chunk, question, lexicon, weights=w,
                     position_floor=floor)
        rows.append(RerankRow(
            chunk_id=chunk.chunk_id, doc_id=chunk.doc_id, heading=chunk.heading,
            fused=s["fused"], rare=s["rare"], heading_signal=s["heading"],
            pos=s["pos"], rel=s["rel"], pre_rank=pre + 1, post_rank=0))
    rows.sort(key=lambda r: r.rel, reverse=True)
    for post, r in enumerate(rows):
        r.post_rank = post + 1
    by_id = {c.chunk_id: c for _, c in results}
    ranked = [(float(r.rel), by_id[r.chunk_id]) for r in rows]
    return ranked, rows


def mmr(ranked: Sequence[tuple[float, Chunk]], *, lam: float = 0.6,
        k: int | None = None,
        sim=None) -> tuple[list[tuple[float, Chunk]], list[dict[str, Any]]]:
    """Maximal Marginal Relevance: `λ·rel − (1−λ)·max sim to kept`, greedy.

    `sim` defaults to cosine over the **stored** Day-5 vectors
    (`chunk.vector`, normalized at write time) — D1's twin crowding is a
    vector fact and arithmetic over what the index already holds. The first
    pick is the highest-rel candidate; each next pick maximizes relevance
    minus redundancy against everything already kept. Returns the diverse
    top-k and a pick trace (chunk, rel, penalty cosine per pick).
    """
    if not 0.0 <= lam <= 1.0:
        raise ValueError(f"lam must be in [0, 1], got {lam}")
    if sim is None:
        sim = lambda a, b: dot(a.vector, b.vector)  # noqa: E731 — stored vectors
    ranked = list(ranked)
    if not ranked:
        return [], []
    take = len(ranked) if k is None else min(k, len(ranked))
    picked: list[tuple[float, Chunk]] = [ranked[0]]
    rest = list(ranked[1:])
    trace: list[dict[str, Any]] = [{
        "chunk_id": ranked[0][1].chunk_id, "rel": float(ranked[0][0]),
        "penalty_sim": 0.0,
    }]
    while len(picked) < take and rest:
        best_i = 0
        best_v: float | None = None
        for i, (rel, c) in enumerate(rest):
            redundancy = max(sim(c, p) for _, p in picked)
            v = lam * rel - (1.0 - lam) * redundancy
            if best_v is None or v > best_v:
                best_v, best_i = v, i
            trace.append({"chunk_id": c.chunk_id, "rel": float(rel),
                          "penalty_sim": float(redundancy)})
        picked.append(rest.pop(best_i))
    return picked, trace


# ------------------------------------------------------------------ Δrank

def delta_ranks(expected: Sequence[str], pre: Sequence[str],
                post: Sequence[str]) -> dict[str, dict[str, int]]:
    """Per-expected-doc rank change across the rerank seam. `MECH`.

    `pre`/`post` are doc_id lists in rank order (the fused list and the rel
    list). Returns `{doc_id: {"pre": r, "post": r', "delta": r' − r}}` for the
    expected docs present in `pre`; `post` `None` marks a doc the rerank
    pushed off the evidence list entirely.
    """
    def rank_of(doc: str, ids: Sequence[str]) -> int | None:
        for i, d in enumerate(ids):
            if d == doc:
                return i + 1
        return None

    out: dict[str, dict[str, int]] = {}
    for doc in expected:
        p = rank_of(doc, pre)
        if p is None:
            continue
        q = rank_of(doc, post)
        out[doc] = {
            "pre": p,
            "post": q if q is not None else len(post) + 1,
            "delta": (q if q is not None else len(post) + 1) - p,
            "kept": q is not None,
        }
    return out


# ------------------------------------------------------------------ PENDING

def relevance_cross_encoder(gateway, question: str, chunk: Chunk) -> float:
    """The pointwise-but-semantic reranker tier — a seam, deliberately not built.

    `PENDING` because it is expensive: **one gateway chat call per candidate**
    (N ledger lines per query, the exact cost a real reranker would add). The
    deterministic tier above is free; Day 20/21 must prove the paid tier
    outranks free on the same table before this ever ships.
    """
    # noqa: D401 — this seam exists to be *replaced*, not called.
    raise NotImplementedError(
        "relevance_cross_encoder is a PENDING seam: one gateway chat per "
        "candidate. The free tier (rel()) must be shown to under-perform the "
        "semantic tier on the Day-9 table before enabling this costs N calls.")