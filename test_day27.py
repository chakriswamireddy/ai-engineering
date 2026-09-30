"""Day 27 §9 — deterministic unit suite for the approval primitives.

    $ python test_day27.py

No API key, no network. The *workflow* fixtures (H1–H9, through the real loop /
registry / executor / role seams) live in `tools/approval_smoke.py`; this is the
arithmetic underneath them —

- `digest_action`: canonical, stable under argument key order, changed by a
  single-field edit to any of the six components, **unchanged** by a run id, a
  model rationale and a signal set, and refused on a float;
- the state machine: every legal edge, every illegal edge with its code, and the
  `ValueError` / `ApprovalRefusal` split (an attempt that is not a record vs one
  that is);
- **no in-place mutation**: there is no `update` method on the store or the record,
  a refused transition leaves the record's own bytes identical, and every
  transition returns a *new* record;
- the fail-closed loader: reversibility, the compensation requirement, the derived
  write rule, the harness-only declaration, and the role table;
- separation of duties and both roles resolved as **grants**;
- signals: triples with caveats, no aggregate, the model's confidence excluded by
  name;
- the store's kind-specific provenance policy in **both** directions;
- the CLI's exit codes (0 ok · 1 refused · 2 the attempt is not a record);
- the two read-only surfaces: the masked record view and `trace.approval_columns`.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from ai_os import approval as appr  # noqa: E402
from ai_os import permissions as perms  # noqa: E402
from ai_os import state as state_mod  # noqa: E402
from ai_os.audit import AuditLog, NullAuditLog  # noqa: E402
from ai_os.state import StateStore  # noqa: E402
from ai_os.tools import ToolResult, build_default_registry  # noqa: E402
from ai_os.trace import (  # noqa: E402
    AGENT_TERMINATIONS, approval_columns, approval_from_result,
)

FAILS = 0
EXPORT = "export_customer_report"


def check(label: str, cond: bool, detail: str = "") -> bool:
    global FAILS
    if not cond:
        FAILS += 1
    print(f"[{'OK  ' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))
    return bool(cond)


def raises(fn) -> str | None:
    """`ApprovalRefusal` code · `ValueError:<Type>` for the other kind · `None` if ok."""
    try:
        fn()
    except appr.ApprovalRefusal as refusal:
        return refusal.code
    except ValueError as exc:
        return f"ValueError:{type(exc).__name__}"
    except Exception as exc:                                   # noqa: BLE001
        return type(exc).__name__
    return None


class Clock:
    """The injected clock: every expiry below is arithmetic, never a `sleep`."""

    def __init__(self, t: float = 500.0) -> None:
        self.t = float(t)

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += float(dt)


def make_desk(tmp: Path, *, clock: Clock | None = None, audit=None, permissions=None,
              ttl_s: float = 900.0, max_per_run: int | None = None,
              name: str = "unit.json") -> appr.ApprovalStore:
    clk = clock or Clock()
    store = StateStore(path=tmp / name, default_ttl=86400.0, autosave=False, clock=clk)
    return appr.ApprovalStore(store, clock=clk, ttl_s=ttl_s, max_per_run=max_per_run,
                              permissions=permissions or perms.default_permission_set(),
                              audit=audit if audit is not None else NullAuditLog())


def open_record(d: appr.ApprovalStore, rid: str, *,
                tool: str = EXPORT, rev: str = appr.REVERSIBILITY_IRREVERSIBLE,
                who: str = "default", **kw) -> appr.ApprovalRecord:
    return d.submit(tool=tool, args={"customer_ids": "1,2"}, effect="read",
                    data="pii", reversibility=rev, requested_by=who,
                    request_id=rid, run_id=f"run-{rid}", **kw)


# --------------------------------------------------------------- the digest

def test_digest() -> None:
    print("\n== digest: canonical, sensitive to the action, blind to the reasoning")
    kwargs = {"effect": "read", "data": "pii", "reversibility": "irreversible",
              "targets": ["1", "2"]}
    d = appr.digest_action("t", {"a": 1, "b": "x"}, **kwargs)
    check("it is a `sha256:` digest", d.startswith("sha256:") and len(d) == 71, d[:20])
    check("stable under argument key order",
          d == appr.digest_action("t", {"b": "x", "a": 1}, **kwargs))
    check("stable across a JSON-string and a dict argument form",
          d == appr.digest_action("t", json.dumps({"a": 1, "b": "x"}), **kwargs))
    for field, value in (("tool", "u"), ("args", {"a": 2, "b": "x"}),
                         ("effect", "write"), ("data", "public"),
                         ("reversibility", "reversible"), ("targets", ["9"])):
        edited = appr.digest_action(
            value if field == "tool" else "t",
            value if field == "args" else {"a": 1, "b": "x"},
            effect=value if field == "effect" else "read",
            data=value if field == "data" else "pii",
            reversibility=value if field == "reversibility" else "irreversible",
            targets=value if field == "targets" else ["1", "2"])
        check(f"changed by a single-field edit to `{field}`", edited != d)
    check("a float is refused rather than rounded (one hashing convention, shared "
          "with the chain)",
          raises(lambda: appr.digest_action("t", {"n": 1.5}, **kwargs))
          == "ValueError:ValueError")
    check("targets are derived from the argument *names*, never from prose",
          appr.action_targets({"customer_ids": "1,2", "note": "export these"})
          == ["1", "2"]
          and appr.action_targets({"order_id": "o-1", "q": "why"}) == ["o-1"]
          and appr.action_targets({"sql": "SELECT 1"}) == [])
    check("targets are de-duplicated and bounded",
          appr.action_targets({"ids": ["a", "a", "b"]}) == ["a", "b"]
          and len(appr.action_targets({"ids": [str(i) for i in range(500)]})) == 64)


# --------------------------------------------------------------- the state machine

def test_state_machine(tmp: Path) -> None:
    print("\n== the state machine: legal edges, illegal edges, no in-place mutation")
    clk = Clock()
    d = make_desk(tmp, clock=clk, max_per_run=None)

    check("there is no `update` method on the store or on the record (the defining "
          "property of an append-only shelf)",
          not hasattr(appr.ApprovalStore, "update")
          and not hasattr(appr.ApprovalRecord, "update"))

    a = open_record(d, "u1")
    check("`submit` opens `requested`, with a decision window and a digest",
          a.state == appr.STATE_REQUESTED and a.expires_at == clk() + 900.0
          and a.ttl_s == 900.0 and a.attributable()
          and a.digest.startswith("sha256:"))
    check("the request body is canonical: names and value *shapes*, composed in zero "
          "fields by the model",
          a.subject == {"tool": EXPORT,
                        "args_shape": {"customer_ids": {"chars": 3}},
                        "effect": "read", "data": "pii",
                        "reversibility": "irreversible", "targets": ["1", "2"]})
    check("a record with no signals is `elevated`, never silently routine",
          a.scrutiny() == appr.SCRUTINY_ELEVATED)
    approved = d.decide("u1", decided_by="support_lead", approve=True)
    check("decide returns a NEW record and leaves the object it came from untouched",
          approved is not a and approved.state == appr.STATE_APPROVED
          and a.state == appr.STATE_REQUESTED)
    check("the decided record is attributable and names its actor and clock reading",
          approved.attributable() and approved.decided_by == "support_lead"
          and approved.decided_at == clk())
    applied = d.apply("u1", applied_by="executor")
    check("apply records a *separate* principal and stays attributable",
          applied.state == appr.STATE_APPLIED and applied.applied_by == "executor"
          and applied.attributable())
    reversible = open_record(d, "u1r", rev=appr.REVERSIBILITY_REVERSIBLE)
    check("a reversible record carries its class and the compensation its grant "
          "declared",
          reversible.reversibility == appr.REVERSIBILITY_REVERSIBLE)
    d.decide("u1r", decided_by="support_lead", approve=True)
    d.apply("u1r", applied_by="executor")
    comp = d.compensate("u1r", compensated_by="executor")
    check("the compensation is a NEW record pointing at the original, whose own state "
          "never moved",
          comp.request_id == "u1r-c1" and comp.compensates == "u1r"
          and comp.state == appr.STATE_COMPENSATED
          and d.get("u1r").state == appr.STATE_APPLIED)
    check("compensating an IRREVERSIBLE action is refused loudly (a pretend-undo is "
          "how a customer is told their refund was reversed)",
          raises(lambda: d.compensate("u1", compensated_by="executor"))
          == appr.REFUSAL_REVERSAL_IMPOSSIBLE
          and d.get("u1").state == appr.STATE_APPLIED)

    open_record(d, "u2")
    check("apply before approve ⇒ approval_invalid_state",
          raises(lambda: d.apply("u2", applied_by="executor"))
          == appr.REFUSAL_INVALID_STATE)
    check("decide twice ⇒ approval_duplicate (one decision, and it is final)",
          d.decide("u2", decided_by="support_lead", approve=True).state
          == appr.STATE_APPROVED
          and raises(lambda: d.decide("u2", decided_by="support_lead", approve=True))
          == appr.REFUSAL_DUPLICATE)
    open_record(d, "u3")
    live = appr.record_fingerprint(d.get("u3"))
    check("a refused decision does not mutate the live record (same bytes)",
          raises(lambda: d.decide("u3", decided_by="nobody", approve=True))
          == appr.REFUSAL_ROLE_NOT_PERMITTED
          and appr.record_fingerprint(d.get("u3")) == live)
    check("`expire` is refused for a live (undue) request — the clock ends a request, "
          "not a pass with a hand on it",
          raises(lambda: d.expire("u3")) == appr.REFUSAL_INVALID_STATE)
    clk.advance(901)
    check("a decision after the window ⇒ approval_expired (silence is a decision, and "
          "it is negative)",
          raises(lambda: d.decide("u3", decided_by="support_lead", approve=True))
          == appr.REFUSAL_EXPIRED)
    check("an expired request is still readable — the refusal needs the record to "
          "exist", d.get("u3") is not None and d.get("u3").is_expired(clk()))
    check("`expire` moves a *due* request to `expired`, and it stays attributable",
          d.expire("u3").state == appr.STATE_EXPIRED and d.get("u3").attributable())
    check("supersede is refused once a decision has landed",
          raises(lambda: d.supersede("u2", by="default"))
          == appr.REFUSAL_INVALID_STATE)
    check("an unknown request id is a coded refusal, never a crash",
          raises(lambda: d.decide("nope", decided_by="support_lead", approve=True))
          == appr.REFUSAL_INVALID_STATE)
    before = appr.record_fingerprint(d.get("u2"))
    check("a decision with no actor is a `ValueError` (not an ApprovalRefusal) and "
          "writes nothing at all",
          raises(lambda: d.decide("u2", decided_by="", approve=True))
          == "ValueError:ValueError"
          and appr.record_fingerprint(d.get("u2")) == before)
    check("a rejection with no reason is refused the same way (unreviewable)",
          (lambda rid: raises(lambda: d.decide(rid, decided_by="support_lead",
                                               approve=False))
           == "ValueError:ValueError")(open_record(d, "u5").request_id))
    check("a rejection WITH a reason is recorded, reason and all",
          d.decide(open_record(d, "u4").request_id, decided_by="support_lead",
                   approve=False, reason="ids wrong").reason == "ids wrong")
    check("an unknown reversibility class is refused at `submit`",
          raises(lambda: d.submit(tool="t", args={}, effect="read", data="pii",
                                  reversibility="sort-of", requested_by="default"))
          == "ValueError:ValueError")
    check("an anonymous request is refused at `submit`",
          raises(lambda: d.submit(tool="t", args={}, effect="read", data="pii",
                                  reversibility="reversible", requested_by="  "))
          == "ValueError:ValueError")
    check("`expire_due` ends a due decided-but-unapplied request and is idempotent (a "
          "pass never rewrites history)",
          [r.request_id for r in d.expire_due()] == ["u2"] and d.expire_due() == []
          and d.get("u2").state == appr.STATE_EXPIRED)
    check("the fingerprint tracks the record (so it can prove it did *not* change)",
          appr.record_fingerprint(d.get("u1")) != appr.record_fingerprint(
              d.get("u1")._replace(state=appr.STATE_SUPERSEDED)))
    check("`list_for` narrows to a principal's own party (requester, decider, "
          "executor) and to nobody else",
          [r.request_id for r in d.list_for("executor")] == ["u1", "u1r", "u1r-c1"]
          and {"u2", "u4"} <= {r.request_id for r in d.list_for("support_lead")}
          and not d.list_for("nobody"))
    check("the approver role is resolved from the grant file rather than repeated by "
          "the caller", d.get("u1").approver_role == "support_lead")

    bounded = appr.ApprovalStore(d.store, clock=clk, max_per_run=1,
                                 permissions=perms.default_permission_set())
    bounded.submit(tool=EXPORT, args={"customer_ids": "9"}, effect="read", data="pii",
                   reversibility=appr.REVERSIBILITY_IRREVERSIBLE,
                   requested_by="default", run_id="cap-run")
    check("a bounded desk refuses a second request from one run (a queue a caller "
          "could otherwise flood)",
          raises(lambda: bounded.submit(
              tool=EXPORT, args={"customer_ids": "9"}, effect="read", data="pii",
              reversibility=appr.REVERSIBILITY_IRREVERSIBLE,
              requested_by="default", run_id="cap-run")) == appr.REFUSAL_RUN_LIMIT)
    check("`counts()` adds up to the shelf and `pending()` is exactly the queue",
          bounded.counts()["total"] == len(bounded.all_records())
          and [r.request_id for r in bounded.pending()]
          == [r.request_id for r in bounded.all_records()
              if r.state == appr.STATE_REQUESTED])


# --------------------------------------------------------------- the loader

def test_loader_refusals() -> None:
    print("\n== the loader refuses, by name, and defaults nothing")
    read = {"effect": "read", "data": "pii"}
    gate = {**read, "reversibility": "irreversible", "confirm": "human",
            "approver_role": "support_lead"}
    roles = {"support_lead": ["default"]}

    def load(grant, *, approver_roles=None, principals=None, name="get_order"):
        raw = {"principals": principals or {"default": [name]},
               "tools": {name: grant}}
        if approver_roles is not None:
            raw["approver_roles"] = approver_roles
        return perms.PermissionSet.from_dict(raw, source="<t>")

    check("no `reversibility` ⇒ load error (forgetting must be a boot error, not a "
          "silent disclosure)", raises(lambda: load(read)) == "ValueError:ValueError")
    check("an unknown `reversibility` class ⇒ load error",
          raises(lambda: load({**read, "reversibility": "undone"}))
          == "ValueError:ValueError")
    check("`irreversible` without `confirm: human` ⇒ load error (Day 26's rule, as "
          "the derived case)",
          raises(lambda: load({**read, "reversibility": "irreversible"}))
          == "ValueError:ValueError")
    check("`confirm: human` with no `approver_role` ⇒ load error",
          raises(lambda: load({**gate, "approver_role": None}, approver_roles=roles))
          == "ValueError:ValueError")
    check("an undeclared `approver_role` ⇒ load error (a gate nobody can answer)",
          raises(lambda: load(gate, approver_roles={"executor": ["default"]}))
          == "ValueError:ValueError")
    check("an `approver_role` naming an undeclared principal ⇒ load error",
          raises(lambda: load(gate, approver_roles={"support_lead": ["ghost"]}))
          == "ValueError:ValueError")
    check("`approver_role` without `confirm: human` ⇒ load error (a role with no gate)",
          raises(lambda: load({**read, "reversibility": "reversible",
                               "approver_role": "support_lead",
                               "compensation": {"undo": "x", "performer": "y"}}))
          == "ValueError:ValueError")
    check("`reversible` with no declared compensation ⇒ load error",
          raises(lambda: load({**read, "reversibility": "reversible"}))
          == "ValueError:ValueError")
    check("`compensatable` with no declared offset ⇒ load error",
          raises(lambda: load({**read, "reversibility": "compensatable"}))
          == "ValueError:ValueError")
    check("a compensation missing `undo`/`performer`, or carrying an unknown key, ⇒ "
          "load error",
          raises(lambda: load({**read, "reversibility": "reversible",
                               "compensation": {"undo": "x"}}))
          == "ValueError:ValueError"
          and raises(lambda: load({**read, "reversibility": "reversible",
                                   "compensation": {"undo": "x", "performer": "y",
                                                    "whenever": 1}}))
          == "ValueError:ValueError")
    check("`irreversible` WITH a compensation ⇒ load error (a pretend-undo in a file)",
          raises(lambda: load({**gate, "compensation": {"undo": "u", "performer": "p"}},
                              approver_roles=roles)) == "ValueError:ValueError")
    check("`effect: write` needs an explicit confirm — and a `reversible` write is "
          "loadable when it is announced",
          raises(lambda: perms.Grant.from_dict(
              "t", {"effect": "write", "data": "pii"}, source="<t>"))
          == "ValueError:ValueError"
          and perms.Grant.from_dict(
              "t", {"effect": "write", "data": "internal", "confirm": "none",
                    "reversibility": "reversible",
                    "compensation": {"undo": "append a correction",
                                     "performer": "executor"}},
              source="<t>").reversibility == "reversible")
    check("a human-gated grant with no reversibility receives the CONSERVATIVE class "
          "(Day 26's C2e shape, loadable for the right reason)",
          perms.Grant.from_dict("t", {"effect": "write", "data": "pii",
                                      "confirm": "human"},
                                source="<t>").reversibility == "irreversible")
    check("a grant for an unregistered tool still fails — unless `harness_only: true` "
          "is declared in the file itself",
          raises(lambda: load({**read, "reversibility": "reversible",
                               "compensation": {"undo": "x", "performer": "y"}},
                              principals={"default": []}, name="not_a_tool"))
          == "ValueError:ValueError"
          and load({**read, "reversibility": "reversible",
                    "compensation": {"undo": "x", "performer": "y"},
                    "harness_only": True}, principals={"default": []},
                   name=EXPORT).harness_only_tools() == (EXPORT,))
    check("a harness-only client may not be assigned to any principal in the file",
          raises(lambda: load({**read, "reversibility": "reversible",
                               "compensation": {"undo": "x", "performer": "y"},
                               "harness_only": True},
                              principals={"default": [EXPORT]}, name=EXPORT))
          == "ValueError:ValueError")
    shipped = perms.default_permission_set()
    check("the shipped file parses, declares one harness-only client, and assigns it "
          "to nobody",
          shipped.harness_only_tools() == (EXPORT,)
          and all(EXPORT not in shipped.granted_tools(p) for p in shipped.principals))
    check("every shipped grant declares a class, and the file's maximum authority is "
          "still a read",
          all(g.reversibility for g in shipped.tools.values())
          and all(g.effect != "write" for g in shipped.tools.values())
          and shipped.grant(EXPORT).result.egress == "external")


# --------------------------------------------------------------- roles & duties

def test_roles_and_duties(tmp: Path) -> None:
    print("\n== separation of duties, and both roles resolved as GRANTS")
    ps = perms.default_permission_set()
    d = make_desk(tmp, clock=Clock(), permissions=ps, max_per_run=None,
                  name="roles.json")
    check("the shipped roles are grants: support_lead approves, executor applies, and "
          "neither `default` nor an unknown name holds either",
          ps.holds_approver_role("support_lead", "support_lead")
          and ps.holds_approver_role("executor", "executor")
          and not ps.holds_approver_role("default", "support_lead")
          and not ps.holds_approver_role("support_lead", "executor")
          and not ps.holds_approver_role(None, "support_lead")
          and not ps.holds_approver_role("admin", "support_lead"))

    own = open_record(d, "r1", who="support_lead")
    check("the requester cannot approve its own irreversible request",
          raises(lambda: d.decide("r1", decided_by="support_lead", approve=True))
          == appr.REFUSAL_SELF_APPROVED)
    check("an ungranted principal decides nothing, whoever it claims to be",
          raises(lambda: d.decide("r1", decided_by="default", approve=True))
          == appr.REFUSAL_ROLE_NOT_PERMITTED
          and raises(lambda: d.decide("r1", decided_by="admin", approve=True))
          == appr.REFUSAL_ROLE_NOT_PERMITTED)
    check("the record did not move through any of those refusals",
          d.get("r1").state == appr.STATE_REQUESTED and d.get("r1").decided_by is None)
    second = open_record(d, "r2", who="support")
    check("the refusal is about the principal, not the role: a request from `support` "
          "may be approved by the role holder",
          d.decide("r2", decided_by="support_lead", approve=True).state
          == appr.STATE_APPROVED and d.get("r2").requested_by != d.get("r2").decided_by)
    check("the requester may not apply — the executor is a second, separate grant",
          raises(lambda: d.apply("r2", applied_by="support"))
          == appr.REFUSAL_ROLE_NOT_PERMITTED
          and raises(lambda: d.apply("r2", applied_by="support_lead"))
          == appr.REFUSAL_ROLE_NOT_PERMITTED)
    check("the granted executor applies, and the two questions keep two answers",
          d.apply("r2", applied_by="executor").applied_by == "executor"
          and d.get("r2").decided_by != d.get("r2").applied_by)
    check("`own` is still pending, exactly as an unresolved request should be",
          d.get("r1").state == appr.STATE_REQUESTED
          and [r.request_id for r in d.pending()] == ["r1"])


# --------------------------------------------------------------- signals

def test_signals() -> None:
    print("\n== signals: triples with caveats, no aggregate, the confidence excluded")
    forbidden = re.compile(r"score|risk|confidence|probability", re.IGNORECASE)
    sig = appr.build_signals(last_result=ToolResult.success(
        "s", evidence=[{"doc_id": "d#1", "chunk_id": "d#1", "score": 0.9}]),
        cited_subset_retrieved=False, prior_approvals=0, declared_data="pii",
        model_stated_confidence=0.8)
    entries = sig["signals"]
    check("no key matches the aggregate vocabulary",
          not [k for k in entries if forbidden.search(k)], str(sorted(entries)))
    check("each entry is a `{value, measures, not}` triple",
          all({"value", "measures", "not"} <= set(v) for v in entries.values()))
    check("it reports the repository's own four measurements, with their caveats",
          set(entries) == {*appr.MEASUREMENT_SIGNALS, appr.MODEL_STATED_KEY}
          and entries["support"]["value"] is False
          and entries["similarity"]["value"] == 0.9
          and entries["novelty"]["value"] == 0)
    check("the model's stated confidence is excluded BY NAME, with its reason",
          entries[appr.MODEL_STATED_KEY]["not_a_signal"] is True
          and entries[appr.MODEL_STATED_KEY]["reason"] == appr.NOT_A_SIGNAL_REASON
          and "confidence" not in appr.MODEL_STATED_KEY)
    check("the no-aggregate rule is re-checked on the way in",
          raises(lambda: appr.Signals.from_dict({"risk": {"value": 1}}))
          == "ValueError:ValueError")
    check("an empty signal set is `elevated`, never a pass",
          appr.build_signals()["scrutiny"] == appr.SCRUTINY_ELEVATED
          and "never a green light" in appr.build_signals()["scrutiny_reason"])
    check("a complete set is `routine` and claims nothing",
          appr.build_signals(last_result=ToolResult.success(
              "s", evidence=[{"doc_id": "d", "chunk_id": "d", "score": 0.5}]),
              cited_subset_retrieved=True, prior_approvals=1,
              declared_data="pii")["scrutiny"] == appr.SCRUTINY_ROUTINE)
    check("an observation with no score yields no similarity signal at all (absent, "
          "not zero)",
          "similarity" not in appr.build_signals(
              last_result=ToolResult.success("s", evidence=[
                  {"doc_id": "d", "chunk_id": "d", "text": "no score"}])
          )["signals"])
    check("the trace view carries signal *keys* and the non-signal label only",
          appr.Signals.from_dict(sig).as_trace() == {
              "signals": sorted(entries), "scrutiny": sig["scrutiny"],
              "not_a_signal": [appr.MODEL_STATED_KEY]})


# --------------------------------------------------------------- policy + surfaces

def test_store_policy_and_surfaces(tmp: Path) -> None:
    print("\n== the store's policy, the record view, and the trace projection")
    store = StateStore(path=tmp / "policy.json", autosave=False)
    for source in ("agent", "orchestrator", "executor", "distiller"):
        check(f"kind=approval refuses an {source!r}-authored write",
              raises(lambda s=source: store.put(
                  "default", state_mod.APPROVAL_KIND, "/approval/x", {},
                  created_by={"run_id": "r", "step": "s", "source": s}))
              == "ValueError:ProvenanceError")
    check("and the approver's author string may not be borrowed for another kind",
          raises(lambda: store.put(
              "default", state_mod.TASK_KIND, "/task/x", {},
              created_by={"run_id": "r", "step": "s", "source": "approver"}))
          == "ValueError:ProvenanceError")
    check("an ordinary kind still writes with its own author (nothing else moved)",
          store.put("default", state_mod.TASK_KIND, "/task/y", {"ok": True},
                    created_by={"run_id": "r", "step": "s", "source": "executor"})
          is not None
          and state_mod.approval_key("apr-1") == "/approval/apr-1"
          and state_mod.APPROVAL_KIND == "approval"
          and state_mod.APPROVAL_AUTHOR_SOURCE in state_mod.AUTHORED_SOURCES)

    d = make_desk(tmp, clock=Clock(), max_per_run=None, name="view.json")
    rec = open_record(d, "v1", proposal="a model-written paragraph")
    masked = appr.approval_view(rec)
    revealed = appr.approval_view(rec, reveal=True)
    check("the read-only view masks argument values by default and drops the proposal",
          masked["args"] == {"customer_ids": {"chars": 3}}
          and masked["values_masked"] is True and "proposal" not in masked
          and masked["signals"] == rec.signal_keys()
          and masked["digest"] == rec.digest)
    check("`reveal=1` shows the values (documented as unauthenticated — Day 29's "
          "argument, not a feature)",
          revealed["args"] == {"customer_ids": "1,2"})

    columns = approval_columns(requested=True, state=rec.state,
                              decision=appr.EVENT_REQUESTED, digest=rec.digest,
                              reversibility=rec.reversibility,
                              signals_present=rec.signal_keys(), ttl_s=rec.ttl_s,
                              applied=False, compensated=False,
                              extra={"approval_tool": rec.tool})
    check("`approval_columns` promotes the digest, the state, the reversibility, the "
          "signal keys and the window — and every cell is a scalar (no value, no "
          "proposal, no nested record)",
          columns["approval_requested"] is True
          and columns["approval_digest"].startswith("sha256:")
          and columns["approval_signals"] == sorted(rec.signal_keys())
          and columns["approval_tool"] == EXPORT
          and columns["approval_ttl_s"] == 900.0
          and not [v for v in columns.values() if isinstance(v, (dict, tuple))])
    result = ToolResult.failure(EXPORT, "withheld", error_kind="approval_required",
                               rejection_class="policy")
    result.approval = {"request_id": "v1", "state": "requested", "digest": rec.digest,
                       "reversibility": "irreversible", "signals": ["support"],
                       "scrutiny": "elevated", "expires_at": 1.0,
                       "args": {"customer_ids": "1,2"}}
    promoted = approval_from_result(result)
    check("the call-row projection is metadata only: no argument values, no expiry, no "
          "scrutiny chip",
          promoted == {"approval_request": "v1", "approval_state": "requested",
                       "approval_digest": rec.digest,
                       "approval_reversibility": "irreversible",
                       "approval_signals": ["support"]}
          and approval_from_result(ToolResult.success("t")) == {})
    ungoverned = build_default_registry()
    check("the ungoverned registry has no gate, and its results carry no approval "
          "columns",
          ungoverned.approvals_on() is False
          and approval_from_result(
              ungoverned.execute("get_order", {"id": "1"})) == {})
    check("the terminal is registered in the trace vocabulary and says who owns it",
          "Day 27" in AGENT_TERMINATIONS["awaiting_approval"]
          and "approval_request_id" in AGENT_TERMINATIONS["awaiting_approval"]
          and appr.ERROR_APPROVAL_REQUIRED not in ("timeout", "tool_error", "internal"))


# --------------------------------------------------------------- the CLI

def test_cli(tmp: Path) -> None:
    print("\n== the CLI: exit 0 on success, 1 on a refusal, 2 on not-a-record")
    path = tmp / "cli.json"
    env = {**os.environ, "APPROVALS_PATH": str(path),
           "AUDIT_LOG_PATH": str(tmp / "cli-audit.jsonl")}
    store = StateStore(path=path, default_ttl=86400.0, autosave=True)
    d = appr.ApprovalStore(store, permissions=perms.default_permission_set(),
                           audit=AuditLog(tmp / "cli-audit.jsonl"))
    open_record(d, "cli-1", who="support")
    open_record(d, "cli-2", who="support")

    def cli(*argv: str) -> tuple[int, str]:
        proc = subprocess.run([sys.executable, str(ROOT / "tools" / "approvals.py"),
                               *argv], cwd=str(ROOT), capture_output=True, text=True,
                              env=env)
        return proc.returncode, (proc.stdout + proc.stderr)

    code, out = cli("--list")
    check("`--list` exits 0 and prints the queue with signal *keys*, never values",
          code == 0 and "cli-1" in out and "PENDING" in out
          and "customer_ids" not in out and "support" in out)
    code, out = cli("--show", "cli-1")
    check("`--show` exits 0 and prints the digest on the first line, values included",
          code == 0 and out.lstrip().startswith("digest: sha256:")
          and "customer_ids" in out and "1,2" in out)
    code, out = cli("--show", "cli-1", "--masked")
    check("`--show --masked` renders shapes instead of values (the GET default)",
          code == 0 and "1,2" not in out and "chars" in out)
    code, out = cli("--show", "nope")
    check("an unknown id exits 1", code == 1 and "no approval record" in out)
    code, out = cli("--approve", "cli-1", "--as", "support")
    check("approving as a principal without the role exits 1 with a coded refusal",
          code == 1 and appr.REFUSAL_ROLE_NOT_PERMITTED in out)
    code, out = cli("--approve", "cli-1", "--as", "support_lead")
    check("the granted approver exits 0", code == 0 and "state: approved" in out)
    code, out = cli("--apply", "cli-1", "--as", "support_lead")
    check("applying as a non-executor exits 1", code == 1
          and appr.REFUSAL_ROLE_NOT_PERMITTED in out)
    code, out = cli("--apply", "cli-1", "--as", "executor")
    check("the granted executor exits 0", code == 0 and "state: applied" in out)
    code, out = cli("--apply", "cli-2", "--as", "executor")
    check("applying an un-approved request exits 1 (invalid state)", code == 1
          and appr.REFUSAL_INVALID_STATE in out)
    code, out = cli("--reject", "cli-2", "--as", "support_lead")
    check("a rejection without a reason exits 2 (not a record, and not a refusal "
          "either)", code == 2 and "NOT RECORDED" in out)
    code, out = cli("--reject", "cli-2", "--as", "support_lead", "--reason", "wrong ids")
    check("a rejection with a reason exits 0", code == 0 and "state: rejected" in out)
    code, out = cli("--reject", "cli-2", "--as", "support_lead", "--reason", "again")
    check("deciding twice exits 1 with `approval_duplicate`", code == 1
          and appr.REFUSAL_DUPLICATE in out)
    code, out = cli("--reject", "cli-2", "--reason", "no actor")
    check("a mutating command with no `--as` exits 2 (an unattributable decision is "
          "not a record)", code == 2 and "requires --as" in out)
    code, out = cli("--verify")
    check("`--verify` exits 0, reports the chain, and counts the approval events",
          code == 0 and "chain_ok: True" in out
          and "approval_requested=2" in out and "approval_applied=1" in out
          and "approval_rejected=1" in out)
    code, out = cli("--help")
    check("`--help` documents every subcommand and exits 0",
          code == 0 and all(f in out for f in ("--list", "--show", "--approve",
                                               "--reject", "--apply", "--verify")))
    code, out = cli("--list", "--nope")
    check("an unknown flag is a usage error (exit 2), never a silent no-op", code == 2)


# --------------------------------------------------------------- the server views

def test_server_routes(tmp: Path) -> None:
    print("\n== the read-only routes: two GETs, masked by default, no decision route")
    from ai_os import server
    src = (ROOT / "ai_os" / "server.py").read_text(encoding="utf-8")
    routes = {(str(getattr(r, "path", "")), tuple(sorted(getattr(r, "methods", ()) or ())))
              for r in server.app.routes}
    check("`/approvals` and `/approvals/{request_id}` are registered as GET",
          ("/approvals", ("GET",)) in routes
          and ("/approvals/{request_id}", ("GET",)) in routes)
    check("no POST decision route exists anywhere, and the omission is recorded at "
          "the registration site with its owner",
          not [p for p, m in routes if "decide" in p and "POST" in m]
          and "deliberately no decision route" in src
          and "Day 29" in src and "no authentication" in src)

    path = tmp / "server.json"
    store = StateStore(path=path, default_ttl=86400.0, autosave=True)
    d = appr.ApprovalStore(store, permissions=perms.default_permission_set(),
                           audit=AuditLog(tmp / "server-audit.jsonl"))
    open_record(d, "srv-1", who="support")
    os.environ["APPROVALS_PATH"] = str(path)
    try:
        listing = server.approvals_endpoint()
        check("`GET /approvals` returns the record with values masked and the honest "
              "note attached",
              listing["count"] == 1
              and listing["records"][0]["args"] == {"customer_ids": {"chars": 3}}
              and "reveal=1 is unauthenticated" in listing["note"])
        revealed = server.approvals_endpoint(reveal=1)
        check("`?reveal=1` shows the values and says that it did",
              revealed["records"][0]["args"] == {"customer_ids": "1,2"}
              and revealed["reveal"] is True)
        one = server.approval_endpoint("srv-1")
        check("`GET /approvals/{id}` returns the same masked record",
              one["request_id"] == "srv-1" and one["values_masked"] is True)
        check("an unknown id is a 404 (not an empty 200)",
              getattr(server.approval_endpoint("nope"), "status_code", None) == 404)
        with appr.approvals(False):
            check("with `APPROVALS=none` the read-only route degrades honestly rather "
                  "than inventing records",
                  server.approvals_endpoint()["count"] == 0
                  and "APPROVALS=none" in server.approvals_endpoint()["note"])
    finally:
        os.environ.pop("APPROVALS_PATH", None)


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="d27-unit-"))
    test_digest()
    test_state_machine(tmp)
    test_loader_refusals()
    test_roles_and_duties(tmp)
    test_signals()
    test_store_policy_and_surfaces(tmp)
    test_cli(tmp)
    test_server_routes(tmp)
    print("\n" + ("ALL DAY 27 (MECH) UNIT CHECKS PASSED" if not FAILS
                  else f"{FAILS} CHECK(S) FAILED"))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
