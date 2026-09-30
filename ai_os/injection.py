"""Day 25 §6/§7 — the injection firewall's detection layer: bounded, offline,
deterministic signals that **report and never decide**.

Day 24 taught one request's identity to survive every boundary it crossed. Day 25
turns the camera around: **attack the system.** The finding is not "prompts can be
jailbroken" — it is that the model is a *confused deputy*, so the defense has to
live somewhere the model cannot talk its way past. This module is the **least**
important rung of that ladder (L2), and it is written to stay that way:

- **It reports; the authority decides.** `scan()` returns a list of signals. It
  takes no action, holds no policy, and cannot refuse anything. A function called
  `scan` that *could* block would eventually be called by something that treats a
  keyword hit as authority — so it is not given the ability to.
- **Bounded on purpose.** A detector that must read an unbounded observation is a
  denial-of-service vector and a log-flood; an attacker who makes the system log
  10,000 signals per turn has already won something. So there is a **scan cap**
  (`DEFAULT_SCAN_CHARS`) and a **max-signals cap** (`DEFAULT_MAX_SIGNALS`).
- **Metadata-safe, always.** A signal carries a **class**, a **source**, a **field
  path**, a **hashed excerpt**, **offsets**, and a **severity** — never the payload
  verbatim. The metadata-safe default from Days 23–24 does not relax because the
  input is hostile.
- **Offline and dependency-free.** Pure string work over bytes the request already
  had: **0 chats, 0 embeds, 0 SQL**. That is what makes I9's zero-delta control
  possible at all.

The vocabulary is the day's: `TaintSource` (`user`, `document`, `tool_observation`,
`record_field`, `state_recall`, `header`, `judge_evidence`), `InjectionSignal`,
`TaintedSpan`, and `scan(text, source, path)`.

Signal families (§5 decision #3):

```text
  override_phrase        instruction-shaped override ("ignore all previous ...")
  role_marker            an impersonated system/developer marker
  exfil_shape            a disclosure ask for a *privileged artifact*
                         (the prompt, the policy, the schema card, the config)
  tool_name_injection    tool-naming by injected text, or covert-action steering
  forged_control         text impersonating the runtime's control state
                         ("policy check passed", a fabricated rejection_class)
  encoding_obfuscation   long base64/hex runs, leetspeak, homoglyph non-ASCII
  instruction_in_field   instruction-shaped content in a field that is supposed to
                         hold a *name* (a product name, a customer first name)
```

Two decisions make the module specific to this repo. **A signal is not a verdict:**
`implies_privileged_action()` is the small, separate predicate the *agent* calls to
decide whether a user-turn signal reaches a privileged action (a write, a PII read,
a tool invention, a forged control marker, a disclosure of a privileged artifact).
A persona-only or answer-extraction-only signal is recorded and does not stop a
turn. And **instruction-shaped text is this corpus's native language** — Day 9's
code RAG retrieves documentation *about* prompts, tools, and SQL — so a hit is a
signal with a measured false-positive rate against a pinned legitimate set, never a
silent block. Day 7's rule, unchanged: over-blocking is measured, never assumed away.
"""
from __future__ import annotations

import hashlib
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, Sequence

# ============================================================= taint sources
# WHERE an untrusted span entered. This is the run's `taint` field, and it is the
# same vocabulary the evidence framing and the trace use, so "which document,
# which field, which channel" is one lookup.
TAINT_USER = "user"
TAINT_DOCUMENT = "document"
TAINT_TOOL_OBSERVATION = "tool_observation"
TAINT_RECORD_FIELD = "record_field"
TAINT_STATE_RECALL = "state_recall"
TAINT_HEADER = "header"
TAINT_JUDGE_EVIDENCE = "judge_evidence"

TAINT_SOURCES = frozenset({TAINT_USER, TAINT_DOCUMENT, TAINT_TOOL_OBSERVATION,
                           TAINT_RECORD_FIELD, TAINT_STATE_RECALL, TAINT_HEADER,
                           TAINT_JUDGE_EVIDENCE})

