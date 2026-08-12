const REMOTE_SNAPSHOT = "https://raw.githubusercontent.com/1581599248-bit/etf-flow-radar/main/public/data/latest.json";

export async function GET() {
  try {
    const response = await fetch(`${REMOTE_SNAPSHOT}?t=${Date.now()}`, {
      cache: "no-store",
      headers: { accept: "application/json" },
    });
    if (!response.ok) throw new Error(`GitHub snapshot returned ${response.status}`);
    const snapshot = await response.json() as { sourceMode?: string; status?: string; tradeDate?: string };
    if (snapshot.sourceMode !== "REAL" || snapshot.status === "failed" || !snapshot.tradeDate) {
      throw new Error("remote snapshot did not pass publication contract");
    }
    return Response.json(snapshot, {
      headers: { "cache-control": "no-store, max-age=0", "x-data-origin": "github-daily-pipeline" },
    });
  } catch (error) {
    return Response.json(
      { error: "Latest remote snapshot is unavailable", detail: String(error) },
      { status: 503, headers: { "cache-control": "no-store" } },
    );
  }
}
