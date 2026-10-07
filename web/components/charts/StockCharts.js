"use client";

import { useMemo, useState } from "react";
import TimeChart from "./TimeChart";

const SMA_COLORS = { sma50: "#5b8def", sma100: "#a855f7", sma150: "#f5b942", sma200: "#ec4899" };

function volume(data, key = "time") {
  return data.map((d) => ({
    [key]: d.time, value: d.volume ?? 0,
    color: d.close >= d.open ? "rgba(34,197,94,0.35)" : "rgba(240,82,82,0.35)",
  }));
}

export function WeeklyChart({ weekly, supertrend }) {
  const series = useMemo(() => {
    const candles = weekly.map((w) => ({ time: w.week_end_date, open: w.open, high: w.high, low: w.low, close: w.close, volume: w.volume }));
    // Supertrend as two series (support below / resistance above) with whitespace gaps,
    // so the line breaks at a flip like TradingView instead of drawing a vertical jump.
    const stSide = (dir) => supertrend.map((s) => (s.direction === dir
      ? { time: s.week_end_date, value: s.st_value } : { time: s.week_end_date }));
    const markers = [];
    for (let i = 1; i < supertrend.length; i++) {
      if (supertrend[i].direction !== supertrend[i - 1].direction) {
        const up = supertrend[i].direction === 1;
        markers.push({
          time: supertrend[i].week_end_date, position: up ? "belowBar" : "aboveBar",
          color: up ? "--up" : "--down", shape: up ? "arrowUp" : "arrowDown", text: up ? "Buy" : "Exit",
        });
      }
    }
    return [
      { type: "candle", data: candles, markers,
        options: { upColor: "--up", downColor: "--down", wickUpColor: "--up", wickDownColor: "--down", borderVisible: false } },
      ...[1, -1].map((dir) => ({
        type: "line", data: stSide(dir),
        options: {
          color: dir === 1 ? "--up" : "--down", lineWidth: 2, crosshairMarkerVisible: false,
          // only the live side gets a price label
          lastValueVisible: supertrend.at(-1)?.direction === dir, title: supertrend.at(-1)?.direction === dir ? "ST" : "",
        },
      })),
      { type: "histogram", data: volume(candles), options: { priceScaleId: "vol", priceFormat: { type: "volume" } },
        scaleMargins: { top: 0.82, bottom: 0 } },
    ];
  }, [weekly, supertrend]);
  return <TimeChart series={series} height={420} />;
}

export function DailyChart({ daily }) {
  const [visible, setVisible] = useState(["sma50", "sma150", "sma200"]);
  const series = useMemo(() => {
    const candles = daily.map((d) => ({ time: d.date, open: d.open, high: d.high, low: d.low, close: d.close, volume: d.volume }));
    return [
      { type: "candle", data: candles,
        options: { upColor: "--up", downColor: "--down", wickUpColor: "--up", wickDownColor: "--down", borderVisible: false } },
      ...visible.map((k) => ({
        type: "line", data: daily.filter((d) => d[k] != null).map((d) => ({ time: d.date, value: d[k] })),
        options: { color: SMA_COLORS[k], lineWidth: 1.5, title: k.toUpperCase(), crosshairMarkerVisible: false },
      })),
      { type: "histogram", data: volume(candles), options: { priceScaleId: "vol", priceFormat: { type: "volume" } },
        scaleMargins: { top: 0.82, bottom: 0 } },
    ];
  }, [daily, visible]);
  return (
    <div>
      <div className="flex gap-1.5 mb-2">
        {Object.entries(SMA_COLORS).map(([k, c]) => {
          const on = visible.includes(k);
          return (
            <button key={k} onClick={() => setVisible((v) => (on ? v.filter((x) => x !== k) : [...v, k]))}
              className={`text-[11px] px-2 py-0.5 rounded border ${on ? "border-transparent text-black font-medium" : "border-line text-muted"}`}
              style={on ? { background: c } : undefined}>{k.toUpperCase()}</button>
          );
        })}
      </div>
      <TimeChart series={series} height={380} />
    </div>
  );
}
