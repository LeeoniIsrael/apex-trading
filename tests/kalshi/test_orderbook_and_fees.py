from decimal import Decimal

import pytest

from src.kalshi.fees import maker_fee_usd, trading_fee_usd
from src.kalshi.orderbook import parse_orderbook
from src.strategy.edge import calculate_edge


def _book():
    return parse_orderbook({"orderbook_fp": {
        "yes_dollars": [["0.40", "10"], ["0.38", "30"]],
        "no_dollars": [["0.55", "5"], ["0.50", "20"]],
    }})


def test_bid_only_book_derives_executable_asks():
    book = _book()
    assert book.best_bid("yes") == 40
    assert book.best_ask("yes") == 45  # complement of best NO bid at 55
    assert book.best_ask("no") == 60   # complement of best YES bid at 40
    assert book.spread_cents("yes") == 5


def test_vwap_walks_actual_ask_depth():
    average, filled = _book().executable_buy("yes", 10)
    assert filled == 10
    assert average == 47.5  # 5 @45, 5 @50


def test_general_fee_formula_rounds_up_to_cent():
    assert trading_fee_usd(1, 50) == Decimal("0.02")
    assert trading_fee_usd(100, 50) == Decimal("1.75")
    assert maker_fee_usd(100, 50) == Decimal("0.00")


def test_edge_uses_executable_vwap_fees_and_depth():
    edge = calculate_edge(side="yes", model_probability=0.70, contracts=10, orderbook=_book())
    assert edge.executable_price_cents == 47.5
    assert edge.fillable_contracts == 10
    assert edge.fee_usd > 0
    assert edge.net_ev_usd > 0
    assert edge.positive


def test_no_ask_liquidity_is_not_executable():
    book = parse_orderbook({"orderbook_fp": {"yes_dollars": [], "no_dollars": []}})
    with pytest.raises(ValueError, match="no executable"):
        calculate_edge(side="yes", model_probability=.8, contracts=1, orderbook=book)