# ============================================================= signal classes
CLASS_OVERRIDE = "override_phrase"
CLASS_ROLE_MARKER = "role_marker"
CLASS_EXFIL = "exfil_shape"
CLASS_TOOL_INJECTION = "tool_name_injection"
CLASS_FORGED_CONTROL = "forged_control"
CLASS_OBFUSCATION = "encoding_obfuscation"
CLASS_FIELD_INSTRUCTION = "instruction_in_field"

SIGNAL_CLASSES = frozenset({CLASS_OVERRIDE, CLASS_ROLE_MARKER, CLASS_EXFIL,
                            CLASS_TOOL_INJECTION, CLASS_FORGED_CONTROL,
                            CLASS_OBFUSCATION, CLASS_FIELD_INSTRUCTION})

# Severities are the ranking the *reader* uses. They are not a decision.
SEV_LOW = "low"
SEV_MEDIUM = "medium"
SEV_HIGH = "high"
SEVERITIES = (SEV_LOW, SEV_MEDIUM, SEV_HIGH)

_CLASS_SEVERITY: dict[str, str] = {
    CLASS_OBFUSCATION: SEV_LOW,
    CLASS_OVERRIDE: SEV_MEDIUM,
    CLASS_ROLE_MARKER: SEV_MEDIUM,
    CLASS_EXFIL: SEV_MEDIUM,
    CLASS_TOOL_INJECTION: SEV_HIGH,
    CLASS_FORGED_CONTROL: SEV_HIGH,
    CLASS_FIELD_INSTRUCTION: SEV_HIGH,
}

# The classes whose *presence alone* implies a privileged action: a forged control
# marker claims a permission check happened; an injected tool name asks for a
# capability; a disclosure ask names a privileged artifact.
PRIVILEGED_CLASSES = frozenset({CLASS_FORGED_CONTROL, CLASS_TOOL_INJECTION,
                                CLASS_EXFIL})

# The instruction-shaped families: their presence in a *name* field is the
# `instruction_in_field` signal (I4).
_INSTRUCTION_CLASSES = frozenset({CLASS_OVERRIDE, CLASS_ROLE_MARKER, CLASS_EXFIL,
                                  CLASS_TOOL_INJECTION, CLASS_FORGED_CONTROL})

# ============================================================= the caps
# Both bounds are security properties, not performance knobs.
DEFAULT_SCAN_CHARS = 4096        # read at most this many chars of one span
DEFAULT_MAX_SIGNALS = 32         # report at most this many signals per scan
RING_SIZE = 256                  # the local `/attacks` ring's bound

_ENABLED_ENV = "INJECTION_FIREWALL"


# ============================================================= the switch
_ENABLED = os.getenv(_ENABLED_ENV, "1").strip().lower() not in {"0", "off", "false", "no"}


def enabled() -> bool:
    """The firewall's single switch. Off ⇒ no scan, no guard, no taint fields."""
    return _ENABLED


def set_enabled(on: bool) -> None:
    """Flip the firewall (the I9 control). Off must be byte-equivalent to a
    pre-Day-25 process on every observable output."""
    global _ENABLED
    _ENABLED = bool(on)


@contextmanager
def firewall(on: bool) -> Iterator[None]:
    """Scoped switch, so the suite can run the same fixture on and off."""
    global _ENABLED
    prev = _ENABLED
    _ENABLED = bool(on)
    try:
        yield
    finally:
        _ENABLED = prev


def sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", "replace")).hexdigest()


# ============================================================= signal shape

@dataclass
class InjectionSignal:
    """One detected signal — a class, where it was, and a *hash* of the excerpt.

    The offsets (`start`/`end`) are into the scanned span, so the hash plus the
    offsets find the text in the corpus without the trace ever storing it.
    """

    signal_class: str
    source: str
    path: str
    excerpt_sha256: str
    start: int
    end: int
    severity: str = SEV_MEDIUM

    def as_dict(self) -> dict[str, Any]:
        """The metadata-safe view — class + source + path + hash + offsets + severity.

        Deliberately no `text`: the payload stays in the corpus where it belongs
        and where whoever owns the corpus can remove it.
        """
        return {
            "class": self.signal_class,
            "source": self.source,
            "path": self.path,
            "sha256": self.excerpt_sha256,
            "start": self.start,
            "end": self.end,
            "severity": self.severity,
        }


