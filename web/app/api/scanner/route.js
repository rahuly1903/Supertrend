import { cacheHeaders, notFound, params, runFor } from "@/lib/api";
import { getScannerRows } from "@/lib/data";
import { applyFilters, parseFilters, sortRows } from "@/lib/filters";

// GET /api/scanner?week=&preset=&dir=&sector=a,b&index=&qualified=1&grade=A+,A&wmin=&wmax=
//     &maxFromHigh=&minMcap=&minTurnover=&fresh=&above=50,200&q=&sort=stock_score&desc=1&page=1&pageSize=100
export async function GET(request) {
  const { sp, obj } = params(request);
  const { run, pinned } = await runFor(obj);
  if (!run) return notFound("no complete run");
  const rows = sortRows(applyFilters(await getScannerRows(run), parseFilters(sp)), obj.sort, obj.desc === "1");
  const pageSize = Math.min(Math.max(Number(obj.pageSize) || 100, 1), 500);
  const page = Math.max(Number(obj.page) || 1, 1);
  return Response.json({
    run, total: rows.length, page, pageSize, pages: Math.ceil(rows.length / pageSize),
    rows: rows.slice((page - 1) * pageSize, page * pageSize),
  }, { headers: cacheHeaders(pinned) });
}
