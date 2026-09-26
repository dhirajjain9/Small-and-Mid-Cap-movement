export function Sparkline({ values, width = 96, height = 28 }: { values: number[] | null; width?: number; height?: number }) {
  if (!values || values.length < 2) return <span className="muted">–</span>;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const step = width / (values.length - 1);
  const pts = values.map((v, i) => `${(i * step).toFixed(1)},${(height - 2 - ((v - min) / span) * (height - 4)).toFixed(1)}`);
  const up = values[values.length - 1] >= values[0];
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className={`spark ${up ? "up" : "down"}`} aria-hidden>
      <polyline points={pts.join(" ")} fill="none" strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}
