// PostHog wrapper — one source of truth for all instrumentation event names.
// Names align with METRICS.md so dashboards work out-of-the-box.
//
// When NEXT_PUBLIC_POSTHOG_API_KEY isn't set, all calls are no-ops.

'use client';

import posthog from 'posthog-js';

let initialized = false;

export function initAnalytics() {
  if (initialized) return;
  if (typeof window === 'undefined') return;
  const key = process.env.NEXT_PUBLIC_POSTHOG_API_KEY;
  const host = process.env.NEXT_PUBLIC_POSTHOG_HOST ?? 'https://us.i.posthog.com';
  if (!key || key.startsWith('phc_REPLACE')) return;
  posthog.init(key, {
    api_host: host,
    capture_pageview: true,
    persistence: 'localStorage',
  });
  initialized = true;
}

// ── account lifecycle ──
export const trackUserSignedUp = (source: string, method: string) =>
  capture('user_signed_up', { source, signup_method: method });

export const trackUserReturned = (daysSinceFirstSeen: number, sessionNumber: number) =>
  capture('user_returned', { days_since_first_seen: daysSinceFirstSeen, session_number: sessionNumber });

// ── waitlist / connect funnel (pivot — W6.2) ──
// The land → join → connect funnel for the briefing product. PostHog's auto
// `$pageview` on /connect is "land"; these are the steps after. PII-safe: the
// waitlist email is NEVER sent as a property — only the event + a source label.
export const trackWaitlistJoined = (source: string) =>
  capture('waitlist_joined', { source });

export const trackConnectStarted = () => capture('connect_started');

export const trackConnectCompleted = () => capture('connect_completed');

export const trackConnectFailed = (reason: string) =>
  capture('connect_failed', { reason });

export const trackBriefingOptInChanged = (optIn: boolean) =>
  capture('briefing_opt_in_changed', { opt_in: optIn });

// ── activation funnel ──
export const trackChatSessionStarted = () => capture('chat_session_started');

export const trackPromptSubmitted = (intent: string, textHash: string) =>
  capture('prompt_submitted', { intent_classification: intent, text_hash: textHash });

export const trackWidgetGenerated = (widgetType: string, latencyMs: number) =>
  capture('widget_generated', { widget_type: widgetType, latency_ms: latencyMs });

export const trackWidgetPinned = (widgetType: string, timeSinceSessionStartMs: number) =>
  capture('widget_pinned', { widget_type: widgetType, time_since_session_start_ms: timeSinceSessionStartMs });

export const trackWidgetUnpinned = (widgetType: string, timeSincePinMs: number) =>
  capture('widget_unpinned', { widget_type: widgetType, time_since_pin_ms: timeSincePinMs });

// ── trade flow ──
export const trackOrderTicketShown = (ticker: string, notional: number, hasTpSl: boolean) =>
  capture('order_ticket_shown', { ticker, notional, has_tp_sl: hasTpSl });

export const trackOrderConfirmed = (ticker: string, notional: number, fromResearchCard: boolean) =>
  capture('order_confirmed', { ticker, notional, from_research_card: fromResearchCard });

export const trackOrderCancelled = (ticker: string) => capture('order_cancelled', { ticker });

export const trackLiveTradePinned = (ticker: string) => capture('live_trade_pinned', { ticker });

export const trackTradeMonitorOpened = (ticker: string) => capture('trade_monitor_opened', { ticker });

// ── diagnostics ──
export const trackChatError = (errorType: string, toolName?: string) =>
  capture('chat_error', { error_type: errorType, tool_name: toolName });

export const trackWidgetValidationFailed = (widgetType: string, reason: string) =>
  capture('widget_validation_failed', { widget_type: widgetType, reason });

// ── primitive ──
function capture(name: string, props?: Record<string, unknown>) {
  if (typeof window === 'undefined') return;
  if (!initialized) return;
  posthog.capture(name, props ?? {});
}

// Naive intent classifier — runs client-side before sending the prompt so we can
// label events for the activation funnel. Backend has the same logic on its side
// for cross-check. Keep these patterns simple; this is for analytics buckets, not
// for routing.
export function classifyIntent(text: string): string {
  const t = text.toLowerCase();
  if (/\b(tldr|portfolio|morning|brief|catch.*up|overnight)\b/.test(t)) return 'morning_brief';
  if (/\b(analyze|research|deep dive|thesis|should i buy|red flags)\b/.test(t)) return 'stock_research';
  if (/\b(ta|chart|sma|support|resistance|golden cross|technical)\b/.test(t)) return 'technical_analysis';
  if (/\b(buy|sell|place|trade|short)\b/.test(t)) return 'place_trade';
  if (/\b(risk|audit|concentration|hedge|correlation)\b/.test(t)) return 'portfolio_risk';
  if (/\b(alert|notify|watch)\b/.test(t)) return 'set_alert';
  if (/\b(bear|steelman|wrong)\b/.test(t)) return 'bear_case';
  if (/\b(compare|vs|peer)\b/.test(t)) return 'compare_peers';
  return 'other';
}

// Tiny hash for analytics — non-cryptographic, just to dedupe without PII.
export function hashText(text: string): string {
  let h = 0;
  for (let i = 0; i < text.length; i++) {
    h = ((h << 5) - h) + text.charCodeAt(i);
    h |= 0;
  }
  return h.toString(36);
}
