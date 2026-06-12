// Connect page (W4 frontend — pivot track).
//
// The waitlist product's onboarding surface, separate from the paused chat app
// at `/`. Three things, top to bottom:
//   1. Hero / value prop.
//   2. Waitlist email capture — no auth (precedes sign-up) → POST /api/waitlist.
//   3. Connect IBKR — requires a signed-in user (magic link); stores the Flex
//      token (encrypted server-side) + WhatsApp number + opt-in, or shows the
//      current connection with a pause/resume toggle.
//
// Auth is handled inline (NOT wrapped in <AuthGate>) so the waitlist form stays
// visible before sign-in. Mirrors AuthGate's mounted-flag SSR guard + the
// magic-link signInWithOtp flow, and lib/portfolio.ts's fetch/token pattern.

'use client';

import { useCallback, useEffect, useState } from 'react';
import type { Session } from '@supabase/supabase-js';
import {
  trackBriefingOptInChanged,
  trackConnectCompleted,
  trackConnectFailed,
  trackConnectStarted,
  trackWaitlistJoined,
} from '@/lib/analytics';
import { authConfigured, getAccessToken, getSupabase, signOut } from '@/lib/supabase';
import {
  connectIbkr,
  getConnection,
  joinWaitlist,
  setOptIn,
  type Connection,
} from '@/lib/waitlist';

const E164 = /^\+\d{6,15}$/;

export default function ConnectPage() {
  const [mounted, setMounted] = useState(false);
  const [session, setSession] = useState<Session | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [conn, setConn] = useState<Connection | null>(null);
  const [editing, setEditing] = useState(false);
  // Has the connection lookup finished? Gates the connect UI so `ConnectForm`
  // (and its `connect_started` event) never renders during the load race — i.e.
  // it won't fire for an ALREADY-connected user before getConnection() resolves.
  const [connLoaded, setConnLoaded] = useState(false);

  const loadConnection = useCallback(async () => {
    const t = await getAccessToken();
    setToken(t);
    try {
      setConn(t ? await getConnection(t) : null);
    } finally {
      setConnLoaded(true);
    }
  }, []);

  useEffect(() => {
    setMounted(true);
    if (!authConfigured()) return;
    const sb = getSupabase();
    if (!sb) return;
    let active = true;
    sb.auth.getSession().then(({ data }) => {
      if (!active) return;
      setSession(data.session);
      if (data.session) void loadConnection();
    });
    const { data: sub } = sb.auth.onAuthStateChange((_e, s) => {
      setSession(s);
      if (s) void loadConnection();
      else {
        setToken(null);
        setConn(null);
        setConnLoaded(false);
      }
    });
    return () => {
      active = false;
      sub.subscription.unsubscribe();
    };
  }, [loadConnection]);

  const configured = mounted && authConfigured();

  return (
    <main className="min-h-screen bg-gradient-to-br from-[#2c2c2a] to-[#444441] flex justify-center p-6">
      <div className="w-full max-w-md space-y-5">
        {/* Hero */}
        <header className="text-[#f5f5f0] pt-6 pb-1">
          <div className="text-[12px] font-semibold tracking-[0.08em] opacity-70">
            DAILY PORTFOLIO BRIEFING
          </div>
          <h1 className="mt-2 text-2xl font-semibold leading-snug">
            Your portfolio, explained — every morning on WhatsApp.
          </h1>
          <p className="mt-2 text-[13px] opacity-80 leading-relaxed">
            Connect Interactive Brokers once. Each morning we read your holdings, write a short
            narrative of what moved and why, and send it to your phone.
          </p>
        </header>

        <WaitlistCard />

        {!configured ? (
          <Card>
            <p className="text-[13px] text-text-3">
              Sign-in isn’t configured in this environment. Set{' '}
              <code className="text-text-2">NEXT_PUBLIC_SUPABASE_URL</code> /{' '}
              <code className="text-text-2">…_ANON_KEY</code> to connect a broker.
            </p>
          </Card>
        ) : !session ? (
          <SignInCard />
        ) : !connLoaded ? (
          <Card>
            <p className="text-[13px] text-text-3">Loading your connection…</p>
          </Card>
        ) : conn && !editing ? (
          <ConnectionCard
            conn={conn}
            email={session.user?.email ?? ''}
            token={token}
            onChanged={setConn}
            onEdit={() => setEditing(true)}
          />
        ) : (
          <ConnectForm
            token={token}
            existing={conn}
            email={session.user?.email ?? ''}
            onConnected={(c) => {
              setConn(c);
              setEditing(false);
            }}
            onCancel={conn ? () => setEditing(false) : undefined}
          />
        )}

        <p className="text-center text-[11px] text-[#f5f5f0] opacity-50">
          Your Flex token is read-only and stored encrypted. We never place trades.
        </p>
        <p className="text-center text-[11px] pb-6">
          <a href="/" className="text-[#f5f5f0] opacity-60 underline underline-offset-2 hover:opacity-90">
            ← Back to the app
          </a>
        </p>
      </div>
    </main>
  );
}

