"""Evidence-based paper-to-live promotion gate."""

from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True, slots=True)
class ReadinessEvidence:
    market_snapshots: int
    resolved_markets: int
    brier_score: float | None
    net_ev_usd: float
    realized_paper_pnl_usd: float
    max_drawdown_pct: float
    unresolved_parser_failures: int
    major_data_incidents: int
    independent_evaluation: bool = False
    accounting_verified: bool = False
    authenticated_balance_verified: bool = False
    idempotency_verified: bool = False
    emergency_stop_verified: bool = False
    telegram_alerts_verified: bool = False
    equity_reporting_verified: bool = False
    live_recovery_verified: bool = False
    live_monitoring_verified: bool = False
    fee_schedule_verified: bool = False


@dataclass(frozen=True, slots=True)
class ReadinessPolicy:
    min_market_snapshots: int = 5_000
    min_resolved_markets: int = 200
    max_brier_score: float = 0.20
    max_drawdown_pct: float = 0.15


def evaluate_readiness(evidence: ReadinessEvidence, policy: ReadinessPolicy) -> tuple[bool, tuple[str, ...]]:
    failures: list[str] = []
    if not all(math.isfinite(v) for v in (evidence.net_ev_usd, evidence.realized_paper_pnl_usd, evidence.max_drawdown_pct)):
        failures.append("nonfinite_evidence")
    for name in ("independent_evaluation", "accounting_verified", "authenticated_balance_verified",
                 "idempotency_verified", "emergency_stop_verified", "telegram_alerts_verified", "equity_reporting_verified", "live_recovery_verified",
                 "live_monitoring_verified", "fee_schedule_verified"):
        if not getattr(evidence, name):
            failures.append(name)

    if evidence.market_snapshots < policy.min_market_snapshots:
        failures.append("insufficient_market_snapshots")
    if evidence.resolved_markets < policy.min_resolved_markets:
        failures.append("insufficient_resolved_markets")
    if evidence.brier_score is None or not math.isfinite(evidence.brier_score) or not 0 <= evidence.brier_score <= policy.max_brier_score:
        failures.append("calibration_gate")
    if evidence.net_ev_usd <= 0:
        failures.append("nonpositive_net_ev")
    if evidence.realized_paper_pnl_usd <= 0:
        failures.append("nonpositive_paper_pnl")
    if evidence.max_drawdown_pct > policy.max_drawdown_pct:
        failures.append("drawdown_gate")
    if evidence.unresolved_parser_failures:
        failures.append("settlement_parser_failures")
    if evidence.major_data_incidents:
        failures.append("data_quality_incidents")
    return not failures, tuple(failures)



def database_readiness(database, bankroll=100.0):
    from datetime import datetime, timezone
    from src.research.accounting import paper_account
    from src.research.experiments import MODEL_VERSION
    account = paper_account(database, bankroll)
    with database.connect() as c:
        # Count useful forecasts with executable books, not discovery-only snapshots.
        snapshots = c.execute("SELECT COUNT(*) FROM (SELECT DISTINCT ticker,price_cents,probability,"
                              "CAST(strftime('%s',captured_at)-observation_age AS INTEGER) AS observation_time "
                              "FROM research_candidates WHERE model_version=?)", (MODEL_VERSION,)).fetchone()[0]
        rows = c.execute("SELECT r.*,s.yes_outcome FROM research_candidates r JOIN settlements s "
                         "ON s.ticker=r.ticker AND s.final=1 WHERE r.split='holdout' AND r.model_version=? AND r.id IN "
                         "(SELECT MIN(id) FROM research_candidates WHERE model_version=? AND baseline_action LIKE 'BUY%' GROUP BY event_key)", (MODEL_VERSION, MODEL_VERSION)).fetchall()
        issues = c.execute("SELECT COUNT(*) FROM health_events WHERE severity IN ('error','critical') "
                           "OR code IN ('settlement_parser_failure','settlement_reconcile_failed','fetch_failed','unknown_fee_schedule','invalid_fee_multiplier')").fetchone()[0]
        checks = {r['name']: bool(r['passed']) and 0 <= (datetime.now(timezone.utc)-datetime.fromisoformat(r['checked_at'])).total_seconds() < 86400
                  for r in c.execute('SELECT * FROM verification_checks')}
        historical = c.execute('SELECT COALESCE(MAX(drawdown),0) FROM equity_history').fetchone()[0]
    n = len(rows)
    outcomes = [(r, int(r['yes_outcome'] == (1 if r['side']=='yes' else 0))) for r in rows]
    brier = sum((r['probability']-y)**2 for r,y in outcomes)/n if n else None
    # One observation per event; lower confidence bound on realized holdout return.
    returns = [r['contracts']*(y-r['price_cents']/100)-r['fee_usd'] for r,y in outcomes]
    mean = sum(returns)/n if n else 0
    variance = sum((v-mean)**2 for v in returns)/(n-1) if n>1 else 0
    lower_bound = mean-1.96*math.sqrt(variance/n) if n>1 else 0
    evidence = ReadinessEvidence(snapshots,n,brier,lower_bound,account.realized_pnl,
        max(account.max_drawdown,historical),0,issues,
        independent_evaluation=n>=200, accounting_verified=not account.discrepancies,
        authenticated_balance_verified=checks.get('authenticated_balance',False),
        idempotency_verified=checks.get('idempotency',False),
        emergency_stop_verified=checks.get('emergency_stop',False),
        telegram_alerts_verified=checks.get('telegram_alerts',False),
        equity_reporting_verified=checks.get('equity_reporting',False),
        live_recovery_verified=checks.get('live_recovery',False),
        live_monitoring_verified=checks.get('live_monitoring',False),
        fee_schedule_verified=checks.get('fee_schedule',False))
    ready, failures = evaluate_readiness(evidence, ReadinessPolicy())
    return ready, failures, evidence
