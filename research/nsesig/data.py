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

def _get(url: str, session=None, retries: int = 3, headers: dict | None = None) -> bytes | None:
    import requests

    s = session or requests.Session()
    for attempt in range(retries):
        try:
            r = s.get(url, headers=headers or HEADERS, timeout=30)
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


BSE_HEADERS = {**HEADERS, "Referer": "https://www.bseindia.com/", "Origin": "https://www.bseindia.com"}
BSE_BHAV = "https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{d}_F_0000.CSV"
BSE_COLUMNS = PANEL_COLUMNS + ["isin", "bse_code", "ticker", "name"]


def is_equity_isin(isin: pd.Series) -> pd.Series:
    """Indian company equity shares: INE + issuer (4) + security type '01'. Excludes ETFs (INF...), debt, REITs."""
    s = isin.astype(str).str.strip().str.upper()
    return s.str.startswith("INE") & (s.str[7:9] == "01") & (s.str.len() == 12)


def parse_bse_udiff(text: str) -> pd.DataFrame | None:
    """Parse a BSE UDiFF equity bhavcopy. Keyed by ISIN (`symbol` = ISIN) so it merges with NSE."""
    if "TradDt" not in text[:300]:
        return None  # BSE serves an HTML page, not a 404, for days without a file
    df = pd.read_csv(io.StringIO(text), skipinitialspace=True, low_memory=False)
    df.columns = [c.strip() for c in df.columns]
    df = df[is_equity_isin(df["ISIN"])]
    turnover = pd.to_numeric(df["TtlTrfVal"], errors="coerce")
    out = pd.DataFrame({
        "date": pd.to_datetime(df["TradDt"]),
        "symbol": df["ISIN"].str.strip(),
        "series": df["SctySrs"].astype(str).str.strip(),  # BSE group: A, B, T, X, XT, Z, M, MT ...
        "open": pd.to_numeric(df["OpnPric"], errors="coerce"),
        "high": pd.to_numeric(df["HghPric"], errors="coerce"),
        "low": pd.to_numeric(df["LwPric"], errors="coerce"),
        "close": pd.to_numeric(df["ClsPric"], errors="coerce"),
        "prev_close": pd.to_numeric(df["PrvsClsgPric"], errors="coerce"),
        "volume": pd.to_numeric(df["TtlTradgVol"], errors="coerce"),
        "turnover": turnover,
        "deliv_pct": np.nan,
        "band": np.nan,
        "isin": df["ISIN"].str.strip(),
        "bse_code": df["FinInstrmId"].astype(str).str.strip(),
        "ticker": df["TckrSymb"].astype(str).str.strip(),
        "name": df["FinInstrmNm"].astype(str).str.strip(),
    })
    return out[BSE_COLUMNS].reset_index(drop=True)


def fetch_bse_day(day: pd.Timestamp, session=None) -> pd.DataFrame | None:
    raw = _get(BSE_BHAV.format(d=day.strftime("%Y%m%d")), session, headers=BSE_HEADERS)
    return None if raw is None else parse_bse_udiff(_maybe_unzip(raw))


def build_cache(
    start: str,
    end: str,
    cache_dir: str | Path,
    pause: float = 0.5,
    max_initial_errors: int = 5,
    exchange: str = "nse",
) -> dict:
    """Download each trading day into `cache_dir/YYYY/YYYY-MM-DD.parquet`.

    Resumable: skips days already cached and days recorded in `no_file.txt`
    (holidays). Per-day errors are logged and skipped; if the first
    `max_initial_errors` attempts all fail, NSE is probably blocking us and it aborts.
    """
    import requests

    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    no_file_path = cache / "no_file.txt"
    no_file = set(no_file_path.read_text().split()) if no_file_path.exists() else set()
    fetcher, home = {"nse": (fetch_day, "https://www.nseindia.com"), "bse": (fetch_bse_day, "https://www.bseindia.com")}[exchange]
    session = requests.Session()
    try:
        session.get(home, headers=HEADERS, timeout=30)  # cookies
    except Exception as e:
        print(f"warning: {home} request failed ({e}); trying downloads anyway")
    stats = {"fetched": 0, "cached": 0, "no_file": 0, "errors": 0}
    attempts = 0
    # Every calendar day, not just weekdays: NSE holds special weekend sessions (Budget day,
    # Muhurat trading, DR drills). Weekends 404 once and are then remembered in no_file.txt.
    for day in pd.date_range(start, end):
        key = str(day.date())
        path = cache / str(day.year) / f"{key}.parquet"
        if path.exists():
            stats["cached"] += 1
            continue
        if key in no_file:
            stats["no_file"] += 1
            continue
        attempts += 1
        try:
            df = fetcher(day, session)
        except Exception as e:
            stats["errors"] += 1
            print(f"{key}: error {e}")
            if attempts == stats["errors"] == max_initial_errors:
                raise RuntimeError(f"first {max_initial_errors} downloads all failed; {exchange.upper()} is likely blocking this IP") from e
            time.sleep(pause)
            continue
        if df is None:
            stats["no_file"] += 1
            # Only remember old misses as holidays: a recent day may just not be published yet.
            if day < pd.Timestamp.today().normalize() - pd.Timedelta(days=5):
                with no_file_path.open("a") as f:
                    f.write(key + "\n")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            df.to_parquet(path, index=False)
            stats["fetched"] += 1
            if stats["fetched"] % 50 == 0:
                print(f"{key}: {stats}", flush=True)
        time.sleep(pause)
    print(f"done: {stats}")
    return stats


