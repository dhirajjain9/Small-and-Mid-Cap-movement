"""Master list of listed companies: NSE main board, NSE SME and BSE, merged by ISIN.

Dormant companies never appear in a bhavcopy, so the master list comes from the exchanges'
listing files, not from prices:
- NSE: EQUITY_L.csv (main board, all series) and SME_EQUITY_L.csv (Emerge).
- BSE: its full scrip list is not downloadable from cloud servers, so BSE-only companies
  come from a year of BSE bhavcopies (anything that traded at least once), plus an
  optional manual export of BSE's "List of Scrips" saved under data/reference/.
"""
from __future__ import annotations

import io
import warnings
from pathlib import Path

import pandas as pd

from .data import is_equity_isin

NSE_MAIN = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
NSE_SME = "https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv"

NSE_T2T = {"BE", "BZ"}  # trade-to-trade on the main board
NSE_SME_SERIES = {"SM", "ST", "SZ"}
BSE_T2T = {"T", "XT", "Z", "ZP", "TS"}
BSE_SME = {"M", "MT", "MS"}


def _norm_cols(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip().upper().replace("_", " ") for c in df.columns]
    return df


def parse_nse_list(text: str, segment: str) -> pd.DataFrame:
    """EQUITY_L.csv or SME_EQUITY_L.csv -> isin, nse_symbol, name, nse_series, listed_on, segment."""
    df = _norm_cols(pd.read_csv(io.StringIO(text), skipinitialspace=True))
    out = pd.DataFrame({
        "isin": df["ISIN NUMBER"].astype(str).str.strip(),
        "nse_symbol": df["SYMBOL"].astype(str).str.strip(),
        "name": df["NAME OF COMPANY"].astype(str).str.strip(),
        "nse_series": df["SERIES"].astype(str).str.strip(),
        "listed_on": pd.to_datetime(df["DATE OF LISTING"], errors="coerce", format="mixed", dayfirst=True),
    })
    out["segment"] = segment
    return out[is_equity_isin(out["isin"])].drop_duplicates("isin")


def fetch_nse_lists(session=None) -> pd.DataFrame:
    from .data import _get, _maybe_unzip

    frames = []
    for url, seg in ((NSE_MAIN, "Main"), (NSE_SME, "SME")):
        try:
            raw = _get(url, session)
            if raw is None:
                warnings.warn(f"{url}: not found")
                continue
            frames.append(parse_nse_list(_maybe_unzip(raw), seg))
        except Exception as e:
            warnings.warn(f"{url}: {e}")
    return pd.concat(frames, ignore_index=True).drop_duplicates("isin") if frames else pd.DataFrame(
        columns=["isin", "nse_symbol", "name", "nse_series", "listed_on", "segment"])


def parse_bse_master(path: str | Path) -> pd.DataFrame:
    """A 'List of Scrips' CSV exported from bseindia.com (Equity, Active or Suspended).

    Column names vary a little between exports, so they are matched loosely.
    """
    df = _norm_cols(pd.read_csv(path, dtype=str, skipinitialspace=True))

    def col(*names):
        for n in names:
            for c in df.columns:
                if c == n or c.replace(" ", "") == n.replace(" ", ""):
                    return df[c].astype(str).str.strip()
        return pd.Series([None] * len(df), index=df.index)

    out = pd.DataFrame({
        "isin": col("ISIN NO", "ISIN", "ISIN NUMBER"),
        "bse_code": col("SECURITY CODE", "SCRIP CODE", "SCRIP CD"),
        "bse_symbol": col("SECURITY ID", "SCRIP ID"),
        "bse_name": col("ISSUER NAME", "SECURITY NAME", "COMPANY NAME"),
        "bse_group": col("GROUP"),
        "bse_status": col("STATUS"),
        "bse_industry": col("INDUSTRY", "INDUSTRY NEW NAME", "SECTOR NAME"),
    })
    return out[is_equity_isin(out["isin"])]


