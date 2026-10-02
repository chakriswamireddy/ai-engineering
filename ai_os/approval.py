"""Day 27 §6 — the approval desk: one digest, one state machine, and the refusals.

Twenty-six days gave this repository authority and Day 26 gave it a decision
procedure over that authority. Day 27 hands the last decision to a **person** —
and finds that the interesting part is not the handoff. It is that the step the
human is supposed to be guarding (the mutation) is the one step this system has
never had: `SqliteDataStore`/`PostgresDataStore` expose a single `query` method,
`default_transaction_read_only` is on, and `policy.yaml` says
`statement_allow: [select, with]`. So this module builds the **decision**, which
is fully exercisable without a row changing: *the security-relevant event is the
decision, not the mutation.* Nothing here refunds anybody, and the word never
appears as a description of what was built.

Five claims carry the module, each a testable property rather than a paragraph:

1. **The layer opens the request; the model never can.** `submit` is called by
   the choke point (`ToolRegistry.execute`) as a side effect of a *grant* that
   says `confirm: human`. There is no `request_approval` tool, no prompt text,
   and no path from the model's position to this module. `REASONING_PROMPT` is
   byte-identical for the twenty-seventh day running.
2. **An approval is a decision about a digest, not about a description.**
   `digest_action` hashes the canonical `(tool, args, effect, data,
   reversibility, targets)` using **Day 26's own canonicalization** (extracted,
   not retyped — two hashing conventions in one repository is how a digest ends
   up reproducible by nobody). It is stable under argument key order, changed by
   any single field, and deliberately *unchanged* by a run id or a model
   rationale, because a re-run that proposes the same action is the same
   approval. The executor recomputes it from the arguments it is about to run and
   refuses on mismatch (`approval_digest_mismatch`) — the time-of-check /
   time-of-use hole that most systems in this genre ship.
3. **Signals, never a score.** `build_signals` produces `{value, measures, not}`
   triples and **no aggregate** (asserted inside the builder *and* re-asserted
   from outside by H6, because a rule only the implementer can see is a rule the
   next implementer deletes). The model's own stated confidence is recorded as
   `not_a_signal: true` with a reason — visible and greppable, so the next person
   who wants a threshold has to delete a *record of the decision* rather than
   overlook a gap. And the rule that replaces the number is negative: **the
   absence of a signal is never a green light** (`scrutiny: elevated`).
4. **One durable record, append-only, and no update method exists.** The record
   lives in Day 17's `StateStore` (`kind="approval"`, author `{"source":
   "approver"}`), so durability, TTL, the size cap, the derived index, compaction
   and the fail-closed author check are inherited rather than rebuilt. Correction
   is a **second** record with a `compensates` pointer; the original is never
   edited, because a record you can edit is a record whose approval cannot be
   reconstructed.
5. **Silence is never consent.** An expired request is `expired`, a decision with
   no actor is a `ValueError` rather than a record, a duplicate decision is
   refused, and a reversal of an irreversible action is refused loudly
   (`approval_reversal_impossible`) — a system that will "undo" an irreversible
   action will eventually tell a customer their refund was reversed.

Two clocks, deliberately different, and the distinction is load-bearing: the
**state store's TTL is the retention of the artifact**, while the record's
`expires_at` is the **decision window** (`APPROVAL_TTL_S`, 900 s on Day 17's own
reasoning that a long TTL is a conscious config). A record that vanished at
`expires_at` could not report `approval_expired` — the refusal needs the record
to still exist — so the state write inherits the store's retention and the
decision window is checked explicitly.

Refusals are two different *kinds* of thing, and the split is a rule rather than
a convenience: a `ValueError` (no actor, no reason on a rejection) means **the
attempt is not a record at all** and nothing is written; an `ApprovalRefusal`
carrying one of the codes below means **the attempt is a record** — it lands in
the chain with its code, in the same hash chain the model's ask lands in.

Everything here is `MECH`, offline, dependency-free and clock-injected: no
network, no model call, and no `time.sleep` anywhere. **0 chats, 0 embeds, 0 SQL,
0 rows.**
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

from ai_os import audit as audit_mod
# The reversibility vocabulary is defined *once*, in the file that reads it out
# of the config (`permissions.py`), and re-exported here so `approval.py`'s
# namespace carries the names the day promises. One definition, two importers —
# never two definitions that can drift.
from ai_os.permissions import (  # noqa: F401  (re-exported on purpose)
    REVERSIBILITY_COMPENSATABLE,
    REVERSIBILITY_IRREVERSIBLE,
    REVERSIBILITY_REVERSIBLE,
    REVERSIBILITIES,
    ROLE_EXECUTOR,
)


class _LazyState:
    """`ai_os.state`, resolved on first use — because the import graph is a cycle.

    `state` imports `planner`, `planner` imports `tools`, and `tools` imports this
    module (for the gate's error kind), so a top-level `from ai_os import state`
    here is a circular import. The same lazy-import discipline Day 26 used for
    `tools` inside `permissions`, applied to the one edge the graph does not allow —
    and it costs nothing: the attribute is looked up when a record is written, by
    which point every module is fully constructed.
    """

    def __getattr__(self, name: str) -> Any:
        from ai_os import state as mod
        return getattr(mod, name)


state_mod = _LazyState()

# ============================================================ Day 27 — vocabulary

# --- the states, and the only legal edges (Day 27 §3).
STATE_REQUESTED = "requested"
STATE_APPROVED = "approved"
STATE_REJECTED = "rejected"
STATE_EXPIRED = "expired"
STATE_APPLIED = "applied"
STATE_COMPENSATED = "compensated"
STATE_SUPERSEDED = "superseded"
STATES = (STATE_REQUESTED, STATE_APPROVED, STATE_REJECTED, STATE_EXPIRED,
          STATE_APPLIED, STATE_COMPENSATED, STATE_SUPERSEDED)

# The states that already carry a decision. A decision is final, so a second one
# is a `duplicate`; the states without one (expired, superseded) are an
# `invalid_state`. The two codes are different facts with different owners.
DECIDED_STATES = frozenset({STATE_APPROVED, STATE_REJECTED, STATE_APPLIED,
                            STATE_COMPENSATED})

# --- the six audit event codes. Defined once, in `audit.py`, because they are the
# chain's vocabulary; aliased here under the names the day's design uses.
EVENT_REQUESTED = audit_mod.EVENT_APPROVAL_REQUESTED
EVENT_APPROVED = audit_mod.EVENT_APPROVAL_APPROVED
EVENT_REJECTED = audit_mod.EVENT_APPROVAL_REJECTED
EVENT_EXPIRED = audit_mod.EVENT_APPROVAL_EXPIRED
EVENT_APPLIED = audit_mod.EVENT_APPROVAL_APPLIED
EVENT_COMPENSATED = audit_mod.EVENT_APPROVAL_COMPENSATED
EVENTS = tuple(audit_mod.APPROVAL_EVENTS)

# --- the tool-layer error kind the gate returns (`ToolRegistry.execute`).
ERROR_APPROVAL_REQUIRED = "approval_required"

# --- the refusals. Each is a *design statement* with a docstring further down;
# the code is part of the contract for the same reason Day 26's `error_kind` is:
# a security decision without a name is a decision nobody can grep for at 3 a.m.
REFUSAL_EXPIRED = "approval_expired"
REFUSAL_DUPLICATE = "approval_duplicate"
REFUSAL_SELF_APPROVED = "approval_self_approved"
REFUSAL_ROLE_NOT_PERMITTED = "approval_role_not_permitted"
REFUSAL_DIGEST_MISMATCH = "approval_digest_mismatch"
REFUSAL_REVERSAL_IMPOSSIBLE = "approval_reversal_impossible"
REFUSAL_INVALID_STATE = "approval_invalid_state"
# The eighth code, and it is a *resource bound* rather than a design statement:
# unlike Day 26's audit log, the thing creating requests is not only the model — a
# caller can, by asking repeatedly — so `APPROVAL_MAX_PER_RUN` keeps a decision
# queue from being flooded by one loop. Real rate limiting is Day 28's.
REFUSAL_RUN_LIMIT = "approval_run_limit"
REFUSALS = frozenset({REFUSAL_EXPIRED, REFUSAL_DUPLICATE, REFUSAL_SELF_APPROVED,
                      REFUSAL_ROLE_NOT_PERMITTED, REFUSAL_DIGEST_MISMATCH,
                      REFUSAL_REVERSAL_IMPOSSIBLE, REFUSAL_INVALID_STATE,
                      REFUSAL_RUN_LIMIT})

# --- scrutiny: the *negative* rule that replaces the number.
SCRUTINY_ROUTINE = "routine"
SCRUTINY_ELEVATED = "elevated"

# The four measurement signals. Each names what it measures and what it does not,
# because they are **not commensurable** (Day 7's cosine measures similarity, Day
# 21's `cited ⊆ retrieved` measures support, Day 26's `data:` is a *declaration*
# of consequence, and the store's own history is novelty) — and every aggregate of
# them is an opinion wearing a decimal point.
MEASUREMENT_SIGNALS = ("similarity", "support", "consequence", "novelty")

# The one key an aggregate could never hide behind: a regex scan, so the rule is
# `False` for a reason a reader can re-run rather than an assertion they must
# trust.
_AGGREGATE_RE = re.compile(r"score|risk|confidence|probability", re.IGNORECASE)

# The model's stated confidence rides under a key that deliberately does **not**
# contain the word "confidence": the no-aggregate rule is checked by *scanning*,
# and a scan cannot tell our audited exception from the thing we excluded. The
# parameter keeps its name; the key carries the exclusion and the reason.
MODEL_STATED_KEY = "model_stated"
NOT_A_SIGNAL_REASON = (
    "a stated confidence is a property of the next-token distribution, not of the "
    "world; it is steerable by the indirect injection Day 25 catalogued; and it is "
    "the confused deputy reporting on its own reliability to the person whose "
    "signature is being collected")

# ============================================================ the config knobs

APPROVALS_ENV = "APPROVALS"                     # `none` restores the ungated path
APPROVALS_PATH_ENV = "APPROVALS_PATH"           # a state-shaped store, approvals only
APPROVAL_TTL_ENV = "APPROVAL_TTL_S"             # the decision window
APPROVAL_MAX_PER_RUN_ENV = "APPROVAL_MAX_PER_RUN"
APPROVAL_EXECUTOR_PRINCIPAL_ENV = "APPROVAL_EXECUTOR_PRINCIPAL"
DEFAULT_APPROVAL_TTL_S = 900.0                  # Day 17's reasoning: short by default
DEFAULT_MAX_PER_RUN = 1
DEFAULT_EXECUTOR_PRINCIPAL = ROLE_EXECUTOR
DEFAULT_APPROVALS_PATH = (Path(__file__).resolve().parent.parent
                          / "data" / "approvals.json")
DEFAULT_REQUEST_PREFIX = "apr"
# The principal column of an expiry nobody decided. Naming it plainly is the
# point: the clock ended the request, not a person, and the chain says so.
EXPIRY_PRINCIPAL = "clock"

_OFF_VALUES = {"0", "off", "false", "no", "none", "disabled"}


def enabled() -> bool:
    """The layer's single switch, mirroring Day 26's `PERMISSIONS`.

    Off ⇒ no gate, no record, no chain entry. The switch exists so the H9 control
    can prove the layer is *passive*: with it off a legitimate request is
    byte-identical and the decision log is empty.
    """
    return os.getenv(APPROVALS_ENV, "1").strip().lower() not in _OFF_VALUES


def approval_ttl_from_env() -> float:
    try:
        return float(os.getenv(APPROVAL_TTL_ENV, "").strip() or DEFAULT_APPROVAL_TTL_S)
    except ValueError:
        return DEFAULT_APPROVAL_TTL_S


def approval_max_per_run_from_env() -> int:
    try:
        return int(os.getenv(APPROVAL_MAX_PER_RUN_ENV, "").strip()
                   or DEFAULT_MAX_PER_RUN)
    except ValueError:
        return DEFAULT_MAX_PER_RUN


def executor_principal_from_env() -> str:
    return os.getenv(APPROVAL_EXECUTOR_PRINCIPAL_ENV, "").strip() or DEFAULT_EXECUTOR_PRINCIPAL


def approvals_path_from_env() -> Path:
    configured = os.getenv(APPROVALS_PATH_ENV, "").strip()
    return Path(configured) if configured else DEFAULT_APPROVALS_PATH


class ApprovalRefusal(ValueError):
    """A refused transition **that is a record**: it carries a code, names the
    request, the current state and what was attempted, and lands in the chain.

    Contrast `ValueError`: an attempt with no actor, or a rejection with no
    reason, is not a decision this system may write down at all, so it produces
    no record and no chain entry. The split is what keeps "every refusal is
    audible" from becoming "every typo is an event".
    """

    def __init__(self, code: str, message: str, *,
                 request_id: str | None = None, state: str | None = None) -> None:
        self.code = code
        self.request_id = request_id
        self.state = state
        super().__init__(f"{code}: {message}")


# ============================================================ Day 27 §3 — signals

def _assert_no_aggregate(signals: dict[str, Any]) -> None:
    """No key may look like a threshold somebody else wrote.

    Checked here *and* from outside (H6): a rule that lives only inside the
    builder is a rule the next contributor can delete, and `risk: 0.87` invites
    the reader to apply a threshold nobody in the system wrote.
    """
    bad = [str(k) for k in signals if _AGGREGATE_RE.search(str(k))]
    if bad:
        raise ValueError(
            f"signal key(s) {bad} match score|risk|confidence|probability — there "
            "is no aggregate, and a traffic-light chip is worse than a raw number "
            "because a raw number invites the reader to distrust it")


@dataclass(frozen=True)
class Signals:
    """The request's signal set: triples, plus the *negative* rule.

    `entries` maps a signal name to `{value, measures, not}` — each names what it
    measures and what it does not. `scrutiny` is `elevated` whenever any
    measurement is **missing** (absent is never a green light), and `scrutiny_
    reason` names which ones, so the record says "more scrutiny" in words rather
    than in arithmetic.
    """

    entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    scrutiny: str = SCRUTINY_ELEVATED
    scrutiny_reason: str = ""

    def keys(self) -> list[str]:
        return sorted(self.entries)

    def value(self, name: str) -> Any:
        return (self.entries.get(name) or {}).get("value")

    def present(self) -> list[str]:
        """The signals that carry a measurement (the model's own is not one)."""
        return [k for k in self.entries if not self.entries[k].get("not_a_signal")]

    def as_dict(self) -> dict[str, Any]:
        return {"signals": {k: dict(v) for k, v in self.entries.items()},
                "scrutiny": self.scrutiny,
                "scrutiny_reason": self.scrutiny_reason}

    @classmethod
    def from_dict(cls, raw: Any) -> "Signals":
        """Normalize whatever arrived into a checked signal set (fail closed).

        A bare `{name: triple}` mapping is accepted (the shape `build_signals`
        produces), as is its `{"signals": …}` wrapper. Every key is re-checked
        against the no-aggregate rule, so an author who hand-builds a signal set
        gets the same refusal the builder would have given.
        """
        if isinstance(raw, Signals):
            return raw
        if not isinstance(raw, dict):
            raise ValueError("signals must be a mapping of name → {value, measures, not}")
        if "signals" in raw and isinstance(raw.get("signals"), dict):
            entries = {str(k): dict(v) for k, v in raw["signals"].items()
                       if isinstance(v, dict)}
            _assert_no_aggregate(entries)
            return cls(entries=entries,
                       scrutiny=str(raw.get("scrutiny") or SCRUTINY_ROUTINE),
                       scrutiny_reason=str(raw.get("scrutiny_reason") or ""))
        entries = {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)}
        _assert_no_aggregate(entries)
        absent = [k for k in MEASUREMENT_SIGNALS if k not in entries]
        return cls(entries=entries,
                   scrutiny=(SCRUTINY_ELEVATED if absent else SCRUTINY_ROUTINE),
                   scrutiny_reason=(f"no signal for {', '.join(absent)} — absence "
                                    "of a signal is never a green light"
                                    if absent else ""))

    def as_trace(self) -> dict[str, Any]:
        """The metadata-safe view: signal **keys** and their caveats, no scoring
        vocabulary, and the model's non-signal labelled as such."""
        return {"signals": sorted(self.entries),
                "scrutiny": self.scrutiny,
                "not_a_signal": sorted(k for k in self.entries
                                       if self.entries[k].get("not_a_signal"))}


def build_signals(*, last_result: Any = None, cited_subset_retrieved: bool | None = None,
                  prior_approvals: int | None = None, declared_data: str | None = None,
                  model_stated_confidence: float | None = None) -> dict[str, Any]:
    """The request's signals — triples with caveats, and **no aggregate**.

    Each of the four measurements names what it measures *and what it does not*,
    and there is no branch that could turn them into a gate input. The one
    candidate excluded on purpose — the model's own stated confidence — is
    accepted, recorded, and marked `not_a_signal: true` with a one-line reason:
    the exclusion is a *record* rather than an omission, so the next contributor
    has to delete a decision rather than overlook a gap.

    `last_result`/`cited_subset_retrieved`/`prior_approvals`/`declared_data` are
    the four measurements available at this seam; whatever is missing produces
    `scrutiny: elevated` and the reason names it. An **empty** call is therefore
    the highest-scrutiny request the builder can produce — never a pass.
    """
    entries: dict[str, dict[str, Any]] = {}

    score = _top_score(last_result)
    if score is not None:
        entries["similarity"] = {
            "value": score,
            "measures": "how close the retrieved text is to the query "
                        "(Day 7's cosine gate at DEFAULT_TAU)",
            "not": "whether the text is *correct* — a confident score over the "
                   "wrong paragraph is still wrong",
        }
    if cited_subset_retrieved is not None:
        entries["support"] = {
            "value": bool(cited_subset_retrieved),
            "measures": "whether the answer is built from what was gathered "
                        "(Day 21's cited ⊆ retrieved)",
            "not": "whether what was gathered was *right* — the system can gather "
                   "the wrong thing impeccably",
        }
    if declared_data is not None:
        entries["consequence"] = {
            "value": str(declared_data),
            "measures": "what the action does — a *declaration* from the grant "
                        "(data class), not an estimate",
            "not": "anything about likelihood at all",
        }
    if prior_approvals is not None:
        entries["novelty"] = {
            "value": int(prior_approvals),
            "measures": "how often this exact action has been approved before "
                        "(the store's own history)",
            "not": "whether this instance is justified — precedent is not evidence",
        }
    if model_stated_confidence is not None:
        entries[MODEL_STATED_KEY] = {
            "value": float(model_stated_confidence),
            "measures": "the next-token distribution of the model's own completion",
            "not": "the truth of the claim, and it is steerable by untrusted text",
            "not_a_signal": True,
            "reason": NOT_A_SIGNAL_REASON,
        }

    _assert_no_aggregate(entries)
    absent = [k for k in MEASUREMENT_SIGNALS if k not in entries]
    scrutiny = SCRUTINY_ELEVATED if absent else SCRUTINY_ROUTINE
    reason = ("no signal was available for " + ", ".join(absent)
              + " — absence of a signal is never a green light") if absent else ""
    return Signals(entries=entries, scrutiny=scrutiny,
                   scrutiny_reason=reason).as_dict()


def _top_score(last_result: Any) -> float | None:
    """The best evidence score of a `ToolResult`-shaped observation, or `None`."""
    rows = list(getattr(last_result, "evidence", None) or [])
    scores = [r.get("score") for r in rows
              if isinstance(r, dict) and isinstance(r.get("score"), (int, float))]
    return round(float(max(scores)), 4) if scores else None


# ============================================================ Day 27 §3 — the digest

def _canonical_args(args: Any) -> dict[str, Any]:
    """Arguments as a plain dict, whatever arrived (a JSON string is accepted).

    Values are never coerced, truncated or reordered: `json.dumps(..., sort_keys)`
    does the ordering, so key order cannot change the digest.
    """
    if args is None:
        return {}
    if isinstance(args, str):
        try:
            parsed = json.loads(args or "{}")
        except (json.JSONDecodeError, TypeError):
            return {"_raw": args}
        return parsed if isinstance(parsed, dict) else {"_raw": parsed}
    if isinstance(args, dict):
        return dict(args)
    return {"_raw": args}


# The argument *names* that declare a target. The target set is derived from the
# schema's own identifier arguments — never from the model's prose — so the digest
# covers "which orders" without a second vocabulary for it.
_TARGET_SUFFIXES = ("_ids", "_id", "ids", "id")
_TARGET_LIMIT = 64


def action_targets(args: Any) -> list[str]:
    """The target ids an action names, derived from the argument names.

    Deterministic and offline: every argument whose name ends in `id`/`ids`
    contributes its value (a comma-separated string is split, a list is walked),
    de-duplicated in first-seen order and bounded. A target list is part of the
    digest because "approve the export" means nothing without *which rows*.
    """
    out: list[str] = []
    for key in sorted(_canonical_args(args)):
        if not str(key).lower().endswith(_TARGET_SUFFIXES):
            continue
        value = _canonical_args(args)[key]
        items = value if isinstance(value, (list, tuple)) else str(value).split(",")
        for item in items:
            text = str(item).strip()
            if text and text not in out:
                out.append(text)
    return out[:_TARGET_LIMIT]


def digest_action(tool: str, args: Any, *, effect: str, data: str,
                  reversibility: str, targets: Iterable[str] = ()) -> str:
    """`sha256:` over the canonical action — the day's smallest load-bearing object.

    Covers `(tool, args, effect, data, reversibility, targets)` and **nothing
    else**: no run id, no rationale, no timestamp. Canonicalization is literally
    Day 26's (`canonical_json`: sorted keys, fixed separators, UTF-8, no floats),
    extracted rather than retyped, so the repository has **one** hashing
    convention and any of these digests is reproducible by a reader with a shell.

    The deliberate exclusion is the point: an approver who says "yes, do that"
    has approved an *action*, so a re-run that proposes the same action with a
    different chain of thought is the same approval — and hashing the reasoning
    instead would make every re-run a new approval, which is how systems train
    people to rubber-stamp.
    """
    payload = {"tool": str(tool), "args": _canonical_args(args),
               "effect": str(effect), "data": str(data),
               "reversibility": str(reversibility),
               "targets": [str(t) for t in (targets or ())]}
    return audit_mod.entry_hash(payload)


# ============================================================ Day 27 §3 — the record

def record_fingerprint(record: "ApprovalRecord") -> str:
    """A stable hash of the record's own bytes — the no-in-place-mutation proof.

    Not the chain's encoding: the audit chain forbids floats so a hash is
    reproducible across platforms, while a record fingerprint is an equality
    check over the record the desk actually holds (which carries `requested_at`
    and `expires_at`). Two encodings, two purposes, both written down.
    """
    blob = json.dumps(record.as_dict(), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass
class ApprovalRecord:
    """One request, its decision, its application and its compensation.

    `subject` is the **canonical rendering**: tool, argument *names and value
    shapes*, effect, data class, reversibility and targets — composed in zero
    fields by the model, so the artifact a person signs is not written by the
    component asking for the signature. Argument *values* live in `args` (an
    approver must see which orders are about to be exported) and never in the
    audit entry, which is Day 26's rule unchanged.

    `proposal` is the one model-authored field, and it is deliberately (a) not
    hashed into the digest, (b) labelled untrusted at every rendering, and (c)
    never written to the chain — the one place in this pipeline where untrusted
    text would be laundered into a trusted artifact.
    """

    request_id: str
    tool: str
    digest: str
    state: str = STATE_REQUESTED
    subject: dict[str, Any] = field(default_factory=dict)
    signals: dict[str, Any] = field(default_factory=dict)
    args: dict[str, Any] = field(default_factory=dict)
    targets: list[str] = field(default_factory=list)
    effect: str | None = None
    data: str | None = None
    reversibility: str = REVERSIBILITY_IRREVERSIBLE
    approver_role: str | None = None
    requested_by: str = ""
    tenant: str = "default"                 # WS1 — read-scoping, not a secret
    requested_at: float = 0.0
    expires_at: float = 0.0
    ttl_s: float = DEFAULT_APPROVAL_TTL_S
    run_id: str | None = None
    decided_by: str | None = None
    decided_at: float | None = None
    reason: str | None = None
    applied_by: str | None = None
    applied_at: float | None = None
    # WS4 — the measured outcome of the applied mutation (rows the engine
    # reports changed), written once by `record_outcome`. Reads back on
    # idempotent apply replays and executor resume, so neither re-executes.
    applied_rows_changed: int = 0
    compensates: str | None = None
    proposal: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ views
    def is_expired(self, now: float) -> bool:
        return bool(self.expires_at) and now >= float(self.expires_at)

    def signal_keys(self) -> list[str]:
        return sorted((self.signals or {}).get("signals") or {})

    def scrutiny(self) -> str:
        return str((self.signals or {}).get("scrutiny") or SCRUTINY_ROUTINE)

    def attributable(self) -> bool:
        """Every decision on this record names its actor — `Receipt.attributable()`
        one shelf over.

        A `requested` record is attributable because the layer authored it; a
        decided one must name `decided_by`; an applied one must name `applied_by`.
        A record that claims a decision and names nobody is not a record, it is a
        shape — which is why `decide()` refuses to write one in the first place.
        """
        if not self.request_id or not self.requested_by or not self.digest:
            return False
        if self.state in DECIDED_STATES and not self.decided_by:
            return False
        if self.state in (STATE_APPLIED, STATE_COMPENSATED) and not self.applied_by:
            return False
        return True

    def values_masked(self) -> dict[str, Any]:
        """The record with argument **values** replaced by their shapes — the
        read-only surface's default (Day 26's metadata-safe rule, extended)."""
        out = self.as_dict()
        out.pop("proposal", None)
        out["args"] = audit_mod.args_shape(self.args)
        out["values_masked"] = True
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id, "tool": self.tool, "digest": self.digest,
            "state": self.state, "subject": dict(self.subject or {}),
            "signals": dict(self.signals or {}), "args": dict(self.args or {}),
            "targets": list(self.targets), "effect": self.effect, "data": self.data,
            "reversibility": self.reversibility,             "approver_role": self.approver_role,
            "requested_by": self.requested_by, "tenant": self.tenant,
            "requested_at": self.requested_at,
            "expires_at": self.expires_at, "ttl_s": self.ttl_s, "run_id": self.run_id,
            "decided_by": self.decided_by, "decided_at": self.decided_at,
            "reason": self.reason, "applied_by": self.applied_by,
            "applied_at": self.applied_at,
            "applied_rows_changed": int(self.applied_rows_changed or 0),
            "compensates": self.compensates,
            "proposal": self.proposal, "provenance": dict(self.provenance or {}),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "ApprovalRecord":
        if not isinstance(raw, dict):
            raise ValueError("an approval record must be a mapping")
        return cls(
            request_id=str(raw.get("request_id") or ""),
            tool=str(raw.get("tool") or ""),
            digest=str(raw.get("digest") or ""),
            state=str(raw.get("state") or STATE_REQUESTED),
            subject=dict(raw.get("subject") or {}),
            signals=dict(raw.get("signals") or {}),
            args=dict(raw.get("args") or {}),
            targets=[str(t) for t in (raw.get("targets") or [])],
            effect=raw.get("effect"), data=raw.get("data"),
            reversibility=str(raw.get("reversibility") or REVERSIBILITY_IRREVERSIBLE),
            approver_role=raw.get("approver_role"),
            requested_by=str(raw.get("requested_by") or ""),
            tenant=str(raw.get("tenant") or "default"),
            requested_at=float(raw.get("requested_at") or 0.0),
            expires_at=float(raw.get("expires_at") or 0.0),
            ttl_s=float(raw.get("ttl_s") or DEFAULT_APPROVAL_TTL_S),
            run_id=raw.get("run_id"), decided_by=raw.get("decided_by"),
            decided_at=(None if raw.get("decided_at") is None
                        else float(raw["decided_at"])),
            reason=raw.get("reason"), applied_by=raw.get("applied_by"),
            applied_at=(None if raw.get("applied_at") is None
                        else float(raw["applied_at"])),
            applied_rows_changed=int(raw.get("applied_rows_changed") or 0),
            compensates=raw.get("compensates"), proposal=raw.get("proposal"),
            provenance=dict(raw.get("provenance") or {}))

    def _replace(self, **fields: Any) -> "ApprovalRecord":
        """A **new** record with fields replaced — the no-in-place-mutation rule.

        There is no `update` method on the store, and no method here mutates
        `self`: every transition returns a record, and the record is written as a
        whole. A record you can edit is a record whose approval you cannot
        reconstruct.
        """
        raw = self.as_dict()
        raw.update(fields)
        return ApprovalRecord.from_dict(raw)


# ============================================================ Day 27 §3 — the store

class ApprovalStore:
    """The state machine over the durable record — no I/O beyond what it is handed.

    The precondition order is itself a design decision and is written down as
    one, because a future caller who reorders it into a weaker check is the exact
    failure mode Days 11 and 26 have been closing since the beginning. On the
    decision side: **actor present · record exists · state is `requested` ·
    unexpired · the decider holds the grant's `approver_role` · the decider is not
    the requester for anything irreversible · a rejection carries a reason.** On
    the apply side: **actor present · record exists · unexpired · state is
    `approved` · the caller holds the `executor` grant · the digest recomputed
    from the arguments about to be run equals the stored digest.** Expiry is
    checked *before* the state on the apply path so a late apply is named as an
    expiry ("the window closed") rather than as a permission problem.

    The store is read through `StateStore.records` rather than `get`, and that is
    deliberate: the store's read path hides an expired record, while the approval
    desk must still be able to *name* one — "silence is not consent" means the
    expired request is a **coded decision**, not a missing record.
    """

    def __init__(self, store: Any, *, clock=time.time,
                 ttl_s: float | None = None, max_per_run: int | None = None,
                 permissions: Any = None, audit: Any = None,
                 request_prefix: str = DEFAULT_REQUEST_PREFIX) -> None:
        if store is None:
            raise ValueError("ApprovalStore needs a StateStore — the record's home "
                             "is the provenance-enforced store, not a new file")
        self.store = store
        self.clock = clock
        self.ttl_s = (DEFAULT_APPROVAL_TTL_S if ttl_s is None else float(ttl_s))
        self.max_per_run = (DEFAULT_MAX_PER_RUN if max_per_run is None
                            else int(max_per_run))
        self._permissions = permissions
        self.audit = audit if audit is not None else audit_mod.NullAuditLog()
        self._prefix = str(request_prefix or DEFAULT_REQUEST_PREFIX)

    # ------------------------------------------------------------- properties
    @property
    def permissions(self) -> Any:
        """The grant file the roles are read from, loaded lazily (no cycle, no
        disk touch unless a role is actually checked)."""
        if self._permissions is None:
            from ai_os.permissions import default_permission_set
            self._permissions = default_permission_set()
        return self._permissions

    def holds_role(self, principal: str | None, role: str | None) -> bool:
        """Is this a **grant**? Yes only if the grant file lists the principal
        under the role. A gate that says only "human" is a boolean with no list
        behind it, and a boolean is a value anyone can set in a config file."""
        if not principal or not role:
            return False
        fn = getattr(self.permissions, "holds_approver_role", None)
        return bool(fn(principal, role)) if callable(fn) else False

    # ------------------------------------------------------------------ reads
    def _all(self) -> list[ApprovalRecord]:
        out: list[ApprovalRecord] = []
        for (_p, kind, _key), rec in self.store.records.items():
            if kind != state_mod.APPROVAL_KIND:
                continue
            payload = rec.payload if isinstance(rec.payload, dict) else {}
            try:
                out.append(ApprovalRecord.from_dict(payload))
            except ValueError:                       # pragma: no cover — a corrupt row
                continue
        return sorted(out, key=lambda r: (r.requested_at, r.request_id))

    def get(self, request_id: str) -> ApprovalRecord | None:
        for rec in self._all():
            if rec.request_id == str(request_id):
                return rec
        return None

    def list_for(self, principal: str, *, state: str | None = None,
                 tool: str | None = None,
                 tenant: str | None = None) -> list[ApprovalRecord]:
        """The records a principal is a party to — requester, decider, executor.

        Three roles, three reasons to look, one shelf: "what am I waiting on",
        "what did I sign", "what did I run". WS1 adds the tenant scope: a
        caller never sees another tenant's records through this path.
        """
        out = []
        for rec in self._all():
            if principal not in (rec.requested_by, rec.decided_by, rec.applied_by):
                continue
            if tenant is not None and rec.tenant != tenant:
                continue
            if state is not None and rec.state != state:
                continue
            if tool is not None and rec.tool != tool:
                continue
            out.append(rec)
        return out

    def pending(self) -> list[ApprovalRecord]:
        """Undecided requests — the queue (length one today, and nobody measures it)."""
        return [r for r in self._all() if r.state == STATE_REQUESTED]

    def all_records(self) -> list[ApprovalRecord]:
        """Every record on the shelf, in request order — the operator's view.

        Not a widening of `list_for`: the *operator* is not a party to the requests
        (`server.py` has no identity to be a party with), and the CLI's `--list` is
        how the queue is read. The narrowing that exists is deliberate — a
        *principal* may ask what it is a party to, while the desk and its operator
        read the shelf.
        """
        return self._all()

    def counts(self) -> dict[str, int]:
        counts = {s: 0 for s in STATES}
        for rec in self._all():
            if rec.state in counts:
                counts[rec.state] += 1
        counts["total"] = len(self._all())
        return counts

    def prior_approvals(self, tool: str, targets: Iterable[str] = ()) -> int:
        """How often this exact action has been approved before (the `novelty`
        signal's input; a count from the store, never a score)."""
        want = {str(t) for t in (targets or ())}
        return sum(1 for r in self._all()
                   if r.tool == str(tool) and set(r.targets) == want
                   and r.state in (STATE_APPROVED, STATE_APPLIED, STATE_COMPENSATED))

    # -------------------------------------------------------------- the writes
    def _write(self, rec: ApprovalRecord) -> ApprovalRecord:
        state_mod.write_approval(self.store, rec.requested_by, rec.request_id,
                                 rec.as_dict(), run_id=rec.run_id, ttl="inherit")
        return rec

    def _record(self, rec: ApprovalRecord, *, event: str, code: str,
                decision: str, principal: str | None, args: Any = None) -> Any:
        """One chain entry per transition — **one chain**, not a second log.

        A request in a JSON file and a decision in a different JSON file,
        correlated by an id, is two systems to join during an incident; the whole
        reason Day 26 built the chain is that a security decision nobody can read
        is a rumour. `args_hash` keeps the call provable without the values, and
        `digest` is the one newly permitted field: an incident review must be able
        to prove *which* action was approved without reading the record's values.
        No argument values, no row values, no `email`, and never the `proposal`.
        """
        if self.audit is None:
            return None
        return self.audit.append({
            "run_id": rec.run_id, "principal": principal or "?",
            "tenant": getattr(rec, "tenant", "default") or "default",
            "tool": rec.tool, "effect": rec.effect, "data": rec.data,
            "reversibility": rec.reversibility, "digest": rec.digest,
            "decision": decision, "code": code,
            "args_hash": audit_mod.args_hash(rec.args if args is None else args),
            "latency_ms": 0,
        })

    def _require_actor(self, actor: str | None, what: str) -> str:
        """Precondition 1, for every transition: **no actor, no record.**

        Day 26 could note that the audit log "records a decision, not a person"
        as a limitation, because a missing actor on a permission call is merely
        inconvenient. On a human approval it is fatal, so the footnote becomes a
        hard refusal: a decision nobody can be attributed to is not written, and
        no chain entry is written either — the entry would name nobody.
        """
        if actor is None or not str(actor).strip():
            raise ValueError(
                f"{what} with no actor is not a record: an unattributable decision "
                "cannot be reviewed, and a chain entry would carry no principal. "
                "Nothing was written.")
        return str(actor).strip()

    def _tenant_of(self, actor: str) -> str:
        """The requester's tenant for read-scoping. File mapping wins, absent
        ⇒ `"default"`; never raises — tenancy must not break a store whose
        permissions object predates it."""
        try:
            lookup = getattr(self.permissions, "tenant_of", None)
            if callable(lookup):
                return str(lookup(actor) or "default") or "default"
        except Exception:                                       # noqa: BLE001
            pass
        return "default"

    def _refuse(self, rec: ApprovalRecord, code: str, message: str, *,
                principal: str | None = None, event: str = EVENT_REQUESTED,
                args: Any = None) -> None:
        """Record the refusal, then raise. A refusal *is* a record (the codes
        below), which is what makes 'every refusal appears in the chain with a
        code' a property rather than a hope."""
        self._record(rec, event=event, code=code, decision="deny",
                     principal=principal, args=args)
        raise ApprovalRefusal(code, message, request_id=rec.request_id,
                              state=rec.state)

    def _next_id(self) -> str:
        """Deterministic ids (`apr-1`, `apr-2`, …): no uuid, no clock, so the
        fixtures and the report table are reproducible."""
        return f"{self._prefix}-{len(self._all()) + 1}"

    # ------------------------------------------------------------------ submit
    def submit(self, *, tool: str, args: Any, effect: str, data: str,
               reversibility: str, requested_by: str, targets: Iterable[str] | None = None,
               approver_role: str | None = None, signals: Any = None,
               run_id: str | None = None, proposal: str | None = None,
               tenant: str | None = None,
               ttl_s: float | None = None, request_id: str | None = None,
               supersedes: str | None = None) -> ApprovalRecord:
        """Open a request. **The layer calls this; the model cannot.**

        The body is canonical and model-authored in zero fields: the tool, the
        arguments, the targets, the effect, the data class, the reversibility, the
        signals and the digest are every one of them computed here. `proposal` is
        the single exception, and it is labelled untrusted, excluded from the
        digest and never written to the chain.
        """
        actor = self._require_actor(requested_by, "a request")
        if str(reversibility) not in REVERSIBILITIES:
            raise ValueError(f"unknown reversibility {reversibility!r} "
                             f"(known: {sorted(REVERSIBILITIES)})")
        now = float(self.clock())
        if self.max_per_run and self.max_per_run > 0 and run_id is not None:
            made = sum(1 for r in self._all() if r.run_id == run_id)
            if made >= self.max_per_run:
                raise ApprovalRefusal(
                    REFUSAL_RUN_LIMIT,
                    f"run {run_id!r} already opened {made} request(s); "
                    f"APPROVAL_MAX_PER_RUN={self.max_per_run} bounds a decision "
                    "queue a caller could otherwise flood (rate limiting is Day 28's)",
                    request_id=None, state=None)
        window = self.ttl_s if ttl_s is None else float(ttl_s)
        target_list = (list(targets) if targets is not None
                       else action_targets(args))
        canonical = _canonical_args(args)
        digest = digest_action(tool, canonical, effect=str(effect), data=str(data),
                               reversibility=str(reversibility),
                               targets=target_list)
        signal_set = Signals.from_dict(signals) if signals is not None else Signals.from_dict(build_signals())
        if approver_role is None:
            # The role is read from the **same file** that answers who may call, so a
            # caller cannot open a request against a different gate than the one the
            # choke point enforced — and a tool with no grant simply has no role.
            grant = None
            lookup = getattr(self.permissions, "grant", None)
            if callable(lookup):
                grant = lookup(tool)
            approver_role = getattr(grant, "approver_role", None)
        subject = {"tool": str(tool), "args_shape": audit_mod.args_shape(canonical),
                   "effect": str(effect), "data": str(data),
                   "reversibility": str(reversibility),
                   "targets": [str(t) for t in target_list]}
        rec = ApprovalRecord(
            request_id=str(request_id or self._next_id()), tool=str(tool),
            digest=digest, state=STATE_REQUESTED, subject=subject,
            signals=signal_set.as_dict(), args=canonical,
            targets=[str(t) for t in target_list], effect=str(effect),
            data=str(data), reversibility=str(reversibility),
            approver_role=approver_role, requested_by=actor, requested_at=now,
            tenant=(str(tenant) if tenant else self._tenant_of(actor)),
            expires_at=now + window, ttl_s=window, run_id=run_id,
            proposal=proposal,
            provenance={"source": state_mod.APPROVAL_AUTHOR_SOURCE,
                        "run_id": run_id, "step": STATE_REQUESTED})
        self._write(rec)
        self._record(rec, event=EVENT_REQUESTED, code=EVENT_REQUESTED,
                     decision="pending", principal=actor)
        if supersedes:
            # A newer request for the same target supersedes the older one — the
            # old record is *not* edited into the new one; it is closed as
            # `superseded`, so the chain shows both and neither is overwritten.
            self.supersede(str(supersedes), by_request_id=rec.request_id, by=actor)
        return rec

    # ------------------------------------------------------------------ decide
    def decide(self, request_id: str, *, decided_by: str | None, approve: bool,
               reason: str | None = None) -> ApprovalRecord:
        """Approve or reject — one decision, final, attributed, in the window.

        A decision is *about a digest*: the decider is shown (and approves) the
        canonical action, and the digest is what the executor later re-checks.
        """
        actor = self._require_actor(decided_by, "a decision")            # 1
        rec = self.get(str(request_id))
        if rec is None:                                                  # 2
            raise ApprovalRefusal(REFUSAL_INVALID_STATE,
                                  f"no approval record {request_id!r} exists",
                                  request_id=str(request_id))
        event = EVENT_APPROVED if approve else EVENT_REJECTED
        if rec.state != STATE_REQUESTED:                                 # 3
            code = (REFUSAL_DUPLICATE if rec.state in DECIDED_STATES
                    else REFUSAL_INVALID_STATE)
            self._refuse(rec, code,
                         f"request {rec.request_id} is {rec.state}; a decision is "
                         "final and one per request (a re-request is a new id)",
                         principal=actor, event=event)
        if rec.is_expired(float(self.clock())):                          # 4
            self._refuse(rec, REFUSAL_EXPIRED,
                         f"request {rec.request_id} expired at "
                         f"{rec.expires_at:.3f}; silence is a decision and it is a "
                         "negative one", principal=actor, event=event)
        if rec.approver_role and not self.holds_role(actor, rec.approver_role):  # 5
            self._refuse(rec, REFUSAL_ROLE_NOT_PERMITTED,
                         f"{actor!r} does not hold {rec.approver_role!r} as a grant "
                         f"(tool {rec.tool!r}) — 'who may approve' is answered by "
                         "the same file that answers 'who may call'",
                         principal=actor, event=event)
        if rec.reversibility == REVERSIBILITY_IRREVERSIBLE and actor == rec.requested_by:  # 6
            self._refuse(rec, REFUSAL_SELF_APPROVED,
                         f"{actor!r} requested this irreversible action and cannot "
                         "approve it: an agent that approves its own consequential "
                         "action has not introduced a human at all",
                         principal=actor, event=event)
        if not approve and not (reason or "").strip():                    # 7
            # Not an `ApprovalRefusal`: an unreadable rejection is not reviewable,
            # so it is not a decision this system may write down at all.
            raise ValueError(
                f"a rejection of {rec.request_id} requires a reason — a rejection "
                "nobody can read is not reviewable. Nothing was written.")
        new = rec._replace(state=(STATE_APPROVED if approve else STATE_REJECTED),
                           decided_by=actor, decided_at=float(self.clock()),
                           reason=(reason or None),
                           provenance={"source": state_mod.APPROVAL_AUTHOR_SOURCE,
                                       "run_id": rec.run_id, "step": "decided"})
        self._write(new)
        self._record(new, event=event, code=event,
                     decision=("allow" if approve else "deny"), principal=actor)
        return new

    # ------------------------------------------------------------------- apply
    def apply(self, request_id: str, *, applied_by: str | None = None,
              args: Any = None, targets: Iterable[str] | None = None,
              effect: str | None = None, data: str | None = None,
              reversibility: str | None = None) -> ApprovalRecord:
        """Apply the staged effect — a **separate, separately-granted** act.

        The executor recomputes the digest from the arguments it is *about to
        run* and refuses on mismatch. That single `sha256` closes the
        time-of-check/time-of-use hole that most systems in this genre ship: the
        approver read a rendering, the executor ran different arguments, and both
        halves were individually plausible.

        The staged effect is an append to this repository's own record — never a
        Saleor write. `execution` here means the approved action record advances
        to `applied`; the row count in the business database does not move, and
        the day does not call it a refund.
        """
        actor = self._require_actor(applied_by, "an apply")
        rec = self.get(str(request_id))
        if rec is None:
            raise ApprovalRefusal(REFUSAL_INVALID_STATE,
                                  f"no approval record {request_id!r} exists",
                                  request_id=str(request_id))
        if rec.is_expired(float(self.clock())):                 # before the state:
            self._refuse(rec, REFUSAL_EXPIRED,                  # a late apply is an
                         f"request {rec.request_id} expired at {rec.expires_at:.3f}; "  # expiry
                         "an apply after the window is refused, and the approval it "
                         "approved is left exactly as it was",
                         principal=actor, event=EVENT_APPLIED)
        if rec.state != STATE_APPROVED:
            self._refuse(rec, REFUSAL_INVALID_STATE,
                         f"request {rec.request_id} is {rec.state}, not {STATE_APPROVED}"
                         " — apply runs only from an approve",
                         principal=actor, event=EVENT_APPLIED)
        if not self.holds_role(actor, ROLE_EXECUTOR):
            self._refuse(rec, REFUSAL_ROLE_NOT_PERMITTED,
                         f"{actor!r} does not hold the {ROLE_EXECUTOR!r} grant — the "
                         "requester may not apply, so 'who approved' and 'who ran it' "
                         "are two answerable questions",
                         principal=actor, event=EVENT_APPLIED)
        about_to_run = _canonical_args(rec.args if args is None else args)
        fresh = digest_action(rec.tool, about_to_run,
                              effect=(rec.effect if effect is None else effect),
                              data=(rec.data if data is None else data),
                              reversibility=(rec.reversibility if reversibility is None
                                             else reversibility),
                              targets=(list(rec.targets) if targets is None
                                       else list(targets)))
        if fresh != rec.digest:
            self._refuse(rec, REFUSAL_DIGEST_MISMATCH,
                         f"the arguments about to be run hash to {fresh} but the "
                         f"approval covers {rec.digest}: the approved action is not "
                         "the action in front of the executor",
                         principal=actor, event=EVENT_APPLIED, args=about_to_run)
        new = rec._replace(state=STATE_APPLIED, applied_by=actor,
                           applied_at=float(self.clock()),
                           provenance={"source": state_mod.APPROVAL_AUTHOR_SOURCE,
                                       "run_id": rec.run_id, "step": "applied"})
        self._write(new)
        self._record(new, event=EVENT_APPLIED, code=EVENT_APPLIED,
                     decision="allow", principal=actor, args=about_to_run)
        return new

    # ------------------------------------------------------- WS4 — the outcome
    def record_outcome(self, request_id: str, *, rows_changed: int,
                       recorded_by: str | None = None) -> ApprovalRecord:
        """Attach the measured mutation outcome to an APPLIED record.

        Called once by the apply route after the mutation commits — never by
        the store transitions themselves, so the state machine stays pure and
        the measurement stays with the execution seam that observed it.
        Idempotent: re-recording returns the record unchanged.
        """
        rec = self.get(str(request_id))
        if rec is None:
            raise ApprovalRefusal(REFUSAL_INVALID_STATE,
                                  f"no approval record {request_id!r} exists",
                                  request_id=str(request_id))
        if rec.state != STATE_APPLIED:
            raise ApprovalRefusal(REFUSAL_INVALID_STATE,
                                  f"request {rec.request_id} is {rec.state}, not "
                                  f"{STATE_APPLIED} — outcomes attach to applied "
                                  "records only",
                                  request_id=rec.request_id, state=rec.state)
        if rec.applied_rows_changed == int(rows_changed or 0):
            return rec
        new = rec._replace(applied_rows_changed=int(rows_changed or 0))
        self._write(new)
        return new

    # --------------------------------------------------------------- compensate
    def compensate(self, request_id: str, *, compensated_by: str | None = None,
                   reason: str | None = None) -> ApprovalRecord:
        """Undo a reversible action — **by appending**, never by editing.

        The compensating record is a *new* record with a `compensates` pointer and
        the original is byte-for-byte untouched. An irreversible action has no
        undo, and the refusal is loud on purpose: a system that will "undo" an
        irreversible action will eventually tell a customer their refund was
        reversed, and that class of bug is discovered by customers rather than by
        fixtures.
        """
        actor = self._require_actor(compensated_by, "a compensation")
        rec = self.get(str(request_id))
        if rec is None:
            raise ApprovalRefusal(REFUSAL_INVALID_STATE,
                                  f"no approval record {request_id!r} exists",
                                  request_id=str(request_id))
        if rec.state != STATE_APPLIED:
            self._refuse(rec, REFUSAL_INVALID_STATE,
                         f"request {rec.request_id} is {rec.state}, not "
                         f"{STATE_APPLIED}: only an applied action can be compensated",
                         principal=actor, event=EVENT_COMPENSATED)
        if rec.reversibility == REVERSIBILITY_IRREVERSIBLE:
            self._refuse(rec, REFUSAL_REVERSAL_IMPOSSIBLE,
                         f"{rec.tool!r} is declared irreversible — nothing this "
                         "system does afterwards restores the prior world, and the "
                         "correct answer is to refuse loudly now",
                         principal=actor, event=EVENT_COMPENSATED)
        existing = sum(1 for r in self._all() if r.compensates == rec.request_id)
        new = rec._replace(
            request_id=f"{rec.request_id}-c{existing + 1}",
            state=STATE_COMPENSATED, compensates=rec.request_id,
            applied_by=actor, applied_at=float(self.clock()),
            reason=reason or (f"compensation for {rec.request_id}: "
                              f"{rec.reversibility} action undone by appending"),
            decided_by=rec.decided_by, decided_at=rec.decided_at,
            provenance={"source": state_mod.APPROVAL_AUTHOR_SOURCE,
                        "run_id": rec.run_id, "step": "compensated"})
        self._write(new)
        self._record(new, event=EVENT_COMPENSATED, code=EVENT_COMPENSATED,
                     decision="allow", principal=actor)
        return new

    # ----------------------------------------------------------------- expiry
    def expire(self, request_id: str, *, expired_by: str | None = None) -> ApprovalRecord:
        """Close one request as `expired` — **only when the clock says so**.

        Expiring a live approval would be a decision nobody made, so a live
        request ends by a decision or by supersession and never by a pass with a
        hand on it. The expiry itself is authored by the clock, and the chain says
        so in the `principal` column rather than inventing a person.
        """
        rec = self.get(str(request_id))
        if rec is None:
            raise ApprovalRefusal(REFUSAL_INVALID_STATE,
                                  f"no approval record {request_id!r} exists",
                                  request_id=str(request_id))
        if rec.state not in (STATE_REQUESTED, STATE_APPROVED):
            self._refuse(rec, REFUSAL_INVALID_STATE,
                         f"request {rec.request_id} is {rec.state}: expiry runs only "
                         f"from {STATE_REQUESTED}/{STATE_APPROVED}",
                         principal=expired_by or EXPIRY_PRINCIPAL,
                         event=EVENT_EXPIRED)
        if not rec.is_expired(float(self.clock())):
            # The due-ness check is the whole reason this method takes no "reason"
            # argument: expiring a *live* approval would be a decision nobody made, so
            # a live request ends by a decision or by supersession and never by a pass
            # with a hand on it. The clock is the only thing allowed to end a request
            # without an actor, and it is checked here rather than trusted.
            self._refuse(rec, REFUSAL_INVALID_STATE,
                         f"request {rec.request_id} is not due (expires_at "
                         f"{rec.expires_at:.3f} > now) — a live approval is ended by a "
                         "decision or by supersession, never by a request to expire it",
                         principal=expired_by or EXPIRY_PRINCIPAL,
                         event=EVENT_EXPIRED)
        return self._expire(rec, expired_by=expired_by)

    def _expire(self, rec: ApprovalRecord, *, expired_by: str | None = None
                ) -> ApprovalRecord:
        new = rec._replace(state=STATE_EXPIRED,
                           reason=rec.reason or "expired: no decision before the window closed",
                           provenance={"source": state_mod.APPROVAL_AUTHOR_SOURCE,
                                       "run_id": rec.run_id, "step": "expired"})
        self._write(new)
        self._record(new, event=EVENT_EXPIRED, code=REFUSAL_EXPIRED,
                     decision="deny", principal=expired_by or EXPIRY_PRINCIPAL)
        return new

    def expire_due(self, now: float | None = None) -> list[ApprovalRecord]:
        """The store pass: every due `requested`/`approved` record becomes `expired`.

        A pass never rewrites a decided record (an `applied` record is history),
        so this is safe to run at any time and idempotent: a second call finds
        nothing to do.
        """
        at = float(self.clock() if now is None else now)
        out: list[ApprovalRecord] = []
        for rec in self._all():
            if rec.state in (STATE_REQUESTED, STATE_APPROVED) and rec.is_expired(at):
                out.append(self._expire(rec))
        return out

    # ------------------------------------------------------------------ supersede
    def supersede(self, request_id: str, *, by_request_id: str | None = None,
                  by: str | None = None) -> ApprovalRecord:
        """Close a *request* because a newer request for the same target exists.

        The request is immutable once submitted: if the arguments need to change,
        that is not an edited request, it is a **different action** — a new
        request — and the old one is closed here. An approval is of an action, and
        an action whose parameters can be edited after a human said yes was never
        approved.
        """
        rec = self.get(str(request_id))
        if rec is None:
            raise ApprovalRefusal(REFUSAL_INVALID_STATE,
                                  f"no approval record {request_id!r} exists",
                                  request_id=str(request_id))
        if rec.state != STATE_REQUESTED:
            self._refuse(rec, REFUSAL_INVALID_STATE,
                         f"request {rec.request_id} is {rec.state}: only an "
                         "undecided request can be superseded",
                         principal=by, event=EVENT_REQUESTED)
        new = rec._replace(
            state=STATE_SUPERSEDED,
            reason=f"superseded by {by_request_id or 'a newer request'} for the same "
                   "target — the old request is closed, never edited",
            provenance={"source": state_mod.APPROVAL_AUTHOR_SOURCE,
                        "run_id": rec.run_id, "step": "superseded"})
        self._write(new)
        self._record(new, event=EVENT_REQUESTED, code=STATE_SUPERSEDED,
                     decision="deny", principal=by or rec.requested_by)
        return new


# ============================================================ the rendering

def render_subject(rec: ApprovalRecord, *, values: bool = False) -> str:
    """The canonical rendering — **byte-stable for a given digest**, digest first.

    Two reviewers must see the same bytes, and the digest is printed above any
    prose so the bytes are read before the explanation. `values=True` renders the
    argument values (the approver is the one reader entitled to them); the default
    renders their shapes only, which is the read-only surface's contract.

    The honest limit belongs beside the function: a human is a poor verifier of a
    canonical rendering, and nothing in this repository measures how poor. The
    mechanism can guarantee that what the human saw is what the executor runs. It
    cannot guarantee that the human understood it.
    """
    lines = [f"digest: {rec.digest}", f"request: {rec.request_id}",
             f"tool: {rec.tool}",
             f"effect: {rec.effect} · data: {rec.data} · "
             f"reversibility: {rec.reversibility}",
             f"targets: {', '.join(rec.targets) or '—'}",
             f"state: {rec.state} · expires_at: {rec.expires_at:.3f} "
             f"(ttl_s={rec.ttl_s:g})",
             f"requested_by: {rec.requested_by} · approver_role: "
             f"{rec.approver_role or '—'}"]
    args = rec.args if values else audit_mod.args_shape(rec.args)
    lines.append("arguments" + ("" if values else " (shape only, values masked):"))
    for key in sorted(args):
        lines.append(f"  {key}: {json.dumps(args[key], ensure_ascii=False, default=str)}")
    signals = (rec.signals or {}).get("signals") or {}
    lines.append(f"signals: {', '.join(sorted(signals)) or '—'} "
                 f"(scrutiny: {rec.scrutiny()})")
    for key in sorted(signals):
        entry = signals[key] or {}
        if entry.get("not_a_signal"):
            lines.append(f"  NOT A SIGNAL — {key}: {entry.get('reason') or NOT_A_SIGNAL_REASON}")
    if rec.scrutiny() == SCRUTINY_ELEVATED:
        lines.append(f"  scrutiny: {(rec.signals or {}).get('scrutiny_reason')}")
    for label, who, at in (("decided_by", rec.decided_by, rec.decided_at),
                           ("applied_by", rec.applied_by, rec.applied_at)):
        if who:
            lines.append(f"{label}: {who} at {float(at or 0.0):.3f}")
    if rec.compensates:
        lines.append(f"compensates: {rec.compensates}")
    if rec.reason:
        lines.append(f"reason: {rec.reason}")
    if rec.proposal:
        lines.append(f"proposal (model-authored, UNTRUSTED, not hashed, never audited): "
                     f"{rec.proposal}")
    return "\n".join(lines)


def render_table(records: Iterable[ApprovalRecord]) -> str:
    """One row per record for the report: digest, state, roles, refusal code.

    Metadata-safe by construction — the table carries the digest, the tool, the
    state, the reversibility, the signal *keys* and the refusal code, and never an
    argument value.
    """
    rows = list(records)
    if not rows:
        return "(no approval records)"
    widths = (12, 24, 8, 13, 11, 11, 26)
    head = ("request_id", "tool", "effect", "reversibility", "signals",
            "state", "digest")
    lines = ["".join(str(h)[:w].ljust(w + 1) for h, w in zip(head, widths)).rstrip(),
             "-" * (sum(widths) + len(widths))]
    for rec in rows:
        cells = (rec.request_id, rec.tool, rec.effect or "—", rec.reversibility,
                 str(len(rec.signal_keys())), rec.state, rec.digest)
        lines.append("".join(str(c)[:w].ljust(w + 1)
                             for c, w in zip(cells, widths)).rstrip())
    return "\n".join(lines)


# ============================================================ the factory

@contextmanager
def approvals(on: bool) -> Iterator[None]:
    """Scoped on/off switch (the H9 control), mirroring Day 26's `governance`.

    Inside `approvals(False)` the desk does not exist: no request is opened, no run
    stops at `awaiting_approval`, and no chain entry is written. That is the whole
    content of "the layer is passive" — and the reason the switch is scoped rather
    than global is that the *reading* surface (the CLI, the two GET routes) must
    keep working while the gate is off, or every open request is stranded.
    """
    prev = os.environ.get(APPROVALS_ENV)
    os.environ[APPROVALS_ENV] = "1" if on else "none"
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop(APPROVALS_ENV, None)
        else:
            os.environ[APPROVALS_ENV] = prev


def state_store_for_approvals(**kw: Any) -> state_mod.StateStore:
    """The approvals shelf: Day 17's store, a different file.

    The store is **retention**; the record's `expires_at` is the **decision
    window**. One `StateStore` at a separate path, the same TTL/cap/principal
    knobs, and no second persistence mechanism to drift.
    """
    path = kw.pop("path", None)
    return state_mod.StateStore(
        path if path is not None else approvals_path_from_env(),
        default_ttl=state_mod.state_default_ttl_from_env(),
        max_records=state_mod.state_max_records_from_env(),
        principal=state_mod.state_principal_from_env(), **kw)


def approval_store(store: Any = None, *, audit: Any = None, permissions: Any = None,
                   clock=time.time, ttl_s: float | None = None,
                   max_per_run: int | None = None) -> "ApprovalStore | None":
    """The gate's factory — `None` is the H9 off-switch.

    Returns `None` when `APPROVALS=none`, in which case `ToolRegistry` has no gate
    at all: `confirm: human` is then a *recorded* declaration that nothing acts
    on, which is exactly the passive-layer control H9 asserts.
    """
    if not enabled():
        return None
    st = store if store is not None else state_store_for_approvals()
    return ApprovalStore(st, clock=clock,
                         ttl_s=approval_ttl_from_env() if ttl_s is None else ttl_s,
                         max_per_run=(approval_max_per_run_from_env()
                                      if max_per_run is None else max_per_run),
                         permissions=permissions, audit=audit)


def approval_view(rec: Any, *, reveal: bool = False) -> dict[str, Any]:
    """The read-only surface's record view — values masked unless `reveal`.

    `reveal=1` shows argument values, and the honest note rides with it in the
    server's docstring: this surface is unauthenticated, so the flag is an
    argument for Day 29's authentication work rather than a feature.
    """
    if isinstance(rec, dict):
        rec = ApprovalRecord.from_dict(rec)
    out = rec.as_dict() if reveal else rec.values_masked()
    out["signals"] = rec.signal_keys()
    out["scrutiny"] = rec.scrutiny()
    return out


@dataclass(frozen=True)
class Lifecycle:
    """The seven-row lifecycle narrative, as a value — the day's headline artifact.

    One record, seven codes, and at the end the number that matters most: the
    count of state changes on the Saleor database is **zero**. A human approved a
    consequential action, the approval was recorded, applied, expired and refused
    — and the business data never moved.
    """

    rows: tuple[dict[str, Any], ...] = ()
    saleor_rows_changed: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"rows": [dict(r) for r in self.rows],
                "saleor_rows_changed": int(self.saleor_rows_changed)}


def lifecycle_row(t: int, step: str, state: str, code: str, **extra: Any
                  ) -> dict[str, Any]:
    return {"t": int(t), "step": step, "state": state, "code": code, **extra}
