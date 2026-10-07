"use client";

import { usePathname } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { tableColumns } from "@/lib/columns";
import { applyFilters, defaultSort, FILTER_KEYS, parseFilters, PRESETS } from "@/lib/filters";
import DataTable from "./DataTable";

const GRADES = ["A+", "A", "B", "Watch"];
const SMAS = ["50", "150", "200"];

function MultiSelect({ label, options, value, onChange }) {
  const sel = value ? value.split(",") : [];
  const toggle = (o) => {
    const next = sel.includes(o) ? sel.filter((x) => x !== o) : [...sel, o];
    onChange(next.join(","));
  };
  return (
    <details className="relative">
      <summary className="input cursor-pointer list-none select-none whitespace-nowrap">
        {label}{sel.length ? <span className="text-accent"> ({sel.length})</span> : ""} ▾
      </summary>
      <div className="absolute z-40 mt-1 card p-2 max-h-72 overflow-auto w-64 shadow-xl">
        {sel.length ? <button className="text-xs text-accent mb-1" onClick={() => onChange("")}>Clear</button> : null}
        {options.map((o) => (
          <label key={o} className="flex items-center gap-2 text-xs py-0.5 cursor-pointer hover:text-text">
            <input type="checkbox" checked={sel.includes(o)} onChange={() => toggle(o)} /> {o}
          </label>
        ))}
      </div>
    </details>
  );
}

