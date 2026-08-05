#!/usr/bin/env python3
"""Offline checks for sanitized SnapTrade failures and registration recovery."""
from __future__ import annotations

import asyncio
import io
import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace

TEST_FILE = Path(__file__).resolve()
IN_PROPOSAL = ".proposed_changes" in TEST_FILE.parts
ROOT = TEST_FILE.parents[3] if IN_PROPOSAL else TEST_FILE.parents[1]
PROPOSAL = ROOT / ".proposed_changes/097-snaptrade-failure-diagnostics-recovery"
BACKEND = PROPOSAL / "backend" if IN_PROPOSAL else ROOT / "backend"
FRONTEND = PROPOSAL / "frontend" if IN_PROPOSAL else ROOT / "frontend"
sys.path.insert(0, str(BACKEND))
if IN_PROPOSAL:
    sys.path.append(str(ROOT / "backend"))

os.environ.update(
    SNAPTRADE_CLIENT_ID="client",
    SNAPTRADE_CONSUMER_KEY="consumer",
    SNAPTRADE_REDIRECT_URL=(
        "https://agentic-brokerage-mvp-front.vercel.app/"
        "settings/brokerage/snaptrade/callback"
    ),
)

import snaptrade_api as api  # noqa: E402
import snaptrade_gateway as gateway  # noqa: E402
from fastapi import HTTPException  # noqa: E402


class ProviderFailure(Exception):
    def __init__(self) -> None:
        self.status = 400
        self.body = {
            "detail": "User with the following userId already exists: secret-user-id",
            "status_code": 400,
            "code": "1010",
        }
        self.headers = {"X-Request-ID": "bf41b688e24b89f741f455d1e889bbb9"}


class FailingAuthentication:
    def register_snap_trade_user(self, **kwargs):
        raise ProviderFailure()


class FailingSDK:
    authentication = FailingAuthentication()


async def test_sanitized_provider_failure() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    gateway.logger.addHandler(handler)
    gateway.logger.setLevel(logging.WARNING)
    try:
        client = gateway.SnapTradeClient(sdk=FailingSDK())
        try:
            await client.register_user(user_id="secret-user-id")
        except gateway.SnapTradeClientError as exc:
            assert exc.code == "snaptrade_user_already_exists"
            assert exc.status == 400
            assert exc.operation == "register_user"
            assert exc.provider_code == "1010"
            assert exc.request_id == "bf41b688e24b89f741f455d1e889bbb9"
            http_error = api._http_error(exc)
            assert http_error.status_code == 409
            assert http_error.detail == "snaptrade_user_already_exists"
        else:
            raise AssertionError("provider error was not classified")
    finally:
        gateway.logger.removeHandler(handler)

    output = stream.getvalue()
    assert "operation=register_user" in output
    assert "status=400" in output
    assert "provider_code=1010" in output
    assert "request_id=bf41b688e24b89f741f455d1e889bbb9" in output
    assert "secret-user-id" not in output
    assert "User with the following" not in output


class RecoveryClient:
    def __init__(self, *, delete_fails: bool = False) -> None:
        self.delete_fails = delete_fails
        self.deleted: list[str] = []

    async def register_user(self, *, user_id: str):
        return {"external_user_id": user_id, "user_secret": "never-log-this-secret"}

    async def delete_user(self, *, user_id: str) -> None:
        self.deleted.append(user_id)
        if self.delete_fails:
            raise gateway.SnapTradeClientError(
                "snaptrade_unavailable",
                "provider unavailable",
                status=503,
                operation="delete_user",
                provider_code="5000",
                request_id="safe-request-id",
            )


async def test_registration_compensation() -> None:
    original_get = api.broker_connections.get_my_snaptrade_identity_private
    original_upsert = api.broker_connections.upsert_my_snaptrade_identity

    async def no_identity(*args, **kwargs):
        return None

    async def failed_store(*args, **kwargs):
        raise RuntimeError("encrypted_user_secret=never-log-this-secret")

    api.broker_connections.get_my_snaptrade_identity_private = no_identity
    api.broker_connections.upsert_my_snaptrade_identity = failed_store

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    api.logger.addHandler(handler)
    api.logger.setLevel(logging.INFO)
    try:
        client = RecoveryClient()
        try:
            await api._identity_or_register(
                user_jwt="jwt",
                auth=SimpleNamespace(user_id="new-app-user"),
                client=client,
            )
        except HTTPException as exc:
            assert exc.status_code == 503
            assert exc.detail == "broker_connection_store_failed"
        else:
            raise AssertionError("failed storage was accepted")
        assert client.deleted == ["new-app-user"]
        assert "never-log-this-secret" not in stream.getvalue()

        recovery_client = RecoveryClient(delete_fails=True)
        try:
            await api._identity_or_register(
                user_jwt="jwt",
                auth=SimpleNamespace(user_id="orphan-risk-user"),
                client=recovery_client,
            )
        except HTTPException as exc:
            assert exc.status_code == 409
            assert exc.detail == "snaptrade_registration_recovery_required"
        else:
            raise AssertionError("failed compensation was hidden")
    finally:
        api.logger.removeHandler(handler)
        api.broker_connections.get_my_snaptrade_identity_private = original_get
        api.broker_connections.upsert_my_snaptrade_identity = original_upsert


async def test_registration_race_recheck() -> None:
    original_get = api.broker_connections.get_my_snaptrade_identity_private
    calls = 0
    stored = {
        "id": "stored-connection",
        "external_user_id": "race-user",
        "user_secret": "stored-secret",
    }

    async def identity_after_registration(*args, **kwargs):
        nonlocal calls
        calls += 1
        return None if calls == 1 else stored

    class RaceClient:
        async def register_user(self, *, user_id: str):
            raise gateway.SnapTradeClientError(
                "snaptrade_user_already_exists",
                "duplicate",
                status=400,
                operation="register_user",
                provider_code="1010",
            )

    api.broker_connections.get_my_snaptrade_identity_private = identity_after_registration
    try:
        result = await api._identity_or_register(
            user_jwt="jwt",
            auth=SimpleNamespace(user_id="race-user"),
            client=RaceClient(),
        )
        assert result == stored
        assert calls == 2
    finally:
        api.broker_connections.get_my_snaptrade_identity_private = original_get


async def main() -> None:
    await test_sanitized_provider_failure()
    await test_registration_compensation()
    await test_registration_race_recheck()

    gateway_source = (BACKEND / "snaptrade_gateway.py").read_text()
    api_source = (BACKEND / "snaptrade_api.py").read_text()
    frontend_source = (FRONTEND / "lib/brokerage.ts").read_text()
    assert "asyncio.to_thread" in gateway_source
    assert "aregister_snap_trade_user" not in gateway_source
    assert "timeout=_timeout_seconds()," not in gateway_source
    assert "snaptrade_provider_error operation=%s" in gateway_source
    assert "snaptrade_user_already_exists" in api_source
    assert "_compensate_unstored_registration" in api_source
    assert "snaptrade_user_already_exists" in frontend_source
    assert "snaptrade_registration_recovery_required" in frontend_source
    print("097 snaptrade failure diagnostics/recovery: PASS")


if __name__ == "__main__":
    asyncio.run(main())
