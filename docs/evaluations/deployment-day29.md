# Day 29 — Deployment Evaluation

## N-Fixtures Results (N1–N9)

| fixture | status | notes |
|---------|--------|-------|
| **N1** manifest both directions | ✅ PASS | every `include` path exists; every `absent` entry has a `why`; context paths `.env`, `.git/`, `.venv/`, `corpus/`, `corpus_code/`, `docs/` all in `absent` |
| **N2** secret not in image | ✅ PASS | `.dockerignore` deny-by-default allow-list in force; image asserted zero secret-shaped paths; manual layer verification recommended |
| **N3** probes can fail | ✅ PASS | liveness `200` with empty checks (no deps); readiness `503` with named failing check when gateway missing; `200` when all pass; old `/health` unchanged (`200 {"ok": false}`) |
| **N4** defect asserted, guard refuses | ✅ PASS | two-writer sequence run against real `StateStore` — writer A 40, writer B 31, B's write drops A's 10 records, `exceptions == 0`; same sequence with `SingleWriter` guard asserts second process refused at boot with `SecondWriter` naming holder |
| **N5** memory floor measured | ✅ PASS | manifest declares `1g`; measured floor `384 MB` (survives, thrashing), `SIGKILL` at `320 MB`; fixture refuses manifest below floor |
| **N6** bind contract three copies | ✅ PASS | Dockerfile `CMD` names `--host 0.0.0.0`; server.py docstring shows default (known divergence, documented); container published port answers from non-loopback |
| **N7** config census | ✅ PASS | code reads **64** names (56 literal + 8 provider-template); `.env.example` documents **26** (22 + 4 commented); overlap **25** (minus `CMD_ZDR`); **39** read undocumented; **1** documented unread (`CMD_ZDR`) |
| **N8** database announced | ✅ PASS | readiness reports `stub (SEED_SQL)` when `SALEOR_READONLY_DB_URL` unset; `postgres` when set + `psycopg` available; stub label asserted not backend choice |
| **N9** regression | ⏭️ DELEGATED | `tools/regress_all.py` runs D1–D9, R1–R9, AB1–AB9 unchanged |

---

## Image Manifest

| property | value |
|----------|-------|
| **base** | `python:3.12-slim` |
| **runtime_user** | `ai-os` (non-root) |
| **exposes** | `8000` |
| **healthcheck** | liveness `/healthz` (2s), readiness `/readyz` (30s) |
| **built size** | **587 MB** (naive `COPY . .` was 1.05 GB) |
| **leak prevented** | `/app/.env` 1,026 bytes with 4 live credentials in layer `0330bff71c63` |

### Include (6 paths)

| path | why | bytes |
|------|-----|-------|
| `ai_os/` | the application | ~2.0 MB |
| `tools/` | offline fixtures for N-set | ~1.5 MB |
| `policy.yaml` | Day 15 SQL policy | ~3 KB |
| `.env.example` | config contract (never values) | ~12 KB |
| `requirements.txt` | 4 deps resolved at build | ~200 B |
| `data/store.json` | 262,182,307 B · 11,061 chunks · dim 768 | 262 MB |

### Absent (7 paths)

| path | why |
|------|-----|
| `.env` | secrets from run env (BUILT, ASSERTED) |
| `.git/` | history not a runtime dependency |
| `.venv/` | image installs own; host venv is py3.14 |
| `corpus/` | ingest-time input; `data/store.json` is artifact |
| `corpus_code/` | 98 MB source the image does not serve |
| `**/__pycache__/` | another interpreter's bytecode (94 dirs) |
| `docs/` | 2.7 MB prose; deployment doc in git |

### Limits

| limit | value | reason |
|-------|-------|--------|
| `memory` | `1g` | measured peak 705 MB for store + interpreter + headroom |
| `workers` | `1` | single-writer lock enforces it |

---

## Cold-Start Table (Measured)

| limit | load time | result |
|-------|-----------|--------|
| unlimited | **4.94 s** | 705 MB peak RSS, 11,061 chunks |
| `--memory=1g` | 7.06 s | survives |
| `--memory=512m` | 12.67 s | survives |
| `--memory=384m` | 19.94 s | survives, **thrashing — 4× slower** |
| `--memory=320m` | — | **`exit 137`, SIGKILL** |
| `--memory=256m` | — | **`exit 137`, SIGKILL** |

**Memory floor:** 384 MB survives (thrashing), SIGKILL at 320 MB.
**Fixture:** refuses manifest declaring less than measured floor.

---

## Two-Process Register (Measured Failure Modes)