@dataclass
class TaintedSpan:
    """A contiguous, untrusted span of text: its origin, its field path, and the
    signals found inside it. The unit the evidence framing and the trace share."""

    source: str
    path: str
    start: int
    end: int
    signals: list[InjectionSignal] = field(default_factory=list)

    @property
    def classes(self) -> list[str]:
        return sorted({s.signal_class for s in self.signals})

    @property
    def severity(self) -> str:
        order = {SEV_LOW: 0, SEV_MEDIUM: 1, SEV_HIGH: 2}
        return max((s.severity for s in self.signals), key=lambda x: order.get(x, 0),
                   default=SEV_LOW)

    def as_dict(self) -> dict[str, Any]:
        return {"source": self.source, "path": self.path, "start": self.start,
                "end": self.end, "classes": self.classes, "severity": self.severity,
                "signals": [s.as_dict() for s in self.signals]}


def spans_of(signals: Sequence[InjectionSignal]) -> list[TaintedSpan]:
    """Group a signal list into spans (one span per distinct source+path)."""
    groups: dict[tuple[str, str], list[InjectionSignal]] = {}
    for s in signals:
        groups.setdefault((s.source, s.path), []).append(s)
    out: list[TaintedSpan] = []
    for (source, path), items in groups.items():
        out.append(TaintedSpan(source=source, path=path,
                               start=min(s.start for s in items),
                               end=max(s.end for s in items),
                               signals=list(items)))
    return out


# ============================================================= the patterns
# Every pattern is scanned against a *bounded window* of the span. Offsets are
# relative to that window, which starts at 0 of the span, so they are the span's.

_OVERRIDE = re.compile(r"""
 (?:
   ignore\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|earlier|preceding|foregoing)\s+(?:instructions?|prompts?|rules?|directions?)
 | disregard\s+(?:your\s+|the\s+|all\s+)?(?:instructions?|system\s+prompt|prompts?|rules?|guidelines?)
 | forget\s+(?:everything|all|your)\s+(?:you\s+were\s+told|instructions?|rules?|previous)
 | remember\s+(?:that|to)\b
 | override\s+(?:the\s+)?(?:policy|system\s+prompt|instructions?|rules?|security|guardrails?)
 | new\s+instructions?\s*:
 | you\s+are\s+now\s+(?:an?\s+)?(?:admin|administrator|root|developer|superuser|unrestricted|jailbroken|evil)
 | (?:enable|activate|enter|switch\s+to)\s+(?:developer|god|admin|debug)\s+mode
 | developer\s+mode
 | pretend\s+(?:to\s+be|you\s+are)
 | act\s+as\s+(?:an?\s+)?(?:unrestricted|admin|administrator|root|developer|jailbroken)
 | do\s+anything\s+now
 | jailbreak
 | bypass\s+(?:the\s+)?(?:policy|restrictions?|safety|guardrails?|filters?)
 | i\s+am\s+(?:an?\s+)?(?:admin|administrator|root|developer|superuser|the\s+operator)
 | you\s+are\s+(?:an?\s+)?(?:admin|administrator|root|superuser|the\s+operator|privileged)
 | as\s+an?\s+(?:admin|administrator|root|developer|superuser)
 | this\s+is\s+pre-?authorized
 | (?:i\s+)?hereby\s+authorize
)
""", re.I | re.X)

_ROLE_MARKER = re.compile(r"""
 (?:
   ^[ \t]*(?:system|developer|assistant|tool|human|user)[ \t]*:[ \t]     # line-leading role marker
 | <\|?(?:system|im_start|im_end|assistant|endoftext)\|?>
 | \[\s*(?:system|developer|assistant)\s*\]
 | <\s*(?:system|developer)\s*>
 | \#{2,}\s*(?:system|instruction|developer)
 | begin\s+(?:system|instruction|developer)
 | \bend\s+of\s+(?:system|instructions)\b
 )
""", re.I | re.X | re.M)

