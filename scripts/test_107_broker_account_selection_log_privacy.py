#!/usr/bin/env python3
"""Contract guard for staged or applied proposal 107."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from pydantic import ValidationError


TEST_FILE = Path(__file__).resolve()
IN_PROPOSAL = ".proposed_changes" in TEST_FILE.parts
ROOT = TEST_FILE.parents[3] if IN_PROPOSAL else TEST_FILE.parents[1]
PROPOSAL = ROOT / ".proposed_changes/107-broker-account-selection-log-privacy"
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend/lib/brokerage.ts"
PROBE = ROOT / "scripts/snaptrade_isolation_probe.py"

if IN_PROPOSAL:
    backend_patch = (PROPOSAL / "backend/snaptrade_api.py.patch").read_text()
    frontend_patch = (PROPOSAL / "frontend/lib/brokerage.ts.patch").read_text()
    probe_patch = (PROPOSAL / "scripts/snaptrade_isolation_probe.py.patch").read_text()

    assert '+@router.post("/broker-accounts/select")' in backend_patch
    assert '-@router.post("/broker-accounts/{account_id}/select")' in backend_patch
    assert "+    account_id: UUID" in backend_patch
    assert "+    req: BrokerAccountSelectRequest" in backend_patch
    assert "+            user_jwt, str(req.account_id)" in backend_patch

    assert "+    '/api/broker-accounts/select'" in frontend_patch
    assert "+      body: JSON.stringify({ account_id: accountId })" in frontend_patch
    assert "-    `/api/broker-accounts/${encodeURIComponent(accountId)}/select`" in frontend_patch

    assert '+        "/api/broker-accounts/select"' in probe_patch
    assert '+        json={"account_id": victim_account_id}' in probe_patch
    assert '-        f"/api/broker-accounts/{victim_account_id}/select"' in probe_patch
else:
    backend_source = (BACKEND / "snaptrade_api.py").read_text()
    frontend_source = FRONTEND.read_text()
    probe_source = PROBE.read_text()

    assert '@router.post("/broker-accounts/select")' in backend_source
    assert '@router.post("/broker-accounts/{account_id}/select")' not in backend_source
    assert "class BrokerAccountSelectRequest(BaseModel):" in backend_source
    assert "account_id: UUID" in backend_source
    assert "user_jwt, str(req.account_id)" in backend_source

    assert "'/api/broker-accounts/select'" in frontend_source
    assert "body: JSON.stringify({ account_id: accountId })" in frontend_source
    assert "encodeURIComponent(accountId)" not in frontend_source

    assert '"/api/broker-accounts/select"' in probe_source
    assert 'json={"account_id": victim_account_id}' in probe_source
    assert 'f"/api/broker-accounts/{victim_account_id}/select"' not in probe_source

    os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
    os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
    sys.path.insert(0, str(BACKEND))

    import snaptrade_api as api
    from auth import AuthCtx

    valid_id = "e26f5473-d40b-479c-87bd-29f9f81c21c9"
    try:
        api.BrokerAccountSelectRequest(account_id="not-a-uuid")
        raise AssertionError("invalid account ID was accepted")
    except ValidationError:
        pass

    calls: list[tuple[str, str]] = []
    original_select = api.broker_connections.select_my_broker_account
    original_state = api.broker_connections.list_my_brokerage_state

    async def select(user_jwt: str, account_id: str) -> str:
        calls.append((user_jwt, account_id))
        return account_id

    async def state(user_jwt: str) -> dict[str, list[object]]:
        assert user_jwt == "user-jwt"
        return {"connections": [], "accounts": []}

    async def exercise() -> None:
        request = api.BrokerAccountSelectRequest(account_id=valid_id)
        result = await api.select_broker_account(
            request, AuthCtx(user_id="user-a", token="user-jwt")
        )
        assert result["selected_account_id"] == valid_id
        assert result["state"] == {"connections": [], "accounts": []}

    try:
        api.broker_connections.select_my_broker_account = select
        api.broker_connections.list_my_brokerage_state = state
        asyncio.run(exercise())
    finally:
        api.broker_connections.select_my_broker_account = original_select
        api.broker_connections.list_my_brokerage_state = original_state

    assert calls == [("user-jwt", valid_id)]

print("107 broker account selection log privacy: PASS")
