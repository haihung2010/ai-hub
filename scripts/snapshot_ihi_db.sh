#!/usr/bin/env bash
# Snapshot ihi_rag_cases (PG) + alert.db (SQLite) to /tmp/ihi_snapshots/<ts>/
# Idempotent. Safe to re-run.
set -euo pipefail

TIMESTAMP="${1:-$(date +%s)}"
SNAPSHOT_DIR="/tmp/ihi_snapshots/${TIMESTAMP}"
mkdir -p "$SNAPSHOT_DIR"

# PG tables
PGPASSWORD="${PGPASSWORD:-aihub_pass}" pg_dump -h "${PGHOST:-localhost}" -U "${PGUSER:-aihub}" \
  -d "${PGDATABASE:-ai_hub}" -t ihi_rag_cases -t ihi_case_embeddings -t ihi_device_overrides \
  --data-only > "$SNAPSHOT_DIR/ihi_pg.sql"

# SQLite
cp /home/hung/ihi_test/alert.db "$SNAPSHOT_DIR/alert.db"

echo "Snapshot saved to $SNAPSHOT_DIR"
echo "  PG: $(wc -l < $SNAPSHOT_DIR/ihi_pg.sql) lines"
echo "  SQLite: $(stat -c %s $SNAPSHOT_DIR/alert.db) bytes"
