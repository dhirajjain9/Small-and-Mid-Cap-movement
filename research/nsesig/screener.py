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

# price change look-backs in calendar days, measured from the snapshot date
CAL_HORIZONS = {"chg_1w": 7, "chg_1m": 30, "chg_3m": 91, "chg_6m": 182, "chg_1y": 365}
HORIZONS = {"chg_1d": 1, **CAL_HORIZONS}
ADTV_SESSIONS = 20

METRIC_COLS = [
    "close", *HORIZONS, "high_52w", "low_52w", "from_high", "volume", "volume_ratio", "deliv_pct",
    "deliv_avg_20", "turnover_cr", "adtv_cr", "band", "locked_up", "locked_down", "tradable", "breakout",
    "last_breakout", "last_trade", "series", "spark",
]


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
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where((b > 0) & np.isfinite(b), a / b - 1, np.nan)


def stock_metrics(df: pd.DataFrame, as_of: pd.Timestamp, spark_weeks: int = 52) -> pd.DataFrame:
    """Per-symbol screener fields from a price panel, as of `as_of`.

    Works for stocks that did not trade on `as_of`: they keep their last close and
    last_trade date, and day-specific fields (1D change, today's value, volume ratio) are blank.
    ADTV counts sessions without trades as zero, so a thinly traded stock shows a small ADTV.
    """
    df = df[df["date"] <= as_of].sort_values(["symbol", "date"])
    g = df.groupby("symbol", sort=False)
    last = g.tail(1).set_index("symbol")
    traded_today = last["date"] == as_of
    out = pd.DataFrame(index=last.index)
    out["close"] = last["close"]
    out["last_trade"] = last["date"]
    out["series"] = last["series"]
    out["volume"] = last["volume"]
    prev_close = g["close"].shift(1)[g.cumcount(ascending=False) == 0]
    prev_close.index = df.loc[prev_close.index, "symbol"]
    out["chg_1d"] = np.where(traded_today, _pct(out["close"], prev_close.reindex(out.index)), np.nan)

    for col, days in CAL_HORIZONS.items():
        target = as_of - pd.Timedelta(days=days)
        past = df[df["date"] <= target].groupby("symbol").tail(1).set_index("symbol")["close"]
        out[col] = _pct(out["close"], past.reindex(out.index))

    win = df[df["date"] > as_of - pd.Timedelta(days=365)]
    wg = win.groupby("symbol")
    out["high_52w"] = wg["high"].max()
    out["low_52w"] = wg["low"].min()
    out["from_high"] = _pct(out["close"], out["high_52w"])

    med_vol = g["volume"].apply(lambda s: s.iloc[-51:-1].median() if len(s) > 10 else np.nan)
    out["volume_ratio"] = np.where(traded_today, out["volume"] / med_vol.reindex(out.index), np.nan)
    out["deliv_pct"] = np.where(traded_today, last["deliv_pct"], np.nan)
    out["deliv_avg_20"] = g["deliv_pct"].apply(lambda s: s.tail(20).mean())
    out["turnover_cr"] = np.where(traded_today, last["turnover"] / CRORE, np.nan)
    sessions = np.sort(df["date"].unique())[-ADTV_SESSIONS:]
    recent = df[df["date"] >= sessions[0]]
    out["adtv_cr"] = (recent.groupby("symbol")["turnover"].sum() / len(sessions) / CRORE).reindex(out.index).fillna(0.0)
    out["band"] = last["band"].replace(np.inf, 0)  # 0 = no band (JSON has no inf)
    for c in ("locked_up", "locked_down", "tradable", "breakout"):
        out[c] = (last[c].astype(bool) & traded_today) if c in last else False
    out["last_breakout"] = df[df["breakout"]].groupby("symbol")["date"].max() if "breakout" in df else pd.NaT

    wk = win.set_index("date").groupby("symbol")["close"].resample("W-FRI").last().dropna()
    out["spark"] = wk.groupby(level=0).apply(lambda s: [round(float(x), 2) for x in s.tail(spark_weeks)])
    return out


def _nse_signals(panel: pd.DataFrame, cfg: UniverseConfig) -> pd.DataFrame:
    """Add circuit flags, tradability and volume-breakout flags to an NSE panel."""
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
    return df


def _finish(out: pd.DataFrame) -> pd.DataFrame:
    for c in ("last_breakout", "last_trade"):
        out[c] = pd.to_datetime(out[c]).dt.strftime("%Y-%m-%d")
    return out


