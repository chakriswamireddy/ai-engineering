"""Day 22 §5 — the agent bench metric core: measure agent behaviour.

Days 11–13 built the tools, the loop, and the guardrails and hand-quoted their
behaviour into tables. Day 21 turned the retriever's verdict into a machine's
output; Day 22 does the same for the object that *acts*. Six of the eight
curriculum columns already live in `run_agent`'s returned dict — task success,
tool selection, argument correctness, steps, cost, and latency — so they are
*passthroughs with a header*. The two genuinely new columns are the day's work:

- **Unnecessary calls** — the precision of the agent bench — needs a notion of
  *necessary* the transcript cannot supply, so it reads the golden row's
  pre-registered `necessary_calls` envelope (Day 21's `relevant_also`
  discipline, wearing a call ledger). A call is graded where the trace makes it
  mechanical::

      U1 duplicate fingerprint, no Δ evidence   (Day 13's no_progress, a unit)
      U2 no-Δ evidence, no data record          (gathered nothing new)
      U3 never-cited                            (bought, never used)

  A call that *looks* unnecessary under U3 but plausibly shaped the answer's
  prose is **`undecided`** — recorded, owned, never counted, the same honesty
  Day 21 applied to undeducible claims.

- **Failure recovery** — the inverse — is a rate the trace always had the
  inputs for, never a number: a failure event (rejection / refusal / `error_kind`
  / timeout) is **recovered** when a later step *progresses* (the evidence union
  grows) or the run ends with a grounded answer, and **never** when the run
  fabricated (`fabricated = false` is the column's gate). `gave_up` is the
  honest zero.

Every function here is a **pure function of one `run_agent` dict** (and the
golden row): the bench adds no chat call, re-executes nothing, and re-answers
nothing — an agent's second run is a second production run and a verdict on a
run the judge was never handed. Cost: 0 chats, 0 embeds beyond the run's own.
"""
from __future__ import annotations

from typing import Any, Iterable, Sequence

from ai_os.eval_dataset import discriminative_tokens
from ai_os.rag import NOT_COVERED
from ai_os.semantic_judge import grade_claims
from ai_os.tools import TOOL_ERROR_KINDS

U_RULES = ("U1", "U2", "U3")

# The owner of each failing column (Day 7's isolation map, extended to behaviour).
BEHAVIOUR_OWNERS: dict[str, str] = {
    "termination": "agent — the loop's exit is a contract (Day 12)",
    "steps": "agent — the pre-registered step envelope (Day 12/22)",
    "description": "description — a tool miss is owned by the schema the chooser "
                   "read (Day 13 F1)",
    "retrieval": "retrieval — the expected evidence never reached the tool "
                 "(Day 8's backlog)",
    "generation": "generation — the answer's claims/citations",
    "semantic": "semantic judge — Day 21's lane",
    "efficiency": "agent — a call the envelope did not require (U1/U2/U3)",
}


# ------------------------------------------------------------- small helpers

def _calls(steps: Sequence[dict[str, Any]]) -> list[tuple[int, dict[str, Any]]]:
    """`(step_number, call_row)` for every executed call, in transcript order."""
    return [(int(s.get("step") or i + 1), c)
            for i, s in enumerate(steps) for c in (s.get("calls") or [])]


def _token_set(text: str) -> set[str]:
    return set(discriminative_tokens(text or ""))


# ================================================== AB1 — task success

def termination_match(run: dict[str, Any], row: dict[str, Any]) -> bool:
    """`termination_reason == expected_termination` — Day 12's contract as a column."""
    return run.get("termination_reason") == (row.get("expected_termination") or "answer")


