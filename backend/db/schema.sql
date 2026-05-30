-- agentic-brokerage-mvp · P4.2 — Supabase persistence + RLS (proposal 016).
--
-- Run this ONCE in your Supabase SQL Editor (Database → SQL Editor → New query).
-- Idempotent (safe to re-run); wraps in a transaction so a single failure rolls back.
--
-- Tables (all in `public`, all owned per-user via RLS `auth.uid() = user_id`):
--   conversations    one row per chat session
--   messages         one row per turn (user or assistant)
--   pinned_widgets   (frontend wiring deferred to a follow-up; RLS on)
--   user_profiles    (not used by code yet; RLS on)

begin;

-- ---------------------------------------------------------------------------
-- Helper: auto-update `updated_at` on row UPDATE.
-- ---------------------------------------------------------------------------

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- ---------------------------------------------------------------------------
-- conversations
-- ---------------------------------------------------------------------------

create table if not exists public.conversations (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  title       text,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
create index if not exists conversations_user_updated
  on public.conversations(user_id, updated_at desc);
drop trigger if exists conversations_set_updated_at on public.conversations;
create trigger conversations_set_updated_at
  before update on public.conversations
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- messages
-- ---------------------------------------------------------------------------

create table if not exists public.messages (
  id              uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references public.conversations(id) on delete cascade,
  user_id         uuid not null references auth.users(id) on delete cascade,
  role            text not null check (role in ('user', 'assistant')),
  content         text,
  widgets         jsonb,
  created_at      timestamptz not null default now()
);
create index if not exists messages_conversation
  on public.messages(conversation_id, created_at);
create index if not exists messages_user
  on public.messages(user_id, created_at desc);

-- ---------------------------------------------------------------------------
-- pinned_widgets
-- ---------------------------------------------------------------------------

create table if not exists public.pinned_widgets (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references auth.users(id) on delete cascade,
  widget     jsonb not null,
  created_at timestamptz not null default now()
);
create index if not exists pinned_widgets_user
  on public.pinned_widgets(user_id, created_at desc);

-- ---------------------------------------------------------------------------
-- user_profiles
-- ---------------------------------------------------------------------------

create table if not exists public.user_profiles (
  user_id      uuid primary key references auth.users(id) on delete cascade,
  display_name text,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);
drop trigger if exists user_profiles_set_updated_at on public.user_profiles;
create trigger user_profiles_set_updated_at
  before update on public.user_profiles
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- Row-Level Security: enable + "user owns row" policy on every table.
-- The `authenticated` role is what a user's JWT maps to; `service_role`
-- bypasses RLS (so the Supabase service key, which we DON'T use in db.py,
-- could read anything — keep it for admin tasks only).
-- ---------------------------------------------------------------------------

alter table public.conversations  enable row level security;
alter table public.messages       enable row level security;
alter table public.pinned_widgets enable row level security;
alter table public.user_profiles  enable row level security;

drop policy if exists "user owns conversation" on public.conversations;
create policy "user owns conversation" on public.conversations
  for all to authenticated
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

drop policy if exists "user owns message" on public.messages;
create policy "user owns message" on public.messages
  for all to authenticated
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

drop policy if exists "user owns pinned widget" on public.pinned_widgets;
create policy "user owns pinned widget" on public.pinned_widgets
  for all to authenticated
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

drop policy if exists "user owns profile" on public.user_profiles;
create policy "user owns profile" on public.user_profiles
  for all to authenticated
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

commit;
