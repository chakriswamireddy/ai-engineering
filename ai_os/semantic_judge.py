"""Day 21 §6 — layer 2 of `judge.py`: citation correctness, groundedness,
answer relevance — graded as *claim-coverage over the run's own evidence*.

Day 20 parked three semantic cells with an owner printed on each ("Day 21").
Day 21's theorem is that all three are one discipline read three ways, and that
the discipline is honest only because the golden set pre-registered the claims:

- **citation correctness** splits into the mechanical half Day 6 owns
  (`cited ⊆ retrieved`, checked by `judge.py`'s layer 1) and the *support* half
  this module adds — the cited doc must state the claim it is cited for. A
  citation can be real and wrong; membership alone cannot see it (the K-row).
- **groundedness** is `claims ⊆ evidence`: every asserted claim must trace to
  the context the run actually showed. A claim the answer asserts with no line
  of evidence in that context is a hallucination **by definition**, however
  fluent the prose (the F-row).
- **answer relevance** is claim-coverage: the fraction of pre-registered
  `claims[]` the answer asserted, with a `must_not_assert[]` hit always a fail
  of class *forbidden* (the J-row).

The deterministic subnet decides what membership can decide: a claim is
**deducible** when, after the series' normalization (stopword-fold, stem
match — `eval_dataset.claim_derivable`, which reuses Day 8's `Lexicon`
stopwords), its content terms appear in the evidence. A claim the subnet
cannot decide is **undecided**: recorded with an owner, optionally explained
by the flagged lane, never silently passed. A semantic verdict that cannot
re-run is Day 20's drift problem wearing a fancier hat, so — like `judge.py` —
this module never re-answers; it reads the run's own context and answer, and
`deterministic_verdict` is untouched by anything model-graded.
"""
from __future__ import annotations

from typing import Any, Iterable, Sequence

from ai_os.eval_dataset import claim_derivable

# One owner per semantic symptom (Day 7's isolation map, extended).
SEMANTIC_OWNERS: dict[str, str] = {
    "citation_support": "semantic judge — the cited doc must support the claim",
    "groundedness": "generation — an asserted claim with no shown evidence",
    "coverage": "generation — a pre-registered claim the answer never asserted",
    "must_not": "generation — a forbidden assertion (a category error)",
}

# `EVAL_JUDGE` lanes shared with judge.py: the paid lane is off by default.
SEMANTIC_CELLS = ("citation_correctness", "groundedness", "answer_relevance")


def _cited_texts(citations: Sequence[str],
                 corpus_text: dict[str, str] | None) -> dict[str, str]:
    if not corpus_text:
        return {}
    return {c: corpus_text[c] for c in citations if c in corpus_text}


def citation_support(citations: Sequence[str], claim: str,
                     corpus_text: dict[str, str] | None) -> bool:
    """Does any cited doc state the claim's key terms? (the support half of R5)

    A containment probe by default — `claim_derivable` against the *cited*
    doc's text only, so a citation of an in-context-but-irrelevant page (the
    K-row) fails here while passing `cited ⊆ retrieved`.
    """
    for c in citations:
        text = (corpus_text or {}).get(c)
        if text and claim_derivable(claim, [text]):
            return True
    return False


def grade_claims(*, answer: str, claims: Sequence[str],
                 must_not_assert: Sequence[str] = (),
                 context_texts: Sequence[str],
                 citations: Sequence[str] = (),
                 corpus_text: dict[str, str] | None = None,
                 graded_by: str = "deterministic") -> dict[str, Any]:
    """The deterministic subnet over one run's claims — the semantic verdict.

    Reads only the run's own `answer` and the context it actually showed; it
    never regenerates. Returns the three cells plus the undecided residue,
    each with its owner, and a `graded` flag that is `False` (everything
    `n/a`) when the row pre-registered nothing semantic to grade.
    """
    answer = answer or ""
    claims = list(claims)
    must_not_assert = list(must_not_assert)
    context_texts = list(context_texts)

    asserted = [c for c in claims if claim_derivable(c, [answer])]
    uncovered = [c for c in claims if c not in asserted]
    forbidden = [c for c in must_not_assert if claim_derivable(c, [answer])]

    # Groundedness: what the answer asserted must trace to the shown context.
    ungrounded = [c for c in asserted if not claim_derivable(c, context_texts)]

    # Citation support: each asserted claim needs a citation that states it.
    unsupported = [c for c in asserted
                   if not citation_support(citations, c, corpus_text)]

    graded = bool(claims or must_not_assert)
    coverage = (len(asserted) / len(claims)) if claims else None

    return {
        "graded": graded,
        "graded_by": graded_by,
        "asserted": asserted,
        "uncovered": uncovered,
        "coverage": coverage,
        "coverage_ok": (not uncovered) if claims else None,
        "forbidden": forbidden,
        "must_not_ok": not forbidden,
        "ungrounded": ungrounded,
        "groundedness_ok": (not ungrounded) if asserted else None,
        "unsupported": unsupported,
        "citation_support_ok": (not unsupported) if asserted else None,
        "undecided": ungrounded,  # the residue the flagged lane may explain
        "owners": dict(SEMANTIC_OWNERS),
    }


def semantic_verdict(sem: dict[str, Any]) -> str:
    """`pass` · `fail` · `n/a` from the three cells (never flips a MECH row)."""
    if not sem.get("graded"):
        return "n/a"
    for key in ("citation_support_ok", "groundedness_ok", "coverage_ok",
                "must_not_ok"):
        value = sem.get(key)
        if value is False:
            return "fail"
    return "pass"


def semantic_owner(sem: dict[str, Any]) -> str | None:
    """The one owner the first failing semantic cell names."""
    if sem.get("citation_support_ok") is False:
        return "semantic judge"
    if sem.get("groundedness_ok") is False or sem.get("coverage_ok") is False \
            or sem.get("must_not_ok") is False:
        return "generation"
    return None


# ------------------------------------------------- the flagged (paid) lane
# Off by default; 1 chat per *undecided* claim; writes only to `semantic:`
# with `graded_by: model`; never flips a deterministic verdict. The lane's
# first bill is a number in the report — semantics priced before paid.

MODEL_CLAIM_PROMPT = (
    "A deterministic judge could not decide whether this claim is supported by "
    "the evidence. Answer with a single word, `supported` or `unsupported` — "
    "do not re-answer the question.\nClaim: {claim}\nEvidence: {evidence}\n")


def model_explain(gateway, sem: dict[str, Any], context_texts: Sequence[str]
                  ) -> dict[str, Any]:
    """Price and explain the undecided claims — bounded, budgeted, advice only."""
    notes: list[dict[str, Any]] = []
    for claim in sem.get("undecided") or []:
        prompt = MODEL_CLAIM_PROMPT.format(
            claim=claim, evidence=" ".join(context_texts)[:1200] or "(none)")
        completion = gateway.chat([{"role": "user", "content": prompt}],
                                  temperature=0.0, max_tokens=8)
        notes.append({
            "claim": claim,
            "verdict": (completion.text or "").strip().lower(),
            "model": getattr(completion, "model", None),
        })
    return {
        "graded_by": "model",
        "claims": notes,
        "cost": len(notes),
        "disclaimer": "stochastic — explains an undecided claim; the "
                      "deterministic verdict stands",
    }
