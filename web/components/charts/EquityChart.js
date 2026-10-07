"use client";

import { useMemo } from "react";
import TimeChart from "./TimeChart";

export default function EquityChart({ equity }) {
  const [curve, dd] = useMemo(() => [
    [
      { type: "line", data: equity.filter((e) => e.benchmark != null).map((e) => ({ time: e.date, value: e.benchmark })),
        options: { color: "--muted", lineWidth: 2, title: "Nifty 500" } },
      { type: "line", data: equity.map((e) => ({ time: e.date, value: e.equity })),
        options: { color: "--accent", lineWidth: 2, title: "Strategy" } },
      // runs that add capital per buy: the money put in so far
      ...(equity.some((e) => e.contributed != null && e.contributed !== equity[0].contributed)
        ? [{ type: "line", data: equity.map((e) => ({ time: e.date, value: e.contributed })),
             options: { color: "--warn", lineWidth: 1, lineStyle: 2, title: "Capital put in" } }]
        : []),
    ],
    [
      { type: "histogram", data: equity.map((e) => ({ time: e.date, value: e.drawdown, color: "rgba(240,82,82,0.6)" })),
        options: { title: "Drawdown %", priceFormat: { type: "price", precision: 1, minMove: 0.1 } } },
      { type: "line", data: equity.map((e) => ({ time: e.date, value: e.invested_pct })),
        options: { color: "--warn", lineWidth: 1, title: "Invested %", priceScaleId: "left" } },
    ],
  ], [equity]);
  return (
    <div className="flex flex-col gap-2">
      <TimeChart series={curve} height={320} />
      <TimeChart series={dd} height={150} />
    </div>
  );
}
