"""Day 7 §8 — confirmed failures promoted to model-free regression fixtures.

Each fixture is `(question, expected_doc_ids, ...)` asserting
`expected ⊆ retrieved` — the mechanical check runs without any LLM:

    FIXTURE A1  expect {"quickstart/api#create-a-checkout"}
    FIXTURE C1  expect apps-overview, must_not app-store pages
    FIXTURE D1  expect api-guide#completing-checkout, max_dupes bound
    FIXTURE E1  must_refuse under tau

Day 8's improved retriever must keep these green; that is the concrete,
per-question meaning of "improved" — the same questions now pass checks
today's pipeline fails. Nothing here scores relevance; that is what makes the
fixtures honest regression tests instead of vibes.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import yaml

from ai_os.gate import should_refuse
from ai_os.trace import retrieve_trace

FIXTURES_PATH = Path(__file__).with_name("questions.yaml")
CODE_FIXTURES_PATH = Path(__file__).with_name("code_questions.yaml")


class FixtureError(Exception):
    pass


def load_questions(path: str | Path = FIXTURES_PATH) -> dict[str, Any]:
    """Load the question set (which *is* the fixture set) as a flat dict.

    Returns `{"tau":..., "budget_chars":..., "questions":[...], "by_id":{...}}`
    so runners can pre-register the shared `tau`/budget without duplicating it.
    """
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, dict) or "questions" not in raw:
        raise FixtureError(f"{path}: expected a {{'questions': [...]}} top level")
    questions = raw["questions"]
    if not questions:
        raise FixtureError(f"{path}: no questions/fixtures registered")
    by_id: dict[str, dict] = {}
    for q in questions:
        qid = q["id"]
        rig = dict(q)
        rig.setdefault("expected", [])
        rig.setdefault("must_not", [])
        rig.setdefault("must_refuse", False)
        rig.setdefault("tau", None)
        by_id[qid] = rig
        # mutually exclusive: a question either expects docs or must refuse
        if rig["must_refuse"] and rig["expected"]:
            raise FixtureError(f"{qid}: must_refuse and expected are exclusive")
    return {
        "tau": raw.get("tau"),
        "budget_chars": raw.get("budget_chars", 1200),
        "questions": [by_id[q["id"]] for q in questions],
        "by_id": by_id,
    }


def _retrieved_pairs(retrieved: Iterable[Any]) -> list[tuple[float, str]]:
    """Normalize any retrieved shape to `(score, doc_id)` pairs.

    Accepts `store.retrieve` output `(score, Chunk)`, `(score, doc_id)` tuples,
    and the trace's serialized `{"score", "doc_id", ...}` dicts.
    """
    pairs: list[tuple[float, str]] = []
    for entry in retrieved:
        if isinstance(entry, dict):
            pairs.append((float(entry["score"]), str(entry["doc_id"])))
        else:
            score, doc = entry
            if hasattr(doc, "doc_id"):
                doc_id: str = doc.doc_id
            elif isinstance(doc, dict):
                doc_id = str(doc["doc_id"])
            else:
                doc_id = str(doc)
            pairs.append((float(score), doc_id))
    return pairs


def check_fixture(fixture: dict[str, Any], retrieved: Iterable[Any],
                  ) -> tuple[bool, str]:
    """MECH: assert a fixture's constraints against a retrieved top-K.

    Returns `(ok, reason)`. Pure arithmetic: set membership for expected/
    must_not, `should_refuse` for must_refuse, string comparison for dupes.
    """
    pairs = _retrieved_pairs(retrieved)

    if fixture.get("must_refuse"):
        tau = fixture.get("tau")
        if tau is None:
            return (False, f"{fixture['id']}: must_refuse needs a calibrated tau")
        refuse = should_refuse(pairs, tau)
        top = f"{pairs[0][0]:.4f}" if pairs else "∅"
        return (refuse,
                f"{fixture['id']}: gate refused (top1 {top} < tau {tau})"
                if refuse else
                f"{fixture['id']}: must refuse but top1 {top} >= tau {tau}")

    ids = {d for _, d in pairs}
    expected = set(fixture["expected"])
    missing = sorted(expected - ids)
    if missing:
        return (False,
                f"{fixture['id']}: expected ∉ top-K → {missing}"
                f"  (recall@{len(pairs)} = {len(expected & ids)}/{len(expected)})")

    bad = sorted(set(fixture["must_not"]) & ids)
    if bad:
        return (False, f"{fixture['id']}: wrong sense retrieved → {bad}")

    max_dupes = fixture.get("max_dupes")
    if len(ids) > 1 and max_dupes is not None:
        texts = [d for _, d in pairs]
        dupe_ratio = 1.0 - len(set(texts)) / len(texts)
        if dupe_ratio * len(pairs) > max_dupes:
            return (False,
                    f"{fixture['id']}: >{max_dupes} duplicated slot(s) "
                    f"(ratio {dupe_ratio:.2f})")

    return (True, f"{fixture['id']}: expected ⊆ top-K → OK")


def run_fixtures(gateway, store, *, budget_chars: int = 1200,
                 tau: float | None = None, k: int | None = None,
                 path: str | Path = FIXTURES_PATH) -> list[dict[str, Any]]:
    """Retrieval-only run of the whole fixture set (no chat calls).

    The mechanical half of the failure report: for every fixture, embed the
    question, retrieve top-K, and run `check_fixture`. Cost: one embed per
    question, zero generations. Semantic verdicts are handled by the caller
    (`tools/rag_debug.py`) against the real gateway.
    """
    data = load_questions(path)
    effective_tau = tau if tau is not None else data["tau"]
    results: list[dict[str, Any]] = []
    for f in data["questions"]:
        fk = dict(f)
        if fk.get("tau") is None:
            fk["tau"] = effective_tau
        trace = retrieve_trace(gateway, store, fk["question"],
                               expected=fk["expected"], k=k,
                               budget_chars=budget_chars, tau=effective_tau)
        trace["id"] = fk["id"]
        trace["family"] = fk["family"]
        ok, reason = check_fixture(fk, trace["retrieved"])
        results.append({
            "id": fk["id"],
            "family": fk["family"],
            "question": fk["question"],
            "predicted": fk.get("predicted"),
            "fix": fk.get("fix"),
            "must_refuse": fk["must_refuse"],
            "ok": ok,
            "reason": reason,
            "trace": trace,
        })
    return results