"""Restart-safe, at-least-once alerts; delivery is recorded only after send succeeds."""
import json
from datetime import datetime, timezone

from src.strategy.settlement import parse_settlement_spec


def describe_market(raw_json, side):
    if not raw_json:
        return 'Unknown city', "I don't know the exact weather rule."
    spec=parse_settlement_spec(json.loads(raw_json))
    city=spec.city or 'Unknown city'
    if not spec.tradeable:
        return city, "I don't know the exact weather rule."
    measure='lowest' if spec.measurement.value=='low' else 'highest'
    low, high=spec.threshold_low,spec.threshold_high
    if low is not None and high is not None:
        interval=f'{low:g}–{high:g}°'
    elif low is not None:
        interval=f'{low:g}° or higher' if spec.inclusive_low else f'above {low:g}°'
    else:
        interval=f'{high:g}° or lower' if spec.inclusive_high else f'below {high:g}°'
    return city, f'Bet: {measure} temperature will {"NOT " if side=="no" else ""}be {interval}'


class AlertQueue:
    def __init__(self,database): self.database=database

    def pending(self):
        with self.database.connect() as c:
            rows=c.execute("SELECT f.*,o.action,m.raw_json FROM fills f JOIN orders o ON o.id=f.order_id "
                           "LEFT JOIN markets m ON m.ticker=f.ticker WHERE o.paper=1 AND NOT EXISTS "
                           "(SELECT 1 FROM notification_deliveries n WHERE n.event_key='fill:'||f.id) ORDER BY f.filled_at LIMIT 20").fetchall()
            settlements=c.execute("SELECT s.*,m.raw_json market_json,SUM(p.realized_pnl_usd) pnl FROM settlements s "
                                  "JOIN positions p ON p.ticker=s.ticker AND p.contracts=0 LEFT JOIN markets m ON m.ticker=s.ticker "
                                  "WHERE s.final=1 AND NOT EXISTS (SELECT 1 FROM notification_deliveries n WHERE n.event_key='settlement:'||s.ticker) "
                                  "GROUP BY s.ticker LIMIT 20").fetchall()
            health=c.execute("SELECT id,severity,component,code FROM health_events WHERE severity IN ('error','critical') "
                             "AND NOT EXISTS (SELECT 1 FROM notification_deliveries n WHERE n.event_key='health:'||health_events.id) ORDER BY id LIMIT 20").fetchall()
        events=[]
        for r in health:
            events.append((f"health:{r['id']}",f"Paper system alert:\n{r['component']}: {r['code']}\nOperator check needed."))
        for r in rows:
            city,bet=describe_market(r['raw_json'],r['side'])
            risk=r['contracts']*r['price_cents']/100+r['fee_usd']
            events.append((f"fill:{r['id']}",f'Paper trade:\n{city}\n{bet}\nRisk: ${risk:.2f}'))
        for r in settlements:
            city,_=describe_market(r['market_json'],'yes')
            pnl=float(r['pnl'])
            events.append((f"settlement:{r['ticker']}",f'Paper result:\n{city}\n{"Won" if pnl>0 else "Lost" if pnl<0 else "Broke even"}\nProfit: {pnl:+.2f} dollars'))
        return events

    def delivered(self,key):
        with self.database.transaction() as c:
            c.execute('INSERT OR IGNORE INTO notification_deliveries VALUES(?,?)',(key,datetime.now(timezone.utc).isoformat()))
