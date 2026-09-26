"""Synthetic bhavcopy-shaped panel for tests and dry runs (no network needed)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import CRORE


def make_panel(n_symbols: int = 30, start: str = "2018-01-01", end: str = "2026-06-30", seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, end)
    frames = []
    for s in range(n_symbols):
        n = len(dates)
        ret = rng.normal(0.0004, 0.022, n)
        vol_mult = np.ones(n)
        # occasional breakout days: big up move on heavy volume, with some drift after
        for d in rng.choice(np.arange(80, n - 30), size=max(n // 250, 1), replace=False):
            ret[d] = abs(ret[d]) + 0.06
            vol_mult[d] = 3.5
            ret[d + 1 : d + 11] += 0.004
        close = 200 * np.exp(np.cumsum(ret))
        prev = np.r_[close[0], close[:-1]]
        open_ = prev * (1 + rng.normal(0, 0.005, n))
        high = np.maximum(open_, close) * (1 + abs(rng.normal(0, 0.008, n)))
        low = np.minimum(open_, close) * (1 - abs(rng.normal(0, 0.008, n)))
        high = np.where(vol_mult > 1, np.maximum(high, close * 1.002), high)
        low = np.where(vol_mult > 1, np.minimum(low, open_), low)
        volume = rng.lognormal(np.log(40 * CRORE), 0.3, n) * vol_mult / close  # ~₹40 cr a day
        deliv = np.clip(rng.normal(40, 8, n) + (vol_mult > 1) * 20, 5, 95)
        frames.append(pd.DataFrame({
            "date": dates, "symbol": f"SYM{s:02d}", "series": "EQ",
            "open": open_, "high": high, "low": low, "close": close, "prev_close": prev,
            "volume": volume, "turnover": volume * close, "deliv_pct": deliv,
            "band": 20.0, "mcap_cr": 5_000.0, "surveillance": False,
        }))
    df = pd.concat(frames, ignore_index=True)
    return df
