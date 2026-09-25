from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

from src.research.experiments import allocation, record_candidate, report
from src.research.readiness import database_readiness, ReadinessEvidence, ReadinessPolicy, evaluate_readiness
from src.storage.database import Database


def test_allocation_is_fixed_by_event_and_readiness_is_fail_closed(tmp_path):
    assert allocation('Austin|2026-09-23') == allocation('Austin|2026-09-23')
    db=Database(tmp_path/'db'); db.migrate(); db.migrate()
    ready, failures, evidence=database_readiness(db)
    assert not ready
    assert evidence.resolved_markets == 0
    assert 'independent_evaluation' in failures
    assert 'telegram_alerts_verified' in failures


def test_repeated_snapshots_count_once_and_controls_have_no_actual_profit(tmp_path):
    db=Database(tmp_path/'db'); db.migrate()
    now=datetime.now(timezone.utc)
    spec=SimpleNamespace(ticker='TEST',station_id='KAUS',official_source=SimpleNamespace(value='twc'), market_date=now.date(),measurement=SimpleNamespace(value='high'), observation_window_end=now+timedelta(hours=1),last_trading_time=now+timedelta(minutes=30),city='Austin')
    edge=SimpleNamespace(executable_price_cents=5,fillable_contracts=10,fee_usd=.04,model_probability=.8,net_ev_usd=7.46,spread_cents=2)
    for i in range(20):
        record_candidate(db,spec=spec,now=now+timedelta(seconds=i),side='yes',edge=edge,baseline='BUY_YES',final='SKIP',jev='VETO',observation_age=60,surprise=3,disagreement=4,probability_change=.1,book_change=.01,latest_bid=3)
    with db.transaction() as c:
        c.execute('INSERT INTO settlements VALUES(?,?,?,?,?,?,?)',('TEST',99,1,'twc',1,now.isoformat(),'{}'))
    with db.connect() as c:
        assert c.execute('SELECT seconds_to_close FROM research_candidates ORDER BY id LIMIT 1').fetchone()[0]==1800
    metrics=report(db)['arms']
    assert all(v['markets']==1 for v in metrics.values())
    assert all(v['actual_pnl']==0 for v in metrics.values())
    assert all(v['winners_vetoed']==1 for v in metrics.values())


def test_nan_cannot_pass_readiness():
    ready,failures=evaluate_readiness(ReadinessEvidence(5000,200,float('nan'),float('nan'),2,.01,0,0),ReadinessPolicy())
    assert not ready and 'nonfinite_evidence' in failures and 'calibration_gate' in failures