// --- shells ---------------------------------------------------------------

function Card({ children }: { children: React.ReactNode }) {
  return <div className="bg-bg rounded-3xl shadow-2xl p-6 text-text">{children}</div>;
}

function Field({
  label,
  hint,
  ...props
}: { label: string; hint?: string } & React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <label className="block">
      <span className="text-[12px] font-medium text-text-2">{label}</span>
      <input
        {...props}
        className="mt-1 w-full h-12 bg-surface border border-border rounded-2xl px-4 text-sm text-text outline-none focus:border-accent transition-colors"
      />
      {hint && <span className="mt-1 block text-[11px] text-text-3">{hint}</span>}
    </label>
  );
}

// --- waitlist -------------------------------------------------------------

function WaitlistCard() {
  const [email, setEmail] = useState('');
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);

  async function submit() {
    const addr = email.trim();
    if (!addr) return;
    setBusy(true);
    const ok = await joinWaitlist(addr);
    setBusy(false);
    if (ok) {
      setDone(true);
      trackWaitlistJoined('connect-page'); // PII-safe: no email sent
    }
  }

  return (
    <Card>
      <h2 className="text-base font-semibold">Join the waitlist</h2>
      {done ? (
        <p className="mt-2 text-[13px] text-green-DEFAULT">You’re on the list — we’ll be in touch.</p>
      ) : (
        <div className="mt-3 flex gap-2">
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && submit()}
            placeholder="you@example.com"
            autoComplete="email"
            className="flex-1 h-12 bg-surface border border-border rounded-2xl px-4 text-sm outline-none focus:border-accent transition-colors"
          />
          <button
            type="button"
            onClick={submit}
            disabled={busy || !email.trim()}
            className="h-12 px-4 rounded-2xl bg-accent text-white text-sm font-semibold active:scale-[0.98] transition-transform disabled:opacity-50"
          >
            {busy ? '…' : 'Join'}
          </button>
        </div>
      )}
    </Card>
  );
}

// --- sign in (inline magic link) ------------------------------------------