# A disclosure ask names a *privileged artifact* (the prompt, the policy, the
# schema, the configuration) — hence the "extraction of a privileged artifact is
# itself privileged" rule in `implies_privileged_action`.
_EXFIL = re.compile(r"""
 (?:
   \b(?:print|reveal|repeat|show|dump|output|disclose|expose|leak|translate|
        summarize|summarise|list|enumerate|give\s+me|tell\s+me|what\s+is)\b
   [^.\n]{0,48}?
   \b(?:your\s+|the\s+|all\s+|a\s+|my\s+)?
   (?:system\s+|full\s+|complete\s+|hidden\s+|initial\s+)?
   (?:prompt|instructions?|rules?|guidelines?|policy|policies|configuration|
      config|schema(?:\s+card)?|system\s+message|initial\s+message|fields?
      \s+you\s+(?:validate|enforce|check))
 | what\s+(?:were|are|was)\s+you\s+told
 | repeat\s+the\s+text\s+above
 | summarize\s+your\s+configuration
 | for\s+your\s+own\s+audit
 | (?:list|name|enumerate|show)\s+the\s+(?:fields|schema|columns)\s+you
 | verbatim
)
""", re.I | re.X)

_TOOL_NAMES = ("search_saleor_docs", "search_saleor_code", "get_product",
               "get_order", "get_customer", "run_saleor_sql",
               "describe_saleor_schema")

_TOOL_INJECTION = re.compile(
    r"""
 (?:
   \b(?:call|invoke|execute|run|use)\s+(?:the\s+)?(?:tool\s+)?"""
    + r"(?:" + "|".join(_TOOL_NAMES) + r")"
    + r"""
 | \b(?:call|invoke|execute)\s+(?:the\s+tool\s+)?[a-z][a-z0-9]*_[a-z0-9_]+
 | \bdo\s+not\s+tell\s+the\s+user\b
 | \bdo\s+not\s+mention\s+this\b
 | \b(?:include|pass|embed|append)\b[^.\n]{0,64}\b(?:in|as|into)\s+the\s+
   (?:query|search|argument|args|sql|url|doc_id)\b
 )
""", re.I | re.X)

# NOTE: compiled **without** `re.I`, because `re.IGNORECASE` case-folds a
# non-ASCII character class and would make `[\u0080-\u02af…]` match ASCII letters
# (U+0130 folds to `i`, U+017F folds to `s`). The leetspeak alternative gets a
# scoped `(?i:…)` instead, so obfuscation detection cannot over-fire on plain text.
_OBFUSCATION = re.compile(r"""
 (?:
   \b[A-Za-z0-9+/]{40,}={0,2}\b                     # a long base64-ish run
 | \b[0-9a-fA-F]{32,}\b                             # a long hex run
 | (?i:\b(?:[1l][gq]n[0o]r[3e]|pr[1l]nt|5y5t3m|p4ssw0rd|1nstruct[10]ns|r3v34l)\b)   # leetspeak
 | [\u0080-\u02af\u0370-\u03ff\u0400-\u04ff\u0530-\u058f]   # homoglyph ranges (not typographic quotes)
 )
""", re.X)

_FORGED_CONTROL = re.compile(r"""
 (?:
   rejection_class\s*["'`:=]\s*["'`]?(?:policy|repairable|invalid_sql)
 | \bpolicy\s+(?:check\s+)?(?:passed|succeeded|cleared|approved|ok)\b
 | \b(?:the\s+)?(?:previous|last|earlier)\s+(?:call|query|request)\s+was\s+rejected\s+as\s+\w+
 | \berror_kind\s*["'`:=]\s*["'`]?\w+
 | \brefused\s*["'`:=]\s*["'`]?(?:true|false)
 | \btool_result\b
 | \btrace_id\s*["'`:=]\s*["'`]?[0-9a-f]{8,}
 | \bSYSTEM\b[^\n]{0,40}\b(?:policy|check|passed|allow)\b
 )
""", re.I | re.X)

