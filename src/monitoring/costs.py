"""Gross-to-net profitability reporting with infrastructure allocation."""

from __future__ import annotations

from dataclasses import dataclass
from calendar import monthrange
from datetime import datetime, timezone

from src.storage.database import Database


@dataclass(frozen=True, slots=True)
class CostReport:
    gross_trading_pnl_usd: float
    trading_fees_usd: float
    estimated_slippage_usd: float
    infrastructure_usd: float
    ai_api_usd: float
    other_api_usd: float

    @property
    def net_pnl_usd(self) -> float:
        return round(
            self.gross_trading_pnl_usd
            - self.trading_fees_usd
            - self.estimated_slippage_usd
            - self.infrastructure_usd
            - self.ai_api_usd
            - self.other_api_usd,
            2,
        )

    @property
    def expected_monthly_income(self) -> str:
        return "INSUFFICIENT DATA"


class CostTracker:
    def __init__(self, database: Database) -> None:
        self.database = database

    def record(self, category: str, amount_usd: float, description: str,
               external_id: str | None = None) -> None:
        if amount_usd < 0:
            raise ValueError("cost cannot be negative")
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO costs(incurred_at,category,amount_usd,description,external_id) "
                "VALUES(?,?,?,?,?)", (datetime.now(timezone.utc).isoformat(), category,
                                      amount_usd, description, external_id),
            )

    def total(self, category: str | None = None) -> float:
        with self.database.connect() as connection:
            if category:
                row = connection.execute(
                    "SELECT COALESCE(SUM(amount_usd),0) FROM costs WHERE category=?", (category,)
                ).fetchone()
            else:
                row = connection.execute("SELECT COALESCE(SUM(amount_usd),0) FROM costs").fetchone()
        return round(float(row[0]), 2)

    def total_since(self, category: str, since: datetime) -> float:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(amount_usd),0) FROM costs "
                "WHERE category=? AND incurred_at>=?",
                (category, since.astimezone(timezone.utc).isoformat()),
            ).fetchone()
        return float(row[0])

    def can_spend(
        self, category: str, amount_usd: float, *,
        daily_limit: float, monthly_limit: float,
    ) -> bool:
        now = datetime.now(timezone.utc)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = day_start.replace(day=1)
        return (
            self.total_since(category, day_start) + amount_usd <= daily_limit
            and self.total_since(category, month_start) + amount_usd <= monthly_limit
        )

    def can_spend_categories(
        self, categories: tuple[str, ...], amount_usd: float, *,
        daily_limit: float, monthly_limit: float,
    ) -> bool:
        now = datetime.now(timezone.utc)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = day_start.replace(day=1)
        daily = sum(self.total_since(category, day_start) for category in categories)
        monthly = sum(self.total_since(category, month_start) for category in categories)
        return daily + amount_usd <= daily_limit and monthly + amount_usd <= monthly_limit

    def report(self, since: datetime | None = None) -> CostReport:
        """Build a realized report; open-position P&L is intentionally excluded."""
        since_iso = since.astimezone(timezone.utc).isoformat() if since else None
        time_filter = " AND updated_at>=?" if since_iso else ""
        fill_filter = " AND f.filled_at>=?" if since_iso else ""
        params = (since_iso,) if since_iso else ()
        with self.database.connect() as connection:
            realized = float(connection.execute(
                "SELECT COALESCE(SUM(realized_pnl_usd),0) FROM positions "
                "WHERE contracts=0" + time_filter, params,
            ).fetchone()[0])
            # Only fees attached to closed positions belong in realized P&L.
            fees = float(connection.execute(
                "SELECT COALESCE(SUM(f.fee_usd),0) FROM fills f "
                "JOIN positions p ON p.ticker=f.ticker AND p.side=f.side "
                "WHERE p.contracts=0" + fill_filter, params,
            ).fetchone()[0])

        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        monthly_infrastructure = self.total_since("infrastructure", month_start)
        if since is None:
            infrastructure = monthly_infrastructure
        else:
            period_days = max(1.0, (now - since.astimezone(timezone.utc)).total_seconds() / 86400)
            infrastructure = monthly_infrastructure * min(
                period_days, monthrange(now.year, now.month)[1]
            ) / monthrange(now.year, now.month)[1]
        ai = self.total_since("jev_api", since) if since else self.total("jev_api")
        ai += self.total_since("openai_api", since) if since else self.total("openai_api")
        other = self.total_since("other_api", since) if since else self.total("other_api")
        return CostReport(
            gross_trading_pnl_usd=round(realized + fees, 2),
            trading_fees_usd=round(fees, 2),
            # Paper/live fills already carry their actual prices. There is no extra
            # synthetic slippage deduction in a realized report.
            estimated_slippage_usd=0.0,
            infrastructure_usd=round(infrastructure, 2),
            ai_api_usd=round(ai, 4),
            other_api_usd=round(other, 4),
        )
