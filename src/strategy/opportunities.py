"""Compare both executable outcomes under the same fee-inclusive dollar budget."""
from decimal import Decimal

from src.kalshi.fees import trading_fee_usd
from src.strategy.edge import calculate_edge


def ranked_opportunities(*, probability_yes, orderbook, budget_usd,
                         fee_rate=Decimal('.07'), min_liquidity=10):
    """Only top-of-book quantities: paper/live limit intent and research use one price.

    Liquidity is actual quoted depth, not the quantity the account can afford.
    No extra budget is granted when a cheap side has a huge modeled return.
    """
    candidates=[]
    for side, probability in (('yes',probability_yes),('no',1-probability_yes)):
        ask=orderbook.best_ask(side)
        if ask is None:
            continue
        depth=sum(level.quantity for level in orderbook.asks(side) if level.price_cents==ask)
        if depth < min_liquidity:
            continue
        quantity=min(depth,int(Decimal(str(budget_usd))*100/ask))
        while quantity>0 and (Decimal(quantity*ask)/100+trading_fee_usd(quantity,ask,rate=fee_rate)>Decimal(str(budget_usd))):
            quantity-=1
        if quantity<=0:
            continue
        edge=calculate_edge(side=side,model_probability=probability,contracts=quantity,
                            orderbook=orderbook,fee_rate=fee_rate)
        candidates.append(edge)
    return sorted(candidates,key=lambda e:(e.net_ev_usd,e.return_on_capital,e.side),reverse=True)
