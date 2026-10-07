"use client";

import { useMemo, useState } from "react";
import TimeChart from "./TimeChart";

const PALETTE = ["#5b8def", "#22c55e", "#f5b942", "#f05252", "#a855f7", "#14b8a6", "#ec4899", "#94a3b8"];

/** Sector score over the last N weekly runs. Top 5 sectors (current week) selected by default. */
export default function ScoreHistory({ history, sectors }) {
  const [selected, setSelected] = useState(() => sectors.slice(0, 5));

  const series = useMemo(() => selected.map((ind, i) => ({
    type: "line",
    data: history.filter((h) => h.industry === ind && h.sector_score != null)
      .map((h) => ({ time: h.week_end_date, value: h.sector_score })),
    options: { color: PALETTE[i % PALETTE.length], lineWidth: 2, title: "" },
  })), [history, selected]);

  const toggle = (ind) => setSelected((s) => (s.includes(ind) ? s.filter((x) => x !== ind) : [...s, ind].slice(-8)));

  return (
    <div>
      <div className="flex flex-wrap gap-1.5 mb-3">
        {sectors.map((ind) => {
          const i = selected.indexOf(ind);
          return (
            <button key={ind} onClick={() => toggle(ind)}
              className={`text-[11px] px-2 py-0.5 rounded border ${i >= 0 ? "border-transparent text-black font-medium" : "border-line text-muted"}`}
              style={i >= 0 ? { background: PALETTE[i % PALETTE.length] } : undefined}>
              {ind}
            </button>
          );
        })}
      </div>
      <TimeChart series={series} height={300} />
    </div>
  );
}
