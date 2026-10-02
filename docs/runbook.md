# WS9 Runbook — SLOs, alerts, DR (the exit criteria, written down)

## SLO / error budget

| Signal | SLO | Alert | Owner |
|---|---|---|---|
| request latency p95 by route | < 2s | `RouteP95High` (10m) | WS9 |
| nightly `eval_harness --live --sample` pass rate | ≥ 0.95 | `EvalDrop` (15m) | WS6 |
| quota refusals | < 50 / 5m | `QuotaExhaustion` | WS9 |
| trace sink write lag | < 5m | `SinkLag` | WS7 |

Alert definitions: `prometheus/alerts.yaml`. Dashboard: `grafana/dashboard.json`
(panels read the WS7 series: `route_latency_ms`, `ledger_cost_usd_total`,
`span_errors_total`; approval queue age is scraped from `GET /approvals`,
never a metric label).

## Rate/abuse

Per-tenant/principal token buckets (`ai_os/rate.py`): Redis when `REDIS_URL`
is set (replicas share the quota), in-memory fallback otherwise. Over-limit is
429 with `Retry-After` (`RATE_LIMIT_RPS`, `RATE_LIMIT_BURST`; `RATE_LIMIT=none`
disables — the offline default). Cost ceiling per request is `AGENT_BUDGET_USD`,
which kills the loop with the `budget` termination before money runs out.

## Trace sink / retention

`TRACE_SINK=file|postgres|off` (`ai_os/trace_sink.py`): file JSONL offline,
TimescaleDB via `TRACE_SINK_DSN` in staging. Retention job keeps 30d raw /
90d aggregates (`run_retention()`); `GET /traces/{id}` resolves after restart
through the sink fallback. `SinkLag` fires when the S6 window goes blind.

## Secrets

Vault via External Secrets (`k8s/external-secrets.yaml`, 15m rotation); the
image carries no `.env` (`docs/deployment.yaml` `absent` asserts it at build).
Missing value ⇒ `/readyz` 503 naming the check (survive_smoke S2 proves it:
`docker run` without env fails readiness, loudly).

## DR — RPO 15m / RTO 1h

- RPO 15m: managed-Postgres WAL archiving (7d PITR window) + hourly base backups.
- RTO 1h: `scripts/restore_drill.sh` (restore base → replay WAL → row counts →
  audit seal verify → `/readyz` → `demo_smoke --regress`). Run monthly in staging.
- Audit anchor: `seal_audit()` after every verified backup (`AUDIT_SEAL_PATH`,
  KMS object in staging); `verify_seal()` detects tail truncation and
  head rewrites — the gap `verify()` is structurally blind to.
- Chaos: `scripts/chaos_kill_replica.sh` kills 1 replica mid-demo (PDB keeps 1
  serving; advisory lock means one writer). Expect zero data loss; approval
  replay is idempotent by construction (apply_smoke F3).
- Load: `scripts/k6-50rps.js` — 50 rps sustained 5m, p95 < 2s, zero data loss,
  `demo_smoke --regress` green after. Staging runs it; the script is the proof.

## Privacy

`columns_deny` is enforced on *results* (`DataAgent._apply_columns_deny`), not
just statements: denied values never reach the model, the shape stays, the drop
is recorded in `result.redacted`. PII redaction in traces/logs is the Day-23/24
sanitize path (unchanged). Encryption at rest is the managed-DB + volume
default, stated here so nobody re-proves it per deploy.
