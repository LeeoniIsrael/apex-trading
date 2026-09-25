# APEX launch handoff — 2026-09-25 UTC

## Verified state

Paper upgrade deployed at 03:24 UTC to the verified existing host. Both
`apex-weather` and `apex-weather-telegram` active/running, zero restarts, main
exit status 0. Source SHA256 (sorted tracked paths plus contents):
`ecfd93ce8e5ec5cd5197dda17309cde8c51f5d39fa85cd6fe2ba348db6cda3f3`.
Backup: `/var/backups/apex-weather/20260925T032414Z-v3`.

163 local tests passed, full source compilation and git diff checks passed.
No tracked private-key file/content or .env found in the targeted credential scan.
SQLite migrations 1–6 present. Authenticated read-only balance, orders, fills,
settlements, positions and public event fee feed succeeded. Telegram getMe and
local /status rendering succeeded. Actual notification delivery and real-order
submission have NOT been exercised in production during this turn.

Current balance at Kalshi: $0.2485. No real order submitted. TRADING_MODE=paper,
live marker absent, live orders in paper database zero. Historical paper P&L:
$328.07 realized, $306.9798 after recorded operating costs; cash $384.9298,
open cost $20.71, bid-marked equity $385.2898 at the verification snapshot.
Ledger discrepancies: none. Five traded markets had settled historically.
These historical results do not validate the changed v3 model.

Deployed code commits, all pushed to origin/weather-autonomous-v2:
- e45073a — preliminary weather uncertainty, bounded extrema, isolated v3 evidence.
- 0f0b50d — durable exact-fee remote portfolio recovery and pagination.
- 1b38513 — daily settlement end-time guard and ambiguous-rule rejection.
- 73f44b6 — real-money Telegram reporting/alerts and cached launch reporting.
- 092d3aa — event fee overrides and fresh live edge recheck.

## Monitoring and remaining work

Read `/var/lib/apex-weather/launch-readiness.json` first. The paper daemon updates
it every ten minutes without AI. The first v3 cycle produced 107 candidates.
Current-model independent resolved holdout events: zero. Preserve prior cohorts;
never relabel historical data to accelerate promotion. Do not tune on holdout.

Launch is NOT blocked only by funding. Still required:

1. 200 independent prospective holdout events and 5,000 useful snapshots, positive
   after-fee holdout EV with a positive lower confidence bound, Brier <=0.20.
2. Investigate/resolve the historical 22.33% drawdown against the 15% gate. It
   includes a $21.09 monthly VPS charge against the initial $100. It will not
   disappear merely by waiting. Do not erase history, raise limits, or reset
   the qualification account to manufacture a pass. A prospective new cohort
   needs an explicit accounting and operating-cost policy before evaluation.
3. Production verification of idempotency, emergency stop, alert delivery, equity
   reporting, live recovery, live monitoring and fee schedule. Unit tests cover
   these software paths but cannot establish actual exchange execution behavior.
   Record checks only after executing and describing the relevant verification.
4. Validate actual exact-fee cash recovery with permitted sandbox/order evidence
   before unattended live operation. Fractional fills, untracked positions,
   incomplete/missing submissions and changed historical records still block.
5. Review model calibration by source/station and forecast horizon. v3 uses
   provisional uncertainty, not a validated predictive error distribution.
6. Retain explicit separate operator activation. Depositing never creates the
   live marker. Do not activate in an implementation turn.

Read-only authentication passed and was recorded separately from the funding
floor; a $100 deposit is not requested while other gates fail. Live limits remain
$2/order including estimated fees, $10 total, $5/city, $2 daily loss, 10% drawdown,
3 positions and 10 daily orders. Initial approved capital <=$100; earned payouts
can exceed it. Unexplained deposits/withdrawals fail cash reconciliation.

Routine weather/research uses zero LLM calls. Existing conversation API caps remain
$0.03/day and $0.50/month. VPS is a separate $21.09/month cost. Daily Codex follow-up
uses the operator's Codex plan, not these runtime API caps; avoid repeated work.

Repository scratch checkout: `/tmp/apex-weather-review-20260924`; re-clone the
existing branch if it is cleaned up. Original iCloud-backed checkout can contain
dataless files. Use SSH root@178.156.159.178 with the existing id_rsa identity,
IdentitiesOnly, BatchMode and StrictHostKeyChecking; never print key contents or
environment secrets. No delegated agents are authorized by this handoff.

## Daily follow-up — 2026-09-25

The cached report stamped 18:03 UTC showed 9,965 useful v3 snapshots and zero
independent resolved holdout events. Historical realized paper P&L was $977.02;
this is not current-model independent evidence. The drawdown and production
verification blockers remain. No deposit is requested.

Prepared an inactive-live-path timing fix: candidate age, observation age, market
close and weather-window end are rechecked using a fresh clock immediately before
POST, after all potentially slow account/fee checks. Tests advance the clock during
fee lookup and the final gate and confirm zero order calls, with attempted IDs
remaining reserved. Invalid negative/nonfinite observation ages also fail closed.
This conservative path still marks a post-reservation failure unknown and requires
reconciliation; it does not silently retry an expired intent.

This follow-up does not deploy or enable live trading, write production verification
passes, change the model cohort, or adjust risk limits. Paper and Telegram services
remain active. Include this prepared fix in the next reviewed deployment.
