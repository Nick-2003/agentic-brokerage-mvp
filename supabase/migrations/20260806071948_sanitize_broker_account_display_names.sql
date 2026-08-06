-- Gate 5 data repair: remove account-holder text from the observed IBKR display
-- name shape. Future imports are sanitized in the backend before persistence.
begin;

update public.broker_accounts
set masked_name = 'Interactive Brokers'
where masked_name ~* '^(Interactive Brokers|IBKR)[[:space:]]*\(';

commit;
