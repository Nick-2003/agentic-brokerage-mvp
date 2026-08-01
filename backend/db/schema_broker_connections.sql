-- Proposal 086: essential provider-neutral connection state for SnapTrade.
-- Apply after backend/db/schema.sql. Idempotent DDL, wrapped in a transaction.

begin;

create table if not exists public.broker_connections (
  id                       uuid primary key default gen_random_uuid(),
  user_id                  uuid not null references auth.users(id) on delete cascade,
  provider                 text not null check (provider in ('snaptrade', 'ibkr_flex')),
  external_user_id         text,
  encrypted_user_secret    text,
  external_connection_id   text,
  status                   text not null default 'pending'
                             check (status in ('pending', 'active', 'disabled', 'error', 'revoked')),
  last_error_code          text,
  created_at               timestamptz not null default now(),
  updated_at               timestamptz not null default now(),
  unique (user_id, provider),
  unique (id, user_id),
  constraint snaptrade_identity_required check (
    provider <> 'snaptrade'
    or (external_user_id is not null and encrypted_user_secret is not null)
  ),
  constraint active_connection_id_required check (
    status <> 'active' or external_connection_id is not null
  )
);

create unique index if not exists broker_connections_external_id_uniq
  on public.broker_connections(provider, external_connection_id)
  where external_connection_id is not null;
create index if not exists broker_connections_user_status_idx
  on public.broker_connections(user_id, status);

drop trigger if exists broker_connections_set_updated_at on public.broker_connections;
create trigger broker_connections_set_updated_at
  before update on public.broker_connections
  for each row execute function public.set_updated_at();

create table if not exists public.broker_accounts (
  id                    uuid primary key default gen_random_uuid(),
  connection_id         uuid not null,
  user_id               uuid not null references auth.users(id) on delete cascade,
  external_account_id   text not null,
  masked_name           text not null,
  base_currency         text not null check (base_currency ~ '^[A-Z]{3}$'),
  is_selected           boolean not null default false,
  status                text not null default 'active'
                          check (status in ('active', 'disabled', 'revoked')),
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  unique (connection_id, external_account_id),
  constraint broker_accounts_owner_connection_fk
    foreign key (connection_id, user_id)
    references public.broker_connections(id, user_id)
    on delete cascade,
  constraint selected_account_must_be_active
    check (not is_selected or status = 'active')
);

create unique index if not exists broker_accounts_one_selected_per_user
  on public.broker_accounts(user_id)
  where is_selected;
create index if not exists broker_accounts_connection_idx
  on public.broker_accounts(connection_id);

drop trigger if exists broker_accounts_set_updated_at on public.broker_accounts;
create trigger broker_accounts_set_updated_at
  before update on public.broker_accounts
  for each row execute function public.set_updated_at();

alter table public.broker_connections enable row level security;
alter table public.broker_accounts enable row level security;

drop policy if exists "user reads own broker connections" on public.broker_connections;
create policy "user reads own broker connections"
  on public.broker_connections for select to authenticated
  using ((select auth.uid()) = user_id);

drop policy if exists "user inserts own broker connections" on public.broker_connections;
create policy "user inserts own broker connections"
  on public.broker_connections for insert to authenticated
  with check ((select auth.uid()) = user_id);

drop policy if exists "user updates own broker connections" on public.broker_connections;
create policy "user updates own broker connections"
  on public.broker_connections for update to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);

drop policy if exists "user deletes own broker connections" on public.broker_connections;
create policy "user deletes own broker connections"
  on public.broker_connections for delete to authenticated
  using ((select auth.uid()) = user_id);

drop policy if exists "user reads own broker accounts" on public.broker_accounts;
create policy "user reads own broker accounts"
  on public.broker_accounts for select to authenticated
  using ((select auth.uid()) = user_id);

drop policy if exists "user inserts own broker accounts" on public.broker_accounts;
create policy "user inserts own broker accounts"
  on public.broker_accounts for insert to authenticated
  with check ((select auth.uid()) = user_id);

drop policy if exists "user updates own broker accounts" on public.broker_accounts;
create policy "user updates own broker accounts"
  on public.broker_accounts for update to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);

drop policy if exists "user deletes own broker accounts" on public.broker_accounts;
create policy "user deletes own broker accounts"
  on public.broker_accounts for delete to authenticated
  using ((select auth.uid()) = user_id);

-- RLS controls rows; explicit grants control which Data API operations exist.
revoke all on table public.broker_connections from anon;
revoke all on table public.broker_accounts from anon;
grant select, insert, update, delete on table public.broker_connections to authenticated;
grant select, insert, update, delete on table public.broker_accounts to authenticated;

-- Atomically select exactly one active account. SECURITY INVOKER is intentional:
-- the caller's JWT and the table RLS policies remain the authorization boundary.
create or replace function public.select_my_broker_account(target_account_id uuid)
returns uuid
language plpgsql
security invoker
set search_path = ''
as $$
declare
  caller_id uuid := (select auth.uid());
  selected_id uuid;
begin
  if caller_id is null then
    raise insufficient_privilege using message = 'authentication required';
  end if;

  select account.id into selected_id
  from public.broker_accounts as account
  join public.broker_connections as connection
    on connection.id = account.connection_id
   and connection.user_id = account.user_id
  where account.id = target_account_id
    and account.user_id = caller_id
    and account.status = 'active'
    and connection.status = 'active';

  if selected_id is null then
    raise no_data_found using message = 'active brokerage account not found';
  end if;

  update public.broker_accounts
     set is_selected = false
   where user_id = caller_id
     and is_selected = true;

  update public.broker_accounts
     set is_selected = true
   where id = selected_id
     and user_id = caller_id;

  return selected_id;
end;
$$;

revoke execute on function public.select_my_broker_account(uuid) from public, anon;
grant execute on function public.select_my_broker_account(uuid) to authenticated;

commit;
