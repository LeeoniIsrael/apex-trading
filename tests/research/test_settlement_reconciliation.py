import json
import pytest
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


@pytest.mark.parametrize("side,threshold,expected,outcome", [("yes",96,6,1),("yes",94,-4,0),("no",94,6,0),("no",96,-4,1)])
def test_official_result_settles_paper_position_and_labels_predictions(tmp_path: Path, side, threshold, expected, outcome):
    database = Database(tmp_path / "db.sqlite3")
    database.migrate()
    market = {
        "ticker": "KXHIGHAUS-26SEP23-T96",
        "event_ticker": "KXHIGHAUS-26SEP23",
        "series_ticker": "KXHIGHAUS",
        "title": "Highest temperature in Austin",
        "rules_primary": f"Maximum temperature at CLIAUS for Sep 23, 2026 is less than {threshold} according to The Weather Company.",
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
            "VALUES(?,?,?,?,0,?)", (market["ticker"], side, 10, 40, "2026-09-23T12:00:00+00:00"),
        )
        connection.execute(
            "INSERT INTO model_predictions(ticker,predicted_at,model_version,probability,inputs_json) "
            "VALUES(?,?,?,?,?)", (market["ticker"], "2026-09-23T12:00:00+00:00", "v1", .7, "{}"),
        )
    reconciler = SettlementReconciler(
        database, nws_user_agent="test", twc=FakeTWC(), nws=NoNWS(),  # type: ignore[arg-type]
    )
    assert reconciler.reconcile() == 1
    assert reconciler.reconcile() == 0
    with database.connect() as connection:
        position = connection.execute(
            "SELECT contracts,realized_pnl_usd FROM positions WHERE ticker=?", (market["ticker"],)
        ).fetchone()
        actual_outcome = connection.execute(
            "SELECT eventual_outcome FROM model_predictions WHERE ticker=?", (market["ticker"],)
        ).fetchone()[0]
    assert position["contracts"] == 0
    assert position["realized_pnl_usd"] == expected
    assert actual_outcome == outcome


def test_partial_fills_multiple_sides_and_unknown_rules(tmp_path):
    from tests.research.test_accounting import fill
    from src.research.accounting import paper_account
    db=Database(tmp_path/'db'); db.migrate()
    market={'ticker':'TEST','rules_primary':'Maximum temperature at CLIAUS for Sep 23, 2026 is less than 96 according to The Weather Company.'}
    with db.transaction() as c:
        c.execute('INSERT INTO markets(ticker,raw_json,observed_at) VALUES(?,?,?)',('TEST',json.dumps(market),'2026-09-23'))
        fill(c,'a','yes',4,25,.08)
        fill(c,'b','yes',6,50,.12)
        fill(c,'c','no',2,25,.02)
        c.execute('INSERT INTO positions VALUES(?,?,?,?,?,?)',('TEST','yes',10,40,0,'2026-09-23'))
        c.execute('INSERT INTO positions VALUES(?,?,?,?,?,?)',('TEST','no',2,25,0,'2026-09-23'))
    reconciler=SettlementReconciler(db,nws_user_agent='test',twc=FakeTWC(),nws=NoNWS())
    assert reconciler.reconcile()==1  # positions reconcile even without predictions
    assert reconciler.reconcile()==0
    a=paper_account(db,100)
    assert a.realized_pnl==pytest.approx(5.28)
    assert not a.discrepancies
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM orders WHERE status='cancelled'").fetchone()[0]==3
    unknown=Database(tmp_path/'unknown'); unknown.migrate()
    with unknown.transaction() as c:
        c.execute('INSERT INTO markets(ticker,raw_json,observed_at) VALUES(?,?,?)',('TEST',json.dumps({'ticker':'TEST','title':'Unknown rules'}),'2026-09-23'))
        c.execute('INSERT INTO positions VALUES(?,?,?,?,?,?)',('TEST','yes',10,40,0,'2026-09-23'))
    assert SettlementReconciler(unknown,nws_user_agent='test',twc=FakeTWC(),nws=NoNWS()).reconcile()==0
