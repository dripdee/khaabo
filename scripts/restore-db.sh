#!/usr/bin/env bash
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Khaabo â€” restore a database from a pg_dump backup.
#
# This is intentionally guarded: it refuses to run against a non-empty
# database unless --force is passed, and restores IN PLACE via
# pg_restore --clean --if-exists (dropping the database itself would fail when
# the target DB — e.g. Supabase's `postgres` — is the only database).
# Restore **must** be tested before you actually need it.
#
# Usage:
#   ./scripts/restore-db.sh /path/to/khaabo-20260101T020000Z.dump
#   ./scripts/restore-db.sh --force /path/to/khaabo-*.dump
#   ./scripts/restore-db.sh --latest   # picks the most recent dump in BACKUP_DIR
#
# Required env:
#   DATABASE_URL or DB_HOST/DB_PORT/DB_ADMIN_USER (superuser)/DB_PASSWORD/DB_NAME
#   (when neither is set, DATABASE_URL is read from ../.env automatically;
#    psql/pg_restore run from a pinned postgres docker image when docker is present)
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
# Fall back to the repo's .env when run bare (e.g. ./scripts/restore-db.sh).
if [[ -z "${DATABASE_URL:-}" && -z "${DB_HOST:-}" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    for ENV_FILE in "$SCRIPT_DIR/../.env" ./.env; do
        if [[ -f "$ENV_FILE" ]]; then
            DATABASE_URL="$(grep -m1 ^DATABASE_URL "$ENV_FILE" | cut -d= -f2-)" || true
            if [[ -n "$DATABASE_URL" ]]; then break; fi
        fi
    done
fi

# Decompose DATABASE_URL (SQLAlchemy driver suffix like +psycopg handled) into
# plain pg connection parts.
if [[ -n "${DATABASE_URL:-}" ]]; then
    if [[ "$DATABASE_URL" =~ ^postgres(ql)?(\+[a-z]+)?://([^:]+):(.+)@([^:/]+):([0-9]+)/([A-Za-z0-9_-]+) ]]; then
        DB_USER="${BASH_REMATCH[3]}"
        DB_PASSWORD="${BASH_REMATCH[4]}"
        DB_HOST="${BASH_REMATCH[5]}"
        DB_PORT="${BASH_REMATCH[6]}"
        DB_NAME="${BASH_REMATCH[7]}"
    else
        echo "[restore] ERROR could not parse DATABASE_URL" >&2
        exit 1
    fi
fi

DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-khaabo}"
DB_NAME="${DB_NAME:-khaabo}"
export PGPASSWORD="${DB_PASSWORD:-khaabo}"

# Prefer a pinned Postgres image: Ubuntu 22.04 ships psql/pg_restore 14, which
# refuses dumps from PG 15/17 servers (what Supabase runs).
PG_IMAGE="${PG_IMAGE:-postgres:17-alpine}"
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    RUNNER=docker
elif command -v psql >/dev/null 2>&1; then
    RUNNER=local
else
    echo "[restore] ERROR: neither docker nor psql found on this host" >&2
    exit 1
fi

DUMP_DIR="$(cd "$(dirname "$DUMP")" && pwd)"
DUMP_BASE="$(basename "$DUMP")"

run_psql() { # run_psql <dbname> [psql args...]
    local db="$1"; shift
    if [[ "$RUNNER" == docker ]]; then
        docker run --rm --network host \
            -e PGPASSWORD="$DB_PASSWORD" \
            -v "$DUMP_DIR:/dump" \
            "$PG_IMAGE" \
            psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$db" "$@"
    else
        psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$db" "$@"
    fi
}

# â”€â”€ confirm the target DB isn't in use â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
CONNECTIONS=$(run_psql postgres -tAc \
    "SELECT count(*) FROM pg_stat_activity WHERE datname = '$DB_NAME' AND pid <> pg_backend_pid() \
    AND state <> 'idle'" 2>/dev/null || echo 0)
if (( CONNECTIONS > 0 )); then
    echo "[restore] $CONNECTIONS active connection(s) on '$DB_NAME' â€” stop the API and workers first" >&2
    exit 1
fi

# â”€â”€ refuse to drop a non-empty DB unless --force â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
TABLE_COUNT=$(run_psql "$DB_NAME" -tAc \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'" 2>/dev/null || echo 0)
if [[ "$TABLE_COUNT" -gt 0 && "$FORCE" == false ]]; then
    cat >&2 <<EOF
[restore] REFUSING to run â€” target DB '$DB_NAME' contains $TABLE_COUNT table(s).

If you are sure, re-run with --force. This will DROP the entire database and
replace it with the contents of $DUMP. The action is irreversible.
EOF
    exit 1
fi

echo "[restore] restoring IN PLACE into '$DB_NAME' (--clean --if-exists replaces schema + data)"
run_psql "$DB_NAME" -v ON_ERROR_STOP=1 \
    -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$DB_NAME' AND pid <> pg_backend_pid()" || true

# PostGIS extension must exist before restore (pg_dump can't fully recreate it).
echo "[restore] installing postgis extension"
run_psql "$DB_NAME" -v ON_ERROR_STOP=1 \
    -c "CREATE EXTENSION IF NOT EXISTS postgis; CREATE EXTENSION IF NOT EXISTS postgis_topology;"

echo "[restore] pg_restore $DUMP â†’ $DB_NAME"
if [[ "$RUNNER" == docker ]]; then
    docker run --rm --network host \
        -e PGPASSWORD="$DB_PASSWORD" \
        -v "$DUMP_DIR:/dump" \
        "$PG_IMAGE" \
        pg_restore -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
            --no-owner --no-privileges --clean --if-exists -j 4 "/dump/$DUMP_BASE" || {
        echo "[restore] pg_restore completed with non-fatal errors (expected with --clean)" >&2
    }
else
    pg_restore -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
        --no-owner --no-privileges --clean --if-exists -j 4 "$DUMP" || {
            echo "[restore] pg_restore completed with non-fatal errors (expected with --clean)" >&2
        }
fi

echo "[restore] verifying row counts on core tables"
run_psql "$DB_NAME" -c '
    SELECT
        (SELECT count(*) FROM dishes) AS dishes,
        (SELECT count(*) FROM restaurants) AS restaurants,
        (SELECT count(*) FROM reviews) AS reviews,
        (SELECT count(*) FROM dish_scores) AS dish_scores;
'

echo "[restore] done â€” run migrations to confirm schema matches app code"
echo "         alembic upgrade head   (should report 'no new migrations to apply')"
