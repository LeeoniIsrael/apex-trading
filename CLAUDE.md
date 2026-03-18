# APEX — Autonomous Prediction EXchange

**Mission**: Build an autonomous AI agent that trades Kalshi prediction markets using Claude-powered analysis, Kelly Criterion position sizing, and RSA-authenticated API access. Running on Hetzner server at 178.156.159.178. Started March 3, 2026.

## The Three Pillars

| Pillar | What it is | Primary output |
|---|---|---|
| **Trading Agent** | Autonomous Claude-powered Kalshi prediction market trader | Live P&L, trades.log, DuckDB |
| **Research Paper** | Academic-grade writeup of strategy, methodology, results | `docs/paper/` (Quarto → PDF) |
| **Documentation Engine** | Daily auto-generated journal + public devlog | `docs/journal/` + published posts |

---

## Tech Stack

| Layer | Tool | Notes |
|---|---|---|
| Language | Python 3.12+ | |
| Package manager | `pip` + `venv` on server; `uv` locally | Server: `/opt/apex/venv/` |
| Market | Kalshi prediction markets | RSA API auth, paper + live modes |
| ML / analysis | Claude Haiku + web_search | `claude-haiku-4-5-20251001` per tick |
| Position sizing | Kelly Criterion | Fractional (0.25×), capped 5% per position |
| Agent loop | APScheduler | Scans every 15min; daily summary 9am ET |
| Infra | Hetzner VPS (178.156.159.178) | PM2 id=7, `/opt/apex/` |
| Notifications | Telegram bot | Startup, trades, daily P&L, errors |
| Database | DuckDB | Single file `data/apex.duckdb` |
| Paper + journal | Quarto (`.qmd`) | `docs/paper/` and `docs/journal/` |
| Config | `.env` file | All secrets via env vars, never hardcoded |
| Testing | pytest | `tests/` mirrors `src/` |

---

## Project Structure

```
apex-trading/
├── CLAUDE.md
├── pyproject.toml          # uv-managed dependencies (local dev)
├── .env                    # secrets (gitignored)
├── src/
│   ├── kalshi/             # Kalshi prediction market agent (deployed to /opt/apex/)
│   │   ├── apex_agent.py   # Main agent — APScheduler entry point
│   │   ├── brain.py        # Claude Haiku reasoning + web_search
│   │   ├── kalshi_client.py# Kalshi REST API v2 with RSA auth
│   │   ├── kelly.py        # Kelly Criterion position sizer
│   │   └── telegram_notify.py # Telegram alerts
│   ├── strategy/           # Legacy equity strategy code (archived)
│   ├── data/               # Data ingestion utilities
│   └── backtest/           # vectorbt backtest scripts
├── docs/
│   ├── paper/              # Quarto research paper
│   │   └── index.qmd
│   ├── journal/            # Daily auto-generated trading journals
│   │   └── YYYY-MM-DD.qmd
│   └── dashboard/          # Public-facing project dashboard (Netlify)
│       └── index.html
├── data/
│   └── apex.duckdb         # All market + trade data (gitignored if large)
├── tests/
└── notes/                  # Raw scratch notes, ideas
```

---

## Trading Phases

| Phase | Period | Mode | Focus |
|---|---|---|---|
| **Build** | March 3–31 | Development | Scaffold, backtest, validate strategies — no agent running |
| **Paper trade** | April 1–14 | Alpaca paper | Agent starts midnight April 1; autonomous paper trading |
| **Live** | April 15–30 | Alpaca live | Real money with $150 hard cap; only if paper phase is profitable |
| **Wrap-up** | May 1 | Agent stops | Final P&L, paper completion, devlog conclusion |

**Rule**: A strategy may not enter paper trading until it has a positive Sharpe ratio (>0.5) over 1+ year of backtested data.

**Rule**: The agent may not enter live trading without explicit user confirmation (`/approve-live` or direct message).

---

## Daily Workflow

Each session should follow this order:

1. **Read** `docs/journal/` — review yesterday's decisions and signals
2. **Code** — implement strategy changes, features, or agent improvements
3. **Test** — run pytest before committing anything
4. **Journal** — generate or update today's `.qmd` journal entry with findings
5. **Paper** — update relevant paper sections if a significant finding emerged
6. **Checkpoint** — run `/checkpoint` to save session state

---

## Agent Behavior Rules

- The agent (`src/kalshi/apex_agent.py`) runs on Hetzner at `/opt/apex/` via PM2 (id=7)
- Scans top 20 Kalshi markets by volume every 15 minutes
- Filters: volume ≥ 1000 contracts, 1 hour to 7 days until close
- Claude Haiku uses `web_search` to find recent news before deciding
- Only trades if edge > 5% AND confidence > 60%
- Position sizing: fractional Kelly (0.25×), hard cap 5% per position
- Max 10 open positions simultaneously
- Every trade logged to `/opt/apex/trades.log` + Telegram alert
- Daily P&L summary sent to Telegram at 9am ET
- **Paper mode by default** (`APEX_ENV=paper`); live requires manual env change + user confirmation

