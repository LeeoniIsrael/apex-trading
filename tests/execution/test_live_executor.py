import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.execution.live_executor import LiveExecutor, authenticated_balance
from src.storage.database import Database
from src.weather_config import WeatherSettings


class FakeClient:
    signer = object()
    def __init__(self): self.calls=[]; self.orders=[]
    def get_balance(self): return {'balance':10000}
    def get_orders(self,**kw): return {'orders':self.orders}
    def get_fills(self,**kw): return {'fills':[]}
    def get_settlements(self,**kw): return {'settlements':[]}
    def get_positions(self,**kw): return {'market_positions':[]}
    def create_order(self,**kwargs):
        self.calls.append(kwargs)
        self.orders.append(dict(kwargs,order_id='remote-'+kwargs['client_order_id'],initial_count_fp=str(kwargs['contracts']),fill_count_fp='0',remaining_count_fp='0',status='canceled'))
        return {'order':{'status':'cancelled','fill_count':0}}


def setup(tmp_path, monkeypatch):
    paper=Database(tmp_path/'paper'); paper.migrate()
    live=Database(tmp_path/'live'); live.migrate()
    marker=tmp_path/'marker'
    marker.write_text(json.dumps({'confirmation':'I_ACCEPT_LIVE_RISK','paper_database':str(paper.path.resolve()),'live_database':str(live.path.resolve())}))
    settings=WeatherSettings(_env_file=None,trading_mode='live',database_path=paper.path,live_database_path=live.path,live_enablement_path=marker)
    monkeypatch.setattr('src.execution.live_executor.database_readiness',lambda *a:(True,(),None))
    now=datetime.now(timezone.utc)
    market={'ticker':'TEST','rules_primary':f'Maximum temperature at CLIAUS for {now.strftime("%b %d, %Y")} is less than 96 according to The Weather Company.','_fee_type':'quadratic','_fee_multiplier':1}
    with paper.transaction() as c:
        c.execute('INSERT INTO markets(ticker,raw_json,observed_at) VALUES(?,?,?)',('TEST',json.dumps(market),now.isoformat()))
        c.execute("INSERT INTO research_candidates(ticker,event_key,captured_at,split,model_version,side,price_cents,contracts,fee_usd,probability,net_ev_usd,baseline_action,final_action,source,station,city,market_type,price_bucket,time_bucket,seconds_to_close,observation_age,liquidity,lag_candidate,control) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",('TEST','event',now.isoformat(),'holdout','v1','yes',40,2,.04,.9,.96,'BUY_YES','BUY_YES','twc','KAUS','Austin','high','>10c','early',3600,60,2,0,0))
    # Keep date boundaries independent of the time this test runs.
    from datetime import timedelta
    monkeypatch.setattr('src.execution.live_executor.parse_settlement_spec', lambda raw:SimpleNamespace(tradeable=True,observation_window_end=now+timedelta(hours=2),last_trading_time=now+timedelta(hours=2),city='Austin'))
    client=FakeClient()
    return LiveExecutor(settings,client,live),client,paper,live,settings


def submit(executor, **overrides):
    args=dict(ticker='TEST',side='yes',price_cents=40,contracts=2,client_order_id='stable')
    args.update(overrides)
    return executor.submit_buy(**args)


def test_paper_cannot_instantiate_live(tmp_path):
    settings=WeatherSettings(_env_file=None,database_path=tmp_path/'paper',live_enablement_path=tmp_path/'absent')
    with pytest.raises(RuntimeError,match='not explicitly enabled'):
        LiveExecutor(settings,FakeClient(),Database(tmp_path/'live'))


def test_live_idempotency_survives_restart(tmp_path,monkeypatch):
    ex,client,paper,live,s=setup(tmp_path,monkeypatch)
    submit(ex)
    ex=LiveExecutor(s,client,live)
    with pytest.raises(RuntimeError,match='duplicate'): submit(ex)
    assert len(client.calls)==1
    assert client.calls[0]['client_order_id']=='LIVE-stable'


@pytest.mark.parametrize('block',['stop','marker','stale','balance','readiness','order_limit','unknown_position','total_limit','city_limit','daily_count','daily_loss','drawdown','pagination','auth'])
def test_guards_prevent_any_post(tmp_path,monkeypatch,block):
    ex,client,paper,live,s=setup(tmp_path,monkeypatch)
    if block=='stop':
        with paper.transaction() as c: c.execute("UPDATE control_state SET value='true' WHERE key='emergency_stop'")
    elif block=='marker': s.live_enablement_path.unlink()
    elif block=='stale':
        with paper.transaction() as c: c.execute("UPDATE research_candidates SET captured_at='2000-01-01T00:00:00+00:00'")
    elif block=='balance': client.get_balance=lambda:{'balance':1}
    elif block=='readiness': monkeypatch.setattr('src.execution.live_executor.database_readiness',lambda *a:(False,('insufficient_markets',),None))
    elif block=='order_limit': s.live_max_order_usd=.1
    elif block=='unknown_position': client.get_positions=lambda:{'market_positions':[{'position':1}]}
    elif block=='total_limit': s.live_max_exposure_usd=.1
    elif block=='city_limit': s.live_max_city_exposure_usd=.1
    elif block=='daily_count':
        s.live_max_daily_orders=1
        with live.transaction() as c:
            stamp=datetime.now(timezone.utc).isoformat()
            c.execute('INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',('old','LIVE-old','TEST','yes','buy',1,1,0,'cancelled',0,stamp,stamp))
    elif block in ('daily_loss','drawdown'):
        with live.transaction() as c:
            key='live_peak' if block=='drawdown' else 'live_day_'+datetime.now(timezone.utc).date().isoformat()
            c.execute('INSERT INTO control_state VALUES(?,?,?)',(key,'200',datetime.now(timezone.utc).isoformat()))
    elif block=='pagination': client.get_orders=lambda **kw:{'orders':[],'cursor':'next'}
    elif block=='auth': client.signer=None
    with pytest.raises(RuntimeError): submit(ex)
    assert not client.calls


def test_uncertain_post_is_never_retried(tmp_path,monkeypatch):
    ex,client,paper,live,s=setup(tmp_path,monkeypatch)
    def fail(**kw): client.calls.append(kw); raise TimeoutError()
    client.create_order=fail
    with pytest.raises(RuntimeError,match='uncertain'): submit(ex)
    with pytest.raises(RuntimeError,match='duplicate'): submit(ex)
    with pytest.raises(RuntimeError,match='ambiguous'): LiveExecutor(s,client,live)
    assert len(client.calls)==1


def test_balance_rejects_missing_auth_or_nan():
    c=FakeClient(); c.signer=None
    with pytest.raises(RuntimeError): authenticated_balance(c)
    c.signer=object(); c.get_balance=lambda:{'balance_dollars':'NaN'}
    with pytest.raises(RuntimeError): authenticated_balance(c)


def test_larger_account_cannot_bypass_hundred_dollar_budget(tmp_path,monkeypatch):
    ex,client,paper,live,s=setup(tmp_path,monkeypatch)
    client.get_balance=lambda:{'balance':10100}
    with pytest.raises(RuntimeError,match='cash mismatch'): submit(ex)
    assert not client.calls
