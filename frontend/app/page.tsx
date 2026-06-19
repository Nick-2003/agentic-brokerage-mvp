'use client';

import { AuthGate, useAuth } from '@/components/AuthGate';
import { ChatBar } from '@/components/ChatBar';
import { Markdown } from '@/components/Markdown';
import { ThinkingCard, type Thought } from '@/components/ThinkingCard';
import { WidgetRenderer } from '@/components/widgets/WidgetRenderer';
import {
  classifyIntent,
  hashText,
  trackChatError,
  trackChatSessionStarted,
  trackOrderTicketShown,
  trackPromptSubmitted,
  trackWidgetGenerated,
  trackWidgetPinned,
} from '@/lib/analytics';
import { fetchPortfolio, type PortfolioSummary } from '@/lib/portfolio';
import { streamChat, type ChatEvent, type ChatRequest } from '@/lib/sse';
import { getAccessToken, signOut } from '@/lib/supabase';
import type { Widget } from '@/lib/widgets';
import { useEffect, useRef, useState } from 'react';

// One agent run: user turn + chain of thoughts + zero or more widgets + final text
type Turn = {
  id: string;
  userText: string;
  thoughts: Thought[];
  done: boolean;
  elapsedMs?: number;
  widgets: Widget[];
  messages: string[];
  error?: string;
};

export default function Home() {
  return (
    <AuthGate>
      <ChatScreen />
    </AuthGate>
  );
}

