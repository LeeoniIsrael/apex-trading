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
    sub.add_parser("launch-report")
    sub.add_parser("research")
    sub.add_parser("accounting")
    sub.add_parser("balance-test")
    sub.add_parser("emergency-stop")
    sub.add_parser("run-once")
    enable = sub.add_parser("enable-live")
    enable.add_argument("--confirm", required=True)
    enable.add_argument("--accept-unvalidated-strategy", action="store_true")
    args = parser.parse_args()

    settings = WeatherSettings(trading_mode="paper")
    database = Database(settings.database_path)
    database.migrate()

    if args.command == "launch-report":
        from src.research.launch_report import write_launch_report
        report = write_launch_report(database, settings.bankroll, settings.live_validation_profile)
        print(json.dumps(report))
        return 0 if report['ready'] else 2
    if args.command == "emergency-stop":
        from datetime import datetime, timezone
        for db in (database, Database(settings.live_database_path)):
            db.migrate()
            with db.transaction() as c:
                for key in ('emergency_stop', 'paused'):
                    c.execute('INSERT INTO control_state VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at', (key,'true',datetime.now(timezone.utc).isoformat()))
                c.execute("INSERT INTO health_events(occurred_at,severity,component,code,message,details_json) VALUES(?,?,?,?,?,?)", (datetime.now(timezone.utc).isoformat(),'critical','controls','emergency_stop','Emergency stop latched','{}'))
        settings.live_enablement_path.unlink(missing_ok=True)
        print("Emergency stop latched. No new orders. Cancel existing remote orders through Kalshi and reconcile before recovery.")
        return 0
    if args.command == "balance-test":
        from datetime import datetime, timezone
        from src.kalshi.auth import KalshiSigner
        from src.execution.live_executor import authenticated_balance
        if not settings.kalshi_api_key_id or not settings.kalshi_private_key_path:
            raise SystemExit('Kalshi credentials are not configured')
        client = KalshiClientV2(base_url=settings.kalshi_base_url, signer=KalshiSigner(settings.kalshi_api_key_id, settings.kalshi_private_key_path))
        balance = authenticated_balance(client)
        passed = balance >= settings.live_balance_floor_usd
        with database.transaction() as c:
            c.execute('INSERT OR REPLACE INTO verification_checks VALUES(?,?,?,?)', ('authenticated_balance',1,datetime.now(timezone.utc).isoformat(),'Read-only authentication validated; funding floor is checked separately before activation'))
        print(json.dumps({'authenticated':True, 'safety_floor_passed':passed}))
        return 0 if passed else 2
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
        if settings.live_validation_profile == 'experimental_100':
            if not args.accept_unvalidated_strategy:
                raise SystemExit('experimental pilot requires --accept-unvalidated-strategy')
            from src.research.readiness import launch_failures
            failures = launch_failures(failures, settings.live_validation_profile)
            ready = not failures
        if not ready:
            raise SystemExit(f"promotion gate failed: {', '.join(failures)}")
        settings.live_enablement_path.parent.mkdir(parents=True, exist_ok=True)
        from datetime import datetime, timezone
        from src.execution.live_executor import authenticated_balance
        from src.kalshi.auth import KalshiSigner
        if not settings.kalshi_api_key_id or not settings.kalshi_private_key_path:
            raise SystemExit('Kalshi credentials are not configured')
        client = KalshiClientV2(base_url=settings.kalshi_base_url, signer=KalshiSigner(settings.kalshi_api_key_id, settings.kalshi_private_key_path))
        if authenticated_balance(client) < settings.live_balance_floor_usd:
            raise SystemExit('balance safety floor failed')
        if settings.database_path.resolve() == settings.live_database_path.resolve():
            raise SystemExit('paper and live databases must be separate')
        with settings.live_enablement_path.open('x') as marker:
            marker.write(json.dumps({'confirmation':args.confirm, 'paper_database':str(settings.database_path.resolve()), 'live_database':str(settings.live_database_path.resolve()), 'created_at':datetime.now(timezone.utc).isoformat(), 'validation_profile':settings.live_validation_profile, 'unvalidated_strategy_acknowledged':args.accept_unvalidated_strategy}))
        settings.live_enablement_path.chmod(0o600)
        print("Live enablement recorded. Set TRADING_MODE=live separately to activate.")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
