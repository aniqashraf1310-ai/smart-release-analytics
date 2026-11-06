#!/usr/bin/env bash
# Recreate the database and apply every migration in order.
# Usage: PGHOST=localhost PGUSER=postgres ./reset_db.sh [dbname]
set -euo pipefail
DB="${1:-release_mgmt}"
cd "$(dirname "$0")"
psql -v ON_ERROR_STOP=1 -qc "DROP DATABASE IF EXISTS $DB" -c "CREATE DATABASE $DB"
for f in migrations/*.sql; do
  echo "applying $f"
  psql -v ON_ERROR_STOP=1 -q -d "$DB" -f "$f"
done
echo "database $DB ready"