function ChatScreen() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [pinnedWidgets, setPinnedWidgets] = useState<Widget[]>([]);
  // P5/028: live portfolio summary for the Hero header. null = loading or
  // fetch failed → Hero falls back to static text.
  const [portfolio, setPortfolio] = useState<PortfolioSummary | null>(null);
  // P4.2: the active conversation. null = the next turn starts a new one;
  // the backend echoes the id back via a `conversation` SSE event.
  const [conversationId, setConversationId] = useState<string | null>(null);
  const ctrlRef = useRef<AbortController | null>(null);
  const screenRef = useRef<HTMLDivElement>(null);
  // Anchor for widget_pinned's time-since-session-start. Reset whenever a new
  // session begins (mount + signed-in-user change).
  const sessionStartRef = useRef(performance.now());

  // Clear any active conversation when the signed-in user changes (incl.
  // sign-out → null). Prevents one user's id from being echoed under another.
  const userKey = useAuth().session?.user?.id ?? null;
  useEffect(() => {
    setConversationId(null);
    setTurns([]);
    // A signed-in-user change (and the initial mount) starts a fresh analytics
    // session — re-anchor the clock and fire chat_session_started.
    sessionStartRef.current = performance.now();
    trackChatSessionStarted();
  }, [userKey]);

  // P5/028: fetch the portfolio summary on mount and whenever the signed-in
  // user changes (the token determines whose account we read).
  useEffect(() => {
    let cancelled = false;
    getAccessToken()
      .then(fetchPortfolio)
      .then((p) => {
        if (!cancelled) setPortfolio(p);
      });
    return () => {
      cancelled = true;
    };
  }, [userKey]);

  async function handleSubmit(text: string) {
    if (streaming) return;
    // Activation-funnel event — hash only, never the raw prompt text (PII rule).
    trackPromptSubmitted(classifyIntent(text), hashText(text));
    setStreaming(true);
    const turnId = crypto.randomUUID();
    const startedAt = performance.now();

    // Insert a fresh turn we'll keep mutating
    setTurns((prev) => [
      ...prev,
      { id: turnId, userText: text, thoughts: [], done: false, widgets: [], messages: [] },
    ]);

    // Attach the Supabase JWT (null in demo mode → backend uses the "demo" user)
    const token = await getAccessToken();

    const body: ChatRequest = conversationId
      ? { message: text, conversation_id: conversationId }
      : { message: text };

    ctrlRef.current = streamChat(body, (ev: ChatEvent) => {
      // P4.2: capture the conversation id as soon as the backend announces it.
      if (ev.event === 'conversation') {
        setConversationId(ev.data.id);
        return;
      }

      // ── analytics — side effects kept OUT of the setTurns updater (which
      // must stay pure). Fire on the raw event here, mutate state below. ──
      if (ev.event === 'widget') {
        trackWidgetGenerated(ev.data.type, performance.now() - startedAt);
        if (ev.data.type === 'order_ticket') {
          const d = ev.data.data;
          trackOrderTicketShown(d.ticker, d.notional, !!(d.tp_price || d.sl_price));
        }
      } else if (ev.event === 'error') {
        trackChatError(ev.data.message);
      }

      setTurns((prev) =>
        prev.map((t) => {
          if (t.id !== turnId) return t;
          const next = { ...t };
          if (ev.event === 'thought') {
            // Mark prior thought as done, add new as active
            const newThoughts: Thought[] = next.thoughts.map((th) => ({
              ...th,
              state: 'done' as const,
            }));
            newThoughts.push({ text: ev.data.text, state: 'active' });
            next.thoughts = newThoughts;
          } else if (ev.event === 'tool_result') {
            // Mark the latest active thought as done
            next.thoughts = next.thoughts.map((th, i, arr) =>
              i === arr.length - 1 && th.state === 'active'
                ? { ...th, state: 'done' as const }
                : th,
            );
          } else if (ev.event === 'widget') {
            next.widgets = [...next.widgets, ev.data];
          } else if (ev.event === 'message') {
            next.messages = [...next.messages, ev.data.text];
          } else if (ev.event === 'error') {
            next.error = ev.data.message;
          } else if (ev.event === 'done') {
            next.done = true;
            next.elapsedMs = performance.now() - startedAt;
            // Mark any remaining active thought as done
            next.thoughts = next.thoughts.map((th) => ({ ...th, state: 'done' as const }));
          }
          return next;
        }),
      );

      if (ev.event === 'done') {
        setStreaming(false);
        // Scroll to bottom after done
        setTimeout(() => screenRef.current?.scrollTo({ top: screenRef.current.scrollHeight, behavior: 'smooth' }), 50);
      }
    }, '', token);
  }

  function handleMicTap() {
    // Voice mode is mocked for now — surface a hint
    alert('Voice input is mocked in the MVP. Type your message instead.');
  }

  function pinWidget(w: Widget) {
    setPinnedWidgets((prev) => [w, ...prev]);
    trackWidgetPinned(w.type, performance.now() - sessionStartRef.current);
  }

  return (
    <main className="min-h-screen flex items-center justify-center bg-gradient-to-br from-[#2c2c2a] to-[#444441] p-6">
      <div className="w-[390px] h-[844px] bg-bg rounded-[48px] shadow-2xl ring-[14px] ring-[#1a1a1a] relative overflow-hidden flex flex-col">
        {/* Notch */}
        <div className="absolute top-3 left-1/2 -translate-x-1/2 w-28 h-7 bg-[#1a1a1a] rounded-3xl z-50" />

        {/* Status bar */}
        <div className="px-7 pt-4 pb-2 flex justify-between text-sm font-medium text-text shrink-0">
          <span>9:41</span>
          <span>●●●● 5G</span>
        </div>

        {/* Scrollable screen */}
        <div ref={screenRef} className="flex-1 overflow-y-auto overflow-x-hidden no-scrollbar pb-[120px]">
          <Header />
          <Hero pinnedCount={pinnedWidgets.length} portfolio={portfolio} />

          {/* Pinned widgets (dashboard) */}
          {pinnedWidgets.length > 0 && (
            <div className="px-4 mt-1 space-y-2.5">
              <SectionTitle>Pinned on home</SectionTitle>
              {pinnedWidgets.map((w, i) => (
                <WidgetRenderer key={i} widget={w} />
              ))}
            </div>
          )}

          {/* Conversation turns */}
          {turns.length === 0 && (
            <div className="px-6 mt-4 text-[13px] text-text-3 leading-relaxed">
              Tap one to begin — or type your own below:
              <div className="mt-3 space-y-1.5">
                {[
                  'give me a tldr on my portfolio',
                  'research on Tencent',
                  'how risky is my book?',
                  'buy 10 shares of TSLA at limit 246, TP 290, SL 225',
                ].map((ex) => (
                  <button
                    key={ex}
                    onClick={() => handleSubmit(ex)}
                    disabled={streaming}
                    className="block w-full text-left px-3 py-2 bg-surface rounded-lg active:scale-[0.98] transition-transform disabled:opacity-50"
                  >
                    {ex}
                  </button>
                ))}
              </div>
            </div>
          )}

          {turns.map((t) => (
            <div key={t.id} className="px-4 mt-3">
              {/* User bubble */}
              <div className="flex justify-end">
                <div className="bg-accent text-white text-sm px-3.5 py-2 rounded-2xl rounded-br-md max-w-[85%]">
                  {t.userText}
                </div>
              </div>

              {/* Loading ring — only in the gap before the first SSE event
                  populates this turn. Reuses ThinkingCard's card chrome + the
                  accent ring so the swap to the real ThinkingCard is seamless.
                  `!t.done` guards against a hang if a turn finishes empty. */}
              {t.thoughts.length === 0 &&
                t.widgets.length === 0 &&
                t.messages.length === 0 &&
                !t.error &&
                !t.done && (
                  <div className="bg-surface border border-border rounded-2xl p-4 mt-3 animate-slide-in flex items-center gap-2.5">
                    <div className="w-4 h-4 rounded-full border-2 border-accent border-t-transparent animate-spin-slow" />
                    <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-accent">
                      Working
                    </span>
                  </div>
                )}

              {/* Thinking */}
              {t.thoughts.length > 0 && (
                <ThinkingCard thoughts={t.thoughts} done={t.done} elapsedMs={t.elapsedMs} />
              )}

              {/* Widgets — pin button beneath each */}
              {t.widgets.map((w, i) => (
                <div key={i} className="mt-3">
                  <WidgetRenderer widget={w} />
                  <button
                    onClick={() => pinWidget(w)}
                    className="mt-2 w-full py-2 rounded-full text-[12.5px] font-semibold bg-accent-bg text-accent border border-accent/20 active:scale-95 transition-transform"
                  >
                    Pin to home
                  </button>
                </div>
              ))}

              {/* Plain text messages — rendered as markdown (042). */}
              {t.messages.map((m, i) => (
                <div key={i} className="mt-3 bg-surface border border-border px-3.5 py-2.5 rounded-2xl rounded-bl-md max-w-[85%]">
                  <Markdown>{m}</Markdown>
                </div>
              ))}

              {/* Error */}
              {t.error && (
                <div className="mt-3 bg-red-bg text-red-DEFAULT text-sm px-3.5 py-2.5 rounded-2xl">
                  {t.error}
                </div>
              )}
            </div>
          ))}
        </div>

        {/* Chat bar */}
        <ChatBar onSubmit={handleSubmit} onMicTap={handleMicTap} disabled={streaming} />
      </div>

      {/* Side panel — info / demo script */}
      <SidePanel pinnedCount={pinnedWidgets.length} streaming={streaming} onPick={handleSubmit} />
    </main>
  );
}

