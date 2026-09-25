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
