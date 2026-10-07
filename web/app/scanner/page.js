import ScannerView from "@/components/ScannerView";
import { NoRuns } from "@/components/ui";
import { getFilterOptions, getScannerRows, resolveRun } from "@/lib/data";
import { niceDate } from "@/lib/format";

export const metadata = { title: "Scanner" };

export default async function ScannerPage({ searchParams }) {
  const sp = await searchParams;
  const run = await resolveRun(sp);
  if (!run) return <NoRuns />;
  const [rows, options] = await Promise.all([getScannerRows(run), getFilterOptions(run)]);
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-baseline justify-between flex-wrap gap-2">
        <h1 className="text-xl font-semibold">Stock scanner</h1>
        <span className="text-xs text-muted">Week ending {niceDate(run.week)} · weekly Supertrend ({run.supertrend ?? "7, 3"})</span>
      </div>
      <ScannerView rows={rows} options={options} initial={sp} week={sp.week} />
    </div>
  );
}
