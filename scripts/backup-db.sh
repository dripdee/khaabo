#!/usr/bin/env bash
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Khaabo â€” nightly PostgreSQL backup with 7-day retention.
#
# Strategy: pg_dump in custom format (parallel-restore friendly), compress on
# the wire, pipe to a directory that an object-storage sync (rclone/aws s3)
# picks up. Prune anything older than N days.
#
# Usage:
#   ./scripts/backup-db.sh
#   ./scripts/backup-db.sh --dry-run   # dump locally, skip remote sync
#
# Required env (or pass flags):
#   DATABASE_URL or build from DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME
#   (when neither is set, DATABASE_URL is read from ../.env automatically;
#    pg_dump runs from a pinned postgres docker image when docker is present)
#   BACKUP_DIR (default: ./backups)
#   BACKUP_RETENTION_DAYS (default: 7)
# Optional:
#   S3_BUCKET â€” if set, sync backups with `aws s3 sync` after dump
#   S3_PREFIX â€” path inside the bucket (default: backups/db)
#   LOCK_FILE â€” prevents two backups from racing (default: /tmp/khaabo-backup.lock)
#
# Run via cron:
#   0 2 * * *  /opt/khaabo/scripts/backup-db.sh >> /var/log/khaabo-backup.log 2>&1
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
set -euo pipefail

DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

# â”€â”€ config â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION="${BACKUP_RETENTION_DAYS:-7}"
S3_BUCKET="${S3_BUCKET:-}"
S3_PREFIX="${S3_PREFIX:-backups/db}"
LOCK_FILE="${LOCK_FILE:-/tmp/khaabo-backup.lock}"

