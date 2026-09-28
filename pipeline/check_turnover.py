"""Cross-check one stock's volume and turnover: NSE bhavcopy vs BSE bhavcopy vs Yahoo.

    python pipeline/check_turnover.py TTKPRESTIG 517506
"""
import io
import sys
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))
from nsesig.data import BSE_BHAV, BSE_HEADERS, HEADERS  # noqa: E402

sym, bse_code = sys.argv[1], sys.argv[2]
s = requests.Session()
rows = []
for day in pd.bdate_range(end="2026-09-25", periods=22):
    r = {"date": day.date()}
    x = s.get(f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{day:%d%m%Y}.csv", headers=HEADERS, timeout=30)
    if x.status_code == 200:
        df = pd.read_csv(io.StringIO(x.text), skipinitialspace=True)
        df.columns = [c.strip() for c in df.columns]
        m = df[(df["SYMBOL"].str.strip() == sym) & (df["SERIES"].str.strip() == "EQ")]
        if len(m):
            r.update(nse_close=float(m["CLOSE_PRICE"].iloc[0]), nse_qty=int(m["TTL_TRD_QNTY"].iloc[0]),
                     nse_turnover_cr=float(m["TURNOVER_LACS"].iloc[0]) / 100, nse_trades=int(m["NO_OF_TRADES"].iloc[0]))
    y = s.get(BSE_BHAV.format(d=f"{day:%Y%m%d}"), headers=BSE_HEADERS, timeout=30)
    if y.status_code == 200 and "TradDt" in y.text[:300]:
        df = pd.read_csv(io.StringIO(y.text), low_memory=False)
        m = df[df["FinInstrmId"].astype(str) == bse_code]
        if len(m):
            r.update(bse_qty=int(m["TtlTradgVol"].iloc[0]), bse_turnover_cr=float(m["TtlTrfVal"].iloc[0]) / 1e7)
    rows.append(r)
t = pd.DataFrame(rows).dropna(subset=["nse_close"])

import yfinance as yf  # noqa: E402

h = yf.Ticker(f"{sym}.NS").history(start=str(t["date"].min()), end="2026-09-26", auto_adjust=False)
h.index = h.index.tz_localize(None).date
t["yahoo_nse_qty"] = t["date"].map(h["Volume"])
t["yahoo_nse_close"] = t["date"].map(h["Close"])
t["combined_turnover_cr"] = t["nse_turnover_cr"] + t.get("bse_turnover_cr", 0)
pd.set_option("display.width", 200)
print(t.to_string(index=False))
print("\n20-day averages (Rs cr): NSE", round(t["nse_turnover_cr"].tail(20).mean(), 2),
      "| BSE", round(t.get("bse_turnover_cr", pd.Series(dtype=float)).tail(20).mean(), 2),
      "| NSE+BSE", round(t["combined_turnover_cr"].tail(20).mean(), 2))
