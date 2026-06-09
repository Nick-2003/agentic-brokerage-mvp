-- agentic-brokerage-mvp · W6.3 — published briefs (web permalink). Pivot track.
--
-- Run ONCE in Supabase SQL Editor (after schema.sql + schema_waitlist.sql).
-- Idempotent; transactional.
--
-- A brief is published under a high-entropy **capability token**; the WhatsApp
-- message (W6.4 template, or the appended "view on web" link) points at
-- /b/<token>. The body IS sensitive (holdings/P&L), so:
--   • token is unguessable (secrets.token_urlsafe(32) — ~256 bits);
--   • rows EXPIRE (default 7d) so a leaked link goes stale;
--   • RLS on with NO anon/authenticated policy — ONLY the service_role (the
--     backend) reads, by exact token. No enumeration, no direct PostgREST access.
-- This is the "stored access-controlled" path DECISION_pivot_waitlist allows for
-- the brief body (the deliveries log still stores metadata-only).

begin;

create table if not exists public.published_briefs (
  token       text primary key,                 -- capability token (URL-safe, ~256-bit)
  user_id     uuid references auth.users(id) on delete cascade,
  account_id  text,
  as_of       date,
  body        text not null,                     -- the full brief (token-gated, expiring)
  created_at  timestamptz not null default now(),
  expires_at  timestamptz not null default (now() + interval '7 days')
);
create index if not exists published_briefs_user
  on public.published_briefs(user_id, created_at desc);
create index if not exists published_briefs_expiry
  on public.published_briefs(expires_at);

-- RLS on, NO policy → anon/authenticated get nothing via PostgREST; only the
-- service_role (the backend's get_published_brief) can read, by exact token.
alter table public.published_briefs enable row level security;

commit;
