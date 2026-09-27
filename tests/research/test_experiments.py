from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

from src.research.experiments import allocation, record_candidate, report
from src.research.readiness import database_readiness, ReadinessEvidence, ReadinessPolicy, evaluate_readiness
from src.research.strategy_evaluation import evaluate_strategy
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


def test_candidate_sampling_keeps_changes_but_not_identical_polling(tmp_path):
    db=Database(tmp_path/'db'); db.migrate()
    start=datetime.now(timezone.utc)
    spec=SimpleNamespace(ticker='TEST',station_id='KAUS',official_source=SimpleNamespace(value='weather_company'),
                         market_date=start.date(),measurement=SimpleNamespace(value='high'),
                         last_trading_time=start+timedelta(hours=2),city='Austin')
    edge=SimpleNamespace(executable_price_cents=40,fillable_contracts=2,fee_usd=.04,
                         model_probability=.8,net_ev_usd=.36,spread_cents=2)

    def save(at, age, price=40, action='SKIP'):
        edge.executable_price_cents=price
        record_candidate(db,spec=spec,now=at,side='yes',edge=edge,baseline='BUY_YES',
                         final=action,jev=None,observation_age=age,surprise=None,
                         disagreement=0,probability_change=None,book_change=None,latest_bid=35)

    save(start,60)
    save(start+timedelta(seconds=30),90)  # Same observation and quote.
    save(start+timedelta(seconds=60),30)  # New official observation.
    save(start+timedelta(seconds=90),60,price=41)  # New executable quote.
    save(start+timedelta(seconds=120),90,price=41,action='BUY_YES')
    save(start+timedelta(minutes=17),60,price=41,action='BUY_YES')
    with db.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM research_candidates').fetchone()[0] == 5


def test_strategy_evaluation_counts_one_pre_settlement_signal_per_event(tmp_path):
    db=Database(tmp_path/'db'); db.migrate()
    now=datetime.now(timezone.utc)
    edge=SimpleNamespace(executable_price_cents=40,fillable_contracts=2,fee_usd=.04,
                         model_probability=.8,net_ev_usd=.36,spread_cents=2)
    for ticker,station in [('FIRST','KAUS'),('SIBLING','KAUS'),('OTHER','KDEN')]:
        spec=SimpleNamespace(ticker=ticker,station_id=station,
                             official_source=SimpleNamespace(value='weather_company'),
                             market_date=now.date(),measurement=SimpleNamespace(value='high'),
                             last_trading_time=now+timedelta(hours=2),city='City')
        record_candidate(db,spec=spec,now=now,side='yes',edge=edge,
                         baseline='BUY_YES',final='SKIP',jev=None,observation_age=60,
                         surprise=None,disagreement=0,probability_change=None,
                         book_change=None,latest_bid=35)
    with db.transaction() as c:
        for ticker in ('FIRST','SIBLING','OTHER'):
            c.execute('INSERT INTO settlements VALUES(?,?,?,?,?,?,?)',
                      (ticker,80,1,'weather_company',1,
                       (now+timedelta(hours=3)).isoformat(),'{}'))
    result=evaluate_strategy(db)
    summaries=[result['development'],result['holdout']]
    assert sum(item['independent_events'] for item in summaries)==2
    assert sum(item['hypothetical_after_fee_pnl_usd'] for item in summaries)==2.32
    assert not any(item['profitable_evidence'] for item in summaries)

    with db.transaction() as c:
        c.execute('UPDATE settlements SET settled_at=? WHERE ticker=?',
                  ((now-timedelta(seconds=1)).isoformat(),'FIRST'))
    assert sum(item['independent_events'] for item in
               (evaluate_strategy(db)['development'],evaluate_strategy(db)['holdout']))==1
