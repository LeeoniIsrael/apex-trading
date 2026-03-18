"""
Telegram notification wrapper for APEX agent.
All functions catch exceptions silently — a Telegram failure never crashes the agent.
"""
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import telegram
    from telegram import Bot
    _TELEGRAM_AVAILABLE = True
except ImportError:
    _TELEGRAM_AVAILABLE = False
    logger.warning("python-telegram-bot not installed; Telegram notifications disabled.")


def _get_bot() -> Optional["Bot"]:
    if not _TELEGRAM_AVAILABLE:
        return None
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return None
    return Bot(token=token)


def _chat_id() -> str:
    return os.getenv("TELEGRAM_CHAT_ID", "")


async def send_message(text: str) -> bool:
    """Send a plain text message."""
    try:
        bot = _get_bot()
        if not bot or not _chat_id():
            logger.info("[TELEGRAM stub] %s", text)
            return False
        await bot.send_message(chat_id=_chat_id(), text=text, parse_mode="Markdown")
        return True
    except Exception as e:
        logger.error("Telegram send_message failed: %s", e)
        return False


async def send_trade_alert(
    market_title: str,
    side: str,
    amount_usd: float,
    edge_pct: float,
    reasoning: str,
) -> bool:
    """Send a trade execution alert."""
    mode = os.getenv("APEX_ENV", "paper").upper()
    text = (
        f"*APEX TRADE — {mode}*\n"
        f"Market: `{market_title}`\n"
        f"Side: `{side.upper()}`\n"
        f"Amount: `${amount_usd:.2f}`\n"
        f"Edge: `{edge_pct:.1f}%`\n"
        f"Reason: {reasoning}"
    )
    return await send_message(text)


async def send_daily_summary(
    pnl: float,
    trades: int,
    win_rate: float,
    bankroll: float,
) -> bool:
    """Send end-of-day P&L summary."""
    pnl_emoji = "📈" if pnl >= 0 else "📉"
    text = (
        f"*APEX DAILY SUMMARY*\n"
        f"{pnl_emoji} P&L: `${pnl:+.2f}`\n"
        f"Trades today: `{trades}`\n"
        f"Win rate: `{win_rate:.0%}`\n"
        f"Bankroll: `${bankroll:.2f}`"
    )
    return await send_message(text)


async def send_error(error_msg: str) -> bool:
    """Send an error/alert message."""
    text = f"*APEX ERROR*\n`{error_msg[:500]}`"
    return await send_message(text)


async def send_startup(balance: float, mode: str) -> bool:
    """Send startup notification."""
    text = (
        f"*APEX AGENT STARTED*\n"
        f"Mode: `{mode.upper()}`\n"
        f"Balance: `${balance:.2f}`\n"
        f"Scanning markets every 15 minutes."
    )
    return await send_message(text)
