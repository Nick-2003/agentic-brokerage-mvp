"""Offline regression for 039 + 040 + 085 portfolio behaviour.

The original 039 test predated per-user IBKR connections/cache isolation (040).
This version supplies a user connection explicitly, uses the current cache
signature, and verifies the additive provider provenance introduced by 085.

Run against the applied live tree:

    backend/.venv/bin/python scripts/test_039_ibkr_portfolio.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_repo_backend = next(
    (up / "backend" for up in _HERE.parents if (up / "backend" / "ibkr_flex.py").exists()),
    None,
)
if _repo_backend is not None:
    sys.path.insert(0, str(_repo_backend))

import connections  # noqa: E402
from tools import execution as E  # noqa: E402
from tools import portfolio as P  # noqa: E402

_PASS = 0
_FAIL = 0

def check(name: str, cond: bool) -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ✓ {name}")
    else:
        _FAIL += 1
        print(f"  ✗ {name}")


def _set(var: str, val: str | None) -> None:
    if val is None:
        os.environ.pop(var, None)
    else:
        os.environ[var] = val


def _reset_cache() -> None:
    """040 changed the cache from one global entry to entries keyed by user."""
    P._ibkr_cache.clear()


async def test_mapping_and_source() -> None:
    import ibkr_flex

    print("portfolio source + IBKR mapping + provider boundary")
    _set("PORTFOLIO_SOURCE", None)
    check("portfolio_source() defaults to ibkr", P.portfolio_source() == "ibkr")

    _set("USE_MOCK_IBKR", "1")
    snap = await ibkr_flex.get_portfolio_snapshot()
    mapped = P._map_ibkr_snapshot(snap)
    check(
        "base currency HKD → currency symbol HK$",
        mapped["currency"] == "HK$" and mapped["base_currency"] == "HKD",
    )
    check("total_equity = NAV total", mapped["total_equity"] == snap["nav"]["total"])
    expected_day = round(
        snap["change_in_nav"]["ending"] - snap["change_in_nav"]["starting"], 2
    )
    check("day_pnl = ending − starting (base ccy)", mapped["day_pnl"] == expected_day)
    check("day_pnl_pct computed", mapped["day_pnl_pct"] is not None)
    check(
        "read_only True, is_paper False, source ibkr",
        mapped["read_only"] is True
        and mapped["is_paper"] is False
        and mapped["source"] == "ibkr",
    )
    check("is_mock True (fixture)", mapped["is_mock"] is True)

    position = mapped["positions"][0]
    raw = snap["positions"][0]
    check(
        "position market_value = position_value_base",
        position["market_value"] == round(raw["position_value_base"], 2),
    )
    expected_upl_base = round(raw["unrealized_pnl"] * raw["fx_rate_to_base"], 2)
    check(
        "position unrealized_pnl converted to base ccy",
        position["unrealized_pnl"] == expected_upl_base,
    )
    check("position keeps native_currency label", position["native_currency"] == raw["currency"])
    check("avg_cost stays native per-share", position["avg_cost"] == raw["cost_basis_price"])

    # Since 040, a signed-in user gets a nil portfolio until their own connection
    # resolves. Stub that boundary rather than assuming global IBKR credentials.
    original_lookup = connections.get_connection_with_token_admin

    async def _connected(user_id: str):
        return {
            "user_id": user_id,
            "flex_token": f"token-{user_id}",
            "flex_query_id": "query-039",
        }

    connections.get_connection_with_token_admin = _connected  # type: ignore[assignment]
    try:
        _reset_cache()
        ibkr_portfolio = await P.get_portfolio({}, "user-039")
    finally:
        connections.get_connection_with_token_admin = original_lookup  # type: ignore[assignment]

    check(
        "get_portfolio (connected IBKR user) → currency HK$",
        ibkr_portfolio["currency"] == "HK$" and ibkr_portfolio["source"] == "ibkr",
    )
    check("get_portfolio (IBKR) → no error", "error" not in ibkr_portfolio)
    check(
        "085 stamps canonical provider provenance",
        ibkr_portfolio.get("provider") == "ibkr_flex"
        and ibkr_portfolio.get("data_source")
        == "Interactive Brokers Flex Web Service",
    )

    _set("PORTFOLIO_SOURCE", "alpaca")
    _set("USE_MOCK_BROKER", "1")
    alpaca_portfolio = await P.get_portfolio({}, "user-039")
    check(
        "get_portfolio (alpaca+mock) → MOCK_PORTFOLIO ($, NVDA)",
        alpaca_portfolio["currency"] == "$"
        and any(item["ticker"] == "NVDA" for item in alpaca_portfolio["positions"]),
    )
    _set("PORTFOLIO_SOURCE", None)
    _set("USE_MOCK_BROKER", None)


async def test_ibkr_cache() -> None:
    import ibkr_flex

    print("per-user IBKR snapshot cache")
    _set("USE_MOCK_IBKR", "1")
    _set("IBKR_PORTFOLIO_CACHE_TTL_S", "600")
    P._CACHE_TTL_S = 600.0
    _reset_cache()
    calls = {"n": 0}
    original_fetch = ibkr_flex.get_portfolio_snapshot

    async def _counting(*args, **kwargs):
        calls["n"] += 1
        return await original_fetch(*args, **kwargs)

    ibkr_flex.get_portfolio_snapshot = _counting  # type: ignore[assignment]
    try:
        await P._ibkr_snapshot_cached("cache-user", "cache-token", "cache-query")
        await P._ibkr_snapshot_cached("cache-user", "cache-token", "cache-query")
        check("same user's second fetch is cached", calls["n"] == 1)

        await P._ibkr_snapshot_cached("other-user", "other-token", "cache-query")
        check("different user receives a separate cache entry", calls["n"] == 2)
        check("cache is keyed by user", set(P._ibkr_cache) == {"cache-user", "other-user"})

        _set("IBKR_PORTFOLIO_CACHE_TTL_S", "0")
        P._CACHE_TTL_S = 0.0
        _reset_cache()
        calls["n"] = 0
        await P._ibkr_snapshot_cached("cache-user", "cache-token", "cache-query")
        await P._ibkr_snapshot_cached("cache-user", "cache-token", "cache-query")
        check("TTL=0 re-fetches each time", calls["n"] == 2)
    finally:
        ibkr_flex.get_portfolio_snapshot = original_fetch  # type: ignore[assignment]
        P._CACHE_TTL_S = 600.0
        _reset_cache()


async def test_trade_gate() -> None:
    print("trading gate (place_paper_order)")
    valid = {"ticker": "AAPL", "side": "buy", "shares": 1, "limit_price": 100.0}

    _set("TRADING_ENABLED", None)
    result = await E.place_paper_order(valid, "user-039")
    check("default → trading_unavailable", result.get("error") == "trading_unavailable")

    _set("TRADING_ENABLED", "0")
    result = await E.place_paper_order(valid, "user-039")
    check("TRADING_ENABLED=0 → trading_unavailable", result.get("error") == "trading_unavailable")

    _set("TRADING_ENABLED", "1")
    result = await E.place_paper_order(
        {"ticker": "AAPL", "side": "nope"}, "user-039"
    )
    check(
        "TRADING_ENABLED=1 → gate opens (bad_side)",
        result.get("error") == "bad_side",
    )
    check("_trading_enabled() reflects env", E._trading_enabled() is True)
    _set("TRADING_ENABLED", None)


async def main() -> int:
    await test_mapping_and_source()
    await test_ibkr_cache()
    await test_trade_gate()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
