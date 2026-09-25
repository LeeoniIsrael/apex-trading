from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from src.kalshi.fee_schedule import effective_fee


def test_event_override_effective_time_clear_and_imminent_change():
    now=datetime.now(timezone.utc)
    series={'fee_type':'quadratic','fee_multiplier':1}
    change={'event_ticker':'E','scheduled_ts':(now-timedelta(days=1)).isoformat(),'fee_type_override':'quadratic','fee_multiplier_override':2}
    assert effective_fee(series,'E',[change],now)==('quadratic',Decimal(2))
    assert effective_fee(series,'OTHER',[change],now)==('quadratic',Decimal(1))
    clear={**change,'scheduled_ts':(now-timedelta(hours=1)).isoformat(),'fee_type_override':None,'fee_multiplier_override':None}
    assert effective_fee(series,'E',[change,clear],now)[1]==1
    soon={**change,'scheduled_ts':(now+timedelta(seconds=30)).isoformat()}
    with pytest.raises(ValueError,match='imminent'): effective_fee(series,'E',[soon],now)
    with pytest.raises(ValueError): effective_fee({'fee_type':'unknown','fee_multiplier':1},'E',[],now)
