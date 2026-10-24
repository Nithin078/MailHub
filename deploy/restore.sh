#!/bin/sh
# Restore a gzipped pg_dump. Usage: ./deploy/restore.sh backups/inbox-YYYYMMDD.sql.gz
set -eu
if [ -z "${1:-}" ]; then
  echo "Pass the dump path." >&2
  exit 1
fi
ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
gunzip -c "$1" | docker compose -f "$ROOT/docker-compose.yml" exec -T postgres psql -U inbox -d inbox
echo "Restored $1"
