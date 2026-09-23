"""Day 8 §4 — the hand-rolled lexical scorer (the keyword half of hybrid retrieval).

Day 7's lexical gap: *"how do I cancel an order"* shares no surface tokens
with a chunk about `orderCancel`, so cosine — which lives on meaning, not
tokens — routes the query to whatever its averaged region contains. Keyword
scoring flips the failure: the tokens *are* what the docs use. This module is
that scorer, stdlib-only, ~50 lines, every trade-off owned (there is no
`rank-bm25` hiding the choices):

    - **IDF over set-membership, not counts.** A chunk saying "checkout" six
      times is not six times more relevant. Saturation (count capped at 1)
      keeps rare-token signal and stops a keyword-stuffed chunk laundering
      its relevance.
    - **Stopword removal before matching.** "How do I cancel an order" →
      `{cancel, order}`. `how/do/i` correlate with nothing.
    - **Normalization by corpus-max per query.** The raw score is an
      unbounded IDF sum; dividing by the corpus max lands it in [0, 1], the
      same band as cosine — the precondition for the α-fusion
      (`retrieval.fuse_score`) to mean anything. A query with no corpus-token
      overlap scores all zeros (the refusal path in lexical disguise).

Everything here is `MECH`: raw arithmetic and string ops, no model, fully
deterministic.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Any, Callable, Iterable

# Words that correlate with nothing: how/do/i are question scaffolding,
# be/of/for are grammar. None of them should contribute IDF.
STOP = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "of", "for", "to", "in", "on", "at", "by", "with", "from", "and", "or",
    "but", "how", "what", "when", "where", "why", "which", "who", "whom",
    "do", "does", "did", "i", "you", "we", "it", "its", "this", "that",
    "these", "those", "there", "about", "into", "than", "as", "not", "no",
}

TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Lowercase → split on non-word chars → drop stopwords."""
    return [t for t in TOKEN_RE.findall(text.lower()) if t not in STOP]


class Lexicon:
    """IDF-over-set-membership scorer, built once over a store's chunks.

    Eager at construction (one pass: document-frequency counts + idf table),
    then `score` / `normalized` are per-query arithmetic. The chunk term-sets
    are parallel to the store's `chunks` list, so a chunk *index* is a chunk's
    lexical identity — the same index `Retriever` hands back in its results.

    `tokenizer` is Day 10's seam: the default is the prose `tokenize`; the code
    corpus passes an identifier-aware splitter (`retrieval.code_tokens`) so
    `checkoutComplete`, `complete_checkout`, and the phrase "complete checkout"
    fall into the same lexical bucket. The scorer itself does not change.
    """

    def __init__(self, chunks: Iterable[Any],
                 tokenizer: Callable[[str], list[str]] | None = None) -> None:
        tok = tokenize if tokenizer is None else tokenizer
        self.chunk_terms: list[set[str]] = [set(tok(c.text)) for c in chunks]
        df: dict[str, int] = defaultdict(int)
        for terms in self.chunk_terms:
            for t in terms:
                df[t] += 1
        n = len(self.chunk_terms)
        self.idf: dict[str, float] = {
            t: math.log(1.0 + n / (1.0 + count))   # smoothed: a 0-df token
            for t, count in df.items()             # is a missing word, not a
        }                                          # `-inf` that kills the query

    # ----------------------------------------------------------- scoring

    def score(self, query_tokens: Iterable[str]) -> list[float]:
        """Raw per-chunk score: sum of IDF over the query tokens the chunk
        contains. Saturation at both ends: repeated query tokens count once
        (a set), and chunk term-sets already collapsed in-chunk repetition.
        """
        want = set(query_tokens) & set(self.idf)
        return [sum(self.idf.get(t, 0.0) for t in want if t in terms)
                for terms in self.chunk_terms]

    def normalized(self, query_tokens: Iterable[str]) -> list[float]:
        """Raw scores scaled to [0, 1] by the corpus max for *this* query.

        All-zero query (no token in the corpus) → all zeros, verbatim: the
        reciprocal of the lexical boost, so an out-of-corpus question can't
        acquire one by keyword coincidence.
        """
        raw = self.score(query_tokens)
        m = max(raw, default=0.0)
        if m == 0.0:
            return [0.0] * len(raw)
        return [v / m for v in raw]