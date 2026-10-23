import json
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace

import pytest

from src.research.consensus_no import direct_no_evidence,qualifies,record_signal,replay
from src.research.experiments import record_candidate
from src.research.strategy_evaluation import evaluate_strategy
from src.storage.database import Database
from src.strategy.settlement import parse_settlement_spec


def inputs():
    return dict(side='no',source='weather_company',probability=.95,price_cents=80,
                contracts=2,fee_usd=.03,spread_cents=2,observation_age=60,
                forecast_disagreement_f=1,seconds_to_close=3600)


@pytest.mark.parametrize('key,value',[
    ('side','yes'),('source','metar'),('probability',.89),('probability',float('nan')),
    ('price_cents',86),('contracts',0),('contracts',3),('fee_usd',-.01),
    ('spread_cents',9),('observation_age',-1),('observation_age',1801),
    ('forecast_disagreement_f',3.1),('seconds_to_close',0),('seconds_to_close',21601),
    ('fee_usd',.21),
])
def test_rejects_unsupported_or_unaffordable_shadow_signal(key,value):
    row=inputs();assert qualifies(row)
    row[key]=value
    assert not qualifies(row)


def fixture(tmp_path):
    db=Database(tmp_path/'research');db.migrate()
    now=datetime(2026,10,23,23,tzinfo=timezone.utc)
    raw=dict(ticker='KXHIGHAUS-26OCT23-B75.5',event_ticker='KXHIGHAUS-26OCT23',
             series_ticker='KXHIGHAUS',close_time='2026-10-24T05:00:00Z',
             rules_primary='If the maximum temperature recorded at Austin (CLIAUS) for Oct 23, 2026, is between 75-76° fahrenheit according to The Weather Company, then the market resolves to Yes.')
    spec=parse_settlement_spec(raw);assert spec.last_trading_time
    edge=SimpleNamespace(side='no',model_probability=.95,executable_price_cents=80,
                         contracts=2,fillable_contracts=2,fee_usd=.03,
                         spread_cents=2,net_ev_usd=.27)
    return db,now,raw,spec,edge


def test_shadow_records_one_event_with_evidence_and_cannot_place_order(tmp_path):
    db,now,raw,spec,edge=fixture(tmp_path)
    kwargs=dict(database=db,spec=spec,now=now,edge=edge,observation_age=60,
                disagreement=1,forecasts_fresh=True,evidence={'forecast_times':['fixture']})
    assert not record_signal(**{**kwargs,'forecasts_fresh':False})
    assert not record_signal(**{**kwargs,'now':now-timedelta(days=10)})
    assert record_signal(**kwargs)
    assert not record_signal(**{**kwargs,'spec':replace(spec,ticker='SIBLING')})
    with db.connect() as c:
        row=c.execute('SELECT * FROM shadow_signals').fetchone()
        assert row['probability']==.9
        assert json.loads(row['evidence_json'])['primary_rule']==spec.raw_rules
        assert c.execute('SELECT COUNT(*) FROM orders').fetchone()[0]==0
        assert c.execute("SELECT value FROM control_state WHERE key='paused'").fetchone()[0]=='false'
    with db.transaction() as c:
        c.execute('INSERT INTO settlements VALUES(?,?,?,?,?,?,?)',
                  (spec.ticker,80,0,'weather_company',1,(now+timedelta(hours=8)).isoformat(),'{}'))
    report=evaluate_strategy(db)
    assert sum(report['fresh_consensus_no_v2'][s]['independent_events']
               for s in ('development','holdout'))==1
    assert sum(report[s]['independent_events'] for s in ('development','holdout'))==0


def test_large_model_market_gap_requires_an_observed_extreme_that_rules_yes_out(tmp_path):
    db,now,raw,spec,edge=fixture(tmp_path)
    cheap_edge=SimpleNamespace(**{**vars(edge), 'executable_price_cents':2})
    kwargs=dict(database=db,spec=spec,now=now,edge=cheap_edge,
                observation_age=60,disagreement=1,forecasts_fresh=True,
                evidence={'observed_extreme_f':74.0})
    assert not record_signal(**kwargs)
    # This high contract is 75--76; a recorded 77 degree daily high makes YES
    # impossible, unlike an ordinary forecast disagreement.
    assert direct_no_evidence(spec,77.0)
    assert record_signal(**{**kwargs,'evidence':{'observed_extreme_f':77.0}})


def test_replay_uses_first_signal_and_requires_forecasts_known_before_signal(tmp_path):
    db,now,raw,spec,edge=fixture(tmp_path)
    with db.transaction() as c:
        for provider in ('Open-Meteo','NWS'):
            c.execute('INSERT INTO forecast_runs(station_id,provider,model,generated_at_utc,ingested_at_utc,raw_json) VALUES(?,?,?,?,?,?)',
                      (spec.station_id,provider,'fixture',now.isoformat(),now.isoformat(),'{}'))
    for i,outcome in enumerate((1,0)):
        market={**raw,'ticker':raw['ticker']+str(i)}
        with db.transaction() as c:
            c.execute('INSERT INTO markets VALUES(?,?,?,?,?,?)',
                (market['ticker'],raw['event_ticker'],raw['series_ticker'],'settled',json.dumps(market),now.isoformat()))
            c.execute('INSERT INTO settlements VALUES(?,?,?,?,?,?,?)',
                (market['ticker'],75,outcome,'weather_company',1,(now+timedelta(hours=8)).isoformat(),'{}'))
        record_candidate(db,spec=replace(spec,ticker=market['ticker']),now=now+timedelta(minutes=i),
            side='no',edge=edge,baseline='SKIP',final='SKIP',jev=None,observation_age=60,
            surprise=None,disagreement=1,probability_change=None,book_change=None,latest_bid=78)
    # Force the synthetic event into development without depending on its hash.
    with db.transaction() as c:c.execute("UPDATE research_candidates SET split='development'")
    result=replay(db,'development')
    assert result['settled_events']==1 and result['wins']==0
    assert result['hypothetical_after_fee_pnl_usd']==-1.63
    with db.transaction() as c:
        c.execute('UPDATE forecast_runs SET ingested_at_utc=?',((now+timedelta(days=1)).isoformat(),))
    assert replay(db,'development')['selected_events']==0
