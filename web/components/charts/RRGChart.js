"use client";

import { useMemo, useState } from "react";
import { num } from "@/lib/format";

const QUAD = {
  Leading: { fill: "var(--up-bg)", color: "var(--up)" },
  Weakening: { fill: "var(--warn-bg)", color: "var(--warn)" },
  Lagging: { fill: "var(--down-bg)", color: "var(--down)" },
  Improving: { fill: "var(--accent-bg)", color: "var(--accent)" },
};

const quadrantOf = (x, y) => (x >= 100 ? (y >= 100 ? "Leading" : "Weakening") : y >= 100 ? "Improving" : "Lagging");
const short = (s) => s.replace(/ and .*/, "").replace("Fast Moving Consumer Goods", "FMCG")
  .replace("Media Entertainment & Publication", "Media").replace("Oil Gas & Consumable Fuels", "Oil & Gas")
  .replace("Information Technology", "IT").replace("Automobile", "Auto");

/** Relative Rotation Graph: RS-Ratio (x) vs RS-Momentum (y), both centred on 100, with weekly tails. */
export default function RRGChart({ tails, highlight = [] }) {
  const [hover, setHover] = useState(null);
  const W = 720, H = 520, P = 40;

  const { sx, sy, ticksX, ticksY } = useMemo(() => {
    const pts = Object.values(tails).flat();
    const span = (key) => {
      const vals = pts.map((p) => p[key]);
      const d = Math.max(1, ...vals.map((v) => Math.abs(v - 100))) * 1.15;
      return [100 - d, 100 + d];
    };
    const [x0, x1] = span("rs_ratio");
    const [y0, y1] = span("rs_momentum");
    const sx = (v) => P + ((v - x0) / (x1 - x0)) * (W - 2 * P);
    const sy = (v) => H - P - ((v - y0) / (y1 - y0)) * (H - 2 * P);
    const ticks = (a, b) => {
      const step = (b - a) / 6;
      return Array.from({ length: 7 }, (_, i) => a + i * step);
    };
    return { sx, sy, ticksX: ticks(x0, x1), ticksY: ticks(y0, y1) };
  }, [tails]);

  const cx = sx(100), cy = sy(100);
  const entries = Object.entries(tails);

  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto select-none" role="img" aria-label="Relative rotation graph">
        <rect x={cx} y={P} width={W - P - cx} height={cy - P} fill={QUAD.Leading.fill} />
        <rect x={cx} y={cy} width={W - P - cx} height={H - P - cy} fill={QUAD.Weakening.fill} />
        <rect x={P} y={cy} width={cx - P} height={H - P - cy} fill={QUAD.Lagging.fill} />
        <rect x={P} y={P} width={cx - P} height={cy - P} fill={QUAD.Improving.fill} />
        {[["Leading", W - P - 8, P + 16, "end"], ["Weakening", W - P - 8, H - P - 8, "end"],
          ["Lagging", P + 8, H - P - 8, "start"], ["Improving", P + 8, P + 16, "start"]].map(([q, x, y, a]) => (
          <text key={q} x={x} y={y} textAnchor={a} fontSize="12" fontWeight="600" fill={QUAD[q].color} opacity="0.8">{q}</text>
        ))}
        <line x1={cx} x2={cx} y1={P} y2={H - P} stroke="var(--muted)" strokeDasharray="3 3" opacity="0.6" />
        <line x1={P} x2={W - P} y1={cy} y2={cy} stroke="var(--muted)" strokeDasharray="3 3" opacity="0.6" />
        {ticksX.map((t) => (
          <text key={`x${t}`} x={sx(t)} y={H - P + 16} textAnchor="middle" fontSize="10" fill="var(--muted)">{num(t, 1)}</text>
        ))}
        {ticksY.map((t) => (
          <text key={`y${t}`} x={P - 6} y={sy(t) + 3} textAnchor="end" fontSize="10" fill="var(--muted)">{num(t, 1)}</text>
        ))}
        <text x={W / 2} y={H - 6} textAnchor="middle" fontSize="11" fill="var(--muted)">RS-Ratio →</text>
        <text x={12} y={H / 2} textAnchor="middle" fontSize="11" fill="var(--muted)" transform={`rotate(-90 12 ${H / 2})`}>RS-Momentum →</text>

        {entries.map(([ind, pts]) => {
          const head = pts[pts.length - 1];
          const q = quadrantOf(head.rs_ratio, head.rs_momentum);
          const dim = hover && hover !== ind;
          const hi = highlight.includes(ind) || hover === ind;
          return (
            <g key={ind} opacity={dim ? 0.15 : 1} onMouseEnter={() => setHover(ind)} onMouseLeave={() => setHover(null)}
              style={{ cursor: "pointer" }}>
              <polyline fill="none" stroke={QUAD[q].color} strokeWidth={hi ? 2.2 : 1.4} opacity="0.75"
                points={pts.map((p) => `${sx(p.rs_ratio)},${sy(p.rs_momentum)}`).join(" ")} />
              {pts.slice(0, -1).map((p, i) => (
                <circle key={i} cx={sx(p.rs_ratio)} cy={sy(p.rs_momentum)} r="2" fill={QUAD[q].color} opacity={0.35 + i * 0.12} />
              ))}
              <circle cx={sx(head.rs_ratio)} cy={sy(head.rs_momentum)} r={hi ? 6 : 4.5} fill={QUAD[q].color}
                stroke="var(--panel)" strokeWidth="1.5" />
              <text x={sx(head.rs_ratio) + 7} y={sy(head.rs_momentum) - 6} fontSize={hi ? 12 : 10.5}
                fontWeight={hi ? 700 : 500} fill="var(--text)">{short(ind)}</text>
            </g>
          );
        })}
      </svg>
      {hover ? (
        <div className="absolute top-2 left-1/2 -translate-x-1/2 card px-3 py-1.5 text-xs tabular pointer-events-none">
          <b>{hover}</b> · RS-Ratio {num(tails[hover].at(-1).rs_ratio, 2)} · RS-Mom {num(tails[hover].at(-1).rs_momentum, 2)}
        </div>
      ) : null}
    </div>
  );
}
