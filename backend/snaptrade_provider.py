"""SnapTrade account data -> the app's provider-neutral portfolio contract."""
from __future__ import annotations

import asyncio
import math
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any

import broker_connections
from broker_provider import PortfolioRequest
from snaptrade_gateway import SnapTradeClient, SnapTradeClientError

ContextLoader = Callable[[str], Awaitable[dict[str, Any] | None]]
ClientFactory = Callable[[], SnapTradeClient]

_CCY_SYMBOL = {
    "USD": "$",
    "HKD": "HK$",
    "EUR": "€",
    "GBP": "£",
    "JPY": "¥",
    "CNH": "¥",
    "CNY": "¥",
}


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


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


def _currency(row: dict[str, Any]) -> str | None:
    value = _nested(
        row,
        ("currency", "code"),
        ("symbol", "currency", "code"),
        ("instrument", "currency", "code"),
        ("instrument", "symbol", "currency", "code"),
    ) or row.get("currency")
    result = str(value or "").strip().upper()
    return result if len(result) == 3 and result.isalpha() else None


def _base_total(details: dict[str, Any], base_currency: str) -> float | None:
    value = _nested(
        details,
        ("balance", "total", "amount"),
        ("balance", "total"),
        ("total_equity",),
        ("total_value",),
    )
    currency = _nested(
        details,
        ("balance", "total", "currency", "code"),
        ("balance", "total", "currency"),
        ("balance", "currency", "code"),
        ("balance", "currency"),
        ("currency", "code"),
    )
    if currency and str(currency).upper() != base_currency:
        return None
    return _number(value)


def _sum_base(balances: list[dict[str, Any]], field: str, base_currency: str) -> float | None:
    values = [
        _number(item.get(field))
        for item in balances
        if _currency(item) == base_currency
    ]
    present = [value for value in values if value is not None]
    return round(sum(present), 2) if present else None


def _ticker(position: dict[str, Any]) -> str | None:
    value = _nested(
        position,
        ("symbol", "symbol"),
        ("instrument", "symbol", "symbol"),
        ("instrument", "symbol"),
        ("ticker",),
    )
    if isinstance(value, dict):
        value = value.get("symbol")
    result = str(value or "").strip().upper()
    return result[:40] or None


def _position_row(
    position: dict[str, Any], base_currency: str, total_equity: float | None
) -> dict[str, Any] | None:
    ticker = _ticker(position)
    if not ticker:
        return None
    native_currency = _currency(position)
    shares = _number(position.get("units", position.get("quantity")))
    avg_cost = _number(
        position.get(
            "average_purchase_price",
            position.get("average_price", position.get("cost_basis")),
        )
    )
    price = _number(position.get("price"))
    native_market_value = _number(position.get("market_value"))
    if native_market_value is None and shares is not None and price is not None:
        native_market_value = shares * price
    native_pnl = _number(position.get("open_pnl", position.get("unrealized_pnl")))

    # The app contract labels these two values as base currency. Without an FX/base
    # amount from SnapTrade, never silently present foreign-currency amounts as base.
    market_value = native_market_value if native_currency == base_currency else None
    unrealized_pnl = native_pnl if native_currency == base_currency else None
    pct = (
        market_value / total_equity * 100
        if market_value is not None and total_equity not in (None, 0)
        else None
    )
    return {
        "ticker": ticker,
        "shares": shares,
        "avg_cost": avg_cost,
        "market_value": round(market_value, 2) if market_value is not None else None,
        "unrealized_pnl": round(unrealized_pnl, 2) if unrealized_pnl is not None else None,
        "native_currency": native_currency,
        "pct_of_nav": round(pct, 2) if pct is not None else None,
    }


def _as_of(details: dict[str, Any]) -> str | None:
    value = _nested(
        details,
        ("sync_status", "holdings", "last_successful_sync"),
        ("syncStatus", "holdings", "lastSuccessfulSync"),
        ("sync_status", "last_successful_sync"),
        ("syncStatus", "lastSuccessfulSync"),
        ("data_freshness", "as_of"),
        ("last_successful_sync",),
        ("updated_at",),
    )
    return str(value) if value else None


