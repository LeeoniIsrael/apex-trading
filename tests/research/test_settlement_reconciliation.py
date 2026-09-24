import json
from datetime import date
from pathlib import Path

from src.research.settlements import SettlementReconciler
from src.storage.database import Database
from src.weather.weather_company import WeatherCompanyDailyResult


class FakeTWC:
    def daily_result(self, station, report_date):
        return WeatherCompanyDailyResult(
            station_id=station.station_id,
            report_date=report_date,
            maximum_f=95,
            minimum_f=70,
            status="official",
            official=True,
            raw={"status": "official"},
        )


class NoNWS:
    def latest(self, station, report_date):
        raise AssertionError("NWS should not be called")


def test_official_result_settles_paper_position_and_labels_predictions(tmp_path: Path):
    database = Database(tmp_path / "db.sqlite3")
    database.migrate()
    market = {
        "ticker": "KXHIGHAUS-26SEP23-T96",
        "event_ticker": "KXHIGHAUS-26SEP23",
        "series_ticker": "KXHIGHAUS",
        "title": "Highest temperature in Austin",
        "rules_primary": "Maximum temperature at CLIAUS for Sep 23, 2026 is less than 96 according to The Weather Company.",
    }
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO markets(ticker,event_ticker,series_ticker,status,raw_json,observed_at) "
            "VALUES(?,?,?,?,?,?)",
            (market["ticker"], market["event_ticker"], market["series_ticker"],
             "closed", json.dumps(market), "2026-09-24T00:00:00+00:00"),
        )
        connection.execute(
            "INSERT INTO positions(ticker,side,contracts,average_price_cents,realized_pnl_usd,updated_at) "
            "VALUES(?,?,?,?,0,?)", (market["ticker"], "yes", 10, 40, "2026-09-23T12:00:00+00:00"),
        )
        connection.execute(
            "INSERT INTO model_predictions(ticker,predicted_at,model_version,probability,inputs_json) "
            "VALUES(?,?,?,?,?)", (market["ticker"], "2026-09-23T12:00:00+00:00", "v1", .7, "{}"),
        )
    reconciler = SettlementReconciler(
        database, nws_user_agent="test", twc=FakeTWC(), nws=NoNWS(),  # type: ignore[arg-type]
    )
    assert reconciler.reconcile() == 1
    with database.connect() as connection:
        position = connection.execute(
            "SELECT contracts,realized_pnl_usd FROM positions WHERE ticker=?", (market["ticker"],)
        ).fetchone()
        outcome = connection.execute(
            "SELECT eventual_outcome FROM model_predictions WHERE ticker=?", (market["ticker"],)
        ).fetchone()[0]
    assert position["contracts"] == 0
    assert position["realized_pnl_usd"] == 6.0
    assert outcome == 1
