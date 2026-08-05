#!/usr/bin/env python3
"""Static security/contract guard for staged or applied 092 frontend files."""
from pathlib import Path

TEST_FILE = Path(__file__).resolve()
IN_PROPOSAL = ".proposed_changes" in TEST_FILE.parts
ROOT = TEST_FILE.parents[3] if IN_PROPOSAL else TEST_FILE.parents[1]
FRONTEND = ROOT / "frontend"

client = (FRONTEND / "lib/brokerage.ts").read_text()
component = (FRONTEND / "components/SnapTradeConnection.tsx").read_text()
callback = (
    FRONTEND / "app/settings/brokerage/snaptrade/callback/page.tsx"
).read_text()
analytics_source = (FRONTEND / "lib/analytics.ts").read_text()
connect_source = (FRONTEND / "app/connect/page.tsx").read_text()
next_source = (FRONTEND / "next.config.js").read_text()
browser_source = "\n".join((client, component, callback, connect_source))

for forbidden in (
    "SNAPTRADE_CLIENT_ID",
    "SNAPTRADE_CONSUMER_KEY",
    "BROKER_SECRET_ENC_KEY",
    "SUPABASE_SERVICE_KEY",
    "NEXT_PUBLIC_SNAPTRADE",
):
    assert forbidden not in browser_source

assert "window.location.assign(result.data.portal_url)" in component
assert "trackBrokerConnectionStarted('snaptrade')" in component
assert "portal_url" not in analytics_source
assert "account.id" in component and "account.masked_name" in component
assert "selectBrokerAccount(token, accountId)" in component
assert "external_account_id" not in component

# 101 snapshots the identifier as callback.connectionId; 092 originally used the
# local connectionId name. Assert the call contract rather than the implementation
# variable so this earlier security test composes with the callback lifecycle fix.
assert callback.count("verifySnapTradeConnection(token,") == 1
assert "window.history.replaceState" in callback
assert "{connectionId}" not in callback  # never render the provider identifier
assert "connection_id" in analytics_source
assert "brokerage_authorization_id" in analytics_source

assert "/api/broker-connections/:path*" in next_source
assert "/api/broker-accounts/:path*" in next_source
assert "SnapTradeConnection token={token}" in connect_source
assert "url.protocol !== 'https:'" in client
assert "cache: 'no-store'" in client

print("092 snaptrade connection UI: PASS")
