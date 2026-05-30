#!/usr/bin/env bash
# ============================================================
#  PHAN 1: CAI DAT PGVECTOR (CAN NHAP PASSWORD SUDO)
#  Chay: bash ~/ai-hub/scripts/01_install_pgvector.sh
# ============================================================
set -euo pipefail

echo "=== Cai dat pgvector cho PostgreSQL 16 ==="

# Thu apt truoc
if sudo apt-get update -qq 2>/dev/null && sudo apt-get install -y postgresql-16-pgvector 2>/dev/null; then
  echo "OK: pgvector installed via apt"
else
  echo "apt failed, building from source..."
  cd /tmp
  rm -rf pgvector
  git clone --branch v0.8.0 https://github.com/pgvector/pgvector.git
  cd pgvector
  sudo apt-get install -y postgresql-server-dev-16 build-essential
  make -j"$(nproc)"
  sudo make install
  cd /home/hung
  rm -rf /tmp/pgvector
  echo "OK: pgvector built and installed from source"
fi

# Enable extension
PGPASSWORD=aihub_pass psql -h localhost -U aihub -d ai_hub -c "CREATE EXTENSION IF NOT EXISTS vector;"

# Verify
PGPASSWORD=aihub_pass psql -h localhost -U aihub -d ai_hub -c "SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';"

echo "=== Done: pgvector ready ==="
