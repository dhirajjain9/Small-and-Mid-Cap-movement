"""Write every volume-breakout signal and trade from --start onwards to CSV.

    python scripts/export_trades.py --cache data/bhav --start 2023-01-01 --out out

Files:
  signals.csv          each signal and how the stock moved 1/5/20 days later
  trades.csv           each signal traded by the rules (entry, stop, target, exit, net return)
  portfolio_trades.csv the trades actually taken under the capital and position limits, with ₹ P&L
  monthly.csv          signals, trades, hit rate, average net return and ₹ P&L by month
"""
import argparse
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from nsesig.backtest import run_volume_breakout  # noqa: E402
from nsesig.data import load_cache  # noqa: E402
from nsesig.synthetic import make_panel  # noqa: E402

warnings.simplefilter("ignore")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data/bhav")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--out", default="out")
    a = ap.parse_args()
    start = pd.Timestamp(a.start)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    panel = make_panel() if a.synthetic else load_cache(a.cache)
    r = run_volume_breakout(panel, test_start=start)

    sig = r.signals[r.signals["date"] >= start].sort_values("date")
    sig_cols = ["date", "symbol", "close", "volume_ratio", "deliv_pct", "prior_high", "fwd_1d", "fwd_5d", "fwd_20d", "mfe"]
    sig[[c for c in sig_cols if c in sig]].round(4).to_csv(out / "signals.csv", index=False)

    tr = r.trades[(r.trades["signal_date"] >= start) & (r.trades["status"] == "filled")].sort_values("signal_date")
    tr_cols = ["signal_date", "symbol", "entry_date", "entry_px", "stop", "target", "exit_date", "exit_px",
               "exit_reason", "bars_held", "gross_ret", "net_ret", "mfe", "mae"]
    tr[tr_cols].round(4).to_csv(out / "trades.csv", index=False)

    log = r.out_of_sample.trade_log
    if len(log):
        log_cols = ["signal_date", "symbol", "entry_date", "entry_px", "exit_date", "exit_reason", "value", "gross_ret", "pnl"]
        log[log_cols].round(2).to_csv(out / "portfolio_trades.csv", index=False)

    m_sig = sig.groupby(sig["date"].dt.to_period("M")).agg(signals=("symbol", "size"), median_5d=("fwd_5d", "median"),
                                                           median_20d=("fwd_20d", "median"))
    m_tr = tr.groupby(tr["signal_date"].dt.to_period("M")).agg(
        trades=("symbol", "size"), hit_rate=("net_ret", lambda x: (x > 0).mean()), avg_net_ret=("net_ret", "mean"))
    monthly = m_sig.join(m_tr, how="outer")
    if len(log):
        monthly = monthly.join(log.groupby(log["exit_date"].dt.to_period("M"))["pnl"].sum().rename("portfolio_pnl"), how="outer")
    monthly.index.name = "month"
    monthly.round(4).to_csv(out / "monthly.csv")

    yearly = tr.groupby(tr["signal_date"].dt.year).agg(
        trades=("symbol", "size"), hit_rate=("net_ret", lambda x: (x > 0).mean()),
        avg_net_ret=("net_ret", "mean"), median_net_ret=("net_ret", "median"))
    print(f"signals since {start.date()}: {len(sig)}, trades: {len(tr)}, target used: {r.target_pct:.2%}\n")
    print(yearly.round(4).to_string())
    print("\nexit reasons:", tr["exit_reason"].value_counts().to_dict())
    print(f"\nlast 20 trades:\n{tr[tr_cols].tail(20).round(3).to_string(index=False)}")
