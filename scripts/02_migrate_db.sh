#!/usr/bin/env bash
# ============================================================
#  PHAN 2: MIGRATE DATABASE (KHONG CAN SUDO)
#  Chay: bash ~/ai-hub/scripts/02_migrate_db.sh
# ============================================================
set -euo pipefail

cd /home/hung/ai-hub

echo "=== Step 1: Apply SQL migration (columns + indexes + trigger) ==="
PGPASSWORD=aihub_pass psql -h localhost -U aihub -d ai_hub -f scripts/migrate_pgvector.sql

echo ""
echo "=== Step 2: Migrate raw embeddings -> pgvector ==="
./venv/bin/python scripts/migrate_embeddings.py

echo ""
echo "=== Step 3: Verify ==="
PGPASSWORD=aihub_pass psql -h localhost -U aihub -d ai_hub -c "
  SELECT
    (SELECT count(*) FROM knowledge_card_chunks WHERE embedding_vec IS NOT NULL) AS vec_count,
    (SELECT count(*) FROM knowledge_card_chunks WHERE content_tsv IS NOT NULL) AS tsv_count,
    (SELECT count(*) FROM knowledge_card_chunks) AS total_chunks;
"

echo ""
echo "=== Done: Database migration complete ==="
