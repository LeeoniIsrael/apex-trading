# APEX — Autonomous Predictive Equity eXperiment

**Mission**: Build a 30-day autonomous algorithmic trading system, document it as a research paper, and publish the journey as a public devlog. Started March 3, 2026.

## The Three Pillars

| Pillar | What it is | Primary output |
|---|---|---|
| **Trading Agent** | Autonomous Claude-powered equity trading system | Live P&L, trade logs, DuckDB |
| **Research Paper** | Academic-grade writeup of strategy, methodology, results | `docs/paper/` (Quarto → PDF) |
| **Documentation Engine** | Daily auto-generated journal + public devlog | `docs/journal/` + published posts |

---

## Tech Stack

| Layer | Tool | Notes |
|---|---|---|
| Language | Python 3.12+ | |
| Package manager | `uv` | Use `uv add`, `uv run`, never raw `pip` |
| Broker | Alpaca Markets | Paper until day 22; live (with hard cap) days 22–28 |
| Backtesting | `vectorbt` | All backtests live in `src/backtest/` |
| Database | DuckDB | Single file `data/apex.duckdb`; no CSV for market data |
| ML signals | LightGBM + scikit-learn | Features in `src/strategy/features.py` |
| Agent loop | Claude API (`claude-haiku-4-5`) + APScheduler | Cheap + fast for autonomous ticks |
| Dashboard | Streamlit | `src/dashboard/app.py` |
| Paper + journal | Quarto (`.qmd`) | `docs/paper/` and `docs/journal/` |
| Config | Pydantic Settings | All secrets via `.env`, never hardcoded |
| Testing | pytest | `tests/` mirrors `src/` |

---

## Project Structure

```
apex-trading/
├── CLAUDE.md
├── pyproject.toml          # uv-managed dependencies
├── .env                    # secrets (gitignored)
├── src/
│   ├── agent/              # Autonomous trading loop
│   │   ├── loop.py         # APScheduler entry point
│   │   ├── brain.py        # Claude API reasoning layer
│   │   └── executor.py     # Alpaca order execution
│   ├── strategy/           # Strategy definitions
│   │   ├── momentum.py
│   │   ├── mean_reversion.py
│   │   └── features.py     # Feature engineering (LightGBM inputs)
│   ├── data/               # Data ingestion
│   │   ├── market.py       # Alpaca market data → DuckDB
│   │   └── schema.py       # DuckDB schema definitions
│   ├── backtest/           # vectorbt backtest scripts
│   └── dashboard/          # Streamlit monitoring app
│       └── app.py
├── docs/
│   ├── paper/              # Quarto research paper
│   │   └── index.qmd
│   └── journal/            # Daily auto-generated trading journals
│       └── YYYY-MM-DD.qmd
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

- The trading agent (`src/agent/`) runs on a schedule via APScheduler during market hours
- Every trade decision MUST be logged with Claude's reasoning to DuckDB
- The agent uses `claude-haiku-4-5` for tick-level decisions (cost efficiency)
- The agent uses `claude-sonnet-4-6` only for end-of-day analysis
- Position sizing follows Kelly Criterion (fractional, capped at 5% per position)
- The agent never exceeds 10 open positions simultaneously
- **Stop-loss is mandatory**: every position gets a 2% trailing stop

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

## Hard Rules (Never Break)

1. **No secrets in code** — all API keys in `.env` via Pydantic Settings
2. **No live trades without user confirmation** — agent can *recommend* but cannot *execute* live without approval
3. **No strategy goes live without a backtest** — Sharpe > 0.5 required
4. **No CSV for market data** — DuckDB only
5. **No `pip install`** — use `uv add`
6. **No skipping tests** — `pytest` must pass before any commit
7. **No modifying `src/agent/executor.py` without running `/verify` first** — this touches real/paper orders

---

## Environment Variables (`.env`)

```
ALPACA_API_KEY=
ALPACA_SECRET_KEY=
ALPACA_BASE_URL=https://paper-api.alpaca.markets   # switch to https://api.alpaca.markets on April 15
ANTHROPIC_API_KEY=
APEX_LIVE_CAP_USD=150                              # hard cap for live phase (April 15–30)
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
