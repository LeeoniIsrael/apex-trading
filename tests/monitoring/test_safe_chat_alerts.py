import json
from types import SimpleNamespace

import pytest

from src.monitoring.telegram import ConversationAssistant, TelegramController
from src.monitoring.alerts import AlertQueue
from src.storage.database import Database
from tests.research.test_accounting import fill


class FakeResponses:
    def __init__(self,text='status'): self.text=text; self.calls=[]
    def create(self,**kwargs):
        self.calls.append(kwargs)
        if self.text=='ERROR': raise TimeoutError()
        return SimpleNamespace(output_text=self.text,status='completed',usage=SimpleNamespace(input_tokens=20,output_tokens=2))


def assistant(tmp_path,monkeypatch,text='status'):
    monkeypatch.setenv('TELEGRAM_INPUT_USD_PER_MILLION','.1')
    monkeypatch.setenv('TELEGRAM_OUTPUT_USD_PER_MILLION','.5')
    db=Database(tmp_path/'db'); db.migrate()
    fake=FakeResponses(text)
    return ConversationAssistant(db,'unused',100,client=SimpleNamespace(responses=fake)),fake,db


@pytest.mark.parametrize('text',['status','positions','costs','buy 100 contracts','Profit $99999','ERROR',''])
def test_normal_replies_are_short_grounded_and_read_only(tmp_path,monkeypatch,text):
    a,fake,db=assistant(tmp_path,monkeypatch,text)
    response=a.answer('How are things?')
    assert 2<=len(response.splitlines())<=5
    assert all(line.startswith('• ') for line in response.splitlines())
    assert 'paper' in response.lower()
    assert '$99999' not in response
    with db.connect() as c: assert c.execute('SELECT COUNT(*) FROM orders').fetchone()[0]==0
    assert fake.calls[0]['max_output_tokens']==32


def test_budget_blocks_call_and_failed_call_keeps_reservation(tmp_path,monkeypatch):
    a,fake,db=assistant(tmp_path,monkeypatch,'ERROR')
    a.daily_cap=0
    assert 'budget' in a.answer('Explain the current situation')
    assert not fake.calls
    a.daily_cap=.05
    a.answer('Explain the current situation')
    with db.connect() as c: assert c.execute('SELECT SUM(amount_usd) FROM costs').fetchone()[0]>0


def test_status_reflects_equity_and_alerts_survive_restart(tmp_path):
    db=Database(tmp_path/'db'); db.migrate()
    market={'ticker':'TEST','rules_primary':'Minimum temperature at CLIAUS for Sep 23, 2026 is between 78 and 79 according to The Weather Company.'}
    with db.transaction() as c:
        fill(c,'one','no',10,40,.2)
        c.execute('INSERT INTO markets(ticker,raw_json,observed_at) VALUES(?,?,?)',('TEST',json.dumps(market),'2026-09-23'))
        c.execute('INSERT INTO positions VALUES(?,?,?,?,?,?)',('TEST','no',10,40,0,'2026-09-23'))
    assert '$95.80' in TelegramController(db).handle('/status')
    q=AlertQueue(db)
    pending=q.pending()
    assert len(pending)==1
    assert 'TEST' not in pending[0][1]
    assert '$4.20' in pending[0][1]
    assert AlertQueue(db).pending()==pending
    q.delivered(pending[0][0])
    assert AlertQueue(db).pending()==[]
    with db.transaction() as c:
        c.execute("INSERT INTO health_events(occurred_at,severity,component,code,message,details_json) VALUES('now','error','provider','fetch_failed','untrusted response','{}')")
    assert 'untrusted response' not in q.pending()[0][1]


def test_status_questions_work_without_pricing_or_api_call(tmp_path,monkeypatch):
    a,fake,db=assistant(tmp_path,monkeypatch)
    a.input_rate=a.output_rate=0
    assert 'Current paper equity' in a.answer('How much money do we have?')
    assert 'Fees paid' in a.answer('What are our costs?')
    assert "I don't know" in a.answer('Buy more positions')
    assert not fake.calls


def test_upgrade_baseline_does_not_reannounce_old_trades(tmp_path):
    db=Database(tmp_path/'db'); db.migrate()
    with db.transaction() as c:
        fill(c,'old','yes',1,40,.02)
        c.execute('INSERT INTO control_state VALUES(?,?,?)',('alert_baseline','2026-09-24T00:00:00+00:00','2026-09-24T00:00:00+00:00'))
        fill(c,'new','yes',1,40,.02,stamp='2026-09-24T01:00:00+00:00')
    assert [key for key,_ in AlertQueue(db).pending()]==['fill:fnew']


def test_status_explains_drawdown_block(tmp_path):
    db=Database(tmp_path/'db'); db.migrate()
    with db.transaction() as c:
        c.execute("INSERT INTO costs(incurred_at,category,amount_usd,description) VALUES('2026-09-23','infrastructure',21.09,'VPS')")
    status=TelegramController(db).handle('/status')
    assert 'NEW TRADES BLOCKED: drawdown limit' in status
    assert '$78.91' in status


def test_plain_replies_and_cached_topics_avoid_repeated_api_calls(tmp_path,monkeypatch):
    a,fake,db=assistant(tmp_path,monkeypatch,'summary')
    assert 'not ready to risk your $100' in a.answer('Can we use real money?')
    assert 'LLM tokens' in a.answer('How does the strategy work?')
    assert not fake.calls
    a.answer('Give me the rundown')
    a.answer('Give me the rundown')
    assert len(fake.calls)==1


def test_chat_uses_shared_ai_budget(tmp_path,monkeypatch):
    a,fake,db=assistant(tmp_path,monkeypatch)
    from src.monitoring.costs import CostTracker
    CostTracker(db).record('jev_api',.05,'prior review')
    a.daily_cap=.03
    assert 'budget' in a.answer('Give me the rundown')
    assert not fake.calls
