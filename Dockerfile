# Day 29 — AI OS Deployment Image
#
# One stage: the dependency set is four packages; a multi-stage build would be a
# second artifact to keep in sync for no measured gain.
#
# Base is pinned to python:3.12-slim (not the host's 3.14) because the image must
# not inherit the laptop's interpreter. The 34 MB .venv in the context is exactly
# that trap — a different interpreter's bytecode produces a working container with
# a subtly wrong environment.
#
# --no-cache-dir on pip install is a measured decision: a pip cache in a layer is
# a layer nobody can delete.
#
# The manifest in docs/deployment.yaml is the source of truth for what ships.
# This Dockerfile copies exactly the include set and nothing else.
# WS0: requirements.txt is hash-pinned (direct + transitive closure, cp312+cp314
# wheels); pip enforces hashes automatically in hash-checking mode.

FROM python:3.12-slim

# WS0 — OCI labels for traceability; the SBOM artifact is generated below.
LABEL org.opencontainers.image.title="ai-layer" \
      org.opencontainers.image.base="python:3.12-slim" \
      org.opencontainers.image.revision="ws0"

# Runtime user — never root; the store is 0644 and read-only
RUN groupadd -r ai-os && useradd -r -g ai-os ai-os

WORKDIR /app

# Install dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the manifest's include set
COPY ai_os/ ./ai_os/
COPY tools/ ./tools/
COPY policy.yaml .
COPY .env.example .
COPY requirements.txt .
COPY data/store.json ./data/store.json

# Ensure data directory is writable by ai-os user (for state.json and lock file)
RUN mkdir -p /app/data && chown -R ai-os:ai-os /app/data

# WS0 — SBOM generation in the image build (CycloneDX JSON, stdlib-only writer).
# Stored outside /app so the Day-29 manifest include set is unchanged; CI copies
# it out as a build artifact (`docker build --output type=local,dest=./sbom-out`
# or `python tools/sbom.py --out sbom.json`). For attestations prefer:
# `docker buildx build --sbom=true --provenance=true .`
RUN mkdir -p /sbom && python tools/sbom.py --out /sbom/sbom.json && chown -R ai-os:ai-os /sbom

# Non-root user
USER ai-os

# Exposed port (documentation; the HEALTHCHECK and CMD use 8000)
EXPOSE 8000

# HEALTHCHECK calls the contract endpoint, not a bespoke command.
# Interval >= 10s because a probe that loads a 705 MB store must not be polled
# like a ping. The liveness probe (/healthz) is shallow; readiness (/readyz) is
# the one that pays the store load cost.
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/healthz || exit 1

# Bind to 0.0.0.0 — measured load-bearing. uvicorn defaults to 127.0.0.1 which
# is unreachable from the host when published via docker run -p.
CMD ["uvicorn", "ai_os.server:app", "--host", "0.0.0.0", "--port", "8000"]