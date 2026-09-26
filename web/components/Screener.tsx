"use client";

import { Fragment, useEffect, useMemo, useState } from "react";
import type { Cap, Meta, Status, Stock } from "@/lib/types";
import { bandLabel, num, pct, price, tone } from "@/lib/format";
import { Sparkline } from "./Sparkline";

type SortKey = keyof Pick<
  Stock,
  "symbol" | "close" | "chg_1d" | "chg_1w" | "chg_1m" | "chg_1y" | "from_high" | "volume_ratio" | "deliv_pct" | "adtv_cr" | "turnover_cr"
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
  { key: "chg_1d", label: "1D", num: true },
  { key: "chg_1w", label: "1W", num: true },
  { key: "chg_1m", label: "1M", num: true },
  { key: "chg_1y", label: "1Y", num: true },
  { key: "from_high", label: "vs 52W high", title: "% below the 52-week high", num: true },
  { key: "volume_ratio", label: "Vol ×", title: "Today's volume ÷ 50-day median", num: true },
  { key: "deliv_pct", label: "Deliv %", title: "Delivery % of traded quantity", num: true },
  { key: "adtv_cr", label: "ADTV ₹cr", title: "20-day average traded value, ₹ crore", num: true },
];

const PAGE = 50;

function toCsv(rows: Stock[]) {
  const keys = Object.keys(rows[0] ?? {}).filter((k) => k !== "spark") as (keyof Stock)[];
  const esc = (v: unknown) => (v == null ? "" : /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v));
  return [keys.join(","), ...rows.map((r) => keys.map((k) => esc(r[k])).join(","))].join("\n");
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
        (isNaN(adtv) || (s.adtv_cr ?? 0) >= adtv),
    );
    const { key, dir } = sort;
    return out.sort((a, b) => {
      const x = a[key];
      const y = b[key];
      if (x == null) return 1;
      if (y == null) return -1;
      return (x < y ? -1 : x > y ? 1 : 0) * dir;
    });
  }, [stocks, preset, q, caps, tradableOnly, industry, exchange, status, minAdtv, sort]);

  useEffect(() => setPage(0), [preset, q, caps, tradableOnly, industry, exchange, status, minAdtv, sort]);

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
        <p className="disclaimer">Personal research tool. Not investment advice.</p>
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
          Min ADTV ₹cr
          <input inputMode="decimal" value={minAdtv} onChange={(e) => setMinAdtv(e.target.value)} placeholder="0" />
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
              <Fragment key={s.symbol}>
                <tr className={`row ${open === s.symbol ? "open" : ""}`} onClick={() => setOpen(open === s.symbol ? null : s.symbol)}>
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
                {open === s.symbol && (
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
                          <dt>52W range</dt><dd>{price(s.low_52w)} – {price(s.high_52w)}</dd>
                          <dt>Today&apos;s value</dt><dd>{s.turnover_cr == null ? "No trade today" : `₹${num(s.turnover_cr, 1)} cr`}</dd>
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