function Header() {
  const { session } = useAuth();
  const email = session?.user?.email ?? null;
  const initial = email ? email[0]!.toUpperCase() : 'G';
  return (
    <div className="px-6 pt-1 flex items-center justify-between">
      <div className="text-[12px] font-semibold tracking-[0.08em] text-text-2">INVESTING</div>
      <button
        type="button"
        onClick={() => {
          if (session && window.confirm('Sign out?')) signOut();
        }}
        title={email ?? 'demo (not signed in)'}
        aria-label={session ? `Signed in as ${email} — tap to sign out` : 'Demo mode'}
        className="w-8 h-8 rounded-full bg-accent text-white text-[13px] font-semibold flex items-center justify-center active:scale-95 transition-transform"
      >
        {initial}
      </button>
    </div>
  );
}

function Hero({ pinnedCount, portfolio }: { pinnedCount: number; portfolio: PortfolioSummary | null }) {
  // 035: the signed-in account email, shown above "Portfolio value". Null in demo
  // mode (no session) → the line is omitted.
  const email = useAuth().session?.user?.email ?? null;
  // 040: the value is the user's OWN read-only IBKR account. Until they connect one,
  // it is NIL — show "—", not a fabricated demo number. `hasData` = a connected
  // account with a value; `notConnected` = the endpoint responded with a null equity
  // (the per-user "no IBKR connection" signal) → show a connect hint. A null portfolio
  // (still loading / fetch error) just shows "—" with no hint.
  const hasData = portfolio != null && portfolio.total_equity != null;
  const notConnected = portfolio != null && portfolio.total_equity == null;
  // 053: the guest read-only sample book — show a "Sample" badge so it's never
  // mistaken for the user's own money, and a sign-in hint instead of the IBKR one.
  const isSample = portfolio?.is_sample === true;
  const currency = portfolio?.currency ?? '';

  const fmt = (n: number) =>
    n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  const up = (portfolio?.day_pnl ?? 0) >= 0;
  const sign = up ? '+' : '-';
  const equityText = hasData ? `${currency}${fmt(portfolio!.total_equity!)}` : '—';
  const pnlText =
    hasData && portfolio!.day_pnl != null && portfolio!.day_pnl_pct != null
      ? `${sign}${currency}${fmt(Math.abs(portfolio!.day_pnl!))} (${sign}${Math.abs(
          portfolio!.day_pnl_pct!
        ).toFixed(2)}%) today`
      : null;

  return (
    <div className="px-6 pt-5 pb-1">
      {email && (
        <div className="text-[11px] text-text-3 mb-1 truncate" title={email}>{email}</div>
      )}
      <div className="text-xs text-text-3 uppercase tracking-[0.06em] mb-1.5 flex items-center gap-2">
        Portfolio value
        {isSample && (
          <span className="px-1.5 py-0.5 rounded-full bg-accent-bg text-accent text-[9px] font-semibold tracking-normal normal-case">
            Sample
          </span>
        )}
      </div>
      <div className="text-4xl font-semibold -tracking-tight">{equityText}</div>
      {pnlText && (
        <div className={`mt-1 text-sm font-medium ${up ? 'text-green-DEFAULT' : 'text-red-DEFAULT'}`}>
          {pnlText}
        </div>
      )}
      {isSample && (
        <div className="mt-1 text-[12px] text-text-3">
          Sample portfolio.{' '}
          <a href="/connect" className="underline underline-offset-2 hover:text-text-2">Sign in &amp; connect IBKR</a>{' '}
          to see your own.
        </div>
      )}
      {notConnected && (
        <div className="mt-1 text-[12px] text-text-3">
          Connect Interactive Brokers to see your portfolio —{' '}
          <a href="/connect" className="underline underline-offset-2 hover:text-text-2">connect</a>.
        </div>
      )}
      {pinnedCount === 0 && (
        <div className="mt-2 text-[11px] text-text-3">Your home is empty — pin a widget to start building it.</div>
      )}
    </div>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-text-3">{children}</div>
  );
}

