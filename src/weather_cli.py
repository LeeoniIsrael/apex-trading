"""Operator CLI for safe setup, collection, health, and promotion checks."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from src.kalshi.client import KalshiClientV2
from src.monitoring.health import check_health
from src.research.collector import MarketCollector
from src.research.readiness import ReadinessEvidence, ReadinessPolicy, evaluate_readiness
from src.storage.database import Database
from src.weather_config import WeatherSettings


def _readiness(
    database: Database, bankroll: float = 100.0,
) -> tuple[bool, tuple[str, ...], ReadinessEvidence]:
    with database.connect() as connection:
        snapshots = connection.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0]
        resolved = connection.execute("SELECT COUNT(*) FROM settlements WHERE final=1").fetchone()[0]
        parser_failures = connection.execute(
            "SELECT COUNT(*) FROM health_events WHERE code='settlement_parser_failure'"
        ).fetchone()[0]
        incidents = connection.execute(
            "SELECT COUNT(*) FROM health_events WHERE severity IN ('error','critical')"
        ).fetchone()[0]
        decision_rows = connection.execute(
            "SELECT action,edge_json FROM decisions WHERE edge_json IS NOT NULL"
        ).fetchall()
        realized_pnl = connection.execute(
            "SELECT COALESCE(SUM(realized_pnl_usd),0) FROM positions"
        ).fetchone()[0]
        prediction_rows = connection.execute(
            "SELECT probability,eventual_outcome FROM model_predictions "
            "WHERE eventual_outcome IN (0,1)"
        ).fetchall()
        pnl_rows = connection.execute(
            "SELECT realized_pnl_usd FROM positions WHERE contracts=0 "
            "ORDER BY updated_at,ticker,side"
        ).fetchall()
    brier = None
    if prediction_rows:
        brier = sum((row[0] - row[1]) ** 2 for row in prediction_rows) / len(prediction_rows)
    net_ev = 0.0
    for action, edge_json in decision_rows:
        if action in {"BUY_YES", "BUY_NO"}:
            net_ev += float(json.loads(edge_json).get("net_ev_usd", 0))
    equity = peak = bankroll
    max_drawdown = 0.0
    for row in pnl_rows:
        equity += float(row[0])
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak)
    evidence = ReadinessEvidence(
        market_snapshots=snapshots,
        resolved_markets=resolved,
        brier_score=brier,
        net_ev_usd=net_ev,
        realized_paper_pnl_usd=float(realized_pnl),
        max_drawdown_pct=max_drawdown,
        unresolved_parser_failures=parser_failures,
        major_data_incidents=incidents,
    )
    ready, failures = evaluate_readiness(evidence, ReadinessPolicy())
    return ready, failures, evidence


def main() -> int:
    parser = argparse.ArgumentParser(prog="apex-weather")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db")
    sub.add_parser("discover")
    sub.add_parser("health")
    sub.add_parser("readiness")
    sub.add_parser("run-once")
    enable = sub.add_parser("enable-live")
    enable.add_argument("--confirm", required=True)
    args = parser.parse_args()

    settings = WeatherSettings(trading_mode="paper")
    database = Database(settings.database_path)
    database.migrate()

    if args.command == "init-db":
        print(json.dumps({"database": str(settings.database_path), "integrity": database.integrity_check()}))
        return 0
    if args.command == "discover":
        specs = MarketCollector(KalshiClientV2(base_url=settings.kalshi_base_url), database).discover()
        print(json.dumps({
            "weather_markets": len(specs),
            "tradeable_specs": sum(spec.tradeable for spec in specs),
            "blocked_specs": sum(not spec.tradeable for spec in specs),
        }))
        return 0
    if args.command == "run-once":
        from src.weather_service import WeatherService
        print(json.dumps(WeatherService(settings).run_once(), sort_keys=True))
        return 0
    if args.command == "health":
        report = check_health(database, settings.database_path.parent)
        print(json.dumps({"healthy": report.healthy, "checks": report.checks}, sort_keys=True))
        return 0 if report.healthy else 1

    ready, failures, evidence = _readiness(database, settings.bankroll)
    if args.command == "readiness":
        print(json.dumps({"ready": ready, "failures": failures, "evidence": asdict(evidence)}, default=str))
        return 0 if ready else 2
    if args.command == "enable-live":
        if args.confirm != "I_ACCEPT_LIVE_RISK":
            raise SystemExit("confirmation phrase must be exactly I_ACCEPT_LIVE_RISK")
        if not ready:
            raise SystemExit(f"promotion gate failed: {', '.join(failures)}")
        settings.live_enablement_path.parent.mkdir(parents=True, exist_ok=True)
        settings.live_enablement_path.write_text("explicitly enabled after readiness gate\n")
        print("Live enablement recorded. Set TRADING_MODE=live separately to activate.")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
