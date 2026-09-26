"""Volume breakout: multi-week high on 2-3x normal volume with high delivery %.

Signal is computed at day t's close. Entry is day t+1's open (after the morning brief).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import VolumeBreakoutConfig

NAME = "volume_breakout"


def add_features(df: pd.DataFrame, cfg: VolumeBreakoutConfig = VolumeBreakoutConfig()) -> pd.DataFrame:
    df = df.sort_values(["symbol", "date"]).copy()
    g = df.groupby("symbol", sort=False)
    # Prior-N-day high, excluding today.
    df["prior_high"] = g["high"].transform(lambda s: s.shift(1).rolling(cfg.high_lookback, min_periods=cfg.high_lookback).max())
    df["normal_volume"] = g["volume"].transform(
        lambda s: s.shift(1).rolling(cfg.volume_lookback, min_periods=cfg.volume_lookback).median()
    )
    df["avg_deliv_pct"] = g["deliv_pct"].transform(
        lambda s: s.shift(1).rolling(cfg.volume_lookback, min_periods=cfg.volume_lookback // 2).mean()
    )
    prev_close = g["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1
    ).max(axis=1)
    df["atr"] = tr.groupby(df["symbol"], sort=False).transform(
        lambda s: s.rolling(cfg.atr_window, min_periods=cfg.atr_window).mean()
    )
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    df["close_location"] = ((df["close"] - df["low"]) / rng).fillna(1.0)
    df["volume_ratio"] = df["volume"] / df["normal_volume"]
    return df


def signals(df: pd.DataFrame, tradable: pd.Series, cfg: VolumeBreakoutConfig = VolumeBreakoutConfig()) -> pd.DataFrame:
    """Rows of `df` (with features) where the breakout fired on a tradable stock."""
    deliv = df["deliv_pct"]
    has_deliv = deliv.notna().any()
    deliv_ok = (deliv >= cfg.min_deliv_pct) & (deliv >= cfg.deliv_rel_mult * df["avg_deliv_pct"].fillna(0))
    fired = (
        tradable.reindex(df.index).fillna(False).astype(bool)
        & (df["close"] > df["prior_high"])
        & (df["volume_ratio"] >= cfg.volume_mult)
        & (df["close_location"] >= cfg.min_close_location)
        & df["atr"].notna()
        & (deliv_ok if has_deliv else True)
    )
    out = df.loc[fired, ["date", "symbol", "close", "atr", "volume_ratio", "deliv_pct", "prior_high"]].copy()
    out["playbook"] = NAME
    out["score"] = out["volume_ratio"]  # rank candidates when capital is short
    return out.reset_index(drop=True)
