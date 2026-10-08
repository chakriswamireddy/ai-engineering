"""WS1 — mint an HS256 access token for the guarded surface. A CLI, on purpose.

    $ python tools/mint_token.py --gen-secret            # one-time setup
    $ export OIDC_HS256_SECRET=<the secret above>
    $ python tools/mint_token.py --sub alice --roles analyst
    $ curl -H "Authorization: Bearer $TOKEN" localhost:8023/chat -d '{…}'

**Why this exists.** `ai_os/identity.py` verifies `Authorization: Bearer`
tokens, but verification without issuance is a lock without a key: the
installation has no IdP (deliberately — one more service to run is one more
service to fail at 3am), so somebody has to be able to mint the first token.
This is that somebody's command.

**Why HS256 and not RS256.** RS256 needs an issuer, a JWKS endpoint and a
clock the verifier trusts. HS256 needs one secret the process already has.
The same `verify_token` path handles both — the algorithm is chosen by the
configured verifier, never by the token's header — so a later move to a real
IdP is a config change (`OIDC_ISSUER`), not a code change.

**The secret is a shared secret, so treat it as one.** It signs every token
the system accepts; `--gen-secret` writes 32 bytes of `secrets.token_urlsafe`
and nothing else. It lives in the deployment env file (mode 600, outside the
image — `docs/deployment.yaml` asserts `.env` is absent), never on the command
line of a shell with a history, never in a repository.

Claims written: `sub` (required), `exp` (required by the verifier), plus
`tenant`/`roles` when given. Roles are *also* read from `permissions.yaml`
at verification time and merged, so this flag can only ever add to the file's
grants, never subtract — a token cannot grant itself more than the file says.

Cost: local computation only. **0 chats, 0 embeds, 0 SQL.**

Exit codes: `0` token printed · `2` usage/config error (no secret, no sub).
"""
from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SECRET_ENV = "OIDC_HS256_SECRET"
DEFAULT_TTL_S = 3600


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="mint_token.py",
        description="Mint an HS256 bearer token for the guarded AI-layer routes.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("\n\n", 1)[1] if "\n\n" in (__doc__ or "") else None)
    ap.add_argument("--gen-secret", action="store_true",
                    help="print a fresh 32-byte shared secret and exit")
    ap.add_argument("--sub", metavar="PRINCIPAL",
                    help="who the token is for (required unless --gen-secret)")
    ap.add_argument("--tenant", metavar="NAME", default="",
                    help="tenant claim; default: the principal's file tenant")
    ap.add_argument("--roles", metavar="R1,R2", default="",
                    help="comma-separated role claims (merged with file roles)")
    ap.add_argument("--ttl", metavar="SECONDS", type=int, default=DEFAULT_TTL_S,
                    help=f"token lifetime (default {DEFAULT_TTL_S}s)")
    ap.add_argument("--issuer", metavar="ISS", default="",
                    help="optional iss claim (must match OIDC_ISSUER if set)")
    ap.add_argument("--audience", metavar="AUD", default="",
                    help="optional aud claim (must match OIDC_AUDIENCE if set)")
    ap.add_argument("--secret", metavar="SECRET", default="",
                    help=f"signing secret (default: ${SECRET_ENV})")
    ap.add_argument("--quiet", action="store_true",
                    help="print only the token, no stderr summary")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.gen_secret:
        print(secrets.token_urlsafe(32))
        return 0

    if not args.sub:
        print("error: --sub is required (or use --gen-secret)", file=sys.stderr)
        return 2

    secret = args.secret or os.getenv(SECRET_ENV, "").strip()
    if not secret:
        print(f"error: no signing secret — pass --secret or set ${SECRET_ENV}\n"
              f"       (generate one with: python tools/mint_token.py --gen-secret)",
              file=sys.stderr)
        return 2
    if args.ttl <= 0:
        print("error: --ttl must be a positive number of seconds", file=sys.stderr)
        return 2

    # The actual signing lives in ai_os.identity.mint_token — the same
    # function the server's POST /ui/login uses — so there is exactly one
    # writer of claims and one reader of the shared secret (CLI + login can
    # never drift apart). This CLI keeps only arg validation and its exit
    # codes above.
    from ai_os.identity import mint_token

    try:
        token = mint_token(args.sub, ttl_s=args.ttl, tenant=args.tenant,
                           roles=[r.strip() for r in args.roles.split(",")
                                  if r.strip()],
                           issuer=args.issuer, audience=args.audience,
                           secret=secret)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not args.quiet:
        print(f"minted: sub={args.sub} ttl={args.ttl}s "
              f"tenant={args.tenant or '-'} roles={args.roles or '-'}",
              file=sys.stderr)
    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
