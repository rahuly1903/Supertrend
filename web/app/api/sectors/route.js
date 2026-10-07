import { cacheHeaders, notFound, params, runFor } from "@/lib/api";
import { getSectors } from "@/lib/data";

export async function GET(request) {
  const { obj } = params(request);
  const { run, pinned } = await runFor(obj);
  if (!run) return notFound("no complete run");
  const data = await getSectors(run);
  return Response.json({ run, ...data }, { headers: cacheHeaders(pinned) });
}
