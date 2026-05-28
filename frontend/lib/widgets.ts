// Widget type contracts — must match backend/prompts/widget_contract.md
// Keep these tight. If the backend ever returns a shape we don't recognise,
// we fall back to rendering it as plain markdown.

export type Source = { name: string; url?: string };

// ── morning_brief ──
export type MorningBriefData = {
  headline: string;
  paragraphs: string[];
};

// ── research_card ──
export type Rating = 'BUY' | 'HOLD' | 'SELL';
export type ResearchCardData = {
  ticker: string;
  company_name: string;
  current_price: number | null; // null when no live price source (yfinance down + no FMP profile price) — Proposal 008
  currency: string;
  rating: Rating;
  target_price: number;
  horizon_months: number;
  thesis_html: string;
  catalysts: string[];
  risks: string[];
};

// ── ta_chart ──
export type TAChartData = {
  ticker: string;
  timeframe: string;
  current_price: number;
  screenshot_url?: string;
  indicators_applied: string[];
  key_levels: { resistance: number[]; support: number[] };
  trend_summary_html: string;
};

// ── order_ticket ──
export type OrderTicketData = {
  side: 'buy' | 'sell';
  ticker: string;
  shares: number;
  notional: number;
  limit_price: number;
  currency: string;
  tp_price?: number;
  sl_price?: number;
  rr_ratio?: number;
  risk_amount?: number;
  reward_amount?: number;
  portfolio_pct?: number;
  within_risk_rule?: boolean;
  bracket_source?: 'from_prompt' | 'from_research' | 'from_default';
  notes_html?: string;
};

// ── live_trade ──
export type NewsItem = { headline: string; source: string; ts: string };
export type LiveTradeData = {
  order_id: string;
  ticker: string;
  side: 'long' | 'short';
  shares: number;
  fill_price: number;
  current_price: number;
  currency: string;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
  tp_armed_at?: number;
  sl_armed_at?: number;
  filled_at: string;
  news_since_fill?: NewsItem[];
};

// ── thesis ──
export type ThesisData = {
  ticker: string;
  rating: Rating;
  horizon: string;
  weight_pct_nav: number;
  confidence: string;
  tldr_html: string;
  reasons_to_be_in: string[];
  what_to_watch_weekly: string[];
  thesis_breakers: string[];
};

// ── tracker ──
export type TrackerData = {
  ticker: string;
  thesis_tldr_html: string;
  trade: {
    side: 'long' | 'short';
    shares: number;
    fill_price: number;
    current_price: number;
    unrealized_pnl: number;
    unrealized_pnl_pct: number;
    tp?: number;
    sl?: number;
  };
};

// ── portfolio_risk ──
export type SectorSeverity = 'normal' | 'warn' | 'danger';
export type FlagSeverity = 'low' | 'med' | 'high';
export type PortfolioRiskData = {
  risk_score: number;
  risk_label: string;
  risk_summary: string;
  sector_exposure: { label: string; pct: number; severity: SectorSeverity }[];
  flags: { severity: FlagSeverity; title: string; detail_html: string }[];
  suggestions: string[];
};

// ── union ──
export type Widget =
  | { type: 'morning_brief'; data: MorningBriefData; sources: Source[] }
  | { type: 'research_card'; data: ResearchCardData; sources: Source[] }
  | { type: 'ta_chart'; data: TAChartData; sources: Source[] }
  | { type: 'order_ticket'; data: OrderTicketData; sources: Source[] }
  | { type: 'live_trade'; data: LiveTradeData; sources: Source[] }
  | { type: 'thesis'; data: ThesisData; sources: Source[] }
  | { type: 'tracker'; data: TrackerData; sources: Source[] }
  | { type: 'portfolio_risk'; data: PortfolioRiskData; sources: Source[] };

export const KNOWN_WIDGET_TYPES = new Set<Widget['type']>([
  'morning_brief',
  'research_card',
  'ta_chart',
  'order_ticket',
  'live_trade',
  'thesis',
  'tracker',
  'portfolio_risk',
]);

export function isWidget(x: unknown): x is Widget {
  if (!x || typeof x !== 'object') return false;
  const obj = x as { type?: unknown; data?: unknown };
  return (
    typeof obj.type === 'string' &&
    KNOWN_WIDGET_TYPES.has(obj.type as Widget['type']) &&
    obj.data !== undefined
  );
}
