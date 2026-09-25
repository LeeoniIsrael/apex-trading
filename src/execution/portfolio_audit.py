"""Read-only, exact-fee recovery for the bot's whole-contract buy-and-hold orders.

Never infer a missing fill, retry a POST, or adopt an unrelated account position.
Remote raw records persist so archived API history cannot erase the cost basis.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json


def number(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise RuntimeError('invalid remote number') from None
    if not result.is_finite():
        raise RuntimeError('nonfinite remote number')
    return result


def whole(value):
    result = number(value)
    if result != result.to_integral_value():
        raise RuntimeError('fractional execution requires operator reconciliation')
    return int(result)


def pages(fetch, field):
    rows, cursor, seen = [], None, set()
    for _ in range(100):
        data = fetch(**({'cursor': cursor} if cursor else {}))
        if not isinstance(data, dict) or not isinstance(data.get(field), list):
            raise RuntimeError('invalid remote portfolio schema')
        rows.extend(data[field])
        cursor = data.get('cursor')
        if not cursor:
            return rows
        if not isinstance(cursor, str) or cursor in seen:
            raise RuntimeError('remote pagination loop')
        seen.add(cursor)
    raise RuntimeError('remote pagination limit')


@dataclass(frozen=True)
class PortfolioAudit:
    cash: Decimal
    open_cost: Decimal
    realized_pnl: Decimal
    positions: dict
    orders: int
    fills: int


def reconcile_portfolio(database, client, cash, capital_limit):
    orders = pages(client.get_orders, 'orders')
    fills = pages(client.get_fills, 'fills')
    settlements = pages(client.get_settlements, 'settlements')
    remote_positions = pages(client.get_positions, 'market_positions')
    cash = number(cash)
    with database.transaction() as c:
        known = {r['client_order_id']: dict(r) for r in c.execute('SELECT * FROM orders WHERE paper=0')}
        prior = {(r['kind'], r['remote_id']): json.loads(r['payload'])
                 for r in c.execute('SELECT * FROM live_remote_records')}
        for order in orders:
            client_id = order.get('client_order_id')
            if client_id not in known:
                if order.get('status') not in ('canceled','cancelled','executed'):
                    raise RuntimeError('untracked remote order')
                continue
            local = known[client_id]
            side = order.get('outcome_side', order.get('side'))
            if (order.get('ticker') != local['ticker'] or side != local['side']
                or order.get('action') != 'buy'
                or whole(order.get('initial_count_fp')) != local['contracts']):
                raise RuntimeError('remote order differs from durable intent')
            remote_id = order.get('order_id')
            if not remote_id:
                raise RuntimeError('missing remote order id')
            prior['order', remote_id] = order
        links = {key[1]: value for key, value in prior.items() if key[0] == 'order'}
        for fill in fills:
            if fill.get('order_id') not in links:
                continue  # Unrelated history never becomes a bot position.
            remote_id = fill.get('fill_id')
            if not remote_id:
                raise RuntimeError('missing remote fill id')
            key = ('fill', remote_id)
            if key in prior and prior[key] != fill:
                raise RuntimeError('remote fill changed after ingestion')
            prior[key] = fill
        bot_tickers = {r['ticker'] for r in known.values()}
        for settlement in settlements:
            if settlement.get('ticker') in bot_tickers:
                key = ('settlement', settlement['ticker'])
                if key in prior and prior[key] != settlement:
                    raise RuntimeError('remote settlement changed after ingestion')
                prior[key] = settlement
        holdings, costs, by_order, total_spent = {}, {}, {}, Decimal(0)
        for (kind, _), fill in prior.items():
            if kind != 'fill':
                continue
            order = links.get(fill.get('order_id'))
            if order is None or order['client_order_id'] not in known:
                raise RuntimeError('orphan remote fill')
            local = known[order['client_order_id']]
            side = fill.get('outcome_side', fill.get('side'))
            qty = whole(fill.get('count_fp'))
            price = number(fill.get(side+'_price_dollars')) if side in ('yes','no') else Decimal(-1)
            fee = number(fill.get('fee_cost'))
            if (qty <= 0 or not 0 < price < 1 or fee < 0
                or fill.get('action') != 'buy' or side != local['side']
                or fill.get('ticker', fill.get('market_ticker')) != local['ticker']
                or price*100 > local['price_cents']):
                raise RuntimeError('remote fill violates durable intent')
            ticker = local['ticker']
            if ticker in holdings and holdings[ticker]['side'] != side:
                raise RuntimeError('opposing positions require netting reconciliation')
            holdings.setdefault(ticker, {'side':side,'contracts':0})['contracts'] += qty
            cost = qty*price+fee
            costs[ticker] = costs.get(ticker, Decimal(0))+cost
            total_spent += cost
            by_order[order['client_order_id']] = by_order.get(order['client_order_id'],0)+qty
        statuses = {}
        for remote in links.values():
            cid = remote['client_order_id']
            if cid not in known:
                raise RuntimeError('untracked durable remote order')
            filled = whole(remote.get('fill_count_fp'))
            remaining = whole(remote.get('remaining_count_fp'))
            if (filled != by_order.get(cid,0) or filled < 0 or remaining != 0
                or filled > known[cid]['contracts']
                or remote.get('status') not in ('executed','canceled','cancelled')):
                raise RuntimeError('incomplete or resting remote execution')
            statuses[cid] = ('executed' if filled else 'cancelled', filled)
        if any(cid not in statuses for cid in known):
            raise RuntimeError('ambiguous submission requires reconciliation')
        paid, realized = Decimal(0), Decimal(0)
        for (kind, ticker), result in prior.items():
            if kind != 'settlement':
                continue
            position = holdings.get(ticker)
            if position is None or result.get('market_result') not in ('yes','no'):
                raise RuntimeError('unmatched or nonbinary settlement')
            for side in ('yes','no'):
                expected = position['contracts'] if side == position['side'] else 0
                if whole(result.get(side+'_count_fp')) != expected:
                    raise RuntimeError('settlement position mismatch')
            revenue = number(result.get('revenue'))/100
            expected = Decimal(position['contracts'] if result['market_result']==position['side'] else 0)
            if revenue != expected:
                raise RuntimeError('settlement payout mismatch')
            # Entry fees are already in exact fill cost; do not charge twice.
            paid += revenue
            realized += revenue-costs[ticker]
            del holdings[ticker]
        remote = {}
        for position in remote_positions:
            qty = whole(position.get('position_fp',position.get('position',0)))
            if qty:
                ticker = position.get('ticker')
                if not ticker or ticker in remote:
                    raise RuntimeError('invalid remote positions')
                remote[ticker] = qty
        expected = {t:p['contracts']*(1 if p['side']=='yes' else -1) for t,p in holdings.items()}
        if remote != expected:
            raise RuntimeError('remote position does not match audited fills')
        initial = c.execute("SELECT value FROM control_state WHERE key='live_initial_cash'").fetchone()
        if initial is None:
            if known or not 0 <= cash <= number(capital_limit):
                raise RuntimeError('approved capital baseline missing or exceeded')
            initial_cash = cash
        else:
            initial_cash = number(initial[0])
        if abs(initial_cash-total_spent+paid-cash) > Decimal('.0001'):
            raise RuntimeError('account cash mismatch; deposit, withdrawal, fee or fill unresolved')
        # Only publish a completely reconciled snapshot, atomically.
        from datetime import datetime, timezone
        stamp = datetime.now(timezone.utc).isoformat()
        c.execute('INSERT OR IGNORE INTO control_state VALUES(?,?,?)',('live_initial_cash',str(initial_cash),stamp))
        for (kind, remote_id), value in prior.items():
            c.execute('INSERT OR REPLACE INTO live_remote_records VALUES(?,?,?)',(kind,remote_id,json.dumps(value,sort_keys=True)))
        for cid,(status,filled) in statuses.items():
            c.execute('UPDATE orders SET status=?,filled_contracts=?,updated_at=? WHERE client_order_id=?',(status,filled,stamp,cid))
        for ticker,p in holdings.items():
            p['cost'] = costs[ticker]
        result = PortfolioAudit(cash,sum((costs[t] for t in holdings),Decimal(0)),realized,holdings,len(known),sum(k[0]=='fill' for k in prior))
        c.execute('INSERT OR REPLACE INTO control_state VALUES(?,?,?)',('live_audit',json.dumps({'cash':str(cash),'open_cost':str(result.open_cost),'realized_pnl':str(realized),'positions':holdings},default=str),stamp))
        return result
