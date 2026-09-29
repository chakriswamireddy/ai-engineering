"""Day 18 §1/§4 — episodic memory: the agent's own past, scored and cited.

Day 17 gave the agent's finished work a home (`kind=task`, `recalled ⊆ authored`).
Day 18 turns the shelf into a *search over the agent's own past*. Nothing here is
a new engine: a finished run becomes a compact **signature** (`episodicize`), and
`recall()` is the series' retrieval arithmetic — Day 8's lexical `Lexicon` +
`fuse_score`, Day 9's `mmr`/`dedup`, Day 7's threshold gate, Day 6's top-k budget
— pointed at those signatures instead of the corpus. The read path is
**lexicon + fusion + MMR**, costed at **0 chats, 0 embeds**: the semantic half of
the fusion is held at 0 (the `PENDING` cross-encoder is the only thing that would
ever spend a chat), so `recall()` is the free deterministic tier.

Two disciplines the module exists to keep:

- **An episode is recalled as an episode.** A finished investigation is a
  *receipt* — returned with its `task_id`, evidence, conclusion, and provenance,
  to be *cited* like any Day 6 source, never re-worded, merged, or generalized.
  `recall_block()` states that in the prompt; generalization is the distiller's,
  and only the distiller (with `kind=semantic`) may mint a lesson.
- **Refuse rather than guess.** An empty shelf returns nothing; a stale episode
  acts absent (Day 17's clock, on this path); a concept whose `strength` fell
  below its recall threshold **acts absent before its TTL** (semantic expiry, §6)
  while staying visible to `list_for` (alive, unserved).

The catalogue is `principal`-keyed, so a recall that served a foreign
principal's investigation is Door S8 opened from the retrieval side — the keying
is the boundary, not a filter applied afterward.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from ai_os.context import dedup, text_sim
from ai_os.lexicon import Lexicon, tokenize
from ai_os.rerank import mmr
from ai_os.retrieval import fuse_score
from ai_os.state import (
    EPISODIC_KIND,
    ORCHESTRATION_KIND,
    PREFERENCE_KIND,
    SEMANTIC_KIND,
    StateRecord,
    StateStore,
    episode_key,
    episode_task_id,
    orchestration_key,
    preference_key,
)

# --------------------------------------------------------------- the knobs
DEFAULT_RECALL_TOP_K = 5        # the served set's size (Day 6's budget floor)
DEFAULT_RECALL_TAU = 0.05       # Day 7's gate, on the memory shelf — below it, absent
DEFAULT_RECALL_ALPHA = 0.0      # lexical-only: the read path is 0 chats, 0 embeds
DEFAULT_MMR_LAMBDA = 0.7        # Day 9's relevance-vs-diversity weight
DEFAULT_DEDUP_TAU = 0.85        # signatures that repeat a kept episode are dropped

MEMORY_ON = "on"
MEMORY_OFF = "off"

MEMORY_KINDS: tuple[str, ...] = (EPISODIC_KIND, SEMANTIC_KIND)

# Entity extraction for the signature: the ids a question/investigation touched,
# kept small enough to score and specific enough to discriminate an episode.
_ENTITY_RE = re.compile(
    r"\b(?:order|checkout|customer|product|payment|user|account|id)"
    r"[ _:-]?([A-Za-z0-9_-]+)\b", re.IGNORECASE)


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


def memory_recall_top_k_from_env() -> int:
    return max(1, _env_int("MEMORY_RECALL_TOP_K", DEFAULT_RECALL_TOP_K))


def memory_recall_tau_from_env() -> float:
    return _env_float("MEMORY_RECALL_TAU", DEFAULT_RECALL_TAU)


def memory_enabled_from_env() -> bool:
    return os.getenv("MEMORY_ENABLED", "1").strip().lower() not in {
        "0", "off", "false", "no"}


# --------------------------------------------------------------- signatures

def extract_entities(text: str) -> list[str]:
    """The order/checkout/customer ids a text names, de-duplicated and ordered."""
    seen: dict[str, None] = {}
    for match in _ENTITY_RE.finditer(text or ""):
        seen.setdefault(match.group(0).lower(), None)
    return list(seen)


def compose_signature(question: str, tools: Iterable[str], entities: Iterable[str],
                      conclusion: str) -> str:
    """The compact, lexicon-friendly fingerprint of one investigation.

    Enough for lexical scoring (the question's paraphrase, the tools used, the
    entities touched, the conclusion), small enough to keep the shelf sized. This
    is Day 8's corpus minus the corpus — no vectors, so `recall` stays free.
    """
    parts = [question.strip(), " ".join(tools), " ".join(entities),
             (conclusion or "").strip()[:240]]
    return " ".join(p for p in parts if p).strip()


def episodicize(run: dict[str, Any], *, task_id: str | None = None) -> dict[str, Any]:
    """Build the `kind=episodic` payload for a finished run.

    `run` is a loop/executor result object (it already carries `question`,
    `tool_calls`, `evidence`, `answer`); `episodicize` only *compacts* it — the
    record is the run's own output, never an invention (the store's closed write
    path would reject an invented one anyway).
    """
    question = str(run.get("question") or "")
    answer = str(run.get("answer") or "")
    tools = [str(c.get("name")) for c in (run.get("tool_calls") or [])
             if isinstance(c, dict) and c.get("name")]
    tools = list(dict.fromkeys(tools))
    entities = list(dict.fromkeys(extract_entities(question + " " + answer)))
    payload = {
        "task_id": task_id or run.get("task_id") or run.get("run_id"),
        "question": question,
        "tools": tools,
        "entities": entities,
        "conclusion": answer,
        "evidence": list(run.get("evidence") or []),
        "signature": compose_signature(question, tools, entities, answer),
    }
    # Day 19 — a specialist's episode carries the *role* that authored it, so the
    # shared shelf can tell one loop's investigation from another's. The role lives
    # in the payload (not in `created_by`) because the store's provenance triple is
    # the citation contract Day 17 fixed; a role is a property of the finding.
    if run.get("role"):
        payload["role"] = run["role"]
    return payload


def remember_episode(store: StateStore, principal: str, episode: dict[str, Any], *,
                     run_id: str, step: Any = "end", role: str | None = None,
                     ttl: float | None | str = "inherit") -> StateRecord | None:
    """Write a finished investigation as an `kind=episodic` record (authored).

    Day 19 — a specialist writes under its own `principal` + `role` provenance: the
    role rides in the payload, and the orchestrator may read it but never *authors*
    one (the write policy, §7). `role=None` is the single loop, byte-for-byte
    Days 17–18.
    """
    payload = dict(episode)
    if role:
        payload["role"] = role
    tid = str(payload.get("task_id") or "")
    return store.put(principal, EPISODIC_KIND, episode_key(tid), payload,
                     created_by={"run_id": run_id, "step": step, "source": "agent"},
                     ttl=ttl)


def episode_role(record: StateRecord) -> str | None:
    """The role that authored an episodic record (`None` for the single loop)."""
    payload = record.payload if isinstance(record.payload, dict) else {}
    return payload.get("role")


def write_orchestration(store: StateStore, principal: str, name: str,
                        payload: dict[str, Any], *, run_id: str,
                        ttl: float | None | str = "inherit") -> StateRecord | None:
    """The orchestrator's own shelf write: routing, delegations, verdicts.

    The one real policy decision of §7: the orchestrator is a **router**, and a
    router that authors investigations claims evidence it never personally grounded.
    So it writes `kind=orchestration` only — never an episodic record — through the
    same closed write path, with `source: "orchestrator"`.
    """
    return store.put(principal, ORCHESTRATION_KIND, orchestration_key(name), payload,
                     created_by={"run_id": run_id, "step": "verdict",
                                 "source": "orchestrator"},
                     ttl=ttl)


def set_preference(store: StateStore, principal: str, key: str, value: Any, *,
                   source: str = "user-stated", run_id: str, ttl: float | None | str = "inherit"
                   ) -> StateRecord | None:
    """A principle-keyed preference record — a *data table*, never a memory.

    Stored under its own `kind=preference`; served to its own answer loop as cited
    data; never distilled, never generalized across principals (§7).
    """
    payload = {"principal": principal, "key": key, "value": value,
               "source": source, "signature": f"{key} {value}"}
    return store.put(principal, PREFERENCE_KIND, preference_key(key), payload,
                     created_by={"run_id": run_id, "step": key, "source": "agent"},
                     ttl=ttl)


def read_preferences(store: StateStore | None, principal: str) -> list[dict[str, Any]]:
    """The principal's own preferences, as citable data rows."""
    if store is None or not store.enabled:
        return []
    rows: list[dict[str, Any]] = []
    for rec in store.list_for(principal, PREFERENCE_KIND):
        payload = rec.payload if isinstance(rec.payload, dict) else {}
        rows.append({"key": payload.get("key"), "value": payload.get("value"),
                     "source": payload.get("source"), "provenance": rec.provenance()})
    return rows


