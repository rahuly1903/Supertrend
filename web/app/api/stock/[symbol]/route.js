import { cacheHeaders, notFound, params, runFor } from "@/lib/api";
import { getStock } from "@/lib/data";

export async function GET(request, ctx) {
  const { symbol } = await ctx.params;
  const { obj } = params(request);
  const { run, pinned } = await runFor(obj);
  const data = await getStock(decodeURIComponent(symbol).toUpperCase(), run);
  if (!data) return notFound(`unknown symbol ${symbol}`);
  return Response.json({ run, ...data }, { headers: cacheHeaders(pinned) });
}
