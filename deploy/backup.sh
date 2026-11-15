#!/usr/bin/env bash
# Nightly: pg_dump (custom format) into $BACKUP_DIR, keeping 7 days. Run by deploy/cron.
set -euo pipefail
BACKUP_DIR="${BACKUP_DIR:-/var/backups/docagent}"
KEEP_DAYS=7
cd "$(dirname "$0")/.."
mkdir -p "$BACKUP_DIR"
out="$BACKUP_DIR/docagent-$(date -u +%Y%m%d-%H%M%S).dump"
# Credentials stay inside the container (its POSTGRES_* env); nothing is read on the host.
deploy/dc exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$out.tmp"
mv "$out.tmp" "$out"
chmod 600 "$out"
find "$BACKUP_DIR" -name 'docagent-*.dump' -mtime +$((KEEP_DAYS - 1)) -delete
echo "$(date -u +%FT%TZ) backup $(basename "$out") $(du -h "$out" | cut -f1)"
