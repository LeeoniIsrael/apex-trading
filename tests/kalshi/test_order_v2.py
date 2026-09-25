import pytest
from src.kalshi.client import KalshiClientV2, KalshiAPIError


@pytest.mark.parametrize('side,action,book_side,price',[
    ('yes','buy','bid','0.3000'),('no','buy','ask','0.7000'),
    ('yes','sell','ask','0.3000'),('no','sell','bid','0.7000')])
def test_v2_uses_yes_leg_price_and_ioc(monkeypatch,side,action,book_side,price):
    calls=[]
    def request(self,method,path,**kwargs):
        calls.append((method,path,kwargs))
        return {'order_id':'remote','fill_count':'2.00','remaining_count':'0.00'}
    monkeypatch.setattr(KalshiClientV2,'request',request)
    result=KalshiClientV2().create_order(ticker='TEST',side=side,action=action,price_cents=30,contracts=2,client_order_id='LIVE-test')
    method,path,kwargs=calls[0]
    assert path=='/portfolio/events/orders' and method=='POST'
    body=kwargs['json_body']
    assert body['side']==book_side and body['price']==price
    assert body['time_in_force']=='immediate_or_cancel'
    assert body['cancel_order_on_pause'] is True
    assert result['order']['fill_count']==2


def test_fractional_response_requires_reconciliation(monkeypatch):
    monkeypatch.setattr(KalshiClientV2,'request',lambda *a,**k:{'fill_count':'0.50','remaining_count':'0.00'})
    with pytest.raises(KalshiAPIError):
        KalshiClientV2().create_order(ticker='TEST',side='yes',action='buy',price_cents=30,contracts=2)
