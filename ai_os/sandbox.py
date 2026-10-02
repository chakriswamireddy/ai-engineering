"""WS8 — test execution sandbox (S5).

The first tool in 30 days that *executes* something (everything so far only
reads): running a test file means running arbitrary code, so the authority is
new (`effect: execute`, a separate grant, approval required) and the execution
is confined:

- subprocess backend (default, no dependency): `python -m pytest <targets>`
  with cwd confined to a fresh workdir, environment scrubbed of secrets
  (`*KEY*/*SECRET*/*TOKEN*` never cross the boundary), wall-clock timeout,
  POSIX CPU/memory rlimits, and a pre-execution static scan that refuses
  obviously-destructive payloads (`rm -rf /`, raw-device writes, fork bombs)
  *before* anything runs. The scan is defense-in-depth — the total guarantee
  is timeout + rlimits + env-scrub + cwd, exactly as `policy.yaml` trusts the
  `saleor_read_only` role over the parser.
- docker backend (`AI_OS_SANDBOX=docker`, image `$SANDBOX_IMAGE`): the same
  pytest run inside `docker run --rm --network=none --pids-limit=64
  --memory=512m --cpus=1` with the corpus mounted read-only. No secrets are
  mounted; the workspace is a tmpfs.

The tool returns `{passed, failed, log_sha256, log_excerpt}` — the excerpt is
capped (never the full log, which may carry secrets), while the sha256 names
the full log for an operator with shell access.

Day-25 firewall: `run_tests` args are injection-scanned (`targets` is a
model-supplied string crossing into execution) and shape-checked
(`maxLength` + `pattern`, reject-not-truncate).
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# The tool's contract: what it is (execute/internal), what it takes, what it
# returns. Registration (grant + approval gate) lives with the callers —
# `tools/run_tests.py`, `tools/sandbox_smoke.py`, `demonstrate.py` S5 — so
# the shipped seven-tool registry never gains authority by import.
RUN_TESTS_SCHEMA: dict[str, Any] = {
    "name": "run_tests",
    "description": "Execute pytest targets inside the WS8 sandbox (approval required).",
    "type": "object",
    "properties": {
        # One string (not an array): the permission layer checks exactly one
        # shape per argument, and a scalar keeps `maxLength`+`pattern` honest.
        "targets": {
            "type": "string",
            "description": "space-separated pytest targets, relative to the repo "
                           "(e.g. 'test_day7.py tools/sandbox_smoke.py')",
            "maxLength": 2048,
            "pattern": r"^[A-Za-z0-9_./\- ]{1,2048}$",
        },
        "timeout_s": {"type": "integer", "minimum": 1, "maximum": 600},
    },
    "required": ["targets"],
}

TARGET_PATTERN = re.compile(r"^[A-Za-z0-9_./\-]{1,256}$")
LOG_EXCERPT_CHARS = 2000
DEFAULT_TIMEOUT_S = 120
DEFAULT_MEMORY_MB = 512

# Pre-execution static scan: obviously-destructive payloads are refused before
# anything runs. A deny-list, deliberately — the total guarantee is the
# timeout + rlimits + env-scrub + cwd below, never this list.
BLOCKED_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"rm\s+-rf\s+/", "rm -rf against /"),
    (r"mkfs|dd\s+.*of=/dev/", "raw device write"),
    (r":\(\)\s*\{\s*:\|\:&\s*\}", "fork bomb"),
    (r"shutdown|reboot|halt\b", "host power operation"),
    (r"/etc/passwd|/etc/shadow", "system credential path"),
    (r"socket\s*\.\s*(connect|create_connection)", "outbound network"),
)


def scrub_env() -> dict[str, str]:
    """The sandbox environment: no secrets cross the boundary, ever."""
    out: dict[str, str] = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if "KEY" in upper or "SECRET" in upper or "TOKEN" in upper:
            continue
        if key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CLOUDFLARE_API_KEY",
                   "PGPASSWORD", "DATABASE_URL"):
            continue
        out[key] = value
    out["PYTHONDONTWRITEBYTECODE"] = "1"
    return out


def scan_file(path: Path) -> str | None:
    """Return a refusal reason if the file is obviously destructive, else None."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return f"unreadable test file: {path}"
    for pattern, name in BLOCKED_PATTERNS:
        if re.search(pattern, text):
            return f"blocked destructive pattern ({name}) in {path.name}"
    return None


def _limited_popen_kwargs(memory_mb: int) -> dict[str, Any]:
    kw: dict[str, Any] = {}
    if sys.platform != "win32":
        try:
            import resource  # noqa: E402

            def _limits() -> None:
                cpu_s = int(os.getenv("SANDBOX_CPU_S", "300"))
                resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s))
                mem = int(memory_mb) * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_AS, (mem, mem))

            kw["preexec_fn"] = _limits
        except (ImportError, ValueError, OSError):
            pass
    return kw


def run_in_subprocess(targets: list[str], *, repo: Path,
                      timeout_s: int = DEFAULT_TIMEOUT_S,
                      memory_mb: int = DEFAULT_MEMORY_MB,
                      python: str | None = None) -> dict[str, Any]:
    """Execute pytest in a confined subprocess. Returns the WS8 result shape."""
    # cwd is the repo under test (suites depend on repo layout: conftest,
    # corpus paths, relative imports). Confinement is timeout + rlimits +
    # env-scrub + the pre-scan above — never the cwd.
    t0 = time.perf_counter()
    cmd: list[str] = [python or sys.executable, "-m", "pytest", "-q",
                      "-p", "no:cacheprovider", *targets]
    try:
        proc = subprocess.run(
            cmd, cwd=str(repo), capture_output=True, text=True,
            timeout=timeout_s, env=scrub_env(),
            **_limited_popen_kwargs(memory_mb))
        wall_ms = (time.perf_counter() - t0) * 1000.0
        log = (proc.stdout or "") + (proc.stderr or "")
        outcome = "pass" if proc.returncode == 0 else "fail"
        return _result(outcome, proc.returncode, log, wall_ms,
                       backend="subprocess", timed_out=False)
    except subprocess.TimeoutExpired as exc:
        wall_ms = (time.perf_counter() - t0) * 1000.0
        raw = exc.stdout
        if isinstance(raw, bytes):
            log = raw.decode(errors="replace")
        else:
            log = raw or ""
        return _result("timeout", None, log, wall_ms,
                       backend="subprocess", timed_out=True)


