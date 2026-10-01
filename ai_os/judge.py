"""Day 20 §6/§7 — the layered judge: grade the trace, never re-answer.

Day 7's trace already carries everything a verdict needs (retrieved top-K,
expected-set membership, recall@K, MRR, citations, the fabricatee flag, the
failure kind, the ledger). So the judge *reads* that — it never runs the model
again. A judge that re-answers is a second production call: it doubles the bill,
re-bakes sampling noise into a "grade", and grades a run the trace never saw.

Three layers, determinism first:

- **layer 1 — deterministic (`MECH`, 0 chats).** Set membership and arithmetic:
  `expected ⊆ retrieved` (+ recall@K / MRR — Day 8/9's metrics, now issued by a
  machine), `cited ⊆ retrieved` (Day 6), `conclusion ⊆ evidence` (Day 16's
  invariant, read here as *claim coverage over the answer* + `must_not_assert`
  avoidance), `tool == expected_tools` (Day 11/19's routing), `must_not`
  avoidance, `refusal == expected` (E-family), and `failure == category` (the
  Day 7 tree + Day 13 taxonomy). Every check that fails names **one owner**,
  reusing Day 7's isolation map.
- **layer 2 — semantic (`PENDING`).** `citation_correctness`,
  `answer_relevance`, `groundedness` beyond set membership: a *slot* declared
  pending with its owner (**Day 21** for RAG, **Day 22** for agent behaviour).
  Parking is a feature, not a stall.
- **layer 3 — LLM-as-judge (`EVAL_JUDGE=model`, OFF by default).** One flagged,
  budgeted call per graded cell, writing only to `semantic:`; it may *explain* a
  verdict, never *decide* one. `deterministic_verdict` is only ever set by layer
  1; a model that flips a pass to fail is a `judged ⊆ pre-registered` violation
  the harness flags red (V4).

The headline invariant, held by construction: **`judged ⊆ pre-registered`** — a
check with no expectation to read (`expected_tools` empty, `failure_category`
null, no `claims`) is reported `n/a`, never failed. A verdict where the
pre-registration was silent is not a verdict.
"""
from __future__ import annotations

import os
import re
from typing import Any

from ai_os.eval_dataset import EVAL_UNRESOLVABLE, JUDGE_HUNTING_KIND, claim_derivable
from ai_os.rag import NOT_COVERED
from ai_os.trace import expected_stats

# `EVAL_JUDGE` — the day's switch. The deterministic layer's verdict row is
# complete without the model lane; the model lane is an optional *comment*.
EVAL_JUDGE_ENV = "EVAL_JUDGE"
JUDGE_KINDS = ("deterministic", "model", "none")


def judge_kind_from_env() -> str:
    kind = os.getenv(EVAL_JUDGE_ENV, "deterministic").strip().lower()
    return kind if kind in JUDGE_KINDS else "deterministic"


# The §9 semantic cells the deterministic layer cannot touch — a slot, parked
# with an owner, exactly where the series has parked semantic truth since Day 7.
SEMANTIC_SLOTS: dict[str, str] = {
    "citation_correctness": "Day 21 (RAG) — is the citation the *right* source, not just a source",
    "answer_relevance": "Day 21 (RAG) — does the answer address the question, not just the ids",
    "groundedness": "Day 21 (RAG) — is the meaning grounded, beyond set membership",
    "agent_behaviour": "Day 22 (agent) — steps, tool-selection, recovery, cost",
}

# One owner per symptom — Day 7's isolation map, reused.
OWNERS: dict[str, str] = {
    "evidence_membership": "retrieval",
    "must_not": "retrieval",
    "cited_subset_retrieved": "generation",
    "conclusion_subset_evidence": "generation",
    "tool_match": "routing",
    "refusal_expected": "gate",
    "failure_category": "taxonomy",
}

# A refusal is a *finding*, not an answer. The judge reads it from the trace when
# present, and otherwise from the answer text — the same conclusion the harness's
# `refusal_restated` flag records, made explicit here so grading is trace-only.
_REFUSAL_RE = re.compile(
    r"not covered|cannot|can't|no such|does not (appear|exist)|unable to|"
    r"not in the corpus|no (page|module|documentation|evidence)",
    re.IGNORECASE)


def looks_like_refusal(text: str) -> bool:
    """`MECH` refusal detector: the answer declines rather than asserts."""
    t = (text or "").strip()
    if not t:
        return True
    return NOT_COVERED in t.lower() or bool(_REFUSAL_RE.search(t))


# ------------------------------------------------------------------ the trace

