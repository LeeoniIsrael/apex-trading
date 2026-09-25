from types import SimpleNamespace
from decimal import Decimal
import pytest

from src.execution.portfolio_audit import reconcile_portfolio, pages
from src.storage.database import Database


def fixture(tmp_path, side='yes'):
    db=Database(tmp_path/'live'); db.migrate()
    c=SimpleNamespace(get_orders=lambda **k:{'orders':[]}, get_fills=lambda **k:{'fills':[]},
        get_settlements=lambda **k:{'settlements':[]},get_positions=lambda **k:{'market_positions':[]})
    reconcile_portfolio(db,c,100,100)
    with db.transaction() as conn:
        conn.execute('INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',('LIVE-one','LIVE-one','TEST',side,'buy',40,3,0,'unknown',0,'2026-09-25','2026-09-25'))
    order=dict(order_id='remote',client_order_id='LIVE-one',ticker='TEST',side=side,action='buy',initial_count_fp='3.00',fill_count_fp='2.00',remaining_count_fp='0.00',status='canceled')
    fill=dict(fill_id='fill',order_id='remote',ticker='TEST',side=side,action='buy',count_fp='2.00',yes_price_dollars='.4000' if side=='yes' else '.6000',no_price_dollars='.4000' if side=='no' else '.6000',fee_cost='.0336')
    c.get_orders=lambda **k:{'orders':[order]}
    c.get_fills=lambda **k:{'fills':[fill]}
    c.get_positions=lambda **k:{'market_positions':[{'ticker':'TEST','position_fp':'2.00' if side=='yes' else '-2.00'}]}
    return db,c,order,fill


@pytest.mark.parametrize('side',['yes','no'])
@pytest.mark.parametrize('outcome',['yes','no'])
def test_partial_fill_exact_fee_restart_settlement_and_profit(tmp_path,side,outcome):
    db,c,order,fill=fixture(tmp_path,side)
    a=reconcile_portfolio(db,c,99.1664,100)
    assert a.open_cost==Decimal('.8336')
    assert a.positions['TEST']['contracts']==2
    assert reconcile_portfolio(db,c,99.1664,100)==a
    # Archived fills/orders remain accounted for after restart.
    c.get_orders=lambda **k:{'orders':[]}
    c.get_fills=lambda **k:{'fills':[]}
    assert reconcile_portfolio(Database(db.path),c,99.1664,100)==a
    payout=2 if side==outcome else 0
    c.get_settlements=lambda **k:{'settlements':[dict(ticker='TEST',market_result=outcome,yes_count_fp='2' if side=='yes' else '0',no_count_fp='2' if side=='no' else '0',revenue=payout*100)]}
    c.get_positions=lambda **k:{'market_positions':[]}
    settled=reconcile_portfolio(db,c,Decimal('99.1664')+payout,100)
    assert not settled.positions
    assert settled.realized_pnl==Decimal(payout)-Decimal('.8336')
    assert reconcile_portfolio(db,c,Decimal('99.1664')+payout,100)==settled


@pytest.mark.parametrize('fault',['missing_fill','changed_fill','wrong_position','fee','fraction','overfill','cash','missing_order','resting','wrong_side'])
def test_mismatches_fail_closed(tmp_path,fault):
    db,c,order,fill=fixture(tmp_path)
    if fault=='missing_fill': c.get_fills=lambda **k:{'fills':[]}
    if fault=='changed_fill':
        reconcile_portfolio(db,c,99.1664,100)
        fill['fee_cost']='.01'
    if fault=='wrong_position': c.get_positions=lambda **k:{'market_positions':[]}
    if fault=='fee': fill['fee_cost']='NaN'
    if fault=='fraction': fill['count_fp']='1.50'
    if fault=='overfill': fill['count_fp']='4';order['fill_count_fp']='4'
    if fault=='cash': fill['fee_cost']='.05'
    if fault=='missing_order': c.get_orders=lambda **k:{'orders':[]}
    if fault=='resting': order['remaining_count_fp']='1';order['status']='resting'
    if fault=='wrong_side': fill['side']='no'
    with pytest.raises(RuntimeError): reconcile_portfolio(db,c,99.1664,100)


def test_pagination_collects_all_pages_and_rejects_cycles():
    assert pages(lambda **k:{'items':[2]} if k else {'items':[1],'cursor':'next'},'items')==[1,2]
    with pytest.raises(RuntimeError,match='pagination'):
        pages(lambda **k:{'items':[],'cursor':'loop'},'items')


def test_delayed_remote_order_blocks_then_recovers_without_new_submission(tmp_path):
    db,client,order,fill=fixture(tmp_path)
    fetch=client.get_orders
    client.get_orders=lambda **k:{'orders':[]}
    with pytest.raises(RuntimeError, match='ambiguous submission'):
        reconcile_portfolio(db,client,99.1664,100)
    with db.connect() as conn:
        assert conn.execute('SELECT status FROM orders').fetchone()[0]=='unknown'
        assert conn.execute('SELECT COUNT(*) FROM live_remote_records').fetchone()[0]==0
    client.get_orders=fetch
    result=reconcile_portfolio(Database(db.path),client,99.1664,100)
    assert result.fills==1 and result.orders==1
    assert result.open_cost==Decimal('.8336')
    assert reconcile_portfolio(Database(db.path),client,99.1664,100)==result
