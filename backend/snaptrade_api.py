"""Authenticated, read-only SnapTrade connection routes."""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import broker_connections
from auth import AuthCtx, resolve_auth
from snaptrade_gateway import SnapTradeClient, SnapTradeClientError

router = APIRouter(prefix="/api", tags=["brokerage-connections"])
logger = logging.getLogger(__name__)
_BROKER_SLUG = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


class SnapTradeSessionRequest(BaseModel):
    broker: str | None = Field(default=None, max_length=80)


class SnapTradeVerifyRequest(BaseModel):
    # This is a callback hint only. The route verifies it against SnapTrade.
    external_connection_id: str = Field(..., min_length=1, max_length=128)


class BrokerAccountSelectRequest(BaseModel):
    account_id: UUID


def _require_user(auth: AuthCtx) -> str:
    if auth.token is None or auth.user_id == "demo":
        raise HTTPException(status_code=401, detail="authentication_required")
    return auth.token


def _http_error(exc: Exception) -> HTTPException:
    code = getattr(exc, "code", "broker_connection_failed")
    if code in {"snaptrade_not_configured", "snaptrade_sdk_missing", "snaptrade_unavailable"}:
        status = 503
    elif code == "snaptrade_rate_limited":
        status = 429
    elif code == "snaptrade_not_found":
        status = 404
    elif code in {"broker_account_not_found"}:
        status = 404
    elif code in {
        "snaptrade_sync_in_progress",
        "snaptrade_user_already_exists",
        "snaptrade_registration_recovery_required",
    }:
        status = 409
    else:
        status = 502
    return HTTPException(status_code=status, detail=code)


def _connection_id(row: dict[str, Any]) -> str:
    return str(row.get("id") or row.get("authorizationId") or "").strip()


def _connection_disabled(row: dict[str, Any]) -> bool:
    return bool(row.get("disabled") or row.get("disabledDate") or row.get("disabled_date"))


def _nested(row: dict[str, Any], *paths: tuple[str, ...]) -> Any:
    for path in paths:
        value: Any = row
        for key in path:
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(key)
        if value not in (None, ""):
            return value
    return None


def _account_currency(account: dict[str, Any], details: dict[str, Any]) -> str:
    value = _nested(
        details,
        ("balance", "total", "currency", "code"),
        ("balance", "total", "currency"),
        ("balance", "currency", "code"),
        ("balance", "currency"),
        ("currency", "code"),
    ) or _nested(
        account,
        ("balance", "total", "currency", "code"),
        ("balance", "total", "currency"),
        ("balance", "currency", "code"),
        ("currency", "code"),
    )
    currency = str(value or "").strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise HTTPException(status_code=409, detail="broker_account_currency_missing")
    return currency


def _account_name(account: dict[str, Any]) -> str:
    # Provider display names can contain an account holder's personal name. Remove
    # the observed IBKR personalized form before it reaches persistence or an API.
    value = account.get("name") or account.get("institution_name") or "Brokerage account"
    name = str(value).strip()[:120] or "Brokerage account"
    if re.match(
        r"^(?:Interactive Brokers|IBKR)\s*\(", name, flags=re.IGNORECASE
    ):
        return "Interactive Brokers"
    return name


async def _identity_or_register(
    *, user_jwt: str, auth: AuthCtx, client: SnapTradeClient
) -> dict[str, Any]:
    identity = await broker_connections.get_my_snaptrade_identity_private(
        user_jwt, auth.user_id
    )
    if identity:
        return identity

    try:
        registered = await client.register_user(user_id=auth.user_id)
    except SnapTradeClientError as exc:
        if exc.code != "snaptrade_user_already_exists":
            raise
        # A concurrent request may have registered and stored this identity after
        # our first read. Re-read once before declaring a genuine orphan.
        raced_identity = await broker_connections.get_my_snaptrade_identity_private(
            user_jwt, auth.user_id
        )
        if raced_identity:
            logger.info("snaptrade_registration_race_recovered")
            return raced_identity
        raise
    external_user_id = registered["external_user_id"]
    try:
        public = await broker_connections.upsert_my_snaptrade_identity(
            user_jwt,
            auth.user_id,
            external_user_id=external_user_id,
            user_secret=registered["user_secret"],
        )
    except Exception as exc:
        # Do not log the exception string: a database client error can include the
        # encrypted secret payload. The class name is enough to group failures.
        logger.error(
            "snaptrade_identity_store_failed error_type=%s", type(exc).__name__
        )
        await _compensate_unstored_registration(client, external_user_id)
        raise HTTPException(
            status_code=503, detail="broker_connection_store_failed"
        ) from exc
    if not public or not public.get("id"):
        logger.error("snaptrade_identity_store_failed error_type=empty_result")
        await _compensate_unstored_registration(client, external_user_id)
        raise HTTPException(status_code=503, detail="broker_connection_store_failed")
    return {
        "id": public["id"],
        "external_user_id": external_user_id,
        "user_secret": registered["user_secret"],
    }


