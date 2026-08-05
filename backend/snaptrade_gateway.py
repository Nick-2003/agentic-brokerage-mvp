"""Small, read-only SnapTrade SDK boundary.

Only this module knows the generated SDK's method names and response wrapper.
Routes and portfolio providers consume plain dictionaries/lists and stable errors.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlparse


logger = logging.getLogger(__name__)
_SAFE_DIAGNOSTIC = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


class SnapTradeClientError(RuntimeError):
    """Sanitised provider error; never includes credentials or raw response bodies."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int | None = None,
        operation: str | None = None,
        provider_code: str | None = None,
        request_id: str | None = None,
    ) -> None:
        self.code = code
        self.status = status
        self.operation = operation
        self.provider_code = provider_code
        self.request_id = request_id
        super().__init__(message)


def _env(name: str) -> str:
    value = os.getenv(name, "").strip()
    return "" if not value or value.endswith("REPLACE") else value


def snaptrade_configured() -> bool:
    """True when application credentials and the fixed callback URL are present."""
    return bool(
        _env("SNAPTRADE_CLIENT_ID")
        and _env("SNAPTRADE_CONSUMER_KEY")
        and _env("SNAPTRADE_REDIRECT_URL")
    )


def snaptrade_redirect_url() -> str:
    value = _env("SNAPTRADE_REDIRECT_URL")
    parsed = urlparse(value)
    if not value or parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SnapTradeClientError(
            "snaptrade_not_configured",
            "SNAPTRADE_REDIRECT_URL must be an absolute HTTP(S) URL",
        )
    return value


def _timeout_seconds() -> float:
    try:
        value = float(os.getenv("SNAPTRADE_TIMEOUT_S", "20"))
    except ValueError:
        value = 20.0
    return min(max(value, 1.0), 60.0)


def _default_sdk_factory() -> Any:
    if not snaptrade_configured():
        raise SnapTradeClientError(
            "snaptrade_not_configured", "SnapTrade application credentials are missing"
        )
    try:
        from snaptrade_client import SnapTrade, SnapTradeAuth
    except ImportError as exc:
        raise SnapTradeClientError(
            "snaptrade_sdk_missing", "SnapTrade Python SDK is not installed"
        ) from exc
    return SnapTrade(
        auth=SnapTradeAuth.commercial_api_key(
            consumer_key=_env("SNAPTRADE_CONSUMER_KEY"),
            client_id=_env("SNAPTRADE_CLIENT_ID"),
        )
    )


# Deliberately replaceable in offline tests; production uses the official SDK.
_sdk_factory: Callable[[], Any] = _default_sdk_factory


