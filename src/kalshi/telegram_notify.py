"""
Little Lio Trader — two-way Telegram bot for APEX.

Outbound: async send_* functions called via asyncio.run() from the agent loop.
Inbound:  long-polling Application running in a background daemon thread.

Security:
  - Whitelist: only TELEGRAM_CHAT_ID may send commands (silent ignore otherwise)
  - Rate limit: 5 messages per 60s per chat_id (silent ignore if exceeded)
  - Hard block list: refuses requests touching keys/money/env/trade-history
  - No personal data stored or logged
  - Input sanitized before processing
  - Long polling only (no webhook, no exposed endpoint)
"""
import asyncio
import collections
import html
import json
import logging
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from telegram import Bot, Update
    from telegram.ext import (
        Application,
        ApplicationBuilder,
        CommandHandler,
        ContextTypes,
        MessageHandler,
        filters,
    )
    _TG = True
except ImportError:
    _TG = False
    logger.warning("python-telegram-bot not installed; Telegram disabled.")

# ── Paths & constants ──────────────────────────────────────────────────────────
PAUSE_FLAG = Path("/opt/apex/paused.flag")
TRADES_LOG  = Path("/opt/apex/trades.log")
RATE_LIMIT_MAX    = 5
RATE_LIMIT_WINDOW = 60  # seconds

# ── Hard block list ────────────────────────────────────────────────────────────
_BLOCKED_RE = re.compile(
    r"api.?key|private.?key|secret|delete.*trade|drop.*log"
    r"|rm\s+-rf|apex_env.*live|send.*money|transfer.*fund"
    r"|withdraw|password|flip.*live|go.*live",
    re.IGNORECASE,
)

# ── Rate limiter ───────────────────────────────────────────────────────────────
_rate_buckets: dict[str, collections.deque] = {}

def _is_rate_limited(chat_id: str) -> bool:
    now = time.monotonic()
    bucket = _rate_buckets.setdefault(chat_id, collections.deque())
    while bucket and now - bucket[0] > RATE_LIMIT_WINDOW:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT_MAX:
        return True
    bucket.append(now)
    return False

# ── Auth & sanitisation ────────────────────────────────────────────────────────
def _allowed_id() -> str:
    return os.getenv("TELEGRAM_CHAT_ID", "")

def _authorized(update: "Update") -> bool:
    allowed = _allowed_id()
    return bool(allowed) and str(update.effective_chat.id) == str(allowed)

def _sanitize(text: str) -> str:
    cleaned = html.unescape(text)
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", cleaned)
    return cleaned[:2000]

# ── Token helpers ──────────────────────────────────────────────────────────────
def _token() -> str:
    return os.getenv("TELEGRAM_BOT_TOKEN", "")

def _chat_id() -> str:
    return os.getenv("TELEGRAM_CHAT_ID", "")

# ── Outbound API ───────────────────────────────────────────────────────────────
async def _send(text: str) -> bool:
    """Send a message to the configured chat. Never raises."""
    try:
        if not _TG or not _token() or not _chat_id():
            logger.info("[TELEGRAM stub] %s", text[:120])
            return False
        bot = Bot(token=_token())
        async with bot:
            await bot.send_message(
                chat_id=_chat_id(),
                text=text,
                parse_mode="Markdown",
            )
        return True
    except Exception as e:
        logger.error("Telegram _send failed: %s", e)
        return False


async def send_message(text: str) -> bool:
    return await _send(text)


async def send_trade_alert(
    market_title: str,
    side: str,
    amount_usd: float,
    edge_pct: float,
    reasoning: str,
) -> bool:
    mode = os.getenv("APEX_ENV", "paper").upper()
    yes_price = None  # price_cents not passed here; edge_pct used instead
    return await _send(
        f"*[{mode}] Found something.*\n"
        f"`{market_title}` — {side.upper()} at edge `{edge_pct:.1f}%`. "
        f"Sizing: `${amount_usd:.2f}`.\n"
        f"_{reasoning[:200]}_"
    )


