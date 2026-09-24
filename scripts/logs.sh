#!/usr/bin/env bash
set -euo pipefail
: "${APEX_HOST:?Set APEX_HOST}"
: "${APEX_SSH_KEY:?Set APEX_SSH_KEY}"
APEX_USER="${APEX_USER:-root}"
ssh -t -i "$APEX_SSH_KEY" -o IdentitiesOnly=yes "$APEX_USER@$APEX_HOST" \
  'journalctl -u apex-weather -n 200 -f'
