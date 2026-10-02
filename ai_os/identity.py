"""WS1 — request identity: OIDC JWT → Principal{id, tenant, roles}.

Day 26 called the caller-supplied `principal` string *authorization without
authentication*; Day 27 refused to add a decision route on top of it. This
module is the other side of both: a verified identity resolved from an OIDC
JWT (`Authorization: Bearer`), carried as a frozen `Principal`, and enforced
at the HTTP seam by `server.py`.

Seams, kept small on purpose:

- `Principal` — `{id, tenant, roles, auth}`. Frozen; `id` is the OIDC `sub`
  (never a client string), `tenant` scopes reads, `roles` travels for WS2
  route use. This is the *request identity*; `permissions.Principal` (name →
  tools) stays the *grant assignment* — the two meet in `coerce()`, never by
  sharing a class.
- `verify_token` — RS256 via the issuer's JWKS (`OIDC_JWKS_URL` or
  `{issuer}/.well-known/jwks.json`), or HS256 via `OIDC_HS256_SECRET` — the
  deploy-time shared secret for installations with no IdP (`OIDC_TEST_SECRET`
  is the offline-suite alias for the same branch). Anything else fails closed.
- `request_principal` — lenient (never raises; anonymous on missing/invalid
  token) for the middleware, which stamps `request.state`. `require_auth`
  is the strict one the guarded routes call: anonymous + `ALLOW_ANON=0` →
  `AuthError` (401).
- `coerce` — `Principal | str | None → Principal`, so `run_agent`,
  `executor.run_plan` and the approval seams accept the request identity
  while every offline caller passing a string keeps working byte-for-byte
  behind `ALLOW_ANON=1`.

0 chats, 0 embeds, 0 SQL. The only I/O is the JWKS fetch, cached for one
hour; every failure mode is an `AuthError` naming its reason.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping

OIDC_ISSUER_ENV = "OIDC_ISSUER"
OIDC_AUDIENCE_ENV = "OIDC_AUDIENCE"
OIDC_JWKS_URL_ENV = "OIDC_JWKS_URL"
OIDC_TEST_SECRET_ENV = "OIDC_TEST_SECRET"   # offline suites only
OIDC_HS256_SECRET_ENV = "OIDC_HS256_SECRET"  # production shared secret (no IdP)
ALLOW_ANON_ENV = "ALLOW_ANON"

DEFAULT_TENANT = "default"
ANON_ID = "anonymous"

_OFF_VALUES = {"0", "off", "false", "no", "none", "disabled"}
_JWKS_TTL_S = 3600
_JWKS_TIMEOUT_S = 5


class AuthError(Exception):
    """A denied authentication — carries its HTTP status and machine reason."""

    def __init__(self, reason: str, *, status: int = 401) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status


@dataclass(frozen=True)
class Principal:
    """Who is asking: the verified OIDC `sub`, its tenant, its roles."""

    id: str
    tenant: str = DEFAULT_TENANT
    roles: tuple[str, ...] = ()
    auth: str = "none"                      # none | oidc | anon

    @property
    def is_anonymous(self) -> bool:
        return self.auth == "anon" or self.id == ANON_ID

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "tenant": self.tenant,
                "roles": list(self.roles), "auth": self.auth}


ANONYMOUS = Principal(id=ANON_ID, tenant=DEFAULT_TENANT, roles=(), auth="anon")


@dataclass(frozen=True)
class Settings:
    """The OIDC knobs, read once per resolve (tests monkeypatch env freely)."""

    issuer: str = ""
    audience: str = ""
    jwks_url: str = ""
    hs256_secret: str = ""                # OIDC_HS256_SECRET — deploy-time secret
    test_secret: str = ""                 # OIDC_TEST_SECRET — offline suites
    allow_anon: bool = True

    @classmethod
    def from_env(cls) -> "Settings":
        issuer = os.getenv(OIDC_ISSUER_ENV, "").strip()
        return cls(
            issuer=issuer,
            audience=os.getenv(OIDC_AUDIENCE_ENV, "").strip(),
            jwks_url=(os.getenv(OIDC_JWKS_URL_ENV, "").strip()
                      or (f"{issuer.rstrip('/')}/.well-known/jwks.json"
                          if issuer else "")),
            hs256_secret=os.getenv(OIDC_HS256_SECRET_ENV, "").strip(),
            test_secret=os.getenv(OIDC_TEST_SECRET_ENV, ""),
            allow_anon=(os.getenv(ALLOW_ANON_ENV, "1").strip().lower()
                        not in _OFF_VALUES),
        )


def allow_anon() -> bool:
    return Settings.from_env().allow_anon


# ------------------------------------------------------- token verification

_JWKS_CACHE: dict[str, tuple[float, Any]] = {}


def _fetch_jwks(url: str) -> Any:
    """The issuer's key set, cached for one hour. Fetch failure is a denial."""
    from joserfc.jwk import KeySet

    now = time.time()
    hit = _JWKS_CACHE.get(url)
    if hit is not None and now - hit[0] < _JWKS_TTL_S:
        return hit[1]
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=_JWKS_TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:                                    # noqa: BLE001
        raise AuthError(f"jwks_unreachable: {exc}") from exc
    try:
        keys = KeySet.import_key_set(payload)
    except Exception as exc:                                    # noqa: BLE001
        raise AuthError(f"jwks_invalid: {exc}") from exc
    _JWKS_CACHE[url] = (now, keys)
    return keys


