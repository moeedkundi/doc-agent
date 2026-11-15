#!/usr/bin/env bash
# Restore a pg_dump from deploy/backup.sh:  deploy/restore.sh /var/backups/docagent/<file>.dump
# Stops everything that talks to the database, replaces its contents, starts them again.
set -euo pipefail
dump="${1:?usage: deploy/restore.sh <backup.dump>}"
[ -f "$dump" ] || { echo "no such file: $dump" >&2; exit 1; }
cd "$(dirname "$0")/.."
read -r -p "Replace the current database with $(basename "$dump")? [y/N] " ok
[ "$ok" = y ] || exit 1
deploy/dc stop caddy api extraction validation sqlstore reference
deploy/dc exec -T postgres sh -c \
  'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --role="$POSTGRES_USER"' \
  < "$dump"
deploy/dc up -d
echo "restored $(basename "$dump")"
