"""Loading NSE daily data into one long price panel.

Panel schema (one row per symbol per trading day):
    date, symbol, series, open, high, low, close, prev_close,
    volume, turnover (₹), deliv_pct, band (float %, NaN = no band / unknown)

Optional columns the filter uses when present: mcap_cr, surveillance (bool).

Survivorship: the bhavcopy lists every stock that traded that day, delisted ones included,
so a panel built from bhavcopies is survivorship-free. The yfinance fallback is not: it only
knows symbols that still exist. Use it for spot checks, not for the backtest of record.
"""
from __future__ import annotations

import io
import time
import warnings
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from .config import LAKH

NSE_ARCHIVE = "https://nsearchives.nseindia.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "*/*",
    "Referer": "https://www.nseindia.com/",
}

PANEL_COLUMNS = [
    "date", "symbol", "series", "open", "high", "low", "close", "prev_close",
    "volume", "turnover", "deliv_pct", "band",
]


# --------------------------------------------------------------------------- parsing

def parse_sec_bhavdata_full(text: str) -> pd.DataFrame:
    """Parse NSE `sec_bhavdata_full_DDMMYYYY.csv` (prices plus delivery %)."""
    df = pd.read_csv(io.StringIO(text), skipinitialspace=True)
    df.columns = [c.strip() for c in df.columns]
    for c in df.columns:
        if pd.api.types.is_string_dtype(df[c]):
            df[c] = df[c].str.strip()
    out = pd.DataFrame({
        "date": pd.to_datetime(df["DATE1"], format="%d-%b-%Y"),
        "symbol": df["SYMBOL"],
        "series": df["SERIES"],
        "open": pd.to_numeric(df["OPEN_PRICE"], errors="coerce"),
        "high": pd.to_numeric(df["HIGH_PRICE"], errors="coerce"),
        "low": pd.to_numeric(df["LOW_PRICE"], errors="coerce"),
        "close": pd.to_numeric(df["CLOSE_PRICE"], errors="coerce"),
        "prev_close": pd.to_numeric(df["PREV_CLOSE"], errors="coerce"),
        "volume": pd.to_numeric(df["TTL_TRD_QNTY"], errors="coerce"),
        "turnover": pd.to_numeric(df["TURNOVER_LACS"], errors="coerce") * LAKH,
        "deliv_pct": pd.to_numeric(df["DELIV_PER"], errors="coerce"),
    })
    out["band"] = np.nan
    return out[PANEL_COLUMNS]


def parse_sec_list(text: str) -> pd.DataFrame:
    """Parse NSE `sec_list_DDMMYYYY.csv` (price band per symbol). 'No Band' -> NaN."""
    df = pd.read_csv(io.StringIO(text), skipinitialspace=True)
    df.columns = [c.strip().lower() for c in df.columns]
    band_col = next(c for c in df.columns if "band" in c)
    band = df[band_col].astype(str).str.strip()
    return pd.DataFrame({
        "symbol": df["symbol"].str.strip(),
        "series": df["series"].str.strip(),
        "band": pd.to_numeric(band, errors="coerce"),  # "No Band" -> NaN
        "no_band": band.str.lower().str.contains("no"),
    })


# --------------------------------------------------------------------------- fetching

def _get(url: str, session=None, retries: int = 3) -> bytes | None:
    import requests

    s = session or requests.Session()
    for attempt in range(retries):
        try:
            r = s.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 404:
                return None  # holiday or file not published
            r.raise_for_status()
            return r.content
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** (attempt + 1))
    return None


def _maybe_unzip(content: bytes) -> str:
    if content[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            return z.read(z.namelist()[0]).decode("utf-8", errors="replace")
    return content.decode("utf-8", errors="replace")


def fetch_day(day: pd.Timestamp, session=None) -> pd.DataFrame | None:
    """Download one day's bhavcopy plus price bands. Returns None on holidays."""
    d = day.strftime("%d%m%Y")
    raw = _get(f"{NSE_ARCHIVE}/products/content/sec_bhavdata_full_{d}.csv", session)
    if raw is None:
        return None
    prices = parse_sec_bhavdata_full(_maybe_unzip(raw))
    bands_raw = _get(f"{NSE_ARCHIVE}/content/equities/sec_list_{d}.csv", session)
    if bands_raw is not None:
        bands = parse_sec_list(_maybe_unzip(bands_raw))
        prices = prices.drop(columns="band").merge(
            bands[["symbol", "series", "band", "no_band"]], on=["symbol", "series"], how="left"
        )
        # Encode "no band" as +inf so it is distinguishable from "unknown" (NaN).
        prices.loc[prices.pop("no_band").fillna(False).astype(bool), "band"] = np.inf
        prices = prices[PANEL_COLUMNS]
    return prices


def build_cache(start: str, end: str, cache_dir: str | Path, pause: float = 0.5) -> None:
    """Download each trading day into `cache_dir/YYYY/YYYY-MM-DD.parquet`. Skips days already cached."""
    import requests

    cache = Path(cache_dir)
    session = requests.Session()
    session.get("https://www.nseindia.com", headers=HEADERS, timeout=30)  # cookies
    for day in pd.bdate_range(start, end):
        path = cache / str(day.year) / f"{day.date()}.parquet"
        if path.exists():
            continue
        df = fetch_day(day, session)
        if df is None:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, index=False)
        time.sleep(pause)


def load_cache(cache_dir: str | Path, start: str | None = None, end: str | None = None) -> pd.DataFrame:
    files = sorted(Path(cache_dir).glob("*/*.parquet"))
    if start:
        files = [f for f in files if f.stem >= str(pd.Timestamp(start).date())]
    if end:
        files = [f for f in files if f.stem <= str(pd.Timestamp(end).date())]
    if not files:
        raise FileNotFoundError(f"no cached bhavcopies in {cache_dir}")
    return prepare_panel(pd.concat((pd.read_parquet(f) for f in files), ignore_index=True))