def task_success(run: dict[str, Any], row: dict[str, Any], *,
                 corpus_text: dict[str, str] | None = None) -> dict[str, Any]:
    """Termination + Day 21's claim-coverage re-used + the E-family refusal clause.

    The semantic lane is Day 21's verbatim (`semantic_judge.grade_claims`),
    read over the run's **own** transcript: the context is the union of the
    evidence the run actually showed, never a fresh retrieval. A refusal row
    succeeds only if the run refused *and* did not assert the forbidden claim.
    """
    answer = run.get("answer") or ""
    evidence = list(run.get("evidence") or [])
    context_texts = [corpus_text[i] for i in evidence
                     if corpus_text and i in corpus_text]
    citations = list(run.get("citations") or [])
    ea = row.get("expected_answer") or {}
    sem = grade_claims(
        answer=answer,
        claims=ea.get("claims") or [],
        must_not_assert=ea.get("must_not_assert") or [],
        context_texts=context_texts, citations=citations,
        corpus_text=corpus_text)

    must_refuse = bool(row.get("must_refuse"))
    refusal_ok: bool | None = None
    if must_refuse:
        refused_signal = (bool(run.get("refused")) or NOT_COVERED in answer
                          or not answer.strip())
        refusal_ok = bool(refused_signal) and not sem.get("forbidden")

    term_ok = termination_match(run, row)
    coverage_ok = sem.get("coverage_ok")
    must_not_ok = sem.get("must_not_ok")
    cited_ok = bool(run.get("cited_subset_retrieved")) and not run.get("fabricated")
    fabricated = bool(run.get("fabricated"))

    ok = (term_ok
          and coverage_ok is not False
          and must_not_ok is not False
          and refusal_ok is not False
          and cited_ok
          and not fabricated)
    return {
        "ok": ok,
        "termination_ok": term_ok,
        "expected_termination": row.get("expected_termination") or "answer",
        "observed_termination": run.get("termination_reason"),
        "coverage": sem.get("coverage"),
        "coverage_ok": coverage_ok,
        "must_not_ok": must_not_ok,
        "refusal_ok": refusal_ok,
        "groundedness_ok": sem.get("groundedness_ok"),
        "citation_support_ok": sem.get("citation_support_ok"),
        "cited_ok": cited_ok,
        "fabricated": fabricated,
        "semantic": sem,
    }


# ================================================== AB2 — tool selection

def tool_selection(steps: Sequence[dict[str, Any]],
                   expected_tools: Iterable[str]) -> dict[str, Any]:
    """TSR@1 (per-step membership) · tool coverage · omissions · misses.

    TSR@1 is the fraction of *steps that emitted a call* whose chosen tools all
    lie in `expected_tools` — membership, not a script, because a correct agent
    may legally walk a row in more than one order. `n/a` (never `0.0`) when the
    row pre-registers no tools (E-family: refusing is the correct behaviour, so
    grading a "choice" would grade the refusal as a miss). A required tool never
    called is a **tool omission**; a chosen tool outside the set is a **tool
    miss**, owned by the description (Day 13's F1 rule).
    """
    expected = list(dict.fromkeys(expected_tools))
    step_names = [list(s.get("tool_calls") or []) for s in steps]
    emitted = [names for names in step_names if names]
    hits = sum(1 for names in emitted if all(n in expected for n in names))
    tsr = (hits / len(emitted)) if (expected and emitted) else None
    called = [n for names in step_names for n in names]
    called_set = set(called)
    coverage = (len(set(expected) & called_set) / len(set(expected))
                if expected else None)
    omissions = sorted(set(expected) - called_set)
    misses = [{"step": i + 1, "tool": n}
              for i, names in enumerate(step_names)
              for n in names if expected and n not in expected]
    return {"tsr_at_1": tsr, "hits": hits, "steps_with_calls": len(emitted),
            "expected_tools": expected, "called": called,
            "tool_coverage": coverage, "tool_omissions": omissions,
            "tool_misses": misses,
            "owner": "description" if misses else None}


# ================================================== AB3 — argument correctness

def argument_correctness(steps: Sequence[dict[str, Any]],
                         necessary_calls: Sequence[dict[str, Any]] = (),
                         ) -> dict[str, Any]:
    """`args_valid` fraction + `arg_deviation`s against the envelope's payloads.

    `args_valid` is Day 11's registry verdict, already in every step row. A
    deviation is a *necessary* call whose pre-registered **structured** args no
    valid made call matches — "correct shape, wrong target", the semantic half
    of which the residue lane owns. Free-text `query`/`sql` is never part of the
    envelope, so a differently-phrased search is not a deviation.
    """
    calls = _calls(steps)
    total = len(calls)
    valid = sum(1 for _, c in calls if c.get("args_valid"))
    frac = (valid / total) if total else None
    deviations: list[dict[str, Any]] = []
    for nc in necessary_calls:
        if not nc.get("args"):
            continue
        matched = False
        for _, c in calls:
            if c.get("name") == nc["tool"] and c.get("args_valid"):
                parsed = c.get("args_parsed") or {}
                if all(parsed.get(k) == v for k, v in nc["args"].items()):
                    matched = True
                    break
        if not matched:
            deviations.append({"tool": nc["tool"], "expected_args": nc["args"]})
    rejections = [c.get("error") for _, c in calls if not c.get("args_valid")]
    return {"args_valid_frac": frac, "valid": valid, "total": total,
            "arg_deviations": deviations,
            "rejections": [r for r in rejections if r],
            "owner": "validation" if frac is not None and frac < 1.0 else None}


