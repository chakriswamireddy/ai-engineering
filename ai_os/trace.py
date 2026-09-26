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
    # Day 15 — the safety layer's hard wall. A *policy* rejection (a write, a
    # denied table/column, an ambiguous definition, an over-cost/runaway query)
    # stops the run: it is not a mistake to fix, and a loop that "corrects" its
    # way past a denial has become a fuzzer for its own defenses.
    "policy": "Day 15 — the safe SQL layer refused a *policy* violation "
              "(forbidden request); the run stopped rather than self-correct",
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


# ===================================================== Day 14 — the data columns
# §8's data table has four columns no retrieval row ever carried: the *generated*
# SQL, the *validation* verdict, whether it *executed*, and the *row_count* (plus
# `truncated` and the database's own `elapsed_ms`). They are promoted here, out
# of the tool result's `{ok, sql, columns, rows, row_count, truncated,
# elapsed_ms}` record, so the debugger reads one row per call for all four
# corpora. `gen_sql` is the artifact Day 15 attacks; `executed` is what separates
# "the validator refused it" (executed: false) from "the database refused it"
# (executed: true).

def data_columns(result) -> dict[str, Any]:
    """Promote a data tool's record columns into the call row (Day 14 §8).

    Empty for a retrieval result — a doc tool has no `sql` key — so this is
    purely additive and Days 7–13 rows are byte-for-byte unchanged. Day 15 adds
    the safety columns: `rejection_class` (repairable vs policy), `stage` (where
    the choke point refused), `executed_sql` (what actually ran), `cost_estimate`
    (the resource pre-check) and `statement_timeout_ms` (the server-side guard).
    """
    data = getattr(result, "data", None)
    if not isinstance(data, dict) or "sql" not in data:
        return {}
    out: dict[str, Any] = {
        "gen_sql": data.get("sql"),
        "executed": bool(data.get("executed")),
    }
    if data.get("validation"):
        out["validation"] = data["validation"]
    if data.get("query_kind"):
        out["query_kind"] = data["query_kind"]
    if data.get("row_count") is not None:
        out["row_count"] = int(data["row_count"])
    if data.get("truncated"):
        out["truncated"] = True
    if data.get("elapsed_ms") is not None:
        out["db_elapsed_ms"] = data["elapsed_ms"]
    # Day 15 — the safety layer's observable columns.
    if data.get("rejection_class"):
        out["rejection_class"] = data["rejection_class"]
    if data.get("stage"):
        out["safety_stage"] = data["stage"]
    if data.get("executed_sql"):
        out["executed_sql"] = data["executed_sql"]
    if data.get("cost_estimate") is not None:
        out["cost_estimate"] = data["cost_estimate"]
    if data.get("statement_timeout_ms") is not None:
        out["statement_timeout_ms"] = data["statement_timeout_ms"]
    return out


def policy_violations(steps: list[dict[str, Any]]) -> int:
    """How many calls the safety layer refused as a **policy** rejection.

    The counter that *stops* a run: a policy rejection is a forbidden request,
    not a mistake, so it must not invite another attempt. Distinct from Day 12's
    `rejections` (arg schema) and Day 13's tool failures.
    """
    return sum(1 for s in steps for c in s.get("calls") or []
               if c.get("rejection_class") == "policy")


def rejection_classes(steps: list[dict[str, Any]]) -> list[str]:
    """The safety classes seen across the run, in call order (for the trace)."""
    return [c["rejection_class"] for s in steps for c in s.get("calls") or []
            if c.get("rejection_class")]


def cost_estimates(steps: list[dict[str, Any]]) -> list[float]:
    """Every cost pre-check estimate the run made (the resource guard's ledger)."""
    return [float(c["cost_estimate"]) for s in steps for c in s.get("calls") or []
            if c.get("cost_estimate") is not None]


def generated_sqls(steps: list[dict[str, Any]]) -> list[str]:
    """The *generated* queries for the run — `query_kind: generated` only.

    The parameterized resolvers carry `sql` too (a fixed query with one `id`), so
    the kind is what keeps "the model wrote this" distinguishable from "the tool
    ran a stored query" in the trace.
    """
    return [c["gen_sql"] for s in steps for c in s.get("calls") or []
            if c.get("gen_sql") and c.get("query_kind") == "generated"]


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


# ======================================================== Day 16 — the plan
# A plan is a *claim about the future* ("these steps will answer this"), so the
# trace must grade plan **adherence** — did execution follow the plan, and where it
# did not, why — not merely the final answer. These columns are to Day 16 what
# `failure_kind` was to Day 13: the plan's own contract, serialized.

