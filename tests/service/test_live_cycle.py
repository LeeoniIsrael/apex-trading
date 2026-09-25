"""Synthetic full-cycle wiring tests; no network or profitability claims."""
from dataclasses import replace
from datetime import datetime,timedelta,timezone
import json

import pytest

from src.weather_service import WeatherService
from src.weather_config import WeatherSettings
from src.weather.forecasts import ForecastRun,HourlyForecast
from src.weather.observations import RawObservation,ReportType,parse_metar
from src.strategy.settlement import parse_settlement_spec
from tests.execution.test_live_executor import setup


def cycle(tmp_path,monkeypatch):
    ex,client,paper,live,settings=setup(tmp_path,monkeypatch)
    service=WeatherService(WeatherSettings(_env_file=None,database_path=paper.path))
    service.settings=settings
    service.live=ex
    service.client=client
    now=datetime.now(timezone.utc)
    with paper.connect() as c:
        raw=json.loads(c.execute('SELECT raw_json FROM markets').fetchone()[0])
    raw.update(_fee_type='quadratic',_fee_multiplier=1)
    with paper.transaction() as c:
        c.execute('UPDATE markets SET raw_json=?',(json.dumps(raw),))
        # This unrelated paper drawdown MUST NOT enter live cycle sizing.
        c.execute('INSERT INTO equity_history VALUES(?,?,?)',(now.isoformat(),1,.99))
    spec=replace(parse_settlement_spec(raw),observation_window_start=now-timedelta(hours=2),
                 observation_window_end=now+timedelta(hours=2),last_trading_time=now+timedelta(hours=2))
    assert spec.tradeable
    service._spec_cache=(now,[spec])
    service._last_settlement_reconcile=now
    raw_obs=RawObservation('KAUS','fixture',ReportType.METAR,now,now,'KAUS 251853Z 27/20 RMK T02700200')
    obs=parse_metar(raw_obs,'America/Chicago')
    forecast=ForecastRun('fixture','KAUS','fixture',now,now,((HourlyForecast(now+timedelta(hours=1),80),),))
    service._station_data=lambda *args:([obs],forecast,forecast)
    client.get_orderbook=lambda *a,**k:{'ticker':'TEST','orderbook_fp':{'yes_dollars':[['0.3900','100.00']],'no_dollars':[['0.6000','100.00']]}}
    monkeypatch.setattr('src.research.experiments.allocation',lambda event:('development',False))
    def wrong_account(*args,**kwargs):
        raise AssertionError('live cycle read paper account')
    monkeypatch.setattr('src.weather_service.paper_account',wrong_account)
    return service,client,paper,live


def test_cycle_records_candidate_and_routes_to_real_executor_only(tmp_path,monkeypatch):
    service,client,paper,live=cycle(tmp_path,monkeypatch)
    result=service.run_once()
    assert result['predictions']==1
    assert len(client.calls)==1
    assert client.calls[0]['client_order_id'].startswith('LIVE-')
    with live.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM orders WHERE paper=0').fetchone()[0]==1
    with paper.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM orders').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM research_candidates').fetchone()[0]>=2
        assert c.execute('SELECT COUNT(*) FROM equity_history').fetchone()[0]==1
    assert result['paper_orders']==0 and result['live_orders']==1


@pytest.mark.parametrize('store,control',[('live','paused'),('live','emergency_stop'),('paper','paused')])
def test_full_cycle_respects_both_control_databases(tmp_path,monkeypatch,store,control):
    service,client,paper,live=cycle(tmp_path,monkeypatch)
    with (live if store=='live' else paper).transaction() as c:
        c.execute('UPDATE control_state SET value=? WHERE key=?',('true',control))
    result=service.run_once()
    assert not client.calls and result['predictions']==0


def test_full_cycle_live_cash_drawdown_blocks_order(tmp_path,monkeypatch):
    service,client,paper,live=cycle(tmp_path,monkeypatch)
    with live.transaction() as c:
        c.execute('INSERT INTO control_state VALUES(?,?,?)',('live_peak','200',datetime.now(timezone.utc).isoformat()))
    result=service.run_once()
    assert result['predictions']==1 and not client.calls
    with paper.connect() as c:
        row=c.execute('SELECT final_action FROM research_candidates ORDER BY id DESC LIMIT 1').fetchone()
        assert row[0]=='SKIP'


def test_cycle_failure_durably_pauses_and_queues_alert(tmp_path,monkeypatch):
    from src.weather_daemon import stop_after_live_cycle_failure
    from src.monitoring.live_telegram import LiveAlertQueue
    from src.storage.database import Database
    service,client,paper,live=cycle(tmp_path,monkeypatch)
    stop_after_live_cycle_failure(service)
    reopened=Database(live.path)
    with reopened.connect() as c:
        assert c.execute("SELECT value FROM control_state WHERE key='paused'").fetchone()[0]=='true'
    alerts=LiveAlertQueue(reopened,paper).pending()
    assert len(alerts)==1 and 'cycle_failed' in alerts[0][1]
    service.run_once()
    assert not client.calls


@pytest.mark.parametrize('fault',[None,'missing_metadata','exposure_mismatch','invalid_cash'])
def test_live_risk_uses_exact_audited_cost_and_rejects_bad_state(tmp_path,monkeypatch,fault):
    from decimal import Decimal
    from src.execution.portfolio_audit import PortfolioAudit
    from src.risk.live_state import live_cycle_state
    service,client,paper,live=cycle(tmp_path,monkeypatch)
    audit=PortfolioAudit(Decimal('99.393'),Decimal('.607'),Decimal(0),
                         {'TEST':{'side':'yes','contracts':1,'cost':Decimal('.607')}},1,1)
    if fault=='missing_metadata':
        with paper.transaction() as c: c.execute('DELETE FROM markets')
    elif fault=='exposure_mismatch': audit=replace(audit,open_cost=Decimal('2'))
    elif fault=='invalid_cash': audit=replace(audit,cash=Decimal('NaN'))
    service.live.audit=audit
    if fault:
        with pytest.raises(RuntimeError): live_cycle_state(service.live,paper,datetime.now(timezone.utc))
    else:
        result=live_cycle_state(service.live,paper,datetime.now(timezone.utc))
        assert result.exposure==.607 and result.cities=={'Austin':.607}
        assert result.cash==99.393 and result.tickers==frozenset({'TEST'})


def test_existing_live_position_is_researched_but_not_submitted_again(tmp_path,monkeypatch):
    from decimal import Decimal
    from src.execution.portfolio_audit import PortfolioAudit
    service,client,paper,live=cycle(tmp_path,monkeypatch)
    service.live.audit=PortfolioAudit(Decimal('99.1664'),Decimal('.8336'),Decimal(0),
        {'TEST':{'side':'yes','contracts':2,'cost':Decimal('.8336')}},1,1)
    service.live.reconcile=lambda:service.live.audit
    result=service.run_once()
    assert result['predictions']==1 and result['live_orders']==0
    assert not client.calls
    with paper.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM research_candidates').fetchone()[0]>=2
