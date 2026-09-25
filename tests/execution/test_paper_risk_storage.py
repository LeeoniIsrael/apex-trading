from pathlib import Path

from src.execution.paper_executor import PaperExecutor, PaperOrderStatus
from src.execution.position_manager import evaluate_exit
from src.kalshi.orderbook import parse_orderbook
from src.research.readiness import ReadinessEvidence, ReadinessPolicy, evaluate_readiness
from src.risk.exposure import RiskPolicy, RiskState, risk_blocks
from src.risk.sizing import SizingLimits, full_kelly_fraction, size_contracts
from src.storage.database import Database


def _book():
    return parse_orderbook({"orderbook": {"yes": [[40, 10]], "no": [[55, 3]]}})


def test_paper_immediate_fill_can_be_partial():
    result = PaperExecutor().submit_buy(
        order_key="one", side="yes", limit_price_cents=45, contracts=5, orderbook=_book(),
    )
    assert result.status == PaperOrderStatus.PARTIALLY_FILLED
    assert result.filled_contracts == 3
    assert result.resting_contracts == 2


def test_paper_resting_order_is_not_assumed_filled():
    result = PaperExecutor(resting_fill_probability=0).submit_buy(
        order_key="two", side="yes", limit_price_cents=44, contracts=5, orderbook=_book(),
    )
    assert result.status == PaperOrderStatus.RESTING
    assert result.filled_contracts == 0


def test_kelly_and_hard_caps():
    assert full_kelly_fraction(.7, .5) == .4
    limits = SizingLimits(bankroll_usd=100, max_position_usd=5, max_position_pct=.05)
    assert size_contracts(probability=.7, price_cents=50, limits=limits,
                          current_total_exposure_usd=0, calibration_quality=1) == 10


def test_risk_fails_closed_on_limits():
    reasons = risk_blocks(
        RiskState(5, 24, 9, -5, .15), RiskPolicy(), new_exposure_usd=2,
    )
    assert {"daily_loss_limit", "drawdown_limit", "open_market_limit",
            "total_exposure_limit", "correlated_city_limit"} <= set(reasons)


def test_sqlite_wal_migrations_are_idempotent(tmp_path: Path):
    database = Database(tmp_path / "weather.sqlite3")
    database.migrate()
    database.migrate()
    assert database.integrity_check() == "ok"
    with database.connect() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"settlement_specs", "weather_observations", "model_predictions",
            "orders", "fills", "costs", "health_events"} <= tables


def test_readiness_rejects_tiny_or_unprofitable_sample():
    ready, failures = evaluate_readiness(
        ReadinessEvidence(10, 2, .1, 5, 2, .01, 0, 0), ReadinessPolicy(),
    )
    assert not ready
    assert "insufficient_market_snapshots" in failures
    assert "insufficient_resolved_markets" in failures


def test_position_exit_uses_updated_forward_ev_not_stop_loss():
    exit_now = evaluate_exit(
        contracts=10, model_probability=.20, executable_bid_cents=40,
    )
    assert exit_now.should_exit
    hold = evaluate_exit(
        contracts=10, model_probability=.80, executable_bid_cents=40,
    )
    assert not hold.should_exit


def test_migrate_existing_database_preserves_orders_and_costs(tmp_path):
    from src.storage.database import MIGRATIONS
    db=Database(tmp_path/'upgrade')
    with db.transaction() as c:
        for version,sql in MIGRATIONS[:3]:
            c.executescript(sql)
            c.execute('INSERT INTO schema_migrations(version) VALUES(?)',(version,))
        c.execute("INSERT INTO costs(incurred_at,category,amount_usd,description) VALUES('2026-09-23','infrastructure',21.09,'existing')")
    db.migrate(); db.migrate()
    with db.connect() as c:
        assert c.execute('SELECT SUM(amount_usd) FROM costs').fetchone()[0]==21.09
        assert c.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0]==len(MIGRATIONS)
