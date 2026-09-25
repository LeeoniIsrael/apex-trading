import json
from datetime import datetime, timezone
from src.monitoring.live_telegram import LiveTelegramController, LiveAlertQueue
from src.storage.database import Database


def test_real_status_is_grounded_read_only_and_emergency_latches(tmp_path):
    db=Database(tmp_path/'live');db.migrate()
    paper=Database(tmp_path/'paper');paper.migrate()
    marker=tmp_path/'marker';marker.write_text('enabled')
    controller=LiveTelegramController(db,paper,marker)
    assert "don't have a verified" in controller.handle('/status')
    with db.transaction() as c:
        c.execute('INSERT INTO control_state VALUES(?,?,?)',('live_audit',json.dumps({'cash':'99.1664','open_cost':'.8336','realized_pnl':'0','positions':{}}),datetime.now(timezone.utc).isoformat()))
    for text in ('How much did we make?', 'Buy $50 of YES', '/resume'):
        answer=controller.answer(text)
        assert 'Real money' in answer and '$99.17' in answer and '$0.83' in answer
        assert 2<=len(answer.splitlines())<=5
    with db.connect() as c: assert c.execute('SELECT COUNT(*) FROM orders').fetchone()[0]==0
    assert 'stopped' in controller.handle('/emergency_stop')
    assert not marker.exists()
    assert 'local operator' in controller.handle('/resume')


def test_real_alerts_are_durable_and_include_exact_fees(tmp_path):
    db=Database(tmp_path/'live');db.migrate()
    paper=Database(tmp_path/'paper');paper.migrate()
    fill=dict(ticker='TEST',side='no',count_fp='2',no_price_dollars='.40',fee_cost='.0336')
    with db.transaction() as c:
        c.execute('INSERT INTO live_remote_records VALUES(?,?,?)',('fill','one',json.dumps(fill)))
    queue=LiveAlertQueue(db,paper)
    pending=queue.pending()
    assert len(pending)==1 and 'Real-money trade' in pending[0][1] and '$0.83' in pending[0][1]
    queue.delivered(pending[0][0])
    assert LiveAlertQueue(db,paper).pending()==[]
