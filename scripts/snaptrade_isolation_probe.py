#!/usr/bin/env python3
"""Staging-only, two-user HTTP isolation probe for the applied brokerage routes."""
from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

ACK = "I_UNDERSTAND_STAGING_ONLY"
FORBIDDEN_KEYS = {
    "external_user_id",
    "external_connection_id",
    "external_account_id",
    "encrypted_user_secret",
    "user_secret",
}


def required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"missing required environment variable: {name}")
    return value


def assert_public(value: Any) -> None:
    if isinstance(value, dict):
        assert not (FORBIDDEN_KEYS & set(value)), "public response exposed provider credentials"
        for child in value.values():
            assert_public(child)
    elif isinstance(value, list):
        for child in value:
            assert_public(child)


async def state(client: httpx.AsyncClient, token: str) -> dict[str, Any]:
    response = await client.get(
        "/api/broker-connections", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200, f"state request failed with HTTP {response.status_code}"
    payload = response.json()
    assert_public(payload)
    return payload


async def cross_select(
    client: httpx.AsyncClient, attacker_token: str, victim_account_id: str
) -> None:
    response = await client.post(
        f"/api/broker-accounts/{victim_account_id}/select",
        headers={"Authorization": f"Bearer {attacker_token}"},
    )
    assert response.status_code in {404, 409}, (
        f"cross-user account selection was not rejected: HTTP {response.status_code}"
    )


async def main() -> None:
    if os.getenv("SNAPTRADE_ISOLATION_VERIFY") != ACK:
        raise SystemExit(
            f"refusing live probe; set SNAPTRADE_ISOLATION_VERIFY={ACK} for staging test users"
        )
    base_url = required("SNAPTRADE_VERIFY_BACKEND_URL").rstrip("/")
    token_a = required("SNAPTRADE_VERIFY_USER_A_JWT")
    token_b = required("SNAPTRADE_VERIFY_USER_B_JWT")
    assert token_a != token_b, "two distinct test-user JWTs are required"

    async with httpx.AsyncClient(base_url=base_url, timeout=20, follow_redirects=False) as client:
        before_a, before_b = await asyncio.gather(state(client, token_a), state(client, token_b))
        accounts_a = before_a.get("accounts") or []
        accounts_b = before_b.get("accounts") or []
        assert accounts_a and accounts_b, "both staging users need at least one broker account"
        ids_a = {item["id"] for item in accounts_a}
        ids_b = {item["id"] for item in accounts_b}
        assert ids_a.isdisjoint(ids_b), "users received overlapping local account IDs"

        await cross_select(client, token_a, next(iter(ids_b)))
        await cross_select(client, token_b, next(iter(ids_a)))

        after_a, after_b = await asyncio.gather(state(client, token_a), state(client, token_b))
        selected_a = {item["id"] for item in before_a["accounts"] if item["is_selected"]}
        selected_b = {item["id"] for item in before_b["accounts"] if item["is_selected"]}
        assert selected_a == {item["id"] for item in after_a["accounts"] if item["is_selected"]}
        assert selected_b == {item["id"] for item in after_b["accounts"] if item["is_selected"]}

    print("SnapTrade staging two-user isolation: PASS")


if __name__ == "__main__":
    asyncio.run(main())
