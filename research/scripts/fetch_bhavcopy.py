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
    ap.add_argument("--max-errors", type=int, default=None, help="exit non-zero if more days than this failed")
    a = ap.parse_args()
    stats = build_cache(a.start, a.end, a.cache, a.pause)
    if a.max_errors is not None and stats["errors"] > a.max_errors:
        sys.exit(f"{stats['errors']} days failed to download")
