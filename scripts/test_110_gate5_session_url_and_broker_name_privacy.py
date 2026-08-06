#!/usr/bin/env python3
"""Contract guard for staged or applied Proposal 110."""
from __future__ import annotations

import os
import sys
from pathlib import Path


TEST_FILE = Path(__file__).resolve()
IN_PROPOSAL = ".proposed_changes" in TEST_FILE.parts
ROOT = TEST_FILE.parents[3] if IN_PROPOSAL else TEST_FILE.parents[1]
PROPOSAL = ROOT / ".proposed_changes/110-gate5-session-url-and-broker-name-privacy"
MIGRATION_NAME = "20260806071948_sanitize_broker_account_display_names.sql"

if IN_PROPOSAL:
    analytics = (PROPOSAL / "frontend/lib/analytics.ts.patch").read_text()
    backend = (PROPOSAL / "backend/snaptrade_api.py.patch").read_text()
    client = (PROPOSAL / "frontend/lib/brokerage.ts.patch").read_text()
    component = (PROPOSAL / "frontend/components/SnapTradeConnection.tsx.patch").read_text()
    migration = (PROPOSAL / "supabase/migrations" / MIGRATION_NAME).read_text()

    assert "+        '$session_entry_url'," in analytics
    assert "+        '$session_entry_referrer'," in analytics
    assert "+        '$initial_referrer'," in analytics
    assert '+        return "Interactive Brokers"' in backend
    assert "+export function brokerageAccountDisplayName" in client
    assert "+                  {brokerageAccountDisplayName(account.masked_name)}" in component
else:
    analytics = (ROOT / "frontend/lib/analytics.ts").read_text()
    backend = (ROOT / "backend/snaptrade_api.py").read_text()
    client = (ROOT / "frontend/lib/brokerage.ts").read_text()
    component = (ROOT / "frontend/components/SnapTradeConnection.tsx").read_text()
    migration = (ROOT / "supabase/migrations" / MIGRATION_NAME).read_text()

    for key in ("$session_entry_url", "$session_entry_referrer", "$initial_referrer"):
        assert f"'{key}'" in analytics
    assert "autocapture: false" in analytics
    assert "disable_session_recording: true" in analytics
    assert "export function brokerageAccountDisplayName" in client
    assert "brokerageAccountDisplayName(account.masked_name)" in component
    assert "{account.masked_name}" not in component

    os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
    os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
    sys.path.insert(0, str(ROOT / "backend"))
    import snaptrade_api

    assert snaptrade_api._account_name(
        {"name": "Interactive Brokers (Example Person)"}
    ) == "Interactive Brokers"
    assert snaptrade_api._account_name({"name": "IBKR (Example Person)"}) == "Interactive Brokers"
    assert snaptrade_api._account_name({"name": "Alpaca Paper"}) == "Alpaca Paper"
    assert snaptrade_api._account_name({"name": "Individual"}) == "Individual"

assert migration.strip().startswith("-- Gate 5 data repair")
assert "update public.broker_accounts" in migration.lower()
assert "set masked_name = 'Interactive Brokers'" in migration
assert "where masked_name ~*" in migration
assert migration.strip().endswith("commit;")
assert "delete" not in migration.lower()
assert "service_role" not in migration.lower()

print("110 gate5 session URL and broker name privacy: PASS")
