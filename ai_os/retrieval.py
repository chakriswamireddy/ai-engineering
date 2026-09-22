"""Day 8 §3–§6 — the improved retriever: a layer, not a function.

Day 6 left one seam: `/rag` calls `store.retrieve(qvec, k)` and nothing else.
Day 8 widens the seam to own the *query side* too, so `/rag` and Day 9 never
care which retriever produced the top-K:

    retriever = Retriever(gateway, store, alpha=0.5)
    results = retriever.retrieve("How do I cancel an order?", k=5)
    # [(score, Chunk)] — the exact shape build_context consumes

The flow, all behind that seam:

    question ─▶ rewrite(question)      deterministic rule table (gateway tier PENDING)
             ─▶ decompose(question)    up to `decompose_cap` sub-questions
             ─▶ embed per sub-query    +1 gateway embed() calls per split (ledger)
             ─▶ filter by section      if the query names exactly one section
             ─▶ fuse                   α·cos(q,c) + (1−α)·lex(q,c), max-score
                                       per chunk across sub-queries, dedup
             ─▶ top-K = [(fused, Chunk)]

`mode` mirrors the α knob: "hybrid" uses the configured α, "semantic" is
α=1 (Day 6's baseline, recoverable verbatim for an apples-to-apples
comparison), "lexical" is α=0. The regression contract is symmetric with Day
7: `fixtures.py` stays green on the improved retriever, and the out-of-corpus
gate (`gate.should_refuse`) still speaks the *fused* score — a retriever that
manufactured a lexical boost for a question with zero corpus overlap would be
a regression wearing an improvement's clothes.
"""
from __future__ import annotations

import re
from typing import Any, Sequence

from ai_os.lexicon import Lexicon, tokenize
from ai_os.rag import Chunk, DimensionMismatch
from ai_os.search import dot, normalize

# ------------------------------------------------- deterministic rule tables
# The corpus's vocabulary as known so far — user phrasing → mechanism words
# the docs actually speak. Every phrase forgotten here is a lexical-gap-shaped
# hole; that is why the gateway rewrite tier stays `PENDING` and why fixtures
# deliberately include trap questions.
REWRITES: dict[str, str] = {
    "why is my cart stuck": "checkout requires a shipping method and valid "
                            "addresses",
    "cart is stuck":        "checkout requires a shipping method and valid "
                            "addresses",
    "cancel an order":      "cancel an order with orderCancel after payment",
    "fix checkout":         "common checkout errors during completion",
}

# Query phrasing → section. A section match narrows the scored set; an
# *ambiguous* query (2+ hits) deliberately gets no filter — the len==1 rule
# means filtering is never what loses an expected doc. Trigger terms are the
# user's words; the prefixes are every corpus path where that section's docs
# actually live (a "right" filter must contain the answer, so a section whose
# docs straddle folders lists each folder — e.g. checkout lives under
# developer/checkout/ AND quickstart/api/).
SECTIONS: dict[str, set[str]] = {
    "checkout":   {"checkout", "cart", "checkoutcomplete"},
    "order":      {"order", "orders", "fulfillment"},
    "payments":   {"payment", "payments", "refund"},
    "attributes": {"attribute", "attributes"},
    "apps":       {"app", "apps", "webhook"},
}
SECTION_PREFIXES: dict[str, list[str]] = {
    "checkout":   ["developer/checkout", "quickstart/api"],
    "order":      ["developer/order"],
    "payments":   ["developer/payments"],
    "attributes": ["developer/attributes"],
    "apps":       ["developer/extending/apps"],
}

# Coordination that splits one user intent into several (Day 7 Family B).
# "and"/"then"/"but" are pure connectors; the *content* after them stays part
# of the sub-question ("what changes after?" is a usable sub-query in its own
# right — the notes' run used exactly that phrasing for the order-lifecycle
# half). Fragments with no content (a terminal "?") are dropped.
SPLIT_RE = re.compile(r"\b(?:and|then|but|or)\b", re.IGNORECASE)


# ----------------------------------------------------------------- rules

