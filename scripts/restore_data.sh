#!/bin/bash

# Restore AI Hub database from backup
# Usage: ./scripts/restore_data.sh

set -e

BACKUP_FILE="${1:-scripts/ai_hub_data_backup.dump}"
DB_USER="${DB_USER:-aihub}"
DB_NAME="${DB_NAME:-ai_hub}"
DB_HOST="${DB_HOST:-localhost}"

if [ ! -f "$BACKUP_FILE" ]; then
    echo "❌ Backup file not found: $BACKUP_FILE"
    exit 1
fi

echo "📦 Restoring database from: $BACKUP_FILE"
echo "   Database: $DB_NAME"
echo "   User: $DB_USER"
echo "   Host: $DB_HOST"
echo ""

# Restore data
PGPASSWORD="${DB_PASSWORD:-aihub_pass}" pg_restore -U "$DB_USER" -d "$DB_NAME" -h "$DB_HOST" --data-only "$BACKUP_FILE"

echo ""
echo "✅ Database restored successfully"
echo ""

# Show stats
echo "📊 Data Summary:"
PGPASSWORD="${DB_PASSWORD:-aihub_pass}" psql -U "$DB_USER" -d "$DB_NAME" -h "$DB_HOST" -c "SELECT 'Users' as type, COUNT(*) as count FROM users UNION ALL SELECT 'Sessions', COUNT(*) FROM sessions UNION ALL SELECT 'Messages', COUNT(*) FROM messages;"