---

## Research Paper Guidelines

- Paper lives in `docs/paper/index.qmd` (Quarto)
- Structure: Abstract → Introduction → Methodology → Strategy Design → Results → Discussion → Conclusion
- Update the paper whenever: a strategy is backtested, paper trading begins, a major trade event occurs
- All charts/figures are generated from DuckDB queries embedded in `.qmd` code blocks
- Target audience: quantitative finance practitioners and ML researchers
- Citation style: APA

---

## Documentation Engine Guidelines

- Daily journal entries are auto-generated in `docs/journal/YYYY-MM-DD.qmd`
- Each entry contains: date, market summary, agent decisions, P&L, signals fired, notable observations
- Public devlog posts (for Substack or GitHub Pages) are derived from journal entries — written in plain language, not academic
- Never publish API keys, specific position sizes, or broker account details publicly

---

## Key Commands

| Command | When to use |
|---|---|
| `/plan` | Before implementing any new strategy or agent feature |
| `/tdd` | When writing strategy logic — tests first |
| `/checkpoint` | At the end of every session |
| `/learn` | After any breakthrough or solved problem |
| `/verify` | Before pushing changes that affect the live agent |
| `/code-review` | After writing any execution or order management code |

---

## Standing Orders

These two commands run after **every meaningful milestone** — not just at end of session:

1. **Update + deploy the dashboard**
   ```bash
   # Edit docs/dashboard/index.html with latest progress/metrics/checklist, then:
   netlify deploy --dir=docs/dashboard --prod
   ```

2. **Commit and push to GitHub**
   ```bash
   git add . && git commit -m '[descriptive message]' && git push origin main
   ```

These are non-negotiable. Every step completion = dashboard update + deploy + commit + push.

---

## Hard Rules (Never Break)

1. **No secrets in code** — all API keys in `.env`, never hardcoded
2. **No live trades without user confirmation** — agent can *recommend* but cannot *execute* live without approval
3. **Never touch `/opt/agency/`** — existing production processes there are off-limits
4. **No CSV for market data** — DuckDB only
5. **No skipping tests** — `pytest` must pass before any commit
6. **No modifying `src/kalshi/kalshi_client.py` without running `/verify` first** — this touches real/paper orders

---

## Server Setup (Hetzner)

- **IP**: `178.156.159.178` | SSH key: `~/.ssh/hetzner_apex`
- **Agent dir**: `/opt/apex/`
- **PM2 process**: `apex-agent` (id=7)
- **Protected**: `/opt/agency/` — **DO NOT TOUCH**
- **Logs**: `pm2 logs apex-agent` or `/opt/apex/apex.log`
- **RSA key**: paste manually into `/opt/apex/kalshi_private.pem` (chmod 600)

---

## Environment Variables (`/opt/apex/.env` on server)

```
ANTHROPIC_API_KEY=<from /opt/agency/.env>
KALSHI_API_KEY_ID=0034df3c-deb3-4b5d-be5c-7b478dec0c1b
KALSHI_PRIVATE_KEY_PATH=/opt/apex/kalshi_private.pem
TELEGRAM_BOT_TOKEN=8661730224:AAEJxtepO1OlVu8S4C7VH3L9iqiuGjcE5qw
TELEGRAM_CHAT_ID=7926373806
APEX_ENV=paper                  # change to 'live' only with explicit approval
APEX_BANKROLL=150.00
KELLY_FRACTION=0.25
MAX_POSITION_PCT=0.05
```

---

## Relevant Skills

For deep reference during this project, these `.claude/skills/` are most applicable:

- `python-patterns` — Pythonic idioms and best practices
- `python-testing` — pytest patterns
- `backend-patterns` — APScheduler, service layer design
- `cost-aware-llm-pipeline` — Claude API cost optimization for the agent loop
- `autonomous-loops` — Architecture for the trading agent's decision loop
- `article-writing` — Devlog and paper writing
- `market-research` — When researching new strategies or market regimes
- `database-migrations` — If DuckDB schema evolves mid-experiment

---

## Experiment Timeline

- **Build period**: March 3–31, 2026 — development, backtesting, strategy validation
- **Agent starts**: April 1, 2026 at midnight — paper trading begins
- **Live trading decision**: April 15, 2026 — go live ($150 cap) only if paper phase profitable
- **Agent stops**: May 1, 2026 at midnight — 30-day experiment concludes
- **Paper submission**: ~May 8, 2026 (1 week post-experiment)
