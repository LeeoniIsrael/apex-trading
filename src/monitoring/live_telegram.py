"""Real-money reporting reads only audited state and cannot originate an order."""
import json
from datetime import datetime, timezone
from decimal import Decimal

from src.monitoring.alerts import describe_market
from src.research.experiments import MODEL_VERSION


class LiveTelegramController:
    def __init__(self, database, paper_database, marker_path):
        self.database, self.paper_database, self.marker_path = database, paper_database, marker_path
        self.bankroll = 100.0

    def handle(self, text):
        command = text.strip().split()[0].lower() if text.strip() else '/status'
        if command in ('/pause','/emergency_stop'):
            with self.database.transaction() as c:
                for key in (('paused','emergency_stop') if command=='/emergency_stop' else ('paused',)):
                    c.execute('INSERT OR REPLACE INTO control_state VALUES(?,?,?)',(key,'true',datetime.now(timezone.utc).isoformat()))
            if command=='/emergency_stop':
                self.marker_path.unlink(missing_ok=True)
            return '• Real-money trading is stopped.\n• Existing bets still carry risk.\n• Recovery requires a local operator check.'
        if command=='/resume':
            return '• Real money is involved.\n• Resuming requires a local operator check; chat cannot enable trading.'
        with self.database.connect() as c:
            row = c.execute("SELECT value,updated_at FROM control_state WHERE key='live_audit'").fetchone()
            controls = dict(c.execute('SELECT key,value FROM control_state'))
        if not row:
            return "• This is the real-money account.\n• I don't have a verified balance yet.\n• Trading must stay stopped until the account is checked."
        data = json.loads(row['value'])
        fresh = 0 <= (datetime.now(timezone.utc)-datetime.fromisoformat(row['updated_at'])).total_seconds() <= 120
        blocked = (not fresh or not self.marker_path.exists()
                   or controls.get('paused')!='false' or controls.get('emergency_stop')!='false')
        pnl = Decimal(data['realized_pnl'])
        pnl_text = f'profit ${pnl:.2f}' if pnl >= 0 else f'loss ${-pnl:.2f}'
        return (f"• Real money. {'New trades stopped or account check needed' if blocked else 'Account checked; every bet still needs all safety checks'}.\n"
                f"• {'Last verified' if not fresh else 'Verified'} cash: ${Decimal(data['cash']):.2f}. Open bet cost including fees: ${Decimal(data['open_cost']):.2f}.\n"
                f"• Finished bets: {pnl_text} after trading fees; server and AI costs are separate.\n"
                f"• Model: {MODEL_VERSION}. Trading: {'stopped' if blocked else 'eligible for checked orders'}.\n"
                '• YES means the outcome happens. NO means it does not. Chat cannot place a bet.')

    def answer(self, text):
        # Conversation remains read-only, including slash-like natural-language input.
        return self.handle('/status')


class LiveAlertQueue:
    def __init__(self, database, paper_database):
        self.database, self.paper_database = database, paper_database

    def pending(self):
        with self.database.connect() as c:
            records=[(r['kind'],r['remote_id'],json.loads(r['payload'])) for r in c.execute('SELECT * FROM live_remote_records')]
            delivered={r[0] for r in c.execute('SELECT event_key FROM notification_deliveries')}
            health=c.execute("SELECT id,component,code FROM health_events WHERE severity IN ('error','critical') ORDER BY id").fetchall()
        events=[]
        for r in health:
            key=f"live-health:{r['id']}"
            if key not in delivered:
                events.append((key,f"Real-money system alert:\n{r['component']}: {r['code']}\nNew orders blocked until recovery."))
        for kind,rid,value in records:
            if kind not in ('fill','settlement'):
                continue
            key=f'live-{kind}:{rid}'
            if key in delivered:
                continue
            ticker=value['ticker']
            with self.paper_database.connect() as p:
                market=p.execute('SELECT raw_json FROM markets WHERE ticker=?',(ticker,)).fetchone()
            side=value.get('outcome_side',value.get('side','yes'))
            city,bet=describe_market(market[0] if market else None,side)
            if kind=='fill':
                cost=Decimal(value['count_fp'])*Decimal(value[side+'_price_dollars'])+Decimal(value['fee_cost'])
                text=f'Real-money trade:\n{city}\n{bet}\nRisk including fees: ${cost:.2f}'
            else:
                spent=sum((Decimal(f['count_fp'])*Decimal(f[f.get('outcome_side',f.get('side'))+'_price_dollars'])+Decimal(f['fee_cost']) for k,_,f in records if k=='fill' and f.get('ticker')==ticker),Decimal(0))
                pnl=Decimal(value['revenue'])/100-spent
                text=f'Real-money result:\n{city}\n{"Won" if pnl>0 else "Lost" if pnl<0 else "Broke even"}\nProfit after trading fees: ${pnl:+.2f}'
            events.append((key,text))
        return events[:20]

    def delivered(self, key):
        with self.database.transaction() as c:
            c.execute('INSERT OR IGNORE INTO notification_deliveries VALUES(?,?)',(key,datetime.now(timezone.utc).isoformat()))