def build_snapshot(
    panel: pd.DataFrame,
    refs: dict[str, pd.DataFrame] | None = None,
    cfg: UniverseConfig = UniverseConfig(),
    spark_weeks: int = 52,
    bse_panel: pd.DataFrame | None = None,
    nse_lists: pd.DataFrame | None = None,
    bse_master: pd.DataFrame | None = None,
    nse_mcap: pd.DataFrame | None = None,
    yahoo: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, dict]:
    """Return (one row per company, metadata).

    With `nse_lists` (from listings.fetch_nse_lists) the snapshot covers every listed company
    across NSE main board, NSE SME and BSE, merged by ISIN, including ones that did not trade.
    Without it, it covers the NSE stocks in `panel` (filtered to EQUITY_L when `refs` has it).
    """
    from . import listings

    nse = _nse_signals(panel, cfg)
    if bse_panel is not None and len(bse_panel):
        bse_panel = listings.canonical_bse_isin(bse_panel)
    as_of = max(nse["date"].max(), bse_panel["date"].max() if bse_panel is not None and len(bse_panel) else nse["date"].max())
    nse_m = stock_metrics(nse, as_of, spark_weeks)
    ref = reference_table(refs) if refs else None

    if nse_lists is None:
        out = nse_m.reset_index().rename(columns={"index": "symbol"})
        if ref is not None:
            out = out.merge(ref, on="symbol", how="left")
            if "equity" in refs:
                out = out[out["is_equity"].fillna(False).astype(bool)]  # drops ETFs, debt, InvITs
        else:
            out["name"] = out["industry"] = out["cap"] = None
        out = _finish(out[["symbol", "name", "industry", "cap", *METRIC_COLS]].copy())
        meta = {"as_of": as_of.strftime("%Y-%m-%d"), "stocks": int(len(out)), "breakouts_today": int(out["breakout"].sum()),
                "has_reference": bool(refs), "universe": {"min_adtv_cr": cfg.min_adtv_cr, "bands": "20% or no band"}}
        return out.sort_values("turnover_cr", ascending=False).reset_index(drop=True), meta

    bse_m = stock_metrics(bse_panel, as_of, spark_weeks) if bse_panel is not None and len(bse_panel) else pd.DataFrame(columns=METRIC_COLS)
    master = listings.master_list(nse_lists, listings.bse_from_bhavcopies(bse_panel), bse_master)

    a = nse_m.reindex(master["nse_symbol"]).reset_index(drop=True)
    b = bse_m.reindex(master["isin"]).reset_index(drop=True)
    # Prefer NSE figures (delivery %, bands, signals) unless BSE has clearly more recent trades.
    use_bse = a["last_trade"].isna() | (b["last_trade"].notna() & (b["last_trade"] > a["last_trade"] + pd.Timedelta(days=7)))
    metrics = a.copy()
    for c in METRIC_COLS:
        if c in b:
            metrics[c] = a[c].where(~use_bse, b[c])
    metrics["price_source"] = np.where(metrics["last_trade"].isna(), None, np.where(use_bse, "BSE", "NSE"))
    out = pd.concat([master.reset_index(drop=True), metrics], axis=1)

    if ref is not None:
        r = ref.set_index("symbol")
        out["industry"] = out["nse_symbol"].map(r["industry"])
        out["cap"] = out["nse_symbol"].map(r["cap"])
    else:
        out["industry"] = out["cap"] = None
    if "bse_industry" in out:
        out["industry"] = out["industry"].fillna(out["bse_industry"])
    for c in ("tradable", "breakout", "locked_up", "locked_down"):
        out[c] = out[c].fillna(False).astype(bool)
    out["status"] = [listings.status_for(r, as_of) for r in out.to_dict("records")]

    from .fundamentals import add_valuation

    out = add_valuation(out, nse_mcap, yahoo)
    # the tradability filter's market-cap floor, now that market cap is known
    out["tradable"] = out["tradable"] & ~(out["mcap_cr"] < cfg.min_mcap_cr)

    cols = ["symbol", "name", "isin", "exchange", "segment", "status", "nse_symbol", "bse_code", "industry", "cap",
            "price_source", "mcap_cr", "pe", "eps_ttm", "shares", "loss_making", *METRIC_COLS]
    for c in cols:
        if c not in out:
            out[c] = None
    out = _finish(out[cols].copy())
    meta = {
        "as_of": as_of.strftime("%Y-%m-%d"),
        "stocks": int(len(out)),
        "breakouts_today": int(out["breakout"].sum()),
        "has_reference": bool(refs),
        "by_exchange": out["exchange"].value_counts().to_dict(),
        "by_status": out["status"].value_counts().to_dict(),
        "bse_master_file": bool(bse_master is not None and len(bse_master)),
        "with_mcap": int(out["mcap_cr"].notna().sum()),
        "with_pe": int(out["pe"].notna().sum()),
        "eps_as_of": (yahoo["fetched_at"].max().strftime("%Y-%m-%d") if yahoo is not None and len(yahoo) else None),
        "universe": {"min_adtv_cr": cfg.min_adtv_cr, "bands": "20% or no band"},
    }
    out = out.sort_values(["turnover_cr", "adtv_cr"], ascending=False, na_position="last").reset_index(drop=True)
    return out, meta
