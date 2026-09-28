"""Shares outstanding and EPS, for market cap and P/E.

- Shares: NSE's daily market-cap file (mcapDDMMYYYY.csv inside the PR zip) gives the issue
  size of every NSE security. Yahoo's sharesOutstanding fills in BSE-only companies.
- EPS, revenue and net profit: Yahoo's trailing twelve months (TTM), refreshed weekly into
  data/fundamentals/yahoo.csv. Revenue growth is Yahoo's year-on-year growth of the latest quarter.

Market cap and P/E are then computed daily from our own closing price, so they move with it.
"""
from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

# Bump when fields are added, so rows fetched without them are refreshed on the next run.
SCHEMA_VERSION = 2
YAHOO_FIELDS = {
    # our column: Yahoo info key
    "eps_ttm": "trailingEps",
    "revenue_ttm": "totalRevenue",
    "revenue_growth": "revenueGrowth",
    "net_income_ttm": "netIncomeToCommon",
    "fin_currency": "financialCurrency",
}
YAHOO_COLUMNS = ["yahoo_symbol", "isin", *YAHOO_FIELDS, "shares", "fetched_at", "v", "error"]


def parse_nse_mcap(text: str) -> pd.DataFrame:
    """mcapDDMMYYYY.csv -> symbol, series, shares."""
    df = pd.read_csv(io.StringIO(text), skipinitialspace=True)
    df.columns = [c.strip() for c in df.columns]
    return pd.DataFrame({
        "symbol": df["Symbol"].astype(str).str.strip(),
        "series": df["Series"].astype(str).str.strip(),
        "shares": pd.to_numeric(df["Issue Size"], errors="coerce"),
    }).dropna(subset=["shares"])


def fetch_nse_mcap(as_of: pd.Timestamp, session=None, lookback_days: int = 7) -> pd.DataFrame | None:
    """Latest NSE market-cap file on or before `as_of` (walks back over holidays)."""
    from .data import _get

    for back in range(lookback_days):
        day = as_of - pd.Timedelta(days=back)
        raw = _get(f"https://nsearchives.nseindia.com/archives/equities/bhavcopy/pr/PR{day:%d%m%y}.zip", session)
        if raw is None or raw[:2] != b"PK":
            continue
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            name = next((n for n in z.namelist() if n.lower().startswith("mcap")), None)
            if name:
                return parse_nse_mcap(z.read(name).decode("utf-8", errors="replace"))
    return None


def yahoo_symbols(stocks: pd.DataFrame) -> pd.DataFrame:
    """isin -> Yahoo ticker: NSE symbol + .NS, else BSE ticker + .BO (Yahoo doesn't take BSE codes)."""
    nse = stocks["nse_symbol"].notna()
    ysym = np.where(nse, stocks["nse_symbol"].astype(str) + ".NS", stocks["symbol"].astype(str) + ".BO")
    return pd.DataFrame({"isin": stocks["isin"], "yahoo_symbol": ysym}).dropna().drop_duplicates("isin")