# ================================================== AB4 — number of steps

def step_count(run: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    """`tool_steps` vs the pre-registered envelope; the exit named honestly.

    A `gave_up`/`max_steps` run is an **honest fail** — the loop said why it
    stopped — never a quietly passing "answered eventually".
    """
    n = int(run.get("tool_steps") or 0)
    env = row.get("expected_steps")
    in_env = env is None or (env["min"] <= n <= env["max"])
    term = run.get("termination_reason")
    return {"tool_steps": n, "envelope": env, "in_envelope": in_env,
            "termination": term, "truncated": bool(run.get("truncated")),
            "gave_up": bool(run.get("gave_up"))}


# ================================================== AB5 — unnecessary calls

def _plausibly_prose_shaping(evidence: Iterable[str], answer: str,
                             corpus_text: dict[str, str] | None) -> bool:
    """Could an uncited call have shaped the answer's prose?

    If we cannot resolve the evidence text, we cannot *prove* the call
    unnecessary — so it is `undecided` (never counted), the same refusal to
    grade what cannot be proved that Day 21 made for undeducible claims.
    """
    if not corpus_text:
        return True
    a_tokens = _token_set(answer)
    if not a_tokens:
        return False
    return any(a_tokens & _token_set(corpus_text.get(i, ""))
               for i in evidence if i in corpus_text)


def unnecessary_calls(steps: Sequence[dict[str, Any]], *, answer: str = "",
                      citations: Iterable[str] = (),
                      corpus_text: dict[str, str] | None = None
                      ) -> dict[str, Any]:
    """The day's first new column: U1/U2/U3 over executed calls.

    Only *efficiency-eligible* calls are graded — `args_valid and result_ok and
    not refused`. A refusal is a finding, never a wasted purchase; a rejection
    or an error is a failure the recovery column owns. Precedence is U1 > U2 >
    U3 so a call is counted once, and a U3 call whose evidence plausibly shaped
    the prose lands in `undecided` — recorded, owned, never counted.
    """
    # A refusal answer cites nothing *by design*, so "never-cited" cannot grade
    # necessity there: the call is the finding that produced the refusal, not a
    # wasted purchase — it lands in `undecided`, never counted.
    refusal_answer = NOT_COVERED in (answer or "")
    cited = set(citations or ())
    seen_fps: set[str] = set()
    prior: set[str] = set()
    total = u1 = u2 = u3 = 0
    details: list[dict[str, Any]] = []
    undecided: list[dict[str, Any]] = []
    for step, c in _calls(steps):
        if not (c.get("args_valid") and c.get("result_ok")
                and not c.get("refused")):
            continue
        total += 1
        ev = set(c.get("evidence_ids") or [])
        fp = c.get("fingerprint")
        has_data = isinstance(c.get("data"), dict)
        row = {"step": step, "tool": c.get("name")}
        if fp and fp in seen_fps and ev <= prior:
            u1 += 1
            details.append({**row, "rule": "U1"})
        elif ev and ev <= prior and not has_data:
            u2 += 1
            details.append({**row, "rule": "U2"})
        elif ev and not (ev & cited):
            if refusal_answer or _plausibly_prose_shaping(ev, answer, corpus_text):
                undecided.append({**row, "rule": "U3",
                                  "evidence_ids": sorted(ev),
                                  "owner": "semantic judge — a U3 call that may "
                                           "have shaped prose (off by default)"})
            else:
                u3 += 1
                details.append({**row, "rule": "U3"})
        if fp:
            seen_fps.add(fp)
        prior |= ev
    unnecessary = u1 + u2 + u3
    efficiency = (1.0 - unnecessary / total) if total else None
    return {"total": total, "unnecessary": unnecessary, "u1": u1, "u2": u2,
            "u3": u3, "efficiency": efficiency, "details": details,
            "undecided": undecided}


def necessary_call_coverage(steps: Sequence[dict[str, Any]],
                            necessary_calls: Sequence[dict[str, Any]] = ()
                            ) -> dict[str, Any]:
    """`necessary ⊆ made` — the minimal call set actually attempted.

    Matched by tool + structured args (the envelope), regardless of whether the
    call executed cleanly: an *attempted* necessary call is "made". `n/a` when
    the row pre-registers no necessary call (E-family).
    """
    if not necessary_calls:
        return {"coverage": None, "matched": 0, "total": 0, "missing": []}
    calls = _calls(steps)
    matched = 0
    missing: list[dict[str, Any]] = []
    for nc in necessary_calls:
        found = False
        for _, c in calls:
            if c.get("name") != nc["tool"]:
                continue
            if not nc.get("args"):
                found = True
                break
            parsed = c.get("args_parsed") or {}
            if all(parsed.get(k) == v for k, v in nc["args"].items()):
                found = True
                break
        if found:
            matched += 1
        else:
            missing.append(nc)
    return {"coverage": matched / len(necessary_calls), "matched": matched,
            "total": len(necessary_calls), "missing": missing}


# ================================================== AB6 — failure recovery

def failure_recovery(run: dict[str, Any]) -> dict[str, Any]:
    """Progress-after-failure, never a fabrication — the day's second new column.

    A failure event is a call that was rejected (`args_valid` false), refused,
    raised a tool error (`error_kind` in `TOOL_ERROR_KINDS`), or hit the wall
    (`timeouts > 0`). It is **recovered** iff a later step grows the evidence
    union, or the run terminates in a grounded answer — and never if the run
    fabricates or gave up (both honest zeros). Reports the rate, the exit, and
    the recovery's extra steps, so "recovered in one step" and "recovered after
    three" are different verdicts.
    """
    steps = list(run.get("steps") or [])
    events: list[int] = []
    for step, c in _calls(steps):
        is_failure = (not c.get("args_valid")
                      or bool(c.get("refused"))
                      or c.get("error_kind") in TOOL_ERROR_KINDS
                      or int(c.get("timeouts") or 0) > 0)
        if is_failure:
            events.append(step)
    failures = len(events)
    gave_up = bool(run.get("gave_up"))
    fabricated = bool(run.get("fabricated"))

    step_adds: list[int] = []
    seen: set[str] = set()
    for s in steps:
        ev = set(s.get("retrieved") or [])
        step_adds.append(len(ev - seen))
        seen |= ev
    final_grounded = (run.get("termination_reason") == "answer"
                      and bool(run.get("cited_subset_retrieved"))
                      and not fabricated
                      and bool((run.get("answer") or "").strip()))
    gated = gave_up or fabricated

    recovered = 0
    for st in events:
        progressed = any(step_adds[i] > 0 for i in range(st, len(steps)))
        if not gated and (progressed or final_grounded):
            recovered += 1
    rate = (recovered / failures) if failures else None
    extra_steps = (len(steps) - min(events)) if (failures and steps) else 0
    return {"failures": failures, "recovered": recovered, "recovery_rate": rate,
            "gave_up": gave_up, "fabricated": fabricated, "gated": gated,
            "extra_steps": extra_steps, "final_grounded": final_grounded,
            "owner": ("agent" if (failures and recovered < failures) else None)}


# ================================================== AB7 — cost

def cost_row(ledger: dict[str, Any] | None) -> dict[str, Any]:
    """The ledger delta, per row and per world: chats, embeds, tokens, USD.

    The *bench* costs 0 chats; the *run* bills what it billed. The two are kept
    in separate rows so nobody confuses the price of measuring with the price of
    being measured.
    """
    led = ledger or {}
    return {"chats": led.get("chats"), "embeds": led.get("embeds"),
            "tokens": led.get("tokens"), "cost_usd": led.get("cost_usd"),
            "latency_ms": led.get("latency_ms"),
            "provider_retries": led.get("failed")}


# ================================================== AB8 — latency

def latency_row(run: dict[str, Any], *,
                ledger: dict[str, Any] | None = None) -> dict[str, Any]:
    """`elapsed_ms` with per-step and per-tool attribution, plus the wall's count.

    "The agent was slow" is not a verdict until it names *who* was slow; the
    *why* — model vs retrieval vs a hung tool — is Day 23's observability seam,
    and `provider_retries` (failed ledger rows) is kept distinct from
    `tool_timeouts` (the tool-call wall), Day 13's layered rule.
    """
    per_step: dict[int, float] = {}
    per_tool: dict[str, float] = {}
    for s in run.get("steps") or []:
        tot = 0.0
        for c in s.get("calls") or []:
            el = float(c.get("elapsed_ms") or 0.0)
            tot += el
            per_tool[c.get("name", "?")] = per_tool.get(c.get("name", "?"), 0.0) + el
        per_step[int(s.get("step") or len(per_step) + 1)] = round(tot, 3)
    slowest = max(per_step, key=lambda k: per_step[k]) if per_step else None
    return {
        "per_step_ms": per_step,
        "per_tool_ms": {k: round(v, 3) for k, v in per_tool.items()},
        "slowest_step": slowest,
        "tool_timeouts": int(run.get("tool_timeouts") or 0),
        "provider_retries": (ledger or {}).get("failed"),
    }


# ================================================== attribution + measure

def attribute_failure(res: dict[str, Any], row: dict[str, Any],
                      run: dict[str, Any]) -> str | None:
    """Day 7's isolation, as a column — never a blend.

    The tool never fetched the expected evidence → **retrieval debit** (Day 8's
    backlog). The tool fetched it and the agent ignored it → **agent/description
    debit**. The report says which; the A-bench must not let the agent's
    failures hide under the retriever's, nor the retriever's under the agent's.
    """
    expected = set(row.get("expected_evidence") or [])
    evidence = set(run.get("evidence") or [])
    if expected and not expected <= evidence:
        return "retrieval"
    tsr = res["tool_selection"]["tsr_at_1"]
    if tsr is not None and tsr < 1.0:
        return "description"
    if res["steps"]["in_envelope"] is False:
        return "agent"
    if res["unnecessary"]["unnecessary"] > 0:
        return "efficiency"
    return "agent"


def measure(run: dict[str, Any], row: dict[str, Any], *,
            ledger: dict[str, Any] | None = None,
            corpus_text: dict[str, str] | None = None,
            wall_ms: float | None = None) -> dict[str, Any]:
    """One golden row's A-row — every AB column a pure function of the run dict."""
    ts = task_success(run, row, corpus_text=corpus_text)
    sel = tool_selection(run.get("steps") or [], row.get("expected_tools") or [])
    args = argument_correctness(run.get("steps") or [],
                                row.get("necessary_calls") or [])
    steps = step_count(run, row)
    unc = unnecessary_calls(run.get("steps") or [], answer=run.get("answer") or "",
                            citations=run.get("citations") or [],
                            corpus_text=corpus_text)
    needed = necessary_call_coverage(run.get("steps") or [],
                                     row.get("necessary_calls") or [])
    rec = failure_recovery(run)
    cost = cost_row(ledger)
    lat = latency_row(run, ledger=ledger)

    fails: list[str] = []
    if not ts["termination_ok"]:
        fails.append("termination")
    if ts["coverage_ok"] is False:
        fails.append("coverage")
    if ts["must_not_ok"] is False:
        fails.append("must_not")
    if ts["refusal_ok"] is False:
        fails.append("refusal")
    if not ts["cited_ok"] or ts["fabricated"]:
        fails.append("citations")
    if steps["in_envelope"] is False:
        fails.append("steps")

    res = {
        "id": row["id"],
        "family": row.get("family"),
        "source": row.get("source"),
        "world": None,                       # labelled by the runner (stub | real)
        "task_success": ts,
        "tool_selection": sel,
        "arguments": args,
        "steps": steps,
        "unnecessary": unc,
        "necessary": needed,
        "recovery": rec,
        "cost": cost,
        "latency": lat,
        "wall_ms": wall_ms,
        "fails": fails,
        "verdict": "pass" if not fails else "fail",
        "owner": (attribute_failure(
            {"tool_selection": sel, "steps": steps, "unnecessary": unc},
            row, run) if fails else None),
    }
    return res


def control_agrees(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """AB9 — the bench's verdict ≡ the same run's production verdict.

    Compares the deterministic cells a benchmark must not perturb: the task
    success, tool selection, step envelope, unnecessary-call arithmetic, and the
    recovery rate. A disagreement is a **bench bug**, never a fixture to weaken.
    """
    for key in ("task_success", "steps", "recovery"):
        if a.get(key) != b.get(key):
            return False
    for key in ("tsr_at_1", "tool_coverage", "tool_omissions"):
        if a["tool_selection"].get(key) != b["tool_selection"].get(key):
            return False
    for key in ("unnecessary", "u1", "u2", "u3"):
        if a["unnecessary"].get(key) != b["unnecessary"].get(key):
            return False
    return a["verdict"] == b["verdict"]
