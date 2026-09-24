"""Explicitly gated live order adapter with startup reconciliation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.kalshi.client import KalshiClientV2
from src.storage.database import Database
from src.weather_config import WeatherSettings


class LiveExecutor:
    def __init__(self, settings: WeatherSettings, client: KalshiClientV2,
                 database: Database) -> None:
        if settings.trading_mode != "live" or not settings.live_enablement_path.is_file():
            raise RuntimeError("live execution is not explicitly enabled")
        self.settings = settings
        self.client = client
        self.database = database

    def reconcile(self) -> dict[str, int]:
        """Make remote Kalshi state authoritative after startup/restart."""
        remote_orders = self.client.get_orders().get("orders", [])
        remote_positions = self.client.get_positions().get("market_positions", [])
        now = datetime.now(timezone.utc).isoformat()
        untracked_positions: list[str] = []
        with self.database.transaction() as connection:
            for order in remote_orders:
                client_id = str(order.get("client_order_id") or "")
                if not client_id:
                    continue
                connection.execute(
                    "UPDATE orders SET status=?,filled_contracts=?,updated_at=? "
                    "WHERE client_order_id=?",
                    (str(order.get("status") or "unknown"),
                     int(order.get("fill_count") or order.get("filled_count") or 0),
                     now, client_id),
                )
            for position in remote_positions:
                ticker = str(position.get("ticker") or "")
                quantity = int(position.get("position") or 0)
                if not ticker or quantity == 0:
                    continue
                side = "yes" if quantity > 0 else "no"
                local = connection.execute(
                    "SELECT 1 FROM positions WHERE ticker=? AND side=?", (ticker, side)
                ).fetchone()
                if local is None:
                    # Preserve the remote exposure, but never fabricate a cost basis.
                    untracked_positions.append(ticker)
                connection.execute(
                    "INSERT INTO positions(ticker,side,contracts,average_price_cents," 
                    "realized_pnl_usd,updated_at) VALUES(?,?,?,?,?,?) "
                    "ON CONFLICT(ticker,side) DO UPDATE SET contracts=excluded.contracts," 
                    "realized_pnl_usd=excluded.realized_pnl_usd,updated_at=excluded.updated_at",
                    (ticker, side, abs(quantity), 0.0,
                     float(position.get("realized_pnl_dollars") or 0), now),
                )
        if untracked_positions:
            raise RuntimeError(
                "untracked remote positions require manual cost-basis reconciliation: "
                + ", ".join(sorted(set(untracked_positions)))
            )
        return {"orders": len(remote_orders), "positions": len(remote_positions)}

    def submit_order(
        self, *, ticker: str, side: str, action: str, price_cents: int,
        contracts: int, client_order_id: str,
    ) -> dict[str, Any]:
        """Submit exactly one logical order using Kalshi client-order idempotency."""
        now = datetime.now(timezone.utc).isoformat()
        local_id = f"LIVE-{client_order_id}"
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT status FROM orders WHERE client_order_id=?", (client_order_id,)
            ).fetchone()
            if existing and existing[0] not in {"failed", "unknown"}:
                raise RuntimeError(f"duplicate client order blocked: {client_order_id}")
            connection.execute(
                "INSERT INTO orders(id,client_order_id,ticker,side,action,price_cents,contracts," 
                "filled_contracts,status,paper,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(client_order_id) DO UPDATE SET status='submitting',updated_at=excluded.updated_at",
                (local_id, client_order_id, ticker, side, action, price_cents,
                 contracts, 0, "submitting", 0, now, now),
            )
        try:
            result = self.client.create_order(
                ticker=ticker, side=side, action=action, price_cents=price_cents,
                contracts=contracts, client_order_id=client_order_id,
            )
        except Exception:
            # Network ambiguity is `unknown`, never safe to blindly submit a new ID.
            with self.database.transaction() as connection:
                connection.execute(
                    "UPDATE orders SET status='unknown',updated_at=? WHERE client_order_id=?",
                    (datetime.now(timezone.utc).isoformat(), client_order_id),
                )
            raise
        remote = result.get("order", result)
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE orders SET status=?,filled_contracts=?,updated_at=? WHERE client_order_id=?",
                (str(remote.get("status") or "submitted"),
                 int(remote.get("fill_count") or 0),
                 datetime.now(timezone.utc).isoformat(), client_order_id),
            )
        return result

    def submit_buy(self, **kwargs: Any) -> dict[str, Any]:
        return self.submit_order(action="buy", **kwargs)
