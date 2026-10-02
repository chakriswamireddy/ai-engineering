# Day 30 → Deployable Product: Developer Implementation Plan

Goal: keep Days 1-30 green while closing the 9 blockers that prevent deployment. Non-goal: no rewrites, no new framework, no quality claims without eval.

Baseline commands (must stay green after every WS):

```bash
python tools/regress_all.py --fast
python tools/demo_smoke.py --regress
python tools/demonstrate.py --json
```

## Sequencing

```text
WS0 baseline
WS1 authN/Z ─┬─> WS2 agent/api routes
             └─> WS4 approval execute
WS3 write path ──> WS4 approval execute ──> WS9 survive
WS5 embeddings ──> WS6 semantic eval
WS7 trace sink ──> WS9 SLOs/alerts
WS8 sandbox (isolated, last)
```

Do not build WS2/WS4 before WS1. Do not multi-replica before WS7 + external state.

## Status (updated at WS9 close-out)

| WS | status | proof |
|----|--------|-------|
| WS0 | **landed** (commit `45130cf`) | `tools/gate_prod.py` |
| WS1 | **landed** | `tools/auth_smoke.py` (U1–U7) |
| WS2 | **landed** | routes + quotas behind `_auth_guard`; same fixture |
| WS3 | **landed** | `tools/write_smoke.py` (W1–W6) |
| WS4 | **landed** | `tools/apply_smoke.py` (F1–F7) |
| WS5 | **landed** (live path needs Ollama) | `tools/code_store_smoke.py` (C1–C5) |
| WS6 | **landed** (live path needs a model) | `tools/semantic_smoke.py` (E1–E6) |
| WS7 | **landed** | `tools/trace_sink_smoke.py` (T1–T6) |
| WS8 | **landed** | `tools/sandbox_smoke.py` (B1–B6) |
| WS9 | **landed offline; staging drills scripted** | `tools/survive_smoke.py` (R1–R11, includes the live `docker run` no-env 503) |

Baseline re-checked green at close-out: `regress_all` 31/31 rows ·
`demo_smoke --regress` 3/3 · `demonstrate.py --json` exit 0 (0 unaccounted) ·
`pytest -q` 28 passed · `ruff` + `mypy` clean. Staging-only WS9 items (restore
drill against real WAL, kubectl chaos, k6 50 rps, KMS seal, Redis quotas) are
scripted in `ai-layer/scripts/` and owned by `ai-layer/docs/runbook.md`.

## WS0: Guardrails — 2 days

Files: `ai-layer/tools/regress_all.py`, `ai-layer/requirements.txt`, `ai-layer/Dockerfile`, `.github/workflows/`

- Pin deps with hashes, add `pip-audit`, `ruff`, `mypy` to CI.
- Add `tools/gate_prod.py`: runs `regress_all.py --fast` + `demo_smoke.py` + `pytest -q`, fails on any `PENDING` without owner.
- Add SBOM generation in Dockerfile build.
- Accept: CI red on new `PENDING`, SBOM artifact uploaded.

## WS1: AuthN + principal plumbing — 1.5-2 weeks

Files: `ai-layer/ai_os/server.py`, `ai-layer/ai_os/permissions.py`, `ai-layer/permissions.yaml`, `ai-layer/ai_os/audit.py`

- Add OIDC (e.g. `authlib`) middleware in `server.py`: verify JWT, set `request.state.principal`, `tenant`, `roles`. Reject anonymous on `/agent`, `/sql`, `/approvals/*`, `/ingest/span`.
- Replace caller-supplied `principal: str` in `run_agent`, `executor.run_plan`, `approval.py` with `Principal{id, tenant, roles}` from request context. Keep string fallback behind `ALLOW_ANON=1` for offline tests only.
- Extend `permissions.yaml` with `tenant`, `role bindings`; deny-by-default unchanged.
- Tests: `tools/auth_smoke.py` — forged JWT denied, expired denied, cross-tenant read denied, audit entry carries `principal.id`.
- Accept: `GET /audit?principal=` shows resolved OIDC `sub`, not client string.

## WS2: Agent/API routes behind auth — 1 week

Files: `ai-layer/ai_os/server.py`

- Add:
  - `POST /agent {question, max_steps} -> {answer, trace_id}` calls `run_agent` with request principal
  - `POST /sql {sql} -> {columns, rows}` calls `SafeSql` + store, labels backend
  - `GET /approvals`, `GET /approvals/{id}` already exist; keep read-only
- Enforce per-tenant rate limit (token bucket in Redis), input size caps, timeout 30s.
- Tests: unauth 401, over-limit 429, `trace_id` returned, ledger delta matches.
- Accept: S3/S4 reachable via HTTP with auth; Day 30 `scenarios.yaml` route `kind: http` updated.

## WS3: Write path — 1 week

Files: `ai-layer/policy.yaml:20`, `ai-layer/ai_os/sql_safety.py:753,786,994`, `ai-layer/ai_os/data.py`, `ai-layer/permissions.yaml`

- `data.py`: add `SqliteDataStore.execute_write(sql, principal)` and `PostgresDataStore.execute_write()` with explicit `BEGIN; ... COMMIT/ROLLBACK`, `statement_timeout`, rowcount return. Keep `query()` read-only. Remove `default_transaction_read_only` only for write session.
- `sql_safety.py`: add `Policy.statement_allow_write: []` default empty; require explicit table + `effect: write` grant + approval id for `insert/update/delete`. All else → `policy` rejection, `Wrote? = no`.
- `policy.yaml`: keep `[select, with]` default; add `write_tables_allow: []` + `require_approval: true`.
- Tests: `tools/write_smoke.py` — DELETE without approval stops, with approval + grant writes 1 row, rollback on error, Postgres role check fails closed.
- Accept: Z7 fixture inverted: asserts write *possible* only with grant+approval; `Saleor rows changed` measured, not 0.

