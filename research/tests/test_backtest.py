import warnings

import numpy as np
import pandas as pd
import pytest

from nsesig.backtest import (
    PriceBook, calibrate_target, kill_bar_verdict, run_portfolio, run_volume_breakout, simulate_trade,
)
from nsesig.config import CostModel, PortfolioConfig
from nsesig.synthetic import make_panel

warnings.simplefilter("ignore")


def bars(rows, band=20.0):
    """rows: list of (open, high, low, close). prev_close chained from the close."""
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    df["prev_close"] = df["close"].shift(1).fillna(df["open"].iloc[0])
    df["date"] = pd.bdate_range("2024-01-01", periods=len(df))
    df["symbol"] = "X"
    df["band"] = band
    return PriceBook(df), df


def run(rows, target=0.10, stop_mult=1.0, time_stop=5, atr=5.0, band=20.0):
    book, _ = bars(rows, band)
    return simulate_trade(book.data["X"], 0, atr, stop_mult, target, time_stop, book.last_date)


def test_entry_next_open_and_target():
    r = run([(100, 101, 99, 100), (100, 102, 99, 101), (101, 111, 100, 110)])
    assert r["entry_px"] == 100 and r["exit_reason"] == "target" and r["exit_px"] == pytest.approx(110)


def test_stop_wins_when_both_hit_same_day():
    r = run([(100, 101, 99, 100), (100, 101, 99, 100), (100, 112, 94, 100)])
    assert r["exit_reason"] == "stop" and r["exit_px"] == 95


def test_gap_below_stop_fills_at_open():
    r = run([(100, 101, 99, 100), (100, 101, 99, 100), (90, 92, 89, 91)])
    assert r["exit_reason"] == "stop_gap" and r["exit_px"] == 90


def test_entry_skipped_when_open_at_upper_circuit():
    # day 1 opens at +20% (limit) and stays there
    r = run([(100, 101, 99, 100), (120, 120, 120, 120), (120, 125, 118, 121)])
    assert r["status"] == "entry_circuit"


def test_lower_circuit_lock_delays_exit():
    rows = [(100, 101, 99, 100), (100, 101, 99, 100), (80, 80, 80, 80), (64, 64, 64, 64), (60, 66, 58, 62)]
    r = run(rows, time_stop=10)
    assert r["exit_reason"] == "stop_gap_after_lock" and r["exit_px"] == 60 and r["bars_held"] == 4


def test_time_stop_exits_at_close():
    rows = [(100, 101, 99, 100)] + [(100, 101, 99, 100.5)] * 6
    r = run(rows, time_stop=3)
    assert r["exit_reason"] == "time" and r["bars_held"] == 3 and r["exit_px"] == 100.5


def test_costs_eat_into_flat_trade():
    c = CostModel()
    r = c.net_return(100, 100, 1e5)
    # 0.75% slippage + 0.2% STT + small charges
    assert -0.012 < r < -0.0095


def test_target_calibration_ignores_out_of_sample():
    o = pd.DataFrame({"date": pd.to_datetime(["2020-01-01", "2021-01-01", "2024-01-01"]), "mfe": [0.1, 0.2, 5.0]})
    assert calibrate_target(o) == pytest.approx(0.15)


def test_stcg_with_loss_carry_forward():
    t = pd.DataFrame({
        "status": "filled", "symbol": ["A", "B", "C"], "score": 1.0,
        "entry_date": pd.to_datetime(["2023-05-01", "2023-06-01", "2024-05-01"]),
        "exit_date": pd.to_datetime(["2023-05-10", "2023-06-10", "2024-05-10"]),
        "entry_px": 100.0, "stop": 90.0, "gross_ret": [-0.08, -0.08, 0.30], "adtv": np.nan,
    })
    cfg = PortfolioConfig(capital=1e6, max_position_pct=0.1)
    log, pre, post = run_portfolio(t, cfg, CostModel(slippage_round_trip=0, brokerage_flat_per_order=0, dp_charge_per_sell=0))
    fy24_loss = log["pnl"].iloc[:2].sum()
    fy25_gain = log["pnl"].iloc[2]
    assert fy24_loss < 0
    expected_tax = 0.2 * (fy25_gain + fy24_loss)
    assert pre.iloc[-1] - post.iloc[-1] == pytest.approx(expected_tax)


def test_kill_bar():
    good = kill_bar_verdict({"trades": 50, "expectancy": 0.02, "profit_factor": 1.8}, {"cagr": 0.2, "max_drawdown": 0.1})
    assert good["passes"]
    bad = kill_bar_verdict({"trades": 10, "expectancy": 0.02, "profit_factor": 1.8}, {"cagr": 0.2, "max_drawdown": 0.1})
    assert not bad["passes"] and not bad["checks"]["enough_trades"]


def test_end_to_end_synthetic():
    r = run_volume_breakout(make_panel(n_symbols=12))
    assert r.in_sample.trades["trades"] > 0 and r.out_of_sample.trades["trades"] > 0
    assert (r.out_of_sample.trade_log["signal_date"] >= pd.Timestamp("2023-01-01")).all()
    assert (r.in_sample.trade_log["signal_date"] <= pd.Timestamp("2022-12-31")).all()
    assert set(r.verdict["checks"]) >= {"expectancy", "post_tax_cagr"}