# ---------------------------------------------------------------- the scorer

@dataclass
class MemoryCard:
    """A record as a scorceable card — `chunk_id`/`text` is the retrieval shape.

    Deliberately *not* a `Chunk`: a signature has no vector (the read path never
    embeds), so the card carries only what the lexical scorer and the MMR/dedup
    arithmetic need. `doc_id` mirrors `chunk_id` for `dedup`'s drop records.
    """

    chunk_id: str
    text: str
    record: Any = None
    doc_id: str = ""

    def __post_init__(self) -> None:
        if not self.doc_id:
            self.doc_id = self.chunk_id


def memory_text(rec: StateRecord) -> str:
    """The scorceable text of a memory record (signature, or a concept's claim)."""
    payload = rec.payload if isinstance(rec.payload, dict) else {}
    if rec.kind == EPISODIC_KIND:
        return str(payload.get("signature") or payload.get("question") or "")
    if rec.kind == SEMANTIC_KIND:
        return str(payload.get("claim") or "")
    return str(payload.get("signature") or "")


def _view(score: float, rec: StateRecord) -> dict[str, Any]:
    payload = rec.payload if isinstance(rec.payload, dict) else {}
    return {
        "kind": rec.kind,
        "key": rec.key,
        "task_id": payload.get("task_id") or episode_task_id(rec.key),
        "score": round(float(score), 4),
        "served_as": "episode" if rec.kind == EPISODIC_KIND else "concept",
        "evidence": list(payload.get("evidence") or []),
        "conclusion": payload.get("conclusion"),
        "claim": payload.get("claim"),
        "source_episodes": list(payload.get("source_episodes") or []),
        "strength": rec.strength,
        "provenance": rec.provenance(),
        "principal": rec.principal,
    }


