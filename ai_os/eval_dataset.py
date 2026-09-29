"""Day 20 §5 — the golden dataset: the five-field, pre-registered, hash-pinned row.

Day 7 pre-registered nine questions by hand and called the expected evidence a
commitment. Day 20 makes the commitment *mechanical*: every golden row carries

    question · expected_evidence · expected_answer · expected_tools · failure_category

all written **before any run**, where

- `expected_answer` is **not** a model-voice paragraph (nobody should author the
  system's voice by hand) but `{claims[], must_not_assert[]}` — minimal facts;
- those claims are **oracle-derivable**: every discriminative term of a claim must
  appear in the row's expected evidence, or the row is *malformed* (a claim the
  evidence cannot reach is a hope, not an expectation). The check is offline —
  a token-grounded analogue of Day 18's `authored ⊆ grounded`;
- `expected_tools` must exist in the Day 11 registry / Day 19 role table;
- each row gets a `registered_at`, and the dataset a content hash. The harness
  refuses to grade under a stale hash, so patching a row after a run came up short
  is an **integrity failure**, not a run (V1/V8). Day 7's "pre-registration is the
  line between a report and curve-fitting" becomes `assert hash == pinned_hash`.

Day 21 adds two optional fields to the row, both under the same hash:

- `relevant_also` — docs known to be *on-topic but not required*, minted from
  corpus structure (never from retrieval output) so `precision@K` is membership
  arithmetic over `expected ∪ relevant_also`. It is disjoint from
  `expected_evidence` by construction (a relevant doc is not a required one);
  absent/empty ⇒ the precision cell is `n/a`, never `0.0`.
- `human_review` — the boundary where every deterministic cell may pass and the
  fact must still call the reader (the corpus-can-be-wrong seam): the RAG bench
  marks such a row's verdict `PENDING-human` by design.

Day 22 adds three optional fields to the row, both under the same hash — the
*agent benchmark*'s pre-registered call envelope:

- `necessary_calls` — the minimal call set a correct answer must have made,
  `[{tool, args?}]` minted from task *structure* (never from a run's output), so
  "was that call necessary?" is membership arithmetic in `necessary_calls`. A
  necessary call is required, so it must appear in `expected_tools` (the
  discipline check); absent/empty ⇒ `necessary ⊆ made` is `n/a`. Free-text
  arguments (`query`/`sql`) are deliberately *not* pre-registered — only the
  tool and any structured args are, because a correct agent may legally phrase a
  search differently (the AB3 deviation is graded on structure, not prose).
- `expected_steps` — the `{min, max}` step envelope `tool_steps` must fall in.
  Absent ⇒ the step cell is `n/a`.
- `expected_termination` — the loop exit the row expects (`answer`/`max_steps`/
  `budget`/`gave_up`/…); default `answer`. An E-family row still terminates
  `answer` — *what* it answers is the refusal, checked separately.

This module is `MECH`: pure parsing, set membership, and arithmetic — 0 chats,
0 embeds. The runner that presents rows to production is `tools/eval_harness.py`;
the grader is `ai_os/judge.py`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

import yaml

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:          # so `python ai_os/eval_dataset.py` works
    sys.path.insert(0, str(_ROOT))

GOLDEN_PATH = Path(__file__).with_name("golden.yaml")

# The Day 11 registry's seven tools (cross-checked against `ai_os.agent.ROLE_TOOLS`
# when importable, so a renamed tool is caught rather than silently accepted).
FALLBACK_TOOLS = (
    "search_saleor_docs", "search_saleor_code", "run_saleor_sql",
    "describe_saleor_schema", "get_product", "get_order", "get_customer",
)

# The §9 failure taxonomy: Day 7's verdict tree + Day 13's `failure_kind`.
FAILURE_CATEGORIES: frozenset[str | None] = frozenset({
    None, "refusal",
    # Day 7 verdict tree
    "missing_retrieval", "context", "context_quality", "generation",
    "corpus_gap", "wrong_entity",
    # Day 13 failure taxonomy
    "wrong_tool", "invalid_args", "unknown_tool", "timeout", "tool_error",
    "no_data", "internal", "refused", "tool_miss", "no_progress",
    "oscillation", "contradiction", "policy_violation",
})

AGENT_KIND = "agent"
JUDGE_HUNTING_KIND = "judge_hunting"
KINDS = (AGENT_KIND, JUDGE_HUNTING_KIND)

TOKEN_RE = re.compile(r"[a-z0-9]+")
_STEM_LEN = 4
# The inflectional slack the stem may absorb (plural, tense, gerund: `-s`, `-ed`,
# `-ing`). The bound is load-bearing, not cosmetic: `checkout` and `checkoutCreate`
# share their first four chars, so a bare 4-char stem lets a page that only says
# "checkout" license a claim about `checkoutCreate` — exactly the R5/K1 fixture
# (a citation that is real and in-context yet does not support the claim). Six
# chars apart, the two never collapse; `created`/`create` (one apart) still fold.
_MAX_INFLECTION = 3


class DatasetError(Exception):
    """A malformed golden row: the entry, not the pipeline, owns the failure."""


class IntegrityError(DatasetError):
    """The dataset hash is stale — a row was patched after registration (V1/V8)."""


# --------------------------------------------------------- oracle-derivability

def _stem(token: str) -> str:
    return token[:_STEM_LEN]


def discriminative_tokens(text: str) -> list[str]:
    """The claim's content terms: tokenized, stopwords dropped, `len >= 4`.

    Mirrors Day 8's `Lexicon` stopword discipline on a single string, so a claim
    written as prose ("a checkout is created with checkoutCreate") reduces to the
    terms that must be licensable by the evidence.
    """
    from ai_os.lexicon import tokenize
    return [t for t in tokenize(text) if len(t) >= _STEM_LEN]


def _licensed(claim_token: str, evidence_tokens: Iterable[str]) -> bool:
    """Is a claim term licensed by an evidence term — the same word or a light
    inflection of it?

    Same 4-char stem *and* within `_MAX_INFLECTION` chars of the claim term, so
    `created`/`create` fold but `checkout` never licenses `checkoutCreate` (the
    R5/K1 support fixture: a citation can be real and wrong).
    """
    stem = _stem(claim_token)
    return any(_stem(w) == stem and abs(len(w) - len(claim_token)) <= _MAX_INFLECTION
               for w in evidence_tokens)


def claim_derivable(claim: str, evidence_texts: Iterable[str]) -> bool:
    """`MECH`: is every content term of `claim` present in the evidence text?

    Stem matching absorbs the plurals/inflections the series' tokenizers already
    fold elsewhere, but the length guard stops it collapsing *distinct compound
    identifiers* (`checkout` vs `checkoutCreate`) onto one another — so a claim's
    content term must genuinely be carried by the evidence, not merely share a
    prefix with a shorter one. A claim with no discriminative term (all stopwords
    / short words) is trivially derivable.
    """
    toks = discriminative_tokens(claim)
    if not toks:
        return True
    evidence = [w for t in evidence_texts for w in TOKEN_RE.findall(t.lower())]
    return all(_licensed(t, evidence) for t in toks)


# ------------------------------------------------------------------- tools

def known_tools() -> set[str]:
    """The registry's tool names — the authority `expected_tools` is checked against."""
    try:
        from ai_os.agent import ROLE_TOOLS
        names = {t for tools in ROLE_TOOLS.values() for t in tools}
        if names:
            return names
    except Exception:
        pass
    return set(FALLBACK_TOOLS)


