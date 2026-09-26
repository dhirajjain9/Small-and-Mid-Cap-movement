import warnings

import pandas as pd
import pytest

from nsesig.screener import build_snapshot, reference_table
from nsesig.synthetic import make_panel

warnings.simplefilter("ignore")


def test_changes_match_prices():
    panel = make_panel(n_symbols=5, start="2024-01-01", end="2025-06-30")
    snap, meta = build_snapshot(panel)
    s = panel[panel["symbol"] == "SYM02"].sort_values("date")["close"].to_numpy()
    row = snap.set_index("symbol").loc["SYM02"]
    assert row["chg_1d"] == pytest.approx(s[-1] / s[-2] - 1)
    assert row["chg_1m"] == pytest.approx(s[-1] / s[-22] - 1)
    assert row["close"] == pytest.approx(s[-1])
    assert meta["as_of"] == "2025-06-30" and meta["stocks"] == 5
    assert len(row["spark"]) == 52


def test_reference_drops_etfs_and_sets_cap():
    panel = make_panel(n_symbols=3, start="2025-01-01", end="2025-06-30")
    refs = {
        "equity": pd.DataFrame({"SYMBOL": ["SYM00", "SYM01"], "NAME OF COMPANY": ["Zero Ltd", "One Ltd"]}),
        "Small": pd.DataFrame({"Company Name": ["Zero Ltd"], "Industry": ["Chemicals"], "Symbol": ["SYM00"]}),
    }
    snap, _ = build_snapshot(panel, refs)
    by = snap.set_index("symbol")
    assert "SYM02" not in by.index  # not in EQUITY_L, e.g. an ETF
    assert by.loc["SYM00", "cap"] == "Small" and by.loc["SYM00", "industry"] == "Chemicals"
    assert by.loc["SYM01", "name"] == "One Ltd" and pd.isna(by.loc["SYM01", "cap"])


def test_reference_table_first_index_wins():
    refs = {
        "Large": pd.DataFrame({"Company Name": ["A"], "Industry": ["X"], "Symbol": ["A"]}),
        "Mid": pd.DataFrame({"Company Name": ["A"], "Industry": ["X"], "Symbol": ["A"]}),
    }
    assert reference_table(refs).set_index("symbol").loc["A", "cap"] == "Large"
