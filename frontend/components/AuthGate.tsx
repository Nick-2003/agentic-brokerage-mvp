// AuthGate — Supabase magic-link login gate (P4.1).
//
// Wrap the app in <AuthGate>. Behaviour (after mount):
//   • Supabase env unset (demo mode) → render children directly (no gate).
//   • Configured + session           → render children.
//   • Configured + "continue in demo" override → render children + a small
//     "Demo mode · exit" pill (dev only — see below).
//   • Configured + no session         → magic-link login screen.
//
// "Continue in demo mode" (proposal 014): a DEV-ONLY escape hatch on the login
// screen so a developer isn't locked out when magic-link email is rate-limited
// (Supabase's built-in email cap is ~a few/hour). It's gated to
// `process.env.NODE_ENV === 'development'`, so it never appears in a production
// build. It only affects the FRONTEND gate — the backend enforces auth
// independently (REQUIRE_AUTH), so this can't bypass a locked-down backend: with
// REQUIRE_AUTH=1 the demo user simply has no token and every /api/chat call 401s.
// The override persists in localStorage so it survives restarts; the pill's
// "exit" clears it and returns to the login screen.
//
// SSR / hydration (the `mounted` flag — proposal 013): getSupabase() and
// localStorage are client-only, so the gate decision is deferred until after
// mount. Server AND first client render both show the same <Splash>; the real
// gate appears once the effect runs. Without this the server (no window →
// "not configured" → children) and the first client paint diverge → mismatch.
//
// Exposes the current session via useAuth() so the rest of the app (e.g. the
// header) can show who's signed in and offer sign-out.

'use client';

import { createContext, useContext, useEffect, useState } from 'react';
import type { Session } from '@supabase/supabase-js';
import { authConfigured, getSupabase } from '@/lib/supabase';

const DEMO_KEY = 'ab-demo-mode';
const DEMO_ALLOWED = process.env.NODE_ENV === 'development';

type AuthState = { session: Session | null; configured: boolean };

const AuthContext = createContext<AuthState>({ session: null, configured: false });
export const useAuth = () => useContext(AuthContext);

export function AuthGate({ children }: { children: React.ReactNode }) {
  const [mounted, setMounted] = useState(false);
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);
  const [demo, setDemo] = useState(false);

  useEffect(() => {
    setMounted(true);
    // Restore a persisted "continue in demo mode" override (dev only).
    if (DEMO_ALLOWED) {
      try {
        if (window.localStorage.getItem(DEMO_KEY) === '1') setDemo(true);
      } catch {
        /* localStorage unavailable — ignore */
      }
    }
    if (!authConfigured()) {
      setReady(true); // demo mode by absence of env — no gate, no session lookup
      return;
    }
    const sb = getSupabase();
    if (!sb) {
      setReady(true);
      return;
    }
    let active = true;
    sb.auth.getSession().then(({ data }) => {
      if (!active) return;
      setSession(data.session);
      setReady(true);
    });
    const { data: sub } = sb.auth.onAuthStateChange((_event, s) => setSession(s));
    return () => {
      active = false;
      sub.subscription.unsubscribe();
    };
  }, []);

  function enterDemo() {
    try {
      window.localStorage.setItem(DEMO_KEY, '1');
    } catch {
      /* ignore */
    }
    setDemo(true);
  }

  function exitDemo() {
    try {
      window.localStorage.removeItem(DEMO_KEY);
    } catch {
      /* ignore */
    }
    setDemo(false);
  }

  // configured / demo are only meaningful client-side, after mount.
  const configured = mounted && authConfigured();
  const demoActive = mounted && DEMO_ALLOWED && demo;

  let body: React.ReactNode;
  if (!mounted || !ready) {
    // Identical on the server and the first client render → no hydration mismatch.
    body = <Splash>Loading…</Splash>;
  } else if (!configured || session) {
    body = children; // demo-by-no-env, or signed in
  } else if (demoActive) {
    // Dev "continue in demo mode" — app + an exit affordance back to login.
    body = (
      <>
        {children}
        <DemoBadge onExit={exitDemo} />
      </>
    );
  } else {
    body = <LoginScreen onDemo={DEMO_ALLOWED ? enterDemo : undefined} />;
  }

  return <AuthContext.Provider value={{ session, configured }}>{body}</AuthContext.Provider>;
}

function Splash({ children }: { children: React.ReactNode }) {
  return (
    <main className="min-h-screen flex items-center justify-center bg-gradient-to-br from-[#2c2c2a] to-[#444441] text-[#f5f5f0] text-sm">
      {children}
    </main>
  );
}

// Small fixed pill shown while in dev "continue in demo mode" — indicates the
// override is on and lets the developer return to the login screen.
function DemoBadge({ onExit }: { onExit: () => void }) {
  return (
    <div className="fixed bottom-4 left-4 z-[60] flex items-center gap-2 rounded-full bg-[#1a1a1a]/90 px-3 py-1.5 text-[11px] text-white shadow-lg">
      <span className="inline-block w-1.5 h-1.5 rounded-full bg-[#f5a623]" />
      Demo mode
      <button
        type="button"
        onClick={onExit}
        className="ml-1 underline underline-offset-2 hover:opacity-80"
      >
        exit
      </button>
    </div>
  );
}

function LoginScreen({ onDemo }: { onDemo?: () => void }) {
  const [email, setEmail] = useState('');
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function sendLink() {
    const sb = getSupabase();
    const addr = email.trim();
    if (!sb || !addr) return;
    setBusy(true);
    setError(null);
    const { error } = await sb.auth.signInWithOtp({
      email: addr,
      options: { emailRedirectTo: window.location.origin },
    });
    setBusy(false);
    if (error) setError(error.message);
    else setSent(true);
  }

  return (
    <main className="min-h-screen flex items-center justify-center bg-gradient-to-br from-[#2c2c2a] to-[#444441] p-6">
      <div className="w-full max-w-sm bg-bg rounded-3xl shadow-2xl p-7 text-text">
        <div className="text-[12px] font-semibold tracking-[0.08em] text-text-2">INVESTING</div>
        <h1 className="mt-2 text-2xl font-semibold">Sign in</h1>
        <p className="mt-1 text-[13px] text-text-3 leading-relaxed">
          A team of Wall Street–grade analysts, at your command. Enter your email — we’ll send a
          one-time magic link.
        </p>

        {sent ? (
          <div className="mt-5 text-[13px] text-green-DEFAULT leading-relaxed">
            Check your inbox — a magic link is on its way to <strong>{email.trim()}</strong>. Open it
            on this device to sign in.
          </div>
        ) : (
          <div className="mt-5 space-y-2.5">
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && sendLink()}
              placeholder="you@example.com"
              autoComplete="email"
              className="w-full h-12 bg-surface border border-border rounded-2xl px-4 text-sm text-text outline-none focus:border-accent transition-colors"
            />
            <button
              type="button"
              onClick={sendLink}
              disabled={busy || !email.trim()}
              className="w-full h-12 rounded-2xl bg-accent text-white text-sm font-semibold active:scale-[0.98] transition-transform disabled:opacity-50"
            >
              {busy ? 'Sending…' : 'Send magic link'}
            </button>
            {error && <div className="text-[12px] text-red-DEFAULT">{error}</div>}
          </div>
        )}

        {onDemo && (
          <div className="mt-5 pt-4 border-t border-border">
            <button
              type="button"
              onClick={onDemo}
              className="w-full text-center text-[12px] text-text-3 underline underline-offset-2 hover:text-text-2"
            >
              Continue in demo mode (dev only)
            </button>
          </div>
        )}
      </div>
    </main>
  );
}
