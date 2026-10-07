"use client";

import { useMemo } from "react";
import TimeChart from "./TimeChart";

export default function SectorVsBench({ series, name }) {
  const chartSeries = useMemo(() => {
    const first = series.find((p) => p.index_value && p.bench_value);
    if (!first) return [];
    const sector = series.map((p) => ({ time: p.week_end_date, value: (p.index_value / first.index_value) * 100 }));
    const bench = series.filter((p) => p.bench_value)
      .map((p) => ({ time: p.week_end_date, value: (p.bench_value / first.bench_value) * 100 }));
    return [
      { type: "line", data: bench, options: { color: "--muted", lineWidth: 2, title: "Nifty 500" } },
      { type: "line", data: sector, options: { color: "--accent", lineWidth: 2, title: name } },
    ];
  }, [series, name]);
  return <TimeChart series={chartSeries} height={320} />;
}
