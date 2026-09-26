"""Daily screener snapshot: one row per listed NSE equity, built from the bhavcopy panel.

Reference lists (company names, index membership, industry) come from NSE archives.
Each is optional: if a download fails the matching column is left blank.
"""
from __future__ import annotations

import io
import warnings

import numpy as np
import pandas as pd

from .config import CRORE, UniverseConfig
from .playbooks import volume_breakout as vb
from .universe import add_circuit_flags, add_universe_features, tradability_mask

INDEX_LISTS = {
    # cap bucket -> NSE constituents file; first match wins, so order is large -> micro
    "Large": "ind_nifty100list.csv",
    "Mid": "ind_niftymidcap150list.csv",
    "Small": "ind_niftysmallcap250list.csv",
    "Micro": "ind_niftymicrocap250_list.csv",
}
TOTAL_MARKET = "ind_niftytotalmarket_list.csv"  # ~750 stocks with industry
EQUITY_LIST = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
INDEX_BASE = "https://nsearchives.nseindia.com/content/indices/"

HORIZONS = {"chg_1d": 1, "chg_1w": 5, "chg_1m": 21, "chg_3m": 63, "chg_6m": 126, "chg_1y": 250}


def _read_csv(text: str) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(text), skipinitialspace=True)
    df.columns = [c.strip() for c in df.columns]
    return df


def fetch_reference(session=None) -> dict[str, pd.DataFrame]:
    """Download the equity list and index constituent lists. Missing ones are skipped."""
    from .data import _get, _maybe_unzip

    out = {}
    urls = {"equity": EQUITY_LIST, "total": INDEX_BASE + TOTAL_MARKET}
    urls.update({cap: INDEX_BASE + f for cap, f in INDEX_LISTS.items()})
    for key, url in urls.items():
        try:
            raw = _get(url, session)
            if raw is None:
                warnings.warn(f"{url}: not found")
                continue
            out[key] = _read_csv(_maybe_unzip(raw))
        except Exception as e:  # a missing reference list must not stop the snapshot
            warnings.warn(f"{url}: {e}")
    return out


def reference_table(refs: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """symbol -> name, industry, cap bucket, is_equity (False for ETFs and anything not in EQUITY_L)."""
    rows = {}
    eq = refs.get("equity")
    if eq is not None:
        for sym, name in zip(eq["SYMBOL"].str.strip(), eq["NAME OF COMPANY"].str.strip()):
            rows[sym] = {"name": name, "is_equity": True}
    for key in ["total", *INDEX_LISTS]:
        df = refs.get(key)
        if df is None:
            continue
        for rec in df.to_dict("records"):
            sym = str(rec["Symbol"]).strip()
            r = rows.setdefault(sym, {"is_equity": eq is None})
            if rec.get("Company Name") and not r.get("name"):
                r["name"] = str(rec["Company Name"]).strip()
            if rec.get("Industry") and not r.get("industry"):
                r["industry"] = str(rec["Industry"]).strip()
            if key in INDEX_LISTS and not r.get("cap"):
                r["cap"] = key
    ref = pd.DataFrame.from_dict(rows, orient="index")
    ref.index.name = "symbol"
    for c in ("name", "industry", "cap"):
        if c not in ref:
            ref[c] = None
    return ref.reset_index()


def _pct(a, b):
    return np.where((b > 0) & np.isfinite(b), a / b - 1, np.nan)


def build_snapshot(
    panel: pd.DataFrame,
    refs: dict[str, pd.DataFrame] | None = None,
    cfg: UniverseConfig = UniverseConfig(),
    spark_weeks: int = 52,
) -> tuple[pd.DataFrame, dict]:
    """Return (one row per stock on the latest date, metadata)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = add_circuit_flags(add_universe_features(panel, cfg))
        df = vb.add_features(df)
        df["tradable"] = tradability_mask(df, cfg)["tradable"]
        df["breakout"] = False
        sig = vb.signals(df, df["tradable"])
    if len(sig):
        key = pd.MultiIndex.from_frame(df[["symbol", "date"]])
        df.loc[key.isin(pd.MultiIndex.from_frame(sig[["symbol", "date"]])), "breakout"] = True

    as_of = df["date"].max()
    g = df.groupby("symbol", sort=False)
    last = df[df["date"] == as_of].set_index("symbol")

    today = (df["date"] == as_of).to_numpy()  # same row order as `last`
    for col, h in HORIZONS.items():
        last[col] = _pct(last["close"].to_numpy(), g["close"].shift(h).to_numpy()[today])
    win = df[df["date"] > as_of - pd.Timedelta(days=365)]
    wg = win.groupby("symbol")
    last["high_52w"] = wg["high"].max()
    last["low_52w"] = wg["low"].min()
    last["from_high"] = _pct(last["close"], last["high_52w"])
    last["deliv_avg_20"] = g["deliv_pct"].transform(lambda s: s.rolling(20, min_periods=5).mean()).to_numpy()[today]
    last["adtv_cr"] = last["adtv"] / CRORE
    last["turnover_cr"] = last["turnover"] / CRORE
    last["last_breakout"] = df[df["breakout"]].groupby("symbol")["date"].max()

    # weekly closes for sparklines
    wk = win.set_index("date").groupby("symbol")["close"].resample("W-FRI").last().dropna()
    spark = wk.groupby(level=0).apply(lambda s: [round(float(x), 2) for x in s.tail(spark_weeks)])

    out = last.reset_index()
    if refs:
        ref = reference_table(refs)
        out = out.merge(ref, on="symbol", how="left")
        if "equity" in refs:
            out = out[out["is_equity"].fillna(False).astype(bool)]  # drops ETFs, debt, InvITs
    else:
        out["name"] = out["industry"] = out["cap"] = None

    out["band"] = out["band"].replace(np.inf, 0)  # 0 = no band (JSON has no inf)
    cols = [
        "symbol", "name", "industry", "cap", "close", *HORIZONS, "high_52w", "low_52w", "from_high",
        "volume", "volume_ratio", "deliv_pct", "deliv_avg_20", "turnover_cr", "adtv_cr", "band",
        "locked_up", "locked_down", "tradable", "breakout", "last_breakout",
    ]
    out = out[cols].copy()
    out["last_breakout"] = out["last_breakout"].dt.strftime("%Y-%m-%d")
    out["spark"] = out["symbol"].map(spark)
    meta = {
        "as_of": as_of.strftime("%Y-%m-%d"),
        "stocks": int(len(out)),
        "breakouts_today": int(out["breakout"].sum()),
        "has_reference": bool(refs),
        "universe": {"min_adtv_cr": cfg.min_adtv_cr, "bands": "20% or no band"},
    }
    return out.sort_values("turnover_cr", ascending=False).reset_index(drop=True), meta