# Fall back to the repo's .env when run bare (e.g. ./scripts/backup-db.sh).
if [[ -z "${DATABASE_URL:-}" && -z "${DB_HOST:-}" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    for ENV_FILE in "$SCRIPT_DIR/../.env" ./.env; do
        if [[ -f "$ENV_FILE" ]]; then
            DATABASE_URL="$(grep -m1 ^DATABASE_URL "$ENV_FILE" | cut -d= -f2-)" || true
            if [[ -n "$DATABASE_URL" ]]; then break; fi
        fi
    done
fi

# pg_dump doesn't understand SQLAlchemy driver suffixes (+psycopg, +asyncpg…).
if [[ -n "${DATABASE_URL:-}" ]]; then
    DATABASE_URL="${DATABASE_URL/postgresql+psycopg:/postgresql:}"
    DATABASE_URL="${DATABASE_URL/postgresql+asyncpg:/postgresql:}"
    DATABASE_URL="${DATABASE_URL/postgres+psycopg:/postgres:}"
fi

# Database connection: prefer DATABASE_URL, else compose from parts.
if [[ -n "${DATABASE_URL:-}" ]]; then
    PG_URL="$DATABASE_URL"
else
    DB_HOST="${DB_HOST:-localhost}"
    DB_PORT="${DB_PORT:-5432}"
    DB_USER="${DB_USER:-khaabo}"
    DB_PASSWORD="${DB_PASSWORD:-khaabo}"
    DB_NAME="${DB_NAME:-khaabo}"
    export PGPASSWORD="$DB_PASSWORD"
    PG_URL="postgresql://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}"
fi

# Prefer a pinned Postgres image for pg_dump: Ubuntu 22.04 ships pg_dump 14,
# which refuses to dump PG 15/17 (what Supabase runs) — "server version mismatch".
PG_IMAGE="${PG_IMAGE:-postgres:17-alpine}"
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    RUNNER=docker
elif command -v pg_dump >/dev/null 2>&1; then
    RUNNER=local
else
    echo "[backup] ERROR: neither docker nor pg_dump found on this host" >&2
    exit 1
fi

# â”€â”€ single-flight lock (a long backup must not overlap) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "[backup] another backup is running (lock held on $LOCK_FILE) â€” exiting"
    exit 0
fi

# â”€â”€ prep â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
mkdir -p "$BACKUP_DIR"
ABS_BACKUP_DIR="$(cd "$BACKUP_DIR" && pwd)"
TIMESTAMP=$(date -u +"%Y%m%dT%H%M%SZ")
BASENAME="khaabo-${TIMESTAMP}.dump"
FILE="$ABS_BACKUP_DIR/$BASENAME"

echo "[backup] starting pg_dump â†’ $FILE"

# â”€â”€ dump (custom format: parallel + compressible + single self-contained file)
if [[ "$RUNNER" == docker ]]; then
    docker run --rm --network host \
        -e PG_URL="$PG_URL" \
        -e DUMP_FILE="/backups/$BASENAME" \
        -v "$ABS_BACKUP_DIR:/backups" \
        "$PG_IMAGE" \
        sh -c 'pg_dump -Fc --no-owner --no-privileges "$PG_URL" -f "$DUMP_FILE"'
else
    pg_dump -Fc --no-owner --no-privileges "$PG_URL" -f "$FILE"
fi

# Verify the dump isn't empty â€” pg_dump can exit 0 on a dropped mid-operation DB.
SIZE=$(stat -c%s "$FILE" 2>/dev/null || stat -f%z "$FILE" 2>/dev/null || echo 0)
if (( SIZE < 1024 )); then
    echo "[backup] ERROR produced a ${SIZE}-byte dump, aborting" >&2
    rm -f "$FILE"
    exit 1
fi
echo "[backup] wrote $FILE ($SIZE bytes)"

# â”€â”€ sync to object storage â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
if [[ -n "$S3_BUCKET" && "$DRY_RUN" == false ]]; then
    if command -v aws >/dev/null 2>&1; then
        echo "[backup] syncing to s3://$S3_BUCKET/$S3_PREFIX/"
        aws s3 sync "$BACKUP_DIR" "s3://$S3_BUCKET/$S3_PREFIX/" \
            --exclude "*" --include "khaabo-*.dump" \
            --storage-class STANDARD_IA \
            --delete
    elif command -v rclone >/dev/null 2>&1; then
        echo "[backup] syncing via rclone"
        rclone sync "$BACKUP_DIR" "rclone:s3/$S3_BUCKET/$S3_PREFIX" \
            --include "khaabo-*.dump" --delete
    else
        echo "[backup] NOTICE â€” S3_BUCKET set but neither aws-cli nor rclone found; skipping remote sync" >&2
    fi
fi

# â”€â”€ prune local copies older than RETENTION â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
PRUNED=$(find "$BACKUP_DIR" -name "khaabo-*.dump" -mtime +"$RETENTION" -print -delete 2>/dev/null | wc -l)
if (( PRUNED > 0 )); then
    echo "[backup] pruned $PRUNED backup(s) older than $RETENTION day(s)"
fi

# â”€â”€ remote prune (best-effort â€” keeping only RETENTION_DAYS on S3 too) â”€â”€â”€â”€â”€â”€
if [[ -n "$S3_BUCKET" && "$DRY_RUN" == false && -n "${AWS_PRUNE:-}" ]]; then
    CUTOFF=$(date -u -d "-${RETENTION} days" +"%Y-%m-%d" 2>/dev/null || date -u -v-${RETENTION}d +"%Y-%m-%d")
    echo "[backup] pruning remote backups older than $CUTOFF"
    if command -v aws >/dev/null 2>&1; then
        aws s3api list-objects-v2 --bucket "$S3_BUCKET" --prefix "$S3_PREFIX/khaabo-" \
            --query "Contents[?LastModified<='${CUTOFF}T00:00:00Z'].[Key]" --output text \
            | xargs -r -I{} aws s3 rm "s3://$S3_BUCKET/{}"
    fi
fi

# â”€â”€ verify: we can read the most recent backup's table of contents â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
echo "[backup] verifying dump integrity via pg_restore --list"
if [[ "$RUNNER" == docker ]]; then
    docker run --rm -v "$ABS_BACKUP_DIR:/backups" "$PG_IMAGE" \
        pg_restore --list "/backups/$BASENAME" >/tmp/khaabo-toc.txt
else
    pg_restore --list "$FILE" >/tmp/khaabo-toc.txt
fi
TOC_LINES=$(wc -l </tmp/khaabo-toc.txt)
if (( TOC_LINES < 30 )); then
    echo "[backup] WARN â€” table of contents has only $TOC_LINES lines, expected >30" >&2
fi
rm -f /tmp/khaabo-toc.txt

echo "[backup] done"