def load_bse_master(ref_dir: str | Path) -> pd.DataFrame:
    files = sorted(Path(ref_dir).glob("bse_scrips*.csv")) if Path(ref_dir).exists() else []
    if not files:
        return pd.DataFrame(columns=["isin", "bse_code", "bse_symbol", "bse_name", "bse_group", "bse_status", "bse_industry"])
    df = pd.concat([parse_bse_master(f) for f in files], ignore_index=True)
    # Active beats Suspended if a scrip appears in both exports
    df["_rank"] = df["bse_status"].str.lower().ne("active").astype(int)
    return df.sort_values("_rank").drop_duplicates("isin").drop(columns="_rank")


def canonical_bse_isin(bse_raw: pd.DataFrame) -> pd.DataFrame:
    """Map every scrip's old ISINs onto its latest one.

    A split or face-value change gives a company a new ISIN but BSE keeps its scrip code,
    so without this the pre-split ISIN shows up as a separate, dormant company.
    """
    if bse_raw is None or bse_raw.empty:
        return bse_raw
    latest = bse_raw.sort_values("date").groupby("bse_code")["isin"].last()
    out = bse_raw.copy()
    out["isin"] = out["bse_code"].map(latest).fillna(out["isin"])
    out["symbol"] = out["isin"]
    return out


def bse_from_bhavcopies(bse_raw: pd.DataFrame) -> pd.DataFrame:
    """Latest identity of every scrip seen in the BSE bhavcopies."""
    if bse_raw is None or bse_raw.empty:
        return pd.DataFrame(columns=["isin", "bse_code", "bse_symbol", "bse_name", "bse_group"])
    last = bse_raw.sort_values("date").drop_duplicates("bse_code", keep="last").drop_duplicates("isin", keep="last")
    return pd.DataFrame({
        "isin": last["isin"], "bse_code": last["bse_code"], "bse_symbol": last["ticker"],
        "bse_name": last["name"], "bse_group": last["series"],
    })


def master_list(nse: pd.DataFrame, bse_seen: pd.DataFrame, bse_master: pd.DataFrame | None = None) -> pd.DataFrame:
    """One row per ISIN across NSE and BSE."""
    bse = bse_seen
    if bse_master is not None and len(bse_master):
        # master file wins for identity fields; bhavcopies fill anything it lacks
        bse = bse_master.merge(bse_seen, on="isin", how="outer", suffixes=("", "_seen"))
        for c in ("bse_code", "bse_symbol", "bse_name", "bse_group"):
            bse[c] = bse[c].where(bse[c].notna() & (bse[c] != "None"), bse[f"{c}_seen"])
        bse = bse.drop(columns=[c for c in bse.columns if c.endswith("_seen")])
    out = nse.merge(bse, on="isin", how="outer")
    out["name"] = out["name"].fillna(out.get("bse_name"))
    on_nse, on_bse = out["nse_symbol"].notna(), out["bse_code"].notna()
    bse_sme = out.get("bse_group", pd.Series(index=out.index, dtype=object)).isin(BSE_SME)
    out["segment"] = out["segment"].fillna(pd.Series(["SME" if x else "Main" for x in bse_sme], index=out.index))
    out["exchange"] = [
        "NSE+BSE" if n and b else "NSE" if n else "BSE" for n, b in zip(on_nse, on_bse)
    ]
    out["symbol"] = out["nse_symbol"].fillna(out["bse_symbol"]).fillna(out["isin"])
    return out.drop(columns=[c for c in ("bse_name",) if c in out]).reset_index(drop=True)


def status_for(row: dict, as_of: pd.Timestamp, stale_after_days: int = 7) -> str:
    """Active / Trade-to-trade / SME / No recent trades / No trades in 1Y / Suspended."""
    if str(row.get("bse_status") or "").lower().startswith("susp") and row.get("exchange") == "BSE":
        return "Suspended"
    last = row.get("last_trade")
    if last is None or pd.isna(last):
        return "No trades in 1Y"
    if (as_of - pd.Timestamp(last)).days > stale_after_days:
        return "No recent trades"
    series = row.get("series")
    if series in NSE_T2T or series in BSE_T2T:
        return "Trade-to-trade"
    if row.get("segment") == "SME":
        return "SME"
    return "Active"
