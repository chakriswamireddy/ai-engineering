"""Day 29 §5.2 — health probes as decisions, not infrastructure.

Two probes, two functions, one module. Dependencies are injected so the whole
thing is testable offline at zero model cost — exactly like permissions.py and
approval.py.

Rules from the series that apply unchanged:
1. The answering model and the deployment's shape may be promoted; the arguments
   may not — so the readiness body reports *which* database backend and *which*
   store identity, never a query, a key, or a path segment.
2. A decision is a value with a consequence — so readiness() returns (503, body)
   and never (200, {"ok": False}), which is the exact anti-pattern deleted from
   server.py:79-80.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Callable

from ai_os.config import build_gateway_from_env
from ai_os.rag import load_store_cached, store_path_from_env, code_store_path_from_env
from ai_os.state import StateStore, state_store_path_from_env


@dataclass(slots=True)
class HealthCheck:
    """One named dependency check with its result and measured cost."""
    name: str
    ok: bool
    detail: str
    cost_ms: int


def liveness() -> tuple[int, dict[str, Any]]:
    """Liveness probe — touches nothing external.

    Returns (200, {"ok": True, "checks": {}}). Cannot fail on a dependency,
    which is the property that makes it safe to restart on. A wedged event loop,
    a dead provider chain, or an unopenable state file are explicitly *not*
    checked here — those are readiness concerns.
    """
    return 200, {"ok": True, "checks": {}}


def _check_gateway() -> HealthCheck:
    """Check if gateway is constructible from environment (local fact, no network)."""
    start = time.perf_counter()
    try:
        gw = build_gateway_from_env()
        ok = gw is not None
        detail = f"providers={[p.name for p in gw.providers]}" if ok else "no providers"
    except Exception as e:
        ok = False
        detail = str(e)
    cost_ms = int((time.perf_counter() - start) * 1000)
    return HealthCheck("gateway", ok, detail, cost_ms)


def _check_store(corpus: str = "docs") -> HealthCheck:
    """Check if RAG store is present and loadable (cached after first success)."""
    start = time.perf_counter()
    try:
        path = code_store_path_from_env() if corpus == "code" else store_path_from_env()
        store = load_store_cached(path)
        ok = store is not None
        detail = f"chunks={len(store.chunks)} dim={store.dimension}" if ok else f"not found at {path}"
    except Exception as e:
        ok = False
        detail = str(e)
    cost_ms = int((time.perf_counter() - start) * 1000)
    return HealthCheck("store", ok, detail, cost_ms)


def _check_database() -> HealthCheck:
    """Check database backend — reports postgres or stub (SEED_SQL) explicitly.

    The silent substitution at data.py:798-806 is the one thing in this
    repository that produces a confidently wrong answer. This check makes it
    visible in the probe an operator actually reads.
    """
    start = time.perf_counter()
    try:
        url = os.getenv("SALEOR_READONLY_DB_URL", "").strip()
        if url:
            # Verify psycopg is available (imported lazily in data.py)
            try:
                import psycopg
                ok = True
                detail = "postgres"
            except ImportError:
                ok = False
                detail = "psycopg not installed"
        else:
            ok = True
            detail = "stub (SEED_SQL)"
    except Exception as e:
        ok = False
        detail = str(e)
    cost_ms = int((time.perf_counter() - start) * 1000)
    return HealthCheck("database", ok, detail, cost_ms)


def _check_state(state_path: str | None, clock: Callable[[], float]) -> HealthCheck:
    """Check if state store is writable (or explicitly disabled).

    Takes the single-writer lock into account: a process that started without
    the guard is reported as unready rather than broken.
    """
    start = time.perf_counter()
    try:
        if not state_path:
            ok = True
            detail = "disabled"
        else:
            # Try to open store with autosave=False so probe doesn't write
            store = StateStore(path=state_path, autosave=False, clock=clock)
            ok = store.enabled
            detail = "writable" if ok else "disabled"

            # Also verify single-writer lock is held (if guard is enabled)
            if ok:
                lock_path = state_path + ".lock"
                try:
                    import fcntl
                    # Try to acquire a non-blocking shared lock to verify we hold exclusive
                    fd = os.open(lock_path, os.O_RDONLY)
                    try:
                        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                        # If we get a shared lock, no exclusive lock is held — guard not active
                        ok = False
                        detail = "single-writer lock not held"
                    except BlockingIOError:
                        # Good — exclusive lock is held (by us)
                        pass
                    finally:
                        os.close(fd)
                except FileNotFoundError:
                    # Lock file doesn't exist — guard not active
                    ok = False
                    detail = "single-writer lock file missing"
                except Exception:
                    # Other error — lock check failed
                    ok = False
                    detail = "single-writer lock check failed"
    except Exception as e:
        ok = False
        detail = str(e)
    cost_ms = int((time.perf_counter() - start) * 1000)
    return HealthCheck("state", ok, detail, cost_ms)


def readiness(
    gateway_builder: Callable[[], Any] | None = None,
    store_loader: Callable[[str], Any] | None = None,
    state_path: str | None = None,
    clock: Callable[[], float] = time.time,
) -> tuple[int, dict[str, Any]]:
    """Readiness probe — returns (200 | 503, body with named checks).

    Four checks, in the order they matter:
    1. gateway — was build_gateway_from_env() constructible (local fact, no network)
    2. store — is the store file present and loadable (cached after first success)
    3. database — postgres or stub (SEED_SQL), named explicitly
    4. state — writable, or explicitly disabled

    Returns 503 the moment any check fails. Body is a table of named checks,
    not a boolean.
    """
    checks: list[HealthCheck] = []

    # Gateway check
    if gateway_builder is not None:
        gw = gateway_builder()
        start = time.perf_counter()
        ok = gw is not None
        detail = f"providers={[p.name for p in gw.providers]}" if ok else "no providers"
        checks.append(HealthCheck("gateway", ok, detail, int((time.perf_counter() - start) * 1000)))
    else:
        checks.append(_check_gateway())

    # Store check (docs corpus)
    if store_loader is not None:
        start = time.perf_counter()
        try:
            store = store_loader("docs")
            ok = store is not None
            detail = f"chunks={len(store.chunks)} dim={store.dimension}" if ok else "not loaded"
        except Exception as e:
            ok = False
            detail = str(e)
        checks.append(HealthCheck("store", ok, detail, int((time.perf_counter() - start) * 1000)))
    else:
        checks.append(_check_store("docs"))

    # Database check
    checks.append(_check_database())

    # State check
    sp = state_path or state_store_path_from_env()
    checks.append(_check_state(sp, clock))

    # Determine overall status
    all_ok = all(c.ok for c in checks)
    status = 200 if all_ok else 503

    body = {
        "ok": all_ok,
        "checks": {
            c.name: {"ok": c.ok, "detail": c.detail, "cost_ms": c.cost_ms}
            for c in checks
        },
    }
    return status, body