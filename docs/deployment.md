# Day 29 — Deployment

## Manifest

The image is a **subset of the repository**, and the interesting part is the subset.
Every path in the image is enumerated below, with a `why`. A file nobody can
justify is a file that should not ship (measured: 578 MB context → 587 MB image).

```yaml
image:
  base: python:3.12-slim
  runtime_user: ai-os
  exposes: [8000]
  healthcheck:
    liveness:  { path: /healthz,  touches: [],                              timeout_s: 2 }
    readiness: { path: /readyz,   touches: [gateway, store, database, state], timeout_s: 30 }
include:
  - path: ai_os/            why: the application
  - path: tools/            why: the offline fixtures the N-set runs inside the image
  - path: policy.yaml       why: Day 15's SQL policy the data layer enforces
  - path: .env.example      why: the configuration *contract*; never a value source
  - path: requirements.txt  why: four dependencies, resolved at build
  - path: data/store.json   why: 262,182,307 B · 11,061 chunks · dim 768
absent:
  - path: .env               why: secrets come from the run environment  (BUILT, ASSERTED)
  - path: .git/              why: history is not a runtime dependency
  - path: .venv/             why: the image installs its own; this one is py3.14
  - path: corpus/            why: ingest-time input; `data/store.json` is the artifact
  - path: corpus_code/       why: ditto — 98 MB of source the image does not serve
  - path: **/__pycache__/    why: another interpreter's bytecode
  - path: docs/              why: 2.7 MB of prose; the deployment doc is in git
limits:
  memory: 1g
  workers: 1
```

**Built image:** 587 MB (naive `COPY . .` was 1.05 GB with 1,026 bytes of `.env` in a layer).
**Naive build leaked:** `/app/.env` at 1,026 bytes with four live credentials in layer `0330bff71c63`.
**Whiteout proof:** `RUN rm -f /app/.env` produces a container without `.env` but the image
still has all 1,026 bytes in layer `0330bff71c63` with a whiteout `app/.wh..env` above it.

## Secret Contract

Three parts, each falsifiable:

1. **`.dockerignore` names `.env`** — deny-by-default allow-list (`*` then `!` for manifest
   `include` set). The built image is asserted to contain **zero** paths matching
   `\.env|\.pem|\.netrc|\.aws|credentials`.
2. **Run command injects the environment** — `docker run --env-file /secure/ai-os.env`
   and the process may fall back to a file only when one is explicitly mounted,
   never from the image.
3. **Count is zero** — not "the file I thought of", so a future `.env.production`,
   `.netrc`, or `*.pem` is caught by the same rule instead of by luck.

**Measured leak:** 1,026 bytes of `.env` (13 names, 3 credentials + 1 base URL) in a
naive build. The only correct channel is the run environment — `ARG`/`ENV` moves the
key into the image **configuration** (document 3 of 3), where `docker inspect` and
`docker history --no-trunc` both print it. Docker's own linter emits
`SecretsUsedInArgOrEnv` twice for that pattern.

## Two Probes

| probe | path | touches | timeout | status codes | measured cost |
|-------|------|---------|---------|--------------|---------------|
| **liveness** | `/healthz` | nothing external | 2 s | `200` only | 1.87 s, 35.6 MiB RSS |
| **readiness** | `/readyz` | gateway · store · database · state | 30 s | `200` if all pass, `503` if any fails | 4.94 s, 705 MB peak RSS (first call, then cached) |

**Liveness** returns `200 {"ok": true, "checks": {}}` — cannot fail on a dependency.
A wedged event loop, dead provider chain, or unopenable state file are explicitly
not checked here.

**Readiness** returns a **table of named checks**, not a boolean:
```json
{
  "ok": true,
  "checks": {
    "gateway":  {"ok": true, "detail": "providers=[openai, anthropic]", "cost_ms": 2},
    "store":    {"ok": true, "detail": "chunks=11061 dim=768", "cost_ms": 4940},
    "database": {"ok": true, "detail": "postgres", "cost_ms": 1},
    "state":    {"ok": true, "detail": "writable", "cost_ms": 3}
  }
}
```
On failure: `503` with the failing check named. The old `/health` stays **byte-identical**
(including its `200 {"ok": false, ...}` anti-pattern) because something in the series
may depend on it, and deleting an anti-pattern by adding a correct thing is the only
way to do it without breaking a contract.

## One-Writer Contract

**One replica, one lock, and the lock's refusal is a refusal, not a migration.**

`SingleWriter(path)` opens `<state>.lock`, takes `fcntl.flock(LOCK_EX | LOCK_NB)`,
and on failure raises `SecondWriter(pid=…, path=…)` — a refusal that **names the
holder**. Wired into start-up (so `uvicorn --workers 2` or a second replica on a
shared volume **fails at boot with a sentence**) and into `readiness()`'s `state`
check (so a process that started without it is reported **unready** rather than
broken).

**Honest limits:**
- `flock` is **advisory** — every writer must take it. The state store is the only
  writer today, which is a property the day owns rather than one it can assume.
