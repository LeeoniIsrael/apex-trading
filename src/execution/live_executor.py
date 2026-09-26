"""Inactive-by-default live adapter. Every submission rechecks all hard gates."""
from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from decimal import Decimal

from src.kalshi.fees import trading_fee_usd
from src.execution.portfolio_audit import reconcile_portfolio, pages, PendingRemoteOrder
from src.kalshi.fee_schedule import effective_fee
from src.research.experiments import MODEL_VERSION
from src.research.readiness import database_readiness, launch_failures
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


def validate_intent_time(spec, candidate, now):
    """Recheck wall-clock expiry after potentially slow remote preflight calls."""
    captured = datetime.fromisoformat(candidate['captured_at'])
    age = (now-captured).total_seconds()
    observation_age = float(candidate['observation_age'])
    if (not spec.observation_window_end or now >= spec.observation_window_end
        or not getattr(spec, 'last_trading_time', None) or now >= spec.last_trading_time
        or not math.isfinite(observation_age) or observation_age < 0
        or not 0 <= age <= 60 or observation_age+age > 7200):
        raise RuntimeError('stale or expired deterministic intent')


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
        if (marker.get('validation_profile', 'validated') != s.live_validation_profile
            or (s.live_validation_profile == 'experimental_100'
                and marker.get('unvalidated_strategy_acknowledged') is not True)):
            raise RuntimeError('live marker does not acknowledge this validation profile')
        ready, failures, _ = database_readiness(self.paper_database, s.bankroll)
        if s.live_validation_profile == 'experimental_100':
            failures = launch_failures(failures, s.live_validation_profile)
            ready = not failures
        if not ready:
            raise RuntimeError('live readiness blocked: '+','.join(failures))
        for db in (self.database, self.paper_database):
            with db.connect() as c:
                controls = dict(c.execute('SELECT key,value FROM control_state'))
                if controls.get('emergency_stop') != 'false' or controls.get('paused') != 'false':
                    raise RuntimeError('emergency stop or pause is active')
        balance = authenticated_balance(self.client)
        with self.database.connect() as c:
            initial = c.execute("SELECT value FROM control_state WHERE key='live_initial_cash'").fetchone()
        if initial is None and balance > s.live_capital_limit_usd:
            raise RuntimeError('funded balance exceeds approved capital')
        if balance < s.live_balance_floor_usd:
            raise RuntimeError('balance safety floor')
        return balance

    def reconcile(self):
        # Order-history visibility can lag a successful POST. Retry only reads;
        # every attempt must pass the complete audit before another order is allowed.
        for attempt in range(3):
            try:
                self.audit = reconcile_portfolio(self.database, self.client,
                    authenticated_balance(self.client), self.settings.live_capital_limit_usd)
                return self.audit
            except PendingRemoteOrder:
                if attempt == 2:
                    raise
                time.sleep(1)


    def submit_order(self, *, ticker, side, action, price_cents, contracts, client_order_id):
        if side not in ('yes','no') or action != 'buy':
            raise RuntimeError('only audited buy intents are supported; exits require reconciliation')
        if type(contracts) is not int or contracts <= 0 or type(price_cents) is not int or not 1 <= price_cents <= 99:
            raise ValueError('invalid order')
        if not client_order_id or len(client_order_id)>100:
            raise ValueError('invalid client order id')
        client_id = 'LIVE-'+client_order_id.removeprefix('LIVE-')
        now = datetime.now(timezone.utc)
        with self.database.connect() as c:
            if c.execute('SELECT 1 FROM orders WHERE client_order_id=?',(client_id,)).fetchone():
                raise RuntimeError('duplicate client order blocked')
        audit = self.reconcile()
        with self.database.transaction() as c:
            # Serialize across processes and reserve intent before POST.
            c.execute('BEGIN IMMEDIATE')
            if c.execute('SELECT 1 FROM orders WHERE client_order_id=?',(client_id,)).fetchone():
                raise RuntimeError('duplicate client order blocked')
            balance = self._gate()
            if c.execute('SELECT COUNT(*) FROM orders WHERE paper=0').fetchone()[0] != audit.orders:
                raise RuntimeError('portfolio changed during reconciliation')
            with self.paper_database.connect() as p:
                market = p.execute('SELECT raw_json FROM markets WHERE ticker=?',(ticker,)).fetchone()
                candidate = p.execute('SELECT * FROM research_candidates WHERE ticker=? ORDER BY id DESC LIMIT 1',(ticker,)).fetchone()
            if not market or not candidate:
                raise RuntimeError('missing deterministic evidence')
            raw = json.loads(market[0]); spec = parse_settlement_spec(raw)
            validate_intent_time(spec, candidate, datetime.now(timezone.utc))
            if (not spec.tradeable
                or candidate['model_version'] != MODEL_VERSION
                or candidate['final_action'] != 'BUY_'+side.upper() or candidate['net_ev_usd'] <= 0
                or price_cents > candidate['price_cents'] or contracts > candidate['contracts']):
                raise RuntimeError('stale, ambiguous, or nonqualifying deterministic intent')
            if raw.get('_fee_type') != 'quadratic':
                raise RuntimeError('unknown fee schedule')
            event = raw.get('event_ticker')
            series_ticker = raw.get('series_ticker')
            if not event or not series_ticker:
                raise RuntimeError('missing fee identifiers')
            series = self.client.get_series(series_ticker).get('series', {})
            changes = pages(lambda **kw:self.client.get_event_fee_changes(event_ticker=event, **kw), 'event_fee_changes')
            _, multiplier = effective_fee(series, event, changes, datetime.now(timezone.utc))
            rate = Decimal('.07') * multiplier
            if not rate.is_finite() or rate < 0:
                raise RuntimeError('invalid fee schedule')
            fee = float(trading_fee_usd(contracts,price_cents,rate=rate))
            value = contracts*price_cents/100 + fee
            if candidate['probability'] - price_cents/100 - fee/contracts < self.settings.min_net_edge:
                raise RuntimeError('fee-adjusted edge no longer qualifies')
            s=self.settings
            reserved = float(audit.open_cost)
            cities = {}
            with self.paper_database.connect() as p:
                for held_ticker, position in audit.positions.items():
                    metadata = p.execute('SELECT raw_json FROM markets WHERE ticker=?',(held_ticker,)).fetchone()
                    if not metadata: raise RuntimeError('unknown correlated exposure')
                    city = parse_settlement_spec(json.loads(metadata[0])).city
                    if not city: raise RuntimeError('unknown correlated exposure')
                    cities[city] = cities.get(city, 0) + float(position['cost'])
            if ticker in audit.positions:
                raise RuntimeError('one audited position per market')
            daily=c.execute('SELECT COUNT(*) FROM orders WHERE paper=0 AND created_at LIKE ?',(now.date().isoformat()+'%',)).fetchone()[0]
            states=dict(c.execute("SELECT key,value FROM control_state WHERE key LIKE 'live_%'"))
            peak=max(float(states.get('live_peak',balance)),balance)
            daily_key='live_day_'+now.date().isoformat()
            day_start=float(states.get(daily_key,balance))
            for key,val in [('live_peak',peak),(daily_key,day_start)]:
                c.execute('INSERT INTO control_state VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at',(key,str(val),now.isoformat()))
            if (value > s.live_max_order_usd or reserved+value > s.live_max_exposure_usd
                or cities.get(spec.city,0)+value > s.live_max_city_exposure_usd
                or len(audit.positions) >= s.live_max_open_positions
                or daily >= s.live_max_daily_orders or day_start-balance+value > s.live_max_daily_loss_usd
                or (peak-balance+value)/peak > s.live_max_drawdown
                or reserved+value > s.live_capital_limit_usd
                or balance-value < s.live_balance_floor_usd):
                raise RuntimeError('live risk limit')
            c.execute('INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                      (client_id,client_id,ticker,side,action,price_cents,contracts,0,'submitting',0,now.isoformat(),now.isoformat()))
        try:
            # Recheck stop after reservation, immediately before external action.
            self._gate()
            validate_intent_time(spec, candidate, datetime.now(timezone.utc))
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
