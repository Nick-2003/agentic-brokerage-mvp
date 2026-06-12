// Thin client for the W4 connect/waitlist endpoints (pivot track).
// Mirrors lib/portfolio.ts: relative `/api/*` fetch (proxied to the backend by
// next.config.js rewrites) + the Bearer-token attach pattern. Returns null /
// {ok:false} on failure so the UI can show a message instead of crashing.

export type Connection = {
  user_id: string;
  flex_query_id: string;
  whatsapp_number: string;
  opt_in: boolean;
  email_opt_in: boolean; // 038 — email channel consent (separate from WhatsApp opt_in)
  status: string;
  created_at?: string;
  updated_at?: string;
};

export type ConnectPayload = {
  flex_token: string;
  flex_query_id: string;
  whatsapp_number: string;
  opt_in: boolean;
  email_opt_in: boolean; // 038
};

// Public — no auth (precedes sign-up).
export async function joinWaitlist(email: string, source = 'connect-page'): Promise<boolean> {
  try {
    const res = await fetch('/api/waitlist', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, source }),
    });
    if (!res.ok) return false;
    const j = (await res.json()) as { ok?: boolean };
    return !!j.ok;
  } catch {
    return false;
  }
}

// Authed — the caller's own connection (token-free), or null if not connected.
export async function getConnection(token: string): Promise<Connection | null> {
  try {
    const res = await fetch('/api/ibkr/connection', {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) return null;
    const j = (await res.json()) as { connection?: Connection | null };
    return j.connection ?? null;
  } catch {
    return null;
  }
}

// Authed — store/replace the caller's connection. The Flex token is encrypted
// server-side; this returns the token-free public view.
export async function connectIbkr(
  token: string,
  payload: ConnectPayload
): Promise<{ ok: boolean; error?: string; connection?: Connection }> {
  try {
    const res = await fetch('/api/ibkr/connect', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify(payload),
    });
    const j = (await res.json().catch(() => ({}))) as {
      ok?: boolean;
      detail?: string;
      connection?: Connection;
    };
    if (!res.ok) return { ok: false, error: j.detail || `Error ${res.status}` };
    return { ok: true, connection: j.connection };
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
}

// Authed — flip opt-in (in-app unsubscribe / re-subscribe).
export async function setOptIn(token: string, opt_in: boolean): Promise<Connection | null> {
  try {
    const res = await fetch('/api/ibkr/opt-in', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify({ opt_in }),
    });
    if (!res.ok) return null;
    const j = (await res.json()) as { connection?: Connection | null };
    return j.connection ?? null;
  } catch {
    return null;
  }
}
