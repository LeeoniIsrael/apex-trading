#!/usr/bin/env bash
set -euo pipefail
: "${APEX_HOST:?Set APEX_HOST}"
: "${APEX_SSH_KEY:?Set APEX_SSH_KEY}"
APEX_USER="${APEX_USER:-root}"
ssh -i "$APEX_SSH_KEY" -o IdentitiesOnly=yes "$APEX_USER@$APEX_HOST" \
  'systemctl --no-pager --full status apex-weather; runuser -u apex-weather -- sh -c "cd /opt/apex-weather/app && DATABASE_PATH=/var/lib/apex-weather/apex_weather.sqlite3 /opt/apex-weather/venv/bin/apex-weather health"'
