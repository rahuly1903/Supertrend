// Formatting helpers shared by server and client components.
const nf = (d) => new Intl.NumberFormat("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });
const F0 = nf(0), F1 = nf(1), F2 = nf(2);

export const isNum = (v) => typeof v === "number" && Number.isFinite(v);

export function num(v, d = 2) {
  if (!isNum(v)) return "—";
  return (d === 0 ? F0 : d === 1 ? F1 : F2).format(v);
}

export function pct(v, d = 1, signed = true, suffix = "%") {
  if (!isNum(v)) return "—";
  const s = (d === 0 ? F0 : d === 1 ? F1 : F2).format(v);
  return `${signed && v > 0 ? "+" : ""}${s}${suffix}`;
}

export function compact(v) {
  if (!isNum(v)) return "—";
  if (Math.abs(v) >= 1e7) return `${F2.format(v / 1e7)} Cr`;
  if (Math.abs(v) >= 1e5) return `${F2.format(v / 1e5)} L`;
  return F0.format(v);
}

export function inr(v, d = 2) {
  return isNum(v) ? `₹${num(v, d)}` : "—";
}

export function date(v) {
  if (!v) return "—";
  return typeof v === "string" ? v.slice(0, 10) : v.toISOString().slice(0, 10);
}

export function niceDate(v) {
  if (!v) return "—";
  const d = new Date(`${date(v)}T00:00:00Z`);
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" });
}

export function toneClass(v) {
  if (!isNum(v) || v === 0) return "text-muted";
  return v > 0 ? "text-up" : "text-down";
}
