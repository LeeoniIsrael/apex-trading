"""Resolve effective event overrides without assuming the series fee is final."""
from datetime import datetime, timedelta
from decimal import Decimal


def effective_fee(series, event_ticker, changes, now):
    kind, multiplier = series.get('fee_type'), series.get('fee_multiplier')
    active=[]
    for change in changes:
        if change.get('event_ticker') != event_ticker:
            continue
        at=datetime.fromisoformat(str(change['scheduled_ts']).replace('Z','+00:00'))
        if at.tzinfo is None:
            raise ValueError('fee schedule timestamp needs timezone')
        if now < at <= now+timedelta(seconds=60):
            raise ValueError('fee change imminent')
        if at <= now:
            active.append((at,change))
    if active:
        latest=max(at for at,_ in active)
        current=[v for at,v in active if at==latest]
        if len({(v.get('fee_type_override'),v.get('fee_multiplier_override')) for v in current}) != 1:
            raise ValueError('conflicting fee overrides')
        change=current[0]
        if change.get('fee_type_override') is not None:
            kind=change['fee_type_override']
        if change.get('fee_multiplier_override') is not None:
            multiplier=change['fee_multiplier_override']
    rate=Decimal(str(multiplier))
    if kind != 'quadratic' or not rate.is_finite() or rate < 0:
        raise ValueError('unsupported fee schedule')
    return kind, rate
