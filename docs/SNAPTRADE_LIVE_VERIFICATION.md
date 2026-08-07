# SnapTrade verification and promotion runbook

This runbook proves that the SnapTrade connection, account-selection, portfolio,
privacy, and rollback paths are suitable for promotion. It never places an order.
Use fixtures and local Supabase first, disposable staging users second, and one
operator-controlled read-only live account last.

Keep these invariants throughout the inspection:

```dotenv
TRADING_ENABLED=0
PORTFOLIO_SOURCE=ibkr
```

Do not set `PORTFOLIO_SOURCE=snaptrade` until gates 1 through 6 pass. Gate 7 then
rehearses `ibkr -> snaptrade -> ibkr` and proves that rollback is a source-variable
change plus redeployment, not a destructive database reversal.

## Evidence and secret-handling rules

- Store only command names, HTTP statuses, booleans, counts, timestamps, and
  redacted screenshots in the inspection record.
- Never store JWTs, SnapTrade `userSecret`, consumer keys, encryption keys,
  callback query values, account IDs, authorization IDs, holdings, or balances.
- Enter JWTs with `read -rsp`; do not put them directly in shell history.
- Use separate/private browser profiles for separate users.
- Revoke disposable-user sessions and remove test analytics people/exports after
  the privacy inspection. Do not delete brokerage rows merely to perform rollback.
- Stop promotion when any required command fails or any required field is absent.

## Gate 1: Offline regressions and frontend build

**Purpose:** prove the complete applied SnapTrade, callback, account-selection,
analytics, and privacy stack still composes without making provider calls.

Install exactly the locked frontend dependencies, run every applied SnapTrade
regression, then type-check and build the production frontend:

```bash
pnpm --dir frontend install --frozen-lockfile

backend/.venv/bin/python scripts/test_089_snaptrade_client_routes.py
backend/.venv/bin/python scripts/test_090_snaptrade_portfolio.py
backend/.venv/bin/python scripts/test_092_snaptrade_connection_ui.py
backend/.venv/bin/python scripts/test_093_snaptrade_fixtures.py
backend/.venv/bin/python scripts/test_094_snaptrade_post_apply_paths.py
backend/.venv/bin/python scripts/test_097_snaptrade_failure_diagnostics.py
backend/.venv/bin/python scripts/test_101_snaptrade_callback_single_processing.py
backend/.venv/bin/python scripts/test_103_snaptrade_add_change_brokerage.py
backend/.venv/bin/python scripts/test_105_posthog_analytics_identity_helpers.py
backend/.venv/bin/python scripts/test_106_posthog_supabase_auth_lifecycle.py
backend/.venv/bin/python scripts/test_107_broker_account_selection_log_privacy.py
backend/.venv/bin/python scripts/test_108_posthog_disable_sensitive_dom_capture.py
backend/.venv/bin/python scripts/test_110_gate5_session_url_and_broker_name_privacy.py
backend/.venv/bin/python scripts/test_111_cross_user_selection_not_found_mapping.py

pnpm --dir frontend typecheck
pnpm --dir frontend build
```

Tests copied from `.proposed_changes/` into `scripts/` must resolve the repository
from either location; a path assumption that works only inside a proposal is a
test defect. Gate 1 passes only when every command exits zero. Next.js warnings
about multiple parent lockfiles should be recorded and corrected separately, but
do not negate a successful compile, type-check, and build unless build tracing is
actually incorrect.

## Gate 2: Clean migration replay and database isolation

**Purpose:** prove a clean database receives the required tables, RPC, grants,
indexes, and RLS policies, and that isolation is enforced by PostgreSQL rather
than only by application filtering.

Inspect the installed CLI, start the local stack, replay all migrations, execute
pgTAP, and run the database checks:

```bash
supabase --version
supabase --help
supabase test db --help
supabase db lint --help
supabase db advisors --help

supabase start
supabase db reset --local
supabase test db
supabase db lint --local --schema public --fail-on error
supabase db advisors --local
```

Gate 2 requires:

