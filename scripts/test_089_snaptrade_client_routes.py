#!/usr/bin/env python3
"""Offline contract checks for proposal 089; no network or Supabase required."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

TEST_FILE = Path(__file__).resolve()
IN_PROPOSAL = ".proposed_changes" in TEST_FILE.parts
ROOT = TEST_FILE.parents[3] if IN_PROPOSAL else TEST_FILE.parents[1]
PROPOSED_BACKEND = ROOT / ".proposed_changes/089-minimal-snaptrade-client-routes/backend"
LIVE_BACKEND = ROOT / "backend"
BACKEND_UNDER_TEST = PROPOSED_BACKEND if IN_PROPOSAL else LIVE_BACKEND
if IN_PROPOSAL:
    sys.path[:0] = [str(PROPOSED_BACKEND), str(LIVE_BACKEND)]
else:
    sys.path.insert(0, str(LIVE_BACKEND))

os.environ.update(
    SNAPTRADE_CLIENT_ID="client",
    SNAPTRADE_CONSUMER_KEY="consumer",
    SNAPTRADE_REDIRECT_URL="http://localhost:3000/settings/brokerage/snaptrade/callback",
)

import snaptrade_api as api  # noqa: E402
from auth import AuthCtx  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from snaptrade_client import SnapTradeClient  # noqa: E402


class Response:
    def __init__(self, body):
        self.body = body


class Authentication:
    async def aregister_snap_trade_user(self, **kwargs):
        assert kwargs == {"user_id": "app-user"}
        return Response({"userId": "app-user", "userSecret": "secret"})

    async def alogin_snap_trade_user(self, **kwargs):
        assert kwargs["connection_type"] == "read"
        assert kwargs["custom_redirect"] == os.environ["SNAPTRADE_REDIRECT_URL"]
        assert kwargs["immediate_redirect"] is True
        return Response({"redirectURI": "https://app.snaptrade.com/snapTrade/redeemToken?x=1"})


class Connections:
    async def alist_brokerage_authorizations(self, **kwargs):
        return Response([{"id": "authorization-1", "disabled": False}])

    async def alist_brokerage_authorization_accounts(self, **kwargs):
        assert kwargs["authorization_id"] == "authorization-1"
        return Response([{"id": "external-account-1", "name": "Individual"}])


class AccountInformation:
    async def aget_user_account_details(self, **kwargs):
        return Response(
            {"balance": {"total": {"amount": 1000, "currency": {"code": "USD"}}}}
        )

    async def aget_user_account_balance(self, **kwargs):
        return Response([{"currency": {"code": "USD"}, "cash": 250}])

    async def aget_all_account_positions(self, **kwargs):
        return Response({"results": [{"units": 1, "symbol": {"symbol": "AAPL"}}]})


class SDK:
    authentication = Authentication()
    connections = Connections()
    account_information = AccountInformation()


async def test_client() -> None:
    client = SnapTradeClient(sdk=SDK())
    registered = await client.register_user(user_id="app-user")
    assert registered == {"external_user_id": "app-user", "user_secret": "secret"}
    portal = await client.create_portal_session(
        user_id="app-user", user_secret="secret", broker="INTERACTIVE_BROKERS"
    )
    assert portal.startswith("https://")
    assert (await client.list_connections(user_id="app-user", user_secret="secret"))[0][
        "id"
    ] == "authorization-1"
    assert len(
        await client.get_account_positions(
            account_id="external-account-1", user_id="app-user", user_secret="secret"
        )
    ) == 1


class RouteClient:
    async def create_portal_session(self, **kwargs):
        assert kwargs["user_secret"] == "secret"
        return "https://app.snaptrade.com/snapTrade/redeemToken?x=1"

    async def list_connections(self, **kwargs):
        return [{"id": "authorization-1", "disabled": False}]

    async def list_connection_accounts(self, **kwargs):
        return [{"id": "external-account-1", "name": "Individual"}]

    async def get_account_details(self, **kwargs):
        return {"balance": {"total": {"currency": {"code": "usd"}}}}


async def test_routes() -> None:
    auth = AuthCtx(user_id="00000000-0000-0000-0000-000000000001", token="jwt")
    identity = {
        "id": "local-connection-1",
        "external_user_id": "app-user",
        "user_secret": "secret",
    }
    stored_accounts = []
    public_state = {
        "connections": [{"id": "local-connection-1", "provider": "snaptrade"}],
        "accounts": [{"id": "local-account-1", "masked_name": "Individual"}],
    }

    api.SnapTradeClient = RouteClient

    async def get_identity(*args, **kwargs):
        return identity

    async def confirm(*args, **kwargs):
        return {"id": "local-connection-1", "status": "active"}

    async def upsert_accounts(*args, **kwargs):
        stored_accounts.extend(kwargs["accounts"])
        return kwargs["accounts"]

    async def list_state(*args, **kwargs):
        return public_state

    api.broker_connections.get_my_snaptrade_identity_private = get_identity
    api.broker_connections.confirm_my_snaptrade_connection = confirm
    api.broker_connections.upsert_my_broker_accounts = upsert_accounts
    api.broker_connections.list_my_brokerage_state = list_state

    session = await api.create_snaptrade_session(api.SnapTradeSessionRequest(), auth)
    assert session["expires_in_seconds"] == 300
    assert "user_secret" not in repr(session)

    state = await api.verify_snaptrade_connection(
        api.SnapTradeVerifyRequest(external_connection_id="authorization-1"), auth
    )
    assert state == public_state
    assert stored_accounts == [
        {
            "external_account_id": "external-account-1",
            "masked_name": "Individual",
            "base_currency": "USD",
        }
    ]
    assert "external_account_id" not in repr(state)

    try:
        await api.verify_snaptrade_connection(
            api.SnapTradeVerifyRequest(external_connection_id="unverified-hint"), auth
        )
    except HTTPException as exc:
        assert exc.status_code == 409
        assert exc.detail == "snaptrade_connection_not_verified"
    else:
        raise AssertionError("unverified callback hint was accepted")


async def main() -> None:
    await test_client()
    await test_routes()
    source = (BACKEND_UNDER_TEST / "snaptrade_client.py").read_text()
    assert "get_all_user_holdings" not in source  # deprecated for new customers
    print("089 snaptrade client/routes: PASS")


if __name__ == "__main__":
    asyncio.run(main())
