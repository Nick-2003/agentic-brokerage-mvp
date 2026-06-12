-- agentic-brokerage-mvp · W4 — waitlist + IBKR connect storage (pivot track).
--
-- Run ONCE in your Supabase SQL Editor (Database → SQL Editor → New query), after
-- backend/db/schema.sql. Idempotent; wrapped in a transaction.
--
-- Tables (public):
--   waitlist_signups     pre-auth interest capture (email). Anon-insertable.
--   ibkr_connections     one per user: ENCRYPTED Flex token + query id + WhatsApp
--                        number + opt-in + status. RLS: a user owns their own row.
--   briefing_deliveries  per-send log (metadata only — NEVER the brief body).
--
-- Access model (enforced by RLS + how the backend builds clients):
--   • The connect endpoint writes via the USER's JWT  → RLS `auth.uid() = user_id`.
--   • The W5 cron reads all opted-in rows + writes the delivery log via the
--     SERVICE KEY (service_role bypasses RLS) — the one legitimate admin path,
--     a backend job, never a user request. The Flex token is decrypted app-side.

begin;

-- reuse the set_updated_at() trigger fn from schema.sql; (re)define defensively
-- so this file is runnable even if schema.sql hasn't been applied in this session.
create or replace function public.set_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- ---------------------------------------------------------------------------
-- waitlist_signups — pre-auth interest (email). No user_id (may precede signup).
-- ---------------------------------------------------------------------------

create table if not exists public.waitlist_signups (
  id          uuid primary key default gen_random_uuid(),
  email       text not null,
  source      text,
  created_at  timestamptz not null default now()
);
create unique index if not exists waitlist_signups_email_uniq
  on public.waitlist_signups(lower(email));

-- ---------------------------------------------------------------------------
-- ibkr_connections — the per-user broker link. One row per user (PK = user_id).
-- `flex_token_encrypted` holds Fernet ciphertext ONLY (app-side, token_crypto.py).
-- The plaintext token NEVER touches the DB.
-- ---------------------------------------------------------------------------

create table if not exists public.ibkr_connections (
  user_id              uuid primary key references auth.users(id) on delete cascade,
  flex_token_encrypted text not null,
  flex_query_id        text not null,
  whatsapp_number      text not null,          -- E.164, e.g. +85291234567
  opt_in               boolean not null default true,   -- WhatsApp briefing consent
  email_opt_in         boolean not null default false,  -- 037: email channel (separate consent)
  status               text not null default 'active'
                         check (status in ('active', 'paused', 'revoked')),
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now()
);
-- 037 — additive column for an ALREADY-CREATED table (the `create table if not
-- exists` above is a no-op on an existing DB, so the new column needs an explicit
-- idempotent ALTER). Safe to run repeatedly.
alter table public.ibkr_connections
  add column if not exists email_opt_in boolean not null default false;
create index if not exists ibkr_connections_active
  on public.ibkr_connections(opt_in, status);
drop trigger if exists ibkr_connections_set_updated_at on public.ibkr_connections;
create trigger ibkr_connections_set_updated_at
  before update on public.ibkr_connections
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- briefing_deliveries — one row per send attempt. METADATA ONLY (no brief body).
-- ---------------------------------------------------------------------------

create table if not exists public.briefing_deliveries (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  account_id  text,                            -- IBKR account (e.g. U19883362)
  as_of       date,                            -- the statement date the brief was about
  channel     text not null default 'whatsapp',  -- whatsapp | email (037)
  status      text not null,                   -- queued | sent | failed | skipped
  provider_id text,                            -- Twilio message sid
  error       text,                            -- error code/message on failure
  created_at  timestamptz not null default now()
);
create index if not exists briefing_deliveries_user
  on public.briefing_deliveries(user_id, created_at desc);

-- ---------------------------------------------------------------------------
-- Row-Level Security.
-- ibkr_connections + briefing_deliveries: user owns their rows (auth.uid()).
-- waitlist_signups: anon may INSERT (join the list) but NOT read (no email harvest).
-- service_role (the W5 cron's key) bypasses RLS for the cross-user system read.
-- ---------------------------------------------------------------------------

alter table public.waitlist_signups    enable row level security;
alter table public.ibkr_connections    enable row level security;
alter table public.briefing_deliveries enable row level security;

drop policy if exists "anon can join waitlist" on public.waitlist_signups;
create policy "anon can join waitlist" on public.waitlist_signups
  for insert to anon, authenticated
  with check (true);

drop policy if exists "user owns ibkr connection" on public.ibkr_connections;
create policy "user owns ibkr connection" on public.ibkr_connections
  for all to authenticated
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

drop policy if exists "user reads own deliveries" on public.briefing_deliveries;
create policy "user reads own deliveries" on public.briefing_deliveries
  for select to authenticated
  using (auth.uid() = user_id);

commit;
