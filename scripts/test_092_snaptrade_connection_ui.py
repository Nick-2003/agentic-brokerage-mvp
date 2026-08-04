#!/usr/bin/env python3
"""Static security/contract guard for the staged 092 frontend proposal."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PROPOSAL = ROOT / ".proposed_changes/092-minimal-snaptrade-connection-ui"

client = (PROPOSAL / "frontend/lib/brokerage.ts").read_text()
component = (PROPOSAL / "frontend/components/SnapTradeConnection.tsx").read_text()
callback = (
    PROPOSAL / "frontend/app/settings/brokerage/snaptrade/callback/page.tsx"
).read_text()
analytics_patch = (PROPOSAL / "frontend/lib/analytics.ts.patch").read_text()
connect_patch = (PROPOSAL / "frontend/app/connect/page.tsx.patch").read_text()
next_patch = (PROPOSAL / "frontend/next.config.js.patch").read_text()
browser_source = "\n".join((client, component, callback, connect_patch))

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
assert "portal_url" not in analytics_patch
assert "account.id" in component and "account.masked_name" in component
assert "selectBrokerAccount(token, accountId)" in component
assert "external_account_id" not in component

assert "verifySnapTradeConnection(token, connectionId)" in callback
assert "window.history.replaceState" in callback
assert "{connectionId}" not in callback  # never render the provider identifier
assert "connection_id" in analytics_patch
assert "brokerage_authorization_id" in analytics_patch

assert "/api/broker-connections/:path*" in next_patch
assert "/api/broker-accounts/:path*" in next_patch
assert "SnapTradeConnection token={token}" in connect_patch
assert "url.protocol !== 'https:'" in client
assert "cache: 'no-store'" in client

print("092 snaptrade connection UI: PASS")
