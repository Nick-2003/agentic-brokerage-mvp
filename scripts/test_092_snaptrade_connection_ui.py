#!/usr/bin/env python3
"""Static security/contract guard for staged or applied 092 frontend files."""
from pathlib import Path

TEST_FILE = Path(__file__).resolve()
IN_PROPOSAL = ".proposed_changes" in TEST_FILE.parts
ROOT = TEST_FILE.parents[3] if IN_PROPOSAL else TEST_FILE.parents[1]
PROPOSAL = ROOT / ".proposed_changes/092-minimal-snaptrade-connection-ui"
FRONTEND = PROPOSAL / "frontend" if IN_PROPOSAL else ROOT / "frontend"

client = (FRONTEND / "lib/brokerage.ts").read_text()
component = (FRONTEND / "components/SnapTradeConnection.tsx").read_text()
callback = (
    FRONTEND / "app/settings/brokerage/snaptrade/callback/page.tsx"
).read_text()
if IN_PROPOSAL:
    analytics_source = (FRONTEND / "lib/analytics.ts.patch").read_text()
    connect_source = (FRONTEND / "app/connect/page.tsx.patch").read_text()
    next_source = (FRONTEND / "next.config.js.patch").read_text()
else:
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

assert "verifySnapTradeConnection(token, connectionId)" in callback
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
