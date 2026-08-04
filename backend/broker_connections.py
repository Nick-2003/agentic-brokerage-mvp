"""Minimal provider-neutral brokerage connection persistence.

User-facing operations run with the caller's Supabase JWT and are constrained by
RLS.  The one system read is for a trusted ``user_id`` already resolved from auth;
it returns the selected provider context required by a portfolio provider.
"""
from __future__ import annotations

import os
from typing import Any, Iterable

from supabase import AsyncClient, acreate_client

import broker_secret_crypto
from db import _client_for_user, _supabase_url


class BrokerConnectionStateError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _service_key() -> str:
    value = os.getenv("SUPABASE_SERVICE_KEY", "").strip()
    if not value or value.endswith("REPLACE"):
        raise BrokerConnectionStateError(
            "broker_service_key_missing", "SUPABASE_SERVICE_KEY is not configured"
        )
    return value


async def _admin_client() -> AsyncClient:
    return await acreate_client(_supabase_url(), _service_key())


_CONNECTION_PUBLIC_COLUMNS = (
    "id",
    "provider",
    "status",
    "last_error_code",
    "created_at",
    "updated_at",
)
_ACCOUNT_PUBLIC_COLUMNS = (
    "id",
    "connection_id",
    "masked_name",
    "base_currency",
    "is_selected",
    "status",
    "created_at",
    "updated_at",
)


def _public_connection(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row.get(key) for key in _CONNECTION_PUBLIC_COLUMNS if key in row}


def _public_account(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row.get(key) for key in _ACCOUNT_PUBLIC_COLUMNS if key in row}