# The loop's exits (Day 12's five + Day 13/15's diagnoses) — the authority
# `expected_termination` is checked against. Imported lazily so this module
# stays importable without dragging the trace in.
FALLBACK_TERMINATIONS = ("answer", "max_steps", "budget", "gave_up",
                         "no_progress", "oscillation", "tool_failure", "policy")


def known_terminations() -> set[str]:
    try:
        from ai_os.trace import AGENT_TERMINATIONS
        if AGENT_TERMINATIONS:
            return set(AGENT_TERMINATIONS)
    except Exception:
        pass
    return set(FALLBACK_TERMINATIONS)


# ------------------------------------------------------------------ validation

def _as_list(value: Any, field: str, rid: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise DatasetError(f"{rid}: {field} must be a list, got {type(value).__name__}")
    return value


def _parse_necessary_calls(value: Any, rid: str) -> list[dict[str, Any]]:
    """Day 22 §2 — the minimal call set, `[{tool, args?}]`, or raise.

    Only the tool and *structured* args are pre-registered: a free-text `query`
    is never part of the envelope, because a correct agent may legally phrase a
    search differently — the envelope is enforceable at the tool/argument level
    (the plan's own honest limit).
    """
    out: list[dict[str, Any]] = []
    for item in _as_list(value, "necessary_calls", rid):
        if not isinstance(item, dict) or not str(item.get("tool") or "").strip():
            raise DatasetError(f"{rid}: necessary_calls entries need a `tool`")
        args = item.get("args") or {}
        if not isinstance(args, dict):
            raise DatasetError(f"{rid}: necessary_calls args must be a mapping")
        out.append({"tool": str(item["tool"]).strip(),
                    "args": {str(k): v for k, v in args.items()}})
    return out


def _parse_step_envelope(value: Any, rid: str) -> dict[str, int] | None:
    """`expected_steps: {min, max}` — the tool_steps envelope, or `None`."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise DatasetError(f"{rid}: expected_steps must be a mapping of {{min, max}}")
    try:
        lo = int(value.get("min", 0))
        hi = int(value.get("max", lo))
    except (TypeError, ValueError):
        raise DatasetError(f"{rid}: expected_steps min/max must be integers")
    return {"min": lo, "max": hi}


def normalize_row(raw: dict[str, Any], registered_at: str) -> dict[str, Any]:
    """Parse one YAML row into the canonical golden row, or raise `DatasetError`."""
    if not isinstance(raw, dict):
        raise DatasetError(f"golden row must be a mapping, got {type(raw).__name__}")
    rid = str(raw.get("id") or "").strip()
    if not rid:
        raise DatasetError("golden row has no id")

    question = str(raw.get("question") or "").strip()
    if not question:
        raise DatasetError(f"{rid}: question must not be empty")

    kind = str(raw.get("kind") or AGENT_KIND)
    if kind not in KINDS:
        raise DatasetError(f"{rid}: unknown kind {kind!r} (known: {list(KINDS)})")

    ea = raw.get("expected_answer")
    if not isinstance(ea, dict):
        raise DatasetError(f"{rid}: expected_answer must be a mapping "
                           "of {claims, must_not_assert}")
    claims = [str(c).strip() for c in _as_list(ea.get("claims"), "claims", rid)
              if str(c).strip()]
    must_not_assert = [str(c).strip()
                       for c in _as_list(ea.get("must_not_assert"),
                                         "must_not_assert", rid) if str(c).strip()]

    evidence = [str(e) for e in _as_list(raw.get("expected_evidence"),
                                         "expected_evidence", rid)]
    tools = [str(t) for t in _as_list(raw.get("expected_tools"), "expected_tools", rid)]
    must_not = [str(m) for m in _as_list(raw.get("must_not"), "must_not", rid)]
    must_refuse = bool(raw.get("must_refuse") or False)
    # Day 21 — the relevance oracle (precision@K) and the human-review boundary.
    relevant_also = [str(e) for e in _as_list(raw.get("relevant_also"),
                                              "relevant_also", rid)]
    human_review = bool(raw.get("human_review") or False)
    semantic_fail = bool(raw.get("semantic_fail") or False)
    # Day 22 — the agent benchmark's pre-registered call envelope.
    necessary_calls = _parse_necessary_calls(raw.get("necessary_calls"), rid)
    expected_steps = _parse_step_envelope(raw.get("expected_steps"), rid)
    expected_termination = str(raw.get("expected_termination") or "answer").strip()

    failure_category = raw.get("failure_category", None)
    if isinstance(failure_category, str):
        failure_category = failure_category.strip() or None
    if failure_category not in FAILURE_CATEGORIES:
        raise DatasetError(
            f"{rid}: failure_category {failure_category!r} not in the taxonomy "
            f"(Day 7 tree + Day 13 failure_kind)")

    row = {
        "id": rid,
        "kind": kind,
        "source": str(raw.get("source") or ("judge-hunting"
                                            if kind == JUDGE_HUNTING_KIND else "doc")),
        "family": raw.get("family"),
        "question": question,
        "expected_evidence": evidence,
        "expected_answer": {"claims": claims, "must_not_assert": must_not_assert},
        "expected_tools": tools,
        "failure_category": failure_category,
        "predicted": raw.get("predicted"),
        "fix": raw.get("fix"),
        "must_not": must_not,
        "must_refuse": must_refuse,
        "relevant_also": relevant_also,
        "human_review": human_review,
        "semantic_fail": semantic_fail,
        # Day 22 — the agent benchmark's call envelope (hash-covered by construction).
        "necessary_calls": necessary_calls,
        "expected_steps": expected_steps,
        "expected_termination": expected_termination,
        "registered_at": registered_at,
    }
    if kind == JUDGE_HUNTING_KIND:
        probe = raw.get("probe")
        if not isinstance(probe, dict) or not str(probe.get("answer") or "").strip():
            raise DatasetError(f"{rid}: judge-hunting rows need a probe.answer")
        row["probe"] = {
            "answer": str(probe["answer"]),
            "citations": [str(c) for c in _as_list(probe.get("citations"),
                                                    "probe.citations", rid)],
            "evidence": [str(e) for e in _as_list(probe.get("evidence"),
                                                  "probe.evidence", rid)],
        }
    return row


def validate_row(row: dict[str, Any], *, corpus_text: dict[str, str] | None = None,
                 tools: set[str] | None = None) -> list[str]:
    """Return the row's validation errors (empty = valid). Never raises.

    Structural rules run always. When `corpus_text` (an `id -> evidence text`
    map) is supplied, the **oracle-derivability** rule runs: every expected id
    must be in the corpus and every claim reachable from the evidence text.
    """
    errors: list[str] = []
    rid = row["id"]
    known = tools if tools is not None else known_tools()

    if row["kind"] == AGENT_KIND:
        for t in row["expected_tools"]:
            if t not in known:
                errors.append(f"{rid}: expected tool {t!r} not in the registry {sorted(known)}")
        if row["must_refuse"] and row["expected_evidence"]:
            errors.append(f"{rid}: must_refuse and expected_evidence are exclusive")
        if (not row["must_refuse"] and row["expected_evidence"]
                and not row["expected_answer"]["claims"]):
            errors.append(f"{rid}: a non-refusal row with expected evidence needs "
                          "at least one claim")
    elif row["kind"] == JUDGE_HUNTING_KIND and not row["expected_answer"]["claims"]:
        errors.append(f"{rid}: a judge-hunting row needs an expected claim to hunt")

    for bad in row["must_not"]:
        if bad in row["expected_evidence"]:
            errors.append(f"{rid}: {bad!r} is both expected and must_not")

    # Day 21 — the relevance oracle's own discipline (precision@K's premise).
    relevant = list(row.get("relevant_also") or [])
    if relevant and row["kind"] == JUDGE_HUNTING_KIND:
        errors.append(f"{rid}: judge-hunting rows carry no relevance set "
                      "(precision is n/a — relevance is the trap)")
    overlap = sorted(set(relevant) & set(row["expected_evidence"]))
    if overlap:
        errors.append(f"{rid}: relevant_also overlaps the required set "
                      f"{overlap} (a relevant doc is not a required one)")
    contradiction = sorted(set(relevant) & set(row["must_not"]))
    if contradiction:
        errors.append(f"{rid}: {contradiction} is both relevant_also and must_not")
    if row.get("semantic_fail") and row["kind"] != JUDGE_HUNTING_KIND:
        errors.append(f"{rid}: semantic_fail is only meaningful on a judge-hunting "
                      "row (a deterministic row's verdict is layer 1's)")

    # Day 22 — the agent benchmark's call envelope (pre-registration discipline).
    if row["kind"] == JUDGE_HUNTING_KIND and row.get("necessary_calls"):
        errors.append(f"{rid}: judge-hunting rows carry no call envelope "
                      "(they are graded against a probe, not a run)")
    for nc in row.get("necessary_calls") or []:
        if nc["tool"] not in row["expected_tools"]:
            errors.append(f"{rid}: necessary call {nc['tool']!r} is not in "
                          f"expected_tools {sorted(row['expected_tools'])} "
                          "(a necessary call is a required one)")
        if nc["tool"] not in known:
            errors.append(f"{rid}: necessary call tool {nc['tool']!r} not in the registry")
    env = row.get("expected_steps")
    if env is not None:
        if env["min"] < 0 or env["max"] < env["min"]:
            errors.append(f"{rid}: expected_steps envelope invalid {env}")
    if str(row.get("expected_termination") or "") not in known_terminations():
        errors.append(f"{rid}: expected_termination "
                      f"{row.get('expected_termination')!r} is not a loop exit "
                      f"{sorted(known_terminations())}")

    if corpus_text is not None:
        missing = [e for e in row["expected_evidence"] if e not in corpus_text]
        if missing:
            errors.append(f"{rid}: expected evidence not in the corpus: {missing}")
        rel_missing = [e for e in relevant if e not in corpus_text]
        if rel_missing:
            errors.append(f"{rid}: relevant_also not in the corpus: {rel_missing}")
        texts = [corpus_text[e] for e in row["expected_evidence"] if e in corpus_text]
        if row["must_refuse"]:
            texts = []  # a refusal row's claims are about behaviour, not facts
        if texts:
            for claim in row["expected_answer"]["claims"]:
                if not claim_derivable(claim, texts):
                    errors.append(f"{rid}: claim not oracle-derivable from expected "
                                  f"evidence: {claim!r}")
    return errors


def validate_dataset(rows: list[dict[str, Any]], *,
                     corpus_text: dict[str, str] | None = None,
                     tools: set[str] | None = None) -> None:
    """Raise `DatasetError` on the first structural invalidity; return None if clean."""
    if not rows:
        raise DatasetError("golden dataset is empty")
    seen: set[str] = set()
    problems: list[str] = []
    for row in rows:
        if row["id"] in seen:
            problems.append(f"{row['id']}: duplicate id")
        seen.add(row["id"])
        problems.extend(validate_row(row, corpus_text=corpus_text, tools=tools))
    if problems:
        raise DatasetError("golden dataset invalid:\n  - " + "\n  - ".join(problems))


def check_against_sources(rows: list[dict[str, Any]],
                          sources: Iterable[str | Path] | None = None) -> None:
    """Enforce the "converted verbatim" contract against Day 7/10's YAML.

    For every golden row whose id appears in `questions.yaml`/`code_questions.yaml`,
    the shared fields (question + expected_evidence + must_not + must_refuse) must
    match. This is the anti-drift check: the golden set can add fields, never
    rewrite the pre-registration it inherits.
    """
    from ai_os.fixtures import CODE_FIXTURES_PATH, FIXTURES_PATH
    paths = list(sources) if sources is not None else [FIXTURES_PATH, CODE_FIXTURES_PATH]
    by_id: dict[str, dict] = {}
    for p in paths:
        with open(p, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        for q in data.get("questions") or []:
            by_id[q["id"]] = q
    problems: list[str] = []
    for row in rows:
        src = by_id.get(row["id"])
        if src is None:
            continue
        if src.get("question", "").strip() != row["question"]:
            problems.append(f"{row['id']}: question drifted from the source YAML")
        if list(src.get("expected") or []) != list(row["expected_evidence"]):
            problems.append(f"{row['id']}: expected_evidence drifted from the source YAML")
        if list(src.get("must_not") or []) != list(row["must_not"]):
            problems.append(f"{row['id']}: must_not drifted from the source YAML")
        if bool(src.get("must_refuse")) != row["must_refuse"]:
            problems.append(f"{row['id']}: must_refuse drifted from the source YAML")
    if problems:
        raise DatasetError("golden rows are not verbatim:\n  - " + "\n  - ".join(problems))


# ------------------------------------------------------------------- the hash

def rows_registered_before(rows: list[dict[str, Any]], run_started_at: str) -> list[str]:
    """Ids whose row was registered **after** the run started (V8's flag).

    `registered_at` and `run_started_at` are ISO-8601 strings, so a plain string
    comparison orders them. A run that started before its row's registration is
    the anti-curve-fit fixture: the expectation cannot have been written in
    response to an output that did not yet exist. Returns the flagged ids.
    """
    started = str(run_started_at or "")
    return [str(r["id"]) for r in rows
            if str(r.get("registered_at") or "") > started]


def hash_rows(rows: list[dict[str, Any]]) -> str:
    """The dataset's content hash: sha256 over the canonical rows (sorted keys).

    Covers every graded field, so patching `expected_evidence`, `expected_answer`,
    `expected_tools`, `failure_category`, or a judge-hunting `probe` after the run
    changes the hash and trips the harness's stale-hash refusal (V1/V8).
    """
    blob = json.dumps(rows, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------- loading

def load_golden(path: str | Path = GOLDEN_PATH, *, verify_hash: bool = True,
                sources: bool = False, corpus_text: dict[str, str] | None = None,
                validate: bool = True) -> dict[str, Any]:
    """Load, validate, and hash-pin the golden dataset.

    Raises `IntegrityError` when `dataset_hash` is non-empty and does not match
    the recomputed hash (a patched row), and `DatasetError` on a malformed row.
    """
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, dict) or not isinstance(raw.get("rows"), list):
        raise DatasetError(f"{path}: expected a {{rows: [...]}} top level")

    registered_at = str(raw.get("registered_at") or "")
    rows = [normalize_row(r, registered_at) for r in raw["rows"]]
    digest = hash_rows(rows)

    pinned = str(raw.get("dataset_hash") or "").strip()
    if verify_hash and pinned and pinned != digest:
        raise IntegrityError(
            f"golden dataset hash is stale (pinned {pinned[:12]}…, computed "
            f"{digest[:12]}…) — a row was patched after registration (V1/V8). "
            f"Re-pin only if the edit is intended: python ai_os/eval_dataset.py --pin")

    if validate:
        validate_dataset(rows, corpus_text=corpus_text)
    if sources:
        check_against_sources(rows)

    return {
        "path": str(path),
        "dataset_version": raw.get("dataset_version", 1),
        "registered_at": registered_at,
        "dataset_hash": digest,
        "pinned_hash": pinned,
        "rows": rows,
        "by_id": {r["id"]: r for r in rows},
    }


def pin_hash(path: str | Path = GOLDEN_PATH) -> str:
    """Recompute and write the dataset hash in place (an *intended* edit).

    Rewrites only the `dataset_hash:` line, preserving the file's comments and
    layout, and returns the new hash. Every other edit to golden.yaml must go
    through this to keep the pre-registration honest.
    """
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    raw = yaml.safe_load(text)
    registered_at = str(raw.get("registered_at") or "")
    rows = [normalize_row(r, registered_at) for r in raw["rows"]]
    digest = hash_rows(rows)
    new_text, n = re.subn(r"(?m)^dataset_hash:\s*.*$",
                          f'dataset_hash: "{digest}"', text, count=1)
    if not n:
        raise DatasetError(f"{path}: no `dataset_hash:` line to pin")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(new_text)
    return digest


# ------------------------------------------------------------------ corpus text

def evidence_text_for(store) -> dict[str, str]:
    """An `id -> text` map for a `RagStore`, for the oracle-derivability check.

    For a code chunk the embeddable interface (signature + docstring) is kept
    *beside* the body, so a claim about a symbol's contract is derivable from
    either — the same interface/body split Day 10 embeds.
    """
    out: dict[str, str] = {}
    for c in getattr(store, "chunks", []):
        parts = [c.text or ""]
        for extra in (getattr(c, "signature", None), getattr(c, "docstring", None)):
            if extra:
                parts.append(extra)
        out.setdefault(c.doc_id, " ".join(parts))
    return out


# ----------------------------------------------------------------------- CLI

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", nargs="?", default=str(GOLDEN_PATH))
    ap.add_argument("--pin", action="store_true",
                    help="recompute and write the dataset hash (an intended edit)")
    ap.add_argument("--sources", action="store_true",
                    help="also check the doc/code rows are verbatim")
    ap.add_argument("--no-hash", action="store_true",
                    help="skip the stale-hash check (for a pre-pin validation)")
    args = ap.parse_args()

    if args.pin:
        digest = pin_hash(args.path)
        print(f"pinned {args.path} → {digest}")
        return

    data = load_golden(args.path, verify_hash=not args.no_hash, sources=args.sources)
    n = len(data["rows"])
    agents = sum(1 for r in data["rows"] if r["kind"] == AGENT_KIND)
    hunting = n - agents
    print(f"golden: {n} rows ({agents} agent / {hunting} judge-hunting)  "
          f"hash={data['dataset_hash'][:12]}…  registered={data['registered_at']}")
    for r in data["rows"]:
        print(f"  {r['id']:<4} {r['kind']:<14} evidence={len(r['expected_evidence'])} "
              f"tools={r['expected_tools']} failure={r['failure_category']}")


if __name__ == "__main__":
    main()
