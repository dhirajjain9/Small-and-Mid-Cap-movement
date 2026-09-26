"""Tunable thresholds. Every number here is a placeholder to be tuned in-sample only."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

TRAIN_START = pd.Timestamp("2018-01-01")
TRAIN_END = pd.Timestamp("2022-12-31")  # build on data up to here
TEST_START = pd.Timestamp("2023-01-01")  # validate on this and later only

CRORE = 1e7
LAKH = 1e5


@dataclass(frozen=True)
class UniverseConfig:
    """Tradability filter. A stock must pass every check on a given day."""

    adtv_window: int = 20  # days for average daily traded value
    min_adtv_cr: float = 15.0  # ₹ crore
    max_position_pct_of_adtv: float = 0.01  # my position <= 1% of ADTV
    allowed_bands: tuple = (20.0, float("inf"))  # 20% band, or no band (encoded as inf)
    min_mcap_cr: float = 1_000.0
    series: tuple = ("EQ",)
    min_history_days: int = 60  # need enough bars for the signal lookbacks


@dataclass(frozen=True)
class CostModel:
    """Indian equity delivery costs. Rates as of 2024-25; update if they change."""

    slippage_round_trip: float = 0.0075  # 0.5-1% per round trip; split half per side
    brokerage_flat_per_order: float = 20.0  # ₹ per executed order (0 for zero-brokerage delivery)
    brokerage_pct: float = 0.0  # alternative percentage brokerage per side
    stt_pct: float = 0.001  # 0.1% on buy and sell for delivery
    exchange_txn_pct: float = 0.0000297  # NSE transaction charges
    sebi_pct: float = 0.000001  # ₹10 per crore
    stamp_duty_buy_pct: float = 0.00015  # 0.015% on buy
    gst_pct: float = 0.18  # on brokerage + exchange + SEBI charges
    dp_charge_per_sell: float = 15.93  # depository charge per scrip sold
    stcg_rate: float = 0.20  # short-term capital gains tax

    def buy_charges(self, value: float) -> float:
        brokerage = self.brokerage_flat_per_order + self.brokerage_pct * value
        exch = self.exchange_txn_pct * value
        sebi = self.sebi_pct * value
        gst = self.gst_pct * (brokerage + exch + sebi)
        return brokerage + exch + sebi + gst + self.stt_pct * value + self.stamp_duty_buy_pct * value

    def sell_charges(self, value: float) -> float:
        brokerage = self.brokerage_flat_per_order + self.brokerage_pct * value
        exch = self.exchange_txn_pct * value
        sebi = self.sebi_pct * value
        gst = self.gst_pct * (brokerage + exch + sebi)
        return brokerage + exch + sebi + gst + self.stt_pct * value + self.dp_charge_per_sell

    def net_return(self, entry_px: float, exit_px: float, notional: float) -> float:
        """Net return of a trade after slippage and charges, before tax.

        Prices are raw fills; slippage is applied here, half on each side.
        """
        half = self.slippage_round_trip / 2
        buy_px = entry_px * (1 + half)
        sell_px = exit_px * (1 - half)
        qty = notional / buy_px
        buy_val, sell_val = qty * buy_px, qty * sell_px
        pnl = sell_val - buy_val - self.buy_charges(buy_val) - self.sell_charges(sell_val)
        return pnl / buy_val


@dataclass(frozen=True)
class VolumeBreakoutConfig:
    high_lookback: int = 30  # multi-week high: close above the prior N-day high
    volume_lookback: int = 50  # "normal" volume = median of prior N days
    volume_mult: float = 2.5  # 2-3x normal volume
    min_deliv_pct: float = 40.0  # absolute delivery % floor
    deliv_rel_mult: float = 1.0  # and delivery % >= this x its own 50-day average
    min_close_location: float = 0.5  # close in top half of the day's range (0=low, 1=high)
    atr_window: int = 14
    stop_atr_mult: float = 2.0
    target_pct: float | None = None  # None = calibrate on in-sample median move
    time_stop_days: int = 20
    forward_horizons: tuple = (1, 5, 20)


@dataclass(frozen=True)
class PortfolioConfig:
    capital: float = 10 * LAKH
    risk_per_trade: float = 0.01  # risk ~1% of capital per trade
    max_position_pct: float = 0.15  # no single position above 15% of capital
    max_positions: int = 10
    max_gross_exposure: float = 1.0  # cap on total small-cap exposure (x capital)
    max_per_sector: int = 3  # only enforced when a sector map is given
    liquidity_pct_of_adtv: float = 0.01


@dataclass(frozen=True)
class KillBar:
    """A playbook goes live only if its out-of-sample results clear all of these."""

    min_trades: int = 30
    min_expectancy: float = 0.005  # mean net return per trade after costs, pre-tax
    min_profit_factor: float = 1.3
    min_post_tax_cagr: float = 0.10  # beat a boring alternative after tax
    max_drawdown: float = 0.30


@dataclass(frozen=True)
class Settings:
    universe: UniverseConfig = field(default_factory=UniverseConfig)
    costs: CostModel = field(default_factory=CostModel)
    breakout: VolumeBreakoutConfig = field(default_factory=VolumeBreakoutConfig)
    portfolio: PortfolioConfig = field(default_factory=PortfolioConfig)
    kill_bar: KillBar = field(default_factory=KillBar)
