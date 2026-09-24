"""Low-noise Little Lio Trader Telegram control surface."""

from __future__ import annotations

import os
from datetime import datetime, time, timedelta, timezone

from src.monitoring.costs import CostTracker
from src.research.calibration import calibration_metrics
from src.storage.database import Database


class TelegramController:
    def __init__(self, database: Database, bankroll: float = 100.0) -> None:
        self.database = database
        self.bankroll = bankroll

    def _set_control(self, key: str, value: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO control_state(key,value,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                (key, value, datetime.now(timezone.utc).isoformat()),
            )

    def handle(self, command: str) -> str:
        command = command.strip().split()[0].lower()
        with self.database.connect() as connection:
            if command == "/status":
                controls = dict(connection.execute(
                    "SELECT key,value FROM control_state"
                ).fetchall())
                exposure = float(connection.execute(
                    "SELECT COALESCE(SUM(contracts*average_price_cents/100.0),0) "
                    "FROM positions WHERE contracts>0"
                ).fetchone()[0])
                state = ("EMERGENCY" if controls.get("emergency_stop") == "true" else
                         "PAUSED" if controls.get("paused") == "true" else "RUNNING")
                return (f"APEX PAPER | {state} | bankroll ${self.bankroll:.2f} | "
                        f"open exposure ${exposure:.2f}")
            if command == "/today":
                today = datetime.now(timezone.utc).date().isoformat()
                orders = connection.execute(
                    "SELECT COUNT(*) FROM orders WHERE created_at LIKE ?", (f"{today}%",)
                ).fetchone()[0]
                outcomes = connection.execute(
                    "SELECT SUM(realized_pnl_usd>0),SUM(realized_pnl_usd<0) FROM positions "
                    "WHERE contracts=0 AND updated_at LIKE ?", (f"{today}%",)
                ).fetchone()
                exposure = float(connection.execute(
                    "SELECT COALESCE(SUM(contracts*average_price_cents/100.0),0) "
                    "FROM positions WHERE contracts>0"
                ).fetchone()[0])
                largest = float(connection.execute(
                    "SELECT COALESCE(MAX(contracts*average_price_cents/100.0),0) "
                    "FROM positions WHERE contracts>0"
                ).fetchone()[0])
                prediction_rows = connection.execute(
                    "SELECT probability,eventual_outcome FROM model_predictions "
                    "WHERE eventual_outcome IN (0,1)"
                ).fetchall()
                errors = connection.execute(
                    "SELECT COUNT(*) FROM health_events WHERE severity IN ('error','critical') "
                    "AND occurred_at LIKE ?", (f"{today}%",)
                ).fetchone()[0]
                metrics = calibration_metrics(
                    [(float(r[0]), int(r[1])) for r in prediction_rows]
                )
                calibration = ("INSUFFICIENT DATA" if metrics.samples < 30
                               else f"Brier {metrics.brier_score:.3f} n={metrics.samples}")
                report = CostTracker(self.database).report(
                    datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
                )
                return (
                    f"DAILY | bankroll ${self.bankroll:.2f} | gross ${report.gross_trading_pnl_usd:.2f} "
                    f"| fees ${report.trading_fees_usd:.2f} | infra/API "
                    f"${report.infrastructure_usd + report.ai_api_usd + report.other_api_usd:.2f} "
                    f"| net ${report.net_pnl_usd:.2f} | trades {orders} | "
                    f"W/L {int(outcomes[0] or 0)}/{int(outcomes[1] or 0)} | exposure ${exposure:.2f} "
                    f"| largest ${largest:.2f} | calibration {calibration} | "
                    f"health {'OK' if not errors else f'{errors} errors'}"
                )
            if command == "/positions":
                rows = connection.execute(
                    "SELECT ticker,side,contracts,average_price_cents FROM positions WHERE contracts != 0"
                ).fetchall()
                return "No open paper positions." if not rows else "\n".join(
                    f"{r['ticker']} {r['side'].upper()} x{r['contracts']} @ {r['average_price_cents']:.1f}c"
                    for r in rows[:20]
                )
            if command == "/weather":
                rows = connection.execute(
                    "SELECT station_id,MAX(observed_at_utc) AS latest FROM weather_observations "
                    "GROUP BY station_id ORDER BY station_id"
                ).fetchall()
                return "No weather observations yet." if not rows else "\n".join(
                    f"{r['station_id']}: {r['latest']}" for r in rows
                )
            if command == "/health":
                errors = connection.execute(
                    "SELECT COUNT(*) FROM health_events WHERE severity IN ('error','critical')"
                ).fetchone()[0]
                return f"Database: {self.database.integrity_check()} | recorded errors: {errors}"
            if command == "/model":
                count = connection.execute("SELECT COUNT(*) FROM model_predictions").fetchone()[0]
                return f"remaining-day-v1 | Monte Carlo predictions: {count}"
            if command == "/calibration":
                rows = connection.execute(
                    "SELECT probability,eventual_outcome FROM model_predictions WHERE eventual_outcome IN (0,1)"
                ).fetchall()
                metrics = calibration_metrics([(float(r[0]), int(r[1])) for r in rows])
                return ("Calibration: INSUFFICIENT DATA" if metrics.samples < 30 else
                        f"Calibration n={metrics.samples} Brier={metrics.brier_score:.4f} logloss={metrics.log_loss:.4f}")
        if command == "/pnl":
            tracker = CostTracker(self.database)
            now = datetime.now(timezone.utc)
            seven = tracker.report(now - timedelta(days=7))
            thirty = tracker.report(now - timedelta(days=30))
            return (f"Realized net P&L: 7d ${seven.net_pnl_usd:.2f} | "
                    f"30d ${thirty.net_pnl_usd:.2f} | "
                    "Expected monthly income: INSUFFICIENT DATA")
        if command == "/costs":
            report = CostTracker(self.database).report()
            return (f"Costs | fees ${report.trading_fees_usd:.2f} | "
                    f"infrastructure ${report.infrastructure_usd:.2f} | "
                    f"AI ${report.ai_api_usd:.4f} | other API ${report.other_api_usd:.4f}")
        if command == "/pause":
            self._set_control("paused", "true")
            return "Trading and paper order creation paused."
        if command == "/resume":
            with self.database.connect() as connection:
                emergency = connection.execute(
                    "SELECT value FROM control_state WHERE key='emergency_stop'"
                ).fetchone()
            if emergency and emergency[0] == "true":
                return "Emergency stop is latched; clear it locally after investigation."
            self._set_control("paused", "false")
            return "Paper trading resumed."
        if command == "/emergency_stop":
            self._set_control("emergency_stop", "true")
            self._set_control("paused", "true")
            return "EMERGENCY STOP latched. Manual local recovery is required."
        return "Commands: /status /pnl /today /positions /weather /health /costs /model /calibration /pause /resume /emergency_stop"


def main() -> int:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    allowed_chat = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not allowed_chat:
        return 0
    from telegram import Update
    from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

    database = Database(os.getenv("DATABASE_PATH", "data/apex_weather.sqlite3"))
    database.migrate()
    controller = TelegramController(database, float(os.getenv("BANKROLL", "100")))
    with database.connect() as connection:
        latest_health_id = int(connection.execute(
            "SELECT COALESCE(MAX(id),0) FROM health_events"
        ).fetchone()[0])
    alert_cursor = {"id": latest_health_id}

    async def reply(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_chat or str(update.effective_chat.id) != allowed_chat:
            return
        if update.message and update.message.text:
            await update.message.reply_text(controller.handle(update.message.text))

    async def send_daily(context: ContextTypes.DEFAULT_TYPE) -> None:
        await context.bot.send_message(chat_id=allowed_chat, text=controller.handle("/today"))

    async def send_important_health(context: ContextTypes.DEFAULT_TYPE) -> None:
        with database.connect() as connection:
            rows = connection.execute(
                "SELECT id,severity,component,code,message FROM health_events "
                "WHERE id>? AND severity IN ('error','critical') ORDER BY id LIMIT 10",
                (alert_cursor["id"],),
            ).fetchall()
        for row in rows:
            await context.bot.send_message(
                chat_id=allowed_chat,
                text=(f"APEX {str(row['severity']).upper()} | {row['component']} | "
                      f"{row['code']} | {row['message']}")[:4000],
            )
            alert_cursor["id"] = int(row["id"])

    application = Application.builder().token(token).build()
    application.add_handler(MessageHandler(filters.COMMAND, reply))
    if application.job_queue is None:
        raise RuntimeError("Telegram job queue dependency is unavailable")
    summary_hour, summary_minute = (
        int(part) for part in os.getenv("TELEGRAM_DAILY_SUMMARY_UTC", "23:55").split(":", 1)
    )
    application.job_queue.run_daily(
        send_daily, time=time(summary_hour, summary_minute, tzinfo=timezone.utc),
        name="daily-summary",
    )
    application.job_queue.run_repeating(
        send_important_health, interval=60, first=15, name="important-health-alerts",
    )
    application.run_polling(drop_pending_updates=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
