"""Day 27 §8 — the decision surface: a CLI on purpose, and not an HTTP route.

    $ python tools/approvals.py --list
    $ python tools/approvals.py --show apr-1
    $ python tools/approvals.py --approve apr-1 --as support_lead
    $ python tools/approvals.py --reject apr-1 --as support_lead --reason "…"
    $ python tools/approvals.py --apply apr-1 --as executor
    $ python tools/approvals.py --verify

**Why a CLI rather than `POST /approvals/{id}/decide`.** `server.py` has no
authentication, no session, no agent route and no `principal` header: the
`principal` is a string the caller supplies, and Day 26 recorded that as
*authorization without authentication*. Yesterday the same string could read a row;
today it could be the person who signs off on exporting a customer list. Adding the
decision route would place the single most valuable call in the system behind the
least protected surface in the system — a regression dressed as a feature. So the
decision surface is a shell command, whose operator is whoever already has the
repository, and today's approvals are attributable by accident: a shell history, a
git author, a filesystem ACL. That accident is a real property and it is worth more
than a convenient API. Day 29 owns the authenticated surface where a decision route
belongs, and it will have to re-earn the attribute this absence provides for free.

Read-only `GET /approvals` and `GET /approvals/{id}` **do** exist (inspect, not
decide), with argument values masked by default.

Every mutating subcommand **exits non-zero on a refusal** and prints the refusal
code and the record's state, so a person's tooling and a person's shell both
notice. Exit codes: `0` success · `1` refused (a coded refusal from the desk, or an
unknown request id) · `2` the attempt is not a record at all (no actor, no reason —
the two `ValueError`s) or a usage error.

Cost: a local store read/write and, on `--verify`, one walk of the audit chain.
**0 chats, 0 embeds, 0 SQL, 0 rows** — no part of the approval path calls a model,
which is a design requirement with a cost consequence: asking a model to summarize
the action would make the human's view nicer and would hand the model the pen on
the artifact a person signs, so the rendering is canonical and the typography is
worse on purpose.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_os import approval as approval_mod  # noqa: E402
from ai_os import audit as audit_mod        # noqa: E402
from ai_os import permissions as perms      # noqa: E402

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_NOT_A_RECORD = 2


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="approvals", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    what = ap.add_mutually_exclusive_group(required=True)
    what.add_argument("--list", action="store_true",
                      help="the queue: pending first, then decided (metadata only)")
    what.add_argument("--show", metavar="ID",
                      help="the canonical rendering (digest first, values included)")
    what.add_argument("--approve", metavar="ID", help="approve a request, attributed")
    what.add_argument("--reject", metavar="ID", help="reject a request, with a reason")
    what.add_argument("--apply", metavar="ID",
                      help="apply a staged effect as the executor principal")
    what.add_argument("--verify", action="store_true",
                      help="verify the audit chain (entries / chain_ok / first bad seq)")
    ap.add_argument("--as", dest="actor", metavar="PRINCIPAL",
                    help="the acting principal (required by every mutating command)")
    ap.add_argument("--reason", metavar="TEXT", help="required by --reject")
    ap.add_argument("--state", metavar="STATE", help="filter --list by state")
    ap.add_argument("--tool", metavar="TOOL", help="filter --list by tool")
    ap.add_argument("--masked", action="store_true",
                    help="mask argument values even in --show (the GET default)")
    ap.add_argument("--path", metavar="PATH",
                    help="the approvals store (default $APPROVALS_PATH)")
    ap.add_argument("--audit-path", metavar="PATH",
                    help="the audit chain (default $AUDIT_LOG_PATH)")
    return ap


def _audit_log(args: argparse.Namespace) -> audit_mod.AuditLog:
    path = args.audit_path or os.getenv("AUDIT_LOG_PATH", "").strip()
    return audit_mod.AuditLog(path or None)


def _desk(args: argparse.Namespace) -> approval_mod.ApprovalStore:
    """The desk as the CLI sees it: the shipped grants, one chain, a real clock.

    ``Approvals=none`` is *not* honoured here on purpose: the switch turns the
    **gate** off (so no run opens a request), while the reading and deciding surface
    is how a person resolves the requests that already exist. Disabling the surface
    with the gate would strand every open request.
    """
    store = approval_mod.state_store_for_approvals(path=args.path)
    return approval_mod.ApprovalStore(store, permissions=perms.default_permission_set(),
                                      audit=_audit_log(args))


# ------------------------------------------------------------------ renderers

def render_list(records: list, *, state: str | None = None, tool: str | None = None
                ) -> None:
    """Pending first, then decided — and a signal *summary* (keys only, no values).

    A signal value on this surface would be exactly the aggregate the day refuses,
    arriving through the reporting layer; and an argument value would put PII in a
    terminal that is not the approver's.
    """
    rows = list(records)
    if state is not None:
        rows = [r for r in rows if r.state == state]
    if tool is not None:
        rows = [r for r in rows if r.tool == tool]
    pending = [r for r in rows if r.state in (approval_mod.STATE_REQUESTED,
                                              approval_mod.STATE_APPROVED)]
    decided = [r for r in rows if r not in pending]
    if not rows:
        print("(no approval records — expected on the default grants: every gated "
              "client is harness-only and no shipped question touches them)")
        return
    print(f"{'request_id':<14}{'tool':<26}{'state':<13}{'requested_by':<14}"
          f"{'expires_at':<14}{'signals'}")
    print("-" * 100)
    for group, label in ((pending, "PENDING"), (decided, "DECIDED")):
        if not group:
            continue
        print(f"— {label} —")
        for r in sorted(group, key=lambda r: (r.requested_at, r.request_id)):
            print(f"{r.request_id:<14}{r.tool[:24]:<26}{r.state:<13}"
                  f"{r.requested_by[:12]:<14}{r.expires_at:<14.3f}"
                  f"{', '.join(r.signal_keys()) or '—'}"
                  + (f"  [scrutiny: {r.scrutiny()}]"
                     if r.scrutiny() == approval_mod.SCRUTINY_ELEVATED else ""))
    print(f"\ntotal: {len(rows)}  pending: {len(pending)}  "
          f"irreversible: {sum(1 for r in rows if r.reversibility == approval_mod.REVERSIBILITY_IRREVERSIBLE)}")


def render_show(rec: approval_mod.ApprovalRecord, *, masked: bool = False) -> None:
    """The full canonical rendering, digest on the first line, values included.

    The approver is the one reader entitled to the values (which orders are about
    to be exported), and the digest is printed above any prose so the bytes are read
    before the explanation. The one prose field — `proposal` — is labelled untrusted
    in the rendering itself, because that label is the only thing standing between a
    model-authored paragraph and a person's signature.
    """
    print(approval_mod.render_subject(rec, values=not masked))
    print(f"\nrecord: {rec.request_id}  attributable: {rec.attributable()}  "
          f"provenance: {rec.provenance.get('source')}/"
          f"{rec.provenance.get('run_id')}/{rec.provenance.get('step')}")


# ------------------------------------------------------------------- commands

def cmd_verify(args: argparse.Namespace) -> int:
    log = _audit_log(args)
    verdict = log.verify()
    codes: dict[str, int] = {}
    for entry in log.entries():
        code = str(entry.get("code") or "?")
        if code in audit_mod.APPROVAL_EVENTS:
            codes[code] = codes.get(code, 0) + 1
    print(f"path: {log.path}")
    print(f"entries: {verdict['entries']}  chain_ok: {verdict['chain_ok']}  "
          f"first_broken_seq: {verdict['first_broken_seq']}")
    print("approval events: " + (", ".join(f"{k}={v}" for k, v in sorted(codes.items()))
                                 or "(none)"))
    print("NOTE: a chain is tamper-evident for edits, reorders and interior deletions, "
          "and blind to truncation of its own tail — the external anchor is Day 29's.")
    return EXIT_OK if verdict["chain_ok"] else EXIT_REFUSED


def cmd_list(args: argparse.Namespace) -> int:
    render_list(_desk(args).all_records(), state=args.state, tool=args.tool)
    return EXIT_OK


def cmd_show(args: argparse.Namespace) -> int:
    rec = _desk(args).get(args.show)
    if rec is None:
        print(f"no approval record {args.show!r}", file=sys.stderr)
        return EXIT_REFUSED
    render_show(rec, masked=args.masked)
    return EXIT_OK


def _mutate(args: argparse.Namespace, fn) -> int:
    """Run one mutating command and honour the two failure *kinds*.

    A refusal (`ApprovalRefusal`) is a record: print the code and the state, exit 1.
    A `ValueError` is an attempt this system may not write down at all (no actor, no
    reason): print it plainly and exit 2. The split matters to a script, which must
    be able to tell "the desk said no" from "you did not ask properly".
    """
    if not args.actor:
        print(f"{args.command_hint} requires --as <principal>: an unattributable "
              "decision is not a record", file=sys.stderr)
        return EXIT_NOT_A_RECORD
    desk = _desk(args)
    try:
        rec = fn(desk)
    except approval_mod.ApprovalRefusal as refusal:
        current = desk.get(refusal.request_id) if refusal.request_id else None
        state = (current.state if current else refusal.state) or "—"
        print(f"REFUSED {refusal.code}  request: {refusal.request_id or '—'}  "
              f"state: {state}", file=sys.stderr)
        print(str(refusal), file=sys.stderr)
        return EXIT_REFUSED
    except ValueError as exc:                       # no actor / no reason on a reject
        print(f"NOT RECORDED: {exc}", file=sys.stderr)
        return EXIT_NOT_A_RECORD
    render_show(rec)
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list:
        args.command_hint = "--list"
        return cmd_list(args)
    if args.verify:
        return cmd_verify(args)
    if args.show:
        return cmd_show(args)
    args.command_hint = ("--approve" if args.approve else
                         "--reject" if args.reject else "--apply")
    if args.approve:
        return _mutate(args, lambda desk: desk.decide(
            args.approve, decided_by=args.actor, approve=True))
    if args.reject:
        return _mutate(args, lambda desk: desk.decide(
            args.reject, decided_by=args.actor, approve=False, reason=args.reason))
    return _mutate(args, lambda desk: desk.apply(args.apply, applied_by=args.actor))


if __name__ == "__main__":
    sys.exit(main())
