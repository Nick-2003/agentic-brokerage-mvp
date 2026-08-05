#!/usr/bin/env python3
"""Static contract guard for staged or applied proposal 103 frontend code."""
from pathlib import Path


TEST_FILE = Path(__file__).resolve()
IN_PROPOSAL = ".proposed_changes" in TEST_FILE.parts
ROOT = TEST_FILE.parents[3] if IN_PROPOSAL else TEST_FILE.parents[1]
PROPOSAL = ROOT / ".proposed_changes/103-snaptrade-add-change-brokerage"
COMPONENT = (
    PROPOSAL / "frontend/components/SnapTradeConnection.tsx"
    if IN_PROPOSAL
    else ROOT / "frontend/components/SnapTradeConnection.tsx"
)
source = COMPONENT.read_text()

# The existing backend-only portal flow is reused; browser code still receives only
# the short-lived URL and never application or per-user secrets.
assert source.count("async function openPortal()") == 1
assert source.count("createSnapTradeSession(token)") == 1
assert source.count("window.location.assign(result.data.portal_url)") == 1
assert "SNAPTRADE_CLIENT_ID" not in source
assert "SNAPTRADE_CONSUMER_KEY" not in source
assert "userSecret" not in source

# Active users get a portal action in addition to refresh and account selection.
assert "!loading && connection?.status === 'active'" in source
assert "'Add or change brokerage'" in source
assert "onClick={openPortal}" in source
assert "Existing imported accounts stay" in source

# Existing selection behavior and its single-account semantics remain intact.
assert "accounts.map((account)" in source
assert "selectBrokerAccount(token, accountId)" in source
assert "account.is_selected ? 'Selected' : 'Select'" in source
assert "trackBrokerAccountSelected('snaptrade')" in source

print("103 snaptrade add/change brokerage: PASS")