function SidePanel({
  pinnedCount,
  streaming,
  onPick,
}: {
  pinnedCount: number;
  streaming: boolean;
  onPick: (text: string) => void;
}) {
  const prompts = [
    'give me a tldr on my portfolio',
    'research on Tencent',
    'add the 50 and 200-day SMA on NVDA',
    'build my thesis on TSLA',
    'buy 10 shares of TSLA at limit 246, TP 290, SL 225',
    'how risky is my book?',
  ];
  return (
    <div className="ml-8 w-[320px] text-[#f5f5f0] text-[13px] leading-relaxed self-start mt-12">
      <h2 className="text-lg font-medium text-white mb-2">Agentic Brokerage — Live</h2>
      <p className="opacity-85 mb-4">
        Tap a prompt below, or type into the chat bar at the bottom of the phone. Every response streams from real Claude. Widgets appear inline — tap{' '}
        <strong className="text-white">Pin to home</strong> to keep them.
      </p>

      <div className="rounded-lg bg-white/5 px-3.5 py-2.5 mb-4 text-[12px]">
        <div className="text-white/55 uppercase tracking-[0.12em] text-[10px] mb-1">Status</div>
        <div className="text-white">{streaming ? 'Streaming…' : 'Idle'}</div>
        <div className="text-white/60 mt-1">{pinnedCount} pinned widget{pinnedCount === 1 ? '' : 's'}</div>
      </div>

      <h3 className="text-white text-[14px] font-medium uppercase tracking-[0.08em] mb-2">Try these</h3>
      <div className="space-y-1.5 text-[12.5px] font-mono">
        {prompts.map((p) => (
          <Prompt key={p} onClick={() => onPick(p)} disabled={streaming}>
            {p}
          </Prompt>
        ))}
      </div>

      <div className="mt-6 text-[11px] text-white/45">
        Backend status: <a href="/api/healthz" target="_blank" className="underline">/api/healthz</a>
      </div>
      <div className="mt-6 text-[11px] text-white/45">
        Portfolio briefings: <a href="/connect" target="_blank" className="underline">/connect</a>
      </div>
    </div>
  );
}

function Prompt({
  children,
  onClick,
  disabled,
}: {
  children: React.ReactNode;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="block w-full text-left px-3 py-2 rounded bg-white/[0.06] border border-white/[0.08] hover:bg-white/[0.1] active:scale-[0.98] transition disabled:opacity-50"
    >
      {children}
    </button>
  );
}