def recall(question: str, principal: str, store: StateStore | None = None, *,
           kinds: Sequence[str] = MEMORY_KINDS,
           top_k: int | None = None, tau: float | None = None,
           alpha: float | None = None, access: bool = True,
           ) -> dict[str, Any]:
    """Return the principal's *matching* past investigations, scored, above τ.

    Day 8/9 turned on the agent's own past: `Lexicon` scores each signature against
    the question, `fuse_score` fuses (semantic half fixed at 0 — no embeds), `mmr`
    de-duplicates near-identical investigations, Day 7's τ refuses below-threshold
    matches, and the top-k is the served set. Every served record is a **citation**
    (an episode carries evidence + `task_id` + provenance), never an asserted fact.
    An empty/stale/decayed shelf returns `recalled: []` — the honest refusal.
    """
    q = (question or "").strip()
    if not q:
        raise ValueError("question must not be empty")
    top_k = memory_recall_top_k_from_env() if top_k is None else max(1, top_k)
    tau = memory_recall_tau_from_env() if tau is None else float(tau)
    alpha = DEFAULT_RECALL_ALPHA if alpha is None else float(alpha)

    result: dict[str, Any] = {
        "question": q, "principal": principal, "kind": "memory",
        "memory_kind": "episodic+semantic", "top_k": top_k, "tau": tau,
        "alpha": alpha, "recalled": [], "scores": [], "refused": True,
        "available": 0, "above_tau": 0, "decayed": [],
        "memory_on_off": MEMORY_OFF if not (store and store.enabled) else MEMORY_ON,
        "cost": {"chats": 0, "embeds": 0},
    }
    if store is None or not store.enabled:
        return result

    records: list[StateRecord] = []
    for kind in kinds:
        records.extend(store.list_for(principal, kind))

    served: list[StateRecord] = []
    decayed: list[StateRecord] = []
    now = store.clock()
    for rec in records:
        if rec.is_decayed():
            decayed.append(rec)              # §6 — alive but below its serve τ
        elif not rec.is_expired(now):
            served.append(rec)
    result["decayed"] = [_view(0.0, r) for r in decayed]
    result["available"] = len(served)
    if not served:
        return result

    cards = [MemoryCard(chunk_id=r.key, text=memory_text(r), record=r) for r in served]
    lexicon = Lexicon(cards, tokenizer=tokenize)
    raw = lexicon.normalized(tokenize(q))
    # Day 8's fusion, with the semantic half held at 0 (the memory read path is
    # the free lexical tier; a cross-encoder here would be a chat per candidate).
    scored = [(fuse_score(alpha, 0.0, s), c) for s, c in zip(raw, cards)]
    above = [(s, c) for s, c in scored if s > 0.0 and s >= tau]
    result["above_tau"] = len(above)
    if not above:
        return result

    ranked = sorted(above, key=lambda t: (t[0], t[1].chunk_id), reverse=True)
    diverse, _trace = mmr(ranked, lam=DEFAULT_MMR_LAMBDA, k=top_k,
                          sim=lambda a, b: text_sim(a.text, b.text))
    kept, _drops = dedup(diverse, sim_tau=DEFAULT_DEDUP_TAU)

    for score, card in kept:
        result["recalled"].append(_view(score, card.record))
        result["scores"].append(round(float(score), 4))
        if access:
            store.touch(card.record.principal, card.record.kind, card.record.key)
    result["refused"] = not result["recalled"]
    return result


