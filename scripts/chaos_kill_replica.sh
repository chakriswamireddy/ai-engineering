#!/usr/bin/env bash
# WS9 — chaos drill: kill 1 replica mid-demo, expect zero data loss.
#
# Staging only (needs kubectl + the k8s/ manifests applied). The premise is
# construction, not luck: PDB minAvailable 1 keeps a server; the Postgres
# advisory lock (STATE_ADVISORY_DSN) means only the holder writes, so the
# killed pod's in-flight write either committed (rows measured) or never
# started (approval replayable, idempotent).
#
# Usage: ./scripts/chaos_kill_replica.sh [namespace=default]
set -u
NS="${1:-default}"
POD="$(kubectl -n "$NS" get pods -l app=ai-layer -o jsonpath='{.items[0].metadata.name}')"
echo "killing replica $POD (PDB keeps 1 serving)…"
kubectl -n "$NS" delete pod "$POD" --grace-period=5
echo "waiting for replacement to pass /readyz…"
kubectl -n "$NS" wait --for=condition=ready pod -l app=ai-layer --timeout=180s
echo "verify: demo_smoke --regress green in staging, approval outcomes intact"
echo "CHAOS DRILL COMPLETE (expect: zero data loss, apply replay idempotent)"
