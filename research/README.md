# /research

Build step 1: the tradability filter and the volume-breakout backtest.

```
pip install -r requirements.txt
python -m pytest -q tests                              # rule tests, no network
python scripts/run_volume_breakout.py --synthetic      # dry run on fake data
python scripts/fetch_bhavcopy.py --start 2018-01-01 --end 2026-06-30 --cache data/bhav
python scripts/run_volume_breakout.py --cache data/bhav
```

The notebook `notebooks/01_volume_breakout.ipynb` covers the same ground plus a sensitivity grid.

## Layout

| File | Job |
|---|---|
| `nsesig/config.py` | All thresholds, the cost model, portfolio caps and the kill bar |
| `nsesig/data.py` | NSE bhavcopy + price-band download/parse, split/bonus adjustment, yfinance fallback |
| `nsesig/universe.py` | Tradability filter; circuit-lock detection |
| `nsesig/playbooks/volume_breakout.py` | Features and trigger |
| `nsesig/backtest.py` | Trade simulation, sizing, STCG, stats, kill-bar verdict |
| `nsesig/synthetic.py` | Fake bhavcopy-shaped panel for tests |

## How the backtest follows the rules

- **Split:** the target (median favourable move) is calibrated on signals up to 2022-12-31 only. The verdict is judged on 2023+ only.
- **Survivorship:** the bhavcopy lists every stock traded each day, delisted ones included. A stock that stops trading while held exits at its last close minus `--delist-haircut`.
- **Costs:** slippage (0.75% round trip by default), brokerage, STT, exchange, SEBI, stamp duty, GST, DP charges. STCG at 20% on each financial year's net gains, with losses carried forward.
- **Circuits:** a stock opening at its upper circuit on entry day is skipped. A day locked at the lower circuit carries the exit to the next tradeable open.
- **Fills:** entry at the next open after the signal. Gaps fill at the open. When stop and target fall in the same bar, the stop counts.
- **Sizing:** risk 1% of equity to the stop, capped by max position %, 1% of ADTV, max positions, gross exposure and (with a sector map) per-sector count.

## Known gaps

- **Price bands:** `sec_list_DDMMYYYY.csv` may not exist for older dates. Where the band is unknown, the band filter passes that row and circuit detection falls back to the 2% minimum.
- **ASM/GSM:** NSE publishes only the current lists. Build a history by saving them daily from now on, then `attach_surveillance`.
- **Market cap:** needs a shares-outstanding history (`attach_mcap`); until then the ₹1,000 cr check is skipped with a warning.
- **Equity curve:** realised P&L only, booked on exit dates, so intra-trade drawdowns are understated.
- **Data availability:** the NSE download code is untested against the live site; it was written without network access. Check the first day's file by eye.
