"use client";

import {
  CandlestickSeries, createChart, createSeriesMarkers, HistogramSeries, LineSeries,
} from "lightweight-charts";
import { useEffect, useRef } from "react";

// Colours may be CSS custom properties ("--up"); canvases need real values, so they are
// resolved at draw time and re-resolved when the theme class on <html> changes.
function css(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
function resolve(c) {
  return typeof c === "string" && c.startsWith("--") ? css(c) : c;
}
function resolveDeep(o) {
  if (Array.isArray(o)) return o.map(resolveDeep);
  if (o && typeof o === "object") return Object.fromEntries(Object.entries(o).map(([k, v]) => [k, resolveDeep(v)]));
  return resolve(o);
}

function chartTheme() {
  return {
    layout: {
      background: { color: css("--panel") }, textColor: css("--muted"), fontSize: 11,
      attributionLogo: false,
    },
    grid: { vertLines: { color: css("--line") }, horzLines: { color: css("--line") } },
    rightPriceScale: { borderColor: css("--line") },
    timeScale: { borderColor: css("--line") },
    crosshair: { mode: 0 },
  };
}

const TYPES = { line: LineSeries, candle: CandlestickSeries, histogram: HistogramSeries };

/**
 * series: [{ type: 'line'|'candle'|'histogram', data, options, markers, scaleMargins }]
 * Data points use { time: 'YYYY-MM-DD', value } or OHLC; per-point `color` is allowed.
 */
export default function TimeChart({ series, height = 360, rightOffset = 4 }) {
  const ref = useRef(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const chart = createChart(el, {
      ...chartTheme(), autoSize: true,
      timeScale: { ...chartTheme().timeScale, rightOffset },
    });
    let apis = [];
    const draw = () => {
      apis = [];
      for (const s of series) {
        const api = chart.addSeries(TYPES[s.type], resolveDeep({
          priceLineVisible: false, lastValueVisible: s.type !== "histogram", ...s.options,
        }));
        api.setData(resolveDeep(s.data));
        if (s.scaleMargins) api.priceScale().applyOptions({ scaleMargins: s.scaleMargins });
        if (s.markers?.length) createSeriesMarkers(api, resolveDeep(s.markers));
        apis.push(api);
      }
      chart.timeScale().fitContent();
    };
    draw();

    // Re-theme on dark/light toggle: rebuild series so per-point colours update too.
    const mo = new MutationObserver(() => {
      chart.applyOptions(chartTheme());
      for (const api of apis) chart.removeSeries(api);
      draw();
    });
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => { mo.disconnect(); chart.remove(); };
  }, [series, rightOffset]);

  return <div ref={ref} style={{ height }} className="w-full" />;
}