function SignInCard() {
  const [email, setEmail] = useState('');
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function send() {
    const sb = getSupabase();
    const addr = email.trim();
    if (!sb || !addr) return;
    setBusy(true);
    setError(null);
    const { error } = await sb.auth.signInWithOtp({
      email: addr,
      options: { emailRedirectTo: window.location.href },
    });
    setBusy(false);
    if (error) setError(error.message);
    else setSent(true);
  }

  return (
    <Card>
      <h2 className="text-base font-semibold">Connect your broker</h2>
      <p className="mt-1 text-[13px] text-text-3">Sign in to connect IBKR — we’ll send a magic link.</p>
      {sent ? (
        <p className="mt-3 text-[13px] text-green-DEFAULT">
          Magic link sent to <strong>{email.trim()}</strong>. Open it on this device.
        </p>
      ) : (
        <div className="mt-3 space-y-2.5">
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && send()}
            placeholder="you@example.com"
            autoComplete="email"
            className="w-full h-12 bg-surface border border-border rounded-2xl px-4 text-sm outline-none focus:border-accent transition-colors"
          />
          <button
            type="button"
            onClick={send}
            disabled={busy || !email.trim()}
            className="w-full h-12 rounded-2xl bg-accent text-white text-sm font-semibold active:scale-[0.98] transition-transform disabled:opacity-50"
          >
            {busy ? 'Sending…' : 'Send magic link'}
          </button>
          {error && <div className="text-[12px] text-red-DEFAULT">{error}</div>}
        </div>
      )}
    </Card>
  );
}

// --- connection status (connected) ----------------------------------------

function ConnectionCard({
  conn,
  email,
  token,
  onChanged,
  onEdit,
}: {
  conn: Connection;
  email: string;
  token: string | null;
  onChanged: (c: Connection) => void;
  onEdit: () => void;
}) {
  const [busy, setBusy] = useState(false);

  async function toggle() {
    if (!token) return;
    setBusy(true);
    const updated = await setOptIn(token, !conn.opt_in);
    setBusy(false);
    if (updated) {
      onChanged(updated);
      trackBriefingOptInChanged(updated.opt_in);
    }
  }

  return (
    <Card>
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold">IBKR connected</h2>
        <span
          className={`text-[11px] font-medium px-2 py-0.5 rounded-full ${
            conn.opt_in ? 'bg-green-bg text-green-DEFAULT' : 'bg-amber-bg text-amber-DEFAULT'
          }`}
        >
          {conn.opt_in ? 'Briefings on' : 'Paused'}
        </span>
      </div>
      <dl className="mt-3 space-y-1.5 text-[13px]">
        <Row k="Flex query" v={conn.flex_query_id} />
        <Row k="WhatsApp" v={conn.whatsapp_number} />
        <Row k="Email briefings" v={conn.email_opt_in ? `On — ${email || 'your account email'}` : 'Off'} />
        <Row k="Status" v={conn.status} />
      </dl>
      <div className="mt-4 flex gap-2">
        <button
          type="button"
          onClick={toggle}
          disabled={busy || !token}
          className="flex-1 h-11 rounded-2xl border border-border text-sm font-semibold active:scale-[0.98] transition-transform disabled:opacity-50"
        >
          {busy ? '…' : conn.opt_in ? 'Pause briefings' : 'Resume briefings'}
        </button>
        <button
          type="button"
          onClick={onEdit}
          className="flex-1 h-11 rounded-2xl bg-accent text-white text-sm font-semibold active:scale-[0.98] transition-transform"
        >
          Update
        </button>
      </div>
      <SignedInAs email={email} />
    </Card>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between">
      <dt className="text-text-3">{k}</dt>
      <dd className="font-medium text-text">{v}</dd>
    </div>
  );
}

// --- connect form ---------------------------------------------------------

