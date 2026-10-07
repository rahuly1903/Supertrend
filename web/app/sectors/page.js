import DataTable from "@/components/DataTable";
import RRGChart from "@/components/charts/RRGChart";
import ScoreHistory from "@/components/charts/ScoreHistory";
import { NoRuns, Section, SectorLink } from "@/components/ui";
import { getSectors, resolveRun } from "@/lib/data";
import { niceDate, pct } from "@/lib/format";

export const metadata = { title: "Sectors" };

const COLUMNS = [
  { key: "sector_rank", header: "Rank", type: "int" },
  { key: "industry", header: "Sector", type: "sector" },
  { key: "sector_score", header: "Score", type: "num", d: 1 },
  { key: "stock_count", header: "Stocks", type: "int" },
  { key: "ret_1w", header: "1W", type: "pct" },
  { key: "ret_4w", header: "4W", type: "pct" },
  { key: "ret_13w", header: "13W", type: "pct" },
  { key: "ret_26w", header: "26W", type: "pct" },
  { key: "ret_52w", header: "52W", type: "pct" },
  { key: "rs_trend", header: "RS trend", type: "num", d: 3, tone: 1 },
  { key: "st_breadth", header: "ST breadth", type: "pct", d: 0, unsigned: true },
  { key: "pct_above_sma50", header: "% > SMA50", type: "pct", d: 0, unsigned: true },
  { key: "pct_above_sma200", header: "% > SMA200", type: "pct", d: 0, unsigned: true },
  { key: "breadth_change_4w", header: "Breadth Δ4W", type: "pct", d: 0, suffix: " pts" },
  { key: "risk_adj_return", header: "Risk-adj", type: "num", d: 2, tone: 0 },
  { key: "rrg_quadrant", header: "RRG", type: "quadrant" },
  { key: "tradeable", header: "Tradeable", type: "bool" },
];

const PERIODS = [["ret_1w", "1W"], ["ret_4w", "4W"], ["ret_13w", "13W"], ["ret_26w", "26W"], ["ret_52w", "52W"]];

function Heatmap({ rows, week }) {
  const maxAbs = Object.fromEntries(PERIODS.map(([k]) => [k, Math.max(...rows.map((r) => Math.abs(r[k] ?? 0)), 1)]));
  const bg = (v, k) => {
    if (v == null) return undefined;
    const a = Math.min(Math.abs(v) / maxAbs[k], 1) * 0.55 + 0.05;
    return v >= 0 ? `rgba(34,197,94,${a})` : `rgba(240,82,82,${a})`;
  };
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs tabular border-separate border-spacing-[2px]">
        <thead>
          <tr className="text-muted">
            <th className="text-left font-medium px-2 py-1">Sector</th>
            {PERIODS.map(([k, h]) => <th key={k} className="font-medium px-2 py-1 w-20">{h}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.industry}>
              <td className="px-2 py-1 whitespace-nowrap"><SectorLink industry={r.industry} week={week} /></td>
              {PERIODS.map(([k]) => (
                <td key={k} className="text-center px-2 py-1 rounded" style={{ background: bg(r[k], k) }}>{pct(r[k], 1)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default async function SectorsPage({ searchParams }) {
  const sp = await searchParams;
  const run = await resolveRun(sp);
  if (!run) return <NoRuns />;
  const { rows, tails, history } = await getSectors(run);
  const tradeable = rows.filter((r) => r.tradeable).map((r) => r.industry);

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-baseline justify-between flex-wrap gap-2">
        <h1 className="text-xl font-semibold">Sector leaderboard</h1>
        <span className="text-xs text-muted">
          Week ending {niceDate(run.week)} · {tradeable.length ? `Tradeable: ${tradeable.join(", ")}` : "No tradeable sectors this week"}
        </span>
      </div>

      <DataTable rows={rows} columns={COLUMNS} week={sp.week} rowKey="industry" maxHeight="none" />
      <p className="text-xs text-muted -mt-3">
        Score = 30% rank(13W return) + 30% rank(RS trend) + 20% rank(ST breadth) + 20% rank(26W return / volatility).
        Tradeable = rank ≤ 5, ST breadth ≥ 60%, RS trend &gt; 1, RRG Leading or Improving.
      </p>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-5">
        <Section title="Relative rotation graph (4-week tails, vs Nifty 500)">
          <RRGChart tails={tails} highlight={tradeable} />
        </Section>
        <Section title="Sector score history (26 weeks)">
          <ScoreHistory history={history} sectors={rows.map((r) => r.industry)} />
        </Section>
      </div>

      <Section title="Returns heatmap">
        <Heatmap rows={rows} week={sp.week} />
      </Section>
    </div>
  );
}
