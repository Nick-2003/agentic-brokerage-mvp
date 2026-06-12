"""Offline test for 039 — IBKR (read-only) as the main-page portfolio source.

Fully offline (no network): the IBKR path runs mock-first via `USE_MOCK_IBKR=1`
(the bundled Flex fixture); the trade gate + source dispatch are pure env logic.

NOTE: `tools.portfolio` / `tools.execution` use package-relative imports, so this
test imports them from the repo `tools` package — run it with the 039 files applied
(the proposal's `tools/portfolio.py` + `tools/execution.py` copied in), e.g. via the
temp-apply→test→restore step in the README.

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
    P._ibkr_cache["snap"] = None
    P._ibkr_cache["at"] = 0.0


async def test_mapping_and_source() -> None:
    import ibkr_flex

    print("portfolio source + IBKR mapping")
    _set("PORTFOLIO_SOURCE", None)
    check("portfolio_source() defaults to ibkr", P.portfolio_source() == "ibkr")

    _set("USE_MOCK_IBKR", "1")
    snap = await ibkr_flex.get_portfolio_snapshot()
    m = P._map_ibkr_snapshot(snap)
    check("base currency HKD → currency symbol HK$", m["currency"] == "HK$" and m["base_currency"] == "HKD")
    check("total_equity = NAV total", m["total_equity"] == snap["nav"]["total"])
    exp_day = round(snap["change_in_nav"]["ending"] - snap["change_in_nav"]["starting"], 2)
    check("day_pnl = ending − starting (base ccy)", m["day_pnl"] == exp_day)
    check("day_pnl_pct computed", m["day_pnl_pct"] is not None)
    check("read_only True, is_paper False, source ibkr",
          m["read_only"] is True and m["is_paper"] is False and m["source"] == "ibkr")
    check("is_mock True (fixture)", m["is_mock"] is True)
    # position currency consistency: market_value in base, unrealized in base, native_currency kept
    pos = m["positions"][0]
    raw = snap["positions"][0]
    check("position market_value = position_value_base", pos["market_value"] == round(raw["position_value_base"], 2))
    exp_upl_base = round(raw["unrealized_pnl"] * raw["fx_rate_to_base"], 2)
    check("position unrealized_pnl converted to base ccy", pos["unrealized_pnl"] == exp_upl_base)
    check("position keeps native_currency label", pos["native_currency"] == raw["currency"])
    check("avg_cost stays native per-share", pos["avg_cost"] == raw["cost_basis_price"])

    # get_portfolio dispatch — IBKR (default)
    _reset_cache()
    p_ibkr = await P.get_portfolio({}, "u1")
    check("get_portfolio (ibkr) → currency HK$", p_ibkr["currency"] == "HK$" and p_ibkr["source"] == "ibkr")
    check("get_portfolio (ibkr) → no error", "error" not in p_ibkr)

    # get_portfolio dispatch — Alpaca legacy path (mock)
    _set("PORTFOLIO_SOURCE", "alpaca")
    _set("USE_MOCK_BROKER", "1")
    p_alp = await P.get_portfolio({}, "u1")
    check("get_portfolio (alpaca+mock) → MOCK_PORTFOLIO ($, NVDA)",
          p_alp["currency"] == "$" and any(x["ticker"] == "NVDA" for x in p_alp["positions"]))
    _set("PORTFOLIO_SOURCE", None)
    _set("USE_MOCK_BROKER", None)


async def test_ibkr_cache() -> None:
    import ibkr_flex

    print("IBKR snapshot cache")
    _set("USE_MOCK_IBKR", "1")
    _set("IBKR_PORTFOLIO_CACHE_TTL_S", "600")
    _reset_cache()
    calls = {"n": 0}
    orig = ibkr_flex.get_portfolio_snapshot

    async def _counting(*a, **k):
        calls["n"] += 1
        return await orig(*a, **k)

    ibkr_flex.get_portfolio_snapshot = _counting  # type: ignore[assignment]
    try:
        await P._ibkr_snapshot_cached()
        await P._ibkr_snapshot_cached()
        check("second fetch served from cache (1 underlying call)", calls["n"] == 1)
        # TTL=0 disables caching → re-fetch
        _set("IBKR_PORTFOLIO_CACHE_TTL_S", "0")
        P._CACHE_TTL_S = 0.0
        _reset_cache()
        calls["n"] = 0
        await P._ibkr_snapshot_cached()
        await P._ibkr_snapshot_cached()
        check("TTL=0 → re-fetches each time (2 calls)", calls["n"] == 2)
    finally:
        ibkr_flex.get_portfolio_snapshot = orig  # type: ignore[assignment]
        P._CACHE_TTL_S = 600.0


async def test_trade_gate() -> None:
    print("trading gate (place_paper_order)")
    valid = {"ticker": "AAPL", "side": "buy", "shares": 1, "limit_price": 100.0}

    _set("TRADING_ENABLED", None)  # default off
    r = await E.place_paper_order(valid, "u1")
    check("default → trading_unavailable", r.get("error") == "trading_unavailable")

    _set("TRADING_ENABLED", "0")
    r = await E.place_paper_order(valid, "u1")
    check("TRADING_ENABLED=0 → trading_unavailable", r.get("error") == "trading_unavailable")

    # Enabled → the gate opens; prove it by passing a BAD order so we get a
    # validation error (NOT trading_unavailable) without writing a mock order.
    _set("TRADING_ENABLED", "1")
    r = await E.place_paper_order({"ticker": "AAPL", "side": "nope"}, "u1")
    check("TRADING_ENABLED=1 → gate opens (bad_side, not trading_unavailable)",
          r.get("error") == "bad_side")
    check("_trading_enabled() reflects env", E._trading_enabled() is True)
    _set("TRADING_ENABLED", None)


async def main() -> int:
    await test_mapping_and_source()
    await test_ibkr_cache()
    await test_trade_gate()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
