"use client";

import { Fragment, useEffect, useMemo, useState } from "react";
import type { Cap, Meta, Status, Stock } from "@/lib/types";
import { bandLabel, num, pct, price, tone } from "@/lib/format";
import { Sparkline } from "./Sparkline";

type SortKey = keyof Pick<
  Stock,
  "symbol" | "close" | "mcap_cr" | "pe" | "revenue_cr" | "revenue_growth" | "chg_1d" | "chg_1w" | "chg_1m" | "chg_1y" | "from_high" | "volume_ratio" | "deliv_pct" | "adtv_cr" | "turnover_cr"
>;

const CAPS: (Cap | "Other")[] = ["Large", "Mid", "Small", "Micro", "Other"];

const STATUSES: Status[] = ["Active", "Trade-to-trade", "SME", "No recent trades", "No trades in 1Y", "Suspended"];

const EXCHANGES: { id: string; label: string; test: (s: Stock) => boolean }[] = [
  { id: "", label: "All exchanges", test: () => true },
  { id: "nse", label: "Listed on NSE", test: (s) => s.exchange === "NSE" || s.exchange === "NSE+BSE" },
  { id: "bse", label: "Listed on BSE", test: (s) => s.exchange === "BSE" || s.exchange === "NSE+BSE" },
  { id: "both", label: "NSE and BSE", test: (s) => s.exchange === "NSE+BSE" },
  { id: "nse-only", label: "NSE only", test: (s) => s.exchange === "NSE" },
  { id: "bse-only", label: "BSE only", test: (s) => s.exchange === "BSE" },
];

const STATUS_TONE: Record<string, string> = {
  "Trade-to-trade": "warn",
  SME: "dim",
  "No recent trades": "dim",
  "No trades in 1Y": "dim",
  Suspended: "down",
};

const shortDate = (d: string) =>
  new Date(d).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "2-digit" });

const PRESETS: { id: string; label: string; hint: string; test: (s: Stock) => boolean }[] = [
  { id: "all", label: "All companies", hint: "Every listed company on NSE and BSE, traded or not", test: () => true },
  {
    id: "traded",
    label: "Traded today",
    hint: "Had at least one trade in the latest session",
    test: (s) => s.turnover_cr != null,
  },
  {
    id: "universe",
    label: "My universe",
    hint: "Small + mid caps that pass the tradability filter",
    test: (s) => s.tradable && (s.cap === "Small" || s.cap === "Mid"),
  },
  { id: "breakout", label: "Breakouts today", hint: "Volume-breakout trigger fired on the last close", test: (s) => s.breakout },
  { id: "high", label: "Near 52-week high", hint: "Within 5% of the 52-week high", test: (s) => (s.from_high ?? -1) >= -0.05 },
  { id: "volume", label: "Volume spike", hint: "Volume at least 2x its 50-day median", test: (s) => (s.volume_ratio ?? 0) >= 2 },
  {
    id: "delivery",
    label: "High delivery",
    hint: "Delivery ≥ 60% and above its 20-day average",
    test: (s) => (s.deliv_pct ?? 0) >= 60 && (s.deliv_pct ?? 0) > (s.deliv_avg_20 ?? 100),
  },
  { id: "circuit", label: "At circuit", hint: "Locked at upper or lower circuit today", test: (s) => s.locked_up || s.locked_down },
];

