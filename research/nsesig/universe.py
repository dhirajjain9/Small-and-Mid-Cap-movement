"""Tradability filter (runs first) and circuit-limit detection.

Everything here is point-in-time: the value on day t uses data up to and including
day t's close, which is known before the next morning's 8:30 brief.
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from .config import CRORE, UniverseConfig

_TOL = 1e-4
_MIN_BAND = 0.02  # smallest NSE band; used when a stock's band is unknown


def add_circuit_flags(df: pd.DataFrame) -> pd.DataFrame:
    """Flag days a stock was locked at its circuit limit (untradeable that day).

    Locked = the whole day traded at one price (high == low) and the move from
    prev_close reached the band. Band NaN (unknown) falls back to the 2% minimum;
    band inf (no band, F&O stocks) never locks.
    Also flags `open_at_upper` / `open_at_lower`: opened at the limit, so a market
    order at the open is assumed unfilled.
    """
    df = df.copy()
    band = df["band"].astype(float) / 100
    known = band.notna() & np.isfinite(band)
    ret = df["close"] / df["prev_close"] - 1
    one_price = (df["high"] - df["low"]).abs() <= _TOL * df["close"]
    thresh = band.where(known, _MIN_BAND) - 0.001
    thresh = thresh.where(~np.isinf(band), np.inf)
    df["locked_up"] = one_price & (ret >= thresh)
    df["locked_down"] = one_price & (ret <= -thresh)
    upper = df["prev_close"] * (1 + band)
    lower = df["prev_close"] * (1 - band)
    df["open_at_upper"] = df["locked_up"] | (known & (df["open"] >= upper * (1 - _TOL)))
    df["open_at_lower"] = df["locked_down"] | (known & (df["open"] <= lower * (1 + _TOL)))
    return df


def add_universe_features(df: pd.DataFrame, cfg: UniverseConfig = UniverseConfig()) -> pd.DataFrame:
    df = df.sort_values(["symbol", "date"]).copy()
    g = df.groupby("symbol", sort=False)
    df["adtv"] = g["turnover"].transform(lambda s: s.rolling(cfg.adtv_window, min_periods=cfg.adtv_window).mean())
    df["bars"] = g.cumcount() + 1
    return df


def tradability_mask(
    df: pd.DataFrame,
    cfg: UniverseConfig = UniverseConfig(),
    position_value: float | None = None,
) -> pd.DataFrame:
    """Return one boolean column per check plus `tradable` (all checks pass).

    `position_value` (₹) enables the "my position <= x% of ADTV" check. Checks whose
    input data is missing (band, mcap, surveillance) pass with a warning, so a
    missing data source never silently shrinks the universe to nothing.
    """
    if "adtv" not in df:
        df = add_universe_features(df, cfg)
    checks = pd.DataFrame(index=df.index)
    checks["series_ok"] = df["series"].isin(cfg.series)
    checks["history_ok"] = df["bars"] >= cfg.min_history_days
    checks["liquidity_ok"] = df["adtv"] >= cfg.min_adtv_cr * CRORE
    if position_value is not None:
        checks["size_ok"] = position_value <= cfg.max_position_pct_of_adtv * df["adtv"]

    band = df["band"].astype(float)
    if band.isna().all():
        warnings.warn("no price-band data: band filter skipped (5%/10% circuit stocks not excluded)")
        checks["band_ok"] = True
    else:
        checks["band_ok"] = band.isin(cfg.allowed_bands) | band.isna()

    if "mcap_cr" in df and df["mcap_cr"].notna().any():
        checks["mcap_ok"] = df["mcap_cr"] >= cfg.min_mcap_cr
    else:
        warnings.warn("no market-cap data: mcap filter skipped")
        checks["mcap_ok"] = True

    if "surveillance" in df:
        checks["surveillance_ok"] = ~df["surveillance"].fillna(False).astype(bool)
    else:
        warnings.warn("no ASM/GSM data: surveillance filter skipped")
        checks["surveillance_ok"] = True

    checks["tradable"] = checks.all(axis=1)
    return checks


def attach_surveillance(df: pd.DataFrame, lists: pd.DataFrame) -> pd.DataFrame:
    """Mark rows on the ASM/GSM lists. `lists` has columns date, symbol (one row per day listed)."""
    key = lists[["date", "symbol"]].drop_duplicates().assign(surveillance=True)
    out = df.drop(columns="surveillance", errors="ignore").merge(key, on=["date", "symbol"], how="left")
    out["surveillance"] = out["surveillance"].fillna(False).astype(bool)
    return out


def attach_mcap(df: pd.DataFrame, shares: pd.DataFrame) -> pd.DataFrame:
    """Point-in-time market cap from shares outstanding.

    `shares` has columns symbol, date (effective from), shares. Uses the latest
    share count known on or before each day, times the unadjusted close.
    """
    s = shares.sort_values("date")
    out = pd.merge_asof(
        df.sort_values("date"), s[["date", "symbol", "shares"]], on="date", by="symbol", direction="backward"
    )
    raw_close = out["close"] / out.get("adj_factor", 1.0)
    out["mcap_cr"] = raw_close * out["shares"] / CRORE
    return out.drop(columns="shares").sort_values(["symbol", "date"]).reset_index(drop=True)
