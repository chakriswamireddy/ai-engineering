"""Day 7 §4 — the per-query retrieval trace and the verdict decision tree.

Day 6 produced fluent, cited answers and declared their semantics
`PENDING`. This module is the observability seam that turns a wrong answer
into evidence: one record per question carrying the retrieval top-K
(doc/heading/score/rank), the pre-registered expected doc set, every
mechanical metric the notes mark `MECH`, and the run's answer/citations.

From the outside a retrieval miss and a generation failure are
indistinguishable — same fluent, confident, cited output. The trace exists
so the two are separated by experiment, not opinion:

  - `retrieve_trace` is the retrieval-only half (no LLM): does the expected
    doc rank? At what rank? Which chunks survive the context budget?
  - `run_trace` adds the run's own answer/citations from the *same* retrieval.
  - `classify_trace` walks the §4 decision tree and returns a verdict. Every
    branch before "is the answer right?" is `MECH`; the last one needs the
    oracle test (`oracle.py`) or a real reading — and is the only place the
    verdict is allowed to be `PENDING`.

Convention: a trace is a plain dict (JSON-serializable) so a run can dump
the whole thing next to the report.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Sequence

from ai_os.gate import score_gap, should_refuse, top_score
from ai_os.rag import (
    Chunk,
    NOT_COVERED,
    SYSTEM_PROMPT,
    build_context,
    citation,
    k_from_budget,
)

# --------------------------------------------------------------- verdicts
# The §4 decision tree's terminals. Each routes to exactly one owning day.

VERDICT_OK = "ok"
VERDICT_MISSING_RETRIEVAL = "missing-retrieval"
VERDICT_CONTEXT = "context"
VERDICT_CONTEXT_QUALITY = "context-quality"
VERDICT_GENERATION = "generation"
VERDICT_CORPUS_GAP = "corpus-gap"
VERDICT_PENDING = "pending"

VERDICTS: dict[str, str] = {
    VERDICT_OK: "answerable and expected docs grounded the answer",
    VERDICT_MISSING_RETRIEVAL:
        "expected doc absent from top-K → retrieval (Day 8)",
    VERDICT_CONTEXT:
        "expected retrieved but dropped by the budget floor → "
        "context construction (Day 9)",
    VERDICT_CONTEXT_QUALITY:
        "expected present but near-duplicates crowd it → "
        "context quality (Day 9)",
    VERDICT_GENERATION:
        "expected in context yet the answer is wrong / refused → "
        "generation (Day 20/21)",
    VERDICT_CORPUS_GAP:
        "no page answers the question; refusing is the correct behaviour",
    VERDICT_PENDING:
        "needs a real run / semantic judgement (or oracle) to decide",
}

# ≥ this duplicate ratio in the *kept* context counts as context pollution
# (a Day 9 problem) rather than an isolated coincidence.
DUP_RATIO_HIGH = 0.35

# ------------------------------------------------------------- mechanics

def duplicate_ratio(texts: Sequence[str]) -> float:
    """MECH: 1 − distinct/total texts. 0.0 for < 2 chunks.

    Both duplication sources land here: the overlap *seam* (the same 40 chars
    in two adjacent chunks) and *near-duplicate pages* (different chunks, the
    same fact served twice). The ratio is arithmetic; whether the duplicated
    span is the answer is semantic and stays `PENDING`.
    """
    if len(texts) < 2:
        return 0.0
    return 1.0 - len(set(texts)) / len(texts)


def duplicate_count(texts: Sequence[str]) -> int:
    """MECH: how many texts repeat a text that appeared earlier."""
    seen: set[str] = set()
    repeats = 0
    for t in texts:
        if t in seen:
            repeats += 1
        seen.add(t)
    return repeats


def expected_stats(expected: Iterable[str],
                   doc_ids: Sequence[str]) -> dict[str, Any]:
    """MECH: rank each expected doc inside a retrieved/kept doc list.

    Returns per-expected membership booleans, best rank, recall@K and MRR
    over `doc_ids` — the machine half of the failure taxonomy.
    """
    rank: dict[str, int] = {}
    for i, d in enumerate(doc_ids):
        rank.setdefault(d, i + 1)
    in_list: list[bool] = []
    best_rank: list[int | None] = []
    for e in expected:
        r = rank.get(e)
        in_list.append(r is not None)
        best_rank.append(r)
    exp = list(expected)
    recall = float(sum(in_list)) / len(in_list) if exp else None
    first = [r for r in best_rank if r]
    mrr = 1.0 / min(first) if first else 0.0
    return {
        "in_list": in_list,
        "best_rank": best_rank,
        "recall_at_k": recall,
        "mrr": mrr,
    }


def all_expected(store, expected: Iterable[str]) -> list[Chunk]:
    """Chunks whose doc_id is in `expected`, in store order.

    Used by the oracle and by rare callers that want the full text of the
    expected documents without guessing positions.
    """
    want = set(expected)
    if not want:
        return []
    return [c for c in store.chunks if c.doc_id in want]


# ---------------------------------------------------------------- traces

def _retrieve(gateway, store, question: str, *, k: int | None,
              budget_chars: int, tau: float | None):
    """Shared retrieval: embed, scan, budget-slice, and return everything the
    trace and the generation half need (kept chunks included, model-free)."""
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
    return emb, k, results, context, used, kept


def _score_dicts(results: Sequence[tuple[float, Chunk]]
                 ) -> list[dict[str, Any]]:
    """Serializable top-K table used by the trace and by `cited ⊆ retrieved`."""
    return [
        {"rank": i + 1, "score": float(s),
         "doc_id": c.doc_id, "chunk_id": c.chunk_id,
         "heading": c.heading, "chars": len(c.text)}
        for i, (s, c) in enumerate(results)
    ]


def retrieve_trace(gateway, store, question: str, *,
                   expected: Iterable[str] = (), k: int | None = None,
                   budget_chars: int = 1200, tau: float | None = None,
                   ) -> dict[str, Any]:
    """Test 1 — retrieval-only trace: embed the query, scan the index, build
    the context. No LLM, fully deterministic: the retrieval half of every
    verdict and the model-free source for the report's `Retrieved documents`
    column.
    """
    emb, k, results, context, used, kept = _retrieve(
        gateway, store, question, k=k, budget_chars=budget_chars, tau=tau)
    expected = list(expected)
    kept_docs = [c.doc_id for _, c in kept]
    kept_texts = [c.text for _, c in kept]
    ret = expected_stats(expected, [c.doc_id for _, c in results])
    ctx = expected_stats(expected, kept_docs)

    return {
        "id": None,
        "family": None,
        "question": question,
        "k": k,
        "embed_model": emb.model,
        "embed_input_tokens": emb.usage.input_tokens,

        "retrieved": _score_dicts(results),
        "top1": top_score(results),
        "top1_minus_topK": score_gap(results, k),
        "scores": [float(s) for s, _ in results],

        "expected": expected,
        "expected_in_retrieved": ret["in_list"],
        "expected_best_rank": ret["best_rank"],
        "recall_at_k": ret["recall_at_k"],
        "mrr": ret["mrr"],

        "context_chars": used,
        "context_budget": budget_chars,
        "context_utilization": (used / budget_chars if budget_chars else 0.0),
        "kept_doc_ids": kept_docs,
        "expected_in_context": ctx["in_list"],
        "kept_duplicate_ratio": duplicate_ratio(kept_texts),
        "duplicate_chunks": duplicate_count(kept_texts),
        "retrieved_duplicate_ratio": duplicate_ratio(
            [c.text for _, c in results]),

        "gate_refused": (should_refuse(results, tau) if tau is not None
                         else None),

        "answer": None,
        "citations": [],
        "refused": None,
        "generation": None,
        "oracle": None,
        "verdict": VERDICT_PENDING,
    }


def run_trace(gateway, store, question: str, *,
              expected: Iterable[str] = (), k: int | None = None,
              budget_chars: int = 1200, tau: float | None = None,
              max_tokens: int = 2048, temperature: float = 0.0,
              with_generation: bool = True) -> dict[str, Any]:
    """A trace plus the run's own answer and citations from the *same*
    retrieval — so `cited ⊆ retrieved` holds by construction and the verdict
    blames the layer it can prove.

    The similarity gate (`tau`) is *recorded* as `gate_refused` but not yet
    enforced: Day 7 measures what the gate would have done next to what the
    model actually did; Day 21 flips the decision into the pipeline.
    """
    emb, k, results, context, used, kept = _retrieve(
        gateway, store, question, k=k, budget_chars=budget_chars, tau=tau)
    expected = list(expected)
    kept_docs = [c.doc_id for _, c in kept]
    kept_texts = [c.text for _, c in kept]
    ret = expected_stats(expected, [c.doc_id for _, c in results])
    ctx = expected_stats(expected, kept_docs)

    trace = {
        "id": None,
        "family": None,
        "question": question,
        "k": k,
        "embed_model": emb.model,
        "embed_input_tokens": emb.usage.input_tokens,
        "retrieved": _score_dicts(results),
        "top1": top_score(results),
        "top1_minus_topK": score_gap(results, k),
        "scores": [float(s) for s, _ in results],
        "expected": expected,
        "expected_in_retrieved": ret["in_list"],
        "expected_best_rank": ret["best_rank"],
        "recall_at_k": ret["recall_at_k"],
        "mrr": ret["mrr"],
        "context_chars": used,
        "context_budget": budget_chars,
        "context_utilization": (used / budget_chars if budget_chars else 0.0),
        "kept_doc_ids": kept_docs,
        "expected_in_context": ctx["in_list"],
        "kept_duplicate_ratio": duplicate_ratio(kept_texts),
        "duplicate_chunks": duplicate_count(kept_texts),
        "retrieved_duplicate_ratio": duplicate_ratio(
            [c.text for _, c in results]),
        "gate_refused": (should_refuse(results, tau) if tau is not None
                         else None),
        "answer": None,
        "citations": [],
        "refused": None,
        "generation": None,
        "oracle": None,
        "verdict": VERDICT_PENDING,
    }

    if not with_generation:
        return trace
    if not kept:
        trace["answer"] = NOT_COVERED
        trace["citations"] = [dict(r) for r in _score_dicts(results)]
        trace["refused"] = True  # empty context → honest refusal *by code*
        trace["verdict"] = classify_trace(trace)
        return trace

    system = SYSTEM_PROMPT.format(used=used, not_covered=NOT_COVERED,
                                  context=context)
    try:
        completion = gateway.chat(
            [{"role": "system", "content": system},
             {"role": "user", "content": question}],
            temperature=temperature, max_tokens=max_tokens)
    except Exception:
        # chat is the quota-limited half of the free tier — a trace must not
        # die for it. answer stays None; the verdict falls back to PENDING and
        # the retrieval half (the part this module guarantees) is complete.
        return trace

    trace["answer"] = completion.text
    trace["citations"] = _score_dicts(results)
    trace["refused"] = False
    trace["generation"] = {
        "model": completion.model,
        "provider": getattr(gateway, "last_provider", None),
        "input_tokens": completion.usage.input_tokens,
        "output_tokens": completion.usage.output_tokens,
        "attempts": getattr(gateway, "last_attempts", None),
    }
    trace["verdict"] = classify_trace(trace)
    return trace


# --------------------------------------------------------- the decision tree

def classify_trace(trace: dict[str, Any], oracle: dict[str, Any] | None = None
                   ) -> str:
    """The §4 decision tree as a function of the trace (+ optional oracle).

    Expected docs non-empty:
        ∉ top-K                      → MISSING RETRIEVAL  (Day 8)  [MECH]
        ∈ top-K, ∉ prompt            → CONTEXT            (Day 9)  [MECH]
        ∈ prompt, model refused      → GENERATION (false refusal)  [MECH]
    Expected docs empty (E-family):
        refused                      → CORPUS GAP — refusing is correct
        answered                     → GENERATION (invented an answer)
    Expected ∈ prompt, answer given:
        oracle wrong too             → GENERATION         (Day 20/21)
        oracle right + dupes crowd   → CONTEXT QUALITY    (Day 9)
        oracle right                 → OK
        no oracle                    → PENDING (needs a real reading)

    Returns a VERDICT_* key (VERDICTS maps keys to human text).
    """
    expected = list(trace.get("expected") or [])
    refused = bool(trace.get("refused"))

    if expected:
        if not all(trace["expected_in_retrieved"]):
            return VERDICT_MISSING_RETRIEVAL
        if not all(trace["expected_in_context"]):
            return VERDICT_CONTEXT
        if refused:
            return VERDICT_GENERATION  # evidence was in the prompt anyway
    else:
        # E-family: no page can answer. Refusing (by code, by gate, or by the
        # model) is correct; answering is an invented answer (generation).
        if refused:
            return VERDICT_CORPUS_GAP
        if trace.get("answer") is not None:
            return VERDICT_GENERATION
        if trace.get("gate_refused"):
            return VERDICT_CORPUS_GAP
        return VERDICT_PENDING

    if oracle is not None:
        if oracle.get("ok"):
            if (trace.get("kept_duplicate_ratio") or 0.0) >= DUP_RATIO_HIGH:
                return VERDICT_CONTEXT_QUALITY
            return VERDICT_OK
        return VERDICT_GENERATION
    return VERDICT_PENDING


def attach_oracle(trace: dict[str, Any], oracle: dict[str, Any]) -> dict[str, Any]:
    """Mutate a trace in place with its oracle verdict and return the trace.

    The run's own answer stays in `answer` (that is the behaviour being
    debugged); the oracle's answer lives under `oracle.answer` so the report
    can compare the two. Only the *verdict* is recomputed.
    """
    trace["oracle"] = oracle
    trace["verdict"] = classify_trace(trace, oracle)
    return trace


# ======================================================== Day 12 — agent loop
# Day 7's per-query trace becomes Day 12's per-step trace: a step is as
# auditable as a single-shot query was, because the row shape is the same. The
# run object adds the fields the loop owns — why it stopped, how many steps,
# whether it truncated — so termination is part of the answer, not a debug
# detail.

AGENT_TERMINATIONS: dict[str, str] = {
    "answer": "exit 1 — the model answered from the evidence it had (no tool_calls)",
    "max_steps": "exit 2 — the step cap fired; the loop stopped and said so "
                 "(why it never converged is Day 13's taxonomy)",
    "budget": "exit 3 — the run's cost budget was spent; the answer is limited "
              "to already-gathered evidence",
    "gave_up": "the self-correction bound was reached; the loop stopped naming "
               "the stuck point",
    # Day 13 — the diagnosis exits. Each fires *before* the cap on a disease the
    # cap could only report as `max_steps`, and hands the user a named reason.
    "no_progress": "Day 13 — the repeat detector fired: the model re-issued an "
                   "identical call instead of correcting or moving on",
    "oscillation": "Day 13 — the cycle detector fired: the loop alternated a "
                   "short set of calls (A,B,A,B) with no tie-breaker",
    "tool_failure": "Day 13 — the tool-call boundary owned it: execution failed "
                    "(timeout/exception) and the fallback ladder was exhausted",
}


# ==================================================== Day 13 — loop hardening
# §4 — the state fingerprint. `max_steps` reports *that* the loop hit a wall;
# the fingerprint reports *why*, so "the model repeated its own rejected call"
# and "the work genuinely exceeded the cap" stop sharing one terminal. It is
# arithmetic over strings the loop already holds — zero embeds, zero chats.

def normalized_args(call) -> Any:
    """The call's args, JSON-canonicalized (key order is noise, not signal).

    Spelling is normalized so "the same call spelled differently" is still a
    repeat; the *value* is never normalized, so Day 12's A2 stays a different
    call the moment the model fixes `kind="mutations"` → `kind="mutation"`.
    """
    parsed = call.parsed() if hasattr(call, "parsed") else None
    raw = parsed if parsed is not None else (getattr(call, "arguments", "") or "")
    try:
        return json.loads(json.dumps(raw, sort_keys=True, default=str))
    except (TypeError, ValueError):
        return str(raw)


def fingerprint(call) -> str:
    """Stable digest of (tool name, normalized args) — the step's identity.

    sha1, not Python's salted `hash()`, so the same call fingerprints the same
    across processes and `distinct_step_fingerprints` is reproducible.
    """
    key = json.dumps([getattr(call, "name", ""), normalized_args(call)],
                     sort_keys=True, default=str)
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def detect_repeat(signatures: Sequence[str]) -> bool:
    """No progress: the identical *valid* call re-issued immediately (A, A).

    Adjacency, not a global count: a call that reappears *after* a different one
    is oscillation's business (checked separately), and a legitimate re-search
    with identical args is not something a stub can distinguish from a spiral.
    """
    return len(signatures) >= 2 and signatures[-1] == signatures[-2]


def detect_oscillation(signatures: Sequence[str], window: int) -> bool:
    """Oscillation: ≤ 2 distinct calls inside a window of `window` steps (A,B,A,B)."""
    if window < 2 or len(signatures) < window:
        return False
    return len(set(signatures[-window:])) <= 2


def contradiction_rows(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """§6 — flag observations that bear on the same claim with conflicting values.

    Detected, not adjudicated: the detector compares *values* for the same claim
    `field` across observations. It is deliberately narrow, so Day 10's
    split-brain — a mutation and a service both answering "where is checkout",
    two correct hits in two layers — does **not** fire: complementary evidence
    carries overlapping *topics*, never contradictory `claims`.
    """
    groups: dict[str, list[tuple[str | None, Any]]] = {}
    for step in steps:
        for call in step.get("calls") or []:
            for claim in call.get("claims") or []:
                groups.setdefault(claim["field"], []).append(
                    (claim.get("subject"), claim.get("value")))
    rows: list[dict[str, Any]] = []
    for field, pairs in groups.items():
        distinct: dict[str, tuple[str | None, Any]] = {}
        for subject, value in pairs:
            key = json.dumps(value, sort_keys=True, default=str)
            distinct.setdefault(key, (subject, value))
        if len(distinct) > 1:
            rows.append({
                "contradiction": True,
                "field": field,
                "observations": [subj for subj, _ in distinct.values()],
                "values": [val for _, val in distinct.values()],
            })
    return rows


def contradiction_coverage(rows: list[dict[str, Any]],
                           citations: Sequence[str]) -> bool:
    """§6's completeness clause: a flagged contradiction must have both sides cited.

    `cited ⊆ retrieved` grows a *cover*: silently picking one side is the failure
    mode, so every contentious observation must appear in the answer's citations.
    """
    if not rows:
        return True
    cited = set(citations or [])
    return all(set(r.get("observations") or []) <= cited for r in rows)


def observed_failures(steps: list[dict[str, Any]]) -> int:
    """How many executed calls ended in a tool failure (timeout/exception/internal)."""
    return sum(int(c.get("tool_failures") or 0)
               for s in steps for c in s.get("calls") or [])


def step_trace(step: int, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """One loop iteration's row — the unit of per-step attribution.

    `rows` is one record per executed call
    (`name`/`args_parsed`/`args_valid`/`result_ok`/`refused`/`error`/`evidence_ids`).
    A step may carry several calls (parallel `tool_calls`), each its own
    observation; the row aggregates them without dropping one — the cheapest
    way to "handle" a multi-call step is to execute the first and hope, and
    this row is what makes that visible.

    `cited` (which of this step's evidence the final answer used) can only be
    filled once the answer exists, so `grounding_over_transcript` writes it in.
    """
    retrieved: list[str] = []
    for r in rows:
        for i in r.get("evidence_ids") or []:
            if i not in retrieved:
                retrieved.append(i)
    return {
        "step": step,
        "tool_calls": [r["name"] for r in rows],
        "calls": rows,
        "args_valid": all(r["args_valid"] for r in rows),
        "result_ok": all(r["result_ok"] for r in rows),
        "refused": any(r["refused"] for r in rows),
        "rejections": sum(1 for r in rows if not r["args_valid"]),
        "observed_refusals": sum(1 for r in rows if r["refused"]),
        "retrieved": retrieved,
        "cited": [],
        "cited_subset_retrieved": True,
        # Day 13 — the failure ledger beside the step row.
        "fingerprints": [r["fingerprint"] for r in rows if r.get("fingerprint")],
        "error_kinds": [r["error_kind"] for r in rows if r.get("error_kind")],
        "tool_failures": sum(int(r.get("tool_failures") or 0) for r in rows),
        "tool_timeouts": sum(int(r.get("timeouts") or 0) for r in rows),
        "observed_failures": sum(int(r.get("tool_failures") or 0) for r in rows),
        "fallback_used": [r["fallback"] for r in rows if r.get("fallback")],
        "tool_retries": sum(int(r.get("tool_retries") or 0) for r in rows),
    }


def grounding_over_transcript(mentioned: list[str],
                              steps: list[dict[str, Any]]) -> dict[str, Any]:
    """`cited ⊆ retrieved` over the *whole transcript* — Day 12's invariant.

    The union of every step's retrieved evidence is the only source the final
    answer may cite; an id outside it is a fabrication. Each step row is
    annotated with the citations that belong to *its* evidence, which is how
    "the answer cites symbols from ≥ 2 observations" becomes a MECH check and
    how "the agent lost the thread between steps" is diagnosable per step.
    """
    retrieved: list[str] = []
    for row in steps:
        for i in row.get("retrieved") or []:
            if i not in retrieved:
                retrieved.append(i)
    evidence = set(retrieved)
    fabricated = [c for c in mentioned if c not in evidence]
    for row in steps:
        own = set(row.get("retrieved") or [])
        row["cited"] = [c for c in mentioned if c in own]
        row["cited_subset_retrieved"] = not row["cited"] or set(row["cited"]) <= evidence
    return {
        "citations": list(mentioned),
        "retrieved": retrieved,
        "fabricated": fabricated,
        "cited_subset_retrieved": not fabricated,
    }