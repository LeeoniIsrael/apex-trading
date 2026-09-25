"""Inactive-by-default live adapter. Every submission rechecks all hard gates."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from decimal import Decimal

from src.kalshi.fees import trading_fee_usd
from src.research.readiness import database_readiness
from src.storage.database import Database
from src.strategy.settlement import parse_settlement_spec


def authenticated_balance(client) -> float:
    if getattr(client, 'signer', None) is None:
        raise RuntimeError('Kalshi authentication is required')
    payload = client.get_balance()
    value = (float(payload['balance_dollars']) if 'balance_dollars' in payload
             else float(payload['balance']) / 100)
    if not math.isfinite(value) or value < 0:
        raise RuntimeError('invalid authenticated balance')
    return value


class LiveExecutor:
    def __init__(self, settings, client, database: Database):
        if settings.trading_mode != 'live' or not settings.live_enablement_path.is_file():
            raise RuntimeError('live execution is not explicitly enabled')
        if database.path.resolve() == settings.database_path.resolve():
            raise RuntimeError('live and paper databases must be separate')
        self.settings, self.client, self.database = settings, client, database
        self.paper_database = Database(settings.database_path)
        self._gate()
        self.reconcile()

    def _gate(self):
        s = self.settings
        try:
            marker = json.loads(s.live_enablement_path.read_text())
        except (OSError, ValueError):
            raise RuntimeError('invalid live enablement marker') from None
        if (marker.get('confirmation') != 'I_ACCEPT_LIVE_RISK'
                or marker.get('paper_database') != str(s.database_path.resolve())
                or marker.get('live_database') != str(self.database.path.resolve())):
            raise RuntimeError('live marker does not match this account configuration')
        ready, failures, _ = database_readiness(self.paper_database, s.bankroll)
        if not ready:
            raise RuntimeError('live readiness blocked: '+','.join(failures))
        for db in (self.database, self.paper_database):
            with db.connect() as c:
                controls = dict(c.execute('SELECT key,value FROM control_state'))
                if controls.get('emergency_stop') != 'false' or controls.get('paused') != 'false':
                    raise RuntimeError('emergency stop or pause is active')
        balance = authenticated_balance(self.client)
        if balance < s.live_balance_floor_usd:
            raise RuntimeError('balance safety floor')
        return balance

    def reconcile(self):
        orders = self.client.get_orders()
        positions = self.client.get_positions()
        # Pagination is never silently ignored. A later implementation may walk it.
        if orders.get('cursor') or positions.get('cursor'):
            raise RuntimeError('remote pagination requires reconciliation')
        remote_orders = orders.get('orders')
        remote_positions = positions.get('market_positions')
        if not isinstance(remote_orders, list) or not isinstance(remote_positions, list):
            raise RuntimeError('invalid remote portfolio schema')
        with self.database.connect() as c:
            known = {r[0] for r in c.execute('SELECT client_order_id FROM orders WHERE paper=0')}
            if c.execute("SELECT 1 FROM orders WHERE status IN ('unknown','submitting')").fetchone():
                raise RuntimeError('ambiguous submission requires reconciliation')
        if any(o.get('client_order_id') not in known for o in remote_orders
               if o.get('status') not in ('canceled','cancelled','executed')):
            raise RuntimeError('untracked remote orders require reconciliation')
        # Until a remote fill has an audited local cost basis, refuse further orders.
        # Do not create zero-cost placeholder positions or erase existing accounting.
        if any(float(p.get('position_fp', p.get('position', 0))) != 0 for p in remote_positions):
            raise RuntimeError('remote positions require manual cost-basis reconciliation')
        self.remote_orders = remote_orders
        return {'orders':len(remote_orders), 'positions':len(remote_positions)}

    def submit_order(self, *, ticker, side, action, price_cents, contracts, client_order_id):
        if side not in ('yes','no') or action != 'buy':
            raise RuntimeError('only audited buy intents are supported; exits require reconciliation')
        if type(contracts) is not int or contracts <= 0 or type(price_cents) is not int or not 1 <= price_cents <= 99:
            raise ValueError('invalid order')
        if not client_order_id or len(client_order_id)>100:
            raise ValueError('invalid client order id')
        client_id = 'LIVE-'+client_order_id.removeprefix('LIVE-')
        now = datetime.now(timezone.utc)
        with self.database.transaction() as c:
            # Serialize across processes and reserve intent before POST.
            c.execute('BEGIN IMMEDIATE')
            if c.execute('SELECT 1 FROM orders WHERE client_order_id=?',(client_id,)).fetchone():
                raise RuntimeError('duplicate client order blocked')
            balance = self._gate()
            self.reconcile()
            with self.paper_database.connect() as p:
                market = p.execute('SELECT raw_json FROM markets WHERE ticker=?',(ticker,)).fetchone()
                candidate = p.execute('SELECT * FROM research_candidates WHERE ticker=? ORDER BY id DESC LIMIT 1',(ticker,)).fetchone()
            if not market or not candidate:
                raise RuntimeError('missing deterministic evidence')
            raw = json.loads(market[0]); spec = parse_settlement_spec(raw)
            age = (now-datetime.fromisoformat(candidate['captured_at'])).total_seconds()
            if (not spec.tradeable or not spec.observation_window_end or now >= spec.observation_window_end
                or not getattr(spec, 'last_trading_time', None) or now >= spec.last_trading_time
                or not 0 <= age <= 60 or candidate['observation_age']+age > 7200
                or candidate['final_action'] != 'BUY_'+side.upper() or candidate['net_ev_usd'] <= 0
                or price_cents > candidate['price_cents'] or contracts > candidate['contracts']):
                raise RuntimeError('stale, ambiguous, or nonqualifying deterministic intent')
            if raw.get('_fee_type') != 'quadratic':
                raise RuntimeError('unknown fee schedule')
            rate = Decimal('.07') * Decimal(str(raw['_fee_multiplier']))
            if not rate.is_finite() or rate < 0:
                raise RuntimeError('invalid fee schedule')
            value = contracts*price_cents/100 + float(trading_fee_usd(contracts,price_cents,rate=rate))
            s=self.settings
            reserved_rows = c.execute("SELECT ticker,contracts,price_cents FROM orders WHERE paper=0 AND status NOT IN ('cancelled','canceled','rejected')").fetchall()
            # Reserve full submitted amounts (including filled orders) until audited recovery.
            reserved=sum(r['contracts'] for r in reserved_rows)
            cities={}
            with self.paper_database.connect() as p:
                for row in reserved_rows:
                    metadata=p.execute('SELECT raw_json FROM markets WHERE ticker=?',(row['ticker'],)).fetchone()
                    if not metadata: raise RuntimeError('unknown correlated exposure')
                    city=parse_settlement_spec(json.loads(metadata[0])).city
                    if not city: raise RuntimeError('unknown correlated exposure')
                    cities[city]=cities.get(city,0)+row['contracts']
            daily=c.execute('SELECT COUNT(*) FROM orders WHERE paper=0 AND created_at LIKE ?',(now.date().isoformat()+'%',)).fetchone()[0]
            states=dict(c.execute("SELECT key,value FROM control_state WHERE key LIKE 'live_%'"))
            peak=max(float(states.get('live_peak',balance)),balance)
            daily_key='live_day_'+now.date().isoformat()
            day_start=float(states.get(daily_key,balance))
            for key,val in [('live_peak',peak),(daily_key,day_start)]:
                c.execute('INSERT INTO control_state VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at',(key,str(val),now.isoformat()))
            if (value > s.live_max_order_usd or reserved+value > s.live_max_exposure_usd
                or cities.get(spec.city,0)+value > s.live_max_city_exposure_usd
                or len({r['ticker'] for r in reserved_rows}) >= s.live_max_open_positions
                or daily >= s.live_max_daily_orders or day_start-balance >= s.live_max_daily_loss_usd
                or (peak-balance)/peak >= s.live_max_drawdown
                or balance-reserved-value < s.live_balance_floor_usd):
                raise RuntimeError('live risk limit')
            c.execute('INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                      (client_id,client_id,ticker,side,action,price_cents,contracts,0,'submitting',0,now.isoformat(),now.isoformat()))
        try:
            # Recheck stop after reservation, immediately before external action.
            self._gate()
            result=self.client.create_order(ticker=ticker,side=side,action=action,price_cents=price_cents,
                                            contracts=contracts,client_order_id=client_id)
        except Exception:
            with self.database.transaction() as c:
                c.execute("UPDATE orders SET status='unknown' WHERE client_order_id=?",(client_id,))
            raise RuntimeError('live submission uncertain; stop and reconcile') from None
        remote=result.get('order',result)
        with self.database.transaction() as c:
            c.execute('UPDATE orders SET status=?,filled_contracts=?,updated_at=? WHERE client_order_id=?',
                      (remote.get('status','unknown'),int(remote.get('fill_count',0)),now.isoformat(),client_id))
        return result

    def submit_buy(self, **kwargs):
        return self.submit_order(action='buy',**kwargs)
