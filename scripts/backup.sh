#!/usr/bin/env bash
set -euo pipefail
: "${APEX_HOST:?Set APEX_HOST}"
: "${APEX_SSH_KEY:?Set APEX_SSH_KEY}"
APEX_USER="${APEX_USER:-root}"
BACKUP_DIR="${APEX_BACKUP_DIR:-$PWD/backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$BACKUP_DIR"
REMOTE_BACKUP="/var/lib/apex-weather/backup-$STAMP.sqlite3"
ssh -i "$APEX_SSH_KEY" -o IdentitiesOnly=yes "$APEX_USER@$APEX_HOST" \
  "sqlite3 /var/lib/apex-weather/apex_weather.sqlite3 '.backup $REMOTE_BACKUP' && chown apex-weather:apex-weather $REMOTE_BACKUP"
scp -i "$APEX_SSH_KEY" -o IdentitiesOnly=yes \
  "$APEX_USER@$APEX_HOST:$REMOTE_BACKUP" "$BACKUP_DIR/"
ssh -i "$APEX_SSH_KEY" -o IdentitiesOnly=yes "$APEX_USER@$APEX_HOST" \
  "find /var/lib/apex-weather -maxdepth 1 -name 'backup-*.sqlite3' -mtime +7 -delete"
echo "Backup saved to $BACKUP_DIR/backup-$STAMP.sqlite3"
