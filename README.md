# NSE Small/Mid-Cap Signal System

Personal tool to find, enter and exit trades in volatile NSE small and mid caps, using rules that are backtested before any capital goes in. Not investment advice; for personal use only. Sharing buy or sell calls with others would fall under SEBI research analyst rules.

## Core idea

The system's first job is keeping me out of bad trades; its second is finding good ones. Every signal is a pre-written rule with historical odds, never a black-box "buy". A playbook goes live only after it survives realistic costs in an out-of-sample backtest.

## Status

| Step | State |
|---|---|
| 1. `/research`: tradability filter + volume-breakout backtest | Done. Volume breakout failed the kill bar on real data (2019–2026) |
| 2. `/pipeline`: Actions jobs + Telegram brief | Not started |
| 3. Regime filter + other playbooks | Not started |
| 4. `/web`: screener on Vercel | First version: every NSE stock, market data only, refreshed daily by `.github/workflows/daily.yml` |

## Architecture

| Piece | Runs on | Job |
|---|---|---|
| Code | GitHub (private repo) | Everything |
| Data jobs | GitHub Actions (cron, UTC) | 8:30 AM IST (`0 3 * * 1-5`): regime check and signals. 4 PM IST (`30 10 * * 1-5`): prices and outcome logging. Weekly: fundamentals |
| Database | Supabase | prices, universe, signals, trades, journal |
| Alerts | Telegram, sent from Actions | Morning brief; stop and target alerts |
| Dashboard | Vercel (Next.js, root = `/web`) | Signals, positions, live vs backtest performance per playbook |

```
/pipeline   Python jobs (fetch, filter, signal, alert)
/research   Jupyter notebooks (backtests)
/web        Next.js dashboard
```

## Data sources

- **NSE bhavcopy:** daily prices plus delivery %.
- **NSE reports:** circuit bands, ASM/GSM lists, bulk/block deals, corporate announcements, FII/DII flows, India VIX.
- **yfinance (`.NS` tickers):** fallback for price history.
- **Screener.in or Tickertape:** fundamentals, refreshed weekly.
- **Broker API (Kite Connect or other):** holdings sync only. Needs a manual daily login, so it can't run in scheduled jobs.

## 1. Tradability filter (runs first)

A stock stays in the universe only if it passes all of these. Thresholds are placeholders to tune.

- Average daily traded value ≥ ₹15–25 cr, and my position is ≤ 1–2% of it.
- Price band of 20% or no band (exclude 5% and 10% circuit stocks).
- Not on the ASM or GSM lists.
- Market cap ≥ ₹1,000 cr.

## 2. Market regime filter (the role of macro)

Risk-on or risk-off, from:

- Nifty Smallcap 250 vs its 50-day moving average
- India VIX
- FII flow trend
- Advance/decline breadth

In risk-off, cut position sizes or take no new trades.

## 3. Playbooks (signals)

Each playbook defines a trigger, its historical 1/5/20-day outcome and hit rate, and rules for entry, target (the historical median move), stop and time stop.

- **Volume breakout:** multi-week high on 2–3x normal volume with high delivery %. *Build this first.*
- **Earnings surprise:** strong results, followed by post-results drift.
- **Smart money:** institutional bulk or block deals, promoter buying.
- **Sector momentum:** strongest stocks in a theme that is moving together.

## 4. Risk rules

- Size each trade by volatility, risking about 1% of capital per trade.
- Every trade has a stop loss and a time stop.
- Cap total small-cap exposure and exposure to any single sector.
- Keep a journal entry per trade: why I entered, and what would make me exit.

## Backtest rules

- Period 2018–2026 (covers the COVID crash, the 2021–24 run and later corrections).
- Build on data up to 2022; validate on 2023–26 only.
- Include delisted and collapsed stocks, to avoid survivorship bias.
- Costs: 0.5–1% slippage per round trip, plus brokerage, STT and 20% STCG.
- A stock at its circuit limit is assumed untradeable that day.
- Kill any playbook whose net return, after costs, doesn't clear the bar.

## Build order

1. `/research`: tradability filter plus volume-breakout backtest.
2. `/pipeline`: move the surviving playbooks to GitHub Actions; add the Telegram brief.
3. Add the regime filter and the other playbooks.
4. `/web`: dashboard on Vercel.

## Notes

- Keep the repo private. Secrets go in GitHub Secrets and Vercel env vars.
- To decide: capital per trade. It sets the liquidity floor.
