# Optional $100 experimental launch

This is the operator-requested unvalidated-strategy trial, not a profitability
certification. The default profile remains `validated`. The experimental profile
waives only research qualification: sample counts, calibration, independent EV,
paper profit and historical paper drawdown. It preserves data integrity,
authentication, accounting, execution, stop, notification and monitoring checks.
The live account's own loss/drawdown controls remain mandatory.

`config/experimental-100.env.example` is an inactive example with paper mode.
Limits: $100 initial total account cash, $2/order including estimated fee,
$10 total exposure, $5/city, $2 daily cash-decline limit, 10% cash drawdown,
three open markets, ten order attempts/day, and a $20 cash floor. Cash declines
conservatively include money spent on open bets, so these limits can stop trading
before a bet settles. Routine weather research uses no LLM. Existing conversation
caps are $0.03/day and $0.50/month; server costs are separate.

The configured production account currently has $0.2485 and no open positions or
resting orders. A $99.75 deposit would leave $99.9985 if no other activity occurs.
A full extra $100 would exceed the strict $100 initial account-cash ceiling.
Recheck the actual balance rather than changing that ceiling to pass a check.

Readiness reports expose `validation_profile`, `validated_readiness_passed` and
`research_requirements_waived`. Experimental operational readiness must never be
presented as validated strategy performance. Technical verification currently
expires after 24 hours; missing/expired evidence blocks new orders. This is not
an indefinitely unattended certification.

After technical checks pass and the operator independently funds the correct
real account, operator activation requires the existing explicit confirmation
plus `--accept-unvalidated-strategy`. The marker binds the selected profile and
both database paths. Changing the profile cannot reuse an old marker.
`TRADING_MODE=live` remains a separate operator action. Neither generating a
report, funding the account nor writing the example configuration activates it.

The agent has not run enable-live, changed production to live, or sent a real
order. The operator must perform any actual real-money activation themselves.

## Operator-only activation after funding

These commands are supplied for the account owner; the agent has NOT run them.
They require a root shell on the existing server. Do not run until the account's
available balance is at least $20 and no more than $100, the launch report is
ready for the experimental profile, and you accept the unproven strategy.
The software is currently installed but remains in paper mode.

```bash
set -e
set -a
. /etc/apex-weather/apex-weather.env
set +a
cd /opt/apex-weather/app
/opt/apex-weather/venv/bin/python -m src.weather_cli launch-report
/opt/apex-weather/venv/bin/python -m src.weather_cli balance-test
/opt/apex-weather/venv/bin/python -m src.weather_cli enable-live --confirm I_ACCEPT_LIVE_RISK --accept-unvalidated-strategy
chown apex-weather:apex-weather /var/lib/apex-weather/LIVE_ENABLED
chmod 600 /var/lib/apex-weather/LIVE_ENABLED
/opt/apex-weather/venv/bin/python -c "from dotenv import set_key; set_key('/etc/apex-weather/apex-weather.env', 'TRADING_MODE', 'live')"
systemctl restart apex-weather apex-weather-telegram
systemctl is-active apex-weather apex-weather-telegram
```

Telegram `/status` must then explicitly say real money. `/pause` stops new
orders; `/emergency_stop` also removes the enablement marker. Existing positions
remain at risk after stopping. If either service fails or the account cannot be
reconciled, stop and investigate rather than retrying orders or resetting history.

## Optional operator-selected $5 daily cash budget

At the operator's request, prepared support for a $5 daily budget, keeping $2
per order and $10 total exposure. Default remains $2/day. The running environment
was not changed by preparation. The staged operator-only script validates the
expected current code/config, backs up both files, sets the daily limit and
restarts the worker. It does not reset spending, clear pauses or alter markers.
The script must be explicitly run by the operator with `--apply`.