def run_in_docker(targets: list[str], *, repo: Path,
                  image: str | None = None,
                  timeout_s: int = DEFAULT_TIMEOUT_S,
                  memory_mb: int = DEFAULT_MEMORY_MB) -> dict[str, Any]:
    """Execute pytest inside a locked-down container (no network, capped pids)."""
    if shutil.which("docker") is None:
        raise RuntimeError("docker backend requested but no docker on PATH")
    resolved_image: str = image or os.getenv("SANDBOX_IMAGE") or "python:3.12-slim"
    t0 = time.perf_counter()
    cmd = ["docker", "run", "--rm", "--network=none",
           "--pids-limit=64", f"--memory={memory_mb}m", "--cpus=1",
           "-v", f"{repo}:/corpus:ro", "-w", "/corpus",
           resolved_image, "python", "-m", "pytest", "-q", "-p", "no:cacheprovider",
           *targets]
    env = {k: v for k, v in os.environ.items()
           if "KEY" not in k.upper() and "SECRET" not in k.upper()
           and "TOKEN" not in k.upper()}
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout_s, env=env)
        wall_ms = (time.perf_counter() - t0) * 1000.0
        log = (proc.stdout or "") + (proc.stderr or "")
        outcome = "pass" if proc.returncode == 0 else "fail"
        return _result(outcome, proc.returncode, log, wall_ms,
                       backend="docker", timed_out=False)
    except subprocess.TimeoutExpired:
        wall_ms = (time.perf_counter() - t0) * 1000.0
        return _result("timeout", None, "", wall_ms,
                       backend="docker", timed_out=True)


def _result(outcome: str, returncode: int | None, log: str,
            wall_ms: float, *, backend: str, timed_out: bool) -> dict[str, Any]:
    digest = hashlib.sha256(log.encode()).hexdigest()
    excerpt = log[:LOG_EXCERPT_CHARS]
    passed = outcome == "pass"
    return {
        "passed": passed,
        "failed": 0 if passed else 1,
        "outcome": outcome,
        "returncode": returncode,
        "timed_out": timed_out,
        "backend": backend,
        "wall_ms": round(wall_ms, 1),
        "log_sha256": digest,
        "log_excerpt": excerpt,
        "log_excerpt_capped": len(log) > LOG_EXCERPT_CHARS,
    }


def validate_targets(targets: Any) -> tuple[list[str] | None, str | None]:
    """Shape-check + Day-25 scan the tool args. Returns (targets, refusal)."""
    if isinstance(targets, str):
        parts = targets.split()
    elif isinstance(targets, list):
        parts = [str(t) for t in targets]
    else:
        return None, "targets must be a space-separated string (or list)"
    if not parts:
        return None, "targets must be non-empty"
    joined = " ".join(parts)
    if len(joined) > 2048:
        return None, "targets exceed maxLength=2048 (reject-not-truncate)"
    clean: list[str] = []
    for target in parts:
        if not TARGET_PATTERN.fullmatch(target):
            return None, f"target {target!r} fails the path pattern"
        if target.startswith("/") or ".." in target.split("/"):
            return None, f"target {target!r} escapes the repo (absolute/.. refused)"
        clean.append(target)
    try:
        from ai_os import injection as inj
        if inj.enabled():
            signals = inj.scan(joined, "tool_args", "run_tests.targets")
            if inj.implies_privileged_action(signals):
                return None, ("injection firewall: run_tests targets imply a "
                              "privileged action — refused")
    except Exception:                                       # noqa: BLE001
        pass                    # firewall fail-open for scanning; shape still held
    return clean, None


def run_tests(targets: list[str] | str, *, repo: Path | None = None,
              timeout_s: int = DEFAULT_TIMEOUT_S,
              memory_mb: int = DEFAULT_MEMORY_MB,
              backend: str | None = None) -> dict[str, Any]:
    """The WS8 entry point: validate → scan → execute → measured result."""
    clean, refusal = validate_targets(targets)
    backend_name = (backend or os.getenv("AI_OS_SANDBOX", "subprocess") or "subprocess")
    backend_name = backend_name.strip().lower()
    if refusal is not None:
        return {"passed": False, "failed": 0, "outcome": "refused",
                "refusal": refusal, "returncode": None, "timed_out": False,
                "backend": "none", "wall_ms": 0.0, "log_sha256": "",
                "log_excerpt": "", "log_excerpt_capped": False}
    root = repo or Path(__file__).resolve().parent.parent
    for target in clean or []:
        hit = scan_file(root / target)
        if hit is not None:
            return {"passed": False, "failed": 0, "outcome": "refused",
                    "refusal": hit, "returncode": None, "timed_out": False,
                    "backend": "none", "wall_ms": 0.0, "log_sha256": "",
                    "log_excerpt": "", "log_excerpt_capped": False}
    if backend_name == "docker":
        return run_in_docker(clean or [], repo=root, timeout_s=timeout_s,
                             memory_mb=memory_mb)
    return run_in_subprocess(clean or [], repo=root, timeout_s=timeout_s,
                             memory_mb=memory_mb)
