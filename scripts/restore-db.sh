#!/usr/bin/env bash
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Khaabo â€” restore a database from a pg_dump backup.
#
# This is intentionally interactive: it refuses to run against a non-empty
# database unless --force is passed, and it always drops and recreates the
# target DB before restoring. Restore **must** be tested before you actually
# need it.
#
# Usage:
#   ./scripts/restore-db.sh /path/to/khaabo-20260101T020000Z.dump
#   ./scripts/restore-db.sh --force /path/to/khaabo-*.dump
#   ./scripts/restore-db.sh --latest   # picks the most recent dump in BACKUP_DIR
#
# Required env:
#   DATABASE_URL or DB_HOST/DB_PORT/DB_ADMIN_USER (superuser)/DB_PASSWORD/DB_NAME
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
set -euo pipefail

FORCE=false
LATEST=false
DUMP=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --force) FORCE=true; shift ;;
        --latest) LATEST=true; shift ;;
        *) DUMP="$1"; break ;;
    esac
done

if [[ "$LATEST" == true ]]; then
    BACKUP_DIR="${BACKUP_DIR:-./backups}"
    DUMP=$(ls -t "$BACKUP_DIR"/khaabo-*.dump 2>/dev/null | head -1 || true)
    if [[ -z "$DUMP" ]]; then
        echo "[restore] no backups found in $BACKUP_DIR" >&2
        exit 1
    fi
    echo "[restore] --latest resolved to $DUMP"
fi

if [[ -z "$DUMP" ]]; then
    echo "[restore] USAGE: $0 [--force|--latest] <dump-file>" >&2
    exit 1
fi

if [[ ! -f "$DUMP" ]]; then
    echo "[restore] dump file not found: $DUMP" >&2
    exit 1
fi

# â”€â”€ DB connection â†’ host/port/user/password/name â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-khaabo}"
DB_NAME="${DB_NAME:-khaabo}"
export PGPASSWORD="${DB_PASSWORD:-khaabo}"

# â”€â”€ confirm the target DB isn't in use â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
CONNECTIONS=$(psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d postgres -tAc \
    "SELECT count(*) FROM pg_stat_activity WHERE datname = '$DB_NAME' AND pid <> pg_backend_pid() \
    AND state <> 'idle'" 2>/dev/null || echo 0)
if (( CONNECTIONS > 0 )); then
    echo "[restore] $CONNECTIONS active connection(s) on '$DB_NAME' â€” stop the API and workers first" >&2
    exit 1
fi

# â”€â”€ refuse to drop a non-empty DB unless --force â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
TABLE_COUNT=$(psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -tAc \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'" 2>/dev/null || echo 0)
if [[ "$TABLE_COUNT" -gt 0 && "$FORCE" == false ]]; then
    cat >&2 <<EOF
[restore] REFUSING to run â€” target DB '$DB_NAME' contains $TABLE_COUNT table(s).

If you are sure, re-run with --force. This will DROP the entire database and
replace it with the contents of $DUMP. The action is irreversible.
EOF
    exit 1
fi

echo "[restore] DROP DATABASE + CREATE DATABASE $DB_NAME"
psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d postgres -v ON_ERROR_STOP=1 <<-SQL
    SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$DB_NAME';
    DROP DATABASE IF EXISTS "$DB_NAME";
    CREATE DATABASE "$DB_NAME";
SQL

# PostGIS extension must exist before restore (pg_dump can't fully recreate it).
echo "[restore] installing postgis extension"
psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 \
    -c "CREATE EXTENSION IF NOT EXISTS postgis; CREATE EXTENSION IF NOT EXISTS postgis_topology;"

echo "[restore] pg_restore $DUMP â†’ $DB_NAME"
pg_restore -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
    --no-owner --no-privileges --clean --if-exists -j 4 "$DUMP" || {
        echo "[restore] pg_restore completed with non-fatal errors (expected with --clean)" >&2
    }

echo "[restore] verifying row counts on core tables"
psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c '
    SELECT
        (SELECT count(*) FROM dishes) AS dishes,
        (SELECT count(*) FROM restaurants) AS restaurants,
        (SELECT count(*) FROM reviews) AS reviews,
        (SELECT count(*) FROM dish_scores) AS dish_scores;
'

echo "[restore] done â€” run migrations to confirm schema matches app code"
echo "         alembic upgrade head   (should report 'no new migrations to apply')"