# The privileges an override tries to reach: a write, a PII read, a broadened
# authority, an escalation, an exfiltration sink.
_PRIVILEGE = re.compile(r"""
 (?:
   \b(?:delete|drop|truncate|update|insert|grant|revoke|alter|merge|upsert)\b
 | \b(?:approve|pre-?approved|pre-?authorized|pre-?approve|authorize)\b
 | \b(?:write|writes|write\s+access|writable)\b
 | \b(?:admin|administrator|root|superuser|super\s+user|sudo)\b
 | \b(?:password|password_hash|api[_ ]?key|secret|token|credential|pii|ssn|credit\s+card)\b
 | \b(?:escalate|escalation|privilege\s+escalation)\b
 | \b(?:dump|exfiltrate|exfil|leak|smuggle)\b
)
""", re.I | re.X)

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (CLASS_OVERRIDE, _OVERRIDE),
    (CLASS_FORGED_CONTROL, _FORGED_CONTROL),
    (CLASS_EXFIL, _EXFIL),
    (CLASS_TOOL_INJECTION, _TOOL_INJECTION),
    (CLASS_ROLE_MARKER, _ROLE_MARKER),
    (CLASS_OBFUSCATION, _OBFUSCATION),
)

# A field that is *supposed* to hold a name / note / title — never instructions.
_NAME_FIELD = re.compile(
    r"(?:^|[.\[])(?:name|first_name|last_name|full_name|note|notes|title|slug|"
    r"description|label|display_name|heading|email)$", re.I)


def _is_name_field(path: str) -> bool:
    return bool(path) and bool(_NAME_FIELD.search(path))


def _signal(cls: str, source: str, path: str, start: int, end: int,
            excerpt: str) -> InjectionSignal:
    return InjectionSignal(signal_class=cls, source=source, path=path,
                           excerpt_sha256=sha256_text(excerpt), start=int(start),
                           end=int(end), severity=_CLASS_SEVERITY.get(cls, SEV_MEDIUM))


# ============================================================= scan (bounded)

def scan(text: str, source: str, path: str, *,
         max_chars: int = DEFAULT_SCAN_CHARS,
         max_signals: int = DEFAULT_MAX_SIGNALS) -> list[InjectionSignal]:
    """Report the signals in `text` — a bounded, deterministic read. **Decides nothing.**

    `source` is one of `TAINT_SOURCES`; `path` is the field path
    (`user_turn`, `chunk:{id}`, `product.name`). At most `max_chars` characters are
    read and at most `max_signals` signals are returned; both bounds are security
    properties (a detector that must read an unbounded span is a DoS vector and a
    log-flood), so an oversized observation is *truncated*, never an error.
    """
    raw = text or ""
    window = raw[:max(1, int(max_chars))]
    out: list[InjectionSignal] = []
    if not window:
        return out
    for cls, pattern in _PATTERNS:
        for match in pattern.finditer(window):
            out.append(_signal(cls, source, path, match.start(), match.end(),
                               window[match.start():match.end()]))
            if len(out) >= max_signals:
                return out
    # A name field carrying instruction-shaped content is its own signal — the
    # field is the finding, so the class names the *field*, not the payload.
    if _is_name_field(path):
        first = next((s for s in out if s.signal_class in _INSTRUCTION_CLASSES), None)
        if first is not None:
            out.append(_signal(CLASS_FIELD_INSTRUCTION, source, path,
                               first.start, first.end,
                               window[first.start:first.end]))
    return out[:max_signals]


def safe_scan(text: str, source: str, path: str, **kw: Any) -> list[InjectionSignal]:
    """`scan`, fail-open: a scanner that raises (I9) reports nothing and changes
    nothing about the request. Detection is not allowed to break the run it reads."""
    try:
        return scan(text, source, path, **kw)
    except Exception:                                       # noqa: BLE001
        return []


