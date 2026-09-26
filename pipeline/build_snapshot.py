"""Build the screener's daily data files from the bhavcopy cache.

    python pipeline/build_snapshot.py --cache research/data/bhav --out web/public/data

Writes stocks.json (one row per listed equity) and meta.json (as-of date, counts).
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from nsesig.data import load_cache  # noqa: E402
from nsesig.listings import fetch_nse_lists, load_bse_master  # noqa: E402
from nsesig.screener import build_snapshot, fetch_reference  # noqa: E402
from nsesig.synthetic import make_panel  # noqa: E402


def to_json_records(df: pd.DataFrame) -> list[dict]:
    recs = []
    for r in df.to_dict("records"):
        clean = {}
        for k, v in r.items():
            if isinstance(v, (float, np.floating)):
                v = None if not np.isfinite(v) else round(float(v), 4)
            elif isinstance(v, (np.integer,)):
                v = int(v)
            elif isinstance(v, (np.bool_,)):
                v = bool(v)
            elif v is pd.NaT or (not isinstance(v, (list, str, bool, int)) and v is not None and pd.isna(v)):
                v = None
            clean[k] = v
        recs.append(clean)
    return recs


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=str(ROOT / "research/data/bhav"))
    ap.add_argument("--bse-cache", default=str(ROOT / "research/data/bse"))
    ap.add_argument("--ref-dir", default=str(ROOT / "data/reference"), help="optional BSE 'List of Scrips' exports")
    ap.add_argument("--out", default=str(ROOT / "web/public/data"))
    ap.add_argument("--synthetic", action="store_true", help="fake data, no network (for local UI work)")
    a = ap.parse_args()

    if a.synthetic:
        snap, meta = build_snapshot(make_panel(n_symbols=60, start="2025-01-01"))
    else:
        start = (pd.Timestamp.today() - pd.Timedelta(days=500)).strftime("%Y-%m-%d")
        nse = load_cache(a.cache, start=start, series=("EQ", "BE", "BZ", "SM", "ST", "SZ"), log=True)
        try:
            bse = load_cache(a.bse_cache, start=start, series=None)
        except FileNotFoundError:
            print("no BSE bhavcopies cached; BSE-only companies will be missing")
            bse = None
        refs, nse_lists, bse_master = fetch_reference(), fetch_nse_lists(), load_bse_master(a.ref_dir)
        print("reference lists:", {k: len(v) for k, v in refs.items()},
              "| NSE listings:", nse_lists["segment"].value_counts().to_dict(),
              "| BSE master rows:", len(bse_master), "| BSE scrips traded:", 0 if bse is None else bse["isin"].nunique())
        snap, meta = build_snapshot(nse, refs, bse_panel=bse, nse_lists=nse_lists, bse_master=bse_master)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "stocks.json").write_text(json.dumps(to_json_records(snap), separators=(",", ":")))
    (out / "meta.json").write_text(json.dumps(meta, indent=2))
    print(json.dumps(meta))
    print(snap.drop(columns="spark").head(10).to_string())
