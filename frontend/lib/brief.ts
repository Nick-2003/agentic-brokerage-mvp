// Client for the public brief permalink (W6.3). Relative `/api/brief/<token>` is
// proxied to the backend via next.config.js. Returns null on miss/expired/error
// so the page can show a friendly "not found" instead of crashing.

// 051 — per-holding day P&L for the bar chart on the brief page.
export type BriefBar = { symbol: string; day_pnl: number; day_pnl_display: string };
export type BriefChartData = { kind: string; base_currency: string; bars: BriefBar[] };

export type PublishedBrief = {
  text: string;
  account_id: string | null;
  as_of: string | null;
  generated_at: string | null;
  chart_data?: BriefChartData | null; // 051 — null/absent on older briefs
};

export async function fetchBrief(token: string): Promise<PublishedBrief | null> {
  try {
    const res = await fetch(`/api/brief/${encodeURIComponent(token)}`);
    if (!res.ok) return null;
    return (await res.json()) as PublishedBrief;
  } catch {
    return null;
  }
}