const COLUMNS: { key: SortKey; label: string; title?: string; num?: boolean }[] = [
  { key: "symbol", label: "Stock" },
  { key: "close", label: "Price", num: true },
  { key: "mcap_cr", label: "Mcap ₹cr", title: "Market cap = last close × shares outstanding, ₹ crore", num: true },
  { key: "pe", label: "P/E", title: "Last close ÷ trailing 12-month EPS", num: true },
  { key: "revenue_cr", label: "Revenue ₹cr", title: "Revenue (sales), trailing 12 months, ₹ crore", num: true },
  { key: "revenue_growth", label: "Rev growth", title: "Revenue growth of the latest quarter vs the same quarter last year", num: true },
  { key: "chg_1d", label: "1D", num: true },
  { key: "chg_1w", label: "1W", num: true },
  { key: "chg_1m", label: "1M", num: true },
  { key: "chg_1y", label: "1Y", num: true },
  { key: "from_high", label: "vs 52W high", title: "% below the 52-week high", num: true },
  { key: "volume_ratio", label: "Vol ×", title: "Today's volume ÷ 50-day median", num: true },
  { key: "deliv_pct", label: "Deliv %", title: "Delivery % of traded quantity", num: true },
  { key: "adtv_cr", label: "Avg traded ₹cr", title: "Average daily traded value (price × shares traded) over 20 sessions, NSE + BSE, ₹ crore. Not revenue.", num: true },
];

const PAGE = 50;

// ISIN is unique across exchanges; a BSE-only ticker can match another company's NSE symbol
const rowKey = (s: Stock) => s.isin ?? s.symbol;

// Download columns: [header, field, kind]. "pct" fields are stored as fractions and exported as percentages.
const CSV_COLUMNS: [string, keyof Stock, "pct" | "num" | "text" | "bool"][] = [
  ["Symbol", "symbol", "text"],
  ["Company", "name", "text"],
  ["ISIN", "isin", "text"],
  ["NSE symbol", "nse_symbol", "text"],
  ["BSE code", "bse_code", "text"],
  ["Listed on", "exchange", "text"],
  ["Segment", "segment", "text"],
  ["Status", "status", "text"],
  ["Industry", "industry", "text"],
  ["Cap bucket", "cap", "text"],
  ["Last close (Rs)", "close", "num"],
  ["Last trade date", "last_trade", "text"],
  ["Market cap (Rs cr)", "mcap_cr", "num"],
  ["P/E (TTM)", "pe", "num"],
  ["EPS TTM (Rs)", "eps_ttm", "num"],
  ["Revenue TTM (Rs cr)", "revenue_cr", "num"],
  ["Revenue growth YoY (%)", "revenue_growth", "pct"],
  ["Net profit TTM (Rs cr)", "net_income_cr", "num"],
  ["Shares outstanding", "shares", "num"],
  ["Change 1D (%)", "chg_1d", "pct"],
  ["Change 1W (%)", "chg_1w", "pct"],
  ["Change 1M (%)", "chg_1m", "pct"],
  ["Change 3M (%)", "chg_3m", "pct"],
  ["Change 6M (%)", "chg_6m", "pct"],
  ["Change 1Y (%)", "chg_1y", "pct"],
  ["52W high (Rs)", "high_52w", "num"],
  ["52W low (Rs)", "low_52w", "num"],
  ["vs 52W high (%)", "from_high", "pct"],
  ["Volume (shares)", "volume", "num"],
  ["Volume vs 50D median (x)", "volume_ratio", "num"],
  ["Delivery (%)", "deliv_pct", "num"],
  ["Delivery 20D avg (%)", "deliv_avg_20", "num"],
  ["Traded value today, NSE+BSE (Rs cr)", "turnover_cr", "num"],
  ["Avg traded value 20D, NSE+BSE (Rs cr)", "adtv_cr", "num"],
  ["Price band (%, 0 = none)", "band", "num"],
  ["Upper circuit", "locked_up", "bool"],
  ["Lower circuit", "locked_down", "bool"],
  ["Tradable", "tradable", "bool"],
  ["Breakout today", "breakout", "bool"],
  ["Last breakout", "last_breakout", "text"],
  ["Loss-making", "loss_making", "bool"],
  ["Price source", "price_source", "text"],
];

