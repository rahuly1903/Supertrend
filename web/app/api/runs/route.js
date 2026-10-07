import { getRuns } from "@/lib/data";

export async function GET() {
  return Response.json({ runs: await getRuns() }, { headers: { "Cache-Control": "public, max-age=60" } });
}
