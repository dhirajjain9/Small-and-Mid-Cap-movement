export type Cap = "Large" | "Mid" | "Small" | "Micro";

export interface Stock {
  symbol: string;
  name: string | null;
  industry: string | null;
  cap: Cap | null;
  close: number;
  chg_1d: number | null;
  chg_1w: number | null;
  chg_1m: number | null;
  chg_3m: number | null;
  chg_6m: number | null;
  chg_1y: number | null;
  high_52w: number | null;
  low_52w: number | null;
  from_high: number | null;
  volume: number;
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
  universe: { min_adtv_cr: number; bands: string };
}
