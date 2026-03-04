# APEX — Autonomous Predictive Equity eXperiment

**A 30-day live experiment: can a Claude-powered trading agent make disciplined, documented, risk-managed decisions in real markets?**

![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)
![Build](https://img.shields.io/badge/Build-Passing-brightgreen?style=flat-square)
![Status](https://img.shields.io/badge/Status-Build%20Phase%20%E2%80%94%20March%202026-orange?style=flat-square)
![Experiment](https://img.shields.io/badge/Experiment%20Start-April%201%202026-blueviolet?style=flat-square)

---

## Overview

APEX is a fully autonomous algorithmic trading system built in 30 days as a structured experiment. A Claude-powered reasoning layer evaluates equity signals, makes position decisions, logs its own thinking to a database, and generates a daily journal — all without human intervention during market hours. What separates this from a weekend trading bot is the architecture: three interconnected pillars (trading agent, research paper, documentation engine) that treat the experiment itself as a product. Every trade made in April is a data point. Every data point becomes a paragraph in a peer-reviewable paper. The entire build is conducted in public, committed daily, and concluded on May 1.

---

## What Makes This Different

Most trading repos on GitHub are one of three things: a backtest script with a promising README, a live bot with no risk controls, or a research notebook that never touched real capital. APEX is none of those.

- **LLM reasoning layer, not just signals** — Claude evaluates each trade signal in context: current portfolio state, market regime, recent performance. The decision *and the reasoning* are logged to DuckDB on every tick. You can query why the agent did anything.
- **Congressional trade tracking via Unusual Whales** *(planned, Week 2)* — institutional and congressional trading data as an alternative signal layer. Retail strategies using the same information that moves markets.
- **Real money on the line** — paper trading April 1–14, then a live decision point on April 15 with a hard $150 cap. The experiment has stakes.
- **Three-pillar architecture** — the agent, the paper, and the journal are not separate concerns. They share the same database. The paper writes itself from trade logs.
- **Fully auditable** — every order, signal, and reasoning string is persisted. Every day's session is committed. Every decision can be replayed.
- **Academic output** — this ends with a Quarto-rendered research paper targeting quantitative finance practitioners, not a Medium post.

---

## Architecture

APEX is built around three interconnected pillars that share a single DuckDB database.

```
┌─────────────────────────────────────────────────────────────────┐
│                         APEX SYSTEM                             │
│                                                                 │
│  ┌─────────────────────┐      ┌──────────────────────────────┐  │
│  │   TRADING AGENT     │      │     DOCUMENTATION ENGINE     │  │
│  │                     │      │                              │  │
│  │  APScheduler loop   │      │  Daily journal (auto)        │  │
│  │  ├─ 09:31 scan      │      │  ├─ docs/journal/YYYY-MM-DD  │  │
│  │  ├─ 15:45 rebalance │      │  └─ Quarto → devlog posts    │  │
│  │  └─ 16:05 EOD       │      │                              │  │
│  │                     │      │  Triggered by: EOD hook      │  │
│  │  brain.py           │      │  Input: DuckDB trade logs    │  │
│  │  └─ Claude haiku    │      └──────────────────────────────┘  │
│  │     (tick decisions)│                    │                   │
│  │  Claude sonnet      │                    │                   │
│  │     (EOD analysis)  │                    ▼                   │
│  │                     │      ┌──────────────────────────────┐  │
│  │  executor.py        │      │      RESEARCH PAPER          │  │
│  │  └─ Alpaca Markets  │      │                              │  │
│  └──────────┬──────────┘      │  docs/paper/index.qmd        │  │
│             │                 │  Quarto → PDF + HTML         │  │
│             ▼                 │                              │  │
│  ┌─────────────────────┐      │  Updated when:               │  │
│  │      DuckDB         │─────▶│  ├─ strategy backtested      │  │
│  │   apex.duckdb       │      │  ├─ paper trade begins       │  │
│  │                     │      │  └─ significant event        │  │
│  │  bars               │      │                              │  │
│  │  signals            │      │  All figures generated from  │  │
│  │  trades             │      │  embedded DuckDB queries     │  │
│  │  portfolio_snapshots│      └──────────────────────────────┘  │
│  │  agent_logs         │                                        │
│  └─────────────────────┘                                        │
└─────────────────────────────────────────────────────────────────┘
```

**Data flow**: Alpaca → DuckDB (bars) → Strategy signals → Claude reasoning → Alpaca orders → DuckDB (trades) → Journal + Paper

---

## Tech Stack

Every tool here was chosen deliberately. The alternatives are listed.

| Layer | Tool | Version | Why this, not X |
|---|---|---|---|
| Language | Python | 3.12 | Undisputed standard for quant finance. No alternatives seriously considered. |
| Package manager | `uv` | 0.10+ | 10–100× faster than pip. Manages Python versions. No Poetry overhead. |
| Broker / execution | Alpaca Markets | — | Free paper trading, real-time data, clean Python SDK. IBKR is more powerful but weeks to set up. |
| Backtesting | vectorbt | 0.28 | NumPy/Numba-accelerated. 100× faster than backtrader (which is no longer maintained). |
| Database | DuckDB | 1.4 | Zero-config columnar storage. Handles OHLCV natively. No Postgres infra to spin up. |
| ML signals | LightGBM + scikit-learn | — | Faster than XGBoost, more interpretable. Proven on financial factor data. |
| Agent reasoning | Claude API (`claude-haiku-4-5`) | — | Haiku for tick decisions (cost efficiency); sonnet for EOD analysis. Every decision logged. |
| Scheduler | APScheduler | 3.11 | Cron-style scheduling inside Python. Gated to April 1 – May 1 experiment window. |
| Dashboard | Streamlit | 1.55 | Live trading monitor in 20 lines of Python. Dash is better at scale; Streamlit is better right now. |
| Paper & journal | Quarto | 2.x | Code + math + narrative → PDF and HTML from one source. Jupyter Book is declining. |
| Config / secrets | Pydantic Settings | 2.x | Type-safe `.env` parsing. No raw `os.environ` calls anywhere in the codebase. |
| Testing | pytest | 9.x | Standard. `tests/` mirrors `src/`. Must pass before every commit. |
| Data feed | yfinance + Alpaca | — | yfinance for backtest history; Alpaca live feed for real-time during experiment. |

---

## Experiment Timeline

```
March 2026          April 2026                           May 2026
────────────────────┬───────────────────────────────────┬──────────────
  BUILD PERIOD      │         LIVE EXPERIMENT            │  WRAP-UP
  Mar 3 – Mar 31    │                                    │
                    │  Apr 1 ──────── Apr 15 ─────────── May 1
  Scaffold          │  Agent starts   Live trading       Agent stops
  Backtest          │  (paper mode)   decision point     30-day window
  Validate          │                 ($150 cap)         closes
  strategies        │                 if paper phase     │
                    │                 is profitable      │  ~May 8
                    │                                    │  Paper
                    │                                    │  submitted
```

**Gate rules:**
- A strategy may not paper-trade until its backtest Sharpe ratio exceeds 0.5 over ≥1 year of data
- The agent may not go live without explicit human confirmation — it can recommend, never unilaterally execute
- Live exposure is hard-capped at `APEX_LIVE_CAP_USD=150`, enforced in config, not just convention

---

## Early Results — Day 1 Backtests

*Universe: SPY, QQQ, AAPL, MSFT, NVDA · Period: 2020-01-01 → 2024-12-31 · Fees: 0.1%/trade*

| Metric | Momentum | Mean Reversion | Better |
|---|---|---|---|
| **Sharpe Ratio** | **1.447** | 0.782 | Momentum |
| **Total Return** | **271.8%** | 77.2% | Momentum |
| **Max Drawdown** | 31.5% | **25.9%** | Mean Rev |
| Max DD Duration | 354 days | 391 days | Momentum |
| **Sortino Ratio** | **2.140** | 1.189 | Momentum |
| Calmar Ratio | **1.473** | 0.698 | Momentum |
| Win Rate | — (open) | **78.1%** | Mean Rev |
| Total Trades | 5 | 32 | — |
| Profit Factor | — | **3.36** | Mean Rev |
| Total Fees | **$0.07** | $7.95 | Momentum |

*Benchmark (equal-weight buy & hold): 573.2% — a high bar in a 5-year tech bull market.*

**Reading these results honestly**: momentum wins on every risk-adjusted metric that matters for April. Its low turnover (5 trades, $0.07 in fees) is particularly valuable at $150 live capital where fee drag is real. Mean reversion has a 78% win rate and lower drawdown — it's retained as a confirmation signal, not discarded. Both strategies underperform buy-and-hold because that's what buy-and-hold does in a concentrated bull run. The research question isn't "does this beat the S&P 500." It's "can an autonomous agent make disciplined, risk-managed decisions over 30 days."

**Primary strategy for April:** Momentum (Sharpe 1.447 > 0.5 threshold ✓)

---

## The Research Paper

**Title**: *APEX: Autonomous Predictive Equity eXperiment — A 30-Day Empirical Study of LLM-Augmented Algorithmic Trading*

**What it covers**: The paper documents the system architecture, strategy design, backtesting methodology, and live results of the April experiment. It analyzes whether Claude's reasoning layer adds measurable value over a pure signal-based approach, examines the agent's decision quality across different market regimes, and reports risk-adjusted performance with full statistical context.

**Methodology**: Quarto document with embedded Python code blocks — all figures are generated directly from DuckDB queries, making every chart in the paper fully reproducible from the raw trade data. No manual chart exports, no copy-pasted numbers.

**Target audience**: Quantitative finance practitioners and ML researchers. APA citations. The paper is updated live throughout the experiment and finalized ~May 8, 2026.

📄 **[Read the paper (in progress)](docs/paper/index.qmd)**

---

## Project Structure

```
apex-trading/
│
├── CLAUDE.md                   # AI session instructions — read this first
├── pyproject.toml              # uv-managed dependencies
├── pytest.ini                  # Test configuration
├── .env.template               # Copy to .env and fill in keys
│
├── src/
│   ├── config.py               # Pydantic Settings — single source of truth for all config
│   │
│   ├── agent/
│   │   ├── loop.py             # APScheduler entry point — gated to Apr 1–May 1
│   │   ├── brain.py            # Claude API reasoning layer (haiku + sonnet)
│   │   └── executor.py        # Alpaca order execution + trailing stops
│   │
│   ├── strategy/
│   │   ├── momentum.py         # Cross-sectional 20-day risk-adjusted momentum
│   │   ├── mean_reversion.py   # Bollinger Band reversion (20-day, 2σ)
│   │   └── features.py         # RSI, ATR, volatility, MA cross — LightGBM inputs
│   │
│   ├── data/
│   │   ├── schema.py           # DuckDB schema: bars, signals, trades, snapshots, logs
│   │   └── market.py           # Alpaca → DuckDB ingestion
│   │
│   ├── backtest/
│   │   └── run.py              # vectorbt CLI runner — both strategies
│   │
│   └── dashboard/
│       └── app.py              # Streamlit monitor — equity curve, trades, agent logs
│
├── docs/
│   ├── paper/
│   │   └── index.qmd           # Research paper (Quarto → PDF + HTML)
│   └── journal/
│       └── YYYY-MM-DD.qmd      # Daily trading journals (auto-generated by agent)
│
├── data/
│   └── apex.duckdb             # All market + trade data (gitignored at scale)
│
└── tests/                      # Mirrors src/ — must pass before every commit
    ├── strategy/
    └── data/
```

---

## Built With AI

This project is built with AI as a core tool, not an afterthought — and that's the point.

**Claude Code** (Anthropic's CLI) runs every development session. It scaffolded the project structure, wrote the initial strategy implementations, debugged vectorbt API changes, and generated this README. It operates under `CLAUDE.md` — a project-specific instruction set that enforces hard rules: no secrets in code, no live trades without confirmation, no strategy deploys without a passing backtest.

**Claude API** (`claude-haiku-4-5`, `claude-sonnet-4-6`) is the agent's brain during the April experiment. Haiku evaluates individual trade signals at market open and close — fast, cheap, and sufficient for structured signal evaluation. Sonnet writes the end-of-day analysis that becomes each journal entry. Every Claude response is logged to DuckDB with the full prompt context.

**The honest framing**: using AI to build a trading agent that uses AI to make trading decisions is not a gimmick. It's an experiment in whether LLMs can add judgment — not just pattern recognition — to quantitative signals. The research paper will answer whether they did.

Modern engineers build with AI. The engineers who pretend otherwise are either lying or falling behind.

---

## Setup

**Requirements**: Python 3.12+, [uv](https://docs.astral.sh/uv/), Alpaca Markets account (free), Anthropic API key.

```bash
# Clone
git clone https://github.com/LeeoniIsrael/apex-trading.git
cd apex-trading

# Install uv if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install all dependencies (creates .venv automatically)
uv sync

# Configure secrets
cp .env.template .env
# Edit .env — add ALPACA_API_KEY, ALPACA_SECRET_KEY, ANTHROPIC_API_KEY

# Verify everything works
uv run pytest
# Expected: 11 passed
```

**Run a backtest:**

```bash
# Momentum strategy — SPY, QQQ, AAPL, MSFT, NVDA (2020–2024)
uv run python -m src.backtest.run --strategy momentum --symbols SPY QQQ AAPL MSFT NVDA

# Mean reversion
uv run python -m src.backtest.run --strategy mean_reversion --symbols SPY QQQ AAPL MSFT NVDA

# Custom universe and date range
uv run python -m src.backtest.run --strategy momentum \
  --symbols SPY QQQ AAPL MSFT NVDA GOOGL AMZN META \
  --start 2022-01-01 --end 2024-12-31
```

**Launch the dashboard:**

```bash
uv run streamlit run src/dashboard/app.py
# Opens at http://localhost:8501
# Shows live equity curve, trade history, and agent logs during April
```

**Note**: The trading agent (`src/agent/loop.py`) will not execute trades outside the April 1 – May 1 window. It is safe to run at any time during the build phase.

---

## Follow the Experiment

- 📓 **[Daily Journal](docs/journal/)** — every session logged, every decision explained
- 📄 **[Research Paper](docs/paper/index.qmd)** — updated live, finalized ~May 8
- 💻 **[Commit history](https://github.com/LeeoniIsrael/apex-trading/commits/main)** — the full build log

The journal is the primary artifact during March. From April 1, the agent writes its own entries.

---

## License & Author

MIT License — use this freely, commercially or otherwise, with attribution.

**Lee Israel** — building APEX as a public experiment at the intersection of quantitative finance, autonomous AI agents, and reproducible research.

---

> **Work in progress.** This repository is updated daily through May 2026. The build phase runs through March 31. The agent goes live April 1. Results are real, documented, and honest — including the losses.
