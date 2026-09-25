from decimal import Decimal
import pytest
from src.kalshi.orderbook import parse_orderbook
from src.strategy.opportunities import ranked_opportunities


def test_likely_side_is_not_always_best_value():
    book=parse_orderbook({'yes':[[10,100]],'no':[[70,100]]})
    choices=ranked_opportunities(probability_yes=.4,orderbook=book,budget_usd=2)
    assert choices[0].side=='yes'  # 40% chance at 30c beats 60% at 90c.
    assert choices[0].net_ev_usd>0


def test_fees_fit_inside_budget_and_quantity_does_not_mean_liquidity():
    book=parse_orderbook({'yes':[[40,100]],'no':[[50,100]]})
    edges=ranked_opportunities(probability_yes=.9,orderbook=book,budget_usd=2,min_liquidity=10)
    assert edges and edges[0].contracts==3
    for e in edges:
        assert e.contracts*e.executable_price_cents/100+e.fee_usd<=2


def test_thin_book_and_zero_budget_are_rejected():
    book=parse_orderbook({'yes':[[40,2]],'no':[[50,2]]})
    assert not ranked_opportunities(probability_yes=.9,orderbook=book,budget_usd=2)
    assert not ranked_opportunities(probability_yes=.9,orderbook=book,budget_usd=0,min_liquidity=1)


def test_research_signal_cannot_override_portfolio_stop():
    from src.strategy.decision_engine import Decision, DecisionAction, apply_portfolio_blocks
    signal=Decision(DecisionAction.BUY_YES,('positive_net_ev',))
    actual=apply_portfolio_blocks(signal,('drawdown_limit',))
    assert signal.action==DecisionAction.BUY_YES
    assert actual.action==DecisionAction.SKIP and 'drawdown_limit' in actual.reason_codes
