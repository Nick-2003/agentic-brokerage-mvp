# SnapTrade verification runbook

This runbook validates the applied SnapTrade connection and portfolio path without
placing trades. Use synthetic fixtures and local Supabase first, a preview/staging
deployment second, and one read-only live account last.

## 1. Offline contract fixtures

```bash
backend/.venv/bin/python scripts/test_089_snaptrade_client_routes.py
backend/.venv/bin/python scripts/test_090_snaptrade_portfolio.py
backend/.venv/bin/python scripts/test_092_snaptrade_connection_ui.py
backend/.venv/bin/python scripts/test_093_snaptrade_fixtures.py
```

Expected: all pass without network access. The 093 test confirms current documented
SnapTrade shapes, multi-currency conservatism, paper-account labelling, freshness,
and fixture sanitisation.

## 2. Local database isolation

Discover the installed CLI before relying on flags:

```bash
supabase --version
supabase --help
supabase test db --help
supabase db lint --help
supabase db advisors --help
```

Start and replay the local database, then run transactional pgTAP tests:

```bash
supabase start
supabase db reset --local
supabase test db
supabase db lint --local --schema public --fail-on error
supabase db advisors --local
```

The 093 test creates two synthetic `auth.users`, two connections, and three
accounts inside `begin`/`rollback`. It verifies cross-user read/update/insert and
RPC-selection failures, one-selection uniqueness, anon grants, and selection
isolation. It must not be pointed at the linked production database.

## 3. Preview UI and callback

Use a SnapTrade test key and two dedicated Supabase test users. Confirm the backend
has sealed `SNAPTRADE_CLIENT_ID`, `SNAPTRADE_CONSUMER_KEY`, and
`BROKER_SECRET_ENC_KEY`. Set:

```dotenv
SNAPTRADE_REDIRECT_URL=https://PREVIEW_HOST/settings/brokerage/snaptrade/callback
```

Allow the exact same URL in SnapTrade, then for each test user:

1. sign in at `/connect`;
2. choose **Connect brokerage**;
3. confirm the portal requests read access;
4. return through the callback;
5. confirm only masked account names and base currencies appear;
6. select one account and reload `/connect`;
7. confirm the selection persists and the other user's accounts never appear.

Inspect browser network traffic and PostHog. No consumer key, userSecret, portal
URL, external account/authorization ID, account number, or callback connection ID
may appear in events. The callback query value should be `[redacted]` in URL
properties.

## 4. Two-user staging HTTP probe

This probe deliberately attempts cross-user account selection and must only use
disposable staging test users. Put values in the current shell environment without
committing them:

```bash
SNAPTRADE_ISOLATION_VERIFY=I_UNDERSTAND_STAGING_ONLY \
SNAPTRADE_VERIFY_BACKEND_URL=https://STAGING_BACKEND \
SNAPTRADE_VERIFY_USER_A_JWT=REDACTED \
SNAPTRADE_VERIFY_USER_B_JWT=REDACTED \
backend/.venv/bin/python scripts/snaptrade_isolation_probe.py
```

The output contains only PASS or a redacted HTTP-status failure. The script never
prints JWTs or account IDs. Delete the two staging users or revoke their sessions
after verification.

## 5. Read-only live portfolio check

Use one operator-owned test account with a selected SnapTrade account. This invokes
live account details, balances, and positions, but performs no registration,
connection, selection, refresh, or order call:

```bash
SNAPTRADE_LIVE_VERIFY=I_UNDERSTAND_READ_ONLY_LIVE_CALLS \
SNAPTRADE_VERIFY_APP_USER_ID=YOUR_TEST_SUPABASE_USER_UUID \
backend/.venv/bin/python scripts/snaptrade_live_verify.py
```

Required backend environment variables are the same as the deployed service:
SnapTrade application credentials, Supabase URL/service key, and broker-secret
encryption key. Run locally with controlled secrets or inside a one-off backend
shell. Do not paste them into command arguments.

The output deliberately contains only booleans and counts. It omits user/account
IDs, tickers, holdings values, provider errors, and credentials.

## 6. Promotion gate

Do not switch `PORTFOLIO_SOURCE=snaptrade` until all of the following are true:

- fixture, prior regression, TypeScript, and Next build checks pass; the SnapTrade modules still compose after subsequent repository changes.
- local pgTAP isolation and database lint/advisors pass; isolation is enforced by PostgreSQL under real authenticated JWT claims, not merely by Python filtering.
- two preview users cannot read or select each other's accounts; JWT resolution, FastAPI routes, Supabase RLS, public response shaping, and account selection work together in deployment.
- callback identifiers are absent from PostHog and logs; the UI reports exactly the same outcome as backend state and does not manufacture a failure event.
- the read-only live summary reports `connected=true`, `is_mock=false`, a base
  currency, equity presence, and plausible position count; operational evidence is useful while remaining free of credentials and raw financial/provider identifiers.
- actual account data freshness is understood for the current SnapTrade plan; a selected account becomes a trustworthy provider-neutral portfolio without placing an order.
- `PORTFOLIO_SOURCE=ibkr` remains documented and tested as rollback; promotion is a reversible source switch, not a migration that traps production on SnapTrade.
