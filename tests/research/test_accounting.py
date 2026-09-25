from datetime import datetime, timezone

import pytest

from src.research.accounting import paper_account
from src.storage.database import Database


def fill(c, oid, side, quantity, price, fee, action='buy', stamp='2026-09-23T12:00:00+00:00'):
    c.execute('INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
              (oid, oid, 'TEST', side, action, price, quantity+2, quantity, 'partially_filled', 1, stamp, stamp))
    c.execute('INSERT INTO fills VALUES(?,?,?,?,?,?,?,?)',
              ('f'+oid, oid, 'TEST', side, quantity, price, fee, stamp))


@pytest.mark.parametrize('side,outcome,expected', [('yes',1,5.8),('yes',0,-4.2),('no',0,5.8),('no',1,-4.2)])
def test_settlement_fifo_partial_orders_and_fees(tmp_path, side, outcome, expected):
    db=Database(tmp_path/'db'); db.migrate(); db.migrate()
    with db.transaction() as c:
        fill(c,'one',side,4,25,.08)
        fill(c,'two',side,6,50,.12)
        c.execute('INSERT INTO settlements VALUES(?,?,?,?,?,?,?)', ('TEST',95,outcome,'twc',1,'2026-09-24T12:00:00+00:00','{}'))
        c.execute('INSERT INTO positions VALUES(?,?,?,?,?,?)', ('TEST',side,0,40,expected,'2026-09-24T12:00:00+00:00'))
        c.execute('INSERT INTO costs(incurred_at,category,amount_usd,description) VALUES(?,?,?,?)',('2026-09-23','infrastructure',2,'test'))
    a=paper_account(db,100)
    assert a.realized_pnl == pytest.approx(expected)
    assert a.equity == pytest.approx(100+expected-2)
    assert a.fees == .2
    assert not a.discrepancies
    assert a.order_results['one']['closed_contracts']==4
    assert paper_account(db,100)==a


def test_open_equity_and_missing_bid_fail_conservatively(tmp_path):
    db=Database(tmp_path/'db'); db.migrate()
    with db.transaction() as c:
        fill(c,'one','yes',10,40,.2)
        c.execute('INSERT INTO positions VALUES(?,?,?,?,?,?)', ('TEST','yes',10,40,0,'2026-09-23'))
    a=paper_account(db,100)
    assert a.cash==95.8 and a.open_cost==4 and a.equity==95.8
    assert a.realized_pnl==0
    assert a.max_drawdown==pytest.approx(.042)


def test_partial_exit_then_reentry_does_not_charge_fees_twice(tmp_path):
    db=Database(tmp_path/'db'); db.migrate()
    with db.transaction() as c:
        fill(c,'one','yes',10,40,.2)
        fill(c,'exit','yes',5,60,.1,'sell','2026-09-23T13:00:00+00:00')
        fill(c,'two','yes',2,50,.04,stamp='2026-09-23T14:00:00+00:00')
        c.execute('INSERT INTO settlements VALUES(?,?,?,?,?,?,?)', ('TEST',95,1,'twc',1,'2026-09-24T12:00:00+00:00','{}'))
    a=paper_account(db,100)
    assert a.realized_pnl==pytest.approx(4.66)
    assert 'realized_pnl_mismatch' in a.discrepancies
