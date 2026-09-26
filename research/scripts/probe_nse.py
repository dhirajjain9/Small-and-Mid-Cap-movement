"""Check that NSE archives are reachable from this machine and the file formats still parse.

    python scripts/probe_nse.py --date 2024-06-14
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import requests  # noqa: E402

from nsesig.data import HEADERS, fetch_day  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2024-06-14")
    a = ap.parse_args()
    s = requests.Session()
    try:
        r = s.get("https://www.nseindia.com", headers=HEADERS, timeout=30)
        print("nseindia.com homepage:", r.status_code)
    except Exception as e:
        print("nseindia.com homepage failed:", e)
    df = fetch_day(pd.Timestamp(a.date), s)
    if df is None:
        sys.exit(f"no bhavcopy for {a.date} (404)")
    eq = df[df["series"] == "EQ"]
    print(f"{len(df)} rows, {len(eq)} EQ; band known for {eq['band'].notna().mean():.0%}; "
          f"delivery % known for {eq['deliv_pct'].notna().mean():.0%}")
    print(eq.sort_values("turnover", ascending=False).head(5).to_string(index=False))
    if eq.empty or eq["close"].isna().all():
        sys.exit("parsed file looks wrong")