- `supabase db reset --local` completes successfully;
- all 22 assertions in `093_broker_connections_rls.test.sql` pass;
- lint reports no schema errors;
- advisors report no unreviewed security issue.

The pgTAP test uses two synthetic `authenticated` JWT subjects inside
`begin`/`rollback`. A query executed as `postgres` or `service_role` does not prove
RLS. If reset fails and the test reports that `broker_connections` does not exist,
repair the local container/migration replay first; a lint pass against the stale
partial database is not sufficient.

## Gate 3: Two-user staging HTTP isolation

**Purpose:** exercise deployed JWT validation, FastAPI routing, public response
shaping, Supabase RLS, and cross-user selection rejection together.

Create two disposable users in the same non-production Supabase project. Connect
at least one brokerage account for each in separate browser profiles. Obtain a
fresh access token from each authenticated browser session, then enter them without
echoing or storing them:

```bash
read -rsp "User A JWT: " SNAPTRADE_VERIFY_USER_A_JWT
echo
read -rsp "User B JWT: " SNAPTRADE_VERIFY_USER_B_JWT
echo
export SNAPTRADE_VERIFY_USER_A_JWT
export SNAPTRADE_VERIFY_USER_B_JWT
export SNAPTRADE_VERIFY_BACKEND_URL="https://STAGING_BACKEND"

SNAPTRADE_ISOLATION_VERIFY=I_UNDERSTAND_STAGING_ONLY \
  backend/.venv/bin/python scripts/snaptrade_isolation_probe.py
```

Expected output:

```text
SnapTrade staging two-user isolation: PASS
```

The probe requires disjoint local account sets, no external identifiers in public
responses, 404/409 rejection when either user selects the other's known local
account ID, and unchanged selections after both rejected attempts. HTTP 500 is not
an acceptable isolation result even if no cross-user update occurs.

If either initial state request returns 401, verify that both tokens are unexpired,
have `aud=authenticated`, use the same Supabase issuer as the backend, and that the
URL is the Railway backend rather than the Vercel frontend. Never print the token
or its `sub` while diagnosing it.

Clean up after the probe:

```bash
unset SNAPTRADE_VERIFY_USER_A_JWT
unset SNAPTRADE_VERIFY_USER_B_JWT
unset SNAPTRADE_VERIFY_BACKEND_URL
```

Revoke both sessions after evidence collection. Delete the disposable users only
when they are no longer needed by later gates.

## Gate 4: Callback and account-selection journey

**Purpose:** prove that real portal completion, callback verification, account
import, selection, and refresh agree with backend state.

Configure the exact preview callback in both the backend and SnapTrade:

```dotenv
SNAPTRADE_REDIRECT_URL=https://PREVIEW_HOST/settings/brokerage/snaptrade/callback
```

For both staging users in separate browser profiles:

1. Sign in and open `/connect`.
2. Choose **Connect brokerage** and complete the read-only SnapTrade portal.
3. Return through the callback and confirm exactly one completion outcome.
4. Confirm only that user's masked brokerage accounts and currencies appear.
5. Select an account, reload `/connect`, and confirm the selection persists.
6. Use **Add or change brokerage** to add another supported brokerage and verify
   that existing imported accounts remain selectable.
7. Select between the imported accounts and confirm the portfolio follows the
   selected account.

Inspect browser Network while performing a new callback. Required backend records
include successful session/verify, account-list, and account-selection requests.
An already completed callback will not emit another verify request merely by
opening `/connect`; create a new portal journey when verify-route evidence is
needed. Gate 4 fails on a callback false-negative, duplicate processing, lost
selection, cross-user account display, or inability to add/change brokerage.

Provider-side disconnection/removal is a separate lifecycle feature. Its absence
does not invalidate selection among active imported accounts, but it must not be
represented as implemented.

## Gate 5: Analytics identity, reset, and privacy

**Purpose:** prove events belong to the authenticated app user and that Railway,
PostHog, browser URLs, and session capture do not become stores for credentials or
financial/provider identifiers.

First run the analytics/privacy regressions included in Gate 1. Then perform two
fresh authenticated journeys in separate browser profiles:

