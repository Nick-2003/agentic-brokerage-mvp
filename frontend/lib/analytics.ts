// PostHog wrapper — one source of truth for all instrumentation event names.
// Names align with METRICS.md so dashboards work out-of-the-box.
//
// When NEXT_PUBLIC_POSTHOG_API_KEY isn't set, all calls are no-ops.

'use client';

import posthog from 'posthog-js';

let initialized = false;
let queuedUserId: string | null = null;
let identifiedUserId: string | null = null;

// SECURITY (W6.2b): the Supabase magic-link redirect lands with the session in the
// URL — `…/connect#access_token=…&refresh_token=…` (implicit flow) or `?code=…`
// (PKCE). PostHog's auto-pageview records the full URL, so without this those
// CREDENTIALS (incl. the long-lived refresh_token) would be sent to PostHog —
// observed live. `scrubUrl` drops the fragment and redacts auth query params; it's
// applied to every event's URL props via `sanitize_properties` below.
const _SENSITIVE_QS = new RegExp(
  '([?&](?:access_token|refresh_token|code|token|id_token|provider_token|expires_at|expires_in|connection_id|authorization_id|brokerage_authorization_id)=)[^&#]*',
  'gi'
);

export function scrubUrl(u: unknown): unknown {
  if (typeof u !== 'string' || !u) return u;
  return u.split('#')[0].replace(_SENSITIVE_QS, '$1[redacted]');
}

export function initAnalytics() {
  if (initialized) return;
  if (typeof window === 'undefined') return;
  const key = process.env.NEXT_PUBLIC_POSTHOG_API_KEY;
  const host = process.env.NEXT_PUBLIC_POSTHOG_HOST ?? 'https://us.i.posthog.com';
  if (!key || key.startsWith('phc_REPLACE')) return;
  posthog.init(key, {
    api_host: host,
    // Gate 5: DOM autocapture serializes rendered text and attributes. Live
    // evidence included a signed-in email and person-bearing broker account name.
    // This application uses explicit typed events instead.
    autocapture: false,
    // Keep replay disabled in code even if project-side remote config changes.
    disable_session_recording: true,
    // Pageviews remain useful and pass through the URL sanitizer below.
    capture_pageview: true,
    persistence: 'localStorage',
    // Strip auth tokens out of any URL property before it leaves the browser.
    sanitize_properties: (props) => {
      for (const k of ['$current_url', '$referrer', '$pathname', '$initial_current_url']) {
        if (k in props) props[k] = scrubUrl(props[k]);
      }
      return props;
    },
  });
  initialized = true;

  // Supabase session resolution and PostHog initialization are independent client
  // effects. Apply an identity that arrived first instead of losing that lifecycle.
  if (queuedUserId) {
    posthog.identify(queuedUserId);
    identifiedUserId = queuedUserId;
  }
}

// Use only the trusted Supabase Auth UUID. Email, brokerage identifiers, account
// numbers, and provider secrets must never be passed to this helper.
export function identifyAnalyticsUser(userId: string): void {
  if (typeof window === 'undefined') return;
  const normalized = userId.trim();
  if (!normalized) return;
  queuedUserId = normalized;
  if (!initialized || identifiedUserId === normalized) return;

  // A direct A→B transition without an observed sign-out must not merge people.
  if (identifiedUserId) posthog.reset();
  posthog.identify(normalized);
  identifiedUserId = normalized;
}

export function resetAnalyticsUser(): void {
  if (typeof window === 'undefined') return;
  queuedUserId = null;
  identifiedUserId = null;
  if (initialized) posthog.reset();
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

// 092 — provider-labelled brokerage funnel. No account/connection IDs, portal
// URLs, userSecret, or brokerage names are captured.
export const trackBrokerConnectionStarted = (provider: string) =>
  capture('broker_connection_started', { provider });

export const trackBrokerConnectionCompleted = (provider: string, accountCount: number) =>
  capture('broker_connection_completed', { provider, account_count: accountCount });

export const trackBrokerConnectionFailed = (provider: string, reasonCode: string) =>
  capture('broker_connection_failed', { provider, reason_code: reasonCode });

export const trackBrokerAccountSelected = (provider: string) =>
  capture('broker_account_selected', { provider });

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

type LlmTurnProperties = {
  turn_id: string;
  primary_provider: string;
  primary_model: string;
  final_provider: string;
  final_model: string;
  fallback_used: boolean;
  fallback_reason?: string | null;
  intent: string;
  output_kind: 'message' | 'widget' | 'error';
  widget_type?: string | null;
  latency_ms: number;
  input_tokens?: number;
  output_tokens?: number;
  iterations?: number;
  status: 'success' | 'error';
  error_code?: string | null;
};

export const trackLlmTurnCompleted = (props: LlmTurnProperties) =>
  capture('llm_turn_completed', props);