def _status_from_exception(exc: Exception) -> int | None:
    value = getattr(exc, "status", None) or getattr(exc, "status_code", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _diagnostic(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if _SAFE_DIAGNOSTIC.fullmatch(text) else None


def _response_body(exc: Exception) -> Mapping[str, Any]:
    body = getattr(exc, "body", None)
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except (TypeError, ValueError):
            return {}
    return body if isinstance(body, Mapping) else {}


def _provider_code_from_exception(exc: Exception) -> str | None:
    body = _response_body(exc)
    return _diagnostic(body.get("code") or body.get("status_code"))


def _request_id_from_exception(exc: Exception) -> str | None:
    headers = getattr(exc, "headers", None)
    if not isinstance(headers, Mapping) and not hasattr(headers, "items"):
        return None
    for key, value in headers.items():
        if str(key).lower() == "x-request-id":
            return _diagnostic(value)
    return None


def _provider_error(exc: Exception, *, operation: str) -> SnapTradeClientError:
    status = _status_from_exception(exc)
    provider_code = _provider_code_from_exception(exc)
    request_id = _request_id_from_exception(exc)
    if status == 400 and provider_code == "1010":
        code, message = (
            "snaptrade_user_already_exists",
            "SnapTrade user exists but no stored application identity was found",
        )
    elif status in {401, 403}:
        code, message = "snaptrade_auth_failed", "SnapTrade rejected the credentials"
    elif status == 404:
        code, message = "snaptrade_not_found", "SnapTrade resource was not found"
    elif status == 425:
        code, message = "snaptrade_sync_in_progress", "SnapTrade account sync is still running"
    elif status == 429:
        code, message = "snaptrade_rate_limited", "SnapTrade rate limit was reached"
    elif status is not None and status >= 500:
        code, message = "snaptrade_unavailable", "SnapTrade is temporarily unavailable"
    else:
        code, message = "snaptrade_request_failed", "SnapTrade request failed"
    return SnapTradeClientError(
        code,
        message,
        status=status,
        operation=operation,
        provider_code=provider_code,
        request_id=request_id,
    )


def _log_provider_error(error: SnapTradeClientError) -> None:
    # Deliberately exclude exception strings and response bodies: generated SDK
    # errors can contain userId/userSecret query parameters or raw provider data.
    logger.warning(
        "snaptrade_provider_error operation=%s status=%s provider_code=%s "
        "request_id=%s app_code=%s",
        error.operation or "unknown",
        error.status if error.status is not None else "unknown",
        error.provider_code or "unknown",
        error.request_id or "unknown",
        error.code,
    )


def _body(response: Any) -> Any:
    return getattr(response, "body", response)


class SnapTradeClient:
    """Async facade over the official commercial SnapTrade SDK."""

    def __init__(self, sdk: Any | None = None) -> None:
        self._sdk = sdk if sdk is not None else _sdk_factory()

    async def _call(self, operation: str, request: Callable[[], Any]) -> Any:
        try:
            # SDK 12.0.4's async non-2xx path raises before closing its temporary
            # aiohttp session. Run the generated synchronous method in a worker
            # thread instead; it uses urllib3 and does not create that session.
            response = await asyncio.wait_for(
                asyncio.to_thread(request), timeout=_timeout_seconds() + 1
            )
        except TimeoutError as exc:
            error = SnapTradeClientError(
                "snaptrade_timeout",
                "SnapTrade did not respond before the timeout",
                operation=operation,
            )
            _log_provider_error(error)
            raise error from exc
        except SnapTradeClientError:
            raise
        except Exception as exc:  # generated SDK exception types vary by release
            error = _provider_error(exc, operation=operation)
            _log_provider_error(error)
            raise error from exc
        return _body(response)

    async def register_user(self, *, user_id: str) -> dict[str, Any]:
        body = await self._call(
            "register_user",
            lambda: self._sdk.authentication.register_snap_trade_user(
                user_id=user_id
            ),
        )
        if not isinstance(body, dict):
            raise SnapTradeClientError(
                "snaptrade_contract_invalid", "SnapTrade registration returned invalid data"
            )
        external_user_id = str(body.get("userId") or body.get("user_id") or "").strip()
        user_secret = str(body.get("userSecret") or body.get("user_secret") or "").strip()
        if not external_user_id or not user_secret:
            raise SnapTradeClientError(
                "snaptrade_contract_invalid", "SnapTrade registration omitted user credentials"
            )
        return {"external_user_id": external_user_id, "user_secret": user_secret}

    async def delete_user(self, *, user_id: str) -> None:
        """Delete a just-created identity during registration compensation only."""
        await self._call(
            "delete_user",
            lambda: self._sdk.authentication.delete_snap_trade_user(
                user_id=user_id
            ),
        )

    async def create_portal_session(
        self,
        *,
        user_id: str,
        user_secret: str,
        broker: str | None = None,
    ) -> str:
        kwargs: dict[str, Any] = {
            "user_id": user_id,
            "user_secret": user_secret,
            "connection_type": "read",
            "custom_redirect": snaptrade_redirect_url(),
            "immediate_redirect": True,
            "show_close_button": True,
        }
        if broker:
            kwargs["broker"] = broker
        body = await self._call(
            "create_portal_session",
            lambda: self._sdk.authentication.login_snap_trade_user(**kwargs),
        )
        if not isinstance(body, dict):
            raise SnapTradeClientError(
                "snaptrade_contract_invalid", "SnapTrade portal response was invalid"
            )
        portal_url = str(
            body.get("redirectURI") or body.get("redirectUri") or body.get("loginLink") or ""
        ).strip()
        parsed = urlparse(portal_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise SnapTradeClientError(
                "snaptrade_contract_invalid", "SnapTrade portal URL was invalid"
            )
        return portal_url

    async def list_connections(self, *, user_id: str, user_secret: str) -> list[dict[str, Any]]:
        body = await self._call(
            "list_connections",
            lambda: self._sdk.connections.list_brokerage_authorizations(
                user_id=user_id,
                user_secret=user_secret,
            ),
        )
        return self._list_body(body, "connections")

    async def list_connection_accounts(
        self, *, authorization_id: str, user_id: str, user_secret: str
    ) -> list[dict[str, Any]]:
        body = await self._call(
            "list_connection_accounts",
            lambda: self._sdk.connections.list_brokerage_authorization_accounts(
                authorization_id=authorization_id,
                user_id=user_id,
                user_secret=user_secret,
            ),
        )
        return self._list_body(body, "accounts")

    async def get_account_details(
        self, *, account_id: str, user_id: str, user_secret: str
    ) -> dict[str, Any]:
        body = await self._call(
            "get_account_details",
            lambda: self._sdk.account_information.get_user_account_details(
                account_id=account_id,
                user_id=user_id,
                user_secret=user_secret,
            ),
        )
        if not isinstance(body, dict):
            raise SnapTradeClientError(
                "snaptrade_contract_invalid", "SnapTrade account details were invalid"
            )
        return body

    async def get_account_balances(
        self, *, account_id: str, user_id: str, user_secret: str
    ) -> list[dict[str, Any]]:
        body = await self._call(
            "get_account_balances",
            lambda: self._sdk.account_information.get_user_account_balance(
                account_id=account_id,
                user_id=user_id,
                user_secret=user_secret,
            ),
        )
        return self._list_body(body, "balances")

    async def get_account_positions(
        self, *, account_id: str, user_id: str, user_secret: str
    ) -> list[dict[str, Any]]:
        body = await self._call(
            "get_account_positions",
            lambda: self._sdk.account_information.get_all_account_positions(
                account_id=account_id,
                user_id=user_id,
                user_secret=user_secret,
            ),
        )
        return self._list_body(body, "positions")

    @staticmethod
    def _list_body(body: Any, label: str) -> list[dict[str, Any]]:
        if isinstance(body, dict):
            body = body.get("results", body.get(label))
        if not isinstance(body, list) or any(not isinstance(item, dict) for item in body):
            raise SnapTradeClientError(
                "snaptrade_contract_invalid", f"SnapTrade {label} response was invalid"
            )
        return body