def _claims_registry(settings: Settings) -> Any:
    from joserfc import jwt as _jwt

    opts: dict[str, Any] = {"exp": {"essential": True}}
    if settings.issuer:
        opts["iss"] = {"essential": True, "value": settings.issuer}
    if settings.audience:
        opts["aud"] = {"essential": True, "value": settings.audience}
    return _jwt.JWTClaimsRegistry(**opts)


def verify_token(token: str, settings: Settings | None = None) -> dict[str, Any]:
    """Verify a JWT and return its claims. Every failure is an `AuthError`."""
    from joserfc import jwt as _jwt
    from joserfc.errors import JoseError

    settings = settings or Settings.from_env()
    token = (token or "").strip()
    if not token or token.count(".") != 2:
        raise AuthError("malformed_token")
    # The algorithm is chosen by the configured verifier below (RS256 JWKS,
    # then HS256 shared secret) — never by the token's own header.
    registry = _claims_registry(settings)
    last_error: AuthError | None = None
    if settings.jwks_url or settings.issuer:
        url = settings.jwks_url or f"{settings.issuer.rstrip('/')}/.well-known/jwks.json"
        try:
            keys = _fetch_jwks(url)
        except AuthError as exc:
            # The issuer is unreachable: fall through to the HS256 test path
            # when one is configured (offline suites), else deny below.
            last_error = exc
        else:
            try:
                decoded = _jwt.decode(token, keys, algorithms=["RS256"])
                registry.validate(decoded.claims)
                return dict(decoded.claims)
            except AuthError as exc:
                # A token that reaches the issuer's keys and fails is forged
                # or malformed — it must NOT be retried as HS256 (algorithm
                # confusion), so this raises instead of falling through.
                raise exc
            except JoseError as exc:
                raise _auth_error_of(exc)
            except Exception as exc:                            # noqa: BLE001
                raise AuthError(f"invalid_token: {exc}") from exc
    shared_secret = settings.hs256_secret or settings.test_secret
    if shared_secret:
        try:
            from joserfc.jwk import OctKey

            decoded = _jwt.decode(token, OctKey.import_key(shared_secret),
                                  algorithms=["HS256"])
            registry.validate(decoded.claims)
            return dict(decoded.claims)
        except AuthError as exc:
            raise exc
        except JoseError as exc:
            last_error = _auth_error_of(exc)
        except Exception as exc:                                # noqa: BLE001
            last_error = AuthError(f"invalid_token: {exc}")
    raise last_error or AuthError("no_verifier_configured")


def _auth_error_of(exc: Exception) -> AuthError:
    name = exc.__class__.__name__
    if name == "ExpiredTokenError":
        return AuthError("expired_token")
    if name == "BadSignatureError":
        return AuthError("forged_token")
    if name in ("InvalidClaimError", "MissingClaimError", "InsecureClaimError"):
        claim = getattr(exc, "claim_name", None) or getattr(exc, "claim_id", None)
        return AuthError(f"invalid_claim:{claim}" if claim else "invalid_claim")
    return AuthError(f"invalid_token:{name}")


