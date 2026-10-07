// Small presentational pieces shared by server and client components.
import Link from "next/link";
import { isNum, pct } from "@/lib/format";

export function Badge({ tone = "muted", children, className = "" }) {
  const tones = {
    up: "bg-up-bg text-up", down: "bg-down-bg text-down", warn: "bg-warn-bg text-warn",
    accent: "bg-accent-bg text-accent", muted: "bg-panel-2 text-muted",
  };
  return (
    <span className={`inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-medium whitespace-nowrap ${tones[tone]} ${className}`}>
      {children}
    </span>
  );
}

export function DirectionBadge({ direction, isNew }) {
  if (!direction) return <span className="text-muted">—</span>;
  const up = direction === "Bullish";
  return <Badge tone={up ? "up" : "down"}>{up ? "▲" : "▼"} {direction}{isNew ? " · new" : ""}</Badge>;
}

export function GradeBadge({ grade }) {
  if (!grade) return <span className="text-muted">—</span>;
  const tone = grade === "A+" ? "up" : grade === "A" ? "accent" : grade === "B" ? "warn" : "muted";
  return <Badge tone={tone}>{grade}</Badge>;
}

export const QUADRANT_TONE = { Leading: "up", Improving: "accent", Weakening: "warn", Lagging: "down" };

export function QuadrantBadge({ q }) {
  return q ? <Badge tone={QUADRANT_TONE[q] ?? "muted"}>{q}</Badge> : <span className="text-muted">—</span>;
}

export function Pct({ v, d = 1, signed = true, suffix = "%" }) {
  const cls = !isNum(v) || v === 0 ? "text-muted" : v > 0 ? "text-up" : "text-down";
  return <span className={`tabular ${signed ? cls : ""}`}>{pct(v, d, signed, suffix)}</span>;
}

export function Stat({ label, value, sub, tone }) {
  const color = tone === "up" ? "text-up" : tone === "down" ? "text-down" : "";
  return (
    <div className="card px-4 py-3">
      <div className="text-xs text-muted">{label}</div>
      <div className={`text-2xl font-semibold tabular mt-0.5 ${color}`}>{value}</div>
      {sub ? <div className="text-xs text-muted mt-0.5">{sub}</div> : null}
    </div>
  );
}

export function Section({ title, right, children, className = "" }) {
  return (
    <section className={`card ${className}`}>
      <div className="flex items-center justify-between gap-3 px-4 py-2.5 border-b border-line">
        <h2 className="text-sm font-semibold">{title}</h2>
        {right}
      </div>
      <div className="p-4">{children}</div>
    </section>
  );
}

export function StockLink({ symbol, week }) {
  return (
    <Link href={`/stock/${encodeURIComponent(symbol)}${week ? `?week=${week}` : ""}`}
      className="font-medium text-accent hover:underline">{symbol}</Link>
  );
}

export function SectorLink({ industry, week, children }) {
  return (
    <Link href={`/sectors/${encodeURIComponent(industry)}${week ? `?week=${week}` : ""}`} className="hover:underline">
      {children ?? industry}
    </Link>
  );
}

export function Empty({ children }) {
  return <div className="text-sm text-muted py-6 text-center">{children}</div>;
}

export function NoRuns() {
  return (
    <div className="card p-8 text-center">
      <h1 className="text-lg font-semibold">No completed scan yet</h1>
      <p className="text-sm text-muted mt-2">Run <code className="px-1 bg-panel-2 rounded">python main.py weekly</code> in <code>scanner/</code>.</p>
    </div>
  );
}
