"""Day 26 §8 — the audit log: append-only, hash-chained, metadata-safe.

The day's evidence layer. The design question is never "how much do we log" — it
is **"what may a security record contain"**, and the answer is decided by
Day 25's rule, so the entry is narrow on purpose. A reviewer can prove the call
happened, prove which arguments were used *without storing them*, and reconstruct
the decision — and cannot read a single row of customer data out of the file.

Permitted: identifiers (`seq`, `run_id`, `trace_id`), the principal string, the
tool name, the declared `effect` and `data` class, the decision and its `code`,
`args_hash` plus `args_shape` (key names and value *lengths*), `result_shape`
(row count, column names, `truncated`, `redacted`, and never a value), and
`latency_ms`. Forbidden, as rules rather than habits: **no SQL text, no row
values, no `email`, no document text, no system prompt, no policy or permission
file contents, no API key, and no `ToolResult.error` string that might echo a
payload back out of a tool.**

The chain is ``hash = sha256(canonical_json(entry − hash))`` with `prev_hash`
carried *into* the hashed body, and canonical meaning `sort_keys`, fixed
separators, UTF-8, and **no floats** — so the hash is reproducible by anyone with
the file and a shell. Three attacks are caught and one is not:

    a value edited in place     → caught (the entry's hash no longer matches)
    an interior entry deleted   → caught (every later prev_hash mismatches)
    two entries reordered       → caught (same)
    the tail truncated          → NOT caught (nothing inside the process can
                                  notice a file that simply ends)

So the claim this module may make is **tamper-evident for edits, reorders, and
interior deletions** — never "tamper-proof". `verify()` therefore reports
`entries` (how many lines survived) *separately* from `chain_ok` (whether the
chain is intact), and the external anchor that makes truncation detectable is
Day 29's. The writer is append-only in the literal sense: append mode, one
`write()` per line, no update and no delete method on the class, and **no
rotation** — silent truncation is the one attack a self-verifying file cannot
see, so rotation is an operator's storage decision, not a library's housekeeping.

Everything here is `MECH`: canonical string work and one `sha256` per entry.
Zero chats, zero embeds, zero SQL.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# The one config knob. Default lives beside the other runtime artifacts.
AUDIT_PATH_ENV = "AUDIT_LOG_PATH"
DEFAULT_AUDIT_PATH = Path(__file__).resolve().parent.parent / "data" / "audit.jsonl"

# The permitted key set. A dict with a key outside this set is refused by
# `AuditLog.append`, so a future caller cannot smuggle a value in by adding a
# field — the record shape is a contract, not a suggestion.
#
# Day 27 adds exactly one **permitted** field and one **forbidden** one, and both
# are stated as rules rather than habits:
#
#   * `digest` (and its companion `reversibility`) is permitted because an incident
#     review must be able to prove *which* action was approved **without reading the
#     record's values** — the digest is a hash of the action, so it answers "was the
#     thing that ran the thing that was approved?" and discloses nothing.
#   * `proposal` is forbidden, because it is the only model-authored text anywhere
#     in the approval pipeline, and Day 25's prohibition on laundering untrusted
#     text into a trusted artifact applies to an audit chain most directly of all.
PERMITTED_KEYS = frozenset({
    "seq", "ts", "run_id", "trace_id", "principal", "tenant", "tool", "effect",
    "data", "decision", "code", "args_hash", "args_shape", "result_shape",
    "latency_ms", "redacted", "prev_hash", "hash", "digest", "reversibility",
})

# Refused **by name**, so the refusal says which rule was broken rather than
# "forbidden key". A model-writable rationale reachable from the chain would turn
# a steering attempt into a trusted record across one day's diff.
FORBIDDEN_KEYS = frozenset({"proposal"})

# ============================================================ Day 27 — the events
# The six approval event codes, defined once — here, because they are the chain's
# vocabulary (`approval.py` aliases them under the names the design uses). One
# chain, one timeline: a request in a JSON file and a decision in a different JSON
# file, correlated by an id, is two systems to join during an incident, and the
# whole reason Day 26 built the chain is that a security decision nobody can read is
# a rumour. Each event carries `principal` (the *acting* principal), `tool`,
# `effect`, `reversibility`, `digest`, `code` — and never an argument value, a row
# value, an `email`, or the `proposal`.
EVENT_APPROVAL_REQUESTED = "approval_requested"
EVENT_APPROVAL_APPROVED = "approval_approved"
EVENT_APPROVAL_REJECTED = "approval_rejected"
EVENT_APPROVAL_EXPIRED = "approval_expired"
EVENT_APPROVAL_APPLIED = "approval_applied"
EVENT_APPROVAL_COMPENSATED = "approval_compensated"
APPROVAL_EVENTS: tuple[str, ...] = (
    EVENT_APPROVAL_REQUESTED, EVENT_APPROVAL_APPROVED, EVENT_APPROVAL_REJECTED,
    EVENT_APPROVAL_EXPIRED, EVENT_APPROVAL_APPLIED, EVENT_APPROVAL_COMPENSATED)


def audit_path_from_env() -> Path:
    configured = os.getenv(AUDIT_PATH_ENV, "").strip()
    return Path(configured) if configured else DEFAULT_AUDIT_PATH


# ============================================================ canonical encoding

def _assert_no_floats(obj: Any, path: str = "$") -> None:
    if isinstance(obj, float):
        raise ValueError(f"audit entry contains a float at {path} — the canonical "
                         "encoding forbids floats so the hash is reproducible")
    if isinstance(obj, dict):
        for k, v in obj.items():
            _assert_no_floats(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _assert_no_floats(v, f"{path}[{i}]")


def canonical_json(obj: Any) -> str:
    """Deterministic bytes-canonical JSON: sorted keys, fixed separators, UTF-8.

    Floats are refused outright rather than rounded, so a value that would encode
    differently under a different platform's float repr can never reach the hash.
    """
    _assert_no_floats(obj)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def entry_hash(body: dict[str, Any]) -> str:
    """`sha256` over the canonical body (which *includes* `prev_hash`)."""
    return "sha256:" + hashlib.sha256(
        canonical_json(body).encode("utf-8")).hexdigest()


def _shape_of(value: Any) -> dict[str, Any]:
    """Key names and value *lengths* — never the value itself."""
    if isinstance(value, dict):
        return {str(k): {"chars": len(str(v))} if isinstance(v, str)
                else {"chars": len(canonical_json(v))} for k, v in value.items()}
    return {"chars": len(str(value))}


def args_hash(args: Any) -> str:
    """A hash of the canonical args — the call is provable, the args are not kept."""
    if isinstance(args, str):
        try:
            args = json.loads(args or "{}")
        except (json.JSONDecodeError, TypeError):
            args = {"_raw": args}
    return "sha256:" + hashlib.sha256(
        canonical_json(args if args is not None else {}).encode("utf-8")).hexdigest()


def args_shape(args: Any) -> dict[str, Any]:
    if isinstance(args, str):
        try:
            args = json.loads(args or "{}")
        except (json.JSONDecodeError, TypeError):
            return {"_raw": {"chars": len(args)}}
    return _shape_of(args if isinstance(args, dict) else {})


# ============================================================ the entry

@dataclass
class AuditEntry:
    """One decision, recorded. Allows included — a log of only denials answers
    "what failed" and never "what is this principal doing".

    Day 27 adds `digest`/`reversibility` (both hashed into the body): the approval
    events are decisions *about a digest*, and a chain that omitted it could not
    answer the only question an incident review actually asks of an approval.
    """

    seq: int
    ts: int
    principal: str
    tool: str
    effect: str | None
    data: str | None
    decision: str
    code: str | None
    args_hash: str
    args_shape: dict[str, Any] = field(default_factory=dict)
    result_shape: dict[str, Any] | None = None
    latency_ms: int = 0
    run_id: str | None = None
    trace_id: str | None = None
    redacted: list[str] = field(default_factory=list)
    digest: str | None = None
    reversibility: str | None = None
    # WS1 — the acting principal's tenant (read-scoping, not a secret).
    tenant: str = "default"
    prev_hash: str = ""
    hash: str = ""

    def body(self) -> dict[str, Any]:
        """The hashed body — every field except `hash` itself."""
        out: dict[str, Any] = {
            "seq": int(self.seq), "ts": int(self.ts), "run_id": self.run_id,
            "trace_id": self.trace_id, "principal": self.principal,
            "tool": self.tool, "effect": self.effect, "data": self.data,
            "decision": self.decision, "code": self.code,
            "args_hash": self.args_hash, "args_shape": dict(self.args_shape),
            "result_shape": self.result_shape, "latency_ms": int(self.latency_ms),
            "redacted": list(self.redacted), "prev_hash": self.prev_hash,
            "digest": self.digest, "reversibility": self.reversibility,
            "tenant": self.tenant,
        }
        return out

    def as_dict(self) -> dict[str, Any]:
        return {**self.body(), "hash": self.hash}


# ============================================================ the log

class AuditLog:
    """Append-only, one `write()` per line, hash-chained, no update/delete/rotate."""

    def __init__(self, path: str | os.PathLike | None = None, *,
                 clock=time.time) -> None:
        self.path = Path(path) if path is not None else audit_path_from_env()
        self.clock = clock
        self._entries: list[dict[str, Any]] = []
        self._last_hash: str = ""
        self._load()

    # ------------------------------------------------------------------ io
    def _load(self) -> None:
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            self._entries.append(entry)
            self._last_hash = entry.get("hash", self._last_hash)

    def _write_line(self, entry: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = canonical_json(entry) + "\n"
        # Append mode + a single write per line: a crash truncates at a line
        # rather than mid-record, and no method on this class can rewrite a line.
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line)

    # ------------------------------------------------------------------ append
    def append(self, fields: dict[str, Any]) -> dict[str, Any]:
        """Assign `seq`/`prev_hash`/`hash`, write one line, return the entry.

        `ts` is stamped *here*, by the writer — not by the decision — so the
        decision stays a pure function of `(principal, tool, args, perms)` and
        C1–C9 stay deterministic.
        """
        stamp = int(fields.pop("ts", None) or int(self.clock()))
        latency = fields.get("latency_ms")
        fields["latency_ms"] = 0 if latency is None else int(round(float(latency)))
        forbidden = [k for k in fields if k in FORBIDDEN_KEYS]
        if forbidden:
            raise ValueError(
                f"audit entry carries forbidden key(s) {forbidden} — a field the "
                "model can author is not a permitted entry field, and laundering "
                "untrusted text into a trusted artifact is the one thing the chain "
                "must never do")
        unknown = [k for k in fields if k not in PERMITTED_KEYS]
        if unknown:
            raise ValueError(f"audit entry carries forbidden key(s) {unknown} "
                             f"(a value is not a permitted entry field)")
        entry = AuditEntry(
            seq=len(self._entries) + 1, ts=stamp,
            run_id=fields.get("run_id"), trace_id=fields.get("trace_id"),
            principal=str(fields.get("principal") or "?"),
            tool=str(fields.get("tool") or "?"),
            effect=fields.get("effect"), data=fields.get("data"),
            decision=str(fields.get("decision") or "?"),
            code=fields.get("code"), args_hash=str(fields.get("args_hash") or ""),
            args_shape=fields.get("args_shape") or {},
            result_shape=fields.get("result_shape"),
            latency_ms=int(fields.get("latency_ms") or 0),
            redacted=list(fields.get("redacted") or []),
            digest=fields.get("digest"), reversibility=fields.get("reversibility"),
            tenant=str(fields.get("tenant") or "default"),
            prev_hash=self._last_hash)
        record = entry.as_dict()
        record["hash"] = entry_hash(entry.body())
        self._write_line(record)
        self._entries.append(record)
        self._last_hash = record["hash"]
        return record

    # ------------------------------------------------------------------ read
    def entries(self, *, principal: str | None = None, tool: str | None = None,
                decision: str | None = None,
                tenant: str | None = None) -> list[dict[str, Any]]:
        """The bounded inspection surface: filter by principal / tool / decision /
        tenant. Entries written before WS1 carry `tenant: "default"`."""
        out = []
        for e in self._entries:
            if principal is not None and e.get("principal") != principal:
                continue
            if tenant is not None and e.get("tenant", "default") != tenant:
                continue
            if tool is not None and e.get("tool") != tool:
                continue
            if decision is not None and e.get("decision") != decision:
                continue
            out.append(e)
        return out

    def __len__(self) -> int:
        return len(self._entries)

    # ------------------------------------------------------------------ verify
    def verify(self) -> dict[str, Any]:
        """Walk the file and separate `entries` from `chain_ok`.

        Reading the *file* (not the in-memory list) is what makes an external edit
        visible. `first_broken_seq` names the first entry whose recomputed hash or
        whose `prev_hash` linkage does not hold — that is how an edit, a reorder,
        and an interior deletion are told apart from a clean truncation.
        """
        rows: list[dict[str, Any]] = []
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    rows.append({"_parse_error": True})
        chain_ok = True
        first_broken: int | None = None
        prev = ""
        for row in rows:
            seq = row.get("seq")
            stored = row.get("hash")
            body = {k: v for k, v in row.items() if k != "hash"}
            recomputed = entry_hash(body) if not row.get("_parse_error") else ""
            linkage_ok = row.get("prev_hash") == prev
            hash_ok = recomputed == stored and not row.get("_parse_error")
            if not (linkage_ok and hash_ok):
                chain_ok = False
                if first_broken is None and isinstance(seq, int):
                    first_broken = seq
            prev = stored if isinstance(stored, str) else ""
        return {"ok": chain_ok, "entries": len(rows), "chain_ok": chain_ok,
                "first_broken_seq": first_broken}


# ------------------------------------------------- WS9 — external seal (anchor)
# `verify()` is blind to tail truncation (a file that simply ends looks clean).
# The seal closes that gap: `seal()` writes `{seq, head_hash}` — the head of
# the chain at sealing time — to a *second* location (`AUDIT_SEAL_PATH`,
# a KMS-backed object in staging, a second disk offline). `verify_seal()`
# replays the log past the sealed seq: a truncated tail (fewer entries than
# sealed) or a rewritten head (different hash at the sealed seq) is refused
# by name. Rotation: re-seal after every verified backup (the DR drill does).
SEAL_PATH_ENV = "AUDIT_SEAL_PATH"


def seal_path_from_env() -> "Path":
    import os as _os
    from pathlib import Path as _Path
    raw = _os.getenv(SEAL_PATH_ENV, "").strip()
    if raw:
        return _Path(raw)
    return audit_path_from_env().with_suffix(".seal")


def seal_audit(log: "AuditLog | None" = None, *, path: "Path | None" = None) -> dict:
    """Seal the current chain head. Returns `{seq, head_hash, sealed_at}`."""
    import time as _time
    target = log if log is not None else default_audit_log()
    rows = target.entries()
    head = rows[-1] if rows else None
    seal = {"seq": head.get("seq", 0) if head else 0,
            "head_hash": head.get("hash", "") if head else "",
            "entries": len(rows), "sealed_at": _time.time()}
    dest = path or seal_path_from_env()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(seal, indent=2), encoding="utf-8")
    return seal


def verify_seal(log: "AuditLog | None" = None, *, path: "Path | None" = None) -> dict:
    """Check the log against its seal: truncation and head-rewrite detected."""
    import json as _json
    dest = path or seal_path_from_env()
    if not dest.exists():
        return {"sealed": False, "ok": True, "note": "no seal yet — seal after backup"}
    try:
        seal = _json.loads(dest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"sealed": True, "ok": False, "reason": "seal_unreadable"}
    target = log if log is not None else default_audit_log()
    verdict = target.verify()
    rows = target.entries()
    if len(rows) < int(seal.get("entries", 0)):
        return {"sealed": True, "ok": False, "reason": "tail_truncated",
                "sealed_entries": seal.get("entries"), "log_entries": len(rows),
                "chain_ok": verdict["chain_ok"]}
    at = next((r for r in rows if r.get("seq") == seal.get("seq")), None)
    if seal.get("seq") and (at is None or at.get("hash") != seal.get("head_hash")):
        return {"sealed": True, "ok": False, "reason": "head_rewritten",
                "chain_ok": verdict["chain_ok"]}
    return {"sealed": True, "ok": bool(verdict["chain_ok"]),
            "chain_ok": verdict["chain_ok"], "entries": len(rows)}


class NullAuditLog:
    """The off-switch and the tests that do not care: nothing is written."""

    path: Path | None = None

    def append(self, fields: dict[str, Any]) -> None:       # noqa: D401
        return None

    def entries(self, **_kw: Any) -> list[dict[str, Any]]:
        return []

    def verify(self) -> dict[str, Any]:
        return {"ok": True, "entries": 0, "chain_ok": True, "first_broken_seq": None}

    def __len__(self) -> int:
        return 0


# The process-wide default log for the server's `/audit` surface. Created lazily
# so importing `audit` never touches disk.
_DEFAULT: AuditLog | NullAuditLog | None = None


def default_audit_log() -> AuditLog:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = AuditLog()
    return _DEFAULT  # type: ignore[return-value]


def reset_default_log() -> None:
    """Drop the cached default (tests point `AUDIT_LOG_PATH` at a temp file)."""
    global _DEFAULT
    _DEFAULT = None