def _trace_view(trace: dict[str, Any]) -> dict[str, Any]:
    """Normalize a run trace (harness output) or a judge-hunting probe into the
    handful of facts layer 1 reads. The judge is indifferent to *which* loop
    produced the trace; it reads the same fields either way."""
    if "probe" in trace:
        p = trace["probe"]
        return {
            "answer": p.get("answer") or "",
            "citations": list(p.get("citations") or []),
            "evidence": list(p.get("evidence") or []),
            "tools": [],
            "refused": False,
            "fabricated": [],
            "failure_kind": None,
            "ledger": {},
            "kind": JUDGE_HUNTING_KIND,
        }
    run = trace.get("run") or {}
    answer = trace.get("answer")
    if answer is None:
        answer = run.get("answer") or ""
    citations = trace.get("citations")
    if citations is None:
        citations = run.get("citations") or []
    evidence = trace.get("evidence")
    if evidence is None:
        evidence = run.get("evidence") or []
    tools = trace.get("tool_calls")
    if tools is None:
        tools = run.get("tool_calls") or []
    tool_names = [c.get("name") if isinstance(c, dict) else str(c) for c in tools]
    refused = trace.get("refused")
    if refused is None:
        refused = bool(run.get("refused"))
    return {
        "answer": answer,
        "citations": list(citations),
        "evidence": list(evidence),
        "tools": tool_names,
        "refused": bool(refused) or looks_like_refusal(answer),
        "fabricated": list(trace.get("fabricated") or run.get("fabricated") or []),
        "failure_kind": trace.get("failure_kind", run.get("failure_kind")),
        "ledger": trace.get("ledger") or run.get("ledger") or {},
        "kind": trace.get("kind", "agent"),
    }


# ---------------------------------------------------------------- layer 1

