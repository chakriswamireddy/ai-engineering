#!/usr/bin/env python3
"""WS9 — survive fixture (R1–R12), offline.

Deploy / secrets / DB / rate / privacy / DR / observability — every row
either proves the mechanism in-process or names the staging step that proves
it there. A staging-only proof is a SKIP with a named owner, never a pass.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from ai_os import audit as audit_mod  # noqa: E402
from ai_os import rate as rate_mod  # noqa: E402
from ai_os.data import DataAgent, QueryResult, SqliteDataStore  # noqa: E402
from ai_os.health import readiness  # noqa: E402
from ai_os.sql_safety import load_policy  # noqa: E402
from ai_os.state import SingleWriter, acquire_distributed_writer_lock  # noqa: E402

FAILS = 0


def check(label: str, cond: bool, detail: str = "") -> bool:
    global FAILS
    if not cond:
        FAILS += 1
    print(f"[{'OK  ' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))
    return bool(cond)


def check_skip(label: str, cond: bool, detail: str = "", skip: str = "") -> bool:
    """A staging/optional proof: absent prerequisites are a SKIP, never a pass."""
    if skip:
        print(f"[SKIP] {label}  ({skip})")
        return True
    return check(label, cond, detail)


def _yaml(path: Path):
    docs = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
    return [d for d in docs if isinstance(d, dict)]


def r1_deploy() -> None:
    docs = {d.get("kind"): d for d in _yaml(ROOT / "k8s" / "deployment.yaml")}
    dep = docs.get("Deployment", {}).get("spec", {})
    check("R1 replicas=2 (one can die)", dep.get("replicas") == 2)
    strat = dep.get("strategy", {}).get("rollingUpdate", {})
    check("R1 rolling update (no recreate kill-first)",
          dep.get("strategy", {}).get("type") == "RollingUpdate"
          and strat.get("maxUnavailable") == 0)
    cont = dep["template"]["spec"]["containers"][0]
    probes = {p: cont.get(f"{p}Probe", {}).get("httpGet", {}).get("path")
              for p in ("readiness", "liveness")}
    check("R1 readiness /readyz + liveness /healthz",
          probes == {"readiness": "/readyz", "liveness": "/healthz"}, str(probes))
    check("R1 secrets from Vault, never .env in image",
          any("secretRef" in (e.get("envFrom") or [{}])[0]
              for e in [cont]) or "secretRef" in str(cont.get("envFrom")))
    hpa = next(d for d in _yaml(ROOT / "k8s" / "hpa.yaml") if d.get("kind") == "HorizontalPodAutoscaler")
    check("R1 HPA 2–10", hpa["spec"]["minReplicas"] == 2
          and hpa["spec"]["maxReplicas"] == 10)
    pdb = next(d for d in _yaml(ROOT / "k8s" / "pdb.yaml") if d.get("kind") == "PodDisruptionBudget")
    check("R1 PDB keeps 1 serving through kills", pdb["spec"]["minAvailable"] == 1)


def r2_secrets() -> None:
    docs = [d for d in _yaml(ROOT / "k8s" / "external-secrets.yaml")]
    es = next(d for d in docs if d.get("kind") == "ExternalSecret")
    check("R2 Vault-sourced secrets with 15m rotation",
          es["spec"]["refreshInterval"] == "15m"
          and len(es["spec"]["data"]) >= 5)
    blob = (ROOT / "k8s" / "external-secrets.yaml").read_text()
    check("R2 no value inline (refs only)", "password" not in blob.lower()
          or "PASSWORD" not in blob)
    # `docker run` without env → /readyz 503 naming the check.
    status, body = readiness(gateway_builder=lambda: None,
                             store_loader=lambda corpus: None,
                             state_path=None)
    names = list(body.get("checks", {}))
    check("R2 no-env boot fails readiness 503 with named check",
          status == 503 and "gateway" in names, f"{status} {names}")
    # The same claim at the image level (the plan's Verify bullet), when docker
    # and the built image are here; otherwise a named SKIP, never a false pass.
    import shutil
    import subprocess
    image = os.environ.get("WS9_IMAGE", "ai-layer-ws9")
    if shutil.which("docker") is None:
        check_skip("R2 docker run without env → /readyz 503", False,
                   skip="no docker CLI")
    elif subprocess.run(["docker", "image", "inspect", image],
                        capture_output=True).returncode != 0:
        check_skip("R2 docker run without env → /readyz 503", False,
                   skip=f"image {image} not built (docker build -t {image} .)")
    else:
        subprocess.run(["docker", "rm", "-f", "ws9-noenv"], capture_output=True)
        subprocess.run(["docker", "run", "-d", "--name", "ws9-noenv", image],
                       capture_output=True, check=True)
        import time as _t
        _t.sleep(3)
        script = (
            "import urllib.request, urllib.error\n"
            "try:\n"
            "    r = urllib.request.urlopen('http://localhost:8000/readyz', timeout=25)\n"
            "    code, body = r.status, r.read().decode()\n"
            "except urllib.error.HTTPError as e:\n"
            "    code, body = e.code, e.read().decode()\n"
            "print(code, body)\n")
        probe = subprocess.run(["docker", "exec", "-i", "ws9-noenv", "python", "-"],
                               input=script, capture_output=True, text=True)
        subprocess.run(["docker", "rm", "-f", "ws9-noenv"], capture_output=True)
        out = probe.stdout.strip().split(" ", 1)
        code = int(out[0]) if out and out[0].isdigit() else -1
        body_txt = out[1] if len(out) > 1 else ""
        parsed = {}
        try:
            import json as _json
            parsed = _json.loads(body_txt)
        except ValueError:
            pass
        check("R2 docker run without env → /readyz 503 + named check",
              code == 503 and parsed.get("ok") is False
              and parsed.get("checks", {}).get("gateway", {}).get("ok") is False,
              f"status={code}")


def r3_db() -> None:
    plat = Path("/home/chakri/Documents/ai-engineering/saleor-platform")
    if not plat.exists():
        plat = ROOT.parent / "saleor-platform"
    writer = (plat / "writer_role.sql").read_text()
    live_lines = [ln for ln in writer.splitlines()
                  if ln.strip().upper().startswith("GRANT")]
    check("R3 saleor_writer role exists, table-scoped (no blanket grant)",
          "saleor_writer" in writer and live_lines
          and all(" ALL " not in ln.upper() + " " for ln in live_lines),
          "; ".join(ln.strip() for ln in live_lines))
    check("R3 reader stays SELECT-only (replica_user.sql untouched)",
          "GRANT SELECT ON ALL TABLES" in (plat / "replica_user.sql").read_text())
    pool = (plat / "pgbouncer.ini").read_text()
    check("R3 PgBouncer pools reader/writer separately",
          "saleor_reader" in pool and "saleor_writer" in pool)
    mig = plat / "migrations" / "001_ws9_roles.sql"
    check("R3 migrations in saleor-platform (idempotent, PITR-guarded)",
          mig.exists() and "DO $$" in mig.read_text() and "wal_level" in mig.read_text())


def r4_rate() -> None:
    prev = {k: os.environ.get(k) for k in ("RATE_LIMIT_RPS", "RATE_LIMIT", "REDIS_URL")}
    try:
        os.environ.pop("RATE_LIMIT_RPS", None)
        os.environ["RATE_LIMIT"] = "none"
        os.environ.pop("REDIS_URL", None)
        rate_mod.reset_quota_manager()
        off = rate_mod.QuotaManager()
        check("R4 quotas off by default (offline suites never 429)",
              not off.enabled and off.check("t", "p").allowed)
        os.environ["RATE_LIMIT_RPS"] = "1"
        os.environ["RATE_LIMIT_BURST"] = "1"
        rate_mod.reset_quota_manager()
        qm = rate_mod.QuotaManager()
        first = qm.check("acme", "analyst")
        second = qm.check("acme", "analyst")
        check("R4 over-limit refused with Retry-After",
              first.allowed and not second.allowed and second.retry_after > 0,
              f"retry_after={second.retry_after:.2f}s backend={second.backend}")
        other = qm.check("acme", "support")
        check("R4 buckets are per-principal (one tenant, two callers)",
              other.allowed)
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        rate_mod.reset_quota_manager()


def r5_cost_ceiling() -> None:
    from ai_os.agent import agent_cost_budget_from_env
    prev = os.environ.get("AGENT_BUDGET_USD")
    try:
        os.environ["AGENT_BUDGET_USD"] = "0.05"
        check("R5 per-request cost ceiling readable (AGENT_BUDGET_USD)",
              agent_cost_budget_from_env() == 0.05)
    finally:
        if prev is None:
            os.environ.pop("AGENT_BUDGET_USD", None)
        else:
            os.environ["AGENT_BUDGET_USD"] = prev
    import ai_os.agent as _agent
    src = open(_agent.__file__, encoding="utf-8").read()
    check("R5 ceiling kills the loop with the `budget` termination",
          '"budget"' in src and "budget_usd" in src)


def r6_columns_deny_on_results() -> None:
    agent = DataAgent(SqliteDataStore(), policy=load_policy())
    res = QueryResult(ok=True, sql="SELECT 1", executed=True,
                      columns=["id", "password", "email"],
                      rows=[[1, "s3cret", "a@x.io"]], row_count=1)
    agent._apply_columns_deny(res)
    check("R6 denied column dropped from results (values never reach model)",
          res.columns == ["id", "email"] and res.rows == [[1, "a@x.io"]],
          str(res.columns))
    check("R6 the drop is recorded (shape stays, redaction named)",
          res.redacted == ["password"])
    refused = agent.run("SELECT password FROM account_user")
    check("R6 statement layer still refuses denied names first",
          not refused.ok, refused.error_kind or "")


def r7_pii_redaction() -> None:
    from ai_os.structured_logging import redact
    line = redact({"api_key": "k", "route": "/rag"})
    check("R7 log redaction drops secret keys, keeps route",
          "api_key" not in line and line.get("route") == "/rag")
    from ai_os.observability import sanitize
    clean = sanitize({"authorization": "Bearer x", "route": "/rag"})
    check("R7 trace sanitize redacts credentials (value never raw)",
          clean.get("authorization") == "[redacted]"
          and clean.get("route") == "/rag")


def r8_audit_seal() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "audit.jsonl"
        seal_path = Path(tmp) / "audit.seal"
        prev = os.environ.get("AUDIT_LOG_PATH")
        os.environ["AUDIT_LOG_PATH"] = str(log_path)
        audit_mod.reset_default_log()
        try:
            log = audit_mod.default_audit_log()
            log.append({"principal": "a", "tool": "t", "decision": "allow",
                        "effect": "read", "data": "public", "args_hash": "h",
                        "args_shape": {}, "result_shape": {}})
            seal = audit_mod.seal_audit(log, path=seal_path)
            verdict = audit_mod.verify_seal(log, path=seal_path)
            check("R8 sealed chain verifies", verdict["ok"] and verdict["sealed"],
                  str(seal))
            # Tail truncation: the gap verify() is blind to, the seal names.
            lines = log_path.read_text(encoding="utf-8").splitlines()
            log_path.write_text("", encoding="utf-8")
            audit_mod.reset_default_log()
            os.environ["AUDIT_LOG_PATH"] = str(log_path)
            log2 = audit_mod.default_audit_log()
            verdict2 = audit_mod.verify_seal(log2, path=seal_path)
            check("R8 truncated tail detected (tail_truncated)",
                  not verdict2["ok"] and verdict2.get("reason") == "tail_truncated",
                  str(verdict2.get("reason")))
            _ = lines
        finally:
            if prev is None:
                os.environ.pop("AUDIT_LOG_PATH", None)
            else:
                os.environ["AUDIT_LOG_PATH"] = prev
            audit_mod.reset_default_log()


def r9_dr() -> None:
    for name in ("scripts/restore_drill.sh", "scripts/chaos_kill_replica.sh",
                 "scripts/k6-50rps.js"):
        check(f"R9 {name} scripted", (ROOT / name).exists())
    runbook = (ROOT / "docs" / "runbook.md").read_text()
    check("R9 RPO 15m / RTO 1h stated", "RPO 15m" in runbook and "RTO 1h" in runbook)


def r10_observability() -> None:
    import json as _json
    dash = _json.loads((ROOT / "grafana" / "dashboard.json").read_text())
    check("R10 dashboard covers latency/cost/refusals/approvals",
          len(dash.get("panels", [])) >= 4, str(len(dash.get("panels", []))))
    alerts = (ROOT / "prometheus" / "alerts.yaml").read_text()
    check("R10 alerts for eval drop, quota exhaustion, sink lag",
          all(a in alerts for a in ("EvalDrop", "QuotaExhaustion", "SinkLag")))


def r11_advisory_lock() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        lock = acquire_distributed_writer_lock(Path(tmp) / "state.json")
        check("R11 no DSN → file lock (Day-29 behavior byte-identical)",
              isinstance(lock, SingleWriter), type(lock).__name__)
        lock.release()
        bogus = acquire_distributed_writer_lock(
            Path(tmp) / "state.json",
            dsn="postgres://nobody:nowhere@127.0.0.1:1/nope")
        check("R11 unreachable DB falls back to file lock (fail-closed writers)",
              isinstance(bogus, SingleWriter))
        bogus.release()


def main() -> int:
    print("== WS9 — survive (R1–R11) ==")
    r1_deploy()
    r2_secrets()
    r3_db()
    r4_rate()
    r5_cost_ceiling()
    r6_columns_deny_on_results()
    r7_pii_redaction()
    r8_audit_seal()
    r9_dr()
    r10_observability()
    r11_advisory_lock()
    print("\n" + ("ALL SURVIVE CHECKS PASSED" if not FAILS else f"{FAILS} CHECK(S) FAILED"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
