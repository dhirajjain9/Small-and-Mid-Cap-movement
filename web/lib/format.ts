export const pct = (v: number | null | undefined, digits = 1) =>
  v == null ? "–" : `${v > 0 ? "+" : ""}${(v * 100).toFixed(digits)}%`;

export const num = (v: number | null | undefined, digits = 0) =>
  v == null ? "–" : v.toLocaleString("en-IN", { maximumFractionDigits: digits, minimumFractionDigits: digits });

export const price = (v: number | null | undefined) =>
  v == null ? "–" : v.toLocaleString("en-IN", { maximumFractionDigits: v < 100 ? 2 : 1, minimumFractionDigits: v < 100 ? 2 : 1 });

export const tone = (v: number | null | undefined) => (v == null || v === 0 ? "" : v > 0 ? "up" : "down");

export const bandLabel = (b: number | null) => (b == null ? "–" : b === 0 ? "None" : `${b}%`);
