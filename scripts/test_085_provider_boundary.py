#!/usr/bin/env python3
"""
Offline contract tests for proposal 085.
Run:
    backend/.venv/bin/python scripts/test_085_provider_boundary.py
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "backend" / "broker_provider.py"
spec = importlib.util.spec_from_file_location("proposal_085_broker_provider", MODULE)
assert spec and spec.loader
broker_provider = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = broker_provider
spec.loader.exec_module(broker_provider)


async def main() -> None:
    requests = []

    async def fetch(request):
        requests.append(request)
        return {"connected": True, "source": "legacy", "total_equity": 100.0}

    registry = broker_provider.PortfolioProviderRegistry()
    provider = broker_provider.CallbackPortfolioProvider(
        name="ibkr_flex",
        data_source="Interactive Brokers Flex Web Service",
        fetch=fetch,
    )
    registry.register(provider)

    result = await registry.get_snapshot(
        "ibkr",
        broker_provider.PortfolioRequest(
            user_id="user-a", connection_id="connection-a", account_id="account-a"
        ),
    )

    assert requests[0].user_id == "user-a"
    assert requests[0].connection_id == "connection-a"
    assert requests[0].account_id == "account-a"
    assert result["provider"] == "ibkr_flex"
    assert result["data_source"] == "Interactive Brokers Flex Web Service"
    assert result["source"] == "legacy", "legacy source remains backward compatible"
    assert registry.names() == ("ibkr_flex",)

    try:
        registry.register(provider)
    except ValueError:
        pass
    else:
        raise AssertionError("duplicate registration must fail")

    try:
        registry.resolve("snaptrade")
    except broker_provider.PortfolioProviderError as exc:
        assert exc.code == "portfolio_provider_not_configured"
    else:
        raise AssertionError("unknown provider must fail explicitly")

    async def bad_fetch(_request):
        return []

    registry.register(
        broker_provider.CallbackPortfolioProvider(
            name="bad", data_source="Bad fixture", fetch=bad_fetch
        )
    )
    try:
        await registry.get_snapshot(
            "bad", broker_provider.PortfolioRequest(user_id="user-a")
        )
    except broker_provider.PortfolioProviderError as exc:
        assert exc.code == "portfolio_contract_invalid"
    else:
        raise AssertionError("non-object snapshots must fail")

    print("085 provider boundary: 12 assertions passed")


if __name__ == "__main__":
    asyncio.run(main())
