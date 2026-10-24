#!/bin/sh
# Dump Postgres to a directory that should live off the VM (mounted disk, or scp the result).
set -eu
ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DIR="${BACKUP_DIR:-$ROOT/backups}"
mkdir -p "$DIR"
docker compose -f "$ROOT/docker-compose.yml" exec -T postgres pg_dump -U inbox --no-owner inbox | gzip > "$DIR/inbox-$STAMP.sql.gz"
find "$DIR" -name 'inbox-*.sql.gz' -mtime +14 -delete
echo "Wrote $DIR/inbox-$STAMP.sql.gz"
