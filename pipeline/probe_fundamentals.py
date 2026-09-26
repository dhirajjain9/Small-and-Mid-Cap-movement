"""Check sources for shares outstanding and EPS.

    python pipeline/probe_fundamentals.py
"""
import io
import sys
import time
import zipfile
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))
from nsesig.data import HEADERS  # noqa: E402

s = requests.Session()

print("=== NSE PR zip (market cap file)")
for d in ("250926", "240926"):
    url = f"https://nsearchives.nseindia.com/archives/equities/bhavcopy/pr/PR{d}.zip"
    r = s.get(url, headers=HEADERS, timeout=60)
    print(url, r.status_code, len(r.content))
    if r.status_code == 200 and r.content[:2] == b"PK":
        z = zipfile.ZipFile(io.BytesIO(r.content))
        print("files:", z.namelist())
        for n in z.namelist():
            if n.lower().startswith("mcap"):
                df = pd.read_csv(io.BytesIO(z.read(n)))
                print(n, df.shape, list(df.columns))
                print(df.head(5).to_string())
                print(df[df.iloc[:, 0].astype(str).str.strip().isin(["RELIANCE", "TCS", "MCX"])].to_string())
        break

print("\n=== Yahoo via yfinance")
import yfinance as yf  # noqa: E402

for t in ("RELIANCE.NS", "MCX.NS", "500325.BO", "ABB.BO"):
    try:
        info = yf.Ticker(t).info
        print(t, {k: info.get(k) for k in ("sharesOutstanding", "marketCap", "trailingEps", "trailingPE", "currency")})
    except Exception as e:
        print(t, "failed:", repr(e))
    time.sleep(1)

print("\n=== Yahoo batch (yf.Tickers fast path)")
try:
    t0 = time.time()
    syms = ["RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ITC.NS", "SBIN.NS", "LT.NS", "MCX.NS", "BSE.NS", "IRCTC.NS"]
    ok = 0
    for sym in syms:
        i = yf.Ticker(sym).info
        ok += i.get("trailingEps") is not None
    print(f"{ok}/{len(syms)} with EPS in {time.time() - t0:.1f}s")
except Exception as e:
    print("batch failed:", repr(e))
