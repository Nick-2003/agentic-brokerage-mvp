#!/usr/bin/env python3
"""Contract guard for staged or applied Proposal 111."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path


TEST_FILE = Path(__file__).resolve()
IN_PROPOSAL = ".proposed_changes" in TEST_FILE.parts
ROOT = TEST_FILE.parents[3] if IN_PROPOSAL else TEST_FILE.parents[1]
PROPOSAL = ROOT / ".proposed_changes/111-cross-user-selection-not-found-mapping"

if IN_PROPOSAL:
    source = (PROPOSAL / "backend/broker_connections.py.patch").read_text()
    assert "+from postgrest.exceptions import APIError" in source
    assert '+        if exc.code == "P0002":' in source
    assert '+                "broker_account_not_found"' in source
    assert "+        raise" in source
else:
    os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
    os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
    sys.path.insert(0, str(ROOT / "backend"))

    import broker_connections
    from postgrest.exceptions import APIError

    class FailingQuery:
        def __init__(self, code: str) -> None:
            self.code = code

        async def execute(self):
            raise APIError(
                {
                    "code": self.code,
                    "message": "database rejected request",
                    "hint": None,
                    "details": None,
                }
            )

    class Client:
        def __init__(self, code: str) -> None:
            self.code = code

        def rpc(self, name: str, params: dict[str, str]):
            assert name == "select_my_broker_account"
            assert params == {"target_account_id": "foreign-account"}
            return FailingQuery(self.code)

    async def run() -> None:
        original = broker_connections._client_for_user
        try:
            async def no_data_client(_token: str):
                return Client("P0002")

            broker_connections._client_for_user = no_data_client
            try:
                await broker_connections.select_my_broker_account(
                    "jwt", "foreign-account"
                )
            except broker_connections.BrokerConnectionStateError as exc:
                assert exc.code == "broker_account_not_found"
            else:
                raise AssertionError("PostgreSQL no_data_found was not translated")

            async def unexpected_client(_token: str):
                return Client("42501")

            broker_connections._client_for_user = unexpected_client
            try:
                await broker_connections.select_my_broker_account(
                    "jwt", "foreign-account"
                )
            except APIError as exc:
                assert exc.code == "42501"
            else:
                raise AssertionError("unexpected database failure was hidden")
        finally:
            broker_connections._client_for_user = original

    asyncio.run(run())

print("111 cross-user selection not-found mapping: PASS")