def scan_spans(text: str, source: str, path: str, **kw: Any) -> list[TaintedSpan]:
    """`scan` grouped into spans — the shape the evidence framing and trace use."""
    return spans_of(safe_scan(text, source, path, **kw))


# ============================================================= result surfaces

# The record resolvers' field prefixes, so a data cell's path reads
# `product.name`, `order.note`, `customer.first_name` — the *field*, not the tool.
_RECORD_PREFIX = {"get_product": "product", "get_order": "order",
                  "get_customer": "customer"}


def _evidence_fields(result: Any) -> list[tuple[str, str, str]]:
    rows = getattr(result, "evidence", None) or []
    out: list[tuple[str, str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        cid = row.get("chunk_id") or row.get("doc_id") or "?"
        if row.get("text"):
            out.append((TAINT_DOCUMENT, f"chunk[{cid}]", str(row["text"])))
        if row.get("heading"):
            out.append((TAINT_DOCUMENT, f"doc[{row.get('doc_id')}].heading",
                        str(row["heading"])))
    return out


def _record_fields(name: str, result: Any) -> list[tuple[str, str, str]]:
    data = getattr(result, "data", None)
    if not isinstance(data, dict):
        return []
    columns = data.get("columns")
    rows = data.get("rows")
    if not isinstance(columns, list) or not isinstance(rows, list):
        return []
    prefix = _RECORD_PREFIX.get(name, data.get("table") or name)
    out: list[tuple[str, str, str]] = []
    for row in rows:
        if not isinstance(row, (list, tuple)):
            continue
        for j, cell in enumerate(row):
            if isinstance(cell, str) and cell:
                col = str(columns[j]) if j < len(columns) else f"col{j}"
                out.append((TAINT_RECORD_FIELD, f"{prefix}.{col}", cell))
    return out


def fields_of(name: str, result: Any) -> list[tuple[str, str, str]]:
    """The free-text surfaces of a tool result, as `(source, path, text)` triples.

    Deliberately the *content* fields (evidence text, headings, record cells) and
    not the tool-result JSON scaffolding — a real `{"rejection_class": "policy"}`
    envelope is scaffolding, while a document that *says* "rejection_class:
    repairable" is the forged-control payload (I6). Scanning the same bytes the
    model reads, but only the fields a human with write access could have edited.
    """
    return _evidence_fields(result) + _record_fields(name, result)


def scan_result(name: str, result: Any, *,
                max_signals: int = DEFAULT_MAX_SIGNALS) -> list[InjectionSignal]:
    """Scan a tool result's content fields. Reports; never decides."""
    sigs: list[InjectionSignal] = []
    for source, path, text in fields_of(name, result):
        sigs.extend(safe_scan(text, source, path))
        if len(sigs) >= max_signals:
            break
    return sigs[:max_signals]


# ============================================================= the decision

def implies_privileged_action(signals: Sequence[InjectionSignal],
                              text: str | None = None) -> bool:
    """Does a signal set reach a **privileged action** — the small predicate the
    *authority* owns, kept separate from `scan`'s reporting.

    True when: a forged-control, tool-injection, or exfil signal is present (each
    names a capability or a privileged artifact); or an override/role signal is
    present *and* the text names a privilege (a write, a PII read, an escalation).
    A persona-only or answer-extraction-only signal is **not** privileged, and so
    is recorded and does not stop a turn: detection may stop; the authority decides.
    """
    classes = {s.signal_class for s in signals}
    if not classes:
        return False
    if classes & PRIVILEGED_CLASSES:
        return True
    if text and (classes & {CLASS_OVERRIDE, CLASS_ROLE_MARKER}) \
            and _PRIVILEGE.search(text or ""):
        return True
    return False


def refusal_text(signals: Sequence[InjectionSignal], path: str = "user_turn") -> str:
    """The deterministic, non-disclosing refusal the *layer* produces.

    It names the **class** of refusal and the **field path** — never the payload,
    never the system prompt, never `policy.yaml`. Reporting is deliberately boring;
    a refused attack is a first-class outcome and silence is a bug.
    """
    classes = sorted({s.signal_class for s in signals})
    return (
        "I cannot act on that request: it asks for a privileged action (an "
        "override of the request policy, a disclosure of a protected artifact, or "
        "a tool outside the allow-list) that this layer will not perform. I have "
        "run no query, disclosed no configuration, and called no tool. "
        f"Refusal class: policy; field: {path}; signals: {', '.join(classes) or 'none'}. "
        "The attempt has been recorded."
    )


def guard_request(text: str, *, source: str = TAINT_USER,
                  path: str = "user_turn") -> dict[str, Any]:
    """Scan a user turn and decide whether it reaches a privileged action.

    The one place a *user* turn can stop the run — because the user holds the
    request channel and a successful direct injection is capability transfer. It
    returns `{blocked, signals, refusal}` and nothing else; the *agent* is what
    turns `blocked` into the existing `ToolResult.rejected(refusal_class=policy)`.
    """
    sigs = safe_scan(text, source, path)
    blocked = implies_privileged_action(sigs, text)
    return {"blocked": bool(blocked), "signals": sigs,
            "refusal": refusal_text(sigs, path) if blocked else None,
            "source": source, "path": path}


# ============================================================= taint helpers

def taint_sources(signals: Sequence[InjectionSignal]) -> list[str]:
    """The distinct taint sources a signal list names, sorted (the run's `taint`)."""
    return sorted({s.source for s in signals if s.source in TAINT_SOURCES})


def is_tainted(signals: Sequence[InjectionSignal]) -> bool:
    return bool(signals)


# ============================================================= metadata safety
# A security feature that expands what gets recorded is a security regression in a
# feature's clothes. The redaction scan (Days 23–24) is extended over the new
# fields by insisting a serialized signal carries **only** these keys.

_SIGNAL_KEYS = frozenset({"class", "source", "path", "sha256", "start", "end",
                          "severity"})


def signals_are_metadata_safe(signals: Sequence[Any]) -> bool:
    """True when every signal is metadata-safe: known keys only, no raw payload.

    A `path` may legitimately be a short string; a `sha256` is 64 hex. Any other
    key — or any long free-text value — is the regression this check exists to catch.
    """
    for sig in signals or []:
        row = sig.as_dict() if hasattr(sig, "as_dict") else dict(sig)
        if set(row) - _SIGNAL_KEYS:
            return False
        if row.get("class") not in SIGNAL_CLASSES:
            return False
        if row.get("source") not in TAINT_SOURCES:
            return False
        digest = str(row.get("sha256") or "")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            return False
        for key in ("path",):
            value = row.get(key)
            if value is not None and len(str(value)) > 200:
                return False
    return True


# ============================================================= the /attacks ring
# Findings you cannot look up do not get tuned. A bounded, oldest-first, local ring
# of the day's attempts — the same shape as the trace ring (Day 23) and the log ring
# (Day 24), and deliberately no bigger.

_RING: "list[dict[str, Any]]" = []


def record_attempt(trace_id: str | None, taint: Sequence[str],
                   signals: Sequence[InjectionSignal], blocked: bool) -> dict[str, Any]:
    """Record one request's injection findings in the bounded local ring."""
    row = {
        "trace_id": trace_id,
        "taint": sorted({t for t in taint}),
        "blocked": bool(blocked),
        "signal_count": len(signals),
        "classes": sorted({s.signal_class for s in signals}),
        "paths": sorted({s.path for s in signals}),
        "signals": [s.as_dict() for s in signals],
    }
    _RING.append(row)
    while len(_RING) > RING_SIZE:
        _RING.pop(0)
    return row


def attempts(trace_id: str | None = None) -> list[dict[str, Any]]:
    """The ring, or one request's rows. Payloads are hashed; nothing raw is stored."""
    if trace_id is None:
        return [dict(r) for r in _RING]
    return [dict(r) for r in _RING if r.get("trace_id") == trace_id]


def clear_ring() -> None:
    _RING.clear()


def ring_size() -> int:
    return len(_RING)