# ------------------------------------------------------- request resolution

def _file_tenant(name: str) -> str:
    try:
        from ai_os import permissions as _perms

        return _perms.default_permission_set().tenant_of(name)
    except Exception:                                           # noqa: BLE001
        return DEFAULT_TENANT


def _file_roles(name: str) -> tuple[str, ...]:
    try:
        from ai_os import permissions as _perms

        return _perms.default_permission_set().roles_of(name)
    except Exception:                                           # noqa: BLE001
        return ()


def principal_from_claims(claims: Mapping[str, Any]) -> Principal:
    """Claims → Principal. `sub` is required; tenant/roles fall back closed."""
    sub = str(claims.get("sub") or "").strip()
    if not sub:
        raise AuthError("missing_sub")
    token_roles = claims.get("roles")
    roles = tuple(str(r) for r in token_roles) if isinstance(token_roles, list) else ()
    tenant = str(claims.get("tenant") or "").strip() or _file_tenant(sub)
    file_roles = _file_roles(sub)
    merged = tuple(dict.fromkeys((*roles, *file_roles)))
    return Principal(id=sub, tenant=tenant or DEFAULT_TENANT,
                     roles=merged, auth="oidc")


def bearer_of(headers: Mapping[str, Any]) -> str:
    auth = ""
    for key, value in dict(headers).items():
        if str(key).lower() == "authorization":
            auth = str(value or "")
            break
    scheme, _, token = auth.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else ""


def request_principal(request: Any,
                      settings: Settings | None = None) -> Principal:
    """Lenient resolve for the middleware: never raises, anonymous on failure.

    Stamps nothing itself — the middleware copies the result onto
    `request.state` (`principal`, `tenant`, `roles`).
    """
    settings = settings or Settings.from_env()
    token = bearer_of(getattr(request, "headers", {}) or {})
    if not token:
        return ANONYMOUS
    try:
        return principal_from_claims(verify_token(token, settings))
    except AuthError:
        return ANONYMOUS


def require_auth(request: Any, *, route: str,
                 settings: Settings | None = None) -> Principal:
    """Strict resolve for guarded routes: anonymous + `ALLOW_ANON=0` → 401."""
    settings = settings or Settings.from_env()
    principal = getattr(getattr(request, "state", None), "principal", None)
    if principal is None:
        principal = request_principal(request, settings)
    if isinstance(principal, Principal) and not principal.is_anonymous:
        return principal
    if settings.allow_anon:
        return ANONYMOUS
    raise AuthError(f"anonymous_denied:{route}")


# ------------------------------------------------------- caller-string bridge

def coerce(principal: "Principal | str | None") -> Principal:
    """`Principal | str | None → Principal` — the WS1 seam for `run_agent`,
    `executor.run_plan` and the approval paths.

    A string keeps its exact historical meaning (grant lookup by name, file
    tenant/roles); `None` is the historical default caller; a `Principal`
    passes through untouched. Nothing downstream changes shape: seams that
    need the name take `.id`.
    """
    if isinstance(principal, Principal):
        return principal
    if principal is None:
        return Principal(id="default", tenant=DEFAULT_TENANT,
                         roles=(), auth="none")
    name = str(principal)
    return Principal(id=name, tenant=_file_tenant(name),
                     roles=_file_roles(name), auth="none")


def principal_id(principal: "Principal | str | None") -> str | None:
    """The grant-lookup name for a coerced principal (`None` stays `None`)."""
    if principal is None:
        return None
    return coerce(principal).id


def tenant_of(principal: "Principal | str | None") -> str:
    if principal is None:
        return DEFAULT_TENANT
    return coerce(principal).tenant or DEFAULT_TENANT


# test + diagnosis helpers (never on a hot path)
_EXTRA_FIELDS = ("roles",)


def describe() -> dict[str, Any]:
    settings = Settings.from_env()
    return {"issuer": settings.issuer or None,
            "audience": settings.audience or None,
            "jwks_url": settings.jwks_url or None,
            "hs256": bool(settings.hs256_secret or settings.test_secret),
            "hs256_test": bool(settings.test_secret),
            "allow_anon": settings.allow_anon,
            "extra_claim_fields": list(_EXTRA_FIELDS)}