def deterministic_checks(row: dict[str, Any], trace: dict[str, Any]) -> dict[str, dict]:
    """Layer 1: every check that is set-membership or arithmetic over the trace.

    Returns `{check: {ok, detail, owner, graded}}`. A check reads only its own
    pre-registered expectation; when that expectation is absent the check is
    `graded: False` (reported `n/a`, never failed) — the mechanism that makes
    `judged ⊆ pre-registered` true by construction.
    """
    v = _trace_view(trace)
    evidence = list(v["evidence"])
    citations = list(v["citations"])
    answer = v["answer"] or ""
    expected = list(row.get("expected_evidence") or [])
    checks: dict[str, dict] = {}

    # `expected ⊆ retrieved` (+ the Day 8/9 metrics), retrieval-only rows.
    if expected:
        stats = expected_stats(expected, evidence)
        ok = all(stats["in_list"])
        checks["evidence_membership"] = {
            "ok": ok, "graded": True, "owner": OWNERS["evidence_membership"],
            "recall_at_k": stats["recall_at_k"], "mrr": stats["mrr"],
            "detail": (f"expected ⊆ retrieved → OK (recall@{len(evidence)}: "
                       f"{int((stats['recall_at_k'] or 0) * len(expected))}/{len(expected)}, "
                       f"MRR {stats['mrr']:.3f})" if ok else
                       f"expected ∉ retrieved → {[e for e, m in zip(expected, stats['in_list']) if not m]}"),
        }
    else:
        checks["evidence_membership"] = {
            "ok": True, "graded": False, "owner": None,
            "detail": "no expected evidence (refusal row) — n/a",
        }

    # `must_not` — the wrong sense must not be retrieved (Family C).
    must_not = list(row.get("must_not") or [])
    if must_not:
        bad = sorted(set(must_not) & set(evidence))
        checks["must_not"] = {
            "ok": not bad, "graded": True, "owner": OWNERS["must_not"],
            "detail": ("no wrong sense retrieved → OK" if not bad
                       else f"wrong sense retrieved → {bad}"),
        }
    else:
        checks["must_not"] = {"ok": True, "graded": False, "owner": None,
                              "detail": "no must_not expectation — n/a"}

    # `cited ⊆ retrieved` — the answer may cite only what it saw (Day 6).
    fabricated = sorted(set(citations) - set(evidence)) or list(v["fabricated"])
    checks["cited_subset_retrieved"] = {
        "ok": not fabricated, "graded": True, "owner": OWNERS["cited_subset_retrieved"],
        "detail": (f"cited ⊆ retrieved → OK ({len(citations)} citation(s))"
                   if not fabricated else f"fabricated citation(s) → {fabricated}"),
    }

    # `conclusion ⊆ evidence` — Day 16's invariant, read as claim coverage:
    # every mandatory claim is asserted, and no `must_not_assert` is.
    claims = list((row.get("expected_answer") or {}).get("claims") or [])
    forbidden = list((row.get("expected_answer") or {}).get("must_not_assert") or [])
    uncovered = [c for c in claims if not claim_derivable(c, [answer])]
    violated = [c for c in forbidden if claim_derivable(c, [answer])]
    if claims or forbidden:
        ok = not uncovered and not violated
        detail = "conclusion ⊆ evidence → OK"
        if uncovered:
            detail = f"mandatory claim(s) not asserted → {uncovered}"
        if violated:
            detail = (detail + "; " if uncovered else "") + \
                f"forbidden claim(s) asserted → {violated}"
        checks["conclusion_subset_evidence"] = {
            "ok": ok, "graded": True, "owner": OWNERS["conclusion_subset_evidence"],
            "detail": detail,
        }
    else:
        checks["conclusion_subset_evidence"] = {
            "ok": True, "graded": False, "owner": None,
            "detail": "no expected answer to grade — n/a",
        }

    # `tool == expected_tools` — the required tools were selected (Day 11/19).
    expected_tools = set(row.get("expected_tools") or [])
    if expected_tools:
        called = set(v["tools"])
        missing = sorted(expected_tools - called)
        checks["tool_match"] = {
            "ok": not missing, "graded": True, "owner": OWNERS["tool_match"],
            "detail": (f"tool == expected → OK ({sorted(called)})" if not missing
                       else f"expected tool(s) not called → {missing} (called {sorted(called)})"),
        }
    else:
        checks["tool_match"] = {"ok": True, "graded": False, "owner": None,
                                "detail": "no expected tools — n/a"}

    # `refusal == expected` — E-family correctness (refuse, and say why).
    must_refuse = bool(row.get("must_refuse"))
    refused = bool(v["refused"])
    if row.get("kind") == JUDGE_HUNTING_KIND:
        checks["refusal_expected"] = {"ok": True, "graded": False, "owner": None,
                                      "detail": "judge-hunting row — n/a"}
    else:
        checks["refusal_expected"] = {
            "ok": refused == must_refuse, "graded": True, "owner": OWNERS["refusal_expected"],
            "detail": (f"refusal == expected ({must_refuse}) → OK" if refused == must_refuse
                       else f"refusal == expected ({must_refuse}) → got {refused}"),
        }

    # `failure == category` — the expected outcome shape (Day 7 tree/Day 13).
    expected_failure = row.get("failure_category")
    observed = v["failure_kind"]
    if expected_failure == "refusal":
        ok = bool(v["refused"])
        detail = f"failure == refusal → {'OK' if ok else 'the run did not refuse'}"
        checks["failure_category"] = {"ok": ok, "graded": True,
                                      "owner": OWNERS["refusal_expected"], "detail": detail}
    elif expected_failure is None:
        checks["failure_category"] = {"ok": True, "graded": False, "owner": None,
                                      "detail": f"no failure expected (observed: {observed}) — n/a"}
    else:
        ok = observed == expected_failure
        checks["failure_category"] = {
            "ok": ok, "graded": True, "owner": OWNERS["failure_category"],
            "detail": f"failure == {expected_failure} → {'OK' if ok else f'got {observed}'}",
        }
    return checks


# ------------------------------------------------------------- layer 1 verdict

