import { notFound, params, runFor } from "@/lib/api";
import { toCsv } from "@/lib/csv";
import { getScannerRows } from "@/lib/data";
import { applyFilters, parseFilters, sortRows } from "@/lib/filters";

// Same filters as /api/scanner; ext=1 appends the extended columns.
export async function GET(request) {
  const { sp, obj } = params(request);
  const { run } = await runFor(obj);
  if (!run) return notFound("no complete run");
  const rows = sortRows(applyFilters(await getScannerRows(run), parseFilters(sp)), obj.sort, obj.desc === "1");
  return new Response(toCsv(rows, obj.ext === "1"), {
    headers: {
      "Content-Type": "text/csv; charset=utf-8",
      "Content-Disposition": `attachment; filename="supertrend_scan_${run.week}${obj.ext === "1" ? "_extended" : ""}.csv"`,
      "Cache-Control": "no-store",
    },
  });
}
