export type Cap = "Large" | "Mid" | "Small" | "Micro";

export type Status = "Active" | "Trade-to-trade" | "SME" | "No recent trades" | "No trades in 1Y" | "Suspended";
export type Exchange = "NSE+BSE" | "NSE" | "BSE";

export interface Stock {
  symbol: string;
  name: string | null;
  isin: string | null;
  exchange: Exchange | null;
  segment: "Main" | "SME" | null;
  status: Status | null;
  nse_symbol: string | null;
  bse_code: string | null;
  price_source: "NSE" | "BSE" | null;
  series: string | null;
  last_trade: string | null;
  industry: string | null;
  cap: Cap | null;
  close: number | null;
  chg_1d: number | null;
  chg_1w: number | null;
  chg_1m: number | null;
  chg_3m: number | null;
  chg_6m: number | null;
  chg_1y: number | null;
  high_52w: number | null;
  low_52w: number | null;
  from_high: number | null;
  volume: number | null;
  volume_ratio: number | null;
  deliv_pct: number | null;
  deliv_avg_20: number | null;
  turnover_cr: number | null;
  adtv_cr: number | null;
  /** price band in %, 0 = no band, null = unknown */
  band: number | null;
  locked_up: boolean;
  locked_down: boolean;
  tradable: boolean;
  breakout: boolean;
  last_breakout: string | null;
  spark: number[] | null;
}

export interface Meta {
  as_of: string;
  stocks: number;
  breakouts_today: number;
  has_reference: boolean;
  by_exchange?: Record<string, number>;
  by_status?: Record<string, number>;
  bse_master_file?: boolean;
  universe: { min_adtv_cr: number; bands: string };
}
