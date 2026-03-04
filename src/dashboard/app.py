"""APEX Streamlit monitoring dashboard.

Run: uv run streamlit run src/dashboard/app.py
"""

from __future__ import annotations

import duckdb
import pandas as pd
import plotly.express as px
import streamlit as st

DB_PATH = "data/apex.duckdb"

st.set_page_config(
    page_title="APEX Trading Monitor",
    page_icon="📈",
    layout="wide",
)

st.title("APEX — Autonomous Predictive Equity eXperiment")
st.caption("April 1 – May 1, 2026 · Live trading cap: $150")


@st.cache_resource
def get_conn():
    return duckdb.connect(DB_PATH, read_only=True)


def load_snapshots() -> pd.DataFrame:
    try:
        conn = get_conn()
        return conn.execute(
            "SELECT * FROM portfolio_snapshots ORDER BY timestamp"
        ).df()
    except Exception:
        return pd.DataFrame()


def load_trades() -> pd.DataFrame:
    try:
        conn = get_conn()
        return conn.execute(
            "SELECT * FROM trades ORDER BY timestamp DESC LIMIT 100"
        ).df()
    except Exception:
        return pd.DataFrame()


def load_recent_logs() -> pd.DataFrame:
    try:
        conn = get_conn()
        return conn.execute(
            "SELECT * FROM agent_logs ORDER BY timestamp DESC LIMIT 50"
        ).df()
    except Exception:
        return pd.DataFrame()


# --- Portfolio equity curve ---
snapshots = load_snapshots()

col1, col2, col3 = st.columns(3)

if not snapshots.empty:
    latest = snapshots.iloc[-1]
    col1.metric("Equity", f"${latest['equity']:,.2f}")
    col2.metric("Cash", f"${latest['cash']:,.2f}")
    col3.metric("Unrealized P&L", f"${latest['unrealized_pnl']:,.2f}")

    st.subheader("Equity Curve")
    fig = px.line(snapshots, x="timestamp", y="equity", title="Portfolio Equity")
    st.plotly_chart(fig, use_container_width=True)
else:
    col1.metric("Equity", "—")
    col2.metric("Cash", "—")
    col3.metric("Unrealized P&L", "—")
    st.info("No portfolio data yet. Agent starts April 1, 2026.")

# --- Recent trades ---
st.subheader("Recent Trades")
trades = load_trades()
if not trades.empty:
    st.dataframe(trades[["timestamp", "symbol", "side", "qty", "fill_price", "strategy", "paper"]])
else:
    st.info("No trades yet.")

# --- Agent logs ---
st.subheader("Agent Logs")
logs = load_recent_logs()
if not logs.empty:
    st.dataframe(logs[["timestamp", "level", "message"]])
else:
    st.info("No agent logs yet.")

st.caption("Auto-refreshes every 60s during market hours.")