def load_cache(
    cache_dir: str | Path, start: str | None = None, end: str | None = None, series: tuple = ("EQ",), log: bool = False
) -> pd.DataFrame:
    files = sorted(Path(cache_dir).glob("*/*.parquet"))
    if start:
        files = [f for f in files if f.stem >= str(pd.Timestamp(start).date())]
    if end:
        files = [f for f in files if f.stem <= str(pd.Timestamp(end).date())]
    if not files:
        raise FileNotFoundError(f"no cached bhavcopies in {cache_dir}")
    frames = (pd.read_parquet(f) for f in files)
    df = pd.concat((f[f["series"].isin(series)] if series else f for f in frames), ignore_index=True)
    return prepare_panel(df, log=log)


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

# Price ratios after common splits (face value 10->5 = 1/2, 10->2 = 1/5, 5->2 = 2/5, ...) and
# bonus issues (1:1 = 1/2, 1:2 = 2/3, 2:1 = 1/3, 3:1 = 1/4). Only drops of 30%+ are considered,
# which a stock in a 20% band cannot make on its own.
_SPLIT_RATIOS = np.array([2 / 3, 1 / 2, 2 / 5, 1 / 3, 1 / 4, 1 / 5, 1 / 10])
_SPLIT_TOL = 0.08


def _infer_split_step(open_: pd.Series, close: pd.Series, last_close: pd.Series) -> pd.Series:
    """Step factor for ex-dates NSE did not restate: open and close both sit near a standard ratio."""
    gap_open = (open_ / last_close).to_numpy()
    gap_close = (close / last_close).to_numpy()
    out = np.ones(len(open_))
    for r in _SPLIT_RATIOS:
        hit = (np.abs(gap_open / r - 1) < _SPLIT_TOL) & (np.abs(gap_close / r - 1) < _SPLIT_TOL + 0.1)
        out = np.where((out == 1) & hit, r, out)
    return pd.Series(out, index=open_.index)


def adjust_for_corporate_actions(df: pd.DataFrame, log: bool = False) -> pd.DataFrame:
    """Back-adjust OHLC and volume for splits/bonuses.

    NSE usually restates PREV_CLOSE on the ex-date, so prev_close[t] / close[t-1] is
    the adjustment factor (moves within 0.5% of 1 are noise). When it did not, a gap to
    a standard split/bonus ratio is used instead (`_infer_split_step`).
    Adds `adj_factor` (cumulative, applied to history before each ex-date).
    """
    df = df.sort_values(["symbol", "date"]).copy()
    last_close = df.groupby("symbol")["close"].shift(1)
    nse_step = (df["prev_close"] / last_close).where(lambda s: (s - 1).abs() > 0.005, 1.0).fillna(1.0)
    # A missing session (a trading day absent from the cache) makes prev_close differ from the
    # last close we have for nearly every stock at once. Real corporate actions never do that.
    by_date = (nse_step != 1.0).groupby(df["date"])
    missing_session = (by_date.transform("mean") > 0.10) & (by_date.transform("size") >= 5)
    if log and missing_session.any():
        print("dates that look like a missing prior session (no adjustment applied):",
              sorted(df.loc[missing_session, "date"].dt.strftime("%Y-%m-%d").unique()))
    nse_step = nse_step.where(~missing_session, 1.0)
    # Likewise when this stock skipped sessions (moved to BE/trade-to-trade, suspended): its
    # prev_close is from a row we don't have, so the step is a price move, not an adjustment.
    dates = pd.Index(np.sort(df["date"].unique()))
    pos = dates.get_indexer(df["date"])
    prev_pos = pd.Series(pos, index=df.index).groupby(df["symbol"]).shift(1)
    stock_gap = prev_pos.notna() & (pos - prev_pos > 1)
    nse_step = nse_step.where(~stock_gap, 1.0)
    inferred = _infer_split_step(df["open"], df["close"], last_close).where(~missing_session, 1.0)
    step = nse_step.where(nse_step != 1.0, inferred)
    if log:
        ev = df.loc[step != 1.0, ["date", "symbol"]].assign(step=step[step != 1.0], source=np.where(nse_step[step != 1.0] != 1.0, "nse", "inferred"))
        big = ev[(ev["step"] - 1).abs() > 0.05]
        print(f"corporate-action adjustments: {len(big)} (> 5%), of which inferred: {(big['source'] == 'inferred').sum()}")
        print(big.tail(40).to_string(index=False))
    # factor for row t = product of steps strictly after t
    rev_cum = step[::-1].groupby(df["symbol"][::-1]).cumprod()[::-1]
    factor = rev_cum / step
    for c in ("open", "high", "low", "close", "prev_close"):
        df[c] = df[c] * factor
    # the ex-date's own prev_close is still on the old basis when NSE did not restate it
    df["prev_close"] = df["prev_close"] * np.where((nse_step == 1.0) & (inferred != 1.0), inferred, 1.0)
    df["volume"] = df["volume"] / factor
    df["adj_factor"] = factor
    return df


def prepare_panel(df: pd.DataFrame, adjust: bool = True, log: bool = False) -> pd.DataFrame:
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df[(df["close"] > 0) & (df["volume"] > 0)]
    # one row per symbol per day; if a stock printed in two series that day, keep the busier one
    df = df.sort_values("turnover").drop_duplicates(["symbol", "date"], keep="last")
    if adjust and "prev_close" in df and df["prev_close"].notna().any():
        df = adjust_for_corporate_actions(df, log=log)
    return df.sort_values(["symbol", "date"]).reset_index(drop=True)