PLAN_TERMINATIONS: dict[str, str] = {
    "answer": "the plan completed / the model answered — the answer is drawn from "
              "the plan's own observations",
    "invalid_plan": "fail-closed — the plan failed validation (unregistered tool, "
                    "invalid args, unknown dependency, or a cycle) and was never "
                    "executed; a grounding failure, not a model failure",
    "infeasible": "the plan's estimated cost exceeded the run budget — refused "
                  "*before* spending, never half-executed into a spent ledger",
    "paused": "a checkpoint interruption, not a termination — the run is resumable "
              "at the next step (P4)",
    "policy_wall": "Day 15's wall — a step attempted a forbidden request; the plan "
                   "stopped and surfaced it and **no rung was climbed**: the retry "
                   "ladder must not help a model past a denial",
    "dependency_violation": "the executor refused to run a step whose dependencies "
                            "were not satisfied — the DAG, not luck, ordered the run",
    "plan_exhausted": "the evidence was insufficient (or a step failed) and no "
                      "replan was available — the answer says what was checked and "
                      "asserts no cause the plan never observed",
}


def plan_adherence(plan_steps: list[dict[str, Any]],
                   state: dict[str, Any]) -> dict[str, Any]:
    """Did execution follow the plan? Per step: planned tool vs the call emitted.

    A correct agent may legally walk a row in more than one order, so this grades
    the *step's* tool, not the sequence; and a step that emitted nothing (skipped /
    pending) is not counted as a deviation. Recorded, so a bad plan and an
    unfollowed plan are different facts with different owners.
    """
    records = {r["id"]: r for r in (state.get("steps") or [])}
    rows: list[dict[str, Any]] = []
    executed = 0
    for step in plan_steps:
        sid = step.get("id")
        rec = records.get(sid) or {}
        emitted = [t for t in (str(rec.get("tool") or "").split(", ")) if t]
        planned = step.get("tool")
        if emitted:
            executed += 1
        rows.append({
            "step_id": sid,
            "planned_tool": planned,
            "executed": emitted,
            "status": rec.get("status"),
            "adhered": bool(emitted) and planned in emitted,
        })
    followed = [r["adhered"] for r in rows if r["executed"]]
    return {"rows": rows, "executed_steps": executed,
            "adhered": all(followed) if followed else True,
            "deviations": [r["step_id"] for r in rows if r["executed"] and not r["adhered"]]}


def conclusion_subset_evidence(mentioned: list[str],
                               evidence: list[str],
                               asserted: dict[str, Any] | None = None
                               ) -> dict[str, Any]:
    """`conclusion ⊆ evidence` — Day 16's headline invariant.

    The third translation of the same rule (`cited ⊆ retrieved` Day 6,
    `asserted ⊆ returned` Day 14): parse the final answer's entities/claims and
    assert membership in the **plan's collected observations**. An id the plan never
    observed is a fabrication; a number the plan's data records never returned is an
    unsupported assertion. The two halves are reported separately and together, so
    a failure localizes to the answer contract, never to vibes.
    """
    obs = set(evidence or [])
    fabricated = [c for c in (mentioned or []) if c not in obs]
    verdict = asserted or {"ok": True, "unsupported": [], "unsupported_entities": []}
    unsupported = list(verdict.get("unsupported") or []) + \
        list(verdict.get("unsupported_entities") or [])
    return {
        "ok": not fabricated and bool(verdict.get("ok", True)),
        "fabricated": fabricated,
        "unsupported": unsupported,
        "cited_subset_retrieved": not fabricated,
        "asserted_subset_returned": bool(verdict.get("ok", True)),
    }


def plan_columns(state: dict[str, Any], *, over_planned: bool, plan_source: str,
                 conclusion: dict[str, Any], adherence: dict[str, Any],
                 extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """The §9 plan-table columns, assembled from the state and the verdicts.

    One row per run, every cell a real run's: the plan's source, whether a trivial
    question was over-planned, how many steps were planned vs executed, the rungs
    fired (and that a policy wall fired **none**), the reinstated
    `conclusion ⊆ evidence` headline, and the checkpoint index the run would resume
    from. `extra` carries the day's additions (replans, policy walls, budget).
    """
    out = {
        "plan_source": plan_source,
        "over_planned": bool(over_planned),
        "steps_planned": len(state.get("plan_ids") or []),
        "steps_executed": adherence.get("executed_steps", 0),
        "plan_adhered": adherence.get("adhered", True),
        "plan_deviations": adherence.get("deviations", []),
        "rungs": [r.get("rung") for r in (state.get("rungs") or [])],
        "policy_walls": len(state.get("policy_walls") or []),
        "replans": int(state.get("replans") or 0),
        "checkpoint_idx": len(state.get("checkpoints") or []),
        "resume_from": state.get("resume_from"),
        "conclusion_subset_evidence": conclusion.get("ok", False),
        "conclusion_unsupported": conclusion.get("unsupported", []),
    }
    out.update(extra or {})
    return out


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