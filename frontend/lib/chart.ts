// Thin client for the /api/chart-data endpoint (044). Mirrors lib/portfolio.ts:
// relative `/api/*` fetch (proxied to the backend by next.config rewrites).
// Returns null on any failure so the chart can fall back to the inline SVG.

export type Candle = {
  time: string; // 'YYYY-MM-DD'
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

export type ChartData = {
  ticker: string;
  timeframe: string;
  currency: string;
  candles: Candle[];
  count: number;
};

export async function fetchChartData(
  ticker: string,
  timeframe = '1D'
): Promise<ChartData | null> {
  try {
    const qs = new URLSearchParams({ ticker, timeframe });
    const res = await fetch(`/api/chart-data?${qs.toString()}`);
    if (!res.ok) return null;
    return (await res.json()) as ChartData;
  } catch {
    return null;
  }
}
