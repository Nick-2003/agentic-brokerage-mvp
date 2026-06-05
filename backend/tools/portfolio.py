"""get_portfolio tool — pulls the user's current positions and equity.

Uses Alpaca paper trading when ALPACA_API_KEY is set. Falls back to a hardcoded
mock portfolio otherwise — handy for local dev before broker keys arrive.
"""

from __future__ import annotations

import os
from typing import Any

from . import ToolDef, register

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


async def get_portfolio(args: dict[str, Any], user_id: str) -> dict[str, Any]:
    """Get the user's current portfolio holdings, cash, and unrealized P&L.

    Returns a dict with positions list. Always paper trading for MVP.
    """
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
            "Retrieve the user's current paper trading portfolio: total equity, cash, "
            "buying power, and all open positions with shares, average cost, market "
            "value, and unrealized P&L. Always paper trading."
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
