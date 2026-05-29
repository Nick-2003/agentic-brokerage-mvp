// AuthGate — Supabase magic-link login gate (P4.1).
//
// Wrap the app in <AuthGate>. Behaviour (after mount):
//   • Supabase env unset (demo mode) → render children directly (no gate).
//   • Configured + no session       → magic-link login screen.
//   • Configured + session          → render children.
//
// SSR / hydration (why the `mounted` flag exists — proposal 013):
//   getSupabase() is client-only (it short-circuits when `typeof window ===
//   "undefined"`). On the server authConfigured() is therefore always false, so
//   a naive gate renders `children`; on the client's FIRST render it's true and
//   renders <Splash> — and React throws a hydration mismatch (server text ≠
//   client text). To avoid it, the gate decision is deferred until after mount:
//   server AND first client render BOTH show the same stable <Splash>, then the
//   effect runs and the real gate (children / login) appears. There's a brief
//   (~tens of ms, localStorage read) splash on load — intentional and stable.
//
// Exposes the current session via useAuth() so the rest of the app (e.g. the
// header) can show who's signed in and offer sign-out. Always wraps children in
// the provider so useAuth() is safe to call in both demo and authed modes.

'use client';

import { createContext, useContext, useEffect, useState } from 'react';
import type { Session } from '@supabase/supabase-js';
import { authConfigured, getSupabase } from '@/lib/supabase';

type AuthState = { session: Session | null; configured: boolean };

const AuthContext = createContext<AuthState>({ session: null, configured: false });
export const useAuth = () => useContext(AuthContext);

export function AuthGate({ children }: { children: React.ReactNode }) {
  const [mounted, setMounted] = useState(false);
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setMounted(true);
    if (!authConfigured()) {
      setReady(true); // demo mode — no gate, no session lookup
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

  // configured is only meaningful client-side, after mount (getSupabase() is
  // client-only). Kept false during SSR / the pre-mount render so it never
  // drives a server↔client divergence.
  const configured = mounted && authConfigured();

  let body: React.ReactNode;
  if (!mounted || !ready) {
    // Identical on the server and the first client render → no hydration mismatch.
    body = <Splash>Loading…</Splash>;
  } else if (!configured || session) {
    body = children; // demo mode, or signed in
  } else {
    body = <LoginScreen />;
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

function LoginScreen() {
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
      </div>
    </main>
  );
}
