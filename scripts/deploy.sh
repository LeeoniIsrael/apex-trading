#!/usr/bin/env bash
set -euo pipefail

: "${APEX_HOST:?Set APEX_HOST to the verified server IP or hostname}"
: "${APEX_SSH_KEY:?Set APEX_SSH_KEY to an absolute private-key path}"
APEX_USER="${APEX_USER:-root}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SSH=(ssh -i "$APEX_SSH_KEY" -o IdentitiesOnly=yes "$APEX_USER@$APEX_HOST")

"${SSH[@]}" 'id -u apex-weather >/dev/null 2>&1 || useradd --system --home /var/lib/apex-weather --shell /usr/sbin/nologin apex-weather
install -d -o apex-weather -g apex-weather -m 0750 /var/lib/apex-weather /opt/apex-weather/app
install -d -o root -g apex-weather -m 0750 /etc/apex-weather'

rsync -az --delete \
  --exclude '.git/' --exclude '.venv/' --exclude '.env' --exclude '*.pem' \
  --exclude 'data/' --exclude '__pycache__/' \
  -e "ssh -i $APEX_SSH_KEY -o IdentitiesOnly=yes" \
  "$ROOT_DIR/" "$APEX_USER@$APEX_HOST:/opt/apex-weather/app/"

"${SSH[@]}" 'set -e
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv python3-pip sqlite3
python3 -m venv /opt/apex-weather/venv
/opt/apex-weather/venv/bin/pip install --quiet --upgrade pip
/opt/apex-weather/venv/bin/pip install --quiet /opt/apex-weather/app
install -m 0644 /opt/apex-weather/app/deploy/apex-weather.service /etc/systemd/system/apex-weather.service
install -m 0644 /opt/apex-weather/app/deploy/apex-weather-telegram.service /etc/systemd/system/apex-weather-telegram.service
if [ ! -f /etc/apex-weather/apex-weather.env ]; then
  install -m 0640 -o root -g apex-weather /opt/apex-weather/app/.env.template /etc/apex-weather/apex-weather.env
fi
systemctl daemon-reload
systemctl enable apex-weather
systemctl enable apex-weather-telegram
systemctl restart apex-weather
systemctl restart apex-weather-telegram
systemctl --no-pager --full status apex-weather'
