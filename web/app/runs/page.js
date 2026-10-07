import Link from "next/link";
import { Badge, DirectionBadge, Empty, GradeBadge, NoRuns, Section, StockLink } from "@/components/ui";
import { getRunDiff, getRuns, resolveRun } from "@/lib/data";
import { niceDate, num } from "@/lib/format";

export const metadata = { title: "Scan history" };

const STATUS_TONE = { complete: "up", running: "accent", failed: "down" };

function DiffList({ title, rows, week, tone, empty, why }) {
  return (
    <Section title={<>{title} <span className="text-muted font-normal">({rows.length})</span></>}>
      {rows.length === 0 ? <Empty>{empty}</Empty> : (
        <ul className="divide-y divide-line text-sm -my-2">
          {rows.map((r) => (
            <li key={r.symbol} className="py-1.5 flex items-center gap-2">
              <StockLink symbol={r.symbol} week={week} />
              <span className="text-xs text-muted truncate">{r.industry}</span>
              <span className="ml-auto flex items-center gap-2">
                {why ? <Badge tone={tone}>{r.why}</Badge> : null}
                {r.direction ? <DirectionBadge direction={r.direction} /> : null}
                <GradeBadge grade={r.grade} />
                <span className="tabular text-xs w-10 text-right">{num(r.stock_score, 1)}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

export default async function RunsPage({ searchParams }) {
  const sp = await searchParams;
  const [runs, run] = await Promise.all([getRuns(), resolveRun(sp)]);
  if (!runs.length) return <NoRuns />;
  const diff = run ? await getRunDiff(run) : null;

  return (
    <div className="flex flex-col gap-5">
      <h1 className="text-xl font-semibold">Scan history</h1>

      {diff ? (
        <>
          <div className="text-sm text-muted">
            Changes in week ending <b className="text-text">{niceDate(run.week)}</b>
            {diff.prev ? <> vs {niceDate(diff.prev.week)}</> : " (no earlier run)"}
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
            <DiffList title="New Strict 8 Filter passes" rows={diff.entries} week={sp.week} empty="No new entries." />
            <DiffList title="Exits" rows={diff.exits} week={sp.week} tone="down" why empty="No exits." />
            <DiffList title="New bullish flips" rows={diff.bullFlips} week={sp.week} empty="None this week." />
            <DiffList title="New bearish flips" rows={diff.bearFlips} week={sp.week} empty="None this week." />
          </div>
        </>
      ) : null}

      <Section title={`Runs (${runs.length})`}>
        <div className="overflow-auto max-h-[60vh] -m-4">
          <table className="w-full text-sm tabular">
            <thead className="text-xs text-muted text-left sticky top-0 bg-panel-2">
              <tr>
                {["Week", "Status", "Stocks", "Strict 8 Filter", "Duration", "Scan / write (ms)", "Finished", "Notes / error"].map((h) => (
                  <th key={h} className="px-3 py-2 font-medium whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => {
                const scanStep = r.timings && Object.entries(r.timings).find(([k]) => k.startsWith("scan "))?.[1];
                const selected = run && r.id === run.id;
                return (
                  <tr key={r.id} className={`border-t border-line ${selected ? "bg-accent-bg" : "hover:bg-panel-2"}`}>
                    <td className="px-3 py-1.5">
                      <Link href={`/runs?week=${r.week_end_date}`} className="text-accent hover:underline">{r.week_end_date}</Link>
                    </td>
                    <td className="px-3 py-1.5"><Badge tone={STATUS_TONE[r.status]}>{r.status}</Badge></td>
                    <td className="px-3 py-1.5">{r.stocks_scanned ?? "—"}</td>
                    <td className="px-3 py-1.5">{r.qualified_count ?? "—"}</td>
                    <td className="px-3 py-1.5">{r.duration_ms != null ? `${num(r.duration_ms / 1000, 2)} s` : "—"}</td>
                    <td className="px-3 py-1.5 text-xs text-muted">
                      {scanStep ?? "—"} / {r.timings?.["write snapshot"] ?? "—"}
                    </td>
                    <td className="px-3 py-1.5 text-xs text-muted whitespace-nowrap">
                      {r.finished_at ? new Date(r.finished_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" }) : "—"}
                    </td>
                    <td className="px-3 py-1.5 text-xs max-w-md truncate" title={r.error ?? r.notes ?? ""}>
                      {r.error ? <span className="text-down">{r.error.split("\n").filter(Boolean).at(-1)}</span> : r.notes ?? ""}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Section>
    </div>
  );
}
