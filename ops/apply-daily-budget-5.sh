#!/bin/bash
# Operator-only: this changes a live financial risk limit. Never auto-run it.
set -euo pipefail
if [ "${1:-}" != "--apply" ]; then
  echo "Operator action required: run with --apply to select a $5 daily cash budget."
  exit 2
fi
/opt/apex-weather/venv/bin/python - <<'OPERATOR_PY'
import hashlib,os,shutil
from pathlib import Path
from datetime import datetime,timezone
from dotenv import dotenv_values,set_key
active=Path('/opt/apex-weather/app/src/weather_config.py')
staged=Path('/opt/apex-weather/staged-budget-5/weather_config.py')
env=Path('/etc/apex-weather/apex-weather.env')
v=dotenv_values(env)
assert v.get('TRADING_MODE')=='live', 'Expected current live configuration; no changes made'
assert v.get('LIVE_VALIDATION_PROFILE')=='experimental_100'
assert float(v.get('LIVE_MAX_DAILY_LOSS_USD','2'))==2, 'Daily limit changed; review before applying'
assert float(v.get('LIVE_MAX_ORDER_USD','2'))==2
assert float(v.get('LIVE_MAX_EXPOSURE_USD','10'))==10
assert hashlib.sha256(active.read_bytes()).hexdigest()=='56c211d1757ed96b98a31d66b53d97cac7d13a98a3ebf49ee433a807164945fa', 'Installed code changed; no changes made'
assert hashlib.sha256(staged.read_bytes()).hexdigest()=='d89653b87020563f067a2d65af1c9127853a2e8ff65f488abac6f6935fbc3d23', 'Staged file mismatch'
backup=Path('/var/backups/apex-weather')/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-daily-budget-5')
backup.mkdir(mode=0o700)
shutil.copy2(active,backup/'weather_config.py')
shutil.copy2(env,backup/'environment');(backup/'environment').chmod(0o600)
meta=env.stat()
try:
    shutil.copyfile(staged,active)
    set_key(env,'LIVE_MAX_DAILY_LOSS_USD','5')
    os.chmod(env,meta.st_mode&0o777);os.chown(env,meta.st_uid,meta.st_gid)
except Exception:
    shutil.copy2(backup/'weather_config.py',active)
    shutil.copy2(backup/'environment',env)
    raise SystemExit('Update failed; original files restored')
print('Selected $5 daily cash budget. Per-order $2 and total exposure $10 unchanged.')
print('Existing spending, pause, emergency stop, and account history were preserved.')
OPERATOR_PY
systemctl restart apex-weather
systemctl is-active apex-weather
