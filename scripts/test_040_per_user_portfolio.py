"""Offline test for 040 — per-user IBKR portfolio + nil when not connected.

Fully offline: the per-user connection lookup is monkeypatched (no Supabase), and a
*connected* user's snapshot is the mock fixture (`USE_MOCK_IBKR=1`). Like 039, this
imports the package-relative `tools.portfolio`, so run it with the 040 files applied
(temp-apply→test→restore — see the README).

    backend/.venv/bin/python scripts/test_040_per_user_portfolio.py
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
import ibkr_flex  # noqa: E402
from tools import portfolio as P  # noqa: E402

# Capture the REAL lookup before any monkeypatch, to restore for the demo-short-circuit check.
_REAL_LOOKUP = connections.get_connection_with_token_admin

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


def _patch_lookup(fn) -> None:
    connections.get_connection_with_token_admin = fn  # type: ignore[assignment]


async def test_not_connected() -> None:
    print("not connected → nil portfolio")
    _set("PORTFOLIO_SOURCE", None)  # default ibkr
    nil = P._nil_portfolio()
    check("_nil_portfolio: connected False, equity None, positions []",
          nil["connected"] is False and nil["total_equity"] is None and nil["positions"] == [])

    async def _none(uid):
        return None

    _patch_lookup(_none)
    P._ibkr_cache.clear()
    r = await P.get_portfolio({}, "user-without-ibkr")
    check("get_portfolio: connected False", r.get("connected") is False)
    check("get_portfolio: total_equity nil", r.get("total_equity") is None)
    check("get_portfolio: no positions", r.get("positions") == [])
    check("get_portfolio: source ibkr, no fabricated number", r.get("source") == "ibkr" and "error" not in r)


async def test_connected() -> None:
    print("connected → user's IBKR snapshot (mock fixture)")
    _set("USE_MOCK_IBKR", "1")

    async def _conn(uid):
        return {"flex_token": "tok-" + uid, "flex_query_id": "123", "user_id": uid}

    _patch_lookup(_conn)
    P._ibkr_cache.clear()
    r = await P.get_portfolio({}, "user-A")
    check("connected True", r.get("connected") is True)
    check("currency HK$ (base ccy)", r.get("currency") == "HK$")
    check("total_equity populated", isinstance(r.get("total_equity"), (int, float)))
    check("no error", "error" not in r)


async def test_cache_per_user() -> None:
    print("per-user cache isolation")
    _set("USE_MOCK_IBKR", "1")
    _set("IBKR_PORTFOLIO_CACHE_TTL_S", "600")
    P._CACHE_TTL_S = 600.0

    async def _conn(uid):
        return {"flex_token": "tok", "flex_query_id": "123", "user_id": uid}

    _patch_lookup(_conn)
    calls: dict[str, int] = {}
    orig = ibkr_flex.get_portfolio_snapshot

    async def _counting(token=None, query_id=None):
        calls[token] = calls.get(token, 0) + 1
        return await orig()

    ibkr_flex.get_portfolio_snapshot = _counting  # type: ignore[assignment]
    P._ibkr_cache.clear()
    try:
        await P._ibkr_snapshot_cached("A", "tokA", "123")
        await P._ibkr_snapshot_cached("A", "tokA", "123")  # cached
        await P._ibkr_snapshot_cached("B", "tokB", "123")  # separate user
        check("user A fetched once (2nd from cache)", calls.get("tokA") == 1)
        check("user B fetched once (own cache slot)", calls.get("tokB") == 1)
        check("cache has both users", set(P._ibkr_cache) == {"A", "B"})
    finally:
        ibkr_flex.get_portfolio_snapshot = orig  # type: ignore[assignment]


async def test_demo_and_failure() -> None:
    print("demo short-circuit + fetch failure → nil+error")
    # Restore the REAL lookup (earlier tests monkeypatched it) for the short-circuit check.
    connections.get_connection_with_token_admin = _REAL_LOOKUP  # type: ignore[assignment]
    # The real lookup short-circuits demo/empty without touching Supabase.
    check("get_connection_with_token_admin('demo') → None", await connections.get_connection_with_token_admin("demo") is None)
    check("get_connection_with_token_admin('') → None", await connections.get_connection_with_token_admin("") is None)

    async def _conn(uid):
        return {"flex_token": "tok", "flex_query_id": "123", "user_id": uid}

    _patch_lookup(_conn)
    orig = ibkr_flex.get_portfolio_snapshot

    async def _boom(token=None, query_id=None):
        raise ibkr_flex.IBKRFlexError("statement timeout", code="ibkr_flex_statement_timeout")

    ibkr_flex.get_portfolio_snapshot = _boom  # type: ignore[assignment]
    P._ibkr_cache.clear()
    try:
        r = await P.get_portfolio({}, "user-C")
        check("fetch failure → error surfaced", r.get("error") == "ibkr_flex_statement_timeout")
        check("fetch failure → nil values (no stale/fake)", r.get("total_equity") is None and r.get("connected") is False)
    finally:
        ibkr_flex.get_portfolio_snapshot = orig  # type: ignore[assignment]


async def main() -> int:
    await test_not_connected()
    await test_connected()
    await test_cache_per_user()
    await test_demo_and_failure()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