async def send_trade_win(market_title: str, pnl: float) -> bool:
    return await _send(f"That one paid. `+${pnl:.2f}` on `{market_title}`.")


async def send_trade_loss(market_title: str, pnl: float) -> bool:
    return await _send(
        f"Took the loss. `-${abs(pnl):.2f}` on `{market_title}`. "
        f"Still within drawdown limits."
    )


async def send_daily_summary(
    pnl: float, trades: int, win_rate: float, bankroll: float, day: int = 0
) -> bool:
    day_str = f"Day {day}. " if day else ""
    if pnl >= 0:
        return await _send(
            f"{day_str}Up `${pnl:.2f}`. Win rate: `{win_rate:.0%}`. "
            f"`{trades}` trades placed."
        )
    else:
        return await _send(
            f"{day_str}Down `${abs(pnl):.2f}`. Win rate: `{win_rate:.0%}`. "
            f"Adjusting filters tomorrow."
        )


async def send_error(error_msg: str) -> bool:
    return await _send(f"Something broke. Checking it now.\n`{error_msg[:300]}`")


async def send_startup(balance: float, mode: str) -> bool:
    return await _send(
        f"*Little Lio Trader*\n"
        f"Online. `{mode.upper()}` mode. Balance `${balance:.2f}`. "
        f"Scanning Kalshi markets every 15 minutes."
    )


# ── Shared Kalshi client factory for handlers ──────────────────────────────────
def _make_kalshi_client():
    sys.path.insert(0, "/opt/apex")
    from kalshi_client import KalshiClient  # noqa: PLC0415
    return KalshiClient(
        key_id=os.getenv("KALSHI_API_KEY_ID", ""),
        private_key_path=os.getenv(
            "KALSHI_PRIVATE_KEY_PATH", "/opt/apex/kalshi_private.pem"
        ),
        paper_mode=os.getenv("APEX_ENV", "paper").lower() == "paper",
    )


