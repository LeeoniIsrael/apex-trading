"""Alpaca order execution layer.

WARNING: Modifying this file touches real and paper orders.
Run /verify before any changes here.
"""

from __future__ import annotations

import logging

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest, TrailingStopOrderRequest

from src.config import settings

logger = logging.getLogger(__name__)

_client: TradingClient | None = None


def _get_client() -> TradingClient:
    global _client
    if _client is None:
        _client = TradingClient(
            api_key=settings.alpaca_api_key,
            secret_key=settings.alpaca_secret_key,
            paper=settings.alpaca_base_url.startswith("https://paper-api"),
        )
    return _client


def get_portfolio() -> dict:
    client = _get_client()
    account = client.get_account()
    positions = client.get_all_positions()
    return {
        "cash": float(account.cash),
        "equity": float(account.equity),
        "buying_power": float(account.buying_power),
        "positions": [
            {
                "symbol": p.symbol,
                "qty": float(p.qty),
                "market_value": float(p.market_value),
                "unrealized_pnl": float(p.unrealized_pl),
            }
            for p in positions
        ],
    }


def submit_market_order(
    symbol: str,
    qty: float,
    side: str,
    reasoning: str = "",
) -> dict | None:
    """Submit a market order. Returns order dict or None on failure."""
    client = _get_client()
    order_side = OrderSide.BUY if side.upper() == "BUY" else OrderSide.SELL

    try:
        order = client.submit_order(
            MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=order_side,
                time_in_force=TimeInForce.DAY,
            )
        )
        logger.info(
            "Order submitted: %s %s %s qty=%.4f | %s",
            order.id,
            side,
            symbol,
            qty,
            reasoning[:80],
        )
        return {
            "id": str(order.id),
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "status": str(order.status),
        }
    except Exception as exc:
        logger.error("Order failed for %s %s: %s", side, symbol, exc)
        return None


def submit_trailing_stop(
    symbol: str,
    qty: float,
    trail_percent: float | None = None,
) -> dict | None:
    """Attach a trailing stop to a position."""
    client = _get_client()
    trail_pct = trail_percent or settings.apex_trailing_stop_pct * 100

    try:
        order = client.submit_order(
            TrailingStopOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.GTC,
                trail_percent=trail_pct,
            )
        )
        logger.info("Trailing stop set: %s %s trail=%.1f%%", order.id, symbol, trail_pct)
        return {"id": str(order.id), "symbol": symbol, "trail_percent": trail_pct}
    except Exception as exc:
        logger.error("Trailing stop failed for %s: %s", symbol, exc)
        return None