def normalise_snaptrade_portfolio(
    *,
    context: dict[str, Any],
    details: dict[str, Any],
    balances: list[dict[str, Any]],
    positions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Pure, fixture-testable normalization with conservative FX semantics."""
    base = str(context["base_currency"]).upper()
    total = _base_total(details, base)
    normalised_positions = []
    skipped = 0
    foreign_without_base_value = 0
    for raw in positions:
        item = _position_row(raw, base, total)
        if item is None:
            skipped += 1
            continue
        if item["native_currency"] not in {None, base} and item["market_value"] is None:
            foreign_without_base_value += 1
        normalised_positions.append(item)

    day_pnl = _number(
        _nested(details, ("balance", "day_pnl"), ("day_pnl",), ("daily_pnl",))
    )
    previous = total - day_pnl if total is not None and day_pnl is not None else None
    day_pnl_pct = day_pnl / previous * 100 if previous not in (None, 0) else None
    as_of = _as_of(details)
    is_paper = bool(details.get("is_paper"))
    account_kind = "paper_snaptrade" if is_paper else "real_snaptrade"
    account_label = "Paper · SnapTrade" if is_paper else "Real · SnapTrade"
    warnings = []
    if skipped:
        warnings.append(f"{skipped} position(s) omitted because no symbol was available")
    if foreign_without_base_value:
        warnings.append(
            f"{foreign_without_base_value} foreign-currency position value(s) left blank; no FX conversion was supplied"
        )

    return {
        "total_equity": round(total, 2) if total is not None else None,
        "cash": _sum_base(balances, "cash", base),
        "buying_power": _sum_base(balances, "buying_power", base),
        "day_pnl": round(day_pnl, 2) if day_pnl is not None else None,
        "day_pnl_pct": round(day_pnl_pct, 2) if day_pnl_pct is not None else None,
        "currency": _CCY_SYMBOL.get(base, f"{base} "),
        "base_currency": base,
        # Local UUID only. SnapTrade authorization/account IDs stay in the backend.
        "account_id": context["account_id"],
        "as_of": as_of,
        "freshness_note": (
            f"SnapTrade account data last synchronized {as_of}."
            if as_of
            else "SnapTrade sync time unavailable; confirm freshness before trading."
        ),
        "source": "snaptrade",
        "read_only": True,
        "connected": True,
        "is_paper": is_paper,
        "is_mock": False,
        "account_kind": account_kind,
        "account_label": account_label,
        "positions": normalised_positions,
        "normalization_warnings": warnings,
    }


def _nil_snaptrade(*, error: str | None = None, message: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "total_equity": None,
        "cash": None,
        "buying_power": None,
        "day_pnl": None,
        "day_pnl_pct": None,
        "currency": None,
        "base_currency": None,
        "account_id": None,
        "as_of": None,
        "freshness_note": None,
        "source": "snaptrade",
        "read_only": True,
        "connected": False,
        "is_paper": False,
        "is_mock": False,
        "account_kind": "none",
        "positions": [],
    }
    if error:
        payload.update(error=error, message=message or "SnapTrade portfolio is unavailable")
    return payload


def _cache_ttl() -> float:
    try:
        value = float(os.getenv("SNAPTRADE_PORTFOLIO_CACHE_TTL_S", "300"))
    except ValueError:
        value = 300.0
    return min(max(value, 0.0), 3600.0)


class SnapTradePortfolioProvider:
    name = "snaptrade"
    data_source = "SnapTrade account data"

    def __init__(
        self,
        *,
        context_loader: ContextLoader = broker_connections.get_selected_broker_context_admin,
        client_factory: ClientFactory = SnapTradeClient,
    ) -> None:
        self._context_loader = context_loader
        self._client_factory = client_factory
        self._cache: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}

    async def get_snapshot(self, request: PortfolioRequest) -> dict[str, Any]:
        try:
            context = await self._context_loader(request.user_id)
        except Exception:
            return _nil_snaptrade(
                error="broker_connection_lookup_failed",
                message="Could not resolve the selected brokerage account",
            )
        if not context or context.get("provider") != "snaptrade":
            return _nil_snaptrade()
        if request.connection_id and request.connection_id != context["connection_id"]:
            return _nil_snaptrade(error="broker_connection_mismatch")
        if request.account_id and request.account_id != context["account_id"]:
            return _nil_snaptrade(error="broker_account_mismatch")

        key = (request.user_id, context["connection_id"], context["account_id"])
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached and now - cached[0] < _cache_ttl():
            return cached[1]

        credentials = {
            "account_id": context["external_account_id"],
            "user_id": context["external_user_id"],
            "user_secret": context["user_secret"],
        }
        try:
            client = self._client_factory()
            details, balances, positions = await asyncio.gather(
                client.get_account_details(**credentials),
                client.get_account_balances(**credentials),
                client.get_account_positions(**credentials),
            )
            snapshot = normalise_snaptrade_portfolio(
                context=context,
                details=details,
                balances=balances,
                positions=positions,
            )
        except SnapTradeClientError as exc:
            return _nil_snaptrade(error=exc.code, message=str(exc))
        except Exception:
            return _nil_snaptrade(
                error="snaptrade_normalization_failed",
                message="SnapTrade returned account data the app could not normalize",
            )
        self._cache[key] = (now, snapshot)
        return snapshot


snaptrade_portfolio_provider = SnapTradePortfolioProvider()
