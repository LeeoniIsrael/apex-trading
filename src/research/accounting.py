"""Replay paper fills once, FIFO, with fees and official settlement cash flows.

Equity is conservative liquidation equity: missing or stale bids are worth zero.
The position cache is audited against the fill ledger, never used as cash truth.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math

from src.kalshi.orderbook import parse_orderbook
from src.storage.database import Database


@dataclass(frozen=True)
class PaperAccount:
    starting_bankroll: float
    realized_pnl: float
    open_cost: float
    fees: float
    operating_costs: float
    cash: float
    equity: float
    after_cost_result: float
    max_drawdown: float
    daily_pnl: float
    discrepancies: tuple[str, ...]
    order_results: dict


def paper_account(database: Database, bankroll: float, *, now: datetime | None = None) -> PaperAccount:
    now = now or datetime.now(timezone.utc)
    with database.connect() as c:
        fills = c.execute("SELECT f.*,o.action FROM fills f JOIN orders o ON o.id=f.order_id "
                          "WHERE o.paper=1 ORDER BY f.filled_at,f.rowid").fetchall()
        settlements = c.execute("SELECT * FROM settlements WHERE final=1").fetchall()
        costs = c.execute("SELECT * FROM costs ORDER BY incurred_at,id").fetchall()
        positions = c.execute("SELECT * FROM positions").fetchall()
        historical = c.execute("SELECT COALESCE(MAX(drawdown),0) FROM equity_history").fetchone()[0]
        books = c.execute("SELECT * FROM orderbook_snapshots ORDER BY captured_at,id").fetchall()
    events = [(f['filled_at'], 0, f) for f in fills]
    events += [(s['settled_at'], 1, s) for s in settlements]
    events += [(c['incurred_at'], 2, c) for c in costs]
    lots = defaultdict(deque)
    cash = peak = bankroll
    realized = fees = operating = drawdown = daily_pnl = 0.0
    results = defaultdict(lambda: {'pnl': 0.0, 'closed_contracts': 0, 'settlement_result': None})
    errors = []
    for stamp, kind, row in sorted(events, key=lambda e: (e[0], e[1])):
        pnl = 0.0
        if kind == 2:
            amount = float(row['amount_usd'])
            cash -= amount
            operating += amount
        elif kind == 0:
            quantity = row['contracts']
            price = float(row['price_cents']) / 100
            fee = float(row['fee_usd'])
            if quantity <= 0 or not all(math.isfinite(v) for v in (price, fee)) or fee < 0:
                errors.append('invalid_fill')
                continue
            fees += fee
            key = (row['ticker'], row['side'])
            if row['action'] == 'buy':
                cash -= quantity * price + fee
                lots[key].append([quantity, price, fee / quantity, row['order_id']])
            else:
                cash += quantity * price - fee
                remaining = quantity
                while remaining and lots[key]:
                    lot = lots[key][0]
                    take = min(remaining, lot[0])
                    profit = take * (price - lot[1] - lot[2] - fee / quantity)
                    pnl += profit
                    results[lot[3]]['pnl'] += profit
                    results[lot[3]]['closed_contracts'] += take
                    lot[0] -= take
                    remaining -= take
                    if not lot[0]:
                        lots[key].popleft()
                if remaining:
                    errors.append('sell_without_entry')
        else:
            if row['yes_outcome'] not in (0, 1):
                errors.append('invalid_settlement')
                continue
            for side in ('yes', 'no'):
                wins = int(row['yes_outcome'] == (1 if side == 'yes' else 0))
                for quantity, price, fee, order_id in lots.pop((row['ticker'], side), []):
                    cash += quantity * wins
                    profit = quantity * (wins - price - fee)
                    pnl += profit
                    results[order_id]['pnl'] += profit
                    results[order_id]['closed_contracts'] += quantity
                    results[order_id]['settlement_result'] = bool(wins)
        realized += pnl
        if stamp[:10] == now.date().isoformat():
            daily_pnl += pnl
        # Cash plus remaining cost, with entry fees expensed immediately.
        cost_equity = cash + sum(q*p for entries in lots.values() for q,p,_,_ in entries)
        peak = max(peak, cost_equity)
        drawdown = max(drawdown, (peak - cost_equity) / peak)
    open_cost = sum(q*p for entries in lots.values() for q,p,_,_ in entries)
    quantities = {key: sum(l[0] for l in entries) for key, entries in lots.items() if entries}
    cached = {(p['ticker'], p['side']): p['contracts'] for p in positions if p['contracts']}
    if cached != quantities:
        errors.append('position_quantity_mismatch')
    if abs(sum(float(p['realized_pnl_usd']) for p in positions) - realized) > .011:
        errors.append('realized_pnl_mismatch')
    latest = {b['ticker']: b for b in books}
    marked = 0.0
    for (ticker, side), quantity in quantities.items():
        row = latest.get(ticker)
        if not row or not 0 <= (now-datetime.fromisoformat(row['captured_at'])).total_seconds() <= 120:
            continue
        book = parse_orderbook(json.loads(row['orderbook_json']))
        for level in book.bids(side):
            take = min(quantity, level.quantity)
            marked += take * level.price_cents / 100
            quantity -= take
            if not quantity:
                break
    equity = cash + marked
    drawdown = max(drawdown, historical, (peak-equity)/peak)
    return PaperAccount(bankroll, round(realized, 4), round(open_cost, 4), round(fees, 4),
                        round(operating, 4), round(cash, 4), round(equity, 4),
                        round(realized-operating, 4), drawdown, daily_pnl,
                        tuple(sorted(set(errors))), dict(results))
