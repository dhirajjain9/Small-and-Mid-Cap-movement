import warnings

import pandas as pd
import pytest

from nsesig.data import is_equity_isin, parse_bse_udiff
from nsesig.listings import parse_nse_list
from nsesig.screener import build_snapshot
from nsesig.synthetic import make_panel

warnings.simplefilter("ignore")

ISIN = {"A": "INE000A01011", "B": "INE000B01011", "C": "INE000C01011", "D": "INE000D01011",
        "E": "INE000E01011", "F": "INE000F01011"}


def _bse_panel(src: pd.DataFrame, isin: str, code: str, group: str = "B") -> pd.DataFrame:
    return src.assign(symbol=isin, isin=isin, bse_code=code, ticker=f"T{code}", name=f"Co {code}", series=group,
                      deliv_pct=float("nan"), band=float("nan"))


def test_isin_filter():
    s = pd.Series(["INE117A01022", "INF204KB14I2", "INE001A07TQ2", "IN9397D01014"])
    assert is_equity_isin(s).tolist() == [True, False, False, False]


def test_parse_bse_udiff_keeps_equity_only():
    text = (
        "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,FininstrmActlXpryDt,StrkPric,OptnTp,"
        "FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,"
        "TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks\n"
        "2026-09-25,2026-09-25,CM,BSE,STK,500002,INE117A01022,ABB,A,,,,,ABB INDIA LIMITED,7216.95,7216.95,7022.75,7057.9,"
        "7057.9,7115,,7052.88,,,2883,20467777,620,F1,1,\n"
        "2026-09-25,2026-09-25,CM,BSE,STK,890001,INE001A07TQ2,NCD1,F,,,,,SOME NCD,1000,1000,1000,1000,1000,1000,,1000,,,5,5000,1,F1,1,\n"
    )
    df = parse_bse_udiff(text)
    assert df["isin"].tolist() == ["INE117A01022"] and df["bse_code"].iloc[0] == "500002"
    assert df["close"].iloc[0] == 7057.9 and df["series"].iloc[0] == "A"
    assert parse_bse_udiff("<html>not found</html>") is None


def test_parse_nse_sme_list_columns():
    text = "SYMBOL,NAME_OF_COMPANY,SERIES,DATE_OF_LISTING,PAID_UP_VALUE,ISIN_NUMBER,FACE_VALUE,\nAXIOMGAS,Axiom Gas,ST,25-Sep-26,5,INE16J201028,5,\n"
    df = parse_nse_list(text, "SME")
    assert df["nse_symbol"].tolist() == ["AXIOMGAS"] and df["segment"].iloc[0] == "SME"


def test_all_companies_snapshot():
    nse = make_panel(n_symbols=2, start="2025-01-01", end="2026-06-30")  # SYM00, SYM01
    nse.loc[nse["symbol"] == "SYM01", "series"] = "SM"
    bse_src = make_panel(n_symbols=3, start="2025-01-01", end="2026-06-30", seed=3)
    bse = pd.concat([
        _bse_panel(bse_src[(bse_src["symbol"] == "SYM00") & (bse_src["date"] >= "2026-01-01")], ISIN["A"], "500001", "A"),  # dual-listed
        # same scrip under its pre-split ISIN: must fold into A, not appear as a dormant company
        _bse_panel(bse_src[(bse_src["symbol"] == "SYM00") & (bse_src["date"] < "2026-01-01")], "INE000Z01011", "500001", "A"),
        _bse_panel(bse_src[bse_src["symbol"] == "SYM01"], ISIN["D"], "500004"),  # BSE only, active
        _bse_panel(bse_src[(bse_src["symbol"] == "SYM02") & (bse_src["date"] <= "2026-04-30")], ISIN["E"], "500005"),
    ])
    nse_lists = pd.DataFrame({
        "isin": [ISIN["A"], ISIN["B"], ISIN["C"]], "nse_symbol": ["SYM00", "SYM01", "DORMANT"],
        "name": ["A Ltd", "B Ltd", "C Ltd"], "nse_series": ["EQ", "SM", "EQ"],
        "listed_on": pd.NaT, "segment": ["Main", "SME", "Main"],
    })
    bse_master = pd.DataFrame({"isin": [ISIN["F"]], "bse_code": ["500006"], "bse_symbol": ["FSUSP"], "bse_name": ["F Ltd"],
                               "bse_group": ["Z"], "bse_status": ["Suspended"], "bse_industry": ["Textiles"]})

    snap, meta = build_snapshot(nse, bse_panel=bse, nse_lists=nse_lists, bse_master=bse_master)
    by = snap.set_index("isin")
    assert len(snap) == 6 and meta["stocks"] == 6
    assert by.loc[ISIN["A"], "exchange"] == "NSE+BSE" and by.loc[ISIN["A"], "price_source"] == "NSE"
    assert by.loc[ISIN["A"], "status"] == "Active" and by.loc[ISIN["A"], "symbol"] == "SYM00"
    assert by.loc[ISIN["B"], "status"] == "SME" and by.loc[ISIN["B"], "segment"] == "SME"
    assert by.loc[ISIN["C"], "status"] == "No trades in 1Y" and pd.isna(by.loc[ISIN["C"], "close"])
    assert by.loc[ISIN["D"], "exchange"] == "BSE" and by.loc[ISIN["D"], "price_source"] == "BSE"
    assert by.loc[ISIN["D"], "symbol"] == "T500004" and by.loc[ISIN["D"], "close"] > 0
    assert by.loc[ISIN["E"], "status"] == "No recent trades" and by.loc[ISIN["E"], "last_trade"] <= "2026-04-30"
    assert pd.isna(by.loc[ISIN["E"], "chg_1d"]) and by.loc[ISIN["E"], "adtv_cr"] == 0
    assert by.loc[ISIN["F"], "status"] == "Suspended" and by.loc[ISIN["F"], "industry"] == "Textiles"
    assert meta["by_exchange"] == {"NSE+BSE": 1, "NSE": 2, "BSE": 3}


def test_calendar_horizons():
    panel = make_panel(n_symbols=1, start="2025-01-01", end="2026-06-30")
    snap, _ = build_snapshot(panel)
    s = panel.set_index("date")["close"]
    row = snap.iloc[0]
    assert row["chg_1y"] == pytest.approx(s.iloc[-1] / s[: pd.Timestamp("2025-06-30")].iloc[-1] - 1)
    assert row["chg_1w"] == pytest.approx(s.iloc[-1] / s[: pd.Timestamp("2026-06-23")].iloc[-1] - 1)
