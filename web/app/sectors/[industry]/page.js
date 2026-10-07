import { notFound } from "next/navigation";
import DataTable from "@/components/DataTable";
import SectorVsBench from "@/components/charts/SectorVsBench";
import { NoRuns, Pct, QuadrantBadge, Badge, Section, Stat } from "@/components/ui";
import { tableColumns } from "@/lib/columns";
import { getSectorDetail, resolveRun } from "@/lib/data";
import { defaultSort } from "@/lib/filters";
import { niceDate, num, pct } from "@/lib/format";

export async function generateMetadata({ params }) {
  return { title: decodeURIComponent((await params).industry) };
}

export default async function SectorDetail({ params, searchParams }) {
  const [{ industry: raw }, sp] = await Promise.all([params, searchParams]);
  const industry = decodeURIComponent(raw);
  const run = await resolveRun(sp);
  if (!run) return <NoRuns />;
  const data = await getSectorDetail(run, industry);
  if (!data) notFound();
  const { row: s, stocks, series } = data;
  const rows = [...stocks].sort(defaultSort);
  const columns = tableColumns(true, { lead: ["stock_score", "grade", "qualified"], omit: ["industry"] });

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <div className="text-xs text-muted">Sector · rank #{s.sector_rank} of 20 · week ending {niceDate(run.week)}</div>
          <h1 className="text-2xl font-semibold">{industry}</h1>
          <div className="flex gap-1.5 mt-1.5">
            <QuadrantBadge q={s.rrg_quadrant} />
            {s.tradeable ? <Badge tone="up">Tradeable</Badge> : <Badge>Not tradeable</Badge>}
          </div>
        </div>
        <div className="text-right">
          <div className="text-3xl font-semibold tabular">{num(s.sector_score, 1)}</div>
          <div className="text-xs text-muted">sector score</div>
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-8 gap-3">
        <Stat label="Stocks" value={s.stock_count} sub={`${s.bullish_count} bullish`} />
        <Stat label="ST breadth" value={pct(s.st_breadth, 0, false)} sub={<>Δ4W <Pct v={s.breadth_change_4w} d={0} suffix=" pts" /></>} />
        <Stat label="% > SMA50" value={pct(s.pct_above_sma50, 0, false)} />
        <Stat label="% > SMA200" value={pct(s.pct_above_sma200, 0, false)} />
        <Stat label="Near 52W high" value={pct(s.pct_near_high, 0, false)} sub="within 10%" />
        <Stat label="13W return" value={<Pct v={s.ret_13w} />} sub={<>26W <Pct v={s.ret_26w} /></>} />
        <Stat label="RS trend" value={num(s.rs_trend, 3)} tone={s.rs_trend > 1 ? "up" : "down"} sub="vs Nifty 500, 10W" />
        <Stat label="Risk-adj (26W)" value={num(s.risk_adj_return, 2)} sub={`vol ${pct(s.vol_26w, 0, false)}`} />
      </div>

      <Section title="Equal-weight sector index vs Nifty 500 (rebased to 100)">
        <SectorVsBench series={series} name={industry} />
      </Section>

      <Section title={`Stocks (${rows.length}) — bullish first, by stock score`}>
        <div className="-m-4">
          <DataTable rows={rows} columns={columns} week={sp.week} maxHeight="75vh" />
        </div>
      </Section>
    </div>
  );
}
