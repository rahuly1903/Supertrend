import { getBacktests } from "@/lib/data";

export async function GET() {
  return Response.json(await getBacktests(), { headers: { "Cache-Control": "public, max-age=60" } });
}
