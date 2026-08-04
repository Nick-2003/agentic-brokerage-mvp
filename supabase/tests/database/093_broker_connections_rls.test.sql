begin;

create extension if not exists pgtap with schema extensions;
select plan(22);

-- Fixed, transaction-scoped fixture users. Everything rolls back after finish().
insert into auth.users (id, email) values
  ('00000000-0000-4000-8000-000000000901', 'fixture-a@example.invalid'),
  ('00000000-0000-4000-8000-000000000902', 'fixture-b@example.invalid');

insert into public.broker_connections (
  id, user_id, provider, external_user_id, encrypted_user_secret,
  external_connection_id, status
) values
  (
    '00000000-0000-4000-8000-000000000911',
    '00000000-0000-4000-8000-000000000901',
    'snaptrade', 'fixture-user-a', 'fernet-v1:fixture-a', 'fixture-authorization-a', 'active'
  ),
  (
    '00000000-0000-4000-8000-000000000912',
    '00000000-0000-4000-8000-000000000902',
    'snaptrade', 'fixture-user-b', 'fernet-v1:fixture-b', 'fixture-authorization-b', 'active'
  );

insert into public.broker_accounts (
  id, connection_id, user_id, external_account_id, masked_name,
  base_currency, is_selected, status
) values
  (
    '00000000-0000-4000-8000-000000000921',
    '00000000-0000-4000-8000-000000000911',
    '00000000-0000-4000-8000-000000000901',
    'fixture-account-a1', 'Fixture A1', 'USD', true, 'active'
  ),
  (
    '00000000-0000-4000-8000-000000000922',
    '00000000-0000-4000-8000-000000000911',
    '00000000-0000-4000-8000-000000000901',
    'fixture-account-a2', 'Fixture A2', 'USD', false, 'active'
  ),
  (
    '00000000-0000-4000-8000-000000000923',
    '00000000-0000-4000-8000-000000000912',
    '00000000-0000-4000-8000-000000000902',
    'fixture-account-b1', 'Fixture B1', 'HKD', false, 'active'
  );

select has_table('public', 'broker_connections', 'broker_connections exists');
select has_table('public', 'broker_accounts', 'broker_accounts exists');
select results_eq(
  $$select relrowsecurity from pg_class where oid = 'public.broker_connections'::regclass$$,
  array[true],
  'broker_connections has RLS enabled'
);
select results_eq(
  $$select relrowsecurity from pg_class where oid = 'public.broker_accounts'::regclass$$,
  array[true],
  'broker_accounts has RLS enabled'
);
select is(
  has_table_privilege('anon', 'public.broker_connections', 'SELECT'),
  false,
  'anon has no connection-table SELECT grant'
);
select is(
  has_function_privilege('anon', 'public.select_my_broker_account(uuid)', 'EXECUTE'),
  false,
  'anon cannot execute account selection'
);

set local role authenticated;
set local request.jwt.claim.sub = '00000000-0000-4000-8000-000000000901';

select results_eq(
  $$select count(*) from public.broker_connections$$,
  array[1::bigint],
  'user A sees only their connection'
);
select results_eq(
  $$select count(*) from public.broker_accounts$$,
  array[2::bigint],
  'user A sees only their accounts'
);
select results_eq(
  $$select count(*) from public.broker_accounts where id = '00000000-0000-4000-8000-000000000923'$$,
  array[0::bigint],
  'user A cannot read user B account by known UUID'
);
select results_eq(
  $$update public.broker_accounts set masked_name = 'tampered' where id = '00000000-0000-4000-8000-000000000923' returning id$$,
  array[]::uuid[],
  'user A cannot update user B account by known UUID'
);
select throws_ok(
  $$insert into public.broker_accounts (connection_id, user_id, external_account_id, masked_name, base_currency) values ('00000000-0000-4000-8000-000000000912', '00000000-0000-4000-8000-000000000902', 'forbidden', 'Forbidden', 'USD')$$,
  '42501',
  null,
  'user A cannot insert an account owned by user B'
);
select results_eq(
  $$select public.select_my_broker_account('00000000-0000-4000-8000-000000000922')$$,
  array['00000000-0000-4000-8000-000000000922'::uuid],
  'user A can select their second active account'
);
select results_eq(
  $$select is_selected from public.broker_accounts where id = '00000000-0000-4000-8000-000000000921'$$,
  array[false],
  'select RPC clears user A previous selection'
);
select results_eq(
  $$select is_selected from public.broker_accounts where id = '00000000-0000-4000-8000-000000000922'$$,
  array[true],
  'select RPC sets user A target selection'
);
select throws_ok(
  $$select public.select_my_broker_account('00000000-0000-4000-8000-000000000923')$$,
  'P0002',
  'active brokerage account not found',
  'user A cannot select user B account through the RPC'
);
select results_eq(
  $$select id from public.broker_accounts where is_selected order by id$$,
  array['00000000-0000-4000-8000-000000000922'::uuid],
  'failed cross-user selection leaves user A selection unchanged'
);
select throws_ok(
  $$update public.broker_accounts set is_selected = true where id = '00000000-0000-4000-8000-000000000921'$$,
  '23505',
  null,
  'unique index prevents two selected accounts for user A'
);
select results_eq(
  $$select count(*) from public.broker_connections where user_id = '00000000-0000-4000-8000-000000000902'$$,
  array[0::bigint],
  'user A cannot filter into user B connection'
);

reset role;
set local role authenticated;
set local request.jwt.claim.sub = '00000000-0000-4000-8000-000000000902';

select results_eq(
  $$select count(*) from public.broker_connections$$,
  array[1::bigint],
  'user B sees only their connection'
);
select results_eq(
  $$select count(*) from public.broker_accounts$$,
  array[1::bigint],
  'user B sees only their account'
);
select results_eq(
  $$select coalesce(bool_or(is_selected), false) from public.broker_accounts$$,
  array[false],
  'user A selection did not alter user B account'
);

reset role;
set local role anon;
select throws_ok(
  $$select count(*) from public.broker_connections$$,
  '42501',
  null,
  'anon cannot read broker connections'
);

select * from finish();
rollback;
