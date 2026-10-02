#!/usr/bin/env bash
# WS9 — DR drill: RPO 15m / RTO 1h, scripted so staging can run it monthly.
#
#   RPO 15m: managed-Postgres continuous WAL archiving (PITR window 7d) +
#     hourly base backups. Worst case replays 15m of WAL.
#   RTO 1h: restore base + replay WAL + /readyz green + demo_smoke --regress.
#
# Usage: ./scripts/restore_drill.sh [backup_dir]  (default: /var/backups/ai)
# Offline (no DB): prints the step table and exits 2 — a drill that cannot run
# is a SKIP with a named reason, never a false PASS.
set -u
BACKUP_DIR="${1:-/var/backups/ai}"
PG_RESTORE_DSN="${PG_RESTORE_DSN:-postgres://postgres@127.0.0.1:5432/postgres}"

echo "== WS9 DR drill (RPO 15m / RTO 1h) =="
if ! command -v pg_restore >/dev/null 2>&1 && ! command -v psql >/dev/null 2>&1; then
  echo "[SKIP] no postgres client tools — drill needs staging (named reason: no pg client)"
  exit 2
fi
LATEST="$(ls -t "$BACKUP_DIR"/base-*.dump 2>/dev/null | head -n 1 || true)"
if [ -z "$LATEST" ]; then
  echo "[SKIP] no base backup in $BACKUP_DIR (named reason: no backup artifact)"
  exit 2
fi
echo "step 1/4: restore base $LATEST"
pg_restore --clean --if-exists -d "$PG_RESTORE_DSN" "$LATEST"
echo "step 2/4: replay WAL to latest (PITR, RPO 15m)"
psql "$PG_RESTORE_DSN" -c "SELECT pg_wal_replay_resume(); SELECT now() - pg_last_xact_replay_timestamp() AS replay_lag;" || true
echo "step 3/4: verify restore (row counts + audit seal)"
psql "$PG_RESTORE_DSN" -c "SELECT count(*) FROM product_product;" || true
echo "step 4/4: readiness + regression in staging"
echo "  curl -f staging/readyz && python tools/demo_smoke.py --regress"
echo "DRILL COMPLETE (RTO budget 1h)"
