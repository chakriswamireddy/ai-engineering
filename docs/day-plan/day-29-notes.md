# Day 29 Notes — Deployment

> Notes answering `docs/day-plan/day-29.md`.
> Twenty-eight days built a machine that reads (Days 6–10), acts (11–12), fails honestly (13), computes (14–15), plans (16), remembers (17–18), splits into specialists (19), learns how it will be judged (20–22), explains one request from the inside (23) and across a process boundary (24), learned to be attacked (25), learned who may open a door (26), learned to stop and wait for a person (27), and yesterday learned to draw a map of what breaks first (28). The curriculum now hands the day a build list: **containerization, environment variables, secrets, application deployment, database, observability, health checks**, with the note that *"AWS can be introduced at architectural depth first and implemented where practical"* and the deliverable **an accessible deployed AI OS or a production-like local deployment**. The inventory is short and the repository's answers to it are worse than yesterday's, because six of the seven items are not about the model and not about architecture either: there is no `Dockerfile`, no compose file, no `.dockerignore`, no `HEALTHCHECK`, no image, and nothing deployed. `requirements.txt` holds four dependencies and no HTTP client, so there is not even a way to ask a remote instance a question. The only documented run command in the repository is the module docstring at `server.py:3–4` — `uvicorn ai_os.server:app` — with no `--host`, no `--port`, and no container around it. And the state story is entirely in-process: the gateway's `last_provider`/`last_latency_ms`/`last_attempts` are module-level fields on a shared global (`gateway.py:80–82`, read after the call at `server.py:125–126`), the ledger is a list in that process (`ledger.py:65`), the trace store is an `OrderedDict` in that process capped at 256 (`observability.py:706–716`, the cap at `:714`), and the secrets are in a `.env` file **in the working directory** that `config.py:23–37` reads at import via `config.py:76`, called from `server.py:35`.
>
> The finding that reorganizes the day is not "there is no Dockerfile." It is that **this repository has never had a second copy of itself, and the first thing a second copy does is lose data quietly** — and the day ran the experiment Day 28 refused to run, and the shipped code turned out to be worse than the drawing. Two containers on one shared volume, each with the state store in memory, drop **10 records with zero exceptions**: writer A holds 40, writer B boots holding 31, B's first write rewrites the whole file from B's copy, and the file ends with 31. The corruption is *mutual and ongoing*, because a `get()` in container A rewrites the file from A's memory (`state.py:607` calls `_maybe_save()` on a read), so the file flips between 21 and 30 records depending on which container last touched it, and each flip deletes the other container's newest record. And in the genuinely concurrent ordering it does not lose data — it **crashes**: both writers compute the same tmp path (`state.py:843`), one's `os.replace` renames the other's tmp away, and the loser dies with `FileNotFoundError` at `state.py:845` after 12 of 40 writes. Day 28's Plate II said "two writers lose records silently," and the honest correction is that the shipped code does **both**, in different interleavings. That is the day's hinge: the container is the *first second process in twenty-nine days*, and the first time yesterday's plate stops being a drawing.
>
> The second finding is that the leak is not hypothetical and the obvious fixes do not work. The build context is 578 MB; the naive `COPY . .` image is **1.05 GB and contains `/app/.env` at 1,026 bytes** with four live credentials in it. Adding `RUN rm -f /app/.env` produces a container with no `.env` and an image that **still has all 1,026 bytes in layer `0330bff71c63`**, with a whiteout (`app/.wh..env`) in the layer above: a whiteout tells the union filesystem to pretend a file is not there, which is not the same operation as removing a byte from a content-addressed store. And the reflex fix is worse: `ARG`/`ENV` moves the key out of a layer and into the image's **configuration**, where Docker's own linter says so — `SecretsUsedInArgOrEnv` twice — and where `docker inspect` and `docker history --no-trunc` both print it. The only correct channel is the run environment, which is why today's deliverable is a manifest with a fixture over it rather than a Dockerfile with a `rm` in it.
>
> The third finding is that the repository's health check is **structurally unable to fail**: `server.py:77–92` returns a `dict`, so FastAPI answers `200` in both branches, and the unconfigured branch is the one an orchestrator must act on. Measured in the built image, a process that cannot answer a single question is maximally healthy by the only signal a platform reads. And being *ready* is expensive: loading `data/store.json` is 4.94 s and 705 MB peak RSS, degrading to 19.94 s at a 384 MB limit and dying with `exit 137` / `SIGKILL` at 320 MB — so a readiness probe that tells the truth is a **crash loop caused by the health check** under a limit chosen by a platform default rather than a measurement.
>
> The single idea: **a deployment is not a claim that the system runs, it is a claim about what happens to its state when the process is replaced** — and the four things that decide that claim are all *outside* the code: the build context, the memory limit, the published port, and the injected environment. Every one of the four is a value a reader can check, which is why the day's deliverable is a manifest, a secret contract, two probes with status codes, and one lock that refuses the second writer.
>
> **The one line the day would die defending: an image is not a folder with the wrong things deleted — it is a file list, a stack of immutable diffs, and a configuration document, and the mistake everyone makes once is treating it as the first, which is exactly how a 1,026-byte secret survives a `RUN rm` and gets pushed to a registry that will serve it forever; so the day's most valuable artifact is not the image at all, it is the measured floor — 4.94 seconds and 705 megabytes to be ready, `SIGKILL` at 320 — because on a deployment day the honest measurement of a system is not what it can do but what it will do to itself when you hand it a memory limit and a build context.**

---

## 0. Verification Status of This Document

This file is written **before** the day's implementation. Two kinds of sentence appear below and they are never mixed:

- **Verified inventory** — every statement about what the repository *is today* and about what the measurements returned. Each was checked by reading the cited line; the citations in this file are mechanically validated: every `file:line` resolves to a file that exists and to a line that is inside it and non-blank. The census method in §7.4 is reproducible from the repository alone.
- **Pre-registered** — every expectation about the day's own new artifacts (`Dockerfile`, `.dockerignore`, `ai_os/health.py`, `docs/deployment.yaml`, the guard, the checker, N1–N9). These are written down *first*, on purpose, and are marked `PENDING` because nothing has been built. They are not results. A reader who finds a number in §10 that is not traceable to a **Verified** sentence is looking at an expectation, and this file says so.

**Corrections to the record, stated here rather than absorbed silently, because they are the failure mode the day is about.**