def rewrite(question: str, rewrites: dict[str, str] | None = None) -> str:
    """MECH: deterministic rule-table rewrite; pass-through when no rule fires.

    The gateway rewrite tier is the `PENDING` extension point — a callable
    that hands back a rewritten string and costs one chat line. Today's tier
    is cheap, testable, and the version the fixtures can re-run.
    """
    q = question.strip()
    table = REWRITES if rewrites is None else rewrites
    for key, val in table.items():
        if key in q.lower():
            return val
    return q


def decompose(question: str, cap: int = 3) -> list[str]:
    """MECH: split a question on coordination into ≤ `cap` sub-questions.

    The cap is a *budget* knob, exactly like K in Day 6: it bounds worst
    case at `cap × embed + cap × scan`. Splitting is lexical today; the
    semantic planner is Day 16's job — today ships the seam.
    """
    parts = [s.strip() for s in SPLIT_RE.split(question)]
    return [s for s in parts if s and any(ch.isalnum() for ch in s)][:cap]


def rule_section(query: str,
                 sections: dict[str, set[str]] | None = None) -> str | None:
    """MECH: the section a query names, *if it names exactly one* (else None).

    `len(hits) == 1` is the whole design: an ambiguous query (2+ sections hit)
    gets no filter rather than a filter flip. A metadata filter must never be
    the mechanism that loses an expected doc. Returns the section *key*; the
    caller maps it to prefixes via `SECTION_PREFIXES`.
    """
    table = SECTIONS if sections is None else sections
    q = set(tokenize(query))
    hits = [sec for sec, terms in table.items() if q & terms]
    return hits[0] if len(hits) == 1 else None


def fuse_score(alpha: float, sem: float, lex: float) -> float:
    """MECH: the one-line fusion. α weights the semantic vote, (1−α) the
    lexical vote; both inputs must be comparable units ([0, 1] bands)."""
    return alpha * sem + (1.0 - alpha) * lex


# -------------------------------------------------------------- Retriever

