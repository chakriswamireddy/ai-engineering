"""Day 18 §5 — the distiller: N episodes → one grounded concept, gated by code.

The distiller is the one genuinely new payment of the day: **one chat per batch**.
But the chat is not the safety mechanism — the *code* is. The model proposes a
candidate concept from N finished investigations, and `validate_concept` refuses
to mint it unless three checks pass:

1. **sample size ≥ `MEMORY_MIN_SOURCES`** — a lesson from one run is an anecdote.
   The distiller may store a single episode (on the episodic shelf, recallable as a
   receipt); it may not mint a general truth from it.
2. **`authored ⊆ grounded`** — every claim must be satisfiable against the *union
   of its source episodes' evidence* (Day 14's `asserted ⊆ returned`, reconstructed
   for distillation). An over-claim, or a claim a source contradicts, is rejected
   **at mint time**, never stored softened.
3. **no principal names** — a concept that names a customer/person/email is a
   privacy failure and is rejected (§7). Data may be the *source* of a lesson;
   an individual can never *be* the lesson.

What is minted carries full provenance (`created_by = {run_id, step,
source: "distiller"}`, `source_episodes`, `strength`, `ttl`) through Day 17's
closed write path — so `recalled ⊆ authored` is not weakened to admit the
distiller; the distiller *is* an author (S7 is its tab-keep).

Whether a *real* model distills a concept that is correct, general, and useful is
`PENDING` — Day 21's benchmark. The routing (when it fires, the gates, the
provenance minting) is `MECH`: deterministic functions of stored episodes.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Iterable, Sequence

from ai_os.lexicon import tokenize
from ai_os.memory import extract_entities
from ai_os.state import (
    DEFAULT_MIN_SOURCES,
    SEMANTIC_KIND,
    StateStore,
    concept_key,
)

DISTILL_SOURCE = "distiller"

DEFAULT_DISTILL_TTL = 7 * 86400.0     # a distilled lesson's clock backstop (7d)
DEFAULT_STRENGTH_DECAY = 0.5          # re-prompt loop: strength halved per decay step
DEFAULT_CONCEPT_THRESHOLD = 0.5       # a concept is served only above this strength
MIN_CONTENT_LEN = 4                   # a claim term must be this long to be a signal

# Contract stated where the model can read it, the way the reasoning prompt states
# the loop's rules. The output shape is fixed so the parser (not prose) is the gate.
DISTILL_PROMPT = (
    "You distill finished investigations into ONE grounded concept. You are shown "
    "N episodes, each with a task_id, its evidence, its conclusion, and any claims "
    "it recorded. Rules:\n"
    "1. Distill only what the evidence supports: every word of the claim must be "
    "satisfiable against the union of the episodes' evidence.\n"
    "2. Name every supporting episode in source_episodes, by task_id.\n"
    "3. Generalize only across episodes that agree; never mint from a single "
    "receipt.\n"
    "4. Do not name a customer, a person, an email, or an individual order/product "
    "id — a concept that names a principal is a privacy failure.\n"
    "5. Reply with JSON only, no prose: {\"claim\": \"...\", "
    "\"source_episodes\": [\"task_id\", ...], \"claims\": {\"field\": value}}."
)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_INDIVIDUAL_RE = re.compile(
    r"\b(?:customer|user|account|person|client)\s+[A-Za-z0-9@._-]+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def memory_min_sources_from_env() -> int:
    return max(1, _env_int("MEMORY_MIN_SOURCES", DEFAULT_MIN_SOURCES))


def memory_distill_ttl_from_env() -> float:
    return _env_float("MEMORY_DISTILL_TTL", DEFAULT_DISTILL_TTL)


def memory_strength_decay_from_env() -> float:
    return _env_float("MEMORY_STRENGTH_DECAY", DEFAULT_STRENGTH_DECAY)


# ----------------------------------------------------------------- parsing

def parse_concept(text: str) -> dict[str, Any]:
    """Parse a distiller reply into a candidate concept (fenced or bare JSON).

    `MECH` and fail-soft: an unparseable reply yields a candidate with `errors`
    set and no claim, so the mint gate refuses it — a model that answers in prose
    is not written, it is discarded.
    """
    raw = (text or "").strip()
    m = _FENCE_RE.search(raw)
    if m:
        raw = m.group(1).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {"claim": None, "source_episodes": [], "claims": {},
                "errors": [f"unparseable: {exc.__class__.__name__}"]}
    if not isinstance(data, dict):
        return {"claim": None, "source_episodes": [], "claims": {},
                "errors": ["not a JSON object"]}
    claim = str(data.get("claim") or "").strip()
    cited = data.get("source_episodes")
    cited = [str(x) for x in cited] if isinstance(cited, list) else []
    claims = data.get("claims") if isinstance(data.get("claims"), dict) else {}
    return {"claim": claim or None, "source_episodes": cited, "claims": claims,
            "errors": [] if claim else ["missing claim"]}


# ----------------------------------------------------------------- the chat

def _episode_block(episodes: Sequence[dict[str, Any]]) -> str:
    lines = []
    for e in episodes:
        tid = e.get("task_id")
        lines.append(
            f"- task {tid}: question={e.get('question')!r}; "
            f"conclusion={e.get('conclusion')!r}; "
            f"evidence={list(e.get('evidence') or [])}; "
            f"claims={e.get('claims') or {}}")
    return "\n".join(lines)


def distill(episodes: Sequence[dict[str, Any]], gateway, *,
            temperature: float = 0.0, max_tokens: int = 1024) -> dict[str, Any]:
    """One guarded chat over N episodes → a candidate concept (`MECH` routing)."""
    user = (f"Episodes (N={len(episodes)}):\n{_episode_block(episodes)}\n\n"
            "Distill the single grounded concept these episodes support.")
    completion = gateway.chat(
        [{"role": "system", "content": DISTILL_PROMPT},
         {"role": "user", "content": user}],
        temperature=temperature, max_tokens=max_tokens)
    candidate = parse_concept(completion.text)
    return {"candidate": candidate, "raw": completion.text, "chats": 1,
            "model": completion.model,
            "usage": {"input_tokens": getattr(completion.usage, "input_tokens", None),
                      "output_tokens": getattr(completion.usage, "output_tokens", None)}}


# ----------------------------------------------------------------- the gates

def _grounded_text(episodes: Sequence[dict[str, Any]]) -> str:
    parts: list[str] = []
    for e in episodes:
        parts.append(str(e.get("question") or ""))
        parts.append(str(e.get("conclusion") or ""))
        parts.append(str(e.get("signature") or ""))
        parts.extend(str(x) for x in (e.get("evidence") or []))
    return " ".join(parts)


def principal_names(text: str, principals: Iterable[str] = ()) -> list[str]:
    """Identifier-shaped personal names in a claim — the privacy gate's input.

    Deliberately narrow (a concept may speak of *checkout failures*; it may not
    speak of *customer ada*): explicit individuals, emails, and any given
    principal id. A model's promise not to leak is not enough — Day 15's
    "privileges beat parsers" becomes "the mint-time check beats the prompt".
    """
    found: list[str] = []
    hay = text or ""
    for p in principals:
        p = str(p or "").strip()
        if p and re.search(rf"\b{re.escape(p)}\b", hay, re.IGNORECASE):
            found.append(p)
    found.extend(m.group(0) for m in _INDIVIDUAL_RE.finditer(hay))
    found.extend(m.group(0) for m in _EMAIL_RE.finditer(hay))
    return sorted(set(found))


def validate_concept(candidate: dict[str, Any],
                     source_episodes: Sequence[dict[str, Any]], *,
                     min_sources: int | None = None,
                     principals: Iterable[str] = ()) -> dict[str, Any]:
    """The mint-time gates: sample size, `authored ⊆ grounded`, no principal names.

    `source_episodes` is the batch the distiller was *given*; a candidate may cite
    only those (authorship), from at least `min_sources` of them (a receipt is not
    a lesson), with every claim term satisfiable against their union of evidence
    (groundedness), and with no source contradicting a claim it repeats.
    """
    ms = memory_min_sources_from_env() if min_sources is None else max(1, int(min_sources))
    reasons: list[str] = list(candidate.get("errors") or [])
    claim = candidate.get("claim")
    cited = [str(x) for x in (candidate.get("source_episodes") or [])]
    by_id = {str(e.get("task_id")): e for e in source_episodes}
    provided = set(by_id)

    if not isinstance(claim, str) or not claim.strip():
        if "missing claim" not in reasons:
            reasons.append("missing claim")

    # 1. authorship + sample size
    unknown = [c for c in cited if c not in provided]
    if unknown:
        reasons.append("cites unprovided episode")
    agreeing = [by_id[c] for c in cited if c in provided]
    if len(set(cited) & provided) < ms:
        reasons.append("sample_size")

    # 2. `authored ⊆ grounded` — the union of the sources' evidence is the ceiling
    grounded = _grounded_text(agreeing)
    grounded_tokens = set(tokenize(grounded))
    claim_tokens = [t for t in tokenize(claim or "") if len(t) >= MIN_CONTENT_LEN]
    unsupported = sorted({t for t in claim_tokens if t not in grounded_tokens})
    claim_entities = [e for e in extract_entities(claim or "")]
    unsupported_entities = sorted({e for e in claim_entities
                                   if e.lower() not in grounded.lower()})
    if unsupported or unsupported_entities:
        reasons.append("ungrounded")

    # contradiction: a candidate claim cannot assert a value its own source denies
    contradicted: list[str] = []
    cand_claims = candidate.get("claims") or {}
    if isinstance(cand_claims, dict):
        for field, value in cand_claims.items():
            for e in agreeing:
                src = (e.get("claims") or {}).get(field)
                if src is not None and src != value:
                    contradicted.append(f"{field}: {value!r} vs source {src!r}")
    if contradicted:
        reasons.append("contradiction")

    # 2b. Day 25 §7 — a **tainted** grounding episode cannot ground a lesson at all.
    # The closed write path already refuses a foreign/absent `source`; this is the
    # honest residue of the day's sharpest point — provenance is a write-authorization
    # property, not a truth property, and the distiller *is* an authored source. So
    # the extra precondition is taint: an episode whose run carried an injection
    # signal (or whose payload says so) is refused as a source, and a concept that
    # would rest on it is never minted.
    tainted_sources = sorted(str(e.get("task_id")) for e in agreeing
                             if e.get("tainted"))
    if tainted_sources:
        reasons.append("tainted_source")

    # 3. privacy — no principal names survive the mint
    names = principal_names(f"{claim or ''} {json.dumps(cand_claims, default=str)}",
                            principals)
    if names:
        reasons.append("principal_name")

    return {
        "ok": not reasons,
        "reasons": reasons,
        "claim": claim,
        "cited": cited,
        "sample_size": len(set(cited) & provided),
        "min_sources": ms,
        "unsupported": unsupported,
        "unsupported_entities": unsupported_entities,
        "contradicted": contradicted,
        "principal_names": names,
        # Day 25 — the taint precondition: episodes that carried an injection.
        "tainted_sources": tainted_sources,
    }


# ----------------------------------------------------------------- the mint

def _concept_id(claim: str) -> str:
    return hashlib.sha1((claim or "").strip().lower().encode()).hexdigest()[:12]


def mint(store: StateStore, principal: str, candidate: dict[str, Any],
         source_episodes: Sequence[dict[str, Any]], *, run_id: str,
         step: Any = "distill", ttl: float | None | str = "inherit",
         min_sources: int | None = None, recall_threshold: float | None = None,
         principals: Iterable[str] = ()) -> dict[str, Any]:
    """Validate then write `kind=semantic` with full provenance, or refuse.

    Returns `{ok, validation, record}` — `ok=False` means the concept was **not
    minted** (rejected at mint time, never stored softened).
    """
    validation = validate_concept(candidate, source_episodes,
                                  min_sources=min_sources, principals=principals)
    if not validation["ok"]:
        return {"ok": False, "validation": validation, "record": None}
    cited = validation["cited"]
    considered = max(1, len(source_episodes))
    strength = len(set(cited)) / considered
    cid = _concept_id(validation["claim"])
    payload = {
        "claim": validation["claim"],
        "source_episodes": list(dict.fromkeys(cited)),
        "claims": candidate.get("claims") or {},
        "strength": round(strength, 4),
        "episodes_considered": considered,
        "created_by": {"run_id": run_id, "step": step, "source": DISTILL_SOURCE},
    }
    if ttl == "inherit":
        ttl = memory_distill_ttl_from_env()
    rec = store.put(principal, SEMANTIC_KIND, concept_key(cid), payload,
                    created_by={"run_id": run_id, "step": step,
                                "source": DISTILL_SOURCE},
                    ttl=ttl,
                    strength=strength,
                    recall_threshold=(DEFAULT_CONCEPT_THRESHOLD
                                      if recall_threshold is None
                                      else recall_threshold))
    return {"ok": True, "validation": validation, "record": rec, "concept_id": cid,
            "strength": strength}


def distill_and_mint(episodes: Sequence[dict[str, Any]], store: StateStore,
                     principal: str, gateway, *, run_id: str,
                     min_sources: int | None = None,
                     ttl: float | None | str = "inherit",
                     temperature: float = 0.0, max_tokens: int = 1024) -> dict[str, Any]:
    """The one-chat batch: distill N episodes, gate the candidate, mint or refuse."""
    run = distill(episodes, gateway, temperature=temperature, max_tokens=max_tokens)
    result = mint(store, principal, run["candidate"], episodes,
                  run_id=run_id, min_sources=min_sources, ttl=ttl,
                  principals=[principal])
    return {**run, **result}


# ---------------------------------------------- the re-prompt loop's verbs (§6)

def decay_concept(store: StateStore, principal: str, key: str, *,
                  rate: float | None = None) -> dict[str, Any]:
    """Lower a concept's strength by `rate` — `alive but unserved` when below τ."""
    rate = memory_strength_decay_from_env() if rate is None else float(rate)
    rec = store.get(principal, SEMANTIC_KIND, key)
    if rec is None:
        return {"ok": False, "reason": "not found"}
    rec.strength = max(0.0, (rec.strength if rec.strength is not None else 0.0)
                       * (1.0 - rate))
    store._maybe_save()
    return {"ok": True, "strength": rec.strength, "served": rec.is_served(store.clock()),
            "action": "decay"}


def retire_concept(store: StateStore, principal: str, key: str) -> int:
    """Retire (delete) a concept — the re-prompt loop's last verb (§6)."""
    return store.forget(principal, key=key, kind=SEMANTIC_KIND)


def reprompt_action(rec: Any, *, missed: int, contradicted: bool,
                    max_misses: int = 3) -> str:
    """Choose a stale concept's fate by code: re-distill · decay · retire (§6).

    A concept is **never** refreshed by re-serving alone — recency is not truth
    (Day 9's lesson, on the memory shelf). A contradiction re-distills over the
    larger episode set; sustained misses decay below τ; a decayed concept the
    clock finally reaches is retired.
    """
    if contradicted:
        return "re-distill"
    if rec is not None and getattr(rec, "is_decayed", lambda: False)():
        return "retire"
    if missed >= max_misses:
        return "decay"
    return "keep"
