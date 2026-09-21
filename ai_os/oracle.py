"""Day 7 §4 — the oracle substitution isolation test.

The heart of the day is the pair of substitution experiments:

    Test 1 (retrieval-only, `trace.retrieve_trace`): does the expected doc
            rank inside top-K? Pure function of embedder + index.
    Test 2 (oracle, this module): bypass retrieval entirely and inject the
            *expected* document as the only context. Same question, perfect
            evidence.

The verdict table these two fill in together:

    | retrieval | oracle             | verdict                       |
    |-----------|--------------------|-------------------------------|
    | missing   | —                  | retrieval failure  (Day 8)    |
    | present   | correct            | retrieval let it through but  |
    |           |                    | the pipeline lost it (Day 9)  |
    | present   | wrong too          | generation failure (Day 20/21)|

The oracle is the only test that separates "searched badly" from "reads
badly", and it costs one extra chat call per question.
"""
from __future__ import annotations

from typing import Any, Iterable

from ai_os.rag import NOT_COVERED, SYSTEM_PROMPT, citation


def chunks_for_doc_ids(store, doc_ids: Iterable[str]) -> list[Any]:
    """All chunks whose `doc_id` is in `doc_ids`, in store order.

    A doc_id is a heading section (e.g. `quickstart/api#create-a-checkout`),
    which may be split into several chunks by position — the oracle wants the
    *whole* section, so every chunk of every expected doc is injected.
    """
    want = set(doc_ids)
    if not want:
        raise ValueError("oracle needs at least one expected doc_id")
    present = {c.doc_id for c in store.chunks}
    missing = sorted(want - present)
    if missing:
        raise ValueError(
            f"oracle: expected docs not in the store (pre-registration bug): "
            f"{missing}")
    return [c for c in store.chunks if c.doc_id in want]


def oracle_test(gateway, store, question: str, expected_doc_ids: Iterable[str],
                *, budget_chars: int = 8000, max_tokens: int = 2048,
                temperature: float = 0.0) -> dict[str, Any]:
    """Inject the expected documentation as the *only* context, ask again.

    Returns a run-shaped dict: the answer, the injected chunks surfaced as
    citations (so `cited ⊆ expected` is checkable), and the context the model
    actually saw. An empty question or an empty injection is a caller bug,
    not a corpus condition — raise.
    """
    q = question.strip()
    if not q:
        raise ValueError("question must not be empty")
    expected = list(expected_doc_ids)
    chunks = chunks_for_doc_ids(store, expected)

    parts: list[str] = []
    used = 0
    kept: list[Any] = []
    for c in chunks:
        if used + len(c.text) > budget_chars:
            continue
        parts.append(f"[{c.heading}]\n{c.text}")
        used += len(c.text)
        kept.append(c)
    if not parts:
        raise ValueError(
            f"oracle: expected docs exceed the {budget_chars}-char budget — "
            "raise budget_chars for this question")

    context = "\n\n".join(parts)
    system = SYSTEM_PROMPT.format(used=used, not_covered=NOT_COVERED,
                                  context=context)
    completion = gateway.chat(
        [{"role": "system", "content": system},
         {"role": "user", "content": q}],
        temperature=temperature, max_tokens=max_tokens)

    return {
        "answer": completion.text,
        "citations": [citation(c) for c in kept],
        "refused": False,
        "ok": None,                      # an answer's *correctness* is semantic
        "context_chars": used,
        "context": context,
        "expected": expected,
        "cited_subset_expected": True,   # citations ARE the injected docs
        "generation": {
            "model": completion.model,
            "provider": getattr(gateway, "last_provider", None),
            "input_tokens": completion.usage.input_tokens,
            "output_tokens": completion.usage.output_tokens,
            "attempts": getattr(gateway, "last_attempts", None),
        },
    }