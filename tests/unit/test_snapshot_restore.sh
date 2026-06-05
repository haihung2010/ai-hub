#!/usr/bin/env bash
set -euo pipefail

# Test snapshot creation
SNAP_OUT=$(./scripts/snapshot_ihi_db.sh "test_$(date +%s)" 2>&1)
echo "$SNAP_OUT"
SNAP_DIR=$(echo "$SNAP_OUT" | head -1 | awk '{print $4}')

# Verify files exist
[[ -f "$SNAP_DIR/ihi_pg.sql" ]] || { echo "FAIL: ihi_pg.sql missing"; exit 1; }
[[ -f "$SNAP_DIR/alert.db" ]] || { echo "FAIL: alert.db missing"; exit 1; }

# Idempotency: re-run with same timestamp
./scripts/snapshot_ihi_db.sh "$(basename $SNAP_DIR)" > /dev/null
[[ -f "$SNAP_DIR/ihi_pg.sql" ]] || { echo "FAIL: idempotent re-run failed"; exit 1; }

echo "PASS: snapshot creates files + idempotent"