def load_yahoo(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        return pd.DataFrame(columns=YAHOO_COLUMNS)
    df = pd.read_csv(p, dtype={"isin": str, "yahoo_symbol": str, "error": str, "fin_currency": str})
    for c in YAHOO_COLUMNS:
        if c not in df:
            df[c] = 1 if c == "v" else np.nan
    df["fetched_at"] = pd.to_datetime(df["fetched_at"], errors="coerce")
    return df[YAHOO_COLUMNS]


def refresh_yahoo(
    targets: pd.DataFrame,
    path: str | Path,
    max_age_days: int = 6,
    pause: float = 0.25,
    save_every: int = 200,
    max_minutes: float = 80,
) -> dict:
    """Fetch EPS, revenue and shares for every target not refreshed in `max_age_days`. Resumable.

    Progress is written to `path` every `save_every` symbols, so a run that is cut short
    (rate limit, timeout) picks up where it stopped next time.
    """
    import yfinance as yf

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    have = load_yahoo(path).set_index("isin")
    now = pd.Timestamp.now().normalize()
    fresh = (
        have.index[((now - have["fetched_at"]).dt.days < max_age_days) & (have["v"] >= SCHEMA_VERSION)] if len(have) else []
    )
    todo = targets[~targets["isin"].isin(fresh)]
    stats = {"todo": len(todo), "ok": 0, "no_data": 0, "errors": 0}
    started, rows, backoff = time.time(), [], 0

    def flush():
        nonlocal have, rows
        if not rows:
            return
        new = pd.DataFrame(rows).set_index("isin")
        have = pd.concat([have[~have.index.isin(new.index)], new])
        have.reset_index()[YAHOO_COLUMNS].to_csv(path, index=False)
        rows = []

    for i, rec in enumerate(todo.itertuples(index=False), 1):
        if (time.time() - started) / 60 > max_minutes:
            print(f"stopping after {max_minutes} min; the rest will be fetched next run")
            break
        row = {"isin": rec.isin, "yahoo_symbol": rec.yahoo_symbol, "fetched_at": now.date(), "shares": np.nan,
               "v": SCHEMA_VERSION, "error": None, **{c: np.nan for c in YAHOO_FIELDS}}
        try:
            info = yf.Ticker(rec.yahoo_symbol).info or {}
            for col, key in YAHOO_FIELDS.items():
                row[col] = info.get(key)
            row["shares"] = info.get("sharesOutstanding") or info.get("impliedSharesOutstanding")
            got = any(row[c] is not None for c in ("eps_ttm", "revenue_ttm", "shares"))
            stats["ok" if got else "no_data"] += 1
            backoff = 0
        except Exception as e:  # rate limits show up as exceptions; back off and keep going
            stats["errors"] += 1
            row["error"] = str(e)[:120]
            backoff = min(backoff * 2 or 5, 120)
            time.sleep(backoff)
        rows.append(row)
        if i % save_every == 0:
            flush()
            print(f"{i}/{len(todo)} {stats}", flush=True)
        time.sleep(pause)
    flush()
    print(f"done: {stats}")
    return stats


def add_valuation(
    out: pd.DataFrame,
    nse_mcap: pd.DataFrame | None,
    yahoo: pd.DataFrame | None,
) -> pd.DataFrame:
    """Add shares, mcap_cr, eps_ttm and pe to a snapshot (needs isin, nse_symbol, close)."""
    from .config import CRORE

    out = out.copy()
    shares = pd.Series(np.nan, index=out.index)
    if nse_mcap is not None and len(nse_mcap):
        by_sym = nse_mcap.sort_values("shares").drop_duplicates("symbol", keep="last").set_index("symbol")["shares"]
        shares = out["nse_symbol"].map(by_sym)
    eps = pd.Series(np.nan, index=out.index)
    if yahoo is not None and len(yahoo):
        y = yahoo.drop_duplicates("isin", keep="last").set_index("isin")
        shares = shares.fillna(out["isin"].map(y["shares"]))
        eps = out["isin"].map(y["eps_ttm"]).astype(float)
    out["shares"] = shares
    out["mcap_cr"] = out["close"] * shares / CRORE
    out["eps_ttm"] = eps
    with np.errstate(divide="ignore", invalid="ignore"):
        out["pe"] = np.where(eps > 0, out["close"] / eps, np.nan)
    out["loss_making"] = eps <= 0
    for c in ("revenue_cr", "net_income_cr", "revenue_growth"):
        out[c] = np.nan
    if yahoo is not None and len(yahoo):
        y = yahoo.drop_duplicates("isin", keep="last").set_index("isin")
        # Yahoo reports a few companies' financials in USD; skip those rather than mix currencies
        inr = y["fin_currency"].isna() | (y["fin_currency"].astype(str).str.upper() == "INR")
        out["revenue_cr"] = out["isin"].map(y["revenue_ttm"].where(inr)) / CRORE
        out["net_income_cr"] = out["isin"].map(y["net_income_ttm"].where(inr)) / CRORE
        out["revenue_growth"] = out["isin"].map(y["revenue_growth"])
    return out