def judge_row(row: dict[str, Any], trace: dict[str, Any], *,
              kind: str | None = None, gateway=None,
              unresolvable: bool = False) -> dict[str, Any]:
    """Grade one golden row against one trace. Layer 1 decides; layers 2/3 annotate.

    The returned **verdict row** is `pass | fail` from layer 1, with the failing
    checks named and one owner per symptom; `semantic` is always `pending` (with
    its owner) unless the flagged model lane adds a *comment* that never changes
    `deterministic_verdict`.

    Day 25 §7 — `unresolvable=True` is set by the caller when the row's **resolved
    evidence hash changed** since registration (the corpus was poisoned under the
    row). The verdict is then neither `pass` nor `fail`: the deterministic checks
    are still computed and reported, but the verdict is `unresolvable`, because
    "did this answer match the pre-registration?" is not a question the system can
    answer about a corpus that moved.
    """
    kind = kind or judge_kind_from_env()
    checks = deterministic_checks(row, trace)
    failed = [name for name, c in checks.items() if not c["ok"]]
    graded = [name for name, c in checks.items() if c["graded"]]
    verdict = "fail" if failed else "pass"
    owner = next((checks[n]["owner"] for n in failed if checks[n].get("owner")), None)

    view = _trace_view(trace)
    metrics = checks.get("evidence_membership", {})
    # Day 20's headline invariant, held by construction: a failed check must have
    # read a pre-registered expectation (`graded`), so no verdict is issued where
    # the pre-registration was silent.
    _overreach = [n for n, c in checks.items() if not c["graded"] and not c["ok"]]
    out: dict[str, Any] = {
        "id": row["id"],
        "family": row.get("family"),
        "kind": row.get("kind"),
        "question": row["question"],
        "verdict": verdict,
        "deterministic_verdict": verdict,
        "failed_checks": failed,
        "graded_checks": graded,
        "checks": checks,
        "owner": owner,
        "recall_at_k": metrics.get("recall_at_k"),
        "mrr": metrics.get("mrr"),
        "evidence_n": len(view["evidence"]),
        "tool_calls": view["tools"],
        "refused": view["refused"],
        "judged_subset_preregistered": not _overreach,
        # layer 2 — the parked semantic cells, with owners.
        "semantic": "pending",
        "semantic_owner": "Day 21 (RAG) / Day 22 (agent)",
        "semantic_slots": dict(SEMANTIC_SLOTS),
        "graded_by": "deterministic",
    }
    out["tokens"] = _ledger_tokens(view["ledger"])
    out["latency_ms"] = (view["ledger"] or {}).get("latency_ms")
    # Day 25 §7 — a row whose evidence moved is *unresolvable*: never `pass` (the
    # answer may have matched a poisoned document) and never `fail` (the system
    # cannot prove the model was wrong). The deterministic checks stay in `checks`
    # for the reader; the verdict is the corpus's, not the model's.
    if unresolvable:
        out["verdict"] = EVAL_UNRESOLVABLE
        out["deterministic_verdict"] = EVAL_UNRESOLVABLE
        out["evidence_changed"] = True
        out["graded_by"] = "evidence"
        out["judged_subset_preregistered"] = True
        out["verdict_note"] = (
            "the row's resolved evidence hash changed since registration — a "
            "poisoned evidence row is unresolvable, neither passed nor wrong")
        return out
    if kind == "model" and gateway is not None:
        note = model_judge(gateway, row, trace)
        out["semantic"] = note.get("semantic", "pending")
        out["graded_by"] = note.get("graded_by", "model")
        out["semantic_note"] = note
        # A model may *explain*; it may never *decide*. If it disagrees with
        # layer 1, that is recorded as a `judged ⊆ pre-registered` violation —
        # `deterministic_verdict` is untouched.
        if note.get("verdict") not in (None, "unknown", verdict):
            out["semantic_conflict"] = True
            out["judged_subset_preregistered"] = False
    return out


def _ledger_tokens(ledger) -> int:
    if not isinstance(ledger, dict):
        return 0
    if "tokens" in ledger:
        return int(ledger["tokens"])
    return 0


# ------------------------------------------------- layer 3 — the model comment

MODEL_JUDGE_PROMPT = (
    "You are a *commentator*, not a grader. A deterministic judge has already "
    "issued the verdict. Explain, in one sentence, whether the answer's citations "
    "are *correct* and whether it addresses the question — commentary only. Do not "
    "issue a pass/fail verdict.\n"
    "Question: {question}\nAnswer: {answer}\nCitations: {citations}\n"
    "Evidence: {evidence}\n")


def model_judge(gateway, row: dict[str, Any], trace: dict[str, Any]) -> dict[str, Any]:
    """The flagged, budgeted model lane — **one chat per graded cell**, off by default.

    Writes only to `semantic:` with `graded_by: model` and the drift disclaimer;
    returns a dict the caller attaches as a comment. It never touches
    `deterministic_verdict`.
    """
    view = _trace_view(trace)
    prompt = MODEL_JUDGE_PROMPT.format(
        question=row["question"], answer=view["answer"] or "(none)",
        citations=", ".join(view["citations"]) or "(none)",
        evidence=", ".join(view["evidence"]) or "(none)")
    completion = gateway.chat([{"role": "user", "content": prompt}], temperature=0.0,
                              max_tokens=256)
    return {
        "graded_by": "model",
        "provider": getattr(gateway, "last_provider", None),
        "model": getattr(completion, "model", None),
        "semantic": (completion.text or "").strip() or "pending",
        "verdict": "unknown",
        "disclaimer": "stochastic grader — comment only; the deterministic verdict stands",
    }


def judge_all(rows: list[dict[str, Any]], traces: dict[str, dict], *,
              kind: str | None = None, gateway=None) -> list[dict[str, Any]]:
    """Grade a whole golden set; one **verdict row** per golden row."""
    return [judge_row(r, traces[r["id"]], kind=kind, gateway=gateway)
            for r in rows if r["id"] in traces]