- **Filesystem-dependent** — works on local volume and bind mount; NFS semantics
  vary and are not claimed.

What it buys: converts Day 28's "two writers lose records silently" from a drawing
into a **refusal**, which is what allows the container to be honestly scaled to
exactly one.

## Two-Process Register (measured failure modes)

| mode | what happens | file:line | guard |
|------|--------------|-----------|-------|
| **read-modify-write** | Writer A holds 40, B boots at 31, B's first write drops A's 10 later records, `exceptions == 0` | `state.py:838-845` (rewrites entire file from `self.records` on every `put`) | `SingleWriter` refuses second process at boot |
| **read-rewrites-file** | `get()` calls `_maybe_save()` → file flips between A's and B's view, each flip deletes the other's newest record | `state.py:607` | same |
| **true concurrent** | Both compute same tmp path, one's `os.replace` renames the other's tmp away, loser dies `FileNotFoundError` after 12/40 writes | `state.py:843-845` | same |

Day 28's Plate II said "two writers lose records silently" — the honest correction is
the shipped code does **both**, in different interleavings. The guard asserts the
defect first (N4), then refuses the second process.

## AWS Map (architectural depth with "does not fix" column)

| service | maps to | **does not fix** | local mechanism that stands in |
|---------|---------|------------------|--------------------------------|
| **ECR** | the image | a secret already in a layer | `.dockerignore` allow-list + N2's zero-count |
| **ECS/Fargate** | the process and its memory limit | a readiness probe that lies; a second replica on a shared file | `docs/deployment.yaml` `limits` + N5's floor refusal |
| **Secrets Manager / SSM** | the key at run time | a key that is *in the image* | `--env-file` at run time |
| **RDS Postgres** | `SALEOR_READONLY_DB_URL` + `saleor_read_only` role | two writers on `state.json` (a different store entirely) | `data.py:798-806` seam, announced by readiness |
| **S3** | the corpus and both store files | "a corpus change is a rebuild today" | the `data/` allow-list entry |
| **ALB** | a health check that can actually fail + TLS termination | **identity** | `/healthz` + `/readyz` status codes |
| **CloudWatch** | the shape `/metrics` and `/logs` already have | retention, sampling, and a sink that does not exist | `server.py:249-255`, `:258-266` |

**RDS does not fix the two-writer problem** — the state store is not the database.
**S3 does not fix the rebuild** — today `data/store.json` is an image input; a 262 MB
artifact inside a 587 MB image makes the "corpus refresh is a rebuild" trigger
*more* likely, not less.

## Cold-Start Table (measured)

| limit | load time | result |
|-------|-----------|--------|
| unlimited | **4.94 s** | 705 MB peak RSS, 11,061 chunks |
| `--memory=1g` | 7.06 s | survives |
| `--memory=512m` | 12.67 s | survives |
| `--memory=384m` | 19.94 s | survives, **thrashing — 4× slower** |
| `--memory=320m` | — | **`exit 137`, SIGKILL** |
| `--memory=256m` | — | **`exit 137`, SIGKILL** |

**Memory floor:** 384 MB survives (thrashing), SIGKILL at 320 MB. Manifest declares
`memory: 1g` with reason (705 MB measured peak for store + interpreter + headroom).
Fixture **refuses a manifest that declares less than the measured floor** — a document
that under-declares memory is a crash loop with a cause nobody can see.

## Residual Register (PENDING with owners)

| item | trigger | owner |
|------|---------|-------|
| rate limiter attachment | TLS + identity + ALB land | WS1 (identity day) |
| TLS, reverse proxy, service unit, compose file | orchestration story earned | WS9 (K8s/service-unit; staging covered by Phase 0-A Compose+Nginx) |
| external anchor for audit chain | KMS + append-only store | WS9 (KMS anchor) |
| connection pool + read replica | two processes (delayed by single-writer lock) | WS7/WS9 (pool + PgBouncer) |
| trace sink + retention | sink exists | WS7 (trace sink) |
| ANN / real vector store | corpus × 10 | WS5 (real code store) |
| object storage (S3) | corpus refresh slower than deploy | WS7 (S3 object storage) |

## Clean Claims (carried forward)

- `Wrote? = no` (unchanged; second reason: no write path, so nothing to scale and no migration to run)
- `Saleor rows changed: 0` (Day 27's lifecycle number; `SALEOR_READONLY_DB_URL` is a *read-only* URL)
- `0 chats` for every `MECH` fixture in this series (why 29 days were affordable)

## Run Command (documented, pasteable)

```bash
docker run --rm -p 8000:8000 --memory=1g \
  --env-file /secure/ai-os.env \
  d29:honest
```

**Deliberate omissions:** no `-v` for store (in image; volume mount exists for corpus
refresh trigger and is named as such), no `--restart` (restart policy hides the
crash loop the memory floor predicts, and the day wants it visible), no `--name`-based
discovery. `--read-only` not claimed (process writes `data/` state and ledger
companions; paths that would need tmpfs are recorded instead of pretending).