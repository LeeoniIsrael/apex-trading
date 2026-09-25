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

## Demo account integration — 2026-09-25 19:49 UTC

User supplied a demo-only key. The downloaded key is Ed25519; the former RSA-only
signer failed before making an HTTP request. Added parsed-key-type dispatch for
Ed25519 and RSA, with independent public-key signature verification tests for
Ed25519, RSA PKCS#1 and RSA PKCS#8 and rejection of unsupported EC keys.
Official reference: https://docs.kalshi.com/getting_started/api_keys .

Authenticated against the explicitly pinned demo endpoint
`https://external-api.demo.kalshi.co/trade-api/v2`. Starting mock cash was $200.
The credential is stored outside git in the operator's private configuration
directory, with directory mode 0700 and credential/config files mode 0600.
No production credential, configuration, service or verification pass changed.

A one-off integration harness used the actual REST client and portfolio auditor,
with its own SQLite database and durable intents. It did NOT run the full
LiveExecutor or weather profit-selection strategy. Read and parsed the complete
market rules, then tested `KXHIGHNY-26SEP26-T62`:

- One YES contract limit 1 cent, IOC: cancelled with zero fills.
- One YES contract limit 59 cents, IOC: filled at 59 cents, exact fee $0.017.
- Remaining cash $199.3930; open cost including fee $0.6070; one open YES
  contract; realized profit $0. No resting orders remain.
- Initial post-fill portfolio read omitted the order, so reconciliation blocked.
  Later read-only reconciliation recovered it without resubmitting. Added a
  regression test for this delayed-history failure/recovery path.
- Reopening the database and auditing again produced identical totals; inserting
  the same durable intent was rejected by SQLite uniqueness before any POST.
- The single-market GET returned 404 in demo although list and orderbook endpoints
  returned the market. The harness used the complete listed market record.

Evidence and harness are at
`~/.local/share/apex-weather/demo-test/report.json`, `demo.sqlite3`, and `check.py`.
Do not rerun the submission harness to seek more fills; it intentionally reserves
fixed unique IDs. Read-only reconciliation can be repeated independently.
The demo-only $200 accounting baseline matches Kalshi's mock grant and does not
raise the production $100 capital limit. This is exchange plumbing evidence,
not strategy performance, settlement verification, a production execution test,
or a passed full unattended-live launch checklist. No real-money order was sent.
Changes are prepared locally and pushed; production deployment remains pending.

User now explicitly requests considering an unproven $100 pilot before 200
resolved events. The 200-event threshold is research policy, not an exchange
requirement. It has not been waived in code, and the live marker remains absent.

## Demo stop and notification checks — 2026-09-25

User clarified that the newly funded account was Kalshi DEMO. Read-only production
balance is still $0.2485; no real $100 deposit has been observed. Production is
paper mode and has no live marker. Do not present the demo grant as real funding.

Ran the actual `emergency-stop` CLI against the isolated demo and research SQLite
files, using an explicit temporary test marker. Both databases durably latched
pause and emergency stop, and the marker was removed. Reopening both databases
kept status stopped; Telegram `/resume` refused activation. Order count remained
two. Demo state intentionally remains stopped after testing.

Rendered the actual demo fill through LiveAlertQueue, explicitly replaced the
real-money heading with `DEMO TEST — pretend money`, and delivered it to the
operator's configured Telegram chat. Telegram acknowledged message 47. Only then
was delivery marked in the demo database. Reopening the queue suppressed repeat
delivery. Production database verification flags were not changed. This proves
the particular demo fill notification and durable acknowledgement, not receipt
by the human or every production alert type.

Evidence is appended to the same private local demo report. The full autonomous
service-to-exchange path, real settlement lifecycle, production deployment of the
latest changes, and chosen validation/pilot policy remain outstanding. Existing
isolated unit tests and these demo checks must not be described as full unattended
real-money launch readiness. No production activation or order was performed.
