// Thin client for the /api/portfolio summary endpoint (P5/028).
// Mirrors the Bearer-token attach pattern in lib/sse.ts. Returns null on any
// failure so the Hero header can fall back to static text instead of crashing.

export type PortfolioSummary = {
  // 040: nil when the user hasn't connected an IBKR account yet (or on fetch error).
  total_equity: number | null;
  day_pnl: number | null;
  day_pnl_pct: number | null;
  currency: string | null;
  is_mock: boolean;
  // 053: true when this is the guest read-only sample book (not the user's own).
  is_sample?: boolean;
};

export async function fetchPortfolio(token: string | null): Promise<PortfolioSummary | null> {
  try {
    const res = await fetch('/api/portfolio', {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!res.ok) return null;
    return (await res.json()) as PortfolioSummary;
  } catch {
    return null;
  }
}
