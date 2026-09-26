"""Download NSE bhavcopies (prices + delivery %) and price bands into a local cache.

    python scripts/fetch_bhavcopy.py --start 2018-01-01 --end 2026-06-30 --cache data/bhav

Re-runs skip days already cached. NSE rate-limits aggressively; keep --pause >= 0.5.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nsesig.data import build_cache  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--cache", default="data/bhav")
    ap.add_argument("--pause", type=float, default=0.5)
    a = ap.parse_args()
    build_cache(a.start, a.end, a.cache, a.pause)
