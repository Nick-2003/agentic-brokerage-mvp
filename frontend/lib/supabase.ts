// Supabase browser client — magic-link auth (P4.1).
// One source of truth for the client singleton + session helpers.
//
// When the Supabase env vars aren't set (or still hold REPLACE placeholders),
// getSupabase() returns null and the app runs in DEMO MODE — mirroring the
// backend's REQUIRE_AUTH=0 fallback. Same graceful-no-op pattern as analytics.ts.

'use client';

import { createClient, type SupabaseClient } from '@supabase/supabase-js';

let _client: SupabaseClient | null = null;

function isPlaceholder(v: string | undefined): boolean {
  return !v || v.includes('REPLACE');
}

export function getSupabase(): SupabaseClient | null {
  if (typeof window === 'undefined') return null;
  if (_client) return _client;
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anon = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (isPlaceholder(url) || isPlaceholder(anon)) return null;
  _client = createClient(url!, anon!, {
    auth: {
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: true, // parses the magic-link token from the redirect URL
    },
  });
  return _client;
}

export function authConfigured(): boolean {
  return getSupabase() !== null;
}

// Current access token (the JWT the backend verifies), or null in demo mode /
// when signed out. Read fresh at submit time so a refreshed token is used.
export async function getAccessToken(): Promise<string | null> {
  const sb = getSupabase();
  if (!sb) return null;
  const { data } = await sb.auth.getSession();
  return data.session?.access_token ?? null;
}

export async function signOut(): Promise<void> {
  const sb = getSupabase();
  if (sb) await sb.auth.signOut();
}
