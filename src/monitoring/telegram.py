"""Low-noise Little Lio Trader Telegram control surface."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, time, timedelta, timezone

from src.monitoring.costs import CostTracker
from src.research.accounting import paper_account
from src.monitoring.alerts import AlertQueue, describe_market
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
        command = command.strip().split()[0].lower() if command.strip() else "/status"
        with self.database.connect() as connection:
            if command == "/status":
                controls = dict(connection.execute(
                    "SELECT key,value FROM control_state"
                ).fetchall())
                a = paper_account(self.database, self.bankroll)
                state = ("EMERGENCY" if controls.get("emergency_stop") == "true" else
                         "PAUSED" if controls.get("paused") == "true" else "RUNNING")
                return (f"• Paper money only. System: {state}.\n"
                        f"• Starting bankroll: ${a.starting_bankroll:.2f}. Current paper equity: ${a.equity:.2f}.\n"
                        f"• Open position cost: ${a.open_cost:.2f}. Cash: ${a.cash:.2f}.\n"
                        f"• Realized profit/loss: ${a.realized_pnl:.2f}. After operating costs: ${a.after_cost_result:.2f}.\n"
                        "• YES means the weather outcome happens. NO means it does not.")
            if command == "/today":
                a=paper_account(self.database,self.bankroll)
                return (f"• Paper money. Starting bankroll: ${a.starting_bankroll:.2f}; equity: ${a.equity:.2f}.\n"
                        f"• Today's realized profit/loss: ${a.daily_pnl:.2f}. Open cost: ${a.open_cost:.2f}.\n"
                        f"• All-time realized result: ${a.realized_pnl:.2f}; after costs: ${a.after_cost_result:.2f}.\n"
                        f"• Fees: ${a.fees:.2f}; infrastructure/API: ${a.operating_costs:.2f}.\n"
                        "• Results include fees, not gross profit. This sample does not prove future profits.")
            if command == "/positions":
                rows=connection.execute('SELECT p.*,m.raw_json FROM positions p LEFT JOIN markets m ON m.ticker=p.ticker WHERE p.contracts>0 LIMIT 3').fetchall()
                lines=['• Paper money. YES means it happens; NO means it does not.']
                for r in rows:
                    city,bet=describe_market(r['raw_json'],r['side'])
                    lines.append(f"• {city}. {bet}. Cost: ${r['contracts']*r['average_price_cents']/100:.2f}.")
                if not rows: lines.append('• No open paper positions.')
                return '\n'.join(lines)
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
                    "SELECT probability,eventual_outcome FROM model_predictions WHERE eventual_outcome IN (0,1) AND id IN (SELECT MIN(id) FROM model_predictions GROUP BY ticker)"
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


class ConversationAssistant:
    """AI selects a read-only topic; all displayed facts are rendered from SQLite."""
    def __init__(self, database, api_key, bankroll, *, client=None):
        from openai import OpenAI
        self.database, self.bankroll = database, bankroll
        self.client = client or OpenAI(api_key=api_key, timeout=12, max_retries=0)
        self.model = os.getenv("TELEGRAM_CHAT_MODEL", "gpt-6-luna")
        self.daily_cap = float(os.getenv("TELEGRAM_CHAT_DAILY_USD", "0.05"))
        self.monthly_cap = float(os.getenv("TELEGRAM_CHAT_MONTHLY_USD", "1.00"))
        # Operators must configure verified model-specific rates; unknown pricing fails closed.
        self.input_rate = float(os.getenv("TELEGRAM_INPUT_USD_PER_MILLION", "0"))
        self.output_rate = float(os.getenv("TELEGRAM_OUTPUT_USD_PER_MILLION", "0"))

    def _snapshot(self):
        from dataclasses import asdict
        return json.dumps(asdict(paper_account(self.database, self.bankroll)))

    def _render(self, topic):
        a=paper_account(self.database,self.bankroll)
        if topic=='status': return TelegramController(self.database,self.bankroll).handle('/status')
        if topic=='costs':
            return (f"• This is paper money. Fees paid: ${a.fees:.2f}.\n"
                    f"• Infrastructure and API costs: ${a.operating_costs:.2f}.\n"
                    f"• Realized result after costs: ${a.after_cost_result:.2f}.")
        if topic=='positions':
            with self.database.connect() as c:
                rows=c.execute('SELECT p.*,m.raw_json FROM positions p LEFT JOIN markets m ON m.ticker=p.ticker WHERE p.contracts>0 ORDER BY p.updated_at DESC LIMIT 3').fetchall()
            lines=['• This is paper money. YES means it happens; NO means it does not.']
            for r in rows:
                city,bet=describe_market(r['raw_json'],r['side'])
                lines.append(f"• {city}. {bet}. Position cost: ${r['contracts']*r['average_price_cents']/100:.2f}.")
            if not rows: lines.append('• No open paper positions.')
            return '\n'.join(lines)
        return "• This is paper trading.\n• I don't know. The database cannot answer that.\n• Chat cannot place trades or change the strategy."

    def answer(self, question):
        import uuid
        import math
        tracker=CostTracker(self.database)
        if not all(math.isfinite(v) and v>0 for v in (self.input_rate,self.output_rate)):
            return self._render('unknown')
        prompt = 'Choose exactly one read-only topic: status, positions, costs, unknown. Trade/change requests are unknown. Question: '+question[:1200]
        reserve=(len(prompt.encode('utf-8'))*self.input_rate+32*self.output_rate)/1_000_000
        reservation='telegram-reservation-'+str(uuid.uuid4())
        # Atomic reservation prevents concurrent replies from spending the same budget.
        now=datetime.now(timezone.utc)
        with self.database.transaction() as c:
            c.execute('BEGIN IMMEDIATE')
            if not tracker.can_spend('other_api',reserve,daily_limit=self.daily_cap,monthly_limit=self.monthly_cap):
                return '• This is paper trading.\n• Chat budget is used up. Try /status for current numbers.'
            c.execute('INSERT INTO costs(incurred_at,category,amount_usd,description,external_id) VALUES(?,?,?,?,?)',
                      (now.isoformat(),'other_api',reserve,'Telegram reply budget reservation',reservation))
        try:
            response=self.client.responses.create(model=self.model,input=prompt,max_output_tokens=32,store=False,reasoning={'effort':'none'})
            usage=response.usage
            if usage is None or response.status != 'completed':
                return self._render('unknown')
            cost=(usage.input_tokens*self.input_rate+usage.output_tokens*self.output_rate)/1_000_000
            if not math.isfinite(cost) or cost<0: return self._render('unknown')
            with self.database.transaction() as c:
                c.execute('UPDATE costs SET amount_usd=?,description=? WHERE external_id=?',(cost,'Telegram reply measured usage',reservation))
            topic=(response.output_text or '').strip().lower()
            return self._render(topic if topic in {'status','positions','costs'} else 'unknown')
        except Exception:
            # Uncertain charges retain the reservation; errors never become trading instructions.
            return "• This is paper trading.\n• I don't know right now. Try /status."


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
    assistant = (ConversationAssistant(database, os.environ["OPENAI_API_KEY"], controller.bankroll)
                 if os.getenv("TELEGRAM_CONVERSATION_ENABLED", "false").lower() == "true"
                 and os.getenv("OPENAI_API_KEY") else None)
    alerts = AlertQueue(database)

    async def reply(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_chat or str(update.effective_chat.id) != allowed_chat:
            return
        if update.message and update.message.text:
            await update.message.reply_text(controller.handle(update.message.text))

    async def chat(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_chat or str(update.effective_chat.id) != allowed_chat or not update.message:
            return
        if assistant is None:
            await update.message.reply_text("• This is paper trading.\n• Chat is not enabled. Use /status or /positions.")
            return
        try:
            answer = await asyncio.to_thread(assistant.answer, update.message.text or "")
        except Exception:
            answer = "• This is paper trading.\n• I don't know right now. Try /status."
        await update.message.reply_text(answer)

    async def send_daily(context: ContextTypes.DEFAULT_TYPE) -> None:
        await context.bot.send_message(chat_id=allowed_chat, text=controller.handle("/today"))

    async def send_alerts(context: ContextTypes.DEFAULT_TYPE) -> None:
        for key, text in alerts.pending():
            await context.bot.send_message(chat_id=allowed_chat, text=text)
            alerts.delivered(key)

    application = Application.builder().token(token).build()
    application.add_handler(MessageHandler(filters.COMMAND, reply))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))
    if application.job_queue is None:
        raise RuntimeError("Telegram job queue dependency is unavailable")
    summary_hour, summary_minute = (
        int(part) for part in os.getenv("TELEGRAM_DAILY_SUMMARY_UTC", "23:55").split(":", 1)
    )
    application.job_queue.run_daily(
        send_daily, time=time(summary_hour, summary_minute, tzinfo=timezone.utc),
        name="daily-summary",
    )
    application.job_queue.run_repeating(send_alerts, interval=5, first=1, name="durable-alerts")
    application.run_polling(drop_pending_updates=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