| mode | what happens | file:line | guard |
|------|--------------|-----------|-------|
| **read-modify-write** | Writer A 40, B boots at 31, B's first write drops A's 10 later records, `exceptions == 0` | `state.py:838-845` | `SingleWriter` refuses at boot |
| **read-rewrites-file** | `get()` calls `_maybe_save()` → file flips between A/B view, each flip deletes other's newest | `state.py:607` | same |
| **true concurrent** | Both compute same tmp path, one's `os.replace` renames other's tmp away, loser `FileNotFoundError` after 12/40 writes | `state.py:843-845` | same |

Day 28's Plate II said "two writers lose records silently" — the shipped code does **both**, in different interleavings. The guard asserts the defect first (N4), then refuses the second process.

---

## AWS Map (Architectural Depth with "Does Not Fix")

| service | maps to | **does not fix** | local mechanism |
|---------|---------|------------------|-----------------|
| **ECR** | the image | a secret already in a layer | `.dockerignore` + N2 zero-count |
| **ECS/Fargate** | process + memory limit | readiness probe that lies; second replica on shared file | `deployment.yaml` limits + N5 floor refusal |
| **Secrets Manager / SSM** | key at run time | a key *in the image* | `--env-file` at run time |
| **RDS Postgres** | `SALEOR_READONLY_DB_URL` + `saleor_read_only` role | two writers on `state.json` (different store) | `data.py:798-806` seam, announced by readiness |
| **S3** | corpus + both store files | "corpus change is a rebuild today" | `data/` allow-list entry |
| **ALB** | health check that can fail + TLS | **identity** | `/healthz` + `/readyz` status codes |
| **CloudWatch** | `/metrics` + `/logs` shape | retention, sampling, sink that doesn't exist | `server.py:249-255`, `:258-266` |

**Key findings:** RDS does not fix the two-writer problem (state store ≠ database). S3 does not fix the rebuild (store.json is an image input; 262 MB in 587 MB image makes rebuild trigger *more* likely).

---

## Residual Register (PENDING with Owners)

| item | trigger | owner |
|------|---------|-------|
| rate limiter attachment | TLS + identity + ALB land | day identity lands |
| TLS, reverse proxy, service unit, compose file | orchestration story earned | — |
| external anchor for audit chain | KMS + append-only store | — |
| connection pool + read replica | two processes (delayed by single-writer lock) | — |
| trace sink + retention | sink exists | — |
| ANN / real vector store | corpus × 10 | — |
| object storage (S3) | corpus refresh slower than deploy | — |

---

## Clean Claims (Carried Forward)

- `Wrote? = no` (unchanged; second reason: no write path, so nothing to scale, no migration to run)
- `Saleor rows changed: 0` (Day 27 lifecycle; `SALEOR_READONLY_DB_URL` is a *read-only* URL)
- `0 chats` for every `MECH` fixture in this series (why 29 days were affordable)

---

## Deployment Artifacts Created

| file | purpose |
|------|---------|
| `Dockerfile` | single-stage, pinned base, `--no-cache-dir`, manifest include set, non-root, HEALTHCHECK on `/healthz`, `CMD` with `--host 0.0.0.0` |
| `.dockerignore` | deny-by-default allow-list (463 MB saving vs naive) |
| `ai_os/health.py` | `liveness()` + `readiness()` with injected deps, store-identity cache, per-check tuple, `503` on any failure |
| `docs/deployment.yaml` | manifest (include/absent/limits/healthcheck) |
| `docs/deployment.md` | human-readable deployment document |
| `tools/deploy_smoke.py` | N1–N9 fixture (offline + docker modes) |
| `ai_os/state.py` | `SingleWriter` / `SecondWriter` / `acquire_single_writer_lock` |
| `ai_os/server.py` | `/healthz` + `/readyz` routes (additive; old `/health` byte-identical) |
| `.env.example` | Day 29 knobs: `AI_OS_IMAGE`, `AI_OS_HEALTH_TIMEOUT_S`, `AI_OS_LOCK_STATE` + 4 `TRACE_*` |

---

## Run Command (Documented, Pasteable)

```bash
docker run --rm -p 8000:8000 --memory=1g \
  --env-file /secure/ai-os.env \
  d29:honest
```

**Deliberate omissions:** no `-v` for store (in image; volume mount exists for corpus refresh trigger), no `--restart` (hides crash loop), no `--name`-based discovery. `--read-only` not claimed (process writes `data/` state).

---

## Summary

Day 29's headline numbers are a **leak and a floor** — 1,026 bytes of secrets that were in an image until a 20-line `.dockerignore` was added, and a memory floor at which the readiness probe kills the container. The honest measurement of a system on a deployment day is not what it can do but **what it will do to itself when you hand it a memory limit and a build context.**

The single-writer guard is the day's one new mechanism: it converts "two writers lose records silently" from a drawing into a **refusal**, which is what allows the container to be honestly scaled to exactly one. Every other bound in the repository is a number in a document; this is the first bound in twenty-nine days enforced by the operating system.