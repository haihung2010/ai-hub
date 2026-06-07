#!/usr/bin/env bash
# Restore ihi_rag_cases (PG) + alert.db (SQLite) from snapshot.
# Usage: restore_ihi_db.sh <snapshot_dir>
set -euo pipefail

SNAPSHOT_DIR="${1:?Usage: restore_ihi_db.sh <snapshot_dir>}"
[[ -d "$SNAPSHOT_DIR" ]] || { echo "ERROR: $SNAPSHOT_DIR not found"; exit 2; }
[[ -f "$SNAPSHOT_DIR/ihi_pg.sql" ]] || { echo "ERROR: ihi_pg.sql missing in snapshot"; exit 2; }
[[ -f "$SNAPSHOT_DIR/alert.db" ]] || { echo "ERROR: alert.db missing in snapshot"; exit 2; }

# PG: truncate + restore
PGPASSWORD="${PGPASSWORD:-aihub_pass}" psql -h "${PGHOST:-localhost}" -U "${PGUSER:-aihub}" -d "${PGDATABASE:-ai_hub}" <<EOF
TRUNCATE ihi_rag_cases RESTART IDENTITY CASCADE;
TRUNCATE ihi_case_embeddings;
TRUNCATE ihi_device_overrides;
\i $SNAPSHOT_DIR/ihi_pg.sql
EOF

# SQLite: copy back
cp "$SNAPSHOT_DIR/alert.db" /home/hung/ihi_test/alert.db

echo "Restored from $SNAPSHOT_DIR"
