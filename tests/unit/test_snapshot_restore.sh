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

# Test restore (only if PG is available)
if command -v psql &> /dev/null && PGPASSWORD=aihub_pass psql -h localhost -U aihub -d ai_hub -c "SELECT 1" &> /dev/null; then
    # Take fresh snapshot
    ./scripts/snapshot_ihi_db.sh "test_restore_$(date +%s)" > /dev/null
    LATEST=$(ls -t /tmp/ihi_snapshots | head -1)
    LATEST_DIR="/tmp/ihi_snapshots/$LATEST"

    # Pollute DB with junk row
    PGPASSWORD=aihub_pass psql -h localhost -U aihub -d ai_hub -c \
      "INSERT INTO ihi_rag_cases (device_id, severity, pattern, description) VALUES ('TEST_POLLUTE_$(date +%s)', 'low', '{}', 'junk')" &> /dev/null
    PRE_COUNT=$(PGPASSWORD=aihub_pass psql -h localhost -U aihub -d ai_hub -tA -c "SELECT COUNT(*) FROM ihi_rag_cases WHERE device_id LIKE 'TEST_POLLUTE_%'")
    [[ "$PRE_COUNT" -ge "1" ]] || { echo "FAIL: pollution insert failed"; exit 1; }

    # Restore
    ./scripts/restore_ihi_db.sh "$LATEST_DIR" > /dev/null

    # Verify pollution cleared
    POST_COUNT=$(PGPASSWORD=aihub_pass psql -h localhost -U aihub -d ai_hub -tA -c "SELECT COUNT(*) FROM ihi_rag_cases WHERE device_id LIKE 'TEST_POLLUTE_%'")
    [[ "$POST_COUNT" == "0" ]] || { echo "FAIL: pollution NOT cleared (got $POST_COUNT rows)"; exit 1; }
    echo "PASS: restore clears pollution"
else
    echo "SKIP: restore test (PG not available)"
fi