async def _compensate_unstored_registration(
    client: SnapTradeClient, external_user_id: str
) -> None:
    """Remove only the identity created in this request before any portal exists."""
    try:
        await client.delete_user(user_id=external_user_id)
    except SnapTradeClientError as exc:
        logger.error(
            "snaptrade_registration_compensation_failed status=%s provider_code=%s "
            "request_id=%s app_code=%s",
            exc.status if exc.status is not None else "unknown",
            exc.provider_code or "unknown",
            exc.request_id or "unknown",
            exc.code,
        )
        raise HTTPException(
            status_code=409, detail="snaptrade_registration_recovery_required"
        ) from exc
    logger.warning("snaptrade_registration_compensated")


@router.get("/broker-connections")
async def list_broker_connections(auth: AuthCtx = Depends(resolve_auth)) -> dict[str, Any]:
    user_jwt = _require_user(auth)
    return await broker_connections.list_my_brokerage_state(user_jwt)


@router.post("/broker-connections/snaptrade/session")
async def create_snaptrade_session(
    req: SnapTradeSessionRequest, auth: AuthCtx = Depends(resolve_auth)
) -> dict[str, Any]:
    user_jwt = _require_user(auth)
    broker = req.broker.strip() if req.broker else None
    if broker and not _BROKER_SLUG.fullmatch(broker):
        raise HTTPException(status_code=422, detail="invalid_broker_slug")
    try:
        client = SnapTradeClient()
        identity = await _identity_or_register(user_jwt=user_jwt, auth=auth, client=client)
        portal_url = await client.create_portal_session(
            user_id=identity["external_user_id"],
            user_secret=identity["user_secret"],
            broker=broker,
        )
        return {
            "portal_url": portal_url,
            "expires_in_seconds": 300,
            "connection": {"id": identity["id"], "provider": "snaptrade"},
        }
    except HTTPException:
        raise
    except (SnapTradeClientError, broker_connections.BrokerConnectionStateError) as exc:
        raise _http_error(exc) from exc


@router.post("/broker-connections/snaptrade/verify")
async def verify_snaptrade_connection(
    req: SnapTradeVerifyRequest, auth: AuthCtx = Depends(resolve_auth)
) -> dict[str, Any]:
    user_jwt = _require_user(auth)
    try:
        identity = await broker_connections.get_my_snaptrade_identity_private(
            user_jwt, auth.user_id
        )
        if not identity:
            raise HTTPException(status_code=409, detail="snaptrade_identity_missing")
        client = SnapTradeClient()
        credentials = {
            "user_id": identity["external_user_id"],
            "user_secret": identity["user_secret"],
        }
        connections = await client.list_connections(**credentials)
        connection = next(
            (row for row in connections if _connection_id(row) == req.external_connection_id),
            None,
        )
        if connection is None:
            raise HTTPException(status_code=409, detail="snaptrade_connection_not_verified")
        if _connection_disabled(connection):
            raise HTTPException(status_code=409, detail="snaptrade_connection_disabled")

        accounts = await client.list_connection_accounts(
            authorization_id=req.external_connection_id, **credentials
        )
        if not accounts:
            raise HTTPException(status_code=409, detail="snaptrade_accounts_not_ready")
        details = await asyncio.gather(
            *(
                client.get_account_details(
                    account_id=str(account.get("id") or ""), **credentials
                )
                for account in accounts
                if account.get("id")
            )
        )
        account_rows = []
        detail_index = 0
        for account in accounts:
            external_account_id = str(account.get("id") or "").strip()
            if not external_account_id:
                continue
            detail = details[detail_index]
            detail_index += 1
            account_rows.append(
                {
                    "external_account_id": external_account_id,
                    "masked_name": _account_name(account),
                    "base_currency": _account_currency(account, detail),
                }
            )
        if not account_rows:
            raise HTTPException(status_code=409, detail="snaptrade_accounts_not_ready")

        confirmed = await broker_connections.confirm_my_snaptrade_connection(
            user_jwt,
            auth.user_id,
            connection_id=identity["id"],
            external_connection_id=req.external_connection_id,
        )
        if not confirmed:
            raise HTTPException(status_code=500, detail="broker_connection_store_failed")
        await broker_connections.upsert_my_broker_accounts(
            user_jwt,
            auth.user_id,
            connection_id=identity["id"],
            accounts=account_rows,
        )
        return await broker_connections.list_my_brokerage_state(user_jwt)
    except HTTPException:
        raise
    except (SnapTradeClientError, broker_connections.BrokerConnectionStateError) as exc:
        raise _http_error(exc) from exc


@router.post("/broker-accounts/select")
async def select_broker_account(
    req: BrokerAccountSelectRequest, auth: AuthCtx = Depends(resolve_auth)
) -> dict[str, Any]:
    user_jwt = _require_user(auth)
    try:
        selected_id = await broker_connections.select_my_broker_account(
            user_jwt, str(req.account_id)
        )
        return {
            "selected_account_id": selected_id,
            "state": await broker_connections.list_my_brokerage_state(user_jwt),
        }
    except broker_connections.BrokerConnectionStateError as exc:
        raise _http_error(exc) from exc