class Retriever:
    """Owns the full retrieval flow and returns the Day-6 shape `[(score, Chunk)]`.

    Modes mirror the α knob: "hybrid" (configured α, rewrite+decompose on),
    "semantic" (α=1, the Day-6 baseline, verbatim), "lexical" (α=0). An
    explicit `alpha=` overrides the mode's; that override is what the Day-8
    α sweep sweeps.
    """

    _MODE_ALPHA = {"hybrid": None, "semantic": 1.0, "lexical": 0.0}

    def __init__(self, gateway, store, *, alpha: float = 0.5,
                 decompose_cap: int = 3, rewrites: dict[str, str] | None = None,
                 sections: dict[str, set[str]] | None = None,
                 section_prefixes: dict[str, list[str]] | None = None) -> None:
        if not 0.0 <= alpha <= 1.0:
            raise ValueError(f"alpha must be in [0, 1], got {alpha}")
        if decompose_cap < 1:
            raise ValueError(f"decompose_cap must be >= 1, got {decompose_cap}")
        self.gateway = gateway
        self.store = store
        self.alpha = alpha
        self.decompose_cap = decompose_cap
        self.rewrites = dict(REWRITES if rewrites is None else rewrites)
        self.sections = dict(SECTIONS if sections is None else sections)
        self.section_prefixes = dict(SECTION_PREFIXES
                                     if section_prefixes is None
                                     else section_prefixes)
        self.lexicon = Lexicon(store.chunks) if store.chunks else None
        self.embeds = 0               # ledger: one line per embed() call
        self.filter_misses = 0        # wrong/empty filters, annotated, counted
        self.last_query: dict[str, Any] = {}

    # ------------------------------------------------------------ retrieve

    def retrieve(self, question: str, k: int = 5, *, mode: str = "hybrid",
                 alpha: float | None = None,
                 filter: dict[str, str] | None = None,
                 do_rewrite: bool = True, do_decompose: bool = True,
                 ) -> list[tuple[float, Chunk]]:
        """Top-K fused retrieval; `[(score, Chunk)]` ready for `build_context`.

        `mode="semantic"` forces the Day-6 baseline verbatim (no rewrite, no
        decompose, α=1) so the comparison is apples-to-apples. An explicit
        `alpha=` sweeps the fusion while mode stays "hybrid".
        """
        if mode not in self._MODE_ALPHA:
            raise ValueError(f"unknown mode {mode!r} "
                             f"(known: {sorted(self._MODE_ALPHA)})")
        eff_alpha = self._MODE_ALPHA[mode]
        if eff_alpha is None:
            eff_alpha = self.alpha if alpha is None else alpha
        if not 0.0 <= eff_alpha <= 1.0:
            raise ValueError(f"alpha must be in [0, 1], got {eff_alpha}")

        if mode == "semantic":
            do_rewrite = False
            do_decompose = False

        q = rewrite(question, self.rewrites) if do_rewrite else question.strip()
        if not q:
            raise ValueError("question must not be empty")
        subs = decompose(q, self.decompose_cap) if do_decompose else [q]

        prefixes: list[str] | None = None
        if filter and "doc_prefix" in filter:
            prefixes = [filter["doc_prefix"]]
        elif filter and "doc_prefixes" in filter:
            prefixes = list(filter["doc_prefixes"])
        elif do_rewrite:
            section = rule_section(q, self.sections)
            if section is not None:
                prefixes = self.section_prefixes.get(section)
        candidates, used_filter, filter_miss = self._candidates(prefixes)

        # max-score merge per chunk id across sub-queries, then dedup+rank.
        best: dict[int, float] = {}
        for sub in subs:
            qvec = self._embed(sub)
            for i, score in zip(candidates,
                                self._fuse(qvec, sub, candidates, eff_alpha)):
                if score > best.get(i, -1e9):
                    best[i] = score
        # exact Day-6 tie-break: (score asc, then chunk_id asc), last k, reversed
        # → equal scores sort by chunk_id descending, byte-for-byte the seam's.
        ranked = sorted(best,
                        key=lambda i: (best[i], self.store.chunks[i].chunk_id),
                        reverse=True)[:k]

        self.last_query = {
            "mode": mode, "alpha": eff_alpha, "original": question,
            "rewritten": q, "sub_queries": subs, "embeds": len(subs),
            "filter": prefixes or None,
            "filter_miss": filter_miss,
        }
        self.embeds += len(subs)
        return [(float(best[i]), self.store.chunks[i]) for i in ranked]

    # ----------------------------------------------------------- internals

    def _candidates(self, prefixes: list[str] | None
                    ) -> tuple[list[int], bool, bool]:
        """Store indices to score: all chunks, or the ones under any prefix.

        A prefix set that matches nothing degrades to the full corpus —
        annotated as a filter-miss, never a crash (a wrong filter is a
        query-classifier failure, and the annotation is exactly what Day 20's
        evaluators need).
        """
        if not prefixes:
            return list(range(len(self.store))), False, False
        idx = [i for i, c in enumerate(self.store.chunks)
               if any(c.doc_id.startswith(p) for p in prefixes)]
        if idx:
            return idx, True, False
        self.filter_misses += 1
        return list(range(len(self.store))), False, True

    def _embed(self, text: str) -> list[float]:
        emb = self.gateway.embed([text])[0]
        if self.store.dimension and len(emb.vector) != self.store.dimension:
            raise DimensionMismatch(
                f"query dim {len(emb.vector)} != store dim {self.store.dimension}"
                " — query was embedded by a different model")
        return normalize(emb.vector)

    def _fuse(self, qvec: list[float], query: str,
              candidates: Sequence[int], alpha: float) -> list[float]:
        """Per-candidate fused scores for one sub-query, in candidate order.

        Semantic half is Day 5's cosine (stored vectors are normalized at
        write time, `qvec` is normalized above). Lexical half is the
        corpus-max-normalized Lexicon score, indexed by store position. Both
        bands are [0, 1]-ish, so `fuse_score`'s weighted sum is honest.
        """
        sem = [dot(qvec, self.store.chunks[i].vector) for i in candidates]
        lex: list[float] = []
        if self.lexicon is not None and query:
            tokens = tokenize(query)
            all_lex = self.lexicon.normalized(tokens)
            lex = [all_lex[i] for i in candidates]
        else:
            lex = [0.0] * len(candidates)
        return [fuse_score(alpha, s, l) for s, l in zip(sem, lex)]