#!/usr/bin/env python3
"""Offline portfolio-normalisation checks for proposal 090."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

TEST_FILE = Path(__file__).resolve()
IN_PROPOSAL = ".proposed_changes" in TEST_FILE.parts
ROOT = TEST_FILE.parents[3] if IN_PROPOSAL else TEST_FILE.parents[1]
if IN_PROPOSAL:
    sys.path[:0] = [
        str(ROOT / ".proposed_changes/090-snaptrade-portfolio-normalisation/backend"),
        str(ROOT / ".proposed_changes/089-minimal-snaptrade-client-routes/backend"),
        str(ROOT / "backend"),
    ]
else:
    sys.path.insert(0, str(ROOT / "backend"))
os.environ["SNAPTRADE_PORTFOLIO_CACHE_TTL_S"] = "300"

from broker_provider import PortfolioRequest  # noqa: E402
from snaptrade_provider import (  # noqa: E402
    SnapTradePortfolioProvider,
    normalise_snaptrade_portfolio,
)

CONTEXT = {
    "provider": "snaptrade",
    "connection_id": "local-connection",
    "account_id": "local-account",
    "external_user_id": "provider-user",
    "user_secret": "provider-secret",
    "external_connection_id": "provider-authorization",
    "external_account_id": "provider-account",
    "masked_name": "Individual",
    "base_currency": "USD",
}
DETAILS = {
    "balance": {
        "total": {"amount": "10000", "currency": {"code": "USD"}},
        "day_pnl": "100",
    },
    "sync_status": {"last_successful_sync": "2026-08-03T02:00:00Z"},
}
BALANCES = [
    {"currency": {"code": "USD"}, "cash": "1000", "buying_power": "2000"},
    {"currency": {"code": "HKD"}, "cash": "7800", "buying_power": "0"},
]
POSITIONS = [
    {
        "symbol": {"symbol": "AAPL", "currency": {"code": "USD"}},
        "units": "10",
        "price": "150",
        "average_purchase_price": "100",
        "open_pnl": "500",
    },
    {
        "instrument": {
            "symbol": {"symbol": "0700", "currency": {"code": "HKD"}},
            "kind": "EQUITY",
        },
        "units": "100",
        "price": "400",
        "open_pnl": "1000",
    },
    {"units": 1, "price": 20},
]


def test_normalisation() -> None:
    result = normalise_snaptrade_portfolio(
        context=CONTEXT, details=DETAILS, balances=BALANCES, positions=POSITIONS
    )
    assert result["total_equity"] == 10000
    assert result["cash"] == 1000  # HKD cash is not silently summed into USD
    assert result["buying_power"] == 2000
    assert result["day_pnl"] == 100
    assert result["day_pnl_pct"] == 1.01
    assert result["currency"] == "$"
    assert result["account_id"] == "local-account"
    assert result["account_kind"] == "real_snaptrade"
    assert result["read_only"] is True and result["is_mock"] is False
    assert result["positions"][0] == {
        "ticker": "AAPL",
        "shares": 10.0,
        "avg_cost": 100.0,
        "market_value": 1500.0,
        "unrealized_pnl": 500.0,
        "native_currency": "USD",
        "pct_of_nav": 15.0,
    }
    hk = result["positions"][1]
    assert hk["ticker"] == "0700"
    assert hk["native_currency"] == "HKD"
    assert hk["market_value"] is None and hk["unrealized_pnl"] is None
    assert len(result["normalization_warnings"]) == 2
    rendered = repr(result)
    assert "provider-secret" not in rendered
    assert "provider-account" not in rendered
    assert "provider-authorization" not in rendered


class FakeClient:
    calls = 0

    async def get_account_details(self, **kwargs):
        self.__class__.calls += 1
        assert kwargs["account_id"] == "provider-account"
        assert kwargs["user_secret"] == "provider-secret"
        return DETAILS

    async def get_account_balances(self, **kwargs):
        return BALANCES

    async def get_account_positions(self, **kwargs):
        return POSITIONS


async def test_provider() -> None:
    async def context_loader(user_id):
        assert user_id == "trusted-user"
        return CONTEXT

    provider = SnapTradePortfolioProvider(
        context_loader=context_loader, client_factory=FakeClient
    )
    first = await provider.get_snapshot(PortfolioRequest(user_id="trusted-user"))
    second = await provider.get_snapshot(PortfolioRequest(user_id="trusted-user"))
    assert first == second
    assert FakeClient.calls == 1  # per-user/account cache
    assert first["connected"] is True

    mismatch = await provider.get_snapshot(
        PortfolioRequest(user_id="trusted-user", account_id="different-local-account")
    )
    assert mismatch["connected"] is False
    assert mismatch["error"] == "broker_account_mismatch"

    async def no_context(user_id):
        return None

    nil_provider = SnapTradePortfolioProvider(context_loader=no_context, client_factory=FakeClient)
    nil = await nil_provider.get_snapshot(PortfolioRequest(user_id="trusted-user"))
    assert nil["connected"] is False
    assert nil["positions"] == []
    assert nil["is_mock"] is False


async def main() -> None:
    test_normalisation()
    await test_provider()
    print("090 snaptrade portfolio normalisation: PASS")


if __name__ == "__main__":
    asyncio.run(main())
