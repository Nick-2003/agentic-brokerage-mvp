"""get_portfolio tool — pulls the user's current positions and equity.

Source is switchable via `PORTFOLIO_SOURCE` (039):
  • `ibkr`  (DEFAULT) — read-only holdings/NAV from the IBKR Flex Web Service
    (reuses `ibkr_flex.get_portfolio_snapshot`, the same connector behind the
    WhatsApp brief). The main page shows the IBKR account; values are in the
    account's BASE currency (e.g. HKD → `HK$`). Read-only — no trading.
  • `alpaca` — the legacy Alpaca paper path (kept, reversible).

`get_portfolio` is the single source of truth for BOTH the agent's `morning_brief`
and the Hero header (`GET /api/portfolio`), so flipping the source updates both.

Mock-first either way: the IBKR path is mock-gated inside `ibkr_flex`
(`USE_MOCK_IBKR=1` / no creds → the bundled fixture); the Alpaca path falls back to
`MOCK_PORTFOLIO`. On a real-path failure we surface an `error` (never silently
serve mock as if real) — same discipline as the rest of the codebase.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from . import ToolDef, register

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mock portfolio — used when no Alpaca key is configured.
# Roughly matches the demo's user profile (semis-heavy swing trader).
# ---------------------------------------------------------------------------

MOCK_PORTFOLIO = {
    "total_equity": 51000.00,
    "cash": 3914.50,
    "buying_power": 7829.00,
    # P5/028: day P&L for the live Hero header. Mock keeps the established demo
    # numbers ($964.10 / +1.93%) so mock-mode looks identical to the old hardcode.
    "day_pnl": 964.10,
    "day_pnl_pct": 1.93,
    "currency": "$",
    "is_paper": True,
    "is_mock": True,
    "positions": [
        {"ticker": "NVDA",  "shares": 18,  "avg_cost": 884.00, "market_value": 16965.00, "unrealized_pnl": 1053.00},
        {"ticker": "TSLA",  "shares": 65,  "avg_cost": 226.30, "market_value": 16152.50, "unrealized_pnl": 1443.00},
        {"ticker": "TCEHY", "shares": 240, "avg_cost": 54.10,  "market_value": 13968.00, "unrealized_pnl": 984.00},
    ],
}


async def _fetch_alpaca_portfolio(user_id: str) -> dict[str, Any]:
    """Fetch live positions + equity from Alpaca paper trading."""
    # Imported lazily so the backend boots even if alpaca-py isn't installed yet.
    from alpaca.trading.client import TradingClient

    api_key = os.environ["ALPACA_API_KEY"]
    api_secret = os.environ["ALPACA_API_SECRET"]
    # Always paper for MVP. Live trading is out of scope.
    client = TradingClient(api_key=api_key, secret_key=api_secret, paper=True)

    account = client.get_account()
    positions = client.get_all_positions()

    # P5/028: today's P&L for the Hero header. Alpaca's account carries
    # `last_equity` (the prior trading day's close); today's change is the
    # delta from it. Guard divide-by-zero on a brand-new/empty account.
    equity = float(account.equity)
    last_equity = float(getattr(account, "last_equity", 0) or 0)
    day_pnl = equity - last_equity
    day_pnl_pct = (day_pnl / last_equity * 100) if last_equity else 0.0

    return {
        "total_equity": equity,
        "cash": float(account.cash),
        "buying_power": float(account.buying_power),
        "day_pnl": day_pnl,
        "day_pnl_pct": day_pnl_pct,
        "currency": "$",
        "is_paper": True,
        "is_mock": False,
        "positions": [
            {
                "ticker": p.symbol,
                "shares": float(p.qty),
                "avg_cost": float(p.avg_entry_price),
                "market_value": float(p.market_value),
                "unrealized_pnl": float(p.unrealized_pl),
            }
            for p in positions
        ],
    }


# ---------------------------------------------------------------------------
# IBKR (read-only) — the default main-page source (039).
# ---------------------------------------------------------------------------

# Currency symbol for the account base ccy (so the Hero renders `HK$889,051`).
_CCY_SYMBOL = {"USD": "$", "HKD": "HK$", "EUR": "€", "GBP": "£", "JPY": "¥", "CNH": "¥", "CNY": "¥"}


def _ccy_symbol(base: str | None) -> str:
    if not base:
        return "$"
    return _CCY_SYMBOL.get(base.upper(), f"{base.upper()} ")


def portfolio_source() -> str:
    """`ibkr` (default) or `alpaca`. Flip to `alpaca` to restore the legacy path."""
    return (os.getenv("PORTFOLIO_SOURCE", "ibkr") or "ibkr").strip().lower()


# IBKR Flex is a STATEMENT service (a 2-step fetch + polling, seconds-slow) and the
# data is end-of-day, not intraday — so caching the snapshot is both a latency win
# (the Hero loads on every page open) and more correct than re-fetching. In-memory,
# single-replica (same posture as 034's token budget / W6.6's rate limiter).
_CACHE_TTL_S = float(os.getenv("IBKR_PORTFOLIO_CACHE_TTL_S", "600"))  # 10 min
_ibkr_cache: dict[str, Any] = {"at": 0.0, "snap": None}


def _money_base(value: float | None, fx: float | None) -> float | None:
    """Convert a native-ccy figure to the account base ccy (× fxRateToBase)."""
    if value is None:
        return None
    return round(value * fx, 2) if fx else round(value, 2)


def _map_ibkr_snapshot(snap: dict) -> dict[str, Any]:
    """Map an `ibkr_flex` snapshot → the portfolio dict shape the Hero + agent use.

    All TOP-LINE figures (equity, cash, day P&L) are the account BASE ccy, straight
    from NAV/ChangeInNAV. Per-position `market_value`/`unrealized_pnl` are converted
    to base ccy (× fxRateToBase) so a row reads in one currency; `avg_cost` stays the
    instrument's NATIVE per-share cost and is labelled with `native_currency`.
    """
    base = snap.get("base_currency") or "USD"
    nav = snap.get("nav") or {}
    change = snap.get("change_in_nav") or {}
    total = nav.get("total")
    prev = change.get("starting")
    if prev is None:
        prev = nav.get("prev_total")
    if change.get("ending") is not None and change.get("starting") is not None:
        day_pnl = round(change["ending"] - change["starting"], 2)
    elif total is not None and prev is not None:
        day_pnl = round(total - prev, 2)
    else:
        day_pnl = None
    day_pnl_pct = (day_pnl / prev * 100) if (day_pnl is not None and prev) else None

    positions = []
    for p in snap.get("positions") or []:
        fx = p.get("fx_rate_to_base")
        mv = p.get("position_value_base")
        if mv is None and p.get("position_value") is not None:
            mv = _money_base(p.get("position_value"), fx)
        positions.append({
            "ticker": p.get("symbol"),
            "shares": p.get("quantity"),
            "avg_cost": p.get("cost_basis_price"),            # native per-share
            "market_value": round(mv, 2) if mv is not None else None,   # base ccy
            "unrealized_pnl": _money_base(p.get("unrealized_pnl"), fx),  # base ccy
            "native_currency": p.get("currency"),
            "pct_of_nav": p.get("pct_of_nav"),
        })

    return {
        "total_equity": total,
        "cash": nav.get("cash"),
        "buying_power": None,          # Flex is read-only — no buying-power figure
        "day_pnl": day_pnl,
        "day_pnl_pct": round(day_pnl_pct, 2) if day_pnl_pct is not None else None,
        "currency": _ccy_symbol(base),  # e.g. "HK$"
        "base_currency": base,          # e.g. "HKD"
        "account_id": snap.get("account_id"),
        "as_of": snap.get("as_of"),     # statement date — Flex isn't intraday
        "source": "ibkr",
        "read_only": True,              # no trading via Flex
        "is_paper": False,
        "is_mock": bool(snap.get("is_mock")),
        "positions": positions,
    }


async def _ibkr_snapshot_cached() -> dict:
    """The IBKR snapshot, cached for `_CACHE_TTL_S` (Flex is slow + end-of-day)."""
    now = time.monotonic()
    if _ibkr_cache["snap"] is not None and (now - _ibkr_cache["at"]) < _CACHE_TTL_S:
        return _ibkr_cache["snap"]
    import ibkr_flex  # top-level module (backend/ on sys.path); lazy so boot is cheap

    snap = await ibkr_flex.get_portfolio_snapshot()  # mock-first (USE_MOCK_IBKR/no creds)
    _ibkr_cache["snap"] = snap
    _ibkr_cache["at"] = now
    return snap


async def _fetch_ibkr_portfolio(user_id: str) -> dict[str, Any]:
    """Read-only IBKR portfolio (mapped). On a real-path failure, surface the error
    (with the mock fixture as fallback values) rather than silently faking it."""
    try:
        snap = await _ibkr_snapshot_cached()
        return _map_ibkr_snapshot(snap)
    except Exception as e:  # noqa: BLE001 — IBKRFlexError or transport
        log.warning("IBKR portfolio fetch failed: %s", e)
        import ibkr_flex
        code = getattr(e, "code", "ibkr_fetch_failed")
        try:
            fallback = _map_ibkr_snapshot(ibkr_flex.parse_flex_statement(ibkr_flex._FIXTURE.read_text()))
        except Exception:  # noqa: BLE001 — fixture unreadable; still return a shaped error
            fallback = {}
        return {
            "error": code,
            "message": f"Could not reach IBKR Flex: {e}",
            "is_mock_fallback": True,
            **fallback,
            "is_mock": True,
        }


# ---------------------------------------------------------------------------

async def get_portfolio(args: dict[str, Any], user_id: str) -> dict[str, Any]:
    """Get the user's current portfolio holdings, cash, and unrealized P&L.

    Source is `PORTFOLIO_SOURCE` (default `ibkr`, read-only). Returns a dict with a
    positions list; top-line figures are in the account's base currency.
    """
    if portfolio_source() == "ibkr":
        return await _fetch_ibkr_portfolio(user_id)

    # --- legacy Alpaca path (PORTFOLIO_SOURCE=alpaca) -------------------------
    # If Alpaca keys present and look real, use the real broker. Otherwise mock.
    # USE_MOCK_BROKER=1 forces the mock portfolio even when Alpaca is configured
    # (demo mode — lets us show a curated portfolio incl. non-US names like TCEHY).
    has_alpaca = (
        os.getenv("USE_MOCK_BROKER") != "1"
        and os.getenv("ALPACA_API_KEY", "").startswith("PK")
        and os.getenv("ALPACA_API_SECRET")
        and not os.getenv("ALPACA_API_KEY", "").endswith("REPLACE")
    )
    if has_alpaca:
        try:
            return await _fetch_alpaca_portfolio(user_id)
        except Exception as e:
            # Don't fall back silently to mock — surface so we know real path is broken.
            return {
                "error": "alpaca_fetch_failed",
                "message": f"Could not reach Alpaca paper trading: {e}",
                "is_mock_fallback": True,
                **MOCK_PORTFOLIO,
            }
    return MOCK_PORTFOLIO


register(
    ToolDef(
        name="get_portfolio",
        description=(
            "Retrieve the user's current portfolio: total equity, cash, and all open "
            "positions with shares, average cost, market value, and unrealized P&L. "
            "By default this is the user's read-only IBKR account — figures are in the "
            "account's base currency (the `currency` field, e.g. HK$); `market_value` "
            "and `unrealized_pnl` are in that base currency, `avg_cost` in the position's "
            "`native_currency`."
        ),
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        callable=get_portfolio,
        thought_template="Reading your paper portfolio",
    )
)