function NumberBox({ label, value, onChange, width = "w-16", placeholder }) {
  return (
    <label className="flex items-center gap-1.5 text-xs text-muted whitespace-nowrap">
      {label}
      <input type="number" className={`input ${width} tabular`} value={value ?? ""} placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

/** Rows arrive once per run; filtering/sorting runs in the browser and is mirrored to the URL. */
export default function ScannerView({ rows, options, initial, week }) {
  const pathname = usePathname();
  const [state, setState] = useState(() => {
    // A ?preset= link expands into explicit filter params.
    const presetParams = PRESETS[initial.preset]?.params ?? {};
    const s = { ...presetParams };
    for (const k of [...FILTER_KEYS, "ext", "sort", "desc"]) if (initial[k] != null) s[k] = initial[k];
    return s;
  });
  const set = (k, v) => setState((s) => {
    const n = { ...s };
    if (v === "" || v == null || v === false) delete n[k]; else n[k] = String(v);
    return n;
  });

  // Mirror state to the URL (shareable links). Filtering is client-side, so use the
  // native History API (Next.js syncs it with useSearchParams) instead of router.replace,
  // which would re-render the server page on every keystroke.
  const first = useRef(true);
  useEffect(() => {
    if (first.current) { first.current = false; return; }
    const qs = new URLSearchParams();
    if (week) qs.set("week", week);
    for (const [k, v] of Object.entries(state)) qs.set(k, v);
    const t = setTimeout(() => window.history.replaceState(null, "", `${pathname}?${qs}`), 150);
    return () => clearTimeout(t);
  }, [state, week, pathname]);

  const filtered = useMemo(() => applyFilters(rows, parseFilters(state)).sort(defaultSort), [rows, state]);
  const extended = state.ext === "1";
  // Stock score right after Symbol in both views (the CSV keeps the published column order)
  const columns = useMemo(() => tableColumns(true, { lead: ["stock_score"] })
    .filter((c) => c.key === "stock_score" || tableColumns(extended).some((x) => x.key === c.key)), [extended]);
  const sorting = state.sort ? [{ id: state.sort, desc: state.desc === "1" }] : [];
  const onSortChange = (next) => setState((s) => {
    const n = { ...s };
    if (next.length) { n.sort = next[0].id; n.desc = next[0].desc ? "1" : "0"; } else { delete n.sort; delete n.desc; }
    return n;
  });

  const activePreset = Object.entries(PRESETS).find(([, p]) =>
    Object.entries(p.params).every(([k, v]) => state[k] === v)
    && FILTER_KEYS.filter((k) => state[k] != null).every((k) => k in p.params))?.[0];

  const exportQs = new URLSearchParams({ ...(week ? { week } : {}), ...state });
  const bull = filtered.filter((r) => r.direction === "Bullish").length;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        {Object.entries(PRESETS).map(([k, p]) => (
          <button key={k} className={`btn ${activePreset === k ? "btn-active" : ""}`}
            onClick={() => setState({ ...p.params, ...(extended ? { ext: "1" } : {}) })}>{p.label}</button>
        ))}
        <button className="btn" onClick={() => setState(extended ? { ext: "1" } : {})}>Reset</button>
        <div className="ml-auto flex items-center gap-2">
          <label className="flex items-center gap-1.5 text-xs text-muted">
            <input type="checkbox" checked={extended} onChange={(e) => set("ext", e.target.checked ? "1" : "")} />
            Extended columns
          </label>
          <a className="btn btn-active" href={`/api/export.csv?${exportQs}`}>Export CSV</a>
        </div>
      </div>

      <div className="card p-3 flex flex-wrap items-center gap-x-4 gap-y-2">
        <input className="input w-44" placeholder="Search symbol / name" value={state.q ?? ""}
          onChange={(e) => set("q", e.target.value)} />
        <select className="input" value={state.dir ?? ""} onChange={(e) => set("dir", e.target.value)}>
          <option value="">All directions</option><option>Bullish</option><option>Bearish</option>
        </select>
        <MultiSelect label="Sector" options={options.sectors} value={state.sector} onChange={(v) => set("sector", v)} />
        <MultiSelect label="Index" options={options.indices} value={state.index} onChange={(v) => set("index", v)} />
        <div className="flex items-center gap-1">
          {GRADES.map((g) => {
            const sel = (state.grade ?? "").split(",").filter(Boolean);
            const on = sel.includes(g);
            return (
              <button key={g} className={`btn px-2 ${on ? "btn-active" : ""}`}
                onClick={() => set("grade", (on ? sel.filter((x) => x !== g) : [...sel, g]).join(","))}>{g}</button>
            );
          })}
        </div>
        <label className="flex items-center gap-1.5 text-xs text-muted">
          <input type="checkbox" checked={state.qualified === "1"} onChange={(e) => set("qualified", e.target.checked ? "1" : "")} />
          Strict 8 Filter only
        </label>
        <NumberBox label="Weeks" value={state.wmin} onChange={(v) => set("wmin", v)} width="w-14" placeholder="min" />
        <NumberBox label="–" value={state.wmax} onChange={(v) => set("wmax", v)} width="w-14" placeholder="max" />
        <NumberBox label="Score ≥" value={state.minScore} onChange={(v) => set("minScore", v)} width="w-14" />
        <label className="flex items-center gap-1.5 text-xs text-muted" title="Liquidity, price and not in ASM/GSM">
          <input type="checkbox" checked={state.tradeable === "1"} onChange={(e) => set("tradeable", e.target.checked ? "1" : "")} />
          Tradeable only
        </label>
        <NumberBox label="Fresh ≤" value={state.fresh} onChange={(v) => set("fresh", v)} width="w-14" placeholder="wks" />
        <NumberBox label="% from high ≤" value={state.maxFromHigh} onChange={(v) => set("maxFromHigh", v)} />
        <NumberBox label="Mcap ≥ Cr" value={state.minMcap} onChange={(v) => set("minMcap", v)} width="w-20" />
        <NumberBox label="Turnover ≥ Cr" value={state.minTurnover} onChange={(v) => set("minTurnover", v)} />
        <div className="flex items-center gap-1 text-xs text-muted">
          Price &gt;
          {SMAS.map((n) => {
            const sel = (state.above ?? "").split(",").filter(Boolean);
            const on = sel.includes(n);
            return (
              <button key={n} className={`btn px-2 ${on ? "btn-active" : ""}`}
                onClick={() => set("above", (on ? sel.filter((x) => x !== n) : [...sel, n]).join(","))}>SMA{n}</button>
            );
          })}
        </div>
      </div>

      <div className="text-xs text-muted">
        {filtered.length} of {rows.length} stocks · {bull} bullish · {filtered.length - bull} bearish
      </div>
      <DataTable rows={filtered} columns={columns} week={week} sorting={sorting} onSortChange={onSortChange} maxHeight="72vh" />
    </div>
  );
}
