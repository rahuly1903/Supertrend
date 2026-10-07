import { cacheHeaders, notFound, params, runFor } from "@/lib/api";
import { getSectorDetail } from "@/lib/data";

export async function GET(request, ctx) {
  const { industry } = await ctx.params;
  const { obj } = params(request);
  const { run, pinned } = await runFor(obj);
  if (!run) return notFound("no complete run");
  const data = await getSectorDetail(run, decodeURIComponent(industry));
  if (!data) return notFound(`unknown sector ${industry}`);
  return Response.json({ run, ...data }, { headers: cacheHeaders(pinned) });
}