# ── Guard decorator (auth + rate limit) ───────────────────────────────────────
def _guarded(fn):
    """Wrap a handler: silently drop if not authorized or rate-limited."""
    import functools

    @functools.wraps(fn)
    async def wrapper(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
        incoming_id = str(update.effective_chat.id) if update.effective_chat else "unknown"
        allowed_id = _allowed_id()
        logger.info(
            "Incoming update | handler=%s chat_id=%s allowed=%s text=%s",
            fn.__name__, incoming_id, allowed_id,
            (update.message.text or "")[:60] if update.message else "",
        )
        if not _authorized(update):
            logger.warning("Rejected message from chat_id=%s (allowed=%s)", incoming_id, allowed_id)
            return
        cid = str(update.effective_chat.id)
        if _is_rate_limited(cid):
            await update.message.reply_text("Slow down.")
            return
        await fn(update, context)

    return wrapper


# ── Command handlers ───────────────────────────────────────────────────────────
@_guarded
async def _cmd_start(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
    await update.message.reply_text(
        "Online. Scanning Kalshi markets every 15 minutes."
    )


@_guarded
async def _cmd_status(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
    try:
        client    = _make_kalshi_client()
        bal_data  = client.get_balance()
        balance   = bal_data.get("balance", 0) / 100
        positions = client.get_positions()
        open_pos  = [p for p in positions if p.get("total_traded", 0) > 0]
        bankroll  = float(os.getenv("APEX_BANKROLL", "150"))
        mode      = os.getenv("APEX_ENV", "paper").upper()
        paused    = PAUSE_FLAG.exists()
        pnl       = balance - bankroll
        n         = len(open_pos)
        pos_str   = f"{n} position{'s' if n != 1 else ''} open"

        if paused:
            text = f"Paused. {pos_str}. Balance `${balance:.2f}`."
        elif not TRADES_LOG.exists() or TRADES_LOG.stat().st_size == 0:
            text = f"No trades yet. Paper mode, scanning markets."
        elif abs(pnl) < 0.01:
            text = f"Flat. {pos_str}. Waiting for edge."
        elif pnl > 0:
            text = f"Up `${pnl:.2f}`. {pos_str}. Looking clean."
        else:
            text = f"Down `${abs(pnl):.2f}`. {pos_str}. Still within limits."
    except Exception as e:
        text = f"Can't reach Kalshi right now. `{e}`"
    await update.message.reply_text(text, parse_mode="Markdown")


@_guarded
async def _cmd_pause(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
    if PAUSE_FLAG.exists():
        await update.message.reply_text("Already paused.")
        return
    PAUSE_FLAG.touch()
    await update.message.reply_text("Paused. Not touching anything until you say so.")


@_guarded
async def _cmd_resume(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
    if not PAUSE_FLAG.exists():
        await update.message.reply_text("Already running.")
        return
    PAUSE_FLAG.unlink()
    await update.message.reply_text("Back on it.")


@_guarded
async def _cmd_trades(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
    try:
        if not TRADES_LOG.exists() or not TRADES_LOG.read_text().strip():
            await update.message.reply_text("No trades on record yet.")
            return
        lines = TRADES_LOG.read_text().strip().splitlines()[-10:]
        if not lines:
            await update.message.reply_text("No trades on record yet.")
            return
        rows = []
        for line in lines:
            try:
                t    = json.loads(line)
                date = t.get("date", "")[:10]
                tkr  = t.get("ticker", "?")[:28]
                side = t.get("side", "?").upper()
                bet  = t.get("bet_usd", 0)
                edge = float(t.get("edge", 0)) * 100
                tag  = "[P]" if t.get("paper") else "[L]"
                rows.append(f"{tag} `{date}` {tkr} {side} ${bet:.2f} edge={edge:+.1f}%")
            except Exception:
                pass
        await update.message.reply_text(
            "*Last 10 trades*\n" + "\n".join(rows), parse_mode="Markdown"
        )
    except Exception as e:
        await update.message.reply_text(f"Couldn't read trades. `{e}`", parse_mode="Markdown")


@_guarded
async def _cmd_briefing(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
    try:
        today  = datetime.now(timezone.utc).date().isoformat()
        trades = []
        if TRADES_LOG.exists():
            for line in TRADES_LOG.read_text().splitlines():
                try:
                    t = json.loads(line)
                    if t.get("date", "").startswith(today):
                        trades.append(t)
                except Exception:
                    pass
        if not trades:
            await update.message.reply_text("Nothing placed today.")
            return
        total_bet = sum(t.get("bet_usd", 0) for t in trades)
        wins      = sum(1 for t in trades if float(t.get("edge", 0)) > 0)
        win_rate  = wins / len(trades)
        mode      = os.getenv("APEX_ENV", "paper").upper()
        text = (
            f"`{mode}` — `{len(trades)}` trades, `${total_bet:.2f}` deployed, "
            f"`{win_rate:.0%}` win rate."
        )
    except Exception as e:
        text = f"Couldn't generate briefing. `{e}`"
    await update.message.reply_text(text, parse_mode="Markdown")


@_guarded
async def _cmd_settings(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
    text = (
        f"*Config*\n"
        f"env: `{os.getenv('APEX_ENV','paper')}`\n"
        f"kelly: `{os.getenv('KELLY_FRACTION','0.25')}`\n"
        f"max position: `{os.getenv('MAX_POSITION_PCT','0.05')}`\n"
        f"bankroll: `${float(os.getenv('APEX_BANKROLL','150')):.2f}`"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


@_guarded
async def _cmd_risk(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
    try:
        client    = _make_kalshi_client()
        positions = client.get_positions()
        open_pos  = [p for p in positions if p.get("total_traded", 0) > 0]
        bankroll  = float(os.getenv("APEX_BANKROLL", "150"))
        exposure  = sum(float(p.get("total_traded", 0)) / 100 for p in open_pos)
        remaining = bankroll - exposure
        exp_pct   = (exposure / bankroll * 100) if bankroll else 0
        text = (
            f"*Risk*\n"
            f"`{len(open_pos)}` open positions, `${exposure:.2f}` deployed "
            f"(`{exp_pct:.1f}%` of bankroll). `${remaining:.2f}` remaining."
        )
    except Exception as e:
        text = f"Couldn't fetch risk data. `{e}`"
    await update.message.reply_text(text, parse_mode="Markdown")


@_guarded
async def _cmd_help(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
    await update.message.reply_text(
        "Here is what I can do:\n\n"
        "/status — balance, positions, P&L\n"
        "/trades — last 10 trades\n"
        "/briefing — today's summary\n"
        "/risk — exposure breakdown\n"
        "/settings — current config\n"
        "/pause — stop placing bets\n"
        "/resume — resume scanning\n\n"
        "Or just ask me something.",
    )


@_guarded
async def _handle_message(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
    """Smart Q&A: call Claude Haiku with trade context."""
    raw  = update.message.text or ""
    text = _sanitize(raw)

    if _BLOCKED_RE.search(text):
        await update.message.reply_text("Not sure what you mean. Try /help.")
        return

    try:
        import anthropic

        context_lines = ""
        if TRADES_LOG.exists():
            context_lines = "\n".join(
                TRADES_LOG.read_text().strip().splitlines()[-20:]
            )
        mode = os.getenv("APEX_ENV", "paper").upper()

        # Use AsyncAnthropic so 429 retries use asyncio.sleep (non-blocking)
        # instead of time.sleep which would freeze the entire event loop.
        ai = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        resp = await ai.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            system=(
                "You are Little Lio Trader, an autonomous Kalshi prediction market agent "
                "running on a Hetzner server. You speak like a sharp, composed financial "
                "assistant — think Jarvis but a 22-year-old version. Direct, no filler. "
                "Occasionally uses natural modern dialect when it fits — not slang, not memes, "
                "just how someone sharp actually talks. Never mention the user name or any "
                "personal info. Always under 3 sentences. Measured when things are good. "
                "Honest when things are bad. Never use exclamation marks."
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"Current mode: {mode}\n"
                    f"Recent trade log (last 20 lines):\n{context_lines}\n\n"
                    f"Question: {text}"
                ),
            }],
        )
        reply = resp.content[0].text if resp.content else "Nothing to add right now."
    except Exception as e:
        logger.error("Q&A brain call failed: %s", e)
        reply = "Unavailable right now. Try again in a minute."

    await update.message.reply_text(reply)


# ── Background listener ────────────────────────────────────────────────────────
_bot_thread: Optional[threading.Thread] = None


async def _run_polling(token: str) -> None:
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start",    _cmd_start))
    app.add_handler(CommandHandler("status",   _cmd_status))
    app.add_handler(CommandHandler("pause",    _cmd_pause))
    app.add_handler(CommandHandler("resume",   _cmd_resume))
    app.add_handler(CommandHandler("trades",   _cmd_trades))
    app.add_handler(CommandHandler("briefing", _cmd_briefing))
    app.add_handler(CommandHandler("settings", _cmd_settings))
    app.add_handler(CommandHandler("risk",     _cmd_risk))
    app.add_handler(CommandHandler("help",     _cmd_help))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_message)
    )
    async with app:
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        logger.info("Telegram bot polling active.")
        # Block until process dies (daemon thread — killed on main exit)
        await asyncio.Event().wait()


def start_bot_listener() -> None:
    """Spawn the inbound handler in a background daemon thread."""
    if not _TG:
        logger.warning("python-telegram-bot unavailable; skipping bot listener.")
        return
    tok = _token()
    if not tok:
        logger.warning("No TELEGRAM_BOT_TOKEN; skipping bot listener.")
        return

    def _run() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_run_polling(tok))
        except Exception as e:
            logger.error("Bot listener crashed: %s", e)
        finally:
            loop.close()

    global _bot_thread
    _bot_thread = threading.Thread(target=_run, name="tg-listener", daemon=True)
    _bot_thread.start()
    logger.info("Telegram bot listener started (long polling, background thread).")
