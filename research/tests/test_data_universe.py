import warnings

import numpy as np
import pandas as pd
import pytest

from nsesig.config import UniverseConfig
from nsesig.data import adjust_for_corporate_actions, parse_sec_bhavdata_full, parse_sec_list
from nsesig.universe import add_circuit_flags, add_universe_features, tradability_mask

BHAV = """SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER
ABC, EQ, 02-Jan-2024, 100.00, 101.00, 105.00, 99.00, 104.00, 104.50, 102.3, 50000, 51.15, 1200, 30000, 60.00
ABC, BE, 02-Jan-2024, 10.00, 10.00, 10.50, 9.90, 10.20, 10.20, 10.1, 100, 0.01, 5, -, -
"""

SEC_LIST = """Symbol,Series,Security Name,Band,Remarks
ABC,EQ,ABC Ltd,20,
DEF,EQ,DEF Ltd,No Band,
GHI,EQ,GHI Ltd,5,
"""


def test_parse_bhavcopy():
    df = parse_sec_bhavdata_full(BHAV)
    row = df.iloc[0]
    assert row["symbol"] == "ABC" and row["close"] == 104.5 and row["deliv_pct"] == 60
    assert row["turnover"] == pytest.approx(51.15e5)
    assert np.isnan(df.iloc[1]["deliv_pct"])


def test_parse_sec_list():
    df = parse_sec_list(SEC_LIST).set_index("symbol")
    assert df.loc["ABC", "band"] == 20 and df.loc["DEF", "no_band"] and df.loc["GHI", "band"] == 5


def test_split_adjustment():
    df = pd.DataFrame({
        "date": pd.bdate_range("2024-01-01", periods=4), "symbol": "X",
        "open": [100, 102, 51, 52], "high": [100, 102, 51, 52], "low": [100, 102, 51, 52],
        "close": [100, 102, 51, 52], "prev_close": [100, 100, 51, 51], "volume": [10, 10, 20, 20],
    })
    adj = adjust_for_corporate_actions(df)
    assert adj["close"].tolist() == pytest.approx([50, 51, 51, 52])
    assert adj["volume"].tolist() == pytest.approx([20, 20, 20, 20])


def test_circuit_flags():
    df = pd.DataFrame({
        "open": [120, 110, 90], "high": [120, 115, 90], "low": [120, 108, 90], "close": [120, 112, 90],
        "prev_close": [100, 100, 100], "band": [20.0, 20.0, 10.0],
    })
    f = add_circuit_flags(df)
    assert f["locked_up"].tolist() == [True, False, False]
    assert f["locked_down"].tolist() == [False, False, True]


def test_tradability_filter():
    n = 70
    base = dict(series="EQ", open=100, high=101, low=99, close=100, prev_close=100, volume=1, deliv_pct=50,
                mcap_cr=2000.0, surveillance=False)
    rows = []
    for sym, band, turnover, mcap, surv in [
        ("OK", 20.0, 30e7, 2000, False), ("THIN", 20.0, 5e7, 2000, False), ("BAND5", 5.0, 30e7, 2000, False),
        ("NOBAND", np.inf, 30e7, 2000, False), ("TINY", 20.0, 30e7, 500, False), ("ASM", 20.0, 30e7, 2000, True),
    ]:
        for d in pd.bdate_range("2024-01-01", periods=n):
            rows.append({**base, "date": d, "symbol": sym, "band": band, "turnover": turnover, "mcap_cr": mcap,
                         "surveillance": surv})
    df = add_universe_features(pd.DataFrame(rows))
    m = tradability_mask(df, UniverseConfig(), position_value=20e5)
    last = df.assign(t=m["tradable"]).groupby("symbol")["t"].last()
    assert last.to_dict() == {"ASM": False, "BAND5": False, "NOBAND": True, "OK": True, "THIN": False, "TINY": False}
    # position of ₹20 lakh vs 1% of ₹30 cr ADTV = ₹30 lakh -> ok; ₹50 lakh would not be
    assert not tradability_mask(df, UniverseConfig(), position_value=50e5)["tradable"].any()


def test_split_inferred_when_prev_close_not_restated():
    # 1:5 split on day 3, but prev_close still shows the old price
    df = pd.DataFrame({
        "date": pd.bdate_range("2024-01-01", periods=4), "symbol": "X",
        "open": [500, 505, 101, 102], "high": [505, 510, 103, 104], "low": [495, 500, 99, 100],
        "close": [500, 505, 102, 103], "prev_close": [500, 500, 505, 102], "volume": [10, 10, 50, 50],
    })
    adj = adjust_for_corporate_actions(df)
    assert adj["close"].tolist() == pytest.approx([100, 101, 102, 103])
    assert adj["volume"].tolist() == pytest.approx([50, 50, 50, 50])
    assert adj["prev_close"].iloc[2] == pytest.approx(101)


def test_real_crash_in_band_not_treated_as_split():
    df = pd.DataFrame({
        "date": pd.bdate_range("2024-01-01", periods=3), "symbol": "X",
        "open": [100, 100, 80], "high": [101, 101, 82], "low": [99, 99, 80], "close": [100, 100, 80],
        "prev_close": [100, 100, 100], "volume": [10, 10, 30],
    })
    assert adjust_for_corporate_actions(df)["close"].tolist() == pytest.approx([100, 100, 80])


def test_missing_session_is_not_an_adjustment():
    # every stock's prev_close on day 3 reflects a session we don't have
    rows = []
    for sym in "ABCDE":
        rows += [
            {"date": pd.Timestamp("2024-01-01"), "symbol": sym, "open": 100, "high": 100, "low": 100, "close": 100, "prev_close": 100, "volume": 1},
            {"date": pd.Timestamp("2024-01-02"), "symbol": sym, "open": 100, "high": 100, "low": 100, "close": 100, "prev_close": 100, "volume": 1},
            {"date": pd.Timestamp("2024-01-04"), "symbol": sym, "open": 103, "high": 104, "low": 102, "close": 103, "prev_close": 102, "volume": 1},
        ]
    adj = adjust_for_corporate_actions(pd.DataFrame(rows))
    assert (adj["close"].groupby(adj["symbol"]).first() == 100).all()


def test_series_gap_is_not_an_adjustment():
    d = pd.bdate_range("2024-01-01", periods=4)
    other = [{"date": x, "symbol": f"O{i}", "open": 10, "high": 10, "low": 10, "close": 10, "prev_close": 10, "volume": 1}
             for x in d for i in range(10)]
    x = [  # X is absent on day 3 (traded in BE), comes back with prev_close from that day
        {"date": d[0], "symbol": "X", "open": 100, "high": 100, "low": 100, "close": 100, "prev_close": 100, "volume": 1},
        {"date": d[1], "symbol": "X", "open": 100, "high": 100, "low": 100, "close": 100, "prev_close": 100, "volume": 1},
        {"date": d[3], "symbol": "X", "open": 88, "high": 88, "low": 88, "close": 88, "prev_close": 90, "volume": 1},
    ]
    adj = adjust_for_corporate_actions(pd.DataFrame(other + x))
    assert adj.loc[adj["symbol"] == "X", "close"].tolist() == pytest.approx([100, 100, 88])