1. **The census in the Day 29 plan was wrong, and the notes' own §0 is where the correction lives.** The plan carried `63 / 26 / 38` and an after-row of `63 / 31 / 32`. The measured row on the current tree is **`64 / 26 / 39`**, and the after-row is **`66 / 33 / 35`**. Two separate errors: the read column was short by one (the provider-template expansion count is **8**, not 7 — nine names are reachable and `OPENAI_API_KEY` is the only one already read literally, so `CLOUDFLARE_ACCOUNT_ID` is not the deduplicated name the plan claimed; it is a plain literal at `config.py:69` and was never in the template set), and the after-row was **internally impossible** — it kept the read column at 63 while claiming 31 documented, which cannot hold when the day documents four already-read names and two new ones and the plan's own `AI_OS_HEALTH_TIMEOUT_S` is by construction read by the module that honours it. The `63 / 31 / 32` row would have shipped a number that no method produces.
2. **`observability.py:744` was cited for the 256-trace cap and is wrong** — line 744 is `_DEFAULT_STORE = TraceStore()`. The cap is `max_traces: int = 256` at **`observability.py:714`**, inside the class at `:706–716`. Same defect as Day 28's retracted claim, at a smaller scale: a plausible line number that is a real line and the wrong line.
3. **The `.env` was said to hold 14 names; it holds 13.** Verified by name only — no value was printed. `OPENAI_API_KEY` (73 chars), `CLOUDFLARE_API_KEY` (53), `CLOUDFLARE_ACCOUNT_ID` (32) are the credentials and `OPENAI_BASE_URL` (28) is the endpoint; the file is 1,026 bytes. The plan also said "four of them credentials … plus the base URL", which counts the base URL as both.
4. **The exit check asked "which *four* AWS services does the map name" and the map has seven rows** (`ECR`, `ECS/Fargate`, `Secrets Manager`, `RDS Postgres`, `S3`, `ALB`, `CloudWatch`). A question that under-counts the deliverable is a question that a reader can answer correctly by accident, so it is now seven, and it also asks for the local stand-in.
5. **The N9 regression list was written from memory and did not match `tools/regress_all.py`.** It named `I1–I9` (the real family is **`D1–D9` distributed trace**, `tools/distributed_smoke.py`) and omitted two suites that are in the gate: **`R1–R9`** (`tools/bench_rag.py --fixtures`) and **`AB1–AB9`** (`tools/bench_agent.py --fixtures`). It also listed "gateway, RAG" as if they were replayed — `tools/gateway_smoke.py`, `tools/rag_smoke.py`, and `tools/rag_semantic_smoke.py` exist and are **not** in the gate. This is the most embarrassing of the five, because the whole point of N9 is that the prior suites are re-run *unchanged*, and the list of what "unchanged" means was wrong.
6. **Two sizes moved while the day was being written** and are now stated at the current values: `docs/` is **2.7 MB** (Day 28's plan added 78 KB to it) and `.git/` is **5.7 MB**. Neither is load-bearing; both are quoted as measurements, so a measurement that has moved must be re-measured rather than inherited.

The pattern across all six is one sentence: **a document inherits unverified claims with total confidence, and the failure rate of a citation rises with the number of days between writing and reading.** Day 28 retracted one inherited claim; Day 29 found five of its own. That is the argument for the day's ratchet stated in its own cost, which is the only argument for it that has ever convinced anyone.

---

## 1. The Loaded Question — "Deploy a Working Version"

"Deploy a working version" contains a hidden word: *version*. A version is the thing that gets replaced; a working process is what gets replaced **with**. So the question is not "does it run" — twenty-nine days of fixtures prove it runs, and the stubs prove it runs *without a key* — but **what does this system still know after the process that learned it is gone, and what does it forget silently.** The useful reformulation, and the day's working definition, is three questions per piece of state: **where does it live, does it survive a restart, and may a second instance touch it.** Every state in this repository answers the first two. None answers the third, and that is the deployment.

The curriculum offers two deliverables and they are not the same work: *"an accessible deployed AI OS **or** a production-like local deployment."* The day takes the second, and says why out loud rather than choosing silently: this host has a Docker daemon (29.8.1) and no cloud account, so the honest deliverable is **a real image, a real container, a real published port, and a real probe** — the same artifact AWS would run, minus the twelve services around it. The curriculum's own phrase *"implemented where practical"* is what makes that legitimate instead of a dodge, and the discipline that follows is that **the AWS mapping is not optional filler**: §5.6 maps every finding to a named service *and states what that service does not fix*, because architectural depth that omits the limits is a wish list with region names in it.

The honest inventory, item by item, with the status the day will defend:

| # | curriculum item | status | verified where |
|---|-----------------|--------|----------------|
| 1 | containerization | **absent** — and the obvious version of it is a leak | no `Dockerfile`, no `.dockerignore`, no compose file, no image; naive `COPY . .` → 1.05 GB containing `/app/.env` at 1,026 B |
| 2 | environment variables | **present, and the best-configured thing in the repository** | 64 names read through one pattern (`os.getenv`/`_env_int`/`_env_float`, never a constant buried in a module), 26 documented, 39 invisible — §7.4 |
| 3 | secrets | **a file read at import, shipped into the image anyway** | `config.py:23–37` via `config.py:76`, called from `server.py:35`; correctly git-ignored (`.gitignore:1`), correctly warned about in the template (`.env.example:1`, *"Never commit a real key"*) — and in a layer, because git and Docker have different definitions of the build context |
| 4 | application deployment | **absent** | no process manager, no service unit, no reverse proxy, no TLS; the only run command is a docstring (`server.py:3–4`) with no `--host` and no `--port` |
| 5 | database | **two backends behind one silent seam** | `data.py:798–806`: Postgres when `SALEOR_READONLY_DB_URL` is set, otherwise `sqlite3.connect(":memory:")` seeded with `SEED_SQL` (`data.py:455–458`), with **no warning at the substitution** |
| 6 | observability | **present, and honest about itself** | `/metrics` returns a Prometheus rendering *and* a note calling itself "a local development surface" (`server.py:249–255`); `/logs` returns a hint when `structured_logging.setup()` was never called (`server.py:258–266`); `POST /ingest/span` is **404 by default** with a refusal naming the reason (`server.py:282–288`) |
| 7 | health checks | **present, and structurally unable to fail** | `server.py:77–92` returns a `dict`, so FastAPI sends `200` in both branches; the unconfigured branch (`server.py:79–80`) is the one an orchestrator must act on |

Six of the seven have an honest answer and only two of the six are *good* news. Item 6 is worth pausing on, because it is the item where yesterday's and today's work show up green: the metrics surface says it is a development surface, the logs surface says whether the ring exists, and the ingest route refuses with a **status code** and names the boundary it is protecting. That is the Day 24 result, and it is a real one — and it is also three surfaces a container now has to decide about, which is the honest way to say "good observability is still a deployment decision."

The deliverable question deserves an explicit ruling, because the day could have gone three ways. (a) *Put it on AWS.* **Rejected** — no account, no credentials, and twelve cloud resources nobody can run is precisely the unfalsifiable artifact Day 28 refused to draw. (b) *Write a Dockerfile.* **Necessary and insufficient** — a Dockerfile is a claim, and the day begins by falsifying the most obvious one available in this repository. (c) **Make the deployment falsifiable.** **Chosen** — an image whose file list is asserted in both directions, a probe whose status codes are asserted, a two-writer precondition that is *refused* rather than assumed, a secret proven absent from the artifact, and a manifest that names every item the deployment does not have. Only (c) survives contact with the next contributor, which is the test every deliverable in this series has been held to since Day 7.

---

## 2. An Image Is Three Things, and Only the Third One Leaks

The word "image" is used for three different objects and the confusion between them is the single most common cause of a secret in a registry. **(1) A file list** — the union of the layers, which is what `docker run` shows you. **(2) A set of layers**, each an immutable diff, which is what `docker save`, `docker push`, and a registry actually store. **(3) A configuration document** — `ENTRYPOINT`/`CMD`/`ENV`/`EXPOSE`/`USER`/`ARG`, a **separate JSON object from the layers** and exactly as much a part of the image as any file in it.

Everyone's first mistake is treating (1) as the image. You delete a file, the container no longer has it, and the image still has it — because deleting in a later layer writes a **whiteout** entry (`.wh.<name>`) instructing the union filesystem to pretend the file is not there. The bytes are still in the content-addressed store, still in `docker save`, still in whatever registry received the push. Measured on the `RUN rm -f /app/.env` image: the running container has no `.env`; `docker save` produces **9 layers**; layer `0330bff71c63` contains `app/.env` at **1,026 bytes, mode `0o664`**; and the layer above it (`93c202d17ac1`) contains two entries, one of which is `app/.wh..env`. A whiteout hides a file from the merged view. It does not remove a byte.

The second mistake is assuming the wrong channel fixes it. `ARG`/`ENV` is the reflex — pass `--build-arg OPENAI_API_KEY=…`, then `ENV OPENAI_API_KEY=$OPENAI_API_KEY` — and it is **measured to be the wrong channel**, not merely an unverified habit. Docker's own linter emits two `SecretsUsedInArgOrEnv` warnings (`ARG` on line 2, `ENV` on line 3), and the value is then readable from `docker inspect` in the image config's `Env` array *and* from `docker history --no-trunc`. The key is no longer in a layer; it is in document (3) — the part that gets pushed, described, and printed by every platform's "show me this deployment" screen. The rule the day writes down is one sentence: **a secret belongs to the process's environment, not to the artifact that starts the process.**

This repository is an unusually good place to learn all of that, because its build context is **578 MB** and almost none of it is needed at runtime. Measured: `data/` 432 MB, `corpus_code/` 98 MB, `.venv/` 34 MB, `.git/` 5.7 MB, `docs/` 2.7 MB, `corpus/` 2.5 MB, `ai_os/` 2.0 MB, `tools/` 1.5 MB. The runtime needs five paths: `ai_os/`, `policy.yaml`, `.env.example` (as the *contract*, never as a source of values), `requirements.txt`, and `data/store.json` (262,182,307 B · 11,061 chunks · dim 768) for the docs corpus. It does **not** need the corpora (ingest-time inputs), the venv (the image installs its own — and this one is a *different interpreter's* venv, which is exactly the kind of thing that produces a working container with a subtly wrong environment), the git history, the docs, the tests, or the 94 `__pycache__` directories the naive build carried in. The honest allow-list image is **587 MB** against the naive build's **1.05 GB** — a **463 MB** saving, and the difference between an artifact with four live credentials and one with none.

And the general rule the numbers illustrate is the day's actual lesson: **an image is a subset of a repository, and the interesting work is deciding which subset** — a decision that is easy to get wrong *silently*, because the wrong answer is a container that runs. That is why the day's manifest is checked in **both** directions: every listed path must exist (a phantom entry is a lie), and every path in the context that is not listed must appear in `absent` **with a reason** (a missing entry is the leak).

**A container is not a deployment.** A container is a process with a filesystem and a namespace, and this application is a process whose entire state story is *in that process*. So "containerized" here means the same single-process application, under a filesystem, killable and restartable — and the honest sentence is that a container does not make this system distributed. It makes the **absence** of a process boundary visible for the first time, which is why §1's two-writer experiment is the centerpiece rather than a footnote. What the container genuinely buys: a reproducible file list, a declared dependency set, an isolated environment, a memory ceiling that can be **declared and violated measurably**, and a process that can be killed. What it does not buy: durability, identity, a second reader, or a health signal.

The last mechanical fact of the day, and the most common container failure in the world, is the bind address. `uvicorn ai_os.server:app` (`server.py:3–4`) binds `127.0.0.1` by default — measured: `ss -ltnp` shows `LISTEN 127.0.0.1:8111`, a loopback request answers `200`, and the same port on the host's routable address `192.168.1.46:8112` **refuses the connection**. Inside a container that is fatal in a specific way: `docker run -p 8000:8000` publishes the container's `eth0`, and a server listening on the container's *loopback* is unreachable from the host no matter what the port mapping says. The bind is therefore a **contract with three copies** — the Dockerfile's `CMD`, the documentation's run command, and the fixture that asserts both name `--host 0.0.0.0` — because a contract documented in one place is a comment.

---

## 3. The Secret Is a File the Build Context Will Ship

A repository has **two doors**, and this one has closed one and never knew the second existed. Git's door: `.gitignore:1` lists `.env`, and `git ls-files` confirms the real file is untracked while `.env.example` is tracked. Docker's door: the build context is the **directory**, and nothing in a directory is secret by default. Both mechanisms are correct and they are not the same rule, which is why the honest statement about this repository's secrets is not *"our keys are safe"* but **"our keys are safe from git and one `docker build` away from being in a layer."** The measured file is 1,026 bytes and 13 names, and the naive build copies every byte of it into the artifact without any human decision.

The mechanism the day has to teach is the layer, not the rule, because the rule ("don't put secrets in images") is already in `.env.example:1` and it was obeyed by git and ignored by Docker. A layer is a diff; `rm` in a later layer writes a whiteout; the earlier layer's bytes remain in the content-addressed store, in `docker save`, and in every registry the image has ever been pushed to. So the only way to keep a secret out of an image is to **never let the build context contain it** — which is what `.dockerignore` is, and which is why the day's `.dockerignore` is a **deny-by-default allow-list** (`*`, then `!` per manifest entry) rather than a list of exclusions. An exclusion list is a list of things somebody remembered, and the failure mode of forgetting is silent and irreversible in a layer; an allow-list's failure mode is loud, because a new file is excluded by default and shows up in N1 as a context path with no reason. The non-size entries are named explicitly because they are the ones that matter: `.env`, `.git/`, `.venv/`, `**/__pycache__/`, `**/*.pyc`, `*.pem`, `.netrc`, `.aws/`, and `data/*` with `!data/store.json` — so the store ships and nothing else in `data/` does.

The seam the repository actually needs is one line of code away from already being right, and the day is careful here because the fix is tempting. `config.py:23–37` reads `.env` from the working directory at import; `config.py:76` calls it inside `build_gateway_from_env`; `server.py:35` calls that at module load, inside a `try` that swallows the failure into `_build_error` (`server.py:36`, `:39`) precisely so `/health` stays informative. So the process **works with no environment at all** — excellent on a laptop and actively dangerous in a container, because a container whose *image* contains `.env` needs no secrets injected and will therefore start happily with yesterday's keys in production. And there is a **second, independent loader**: `llm_client.py:22–45` has its own `KEY=VALUE` parser and its own `LLM_BASE_URL`/`LLM_MODEL`/`API_KEY` chain, so a container that fixes the first loader can still be configured by the second — a two-door secret channel, found by reading rather than by trying.

So the secret contract has **three** parts, and each part is falsifiable: (1) `.dockerignore` names `.env`, the allow-list is in force, and the image is asserted to contain **zero** paths matching `\.env|\.pem|\.netrc|\.aws|credentials`; (2) the documented run command **injects** the environment (`--env-file /secure/ai-os.env`) and the process may fall back to a file only when one is explicitly mounted, never from the image; (3) the count is **zero** rather than "the file I thought of", so a future `.env.production`, `.netrc`, or `*.pem` is caught by the same rule instead of by luck.

And the cost the day states because nobody prices it: **what does a key rotation cost when the key was in an image?** The honest answer is *a rebuild of every layer that ever contained it, and a re-push, and a purge request to the registry, and an audit of what was pulled in the meantime* — which is why the day treats "the secret was in the artifact" as a **security event** and not a packaging detail. That is the sentence that makes the 20-line ignore file the highest-leverage artifact of the day.

---

## 4. A Health Check Is a Claim About Dependencies, and Both Obvious Ones Are Wrong Here

A health check is not "is the process running" — that is what a supervisor is for. A health check is the input to a **routing decision**: an instance that cannot serve should not receive traffic, and an instance that is fine should not be restarted. That makes it a claim about **dependencies**, and the claim has to be made *per class of request*, because this application serves at least four that fail independently:

| request class | needs | fails today as | where |
|---|---|---|---|
| chat message | a gateway (a key and a provider) | `200` with `{"error": …}` | `server.py:79–80` pattern, `config.py:88–90` |
| RAG question | a gateway **and** a 262 MB store | `200` with `{"error": …}` | `server.py:218–223` |
| data question | a database URL — or it silently answers from `SEED_SQL` | a confidently wrong answer | `data.py:798–806` → `data.py:455–458` |
| observability query | the trace store, which is in this process | `{"found": false}` after a restart | `observability.py:706–716` |

**One boolean over all four is a boolean over none.** That is the design argument for splitting the probe, and it is also the reason the readiness body is a **table of named checks** rather than a flag.

The current probe's specific defect is a better lesson than a generic "add status codes." `server.py:77–92` returns a `dict`, so FastAPI sends `200` in *both* branches, and the unconfigured branch puts `ok: false` in the body. Every orchestrator in the world — Docker's `HEALTHCHECK`, a Kubernetes readiness probe, an ALB target group, an uptime monitor — keys on the status code. Measured inside the built image: `/health` → `200 {"ok":false,"error":"missing API key(s) for providers: openai — set OPENAI_API_KEY in .env"}`; `POST /chat` → `200 {"error":"missing API key(s) for providers: openai…"}`; `GET /ledger` → `200 {"error":…}`; `POST /rag` → `200 {"error":…}`. So **a process that cannot answer a single question is maximally healthy by the only signal a platform reads.** The only route in the whole application that can return a non-200 on purpose is `POST /ingest/span` (`server.py:274`), which returns `404` when `TRACE_INGEST` is off (`server.py:282–288`) — and that is *correct behaviour*, which is why the day's control asserts that it stays correct rather than that it changes. A day that breaks a route to make a point has made a deployment instead of a measurement.

The two probes and what each costs, measured:

| probe | touches | measured cost | what it misses | status |
|---|---|---|---|---|
| **liveness** (`/healthz`) | nothing external | `200` in **1.87 s** at **35.6 MiB** RSS | a wedged event loop, a dead provider chain, an unopenable state file | `PENDING` |
| **readiness** (`/readyz`) | gateway · store · database · state | store load **4.94 s** and **705 MB** peak RSS; must be cached or it is paid per probe | nothing, at the price of the memory floor | `PENDING` |

The asymmetry is the whole lesson: **liveness is cheap because it is allowed to be shallow, and readiness is expensive because it is not.** A deployment that collapses the two — which is today's state, and only today's state — is paying the liveness price and calling it the readiness claim.

**The memory floor is a deployment decision, not a code decision, and it is measured:**

| limit | load time | result |
|---|---|---|
| unlimited | **4.94 s** | 705 MB peak RSS, 11,061 chunks |
| `--memory=1g` | 7.06 s | survives |
| `--memory=512m` | 12.67 s | survives |
| `--memory=384m` | 19.94 s | survives, **thrashing — 4× slower** |
| `--memory=320m` | — | **`exit 137`, SIGKILL** |
| `--memory=256m` | — | **`exit 137`, SIGKILL** |

Two consequences and the day takes both. **(1) Time-to-load degrades non-linearly as the limit approaches the floor** — a container that is merely slow at 384 MB looks like a slow *application*, not a memory-starved one, which is the most common way this is misdiagnosed in production. **(2) A readiness probe that loads the store under a 256 MB limit is a crash loop caused by the health check** — the probe kills the container, the restart policy restarts it, the probe kills it again, and the observable symptom is a flapping service whose logs show nothing wrong at all. So the floor goes into the deployment as a **declared precondition with a measured value and a refusal**: the manifest states `memory: 1g` with the reason (705 MB measured for the store, plus interpreter, plus headroom), and the fixture **refuses a manifest that declares less than the measured floor**, because a document that under-declares memory is a crash loop with a cause nobody can see.

What a health check must **never** do in this repository, listed with the same seriousness as Day 27's list of what an approval must not be:

- **Call a model.** A probe that spends a token per poll costs money and can be rate-limited into failing. The provider chain is reported as *configuration* — which is what today's `/health` already gets right at `server.py:83–86`, listing `name`/`model`/`base_url` and never making a call — and that is what it should keep.
- **Write state.** A probe that calls `get()` on the state store **rewrites the file** (`state.py:607` → `state.py:838–845`), so a readiness probe on a shared volume is a *writer*. A read-only probe must construct its store with `autosave=False`.
- **Hold the single-writer lock.** A liveness probe that took the lock would make the process unprobeable for as long as the probe ran.
- **Depend on a network call it does not control.** A provider outage is not a reason to remove an instance from a pool that has a cache, and a `Retry-After` of 429 seconds in a probe timeout is not a health signal, it is a bill.

---

## 5. Design: the Manifest, the Split, and the Refusal Register

### 5.1 The manifest is a file, and the image is checked against it

Day 28's ratchet was "every box in the diagram is a file." Today's is the same idea one level up, and the shape is the design:

```yaml
# docs/deployment.yaml — an image is a SUBSET of a repository, and the
#   interesting part is the subset.
#   * `why` is required per entry: a file nobody can justify is a file that
#     should not ship (measured: 578 MB context -> 587 MB image).
#   * `absent` is a first-class section. A deployment doc that lists only
#     what exists is a brochure.
image:
  base: python:3.12-slim
  runtime_user: ai-os            # never root
  exposes: [8000]
  healthcheck:                   # the *contract*, mirrored by ai_os/health.py
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
  - path: **/__pycache__/    why: another interpreter's bytecode (94 of them)
  - path: docs/              why: 2.7 MB of prose; the deployment doc is in git
limits:
  memory: 1g                  # measured: 384m survives, SIGKILL at 320m
  workers: 1                  # one process; the single-writer lock enforces it
```

**Status: `PENDING`.** The fixture (`tools/deploy_smoke.py`) resolves every `include` path against the filesystem and asserts **both directions** — every listed path exists, and every path in the build context that is not listed appears in `absent` with a reason. A manifest with a phantom entry and a manifest missing an entry are the same defect from opposite ends, and the second one is the one that ships the secret. With Docker present it also compares the manifest's expansion against the built image's `/app` file list, both directions, and prints the manifest's own byte count against the image's, so a store that grew by 40 MB cannot pass unnoticed.

### 5.2 The two probes are two functions in one module

`ai_os/health.py`, in the same register as `permissions.py` and `approval.py`: **decisions, not infrastructure**, with dependencies injected (`gateway`, `store_loader`, `state_path`, `clock`) so the whole module is testable offline at zero model cost. `liveness()` returns `(200, {"ok": True, "checks": {}})` and touches nothing — *cannot* fail on a dependency, which is the property that makes it safe to restart on. `readiness()` returns `(200 | 503, {"checks": {...}, "ok": bool})` where each check is a named tuple of `(name, ok, detail, cost_ms)`, so the body is a **table rather than a boolean**, and `503` is returned the moment any check fails. Four checks, in the order they matter: `gateway` (was `build_gateway_from_env()` constructible — a *local* fact, no network), `store` (present and loadable — cached after first success), `database` (`postgres` or `stub (SEED_SQL)`, **named explicitly**), `state` (writable, or explicitly disabled).

Two series rules apply unchanged and are worth restating because both are load-bearing. **The answering model and the deployment's shape may be promoted; the arguments may not** — so the readiness body reports *which* database backend and *which* store identity, never a query, a key, or a path segment. **A decision is a value with a consequence** — so `readiness()` returns `(503, body)` and **never** `(200, {"ok": False})`, which is the exact anti-pattern deleted from `server.py:79–80`.

Rejected explicitly, each with a reason: a `readyz` that pings the provider (costs money, can be rate-limited, and an outage is not a reason to drain a pool that has a cache); a probe framework (three routes and one helper do not need a dependency); and a `HEALTHCHECK` interval below 10 s, because a probe that loads a 705 MB store must not be polled like a ping.

### 5.3 The store check is cached, and the cache is the argument

The readiness `store` check pays 4.94 s and 705 MB on first call and then holds the store, so every later probe is a dictionary lookup. That resolves the tension in §4 without lying in either direction, and it is **Day 28's identity-keyed cache arriving at its first real use**. The day states the cost honestly in both currencies: the probe converts a per-request 4.94 s parse (`server.py:192–197`, called at `server.py:217`) into a **once-per-process** cost, and the memory becomes *resident* rather than *transient* — which is exactly why `memory: 1g` is in the manifest and not `512m`. Day 28 said the cache was a correctness decision (key the cache on the store's identity, or you serve yesterday's evidence as today's answer); Day 29 says it is also the difference between a probe that can be polled every ten seconds and one that cannot.

### 5.4 The single-writer guard is the day's one new mechanism

`SingleWriter(path)` opens `<state>.lock`, takes `fcntl.flock(LOCK_EX | LOCK_NB)`, and on failure raises `SecondWriter(pid=…, path=…)` — a refusal that **names the holder**. Wired into the process's start-up (so `uvicorn --workers 2` or a second replica on a shared volume **fails at boot with a sentence** rather than corrupting a file over the next hour) and into `readiness()`'s `state` check (so a process that *did* start without it is reported **unready** rather than broken).

The honest limits are stated in the same breath, because a lock whose limits are unstated is a claim: `flock` is **advisory** — every writer must take it, and the state store is the only writer today, which is a property the day owns rather than one it can assume — and it is **filesystem-dependent**, working on a local volume and a bind mount, with NFS semantics varying and **not claimed**. What it buys is enormous and specific: it converts "two writers lose records silently" from a drawing into a **refusal**, which is what allows the container to be honestly scaled to exactly one.

`WORKERS=1` in a comment is a hope. A lock at boot is a check. That distinction is the day's only genuinely new idea and it is worth one paragraph on its own: **every other bound in this repository is a number in a document** — `AGENT_MAX_STEPS`, `STATE_MAX_RECORDS`, `LLM_MAX_ATTEMPTS`, and Day 28's whole `capacity.yaml` — and this is the first bound in twenty-nine days that is *enforced by the operating system* and produces a refusal rather than a report.

### 5.5 What the day refuses, and where each refusal is written

A refusal nobody can find is a refusal nobody believes, so each is written at the seam where it would have gone, with the trigger that would end it.

1. **The rate limiter is still not attached.** The key is a header a caller chooses (Day 26: authorization without authentication; Day 28: *unattributable in fact, usable in practice*). The attachment is now behind TLS + identity + ALB in §5.6. **Owner: the day identity lands.** Refusing it today would mean counting a key anyone can forge, which is worse than not counting.
2. **No TLS, no reverse proxy, no service unit, no compose file.** The deliverable is a local production-like deployment plus a mapping table. A compose file would imply an orchestration story this repository has not earned, and Day 28's rule holds: **a trigger is a condition, not a quarter.**
3. **No `--restart` in the documented command.** A restart policy hides the crash loop §4's memory floor predicts, and the day wants the flapping service to be *visible* on a fresh run. The health check refusing to be silent is the deliverable.
4. **No `HEALTHCHECK` on the model path and no provider call in any probe.** A probe that spends a token per poll costs money and can be rate-limited into failing.
5. **`--read-only` is not claimed.** The process writes `data/` state and the ledger's companions, so the day records the paths that would need to be tmpfs instead of pretending the flag would work.

### 5.6 The AWS map, with what each service does *not* fix

The curriculum asks for "architectural depth first." A table with a **does-not-fix** column is the only form of depth that cannot become a wish, because the failure mode of cloud architecture prose is that every box looks like a solution and the limits live in a footnote nobody reads. Seven rows, in the order the findings appear:

| service | maps to | **does not fix** | local mechanism that stands in |
|---|---|---|---|
| **ECR** | the image | a secret already in a layer | `.dockerignore` allow-list + N2's zero-count |
| **ECS/Fargate** | the process and its memory limit | a readiness probe that lies; a second replica on a shared file | `docs/deployment.yaml` `limits` + N5's floor refusal |
| **Secrets Manager / SSM** | the key at run time | a key that is *in the image* | `--env-file` at run time |
| **RDS Postgres** | `SALEOR_READONLY_DB_URL` and the `saleor_read_only` role | two writers on `state.json` (a different store entirely) | `data.py:798–806` seam, announced by readiness |
| **S3** | the corpus and both store files | "a corpus change is a rebuild today" | the `data/` allow-list entry |
| **ALB** | a health check that can actually fail, plus TLS termination | **identity** | `/healthz` + `/readyz` status codes |
| **CloudWatch** | the shape `/metrics` and `/logs` already have | retention, sampling, and a sink that does not exist | `server.py:249–255`, `:258–266` |

Two rows are worth reading twice. **RDS does not fix the two-writer problem**, because the state store is not the database — a reader who saw "Postgres" in a deployment diagram and concluded the concurrency ceiling was gone would be wrong in the specific way Day 28 spent a whole day documenting. And **S3 does not fix the rebuild**, because today `data/store.json` is an image input: a corpus refresh is a **new image**, not a new object, and a 262 MB artifact inside a 587 MB image makes that trigger *more* likely, not less. The day says so rather than letting the mapping imply the problem was solved by drawing a bucket.

### 5.7 What the documents carry forward

Three claims stay visible in the deployment document, not only in the report, because a deployment doc that omitted them would be describing a system the series has spent thirteen days proving cannot write: **`Wrote? = no`** (unchanged, and now with a second reason — there is no write path, so there is nothing to scale and no migration to run), **`Saleor rows changed: 0`** (Day 27's lifecycle number, and the reason `SALEOR_READONLY_DB_URL` is a *read-only* URL), and **`0 chats`** for every `MECH` fixture in the series, which is the reason twenty-nine days of this were affordable at all.

---

## 6. Build: the Dockerfile, the `.dockerignore`, and the Health Module

**Status: `PENDING` — not written, not run.** Four artifacts, and the discipline is that the ignore file is the one that matters most and the health module the one that matters most afterwards.

**`Dockerfile`**, at the repository root, in the same register as every other file in this project: a decision, written down, with the reasons in comments — and deliberately not a tour de force. One stage, because the dependency set is four packages and a multi-stage build would be a second artifact to keep in sync for no measured gain. A pinned base (`python:3.12-slim`, and the day records *why* 3.12 and not this host's 3.14: **the image must not inherit the laptop's interpreter**, and the 34 MB `.venv` in the context is exactly that trap). `WORKDIR /app`; `COPY requirements.txt` then `pip install --no-cache-dir -r requirements.txt` — the `--no-cache-dir` is a measured decision, because a pip cache in a layer is a layer nobody can delete. Then the manifest's `include` set and nothing else, a non-root `USER`, `EXPOSE 8000`, a `HEALTHCHECK` that calls the **contract endpoint** and not a bespoke command, and `CMD ["uvicorn","ai_os.server:app","--host","0.0.0.0","--port","8000"]` — the bind §2 measured as load-bearing.

**`.dockerignore`** — the deny-by-default allow-list from §3, and the difference between a 1.05 GB image carrying four live credentials and a 587 MB image carrying none.

**`ai_os/health.py`** — `liveness()`, `readiness()`, the per-dependency check tuple, and the store-identity cache from §5.3. Two rules apply unchanged: the readiness body reports *which* database backend and *which* store identity and never a query, a key, or a path segment; and `readiness()` returns `(503, body)`, never `(200, {"ok": False})`.

**Two additive routes in `server.py` — `GET /healthz` and `GET /readyz` — and nothing else changes.** The old `/health` (`server.py:77–92`) stays **byte-identical**, because something in the series may depend on it and because deleting an anti-pattern by *adding a correct thing* is the only way to do it without breaking a contract. `server.py:35`'s import-time gateway build is **not** moved: hoisting it is the right refactor, but it changes *when* a misconfiguration becomes visible, and the day refuses to move a failure's timing inside a day whose deliverable is a container. The comment at that seam says exactly that, in one sentence, which is cheaper than the refactor and more honest than the refactor would be today.

The current route inventory, verified, is the baseline the two routes are added to: `/health` (`:77`), `/chat` (`:95`), `/chat/stream` (`:134`), `/traces/{trace_id}` (`:171`), `/traces` (`:180`), `/ledger` (`:185`), `/rag` (`:200`), `/metrics` (`:249`), `/logs` (`:258`), `/ingest/span` (`:274`). Eleven today; thirteen after.

---

## 7. Build: the Single-Writer Guard, the Database Announcement, and the Census

**Status: `PENDING` — not written, not run.** Three changes, and **exactly one** of them alters shipped behaviour.

**The guard** is §5.4, with the failure written at the seam where it would have gone: `SecondWriter` naming the holder's pid and path, the process exiting non-zero, and the message naming the fix (*"two writers on one state file lose records; run one process, or give each its own `STATE_STORE_PATH`"*). The refusal is the deliverable and the fixture asserts the **defect first** (N4), because a guard with no demonstrated failure is a guard nobody can trust.

**The database seam announces itself** — the one behavioural change the day makes to shipped code, and it is one line's worth: readiness reports `database: postgres` or `database: stub (SEED_SQL)`, turning the silent substitution at `data.py:798–806` → `sqlite3.connect(":memory:")` + `SEED_SQL` (`data.py:455–458`) into a **named, visible degradation** in the response to a probe an operator actually reads. The day does **not** change the default, does **not** remove the stub, and does **not** add a warning to every query — the stub is what every `MECH` check in twenty-nine days runs on, and removing it would invalidate the series' own regression gate. Same class of decision as Day 14's `fails-loud`: the asymmetry Day 28 recorded (the RAG path refuses loudly at `server.py:218–223` while the data path substitutes silently) is closed **at the boundary** rather than in the layer, which is the only place it can be closed without changing every answer in the series.

**The census** is the day's smallest artifact and the one most likely to be quoted, so here is the whole method, because **a census without a method is a number that can be wrong silently** — and this file's §0 records that the plan's first version was wrong by one name and had an impossible after-row.

*Scan scope:* `ai_os/**/*.py` only. *Read rule:* every name passed as the first argument to `os.getenv` / `_env_int` / `_env_float` where the argument is a string literal or a module-level string constant, **plus** the `{PROVIDER}_API_KEY` / `_BASE_URL` / `_EMBEDDING_MODEL` names that `config.py:60–65`, `:83`, `:86` build by f-string for the three registered providers — `openai` (`providers/openai.py:83`), `anthropic` (`providers/anthropic.py:86`), `cloudflare` (`providers/cloudflare.py:31`) — which is 9 reachable names, of which `OPENAI_API_KEY` is already read literally at `config.py:60`, leaving **8** additional. *Documented rule:* every name with a value assignment in `.env.example`, counting the four the template explicitly tells the reader to uncomment (`CLOUDFLARE_API_KEY` at `.env.example:43`, `CLOUDFLARE_ACCOUNT_ID` at `:44`, `SALEOR_READONLY_DB_URL` at `:53`, `STATE_STORE_PATH` at `:62`).

| | count | notes |
|---|---|---|
| names the code can read | **64** | 56 literal/constant-resolved + 8 provider-template expansions |
| documented in `.env.example` | **26** | 22 uncommented + 4 commented-out-but-documented |
| documented **and** read | **25** | the 26 minus `CMD_ZDR` |
| read but undocumented | **39** | `64 − 25` |
| documented and read by nothing | **1** | `CMD_ZDR` — and only this one |

The `CMD_ZDR` row is the interesting one, for the second day running. A literal-only scan reports **five** documented-but-unread names — `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `CLOUDFLARE_API_KEY`, `OPENAI_BASE_URL`, `CMD_ZDR` — and **four of those five are false positives**: they are exactly the f-string names a literal scan cannot see. Calling them inert would have put four false exceptions into a document whose whole claim is that it is not full of false claims. Only `CMD_ZDR`, which no file in any path reads, survives as a genuine exception. Day 27 flagged it, Day 28 made it a fixture, and Day 29 keeps it a fixture — a flag in the template that reads nothing is the cheapest documentation bug in a repository and it has now survived three days.

**The reconciliation with Day 28 is the most interesting sentence in the section, and it goes in the report verbatim.** Day 28 printed `60 / 21 / 39`; today measures `64 / 26 / 39`.

- **The read column reconciles exactly.** `60 → 64` is `+4`, and the four are precisely Day 24's `TRACE_*` knobs: `TRACE_INGEST` (`server.py:270`), `TRACE_METRICS` (`metrics.py:227`), `TRACE_LOGGING` (`structured_logging.py:58`), `TRACE_PROPAGATION` (`observability.py:1107`). Not one of them is in `.env.example`.
- **The documented column does not reconcile, and every unit of the `+5` has a name.** `.env.example` has not changed since Day 17 (`e1c796f`) and is byte-identical to `HEAD` (`3f4f18c`) — 26 names have sat in the template for twelve days. Day 28's `21` was `22` active assignments **minus `CMD_ZDR`**, which it counted as an exception rather than as a documented name. So `21 → 26` is the four commented-out lines plus `CMD_ZDR` returning to its own column.
- **The missing column does not move at all: `60 − 21 = 39` and `64 − 25 = 39`.** The four names added to the read column are the same four that were added to the documented column, so two columns grew together and cancelled.

That last bullet is the day's smallest real finding and it is worth more in a report than a larger number would be: **a census whose middle column changed method and whose bottom row did not move is a more informative artifact than a number that went up.** Both triples are reported; **neither is called the correction**, because a count whose method changed between two days cannot be differenced without naming the method, and the method is the deliverable.

---

## 8. Build: the Deployment Document, the Checker, and the Two Reports

**Status: `PENDING` — not written, not run.**

`docs/deployment.md` is the deliverable, in this order and no other: **the manifest** (what is in the image, what is not, and why) · **the secret contract** (three parts, the `.dockerignore` rule, the run command, and the count of secret-shaped paths in the image — asserted zero) · **the two probes** (path, touches, timeout, status codes, and the *measured cost of each*) · **the one-writer contract** (one replica, one lock, what the lock does not cover) · **the two-process register** (the three measured failure modes from §1, each with the file and line that makes it happen and the guard that now refuses it) · **the AWS table** (seven rows, each with its local stand-in) · **the cold-start table** · and the residual register with owners.

The checker is a **fixture, not a linter**: `tools/deploy_smoke.py` with a `--no-docker` mode, so everything except the build runs offline and the N-set is cheap and deterministic on a machine with no daemon. It parses `docs/deployment.yaml` and asserts the manifest in both directions; scans `ai_os/` for env names and compares against `.env.example` plus the exception list; asserts the `Dockerfile`'s bind, `USER`, `EXPOSE`, `HEALTHCHECK`, and `CMD`; and runs the two-writer sequence, the probe bodies, and the parity check. With Docker present it additionally builds, asserts the image's file list, and runs the container. **A linter reports and exits; a fixture fails the day's own control when the deployment and the repository stop agreeing** — which is the only thing that makes a manifest trustworthy six weeks from now, and the reason Day 28's ratchet is inherited rather than reinvented.

`docs/evaluations/deployment-day29.md` carries the N1–N9 table, the image manifest, the cold-start and memory-floor tables, the census (`64 / 26 / 39 → 66 / 33 / 35`), the secret assertions, and the `PENDING` lines. The day's report is unusual in one respect and the notes must say why: **its headline numbers are a leak and a floor** — 1,026 bytes of secrets that were in an image until a 20-line ignore file was added, and a memory floor at which the readiness probe kills the container — because on a deployment day the honest measurement of a system is not what it can do but **what it will do to itself when you give it a memory limit and a build context**.

`docs/concepts/deployment.md` joins the **seventeen** concept documents already in `docs/concepts/`, and all three documents keep the same three lines visible: **what exists, what does not, and who owns each absence.**

---

## 9. Test: N1–N9, Pre-Registered

**Status: every row `PENDING` — not run.** Expectations were written before implementation, which is the series' standard and the reason the rest of this file is worth anything. Each row names the assertion *and* the way it is falsified, because an assertion with no falsification route is a wish.

| id | proves | assertion | falsified by |
|----|--------|-----------|--------------|
| **N1** | the image is the manifest | every `include` path exists; every build-context path not in `include` appears in `absent` **with a reason**; both missing-pledge cases (listed file that does not exist, context file with no reason) are *refused*; the manifest's byte count is printed against the image's | a phantom `include` entry, or a context path with no reason — the second one is the leak |
| **N2** | the secret is not in the image | `.dockerignore` names `.env`; a deny-by-default allow-list is in force and an **exclusion-only file is refused**; the built image contains **zero** paths matching `\.env\|\.pem\|\.netrc\|\.aws\|credentials`; the documented run command injects the environment | one matching path, or an exclusion-only ignore file — its failure mode is silent, which is why it is refused |
| **N3** | the probes can fail | liveness returns `200` with an empty check table in a process whose gateway is broken, store missing, and no database — liveness asserted **unable** to fail on a dependency; readiness returns `503` with a **named** failing check for each of the four dependencies in turn; `200` only when all four pass; the old `/health` is asserted **unchanged**, including its `200` with `ok: false` | a liveness call that fails on a dependency, a `200` from `/readyz` with a failing check, or any byte of change to `/health` |
| **N4** | the defect is asserted, then the guard refuses it | the two-writer sequence runs against the real `StateStore` — A holds 40, B boots at 31, B's first write drops A's 10 later records, **`exceptions_raised == 0`** — and the *same* sequence with the guard asserts the second process is refused at boot with `SecondWriter` naming the holder; both tables printed | a second process that starts successfully on a held lock, or a lost record that raises an exception instead of passing silently |
| **N5** | the memory floor is measured, not assumed | the harness loads the store under the declared limit and one step below the measured floor, printing seconds and peak RSS for each, and **refuses a manifest whose `memory` is below the measured floor** | a manifest that loads and passes while declaring a limit at which the load is killed |
| **N6** | the bind contract has three copies | the `Dockerfile`'s `CMD` names `--host 0.0.0.0`; the documented run command names it too (and `server.py:3–4` is the one that does **not** — the fixture *reports* the divergence and the day fixes the docstring, because the docstring is the command a reader copies); with Docker, a request to the published port **from a non-loopback address** answers `200` | a `200` on loopback with a refusal on the routable address — which is exactly today's behaviour and is the bug N6 exists to catch |
| **N7** | the configuration surface is counted, and the count has a method | code-read names = documented + named exceptions; prints `64 / 26 / 39` before and `66 / 33 / 35` after, with the §7.4 method line travelling next to the number; `CMD_ZDR` justified in a comment; `AI_OS_IMAGE` named as a **scoped** exception with its reason | any undocumented readable name, or any *bogus* exception — the `ANTHROPIC_API_KEY` case in §7.4 is the canonical trap, and a fixture that counts template names as literals would produce four of them |
| **N8** | the database seam is announced and the read-only role is reachable | the image's `pip freeze` contains `psycopg`, so `SALEOR_READONLY_DB_URL` is *reachable* rather than silently downgraded; readiness reports `postgres` with the URL set and `stub (SEED_SQL)` without it; the stub case is what every offline fixture uses, so the fixture asserts the **label** and not the backend choice; with the URL set and no `psycopg`, the data layer must raise (`data.py:520–524`) | a readiness body that says neither `postgres` nor `stub`, or a silent downgrade when the driver is missing |
| **N9** | the control — the container is the same system | the offline N-set runs **twice**, once on the host and once inside the built image, and every verdict is identical: same fixture ids, same pass/fail, answers byte-identical, same tool sequence, same SQL text hash, same rows, same tokens, same cents, **0 chats, 0 embeds, 0 SQL, 0 rows**; the prior suites are re-run unchanged — the list is `tools/regress_all.py:59–77` verbatim, plus the two layer-1 replays at `:81–82` | any prior fixture moving, any verdict differing between host and image, or a differing answer between the two runs |

**N4 is the first control in this series that asserts a defect is *present*.** Days 7–28 asserted that correct things stay correct; N4 asserts that a real bug still reproduces, and then that the new guard refuses it. The order matters and is the reason the fixture prints both tables: a guard with no demonstrated failure is a guard nobody can trust, and a defect that has been *fixed* silently is a defect whose fix will be reverted by the next refactor.

**N9's honesty clause belongs in the report verbatim, in the same table as the pass:** the image is **not** the environment. The container has no `.env`, so the `gateway` check on the image is `false` **by design**, and a reader who runs `docker run` without `--env-file` and sees `503` from `/readyz` is seeing **the probe working**, not the deployment failing. A control whose expected output on the artifact under test is a failure is a control that can be faked by breaking the build, and the day says so out loud rather than letting a green row imply the container is serving traffic.

**MECH vs not.** The image file list, the `.dockerignore` rule set, the secret's absence from the image, the two probe status codes and bodies, the single-writer refusal, the census, the `0.0.0.0` bind contract, the database announcement, the host/container parity, and the off/on equivalence are `MECH`. Whether 587 MB is a right image size, whether 4.94 s is an acceptable cold start, whether 1 GB is the right memory request, whether ECS/Fargate is the right compute primitive, whether one replica is the right number, whether S3 is the right home for a 262 MB store, whether ALB is the right place to terminate TLS, and whether an advisory `flock` is an acceptable precondition are **not** — and the day will not write a line claiming otherwise. A green N-set proves the **mechanisms and the refusals**; it does not prove the **sizing, the platform, or the policy**, and those have different owners. That sentence is now the series' standard and appears here for the fifth consecutive day.

---

## 10. Measure: the Report, the Cold-Start Table, and What Is Still `PENDING`

**Status: the *measurements* below are `Verified`; everything about the day's own artifacts is `PENDING`.** The report's shape is specified in §8.

**The cold-start narrative** is the day's second headline artifact, seven rows, **zero of them a crash**, and the last one is the only row that silently destroys something a user was told exists:

| t | event | consequence | label |
|---|-------|-------------|-------|
| 0 | `docker run` starts | the process begins; `config.py:23–37` finds no `.env` | — |
| 1 | import reaches `server.py:35` | the gateway fails to build; the error is kept in `_build_error` (`server.py:39`) and `/health` **still answers `200`** | `fails-quiet` |
| 2 | first `/healthz` | `200` in **1.87 s** at **35.6 MiB** — the process is alive and cannot serve | — |
| 3 | first `/readyz` | `503`, `gateway: missing API key`, no store loaded, 0 extra MB | `fails-loud` |
| 4 | operator passes `--env-file` | the gateway builds; `/readyz` now pays the store check: **4.94 s, 705 MB** | `fails-slow` |
| 5 | a `/rag` request arrives | reuses the cached store instead of paying 4.94 s again | — |
| 6 | a second container starts on the same volume | **refused at boot** by the single-writer lock, naming the holder's pid | `fails-loud` |
| 7 | the restart policy restarts the first one | the trace store is empty again: a trace id from `t=5` is unfetchable, and the ledger's total is `$0.00` | `fails-lost` |

Every one of `t=0`–`t=6` is a **Verified** measurement or a mechanism that exists; `t=7` is **Verified** as a property of the current code (a trace id from one process is unfetchable in a fresh process; a fresh process has zero traces, zero ledger rows, and `$0.00` — the ledger is a per-process list at `ledger.py:65` and the trace store is a per-process `OrderedDict` at `observability.py:706–716`).

**The questions the day exists to answer**, each with its honest status today:

| question | answerable today? | answer |
|---|---|---|
| What is in the build context, and how much of it does the runtime need? | yes | 578 MB; five paths; 463 MB saved by the honest allow-list |
| Is the live `.env` in an image built the obvious way? | yes | yes — 1,026 bytes, four credentials, layer `0330bff71c63` |
| Does `RUN rm` remove it? | yes | **no** — the bytes remain; a whiteout hides them from the merged view |
| Where do the secrets come from in a correct deployment? | yes | the run environment; `ARG`/`ENV` is measured to be the wrong channel |
| How long and how much memory to be *alive*? | yes | 1.87 s, 35.6 MiB |
| How long and how much memory to be *ready*? | yes | 4.94 s, 705 MB — and `SIGKILL` at a 320 MB limit |
| Can two instances share this state store? | yes | **no** — 10 records lost with zero exceptions, or `FileNotFoundError` at `state.py:845` |
| What refuses the second instance? | **no** | `PENDING` — the lock is designed and unwritten |
| Which database answered, and how would an operator know? | **no** | `PENDING` — the seam is silent at `data.py:798–806` |
| What happens to a trace and a ledger across a restart? | yes | both gone; `$0.00` and `fetched: false` |
| Is one replica the right number? | **no** | `PENDING`, owner: whoever deploys this |
| Is 587 MB the right image size? | **no** | `PENDING`, owner: whoever deploys this |

The eighth row is the day. Everything above it is inventory; that row is the deliverable, and it is the first row in this table whose answer is a **refusal** rather than a measurement — which is the whole argument for §5.4 existing.

**The cost shape, before and after.** *Before*: the deployment surface was a docstring (`server.py:3–4`), a 578 MB build context, a `.env` read at import, and a `/health` that could not fail. *After*: a 587 MB image with an enumerated manifest and zero secret-shaped paths, a 20-line ignore file standing between that and a 1.05 GB image with 1,026 bytes of live credentials in a layer, two probes with status codes and measured costs, and one lock that refuses the second writer. The machine cost of everything the day builds is **0 chats, 0 embeds, 0 SQL, 0 rows**; the machine cost of the day itself is one image build, two `docker save`/`docker run` inspections, and a bounded set of `--memory` probes. And the cost the series still cannot measure: **what a key rotation costs when the key was in an image** — a rebuild of every layer that ever contained it — which is why the day treats it as a security event.

---

## 11. Documents

- `docs/deployment.md` — **the deliverable**, `PENDING`. Manifest, secret contract, the two probes with measured costs, the one-writer contract, the two-process register, the seven-row AWS table, the cold-start table, and the residual register — with the carried claims (`Wrote? = no`, `Saleor rows: 0`, `0 chats`) visible in the document rather than only in a report.
- `docs/evaluations/deployment-day29.md` — **the report**, `PENDING`. The N1–N9 table with the "effect that did not happen" column carried from Day 26; the image manifest; the cold-start and memory-floor tables; the census (`64 / 26 / 39 → 66 / 33 / 35` with the §7.4 method line); the secret assertions; and the `PENDING` lines with owners.
- `docs/concepts/deployment.md` — **the concept doc**, `PENDING`, joining the seventeen already in `docs/concepts/`.
- `docs/deployment.yaml` — **the manifest**, `PENDING`. `include` with a `why` per entry, `absent` as a first-class section, and `limits` with the measured floor.
- `Dockerfile` — `PENDING`. One stage, pinned base, `--no-cache-dir`, non-root, `EXPOSE 8000`, `HEALTHCHECK` on the contract endpoint, `CMD` with `--host 0.0.0.0`.
- `.dockerignore` — `PENDING`. Deny-by-default allow-list; the highest-leverage artifact of the day.
- `ai_os/health.py` — `PENDING`. `liveness()`, `readiness()`, the check tuple, the store-identity cache.
- `ai_os/server.py` — `PENDING`. Two additive routes (`/healthz`, `/readyz`) and nothing else; `/health` byte-identical; `server.py:35` unmoved with the reason in a comment.
- `tools/deploy_smoke.py` — `PENDING`. The checker, as a fixture, with `--no-docker`.
- `.env.example` — `PENDING`. The four `TRACE_*` knobs documented, the three `AI_OS_*` knobs added in the documented style, `AI_OS_IMAGE` named as a scoped exception, `CMD_ZDR` kept as the one named exception with its reason.
- **Not written, on purpose, with an owner each**: no TLS, no reverse proxy, no service unit, no compose file (§5.5.2); no limiter attachment (owner: identity lands); no provider call in any probe; no `--restart` in the documented command; no `--read-only`; no second replica (refused at boot by design); no continuous delivery; no trace sink and no retention; no connection pool or read replica (trigger: two processes, which today's lock *delays* rather than removes); no ANN index or vector store (trigger: corpus × 10); no object storage (trigger: corpus refresh slower than the deploy — a trigger a 262 MB store inside a 587 MB image makes **more** likely); and **no claim that this system is deployed**, which is a line the rollup is never allowed to write.
- `docs/PROGRESS.md` — **deliberately not touched, and the reason is on the record.** The rollup table's last row is Day 23; Days 24–28 have notes but no rollup row, so the gap is now five days old and predates this file. Adding a Day 29 row would either claim `DONE` for artifacts that do not exist or claim `PENDING` for a day whose plan is not finished. The rollup is `PENDING` with **no owner yet**; that is a real gap across five documents, named rather than quietly widened.
- **Corrections to the record**: §0 lists six, all in this file's own plan — the census (`63 / 26 / 38` → `64 / 26 / 39`, and an after-row that no method could produce → `66 / 33 / 35`), `observability.py:744` → `:714` for the trace cap, the `.env` name count (14 → 13) and its credential parenthetical, the exit check's "four AWS services" → seven, the N9 regression list (`I1–I9` → `D1–D9` plus the two missing bench suites and three suites that are not in the gate), and two sizes that moved while the day was written (`docs/` 2.6 → 2.7 MB, `.git/` 5.6 → 5.7 MB). **Five of the six are the same defect Day 28 retracted at larger scale, and one of them — a regression list written from memory — is the exact failure N9 is built to prevent.**

---

## 12. Daily Learning Record

### Problem
Deployment is normally a checklist, and a checklist cannot be wrong. The problem the day actually solved is that this repository had never been given a second copy of itself, and the second copy turned out to be the interesting part. Concretely, and in the order the day found them: a 1,026-byte `.env` with four live credentials sitting inside the 1.05 GB image that the obvious `COPY . .` produces; a whiteout that hides a secret from the container while leaving every byte in the layer; an `ARG`/`ENV` "fix" that moves the key into the image *configuration* where `docker inspect` prints it; a `/health` that answers `200` in both branches while the process cannot answer a single question; a readiness check that costs 4.94 s and 705 MB and dies at a 320 MB limit; a state store where a *read* is a full-file rewrite; a bind address that makes a published port unreachable; and two containers on one volume that either lose ten records with zero exceptions or kill a writer with `FileNotFoundError`.

### Concept
**A deployment is a claim about what happens to state when the process is replaced.** Four supporting ideas did the real work. *An image is three objects — a file list, a stack of diffs, and a config document* — and the leak lives in the gap between them, because a whiteout is an instruction to the union filesystem and not a deletion from a content-addressed store. *A secret belongs to the process's environment, not to the artifact that starts the process*, which is the only formulation of the rule that survives being quoted at somebody who was about to use `ARG`. *A health check is a claim about dependencies, made per class of request*, so liveness is cheap because it is allowed to be shallow and readiness is expensive because it is not. And *one writer enforced by a lock is different in kind from one worker documented in a comment* — the first is the only bound in twenty-nine days that the operating system enforces and that answers with a refusal.

### Understanding
The synthesis is that **the four things that decide whether a deployment is correct are all outside the code**: the build context, the memory limit, the published port, and the injected environment. That is why a Dockerfile is a claim and not a control, and why the day's deliverable is a manifest with a fixture over it rather than a file with a `rm` in it. The second insight is that the cheapest and most valuable thing the container bought was not isolation, reproducibility, or a restart policy — it was **the ability to run the experiment**. Twenty-eight days of reasoning about a second writer from the absence of a lock became a measurement with two interleavings, two outcomes, and a specific line number, and the answer was worse than the drawing. The third is that `SIGKILL` at 320 MB turns a *document's* number into a process's death, which is the first time in this series that a value written in prose could kill a running thing — and that is the whole argument for measuring the floor instead of picking a round number.

### Implementation
`PENDING` — the day's implementation is specified in §6–§8 and has not been written. What is already true of the shape: the diff is small by construction (one `Dockerfile`, one ignore file, one module, one manifest, one harness, three documents), **exactly one** behaviour in shipped code changes (the readiness body naming the database backend) plus two additive routes, and `agent.py`, `tools.py`, `planner.py`, `executor.py`, `orchestrator.py`, `memory.py`, `sql_safety.py`, `permissions.py`, `approval.py`, `worker.py`, `metrics.py`, `structured_logging.py`, the seven tools, and `REASONING_PROMPT` all gain **nothing**. The measurement that will justify the design is 0 chats, 0 embeds, 0 SQL, 0 rows.

### Failure
Six failures, all in this day's own plan, and five of them are one failure. The census was wrong (`63` instead of `64`, and an after-row of `63 / 31 / 32` that no method can produce); the trace cap was cited at `observability.py:744`, which is a real line and the wrong line; the `.env` was said to hold 14 names and holds 13; the exit check asked for four AWS services out of seven; and **the N9 regression list was written from memory** — `I1–I9` for a family that is really `D1–D9`, missing `R1–R9` and `AB1–AB9`, and crediting three suites that are not in the gate. That last one is the expensive one, because N9's entire claim is that the prior suites are re-run *unchanged* and the definition of "unchanged" was wrong in the document that defines it. The lesson is not "be careful." It is that **a plausible number in a document is indistinguishable from a verified one, and the only defence is a method that a reader can re-run** — which is why the census method travels with the census, and why §7.4 spells out the scan scope, the read rule, and the documented rule in full.

### Debugging
The census was the hard part, and the interesting part is that a *reasonable* scan was wrong in a new way each time. An AST walk for `os.getenv`/`_env_int`/`_env_float` with module-level constant resolution gives **56** names — a clean, quotable, wrong answer, because `config.py:60–65` builds credential names by f-string from the provider name, so eight more are reachable at runtime. Getting from there to `64` required enumerating `PROVIDER_REGISTRY` properly (it is populated by a `@register` decorator at `providers/__init__.py:113–115`, not by a literal dict, so the naive parse of it returned *zero* providers and a total of 56) and then noticing that `CLOUDFLARE_ACCOUNT_ID` is a plain literal at `config.py:69` and was never part of the template set at all — which is how the plan came to name it as a deduplicated template expansion and lose a name. The other four false positives came from the opposite error: counting a commented-out `.env.example` line as documented, which moves the documented column by five and changes the meaning of the whole table. The fix in both directions was the same: **ask what the process can actually read and what a reader can actually find, then state the inclusion rule next to the number.**

### Evaluation
Pre-registered N1–N9 with a falsification route for each (§9), including the two that matter most for honesty: **N4** asserts that a real defect still reproduces — 10 records lost, `exceptions_raised == 0` — *before* asserting that the guard refuses it, which is the first control in the series that tests a safety property rather than a behaviour; and **N9** asserts host/container parity with the note, in the same table as the pass, that the image's expected `gateway: false` and a `503` from `/readyz` are the probe **working**. A green N-set proves the mechanisms and the refusals. It does not prove the sizing, the platform, or the policy, and the day will not write a line claiming otherwise.

### Architecture
The deployment is one process, one writer, one lock, two probes, and a manifest — and the map to seven AWS services is a **table of what each one does not fix**, because the two rows that matter most are the ones that look like solutions: RDS does not touch the state store, and S3 does not touch the rebuild. The eleven absences are the day (no identity, no TLS, no reverse proxy, no secret manager, no trace sink, no pool, no read replica, no object storage, no compose file, no second replica, no continuous delivery), each with an owner and a trigger, none of them a wish. The one conclusion worth keeping: **a deployment is the first artifact in this series whose correctness is decided by something outside the code**, and every one of those four things is a value a reader can check — which is why the deliverable is a manifest with a fixture over it and not a diagram with a paragraph under it.

### Questions
Does an advisory `flock` hold on the volume types a real orchestrator actually mounts, or does the guard need to be a database lease before the second replica is ever tempting? What is the right home for a 262 MB store that is read on every process start and written on every state mutation — a mounted volume, object storage with a local cache, or a real vector database — and does the answer change the readiness probe's cost? Should `/health` be *changed* rather than supplemented, given that it is structurally unable to fail and a reader will always call the oldest route "the health check"? And is a `503` from a fresh `docker run` the right first impression, or should the refusal be written where a human looks first?

### Tomorrow
Day 30 is the **defence** — the capstone, and the first day in the series whose job is to explain this system to somebody who did not build it. It inherits a packaged, probe-able, single-writer deployment and a manifest that can be asserted false, and it inherits the caution that the most impressive thing about a deployment document is the part that is not in it. The items that stay `PENDING` and are **not** Day 30's: whether 587 MB is the right image size, whether 4.94 s is an acceptable cold start, whether 1 GB is the right memory request, whether one replica is the right number, and every claim about what a managed platform does with a probe it considers failing. And the sentence the rollup is never allowed to write: **"the system is now deployed."** The system is *packaged and probe-able*, which is smaller, and Day 30's defence is about a system that can be started, measured, and explained.
