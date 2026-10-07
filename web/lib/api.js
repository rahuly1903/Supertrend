import { resolveRun } from "./data";

// Snapshots are immutable once a run completes. A request pinned to a week/run can be
// cached for long; "latest" changes every Saturday, so it gets a short TTL.
export function cacheHeaders(pinned) {
  return {
    "Cache-Control": pinned
      ? "public, max-age=300, s-maxage=86400, stale-while-revalidate=604800"
      : "public, max-age=60, s-maxage=300, stale-while-revalidate=3600",
  };
}

export function params(request) {
  const sp = new URL(request.url).searchParams;
  return { sp, obj: Object.fromEntries(sp.entries()) };
}

export async function runFor(obj) {
  const run = await resolveRun(obj);
  return { run, pinned: Boolean(obj.week || obj.run) };
}

export function notFound(message) {
  return Response.json({ error: message }, { status: 404 });
}