def recall_block(result: dict[str, Any]) -> str | None:
    """The prompt block a recall contributes — episodes, marked *as* episodes.

    The answer may **cite** these past investigations but must not absorb one as a
    general truth: that promotion is the distiller's, and only the distiller mints a
    lesson. Returned `None` when the shelf served nothing, so an empty recall
    changes nothing (M8).
    """
    episodes = [e for e in result.get("recalled") or [] if e["served_as"] == "episode"]
    concepts = [c for c in result.get("recalled") or [] if c["served_as"] == "concept"]
    if not episodes and not concepts:
        return None
    lines = [
        "Prior memory. Serve these as RECORDS you may cite, not as rules: do not "
        "generalize an episode into a lesson (only a distilled concept is a "
        "lesson), and quote the specific investigation you rely on.",
    ]
    for e in episodes:
        detail = e.get("conclusion") or "(no conclusion recorded)"
        lines.append(f"- EPISODE task {e['task_id']} (score {e['score']}, "
                     f"provenance {e['provenance']}): {detail}")
    for c in concepts:
        lines.append(f"- CONCEPT (distilled, score {c['score']}, strength "
                     f"{c['strength']}, sources {c['source_episodes']}): "
                     f"{c.get('claim')}")
    return "\n".join(lines)


def preference_block(store: StateStore | None, principal: str) -> tuple[str | None,
                                                                         list[dict]]:
    """The preference data table, as citable data (never a lesson)."""
    rows = read_preferences(store, principal)
    if not rows:
        return None, []
    lines = ["User preferences (data about THIS principal; cite as data and never "
             "generalize across users):"]
    for r in rows:
        lines.append(f"- {r['key']} = {r['value']} ({r['source']})")
    return "\n".join(lines), rows