function ConnectForm({
  token,
  existing,
  email,
  onConnected,
  onCancel,
}: {
  token: string | null;
  existing: Connection | null;
  email: string;
  onConnected: (c: Connection) => void;
  onCancel?: () => void;
}) {
  const [flexToken, setFlexToken] = useState('');
  const [queryId, setQueryId] = useState(existing?.flex_query_id ?? '');
  const [whatsapp, setWhatsapp] = useState(existing?.whatsapp_number ?? '');
  const [optIn, setOptIn] = useState(existing?.opt_in ?? true);
  const [emailOptIn, setEmailOptIn] = useState(existing?.email_opt_in ?? false); // 038
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Funnel "connect" step — only a first-time connect (not an Update of an
  // existing connection), fired once when the form is reached.
  useEffect(() => {
    if (!existing) trackConnectStarted();
  }, [existing]);

  const waOk = E164.test(whatsapp.trim());
  const canSubmit = !!token && flexToken.trim().length >= 8 && queryId.trim() && waOk && optIn;

  async function submit() {
    if (!token) return;
    setBusy(true);
    setError(null);
    const res = await connectIbkr(token, {
      flex_token: flexToken.trim(),
      flex_query_id: queryId.trim(),
      whatsapp_number: whatsapp.trim(),
      opt_in: optIn,
      email_opt_in: emailOptIn,
    });
    setBusy(false);
    if (res.ok && res.connection) {
      onConnected(res.connection);
      trackConnectCompleted();
    } else {
      setError(res.error || 'Could not connect');
      trackConnectFailed(res.error || 'unknown');
    }
  }

  return (
    <Card>
      <h2 className="text-base font-semibold">{existing ? 'Update connection' : 'Connect IBKR'}</h2>
      <div className="mt-3 space-y-3">
        <Field
          label="Flex Web Service token"
          type="password"
          value={flexToken}
          onChange={(e) => setFlexToken(e.target.value)}
          placeholder={existing ? 'Re-enter to update' : 'From IBKR → Reporting → Flex Web Service'}
          hint="Read-only. Stored encrypted — never used to trade."
        />
        <Field
          label="Flex query ID"
          value={queryId}
          onChange={(e) => setQueryId(e.target.value)}
          placeholder="e.g. 1234567"
          inputMode="numeric"
        />
        <Field
          label="WhatsApp number"
          value={whatsapp}
          onChange={(e) => setWhatsapp(e.target.value)}
          placeholder="+85291234567"
          hint={whatsapp && !waOk ? 'Use E.164 format, e.g. +85291234567' : undefined}
        />
        <label className="flex items-start gap-2 text-[12px] text-text-2">
          <input
            type="checkbox"
            checked={optIn}
            onChange={(e) => setOptIn(e.target.checked)}
            className="mt-0.5"
          />
          <span>
            I agree to receive a daily portfolio briefing on WhatsApp. I can pause it anytime.
          </span>
        </label>
        {/* 038 — email channel: a separate, optional consent. Off by default. */}
        <label className="flex items-start gap-2 text-[12px] text-text-2">
          <input
            type="checkbox"
            checked={emailOptIn}
            onChange={(e) => setEmailOptIn(e.target.checked)}
            className="mt-0.5"
          />
          <span>
            Also email me the same briefing{email ? ` at ${email}` : ''}. Every email has a
            one-click unsubscribe; turning it off doesn’t affect WhatsApp.
          </span>
        </label>
        <button
          type="button"
          onClick={submit}
          disabled={busy || !canSubmit}
          className="w-full h-12 rounded-2xl bg-accent text-white text-sm font-semibold active:scale-[0.98] transition-transform disabled:opacity-50"
        >
          {busy ? 'Connecting…' : existing ? 'Save changes' : 'Connect & start briefings'}
        </button>
        {error && <div className="text-[12px] text-red-DEFAULT">{error}</div>}
        {onCancel && (
          <button
            type="button"
            onClick={onCancel}
            className="w-full text-center text-[12px] text-text-3 underline underline-offset-2 hover:text-text-2"
          >
            Cancel
          </button>
        )}
      </div>
      <SignedInAs email={email} />
    </Card>
  );
}

function SignedInAs({ email }: { email: string }) {
  return (
    <div className="mt-4 pt-3 border-t border-border text-[11px] text-text-3 flex justify-between">
      <span>{email ? `Signed in as ${email}` : 'Signed in'}</span>
      <button
        type="button"
        onClick={() => void signOut()}
        className="underline underline-offset-2 hover:text-text-2"
      >
        Sign out
      </button>
    </div>
  );
}