1. Sign in as user A, connect/select an account, and inspect PostHog events.
2. Sign out and confirm the analytics client resets.
3. Sign in as user B in a clean profile and repeat the journey.
4. Confirm each event is attributed to the correct Supabase user identity and no
   anonymous identity from the previous session is reused.
5. Confirm callback URLs are scrubbed and sensitive DOM/session recording is
   disabled for brokerage surfaces.

Search Railway and exported PostHog events for these forbidden values or fields:

```text
Authorization headers or access JWTs
SnapTrade userSecret, consumer key, or client ID
broker secret encryption key
raw connection, authorization, external-account, or account IDs
callback query values or unredacted callback URLs
account numbers, balances, positions, tickers, broker account nicknames
email addresses or other unnecessary direct identifiers
```

Allowed evidence is limited to app-level event names, success/error codes,
provider labels, HTTP method/path/status, deployment timestamps, and the correctly
identified app person. Query paths must be normalized, for example
`POST /api/broker-accounts/select`, without an account ID embedded in the path.

After inspection, revoke the disposable Supabase sessions, remove the test PostHog
people if required by the test-data policy, and delete downloaded exports containing
test data. Record only the sanitized findings. Gate 5 passes when attribution/reset
works for both users and all inspected sources are free of forbidden data.

## Gate 6: Read-only live SnapTrade normalization

**Purpose:** prove one selected live account can be decrypted, fetched, normalized,
and summarized without registration, refresh, selection, or order calls.

Use an operator-controlled Supabase user with a selected SnapTrade account. Load
the controlled backend environment without printing it, set the test UUID, and run
a preflight:

```bash
set -a
source backend/.env
set +a
read -rsp "Test Supabase user UUID: " SNAPTRADE_VERIFY_APP_USER_ID
echo
export SNAPTRADE_VERIFY_APP_USER_ID

backend/.venv/bin/python -c '
import os, uuid
required = [
    "SUPABASE_URL",
    "SUPABASE_SERVICE_KEY",
    "BROKER_SECRET_ENC_KEY",
    "SNAPTRADE_CLIENT_ID",
    "SNAPTRADE_CONSUMER_KEY",
    "SNAPTRADE_REDIRECT_URL",
    "SNAPTRADE_VERIFY_APP_USER_ID",
]
missing = [key for key in required if not os.getenv(key) or "REPLACE" in os.getenv(key, "")]
assert not missing, f"missing/placeholders: {missing}"
uuid.UUID(os.environ["SNAPTRADE_VERIFY_APP_USER_ID"])
print("Gate 6 environment preflight: PASS")
'

SNAPTRADE_LIVE_VERIFY=I_UNDERSTAND_READ_ONLY_LIVE_CALLS \
  backend/.venv/bin/python scripts/snaptrade_live_verify.py
```

Required sanitized result:

```json
{
  "base_currency_present": true,
  "connected": true,
  "data_source": "SnapTrade account data",
  "equity_present": true,
  "freshness_present": true,
  "is_paper": true,
  "normalization_warning_count": 0,
  "ok": true,
  "positions_count": 3,
  "provider": "snaptrade",
  "read_only": true
}
```

`positions_count` and `is_paper` depend on the selected test account and need not
equal the example, but they must be plausible and correctly classified. Review the
actual `as_of`/freshness presentation privately and document the observed delay,
expected plan delay, and operational stale-data rule without recording holdings or
account identifiers. Do not promote if freshness is unexplained or warnings remain
unreviewed.

Clean up the verification-only variable and close any terminal into which the full
backend environment was sourced:

```bash
unset SNAPTRADE_VERIFY_APP_USER_ID
```

## Gate 7: Provider promotion and rollback rehearsal

**Purpose:** prove SnapTrade can become the portfolio source and that the service
can return to the known-good IBKR path without deleting users, connections, or
schema changes.

Use suitable operator-controlled users for each provider. A user connected only to
SnapTrade is expected to receive an empty IBKR portfolio, and vice versa; that is
not a rollback failure. The final IBKR proof must use the IBKR-connected user's
fresh JWT.

### Establish the IBKR baseline

In the Railway backend service, set and deploy:

