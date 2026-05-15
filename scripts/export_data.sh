#!/bin/bash

# Export AI Hub database to JSON for analysis
# Usage: ./scripts/export_data.sh [output_file]

set -e

OUTPUT_FILE="${1:-data_export_$(date +%Y%m%d_%H%M%S).json}"
DB_USER="${DB_USER:-aihub}"
DB_NAME="${DB_NAME:-ai_hub}"
DB_HOST="${DB_HOST:-localhost}"

echo "📤 Exporting database to: $OUTPUT_FILE"

# Export messages with session and user info
PGPASSWORD="${DB_PASSWORD:-aihub_pass}" psql -U "$DB_USER" -d "$DB_NAME" -h "$DB_HOST" -t -A -F'|' \
  "SELECT json_build_object(
    'messages', (SELECT json_agg(json_build_object(
      'id', id,
      'session_id', session_id,
      'user_id', user_id,
      'role', role,
      'content', content,
      'is_summarized', is_summarized,
      'created_at', created_at
    )) FROM messages ORDER BY created_at),
    'sessions', (SELECT json_agg(json_build_object(
      'id', id,
      'user_id', user_id,
      'tenant_id', tenant_id,
      'created_at', created_at,
      'updated_at', updated_at
    )) FROM sessions),
    'users', (SELECT json_agg(json_build_object(
      'id', id,
      'name', name,
      'tenant_id', tenant_id,
      'created_at', created_at
    )) FROM users),
    'stats', json_build_object(
      'total_messages', (SELECT COUNT(*) FROM messages),
      'total_sessions', (SELECT COUNT(*) FROM sessions),
      'total_users', (SELECT COUNT(*) FROM users),
      'export_time', NOW()
    )
  )" > "$OUTPUT_FILE"

echo "✅ Export complete: $OUTPUT_FILE"
echo ""
echo "📊 Data Summary:"
PGPASSWORD="${DB_PASSWORD:-aihub_pass}" psql -U "$DB_USER" -d "$DB_NAME" -h "$DB_HOST" -c "SELECT 'Messages' as type, COUNT(*) as count FROM messages UNION ALL SELECT 'Sessions', COUNT(*) FROM sessions UNION ALL SELECT 'Users', COUNT(*) FROM users;"
