# APEX — Autonomous Prediction EXchange

**A fully autonomous Kalshi prediction market trading agent, running live on a Hetzner VPS. Powered by Claude Haiku. Managed by a two-way Telegram bot named Little Lio Trader.**

![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white)
![Status](https://img.shields.io/badge/Status-Paper%20Trading%20Live-brightgreen?style=flat-square)
![Mode](https://img.shields.io/badge/Mode-PAPER-orange?style=flat-square)
![Bankroll](https://img.shields.io/badge/Bankroll-%24150-blue?style=flat-square)

**[Live Dashboard →](https://apex-trading-apex.netlify.app)**

---

## What APEX Does

APEX scans Kalshi prediction markets every 15 minutes. For each market that passes volume and time filters, it calls Claude Haiku with real-time web search to estimate the true probability of the outcome. If Claude finds a ≥5% edge over the market price with ≥60% confidence, it sizes a bet using the Kelly Criterion and places it — or logs it as a paper trade.

The whole thing runs unattended on a Hetzner server. You can query it, pause it, and get briefings from Telegram.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      HETZNER CX22 VPS                           │
│                    178.156.159.178                               │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  apex_agent.py  (PM2 id=7, process: apex-agent)          │   │
│  │                                                          │   │
│  │  APScheduler ──► scan_markets() every 15 minutes        │   │
│  │       │                                                  │   │
│  │       ├──► KalshiClient ──────────────► Kalshi REST v2  │   │
│  │       │    RSA-PSS-SHA256 auth          (get markets,    │   │
│  │       │                                 place orders)    │   │
│  │       │                                                  │   │
│  │       ├──► brain.py ────────────────► Claude Haiku       │   │
│  │       │    analyze_market()            haiku-4-5         │   │
│  │       │    + web_search (2 uses)       web_search tool   │   │
│  │       │                                                  │   │
│  │       ├──► kelly.py                                      │   │
│  │       │    0.25x Kelly, 5% cap                           │   │
│  │       │                                                  │   │
│  │       └──► telegram_notify.py ──────► Telegram Bot       │   │
│  │            Little Lio Trader           (two-way)         │   │
│  │            outbound alerts                               │   │
│  │            inbound commands (long poll)                  │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  /opt/apex/trades.log    /opt/apex/paused.flag                   │
└─────────────────────────────────────────────────────────────────┘
```

---

## How It Works

### Scan Cycle (every 15 minutes)

1. **Filter** — fetch top 20 markets by volume from Kalshi (events + short-term series). Skip anything with volume < 50, closing in < 1 hour, or closing > 14 days out.
2. **Analyze** — for each passing market, call Claude Haiku with a structured prompt. Claude does a web search for recent news, estimates the true probability, and returns `BUY_YES`, `BUY_NO`, or `SKIP` with edge and confidence scores.
3. **Size** — if edge ≥ 5% and confidence ≥ 60%, calculate bet size via fractional Kelly (0.25×), capped at 5% of bankroll per position. Max 10 positions simultaneously.
4. **Execute** — in paper mode: log to `trades.log`. In live mode: place a limit order via Kalshi REST API.
5. **Notify** — send a Telegram alert with the market, side, amount, and reasoning.

### Paper vs Live Mode

Controlled by `APEX_ENV` in `/opt/apex/.env`. In paper mode, `place_order()` returns a mock `PAPER-{timestamp}` order ID and logs the trade. No real money moves.

---

## Tech Stack

| Layer | Tool | Notes |
|---|---|---|
| Language | Python 3.12 | |
| Agent framework | APScheduler | 15min scan + 9am daily summary |
| LLM brain | Claude Haiku (`haiku-4-5-20251001`) | web_search enabled, 2 uses per market |
| Market API | Kalshi REST API v2 | RSA-PSS-SHA256 auth |
| Position sizing | Kelly Criterion | 0.25× fractional, 5% cap |
| Telegram bot | python-telegram-bot 22.x | long polling, two-way, 9 commands |
| Database | DuckDB | trade logging (planned expansion) |
| Process manager | PM2 | id=7, restart on crash |
| Server | Hetzner CX22 | Ubuntu 22.04, 2 vCPU, 4GB RAM |
| Dashboard | Netlify (static HTML) | apex-trading-apex.netlify.app |

---

## Server File Structure

```
/opt/apex/
├── apex_agent.py         # Main agent — scheduler entry point
├── brain.py              # Claude Haiku decision engine
├── kalshi_client.py      # Kalshi REST API client (RSA auth)
├── kelly.py              # Kelly Criterion position sizer
├── telegram_notify.py    # Outbound alerts + inbound command handler
├── .env                  # Secrets (not in repo)
├── kalshi_private.pem    # RSA private key for Kalshi auth
├── trades.log            # Append-only JSON trade log
├── apex.log              # Agent logs
├── paused.flag           # Created by /pause, deleted by /resume
└── venv/                 # Python virtualenv
```

---

## Telegram Bot — Little Lio Trader

The agent is controllable via a Telegram bot named **Little Lio Trader**. Only messages from `TELEGRAM_CHAT_ID` are accepted.

| Command | Response |
|---|---|
| `/start` | Online. Scanning Kalshi markets every 15 minutes. |
| `/status` | Balance, open positions, P&L vs bankroll, mode |
| `/pause` | Creates `/opt/apex/paused.flag` — agent skips scans |
| `/resume` | Deletes the pause flag — scanning resumes |
| `/trades` | Last 10 trades with side, size, edge |
| `/briefing` | Today's P&L summary, win rate, trade count |
| `/settings` | Current env, Kelly fraction, max position %, bankroll |
| `/risk` | Open position count, total exposure, remaining bankroll |
| `/help` | Command list |
| *(any message)* | Routes to Claude Haiku with trade context for Q&A |

**Security**: chat ID whitelist, 5 msg/min rate limit, hard block list (keys/money/env changes), input sanitization, long polling only.

---

## Safety Features

- **Paper mode default** — `APEX_ENV=paper` in `.env`. All trades are logged, none executed.
- **Live requires explicit change** — no code path automatically switches to live.
- **Pause flag** — `/pause` creates a file that halts the scan loop at the top of every cycle.
- **Kelly cap** — bets are capped at 5% of bankroll regardless of Kelly output.
- **Position limit** — max 10 open positions, enforced before each scan.
- **Time window filter** — no markets closing in < 1 hour or > 14 days.
- **Volume filter** — min 50 contracts traded; avoids illiquid markets.
- **Telegram whitelist** — bot silently ignores any sender other than the configured `TELEGRAM_CHAT_ID`.

---

## Current Status

| Item | Value |
|---|---|
| Mode | PAPER |
| Bankroll | $150 |
| Server | Online (Hetzner CX22) |
| PM2 process | apex-agent, id=7 |
| Scan interval | Every 15 minutes |
| Trades placed | 2 (paper) |
| Agent started | March 17, 2026 |
| Paper trading until | April 15, 2026 |

---

## Running Your Own

**Requirements**: Python 3.12+, Kalshi account + RSA key pair, Anthropic API key, Telegram bot token.

```bash
git clone https://github.com/LeeoniIsrael/apex-trading.git
cd apex-trading/src/kalshi

# Create virtualenv and install deps
python3 -m venv venv
source venv/bin/activate
pip install anthropic apscheduler python-telegram-bot requests cryptography python-dotenv

# Configure
cp .env.example .env
# Fill in: KALSHI_API_KEY_ID, KALSHI_PRIVATE_KEY_PATH,
#          ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
#          APEX_ENV=paper, APEX_BANKROLL=150

# Run
python apex_agent.py
```

**Kalshi API key setup**: Register at kalshi.com, generate an RSA key pair in your account settings, save the private key to a `.pem` file and the key ID to `.env`.

**Telegram bot setup**: Create a bot via [@BotFather](https://t.me/botfather), copy the token to `.env`, send a message to your bot and get your chat ID from `getUpdates`.

---

## Links

- **[Live Dashboard](https://apex-trading-apex.netlify.app)** — metrics, progress, trade feed
- **[Commit history](https://github.com/LeeoniIsrael/apex-trading/commits/main)** — daily build log

---

MIT License
