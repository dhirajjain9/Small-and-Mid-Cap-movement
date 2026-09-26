"""Trade simulation, portfolio sizing, tax and the kill bar.

Fill assumptions (all deliberately conservative):
- Signal at day t's close, entry at day t+1's open. If t+1 opens at the upper
  circuit, the order is assumed unfilled and the trade is skipped.
- A gap through the stop or target fills at the open.
- If stop and target are both inside one day's range, the stop is assumed hit first.
- A day locked at the lower circuit is untradeable: the exit carries to the next
  tradeable open.
- Time stop exits at the close of the Nth day held.
- If a stock stops trading (delisting, suspension), the position exits at the last
  close minus `delist_haircut`.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import TEST_START, TRAIN_END, CostModel, KillBar, PortfolioConfig, Settings
from .universe import add_circuit_flags

_FIELDS = ("date", "open", "high", "low", "close", "open_at_upper", "locked_down", "adtv")


class PriceBook:
    """Per-symbol numpy arrays for fast path simulation."""

    def __init__(self, df: pd.DataFrame):
        if "locked_down" not in df:
            df = add_circuit_flags(df)
        if "adtv" not in df:
            df = df.assign(adtv=np.nan)
        self.last_date = df["date"].max()
        self.data: dict[str, dict[str, np.ndarray]] = {}
        self.index: dict[str, dict[pd.Timestamp, int]] = {}
        for sym, g in df.sort_values("date").groupby("symbol", sort=False):
            arrs = {f: g[f].to_numpy() for f in _FIELDS}
            self.data[sym] = arrs
            self.index[sym] = {d: i for i, d in enumerate(pd.DatetimeIndex(arrs["date"]))}


def forward_outcomes(book: PriceBook, sigs: pd.DataFrame, horizons=(1, 5, 20), mfe_window: int = 20) -> pd.DataFrame:
    """Event study: close-to-close forward returns, plus max favourable move from the next open."""
    out = {f"fwd_{h}d": [] for h in horizons}
    out["mfe"] = []
    for sym, date in zip(sigs["symbol"], sigs["date"]):
        a, i = book.data[sym], book.index[sym][date]
        n = len(a["close"])
        for h in horizons:
            out[f"fwd_{h}d"].append(a["close"][i + h] / a["close"][i] - 1 if i + h < n else np.nan)
        if i + 1 < n:
            hi = a["high"][i + 1 : i + 1 + mfe_window].max()
            out["mfe"].append(hi / a["open"][i + 1] - 1)
        else:
            out["mfe"].append(np.nan)
    return sigs.assign(**out)


def calibrate_target(outcomes: pd.DataFrame, train_end: pd.Timestamp = TRAIN_END) -> float:
    """Target = median max favourable move of in-sample signals. Never looks past `train_end`."""
    ins = outcomes.loc[outcomes["date"] <= train_end, "mfe"].dropna()
    if ins.empty:
        raise ValueError("no in-sample signals to calibrate the target on")
    return float(ins.median())


def simulate_trade(a: dict, i: int, atr: float, stop_mult: float, target_pct: float, time_stop: int, last_date) -> dict:
    n = len(a["close"])
    j = i + 1
    if j >= n:
        return {"status": "no_next_bar"}
    if a["open_at_upper"][j]:
        return {"status": "entry_circuit"}
    entry = a["open"][j]
    stop = entry - stop_mult * atr
    target = entry * (1 + target_pct)
    pending = None  # reason waiting on a locked-down day
    exit_k = exit_px = reason = None
    for k in range(j, n):
        o, h, lo, c = a["open"][k], a["high"][k], a["low"][k], a["close"][k]
        locked = a["locked_down"][k]
        if pending:
            if locked:
                continue
            exit_k, exit_px, reason = k, o, pending + "_after_lock"
            break
        if k > j and o <= stop:
            want = ("stop_gap", o)
        elif k > j and o >= target:
            want = ("target_gap", o)
        elif lo <= stop:
            want = ("stop", stop)
        elif h >= target:
            want = ("target", target)
        elif k - j + 1 >= time_stop:
            want = ("time", c)
        else:
            continue
        if locked:
            pending = want[0]
            continue
        exit_k, (reason, exit_px) = k, want
        break
    delisted = False
    if exit_k is None:
        exit_k = n - 1
        delisted = pd.Timestamp(a["date"][-1]) < last_date - pd.Timedelta(days=10)
        reason = "delisted" if delisted else "open"
        exit_px = a["close"][-1]
    path = slice(j, exit_k + 1)
    return {
        "status": "filled",
        "entry_date": pd.Timestamp(a["date"][j]),
        "exit_date": pd.Timestamp(a["date"][exit_k]),
        "entry_px": entry,
        "exit_px": exit_px,
        "stop": stop,
        "target": target,
        "exit_reason": reason,
        "bars_held": exit_k - j + 1,
        "mfe": a["high"][path].max() / entry - 1,
        "mae": a["low"][path].min() / entry - 1,
        "adtv": a["adtv"][i],
    }


def simulate_trades(
    book: PriceBook,
    sigs: pd.DataFrame,
    target_pct: float,
    stop_mult: float,
    time_stop: int,
    costs: CostModel = CostModel(),
    notional: float = 1e5,
    delist_haircut: float = 0.0,
) -> pd.DataFrame:
    rows = []
    for rec in sigs.itertuples(index=False):
        a = book.data[rec.symbol]
        r = simulate_trade(a, book.index[rec.symbol][rec.date], rec.atr, stop_mult, target_pct, time_stop, book.last_date)
        rows.append({"signal_date": rec.date, "symbol": rec.symbol, "score": rec.score, **r})
    t = pd.DataFrame(rows)
    if t.empty or "entry_px" not in t:
        return t
    filled = t["status"] == "filled"
    exit_px = t["exit_px"] * np.where(t["exit_reason"] == "delisted", 1 - delist_haircut, 1.0)
    t["gross_ret"] = exit_px / t["entry_px"] - 1
    t.loc[filled, "net_ret"] = [
        costs.net_return(e, x, notional) for e, x in zip(t.loc[filled, "entry_px"], exit_px[filled])
    ]
    return t


# --------------------------------------------------------------------------- portfolio

def _fy(d: pd.Timestamp) -> int:
    """Indian financial year label (FY ending March): Apr 2023-Mar 2024 -> 2024."""
    return d.year + 1 if d.month >= 4 else d.year


def run_portfolio(
    trades: pd.DataFrame,
    cfg: PortfolioConfig = PortfolioConfig(),
    costs: CostModel = CostModel(),
    sectors: dict[str, str] | None = None,
) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """Pick and size trades under capital, exposure and sector caps.

    Returns (taken trades with ₹ P&L, pre-tax equity, post-tax equity). Equity is
    realised (booked on exit dates); tax is STCG on each FY's net gains, losses
    carried forward.
    """
    t = trades[trades["status"] == "filled"].sort_values(["entry_date", "score"], ascending=[True, False])
    equity = cfg.capital
    open_pos: list[dict] = []
    taken = []
    half = costs.slippage_round_trip / 2
    for rec in t.itertuples(index=False):
        for p in [p for p in open_pos if p["exit_date"] < rec.entry_date]:
            equity += p["pnl"]
            open_pos.remove(p)
        if len(open_pos) >= cfg.max_positions:
            continue
        if any(p["symbol"] == rec.symbol for p in open_pos):
            continue
        sector = (sectors or {}).get(rec.symbol)
        if sector and sum(p["sector"] == sector for p in open_pos) >= cfg.max_per_sector:
            continue
        per_share_risk = rec.entry_px * (1 + half) - rec.stop
        if per_share_risk <= 0:
            continue
        value = cfg.risk_per_trade * equity / per_share_risk * rec.entry_px
        room = cfg.max_gross_exposure * equity - sum(p["value"] for p in open_pos)
        caps = [value, cfg.max_position_pct * equity, room]
        if pd.notna(rec.adtv):
            caps.append(cfg.liquidity_pct_of_adtv * rec.adtv)
        value = min(caps)
        if value < 10_000:  # flat charges would dominate
            continue
        pnl = value * costs.net_return(rec.entry_px, rec.entry_px * (1 + rec.gross_ret), value)
        pos = {**rec._asdict(), "value": value, "pnl": pnl, "sector": sector}
        open_pos.append(pos)
        taken.append(pos)
    log = pd.DataFrame(taken)
    if log.empty:
        return log, pd.Series(dtype=float), pd.Series(dtype=float)

    pre = cfg.capital + log.groupby("exit_date")["pnl"].sum().sort_index().cumsum()
    # STCG by financial year with loss carry-forward.
    fy_pnl = log.groupby(log["exit_date"].map(_fy))["pnl"].sum()
    carry, tax_by_fy = 0.0, {}
    for fy, p in fy_pnl.items():
        taxable = p + carry
        tax_by_fy[fy] = max(taxable, 0.0) * costs.stcg_rate
        carry = min(taxable, 0.0)
    tax_dates = {fy: pd.Timestamp(f"{fy}-03-31") for fy in tax_by_fy}
    tax = pd.Series({tax_dates[fy]: v for fy, v in tax_by_fy.items()})
    idx = pre.index.union(tax.index)
    post = pre.reindex(idx).ffill().fillna(cfg.capital) - tax.reindex(idx).fillna(0).cumsum()
    return log, pre, post


# --------------------------------------------------------------------------- stats

def trade_stats(t: pd.DataFrame) -> dict:
    f = t[t["status"] == "filled"] if "status" in t else t
    r = f["net_ret"].dropna()
    if r.empty:
        return {"trades": 0}
    wins, losses = r[r > 0], r[r <= 0]
    return {
        "trades": len(r),
        "hit_rate": float((r > 0).mean()),
        "expectancy": float(r.mean()),
        "median_ret": float(r.median()),
        "avg_win": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss": float(losses.mean()) if len(losses) else 0.0,
        "profit_factor": float(wins.sum() / -losses.sum()) if losses.sum() < 0 else float("inf"),
        "avg_bars_held": float(f["bars_held"].mean()),
        "exit_reasons": f["exit_reason"].value_counts().to_dict(),
        "skipped_entry_circuit": int((t.get("status") == "entry_circuit").sum()) if "status" in t else 0,
    }


def curve_stats(equity: pd.Series, start_capital: float, start: pd.Timestamp, end: pd.Timestamp) -> dict:
    if equity.empty:
        return {"cagr": 0.0, "max_drawdown": 0.0, "final": start_capital}
    years = max((end - start).days / 365.25, 1 / 365.25)
    final = float(equity.iloc[-1])
    path = pd.concat([pd.Series([start_capital], index=[start]), equity])
    dd = (path / path.cummax() - 1).min()
    return {"cagr": (final / start_capital) ** (1 / years) - 1, "max_drawdown": float(-dd), "final": final}


def event_stats(outcomes: pd.DataFrame, horizons=(1, 5, 20)) -> dict:
    res = {"signals": len(outcomes)}
    for h in horizons:
        col = outcomes[f"fwd_{h}d"].dropna()
        res[f"{h}d_median"] = float(col.median()) if len(col) else np.nan
        res[f"{h}d_hit_rate"] = float((col > 0).mean()) if len(col) else np.nan
    return res


def kill_bar_verdict(tstats: dict, post_tax: dict, bar: KillBar = KillBar()) -> dict:
    checks = {
        "enough_trades": tstats.get("trades", 0) >= bar.min_trades,
        "expectancy": tstats.get("expectancy", -1) >= bar.min_expectancy,
        "profit_factor": tstats.get("profit_factor", 0) >= bar.min_profit_factor,
        "post_tax_cagr": post_tax.get("cagr", -1) >= bar.min_post_tax_cagr,
        "drawdown": post_tax.get("max_drawdown", 1) <= bar.max_drawdown,
    }
    return {"passes": all(checks.values()), "checks": checks}


# --------------------------------------------------------------------------- runner

@dataclass
class PeriodResult:
    name: str
    events: dict
    trades: dict
    pre_tax: dict
    post_tax: dict
    trade_log: pd.DataFrame
    equity_post_tax: pd.Series


@dataclass
class BacktestResult:
    playbook: str
    target_pct: float
    in_sample: PeriodResult
    out_of_sample: PeriodResult
    verdict: dict  # judged on out-of-sample only
    signals: pd.DataFrame | None = None  # every signal with its forward moves
    trades: pd.DataFrame | None = None  # every signal simulated as a trade (before portfolio limits)


def run_volume_breakout(
    panel: pd.DataFrame,
    settings: Settings = Settings(),
    sectors: dict[str, str] | None = None,
    train_end: pd.Timestamp = TRAIN_END,
    test_start: pd.Timestamp = TEST_START,
    delist_haircut: float = 0.0,
) -> BacktestResult:
    from .playbooks import volume_breakout as vb
    from .universe import add_universe_features, tradability_mask

    cfg = settings.breakout
    df = add_circuit_flags(add_universe_features(panel, settings.universe))
    df = vb.add_features(df, cfg)
    mask = tradability_mask(df, settings.universe)["tradable"]
    sigs = vb.signals(df, mask, cfg)
    book = PriceBook(df)
    outcomes = forward_outcomes(book, sigs, cfg.forward_horizons, cfg.time_stop_days)
    target = cfg.target_pct if cfg.target_pct is not None else calibrate_target(outcomes, train_end)
    trades = simulate_trades(
        book, outcomes, target, cfg.stop_atr_mult, cfg.time_stop_days, settings.costs, delist_haircut=delist_haircut
    )

    def period(name, lo, hi):
        sel = (outcomes["date"] >= lo) & (outcomes["date"] <= hi)
        tsel = (trades["signal_date"] >= lo) & (trades["signal_date"] <= hi) if len(trades) else sel[:0]
        tp = trades[tsel] if len(trades) else trades
        log, pre, post = run_portfolio(tp, settings.portfolio, settings.costs, sectors) if len(tp) else (tp, pd.Series(dtype=float), pd.Series(dtype=float))
        start = max(lo, df["date"].min())
        end = min(hi, df["date"].max())
        cap = settings.portfolio.capital
        return PeriodResult(
            name, event_stats(outcomes[sel], cfg.forward_horizons), trade_stats(tp) if len(tp) else {"trades": 0},
            curve_stats(pre, cap, start, end), curve_stats(post, cap, start, end), log, post,
        )

    ins = period("in_sample", df["date"].min(), train_end)
    oos = period("out_of_sample", test_start, df["date"].max())
    verdict = kill_bar_verdict(oos.trades, oos.post_tax, settings.kill_bar)
    return BacktestResult(vb.NAME, target, ins, oos, verdict, outcomes, trades)
