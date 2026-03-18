# APEX Google Sheets Logger
# Status: PENDING CREDENTIALS
# Once activated, logs to APEX Trading Log sheet daily
# Required: /opt/apex/google_credentials.json + SHEET_ID in .env

import os
import logging
from pathlib import Path

CREDENTIALS_PATH = Path("/opt/apex/google_credentials.json")
SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")


def is_configured() -> bool:
    return CREDENTIALS_PATH.exists() and bool(SHEET_ID)


def log_daily_summary(
    date: str,
    day_number: int,
    trades: int,
    wins: int,
    losses: int,
    pnl: float,
    bankroll: float,
    win_rate: float,
) -> None:
    if not is_configured():
        logging.info("Sheets logger not configured yet — skipping")
        return
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        scopes = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_file(str(CREDENTIALS_PATH), scopes=scopes)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SHEET_ID).sheet1
        sheet.append_row([
            date, day_number, trades, wins, losses,
            round(pnl, 2), round(bankroll, 2), round(win_rate, 1),
        ])
        logging.info("Sheets logger: row appended for %s", date)
    except Exception as e:
        logging.error("Sheets logger error: %s", e)
