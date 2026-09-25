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
    from src.research.readiness import database_readiness
    return database_readiness(database, bankroll)


def main() -> int:
    parser = argparse.ArgumentParser(prog="apex-weather")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db")
    sub.add_parser("discover")
    sub.add_parser("health")
    sub.add_parser("readiness")
    sub.add_parser("research")
    sub.add_parser("accounting")
    sub.add_parser("run-once")
    enable = sub.add_parser("enable-live")
    enable.add_argument("--confirm", required=True)
    args = parser.parse_args()

    settings = WeatherSettings(trading_mode="paper")
    database = Database(settings.database_path)
    database.migrate()

    if args.command in {"research", "accounting"}:
        from src.research.accounting import paper_account
        from src.research.experiments import report
        result = report(database, settings.bankroll) if args.command == "research" else asdict(paper_account(database, settings.bankroll))
        print(json.dumps(result, default=str))
        return 0
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
