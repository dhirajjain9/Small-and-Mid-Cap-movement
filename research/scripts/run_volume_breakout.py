"""Run the volume-breakout backtest and print in-sample vs out-of-sample results.

    python scripts/run_volume_breakout.py --cache data/bhav
    python scripts/run_volume_breakout.py --synthetic      # dry run, no data needed
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nsesig.backtest import run_volume_breakout  # noqa: E402
from nsesig.data import load_cache  # noqa: E402
from nsesig.synthetic import make_panel  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data/bhav")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--delist-haircut", type=float, default=0.0)
    a = ap.parse_args()
    panel = make_panel() if a.synthetic else load_cache(a.cache)
    r = run_volume_breakout(panel, delist_haircut=a.delist_haircut)
    report = {
        "playbook": r.playbook,
        "target_pct (calibrated in-sample)": round(r.target_pct, 4),
        "verdict (out-of-sample)": r.verdict,
    }
    for p in (r.in_sample, r.out_of_sample):
        report[p.name] = {"events": p.events, "trades": p.trades, "pre_tax": p.pre_tax, "post_tax": p.post_tax}
    print(json.dumps(report, indent=2, default=float))