```dotenv
PORTFOLIO_SOURCE=ibkr
TRADING_ENABLED=0
USE_MOCK_IBKR=0
```

Capture the deployment ID and enter the IBKR-connected user's fresh JWT:

```bash
read -rsp "IBKR test user JWT: " PORTFOLIO_VERIFY_JWT
echo
export PORTFOLIO_VERIFY_JWT
export PORTFOLIO_VERIFY_BACKEND_URL="https://BACKEND_HOST"
```

Run the sanitized baseline check:

```bash
backend/.venv/bin/python -c '
import os, httpx
base = os.environ["PORTFOLIO_VERIFY_BACKEND_URL"].rstrip("/")
token = os.environ["PORTFOLIO_VERIFY_JWT"].strip()
headers = {"Authorization": f"Bearer {token}"}

health = httpx.get(f"{base}/healthz", timeout=30)
connection_response = httpx.get(f"{base}/api/ibkr/connection", headers=headers, timeout=60)
portfolio_response = httpx.get(f"{base}/api/portfolio", headers=headers, timeout=180)
connection_response.raise_for_status()
portfolio_response.raise_for_status()

connection = connection_response.json().get("connection")
portfolio = portfolio_response.json()
print({
    "health_status": health.status_code,
    "ibkr_connection_present": connection is not None,
    "portfolio_status": portfolio_response.status_code,
    "equity_present": portfolio.get("total_equity") is not None,
    "currency_present": bool(portfolio.get("currency")),
    "is_mock": portfolio.get("is_mock"),
    "is_sample": portfolio.get("is_sample"),
})
'
```

The IBKR baseline requires HTTP 200 health/portfolio, a present connection, present
equity/currency, and both mock/sample flags false.

### Promote SnapTrade

Change only the portfolio source and deploy:

```dotenv
PORTFOLIO_SOURCE=snaptrade
TRADING_ENABLED=0
```

Use the SnapTrade-connected user's JWT to run the `/api/portfolio` summary check,
then rerun the Gate 6 verifier so evidence explicitly names provider `snaptrade`.
Capture the deployment ID. HTTP `/healthz` will normally be identical for both
sources and does **not** prove which portfolio provider is active.

### Roll back to IBKR

Restore and deploy:

```dotenv
PORTFOLIO_SOURCE=ibkr
TRADING_ENABLED=0
```

Replace `PORTFOLIO_VERIFY_JWT` with a fresh token for the IBKR-connected user and
rerun the complete IBKR baseline command. A successful HTTP 200 containing null
equity/currency is an intentionally empty portfolio and does not pass the rollback
gate. The required final booleans are:

```text
health_status: 200
ibkr_connection_present: True
portfolio_status: 200
equity_present: True
currency_present: True
is_mock: False
is_sample: False
```

Gate 7 passes when the recorded sequence is:

```text
IBKR baseline: PASS
SnapTrade promotion: PASS
IBKR rollback: PASS
IBKR and SnapTrade connection rows retained: PASS
TRADING_ENABLED remained 0: PASS
```

Do not delete provider rows or reverse migrations during rollback. Retain them for
diagnosis unless a separately approved deletion request exists. Clean up:

```bash
unset PORTFOLIO_VERIFY_JWT
unset PORTFOLIO_VERIFY_BACKEND_URL
```

## Promotion decision

Promotion is approved only when every gate has dated, sanitized passing evidence:

1. Offline regressions, TypeScript, and production build pass.
2. Clean migrations, 22/22 RLS assertions, lint, and advisors pass.
3. The deployed two-user isolation probe prints its single PASS line.
4. Callback and account-selection behavior matches backend state for both users.
5. Analytics identity/reset and privacy inspection pass, followed by test-data cleanup.
6. The live verifier reports a real, read-only, fresh SnapTrade portfolio.
7. `ibkr -> snaptrade -> ibkr` succeeds without destructive recovery.

After Gate 7, production may be deployed again with
`PORTFOLIO_SOURCE=snaptrade`. The rehearsed emergency rollback remains restoring
`PORTFOLIO_SOURCE=ibkr` and redeploying.
