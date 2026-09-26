"""Refresh EPS and shares outstanding from Yahoo for every company in the screener.

    python pipeline/fetch_fundamentals.py

Reads the company list from web/public/data/stocks.json and updates
data/fundamentals/yahoo.csv. Resumable: companies fetched in the last 6 days are skipped.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

import pandas as pd  # noqa: E402

from nsesig.fundamentals import refresh_yahoo, yahoo_symbols  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--stocks", default=str(ROOT / "web/public/data/stocks.json"))
    ap.add_argument("--out", default=str(ROOT / "data/fundamentals/yahoo.csv"))
    ap.add_argument("--max-minutes", type=float, default=80)
    ap.add_argument("--limit", type=int, default=None, help="only the first N companies (for testing)")
    a = ap.parse_args()
    stocks = pd.DataFrame(json.loads(Path(a.stocks).read_text()))
    # most-traded first, so a run cut short still covers the companies that matter most
    stocks = stocks.sort_values("adtv_cr", ascending=False, na_position="last")
    targets = yahoo_symbols(stocks)
    if a.limit:
        targets = targets.head(a.limit)
    refresh_yahoo(targets, a.out, max_minutes=a.max_minutes)