def _account_rows(
    *,
    user_id: str,
    connection_id: str,
    accounts: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for account in accounts:
        external_id = str(account.get("external_account_id") or "").strip()
        if not external_id:
            raise BrokerConnectionStateError(
                "broker_account_invalid", "external_account_id is required"
            )
        currency = str(account.get("base_currency") or "").strip().upper()
        if len(currency) != 3 or not currency.isalpha():
            raise BrokerConnectionStateError(
                "broker_account_invalid", "base_currency must be a three-letter ISO code"
            )
        masked_name = str(account.get("masked_name") or "Brokerage account").strip()
        rows.append(
            {
                "connection_id": connection_id,
                "user_id": user_id,
                "external_account_id": external_id,
                "masked_name": masked_name[:120],
                "base_currency": currency,
                # Omit is_selected: inserts receive the DB default (false), while an
                # upsert of an existing account preserves its current selection.
                "status": "active",
            }
        )
    return rows


async def upsert_my_snaptrade_identity(
    user_jwt: str,
    user_id: str,
    *,
    external_user_id: str,
    user_secret: str,
) -> dict[str, Any] | None:
    """Persist a SnapTrade identity under the user's JWT; return no identifiers."""
    row = {
        "user_id": user_id,
        "provider": "snaptrade",
        "external_user_id": external_user_id,
        "encrypted_user_secret": broker_secret_crypto.encrypt_broker_secret(user_secret),
        "status": "pending",
        "last_error_code": None,
    }
    client = await _client_for_user(user_jwt)
    result = (
        await client.table("broker_connections")
        .upsert(row, on_conflict="user_id,provider")
        .execute()
    )
    return _public_connection(result.data[0]) if result.data else None


async def get_my_snaptrade_identity_private(
    user_jwt: str, user_id: str
) -> dict[str, Any] | None:
    """Resolve this caller's SnapTrade credentials for server-side API calls.

    This is deliberately not an HTTP response model. The query runs with the
    caller's JWT/RLS and the decrypted secret must never leave the backend.
    """
    client = await _client_for_user(user_jwt)
    result = (
        await client.table("broker_connections")
        .select("*")
        .eq("user_id", user_id)
        .eq("provider", "snaptrade")
        .limit(1)
        .execute()
    )
    row = result.data[0] if result.data else None
    if not row:
        return None
    try:
        user_secret = broker_secret_crypto.decrypt_broker_secret(
            row["encrypted_user_secret"]
        )
    except broker_secret_crypto.BrokerSecretCryptoError as exc:
        raise BrokerConnectionStateError(exc.code, str(exc)) from exc
    return {
        "id": row["id"],
        "external_user_id": row["external_user_id"],
        "user_secret": user_secret,
        "status": row["status"],
    }


async def confirm_my_snaptrade_connection(
    user_jwt: str,
    user_id: str,
    *,
    connection_id: str,
    external_connection_id: str,
) -> dict[str, Any] | None:
    """Mark a server-verified portal connection active under user RLS."""
    client = await _client_for_user(user_jwt)
    result = (
        await client.table("broker_connections")
        .update(
            {
                "external_connection_id": external_connection_id,
                "status": "active",
                "last_error_code": None,
            }
        )
        .eq("id", connection_id)
        .eq("user_id", user_id)
        .eq("provider", "snaptrade")
        .execute()
    )
    return _public_connection(result.data[0]) if result.data else None


async def upsert_my_broker_accounts(
    user_jwt: str,
    user_id: str,
    *,
    connection_id: str,
    accounts: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Upsert provider-confirmed account metadata without changing selection."""
    rows = _account_rows(
        user_id=user_id, connection_id=connection_id, accounts=accounts
    )
    if not rows:
        return []
    client = await _client_for_user(user_jwt)
    result = (
        await client.table("broker_accounts")
        .upsert(rows, on_conflict="connection_id,external_account_id")
        .execute()
    )
    return [_public_account(row) for row in result.data or []]


async def list_my_brokerage_state(user_jwt: str) -> dict[str, list[dict[str, Any]]]:
    client = await _client_for_user(user_jwt)
    connections = (
        await client.table("broker_connections")
        .select(",".join(_CONNECTION_PUBLIC_COLUMNS))
        .order("created_at", desc=False)
        .execute()
    )
    accounts = (
        await client.table("broker_accounts")
        .select(",".join(_ACCOUNT_PUBLIC_COLUMNS))
        .order("created_at", desc=False)
        .execute()
    )
    return {
        "connections": connections.data or [],
        "accounts": accounts.data or [],
    }


async def select_my_broker_account(user_jwt: str, account_id: str) -> str:
    """Atomically select one active account through the security-invoker RPC."""
    client = await _client_for_user(user_jwt)
    result = await client.rpc(
        "select_my_broker_account", {"target_account_id": account_id}
    ).execute()
    if not result.data:
        raise BrokerConnectionStateError(
            "broker_account_not_found", "active brokerage account was not found"
        )
    return str(result.data)


async def get_selected_broker_context_admin(user_id: str) -> dict[str, Any] | None:
    """Resolve one trusted user's selected account for a backend portfolio fetch.

    This is the narrow service-role seam used by agent tools and scheduled work,
    which currently receive the verified JWT subject but not the original JWT.
    It must never accept a client-supplied/spoofable user identifier.
    """
    if not user_id or user_id == "demo":
        return None
    client = await _admin_client()
    account_result = (
        await client.table("broker_accounts")
        .select("*")
        .eq("user_id", user_id)
        .eq("is_selected", True)
        .eq("status", "active")
        .limit(1)
        .execute()
    )
    account = account_result.data[0] if account_result.data else None
    if not account:
        return None
    connection_result = (
        await client.table("broker_connections")
        .select("*")
        .eq("id", account["connection_id"])
        .eq("user_id", user_id)
        .eq("status", "active")
        .limit(1)
        .execute()
    )
    connection = connection_result.data[0] if connection_result.data else None
    if not connection:
        return None
    try:
        user_secret = broker_secret_crypto.decrypt_broker_secret(
            connection["encrypted_user_secret"]
        )
    except broker_secret_crypto.BrokerSecretCryptoError as exc:
        raise BrokerConnectionStateError(exc.code, str(exc)) from exc
    return {
        "provider": connection["provider"],
        "connection_id": connection["id"],
        "account_id": account["id"],
        "external_user_id": connection["external_user_id"],
        "user_secret": user_secret,
        "external_connection_id": connection["external_connection_id"],
        "external_account_id": account["external_account_id"],
        "masked_name": account["masked_name"],
        "base_currency": account["base_currency"],
    }
