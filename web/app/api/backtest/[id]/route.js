import { notFound } from "@/lib/api";
import { getBacktest } from "@/lib/data";

export async function GET(_request, ctx) {
  const { id } = await ctx.params;
  const run = /^\d+$/.test(id) ? await getBacktest(Number(id)) : null;
  if (!run) return notFound(`unknown backtest ${id}`);
  // a finished backtest never changes
  return Response.json(run, { headers: { "Cache-Control": "public, max-age=3600, s-maxage=86400" } });
}