function toCsv(rows: Stock[]) {
  const esc = (v: unknown) => (v == null ? "" : /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v));
  const cell = (r: Stock, [, key, kind]: (typeof CSV_COLUMNS)[number]) => {
    const v = r[key];
    if (v == null) return "";
    if (kind === "pct") return (Number(v) * 100).toFixed(2);
    if (kind === "num") return String(Math.round(Number(v) * 100) / 100);
    if (kind === "bool") return v ? "Yes" : "No";
    return esc(v);
  };
  const lines = [CSV_COLUMNS.map((c) => esc(c[0])).join(","), ...rows.map((r) => CSV_COLUMNS.map((c) => cell(r, c)).join(","))];
  return "\uFEFF" + lines.join("\r\n"); // BOM + CRLF so Excel opens it cleanly
}

export function Screener() {
  const [stocks, setStocks] = useState<Stock[] | null>(null);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [q, setQ] = useState("");
  const [preset, setPreset] = useState("all");
  const [caps, setCaps] = useState<Set<string>>(new Set());
  const [tradableOnly, setTradableOnly] = useState(false);
  const [minAdtv, setMinAdtv] = useState("");
  const [minMcap, setMinMcap] = useState("");
  const [maxPe, setMaxPe] = useState("");
  const [minRev, setMinRev] = useState("");
  const [minGrowth, setMinGrowth] = useState("");
  const [industry, setIndustry] = useState("");
  const [exchange, setExchange] = useState("");
  const [status, setStatus] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "turnover_cr", dir: -1 });
  const [page, setPage] = useState(0);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([fetch("/data/stocks.json"), fetch("/data/meta.json")])
      .then(async ([s, m]) => {
        if (!s.ok || !m.ok) throw new Error("No data yet: the daily job hasn't published a snapshot.");
        setStocks(await s.json());
        setMeta(await m.json());
      })
      .catch((e) => setError(String(e.message ?? e)));
  }, []);

  const industries = useMemo(
    () => Array.from(new Set((stocks ?? []).map((s) => s.industry).filter(Boolean) as string[])).sort(),
    [stocks],
  );

  const rows = useMemo(() => {
    if (!stocks) return [];
    const test = PRESETS.find((p) => p.id === preset)!.test;
    const exTest = EXCHANGES.find((e) => e.id === exchange)!.test;
    const needle = q.trim().toLowerCase();
    const adtv = parseFloat(minAdtv);
    const mcapMin = parseFloat(minMcap);
    const peMax = parseFloat(maxPe);
    const revMin = parseFloat(minRev);
    const growthMin = parseFloat(minGrowth) / 100;
    const out = stocks.filter(
      (s) =>
        test(s) &&
        exTest(s) &&
        (!status || s.status === status) &&
        (!needle ||
          s.symbol.toLowerCase().includes(needle) ||
          (s.name ?? "").toLowerCase().includes(needle) ||
          (s.bse_code ?? "").includes(needle) ||
          (s.isin ?? "").toLowerCase() === needle) &&
        (caps.size === 0 || caps.has(s.cap ?? "Other")) &&
        (!tradableOnly || s.tradable) &&
        (!industry || s.industry === industry) &&
        (isNaN(adtv) || (s.adtv_cr ?? 0) >= adtv) &&
        (isNaN(mcapMin) || (s.mcap_cr ?? 0) >= mcapMin) &&
        (isNaN(peMax) || (s.pe != null && s.pe <= peMax)) &&
        (isNaN(revMin) || (s.revenue_cr ?? -Infinity) >= revMin) &&
        (isNaN(growthMin) || (s.revenue_growth ?? -Infinity) >= growthMin),
    );
    const { key, dir } = sort;
    return out.sort((a, b) => {
      const x = a[key];
      const y = b[key];
      if (x == null) return 1;
      if (y == null) return -1;
      return (x < y ? -1 : x > y ? 1 : 0) * dir;
    });
  }, [stocks, preset, q, caps, tradableOnly, industry, exchange, status, minAdtv, minMcap, maxPe, minRev, minGrowth, sort]);

  useEffect(() => setPage(0), [preset, q, caps, tradableOnly, industry, exchange, status, minAdtv, minMcap, maxPe, minRev, minGrowth, sort]);

  const pages = Math.max(1, Math.ceil(rows.length / PAGE));
  const visible = rows.slice(page * PAGE, page * PAGE + PAGE);

  const toggleCap = (c: string) =>
    setCaps((prev) => {
      const next = new Set(prev);
      next.has(c) ? next.delete(c) : next.add(c);
      return next;
    });

  const sortBy = (key: SortKey) =>
    setSort((s) => (s.key === key ? { key, dir: (s.dir * -1) as 1 | -1 } : { key, dir: key === "symbol" ? 1 : -1 }));

  const download = () => {
    const blob = new Blob([toCsv(rows)], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `screener-${meta?.as_of ?? "data"}-${preset}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <main className="wrap">
      <header className="top">
        <div>
          <h1>India Stock Screener</h1>
          <p className="muted">
            {meta
              ? `${num(meta.stocks)} companies${meta.by_exchange ? ` (NSE+BSE ${num(meta.by_exchange["NSE+BSE"] ?? 0)} · NSE only ${num(meta.by_exchange["NSE"] ?? 0)} · BSE only ${num(meta.by_exchange["BSE"] ?? 0)})` : ""} · data as of ${new Date(meta.as_of).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" })} · ${meta.breakouts_today} breakouts today`
              : "Loading…"}
          </p>
        </div>
        <p className="disclaimer">
          Personal research tool. Not investment advice.
          {meta?.eps_as_of && <> P/E and revenue use trailing-12-month figures from Yahoo Finance, refreshed {shortDate(meta.eps_as_of)}.</>}
        </p>
      </header>

      {error && <div className="notice">{error}</div>}

      <section className="presets" aria-label="Screens">
        {PRESETS.map((p) => (
          <button key={p.id} className={`chip ${preset === p.id ? "on" : ""}`} onClick={() => setPreset(p.id)} title={p.hint}>
            {p.label}
            {stocks && <span className="count">{stocks.filter(p.test).length}</span>}
          </button>
        ))}
      </section>

      <section className="filters">
        <input className="search" placeholder="Name, symbol, BSE code or ISIN" value={q} onChange={(e) => setQ(e.target.value)} />
        <select value={exchange} onChange={(e) => setExchange(e.target.value)} aria-label="Exchange">
          {EXCHANGES.map((x) => (
            <option key={x.id} value={x.id}>
              {x.label}
            </option>
          ))}
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
          <option value="">All statuses</option>
          {STATUSES.map((x) => (
            <option key={x}>{x}</option>
          ))}
        </select>
        <div className="group" role="group" aria-label="Market cap">
          {CAPS.map((c) => (
            <button key={c} className={`chip small ${caps.has(c) ? "on" : ""}`} onClick={() => toggleCap(c)}>
              {c}
            </button>
          ))}
        </div>
        <select value={industry} onChange={(e) => setIndustry(e.target.value)} aria-label="Industry">
          <option value="">All industries</option>
          {industries.map((i) => (
            <option key={i}>{i}</option>
          ))}
        </select>
        <label className="field">
          Min revenue ₹cr
          <input inputMode="decimal" value={minRev} onChange={(e) => setMinRev(e.target.value)} placeholder="0" />
        </label>
        <label className="field">
          Min rev growth %
          <input inputMode="decimal" value={minGrowth} onChange={(e) => setMinGrowth(e.target.value)} placeholder="any" />
        </label>
        <label className="field" title="Average daily traded value, not revenue">
          Min avg traded ₹cr
          <input inputMode="decimal" value={minAdtv} onChange={(e) => setMinAdtv(e.target.value)} placeholder="0" />
        </label>
        <label className="field">
          Min mcap ₹cr
          <input inputMode="decimal" value={minMcap} onChange={(e) => setMinMcap(e.target.value)} placeholder="0" />
        </label>
        <label className="field">
          Max P/E
          <input inputMode="decimal" value={maxPe} onChange={(e) => setMaxPe(e.target.value)} placeholder="any" />
        </label>
        <label className="check">
          <input type="checkbox" checked={tradableOnly} onChange={(e) => setTradableOnly(e.target.checked)} />
          Tradable only
        </label>
        <button className="ghost" onClick={download} disabled={!rows.length}>
          Download CSV
        </button>
      </section>

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              {COLUMNS.map((c) => (
                <th key={c.key} className={c.num ? "num" : ""} title={c.title}>
                  <button onClick={() => sortBy(c.key)} className={sort.key === c.key ? "sorted" : ""}>
                    {c.label}
                    {sort.key === c.key ? (sort.dir === 1 ? " ↑" : " ↓") : ""}
                  </button>
                </th>
              ))}
              <th>1Y trend</th>
              <th>Flags</th>
            </tr>
          </thead>
          <tbody>
            {!stocks && !error &&
              Array.from({ length: 8 }).map((_, i) => (
                <tr key={i} className="skeleton">
                  <td colSpan={COLUMNS.length + 2}>&nbsp;</td>
                </tr>
              ))}
            {stocks && !visible.length && (
              <tr>
                <td colSpan={COLUMNS.length + 2} className="empty">
                  No stocks match these filters.
                </td>
              </tr>
            )}
            {visible.map((s) => (
              <Fragment key={rowKey(s)}>
                <tr className={`row ${open === rowKey(s) ? "open" : ""}`} onClick={() => setOpen(open === rowKey(s) ? null : rowKey(s))}>
                  <td className="stock">
                    <strong>{s.symbol}</strong>
                    <span className="muted name">
                      {s.name ?? ""}
                      {s.cap ? ` · ${s.cap}` : ""}
                      {s.exchange && s.exchange !== "NSE+BSE" ? ` · ${s.exchange} only` : ""}
                    </span>
                  </td>
                  <td className="num">
                    {price(s.close)}
                    {s.last_trade && meta && s.last_trade !== meta.as_of && (
                      <span className="muted stale">last {shortDate(s.last_trade)}</span>
                    )}
                  </td>
                  <td className="num">{num(s.mcap_cr, 0)}</td>
                  <td className={`num ${s.loss_making ? "down" : ""}`}>{s.pe != null ? num(s.pe, 1) : s.loss_making ? "Loss" : "–"}</td>
                  <td className="num">{num(s.revenue_cr, 0)}</td>
                  <td className={`num ${tone(s.revenue_growth)}`}>{pct(s.revenue_growth, 0)}</td>
                  {(["chg_1d", "chg_1w", "chg_1m", "chg_1y"] as const).map((k) => (
                    <td key={k} className={`num ${tone(s[k])}`}>
                      {pct(s[k])}
                    </td>
                  ))}
                  <td className="num">{pct(s.from_high)}</td>
                  <td className={`num ${(s.volume_ratio ?? 0) >= 2 ? "hot" : ""}`}>{s.volume_ratio == null ? "–" : `${s.volume_ratio.toFixed(1)}×`}</td>
                  <td className="num">{num(s.deliv_pct, 0)}</td>
                  <td className="num">{num(s.adtv_cr, 1)}</td>
                  <td>
                    <Sparkline values={s.spark} />
                  </td>
                  <td className="flags">
                    {s.status && s.status !== "Active" && <span className={`tag ${STATUS_TONE[s.status] ?? "dim"}`}>{s.status}</span>}
                    {s.breakout && <span className="tag go">Breakout</span>}
                    {s.locked_up && <span className="tag up">Upper circuit</span>}
                    {s.locked_down && <span className="tag down">Lower circuit</span>}
                    {s.band != null && s.band > 0 && s.band < 20 && <span className="tag warn">{s.band}% band</span>}
                    {!s.tradable && s.status === "Active" && s.price_source === "NSE" && <span className="tag dim">Untradable</span>}
                  </td>
                </tr>
                {open === rowKey(s) && (
                  <tr className="detail">
                    <td colSpan={COLUMNS.length + 2}>
                      <div className="detail-grid">
                        <div className="big-spark">
                          <Sparkline values={s.spark} width={280} height={80} />
                          <span className="muted">Weekly closes, last 52 weeks</span>
                        </div>
                        <dl>
                          <dt>Industry</dt><dd>{s.industry ?? "–"}</dd>
                          <dt>Listed on</dt><dd>{s.exchange ?? "–"}{s.segment === "SME" ? " (SME)" : ""}</dd>
                          <dt>ISIN</dt><dd>{s.isin ?? "–"}{s.bse_code ? ` · BSE ${s.bse_code}` : ""}</dd>
                          <dt>3M / 6M</dt><dd><span className={tone(s.chg_3m)}>{pct(s.chg_3m)}</span> / <span className={tone(s.chg_6m)}>{pct(s.chg_6m)}</span></dd>
                          <dt>Market cap</dt><dd>{s.mcap_cr == null ? "–" : `₹${num(s.mcap_cr, 0)} cr`}</dd>
                          <dt>EPS (TTM)</dt><dd>{s.eps_ttm == null ? "–" : `₹${num(s.eps_ttm, 2)}`}{s.pe != null ? ` · P/E ${num(s.pe, 1)}` : ""}</dd>
                          <dt>Revenue (TTM)</dt>
                          <dd>{s.revenue_cr == null ? "–" : `₹${num(s.revenue_cr, 0)} cr`}{s.revenue_growth != null ? ` · ${pct(s.revenue_growth, 0)} YoY` : ""}</dd>
                          <dt>Net profit (TTM)</dt><dd className={tone(s.net_income_cr)}>{s.net_income_cr == null ? "–" : `₹${num(s.net_income_cr, 0)} cr`}</dd>
                          <dt>Shares</dt><dd>{s.shares == null ? "–" : `${num(s.shares / 1e7, 2)} cr`}</dd>
                          <dt>52W range</dt><dd>{price(s.low_52w)} – {price(s.high_52w)}</dd>
                          <dt>Traded today</dt><dd>{s.turnover_cr == null ? "No trade today" : `₹${num(s.turnover_cr, 1)} cr (NSE + BSE)`}</dd>
                        </dl>
                        <dl>
                          <dt>Delivery</dt>
                          <dd>{s.deliv_pct == null && s.deliv_avg_20 == null ? "–" : `${num(s.deliv_pct, 1)}% (20D avg ${num(s.deliv_avg_20, 1)}%)`}</dd>
                          <dt>Price band</dt><dd>{bandLabel(s.band)}</dd>
                          <dt>Last trade</dt><dd>{s.last_trade ? `${shortDate(s.last_trade)}${s.price_source ? ` on ${s.price_source}` : ""}` : "None in the past year"}</dd>
                          <dt>Last breakout</dt><dd>{s.last_breakout ?? "None in the past year"}</dd>
                          <dt>Links</dt>
                          <dd>
                            {s.nse_symbol && (
                              <>
                                <a href={`https://www.nseindia.com/get-quotes/equity?symbol=${encodeURIComponent(s.nse_symbol)}`} target="_blank" rel="noreferrer">NSE</a>
                                {" · "}
                              </>
                            )}
                            {s.bse_code && (
                              <>
                                <a href={`https://www.bseindia.com/stock-share-price/x/${encodeURIComponent(s.symbol)}/${s.bse_code}/`} target="_blank" rel="noreferrer">BSE</a>
                                {" · "}
                              </>
                            )}
                            <a href={`https://www.screener.in/company/${encodeURIComponent(s.nse_symbol ?? s.bse_code ?? s.symbol)}/`} target="_blank" rel="noreferrer">Screener.in</a>
                          </dd>
                        </dl>
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>

      <footer className="pager">
        <span className="muted">
          {num(rows.length)} matches · page {page + 1} of {pages}
        </span>
        <div>
          <button className="ghost" disabled={page === 0} onClick={() => setPage(page - 1)}>
            ← Prev
          </button>
          <button className="ghost" disabled={page >= pages - 1} onClick={() => setPage(page + 1)}>
            Next →
          </button>
        </div>
      </footer>
    </main>
  );
}
