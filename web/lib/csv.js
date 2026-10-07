import { columnsFor } from "./columns";

// Round half to even, like numpy/pandas, so this file matches `python main.py export` byte for byte.
function roundHalfEven(v, d) {
  const m = 10 ** d;
  const x = v * m;
  const f = Math.floor(x);
  const diff = x - f;
  if (diff === 0.5) return (f % 2 === 0 ? f : f + 1) / m;  // exact tie only, as numpy.rint
  return Math.round(x) / m;
}

function cell(v, col) {
  if (v === null || v === undefined || (typeof v === "number" && !Number.isFinite(v))) return "";
  let s;
  if (col.type === "bool") s = v ? "Yes" : "No";
  else if (col.type === "int") s = String(roundHalfEven(v, 0));
  else if (col.type === "date") s = String(v).slice(0, 10);
  else if (typeof v === "number") s = String(roundHalfEven(v, 2));
  else s = String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** Scanner CSV: exact published column order (extended columns appended on request). */
export function toCsv(rows, extended = false) {
  const cols = columnsFor(extended);
  const lines = [cols.map((c) => c.header).join(",")];
  for (const r of rows) lines.push(cols.map((c) => cell(r[c.key], c)).join(","));
  return lines.join("\n") + "\n";
}