def fetch_yfinance(symbols: list[str], start: str, end: str) -> pd.DataFrame:
    """Fallback price history. No delivery %, no bands, survivorship-biased."""
    import yfinance as yf

    warnings.warn("yfinance data is survivorship-biased and has no delivery %; not for the backtest of record")
    frames = []
    for sym in symbols:
        h = yf.Ticker(f"{sym}.NS").history(start=start, end=end, auto_adjust=False)
        if h.empty:
            continue
        h = h.reset_index()
        frames.append(pd.DataFrame({
            "date": pd.to_datetime(h["Date"]).dt.tz_localize(None).dt.normalize(),
            "symbol": sym, "series": "EQ",
            "open": h["Open"], "high": h["High"], "low": h["Low"], "close": h["Close"],
            "prev_close": h["Close"].shift(1), "volume": h["Volume"],
            "turnover": h["Volume"] * (h["High"] + h["Low"] + h["Close"]) / 3,
            "deliv_pct": np.nan, "band": np.nan,
        }))
    return prepare_panel(pd.concat(frames, ignore_index=True))


# --------------------------------------------------------------------------- cleaning

def adjust_for_corporate_actions(df: pd.DataFrame) -> pd.DataFrame:
    """Back-adjust OHLC and volume for splits/bonuses.

    NSE restates PREV_CLOSE on the ex-date, so prev_close[t] / close[t-1] is the
    adjustment factor. Anything within 0.5% of 1 is treated as noise and ignored.
    Adds `adj_factor` (cumulative, applied to history before each ex-date).
    """
    df = df.sort_values(["symbol", "date"]).copy()
    last_close = df.groupby("symbol")["close"].shift(1)
    step = (df["prev_close"] / last_close).where(lambda s: (s - 1).abs() > 0.005, 1.0).fillna(1.0)
    # factor for row t = product of steps strictly after t
    rev_cum = step[::-1].groupby(df["symbol"][::-1]).cumprod()[::-1]
    factor = rev_cum / step
    for c in ("open", "high", "low", "close", "prev_close"):
        df[c] = df[c] * factor
    df["volume"] = df["volume"] / factor
    df["adj_factor"] = factor
    return df


def prepare_panel(df: pd.DataFrame, adjust: bool = True) -> pd.DataFrame:
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df[(df["close"] > 0) & (df["volume"] > 0)]
    df = df.drop_duplicates(["symbol", "series", "date"], keep="last")
    if adjust and "prev_close" in df and df["prev_close"].notna().any():
        df = adjust_for_corporate_actions(df)
    return df.sort_values(["symbol", "date"]).reset_index(drop=True)