## WS4: Approval execution + compensation — 1 week (after WS1+WS3)

Files: `ai-layer/ai_os/approval.py`, `ai-layer/ai_os/server.py`, `ai-layer/ai_os/state.py`, `ai-layer/ai_os/executor.py`

- Add `POST /approvals/{id}/decide {decision, reason}` — auth required, idempotent, records `decided_by`, digest check `sha256(canonical)`.
- Add `POST /approvals/{id}/apply` — loads approved request, calls whitelisted mutation tool, writes compensation record (`reverse_sql` / saga step) to `StateStore`.
- `executor.py`: add `awaiting_approval` → resume path; never auto-retry policy wall.
- Tests: approve-twice = 1 apply, digest mismatch refused, compensation runs on failure, ledger shows write cost.
- Accept: S7 demo shows `applied: true, rows_changed: N, compensated: false` with audit chain intact.

## WS5: Real code store — 3-5 days + $50-100/mo or self-host

Files: `ai-layer/ai_os/rag.py`, `ai-layer/tools/build_code_store.py`, `ai-layer/data/code_store.json`

- Switch embeddings to self-hosted (Ollama `bge-base-en-v1.5` dim 768) or paid tier; re-run `build_code_store.py` for 29,156 symbols.
- Keep identity key `sha256(file)+model+dim` in `load_store_cached`; assert dim match, fail loudly on mismatch.
- Tests: `retrieval_day10.py --live` CA1-CE1 green, S2 latency labeled cold/warm.
- Accept: Day 10 quota `PENDING` closed.

## WS6: Semantic eval L2/L3 — 2 weeks

Files: `ai-layer/ai_os/judge.py`, `ai-layer/ai_os/semantic_judge.py`, `ai-layer/ai_os/golden.yaml`, `ai-layer/tools/eval_harness.py`

- Implement L2: citation support (NLI entailment), groundedness beyond membership, answer relevance. Deterministic thresholds + owner per check.
- L3 LLM-judge off by default, calibrated prompts, sampled runs only.
- Join to scorecard: S1/S2/S3/S4 get semantic column; green L1 + red L2 = `pending`, never `pass`.
- Accept: nightly `eval_harness.py --live --sample` passes with published thresholds.

## WS7: Trace sink, retention, metrics — 1 week

Files: `ai-layer/ai_os/observability.py:706-716`, `ai-layer/ai_os/server.py:/metrics,/traces`, `ai-layer/ai_os/metrics.py`

- Replace in-process `OrderedDict` with Postgres/TimescaleDB sink behind same `TraceStore` interface; keep 256 cap only for local fallback.
- Add retention job (e.g. 30d raw, 90d aggregates), `/metrics` Prometheus export, per-route latency histograms.
- S6 aggregate "why increased" becomes answerable: window query + baseline compare.
- Tests: `trace_smoke.py` + `distributed_smoke.py` against sink; eviction test becomes retention test.
- Accept: `GET /traces/{id}` works after restart; p95 alert fires in staging.

## WS8: Test execution sandbox (S5) — 3 weeks, isolated

Files: new `ai-layer/ai_os/sandbox.py`, `ai-layer/tools/run_tests.py`, new `run_tests` tool

- Run tests in gVisor/Firecracker or Docker `--network=none --pids-limit --memory` with timeout, no secrets mounted, read-only corpus.
- Tool returns `{passed, failed, log_sha256, log_excerpt}` — never full log with secrets.
- Permission: `effect: execute`, separate grant, requires approval.
- Tests: malicious `rm -rf`, exfil attempt, infinite loop all contained.
- Accept: S5 absence fixture replaced with sandboxed pass/fail; Day 25 firewall extended to tool args.

## WS9: Survive — 2-3 weeks parallelizable after WS1+WS3+WS7

- **Deploy:** K8s manifests, HPA 2-10, PDB, rolling update, readiness `/readyz` checks gateway+store+DB+state. Single-writer lock replaced by Postgres advisory lock.
- **Secrets:** Vault / External Secrets, rotation, no `.env` in image. Verify: `docker run` without env → `/readyz` 503 with named check.
- **DB:** Managed Postgres, `saleor_readonly` + `saleor_writer` roles, PgBouncer, migrations in `saleor-platform/`, PITR backups tested.
- **Rate/abuse:** Redis quotas per tenant/principal, `Retry-After`, cost ceiling per request kills loop with `budget` termination.
- **Privacy:** `columns_deny` enforced on results (not just statements), PII redaction in traces/logs, encryption at rest, audit log immutability check.
- **DR:** RPO 15m / RTO 1h defined, restore drill scripted, chaos test kills 1 replica mid-demo.
- **Observability:** Prometheus + Grafana dashboards (latency p50/p95, cost/run, refusal rate, approval queue age), alerts for eval drop, quota exhaustion, sink lag.
- Accept: load test 50 rps sustained, 1 replica kill with zero data loss, restore from backup verified, `demo_smoke.py --regress` green in staging.

## Exit criteria to call it deployable

1. `regress_all.py` + `demo_smoke.py --regress` green in CI *and* staging.
2. Auth on, anon off; cross-tenant test red.
3. Write path exercised with approval + compensation; `Wrote?` measured per request.
4. Trace queryable after restart; S6 aggregate query works over 7d window.
5. Semantic eval nightly, thresholds published.
6. Runbook + SLOs + alerts + restore drill completed.

Start with WS0+WS1. Do not start WS2/WS4 until principal is real — otherwise you ship confused-deputy-as-a-service.
