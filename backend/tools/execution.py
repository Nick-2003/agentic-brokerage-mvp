"""Trade execution — Alpaca paper trading with mock fallback.

Mock mode writes orders to backend/data/mock_orders.json so subsequent calls
(get_open_position, refresh_live_trade) can read back filled state.

When ALPACA_API_KEY is present (starts with PK, not REPLACE), switch to real
Alpaca paper API.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import ToolDef, register
from .market import MOCK_QUOTES

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)
MOCK_ORDERS_FILE = DATA_DIR / "mock_orders.json"

# Cap from env (Threat 6 mitigation in SECURITY.md)
MAX_NOTIONAL = float(os.getenv("MAX_PAPER_TRADE_NOTIONAL", "50000"))


def _trading_enabled() -> bool:
    """Whether order placement is allowed (039). Defaults to **off**: the main page
    now shows a read-only IBKR portfolio, and IBKR Flex can't trade — so executing an
    Alpaca order would be incoherent (trading one account while displaying another).
    Set `TRADING_ENABLED=1` to re-enable the Alpaca paper-order path (e.g. when
    `PORTFOLIO_SOURCE=alpaca`)."""
    return os.getenv("TRADING_ENABLED", "0") == "1"


# ---------------------------------------------------------------------------
# Mock order book — persisted to disk so it survives backend restarts.
# ---------------------------------------------------------------------------


def _load_mock_orders() -> dict[str, dict[str, Any]]:
    if not MOCK_ORDERS_FILE.exists():
        return {}
    try:
        return json.loads(MOCK_ORDERS_FILE.read_text())
    except Exception:
        return {}


def _save_mock_orders(orders: dict[str, dict[str, Any]]) -> None:
    MOCK_ORDERS_FILE.write_text(json.dumps(orders, indent=2))


def _alpaca_enabled() -> bool:
    # USE_MOCK_BROKER=1 forces the mock execution path (demo mode) even when
    # Alpaca keys are configured. Mock orders fill instantly and persist locally.
    if os.getenv("USE_MOCK_BROKER") == "1":
        return False
    return (
        os.getenv("ALPACA_API_KEY", "").startswith("PK")
        and not os.getenv("ALPACA_API_KEY", "").endswith("REPLACE")
        and bool(os.getenv("ALPACA_API_SECRET"))
    )


def _mock_fill_backdate_minutes() -> int:
    """Minutes to back-date a *mock* fill's `filled_at` (Proposal 005).

    Mock fills are synchronous — without this, `filled_at == now`, so the
    `news_since_fill` window `[filled_at, now]` is empty and the "Since you
    bought" section can never render in the immediate post-fill turn.
    Back-dating makes the mock represent "placed N minutes ago", giving recent
    mock news a window to fall into.

    Default 30. Must exceed the largest "recent" `minutes_ago` in
    `market._NEWS_TEMPLATES` (currently 28, for AMD) so the recent-tier mock
    news qualifies; the recent→old gap (28 → 360 min) is a wide safe band, so
    a value anywhere in [28, 360) captures exactly the recent tier. Real Alpaca
    fills are unaffected — this only touches the mock branch.
    """
    try:
        return int(os.getenv("MOCK_FILL_BACKDATE_MINUTES", "30"))
    except ValueError:
        return 30


# ---------------------------------------------------------------------------
# Real Alpaca path
# ---------------------------------------------------------------------------


async def _place_alpaca_order(args: dict[str, Any], user_id: str) -> dict[str, Any]:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
    from alpaca.trading.requests import LimitOrderRequest, TakeProfitRequest, StopLossRequest

    client = TradingClient(
        api_key=os.environ["ALPACA_API_KEY"],
        secret_key=os.environ["ALPACA_API_SECRET"],
        paper=True,
    )

    side = OrderSide.BUY if args["side"] == "buy" else OrderSide.SELL
    req_kwargs: dict[str, Any] = {
        "symbol": args["ticker"],
        "qty": args["shares"],
        "side": side,
        "type": "limit",
        "limit_price": args["limit_price"],
        "time_in_force": TimeInForce.DAY,
    }
    # OCO bracket only when both TP and SL given
    if args.get("tp_price") and args.get("sl_price"):
        req_kwargs["order_class"] = OrderClass.BRACKET
        req_kwargs["take_profit"] = TakeProfitRequest(limit_price=args["tp_price"])
        req_kwargs["stop_loss"] = StopLossRequest(stop_price=args["sl_price"])

    order = client.submit_order(LimitOrderRequest(**req_kwargs))
    return {
        "order_id": str(order.id),
        "status": order.status.value if hasattr(order.status, "value") else str(order.status),
        "ticker": args["ticker"],
        "side": args["side"],
        "shares": args["shares"],
        "limit_price": args["limit_price"],
        "tp_price": args.get("tp_price"),
        "sl_price": args.get("sl_price"),
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "is_paper": True,
        "is_mock": False,
    }


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


async def place_paper_order(args: dict[str, Any], user_id: str) -> dict[str, Any]:
    """Submit a paper-trading order with optional OCO bracket (TP + SL).

    Always paper. Notional capped at MAX_PAPER_TRADE_NOTIONAL.
    """
    # 039 — order placement is OFF by default (read-only IBKR main page; Flex can't
    # trade). Return a clear, non-fabricated "unavailable" result; the agent relays
    # it as plain text (see system.md "Trading is currently unavailable").
    if not _trading_enabled():
        return {
            "error": "trading_unavailable",
            "message": (
                "Order placement isn't available right now — your portfolio is "
                "connected read-only (IBKR). Trading is coming later."
            ),
        }

    # Validation — server-side cap (Threat 6 mitigation)
    ticker = (args.get("ticker") or "").upper()
    side = args.get("side")
    shares = args.get("shares")
    limit_price = args.get("limit_price")
    notional = args.get("notional")
    tp_price = args.get("tp_price")
    sl_price = args.get("sl_price")

    if side not in {"buy", "sell"}:
        return {"error": "bad_side", "message": "side must be buy or sell"}
    if not ticker:
        return {"error": "no_ticker"}
    # Derive whichever is missing
    if not shares and notional and limit_price:
        shares = int(notional // limit_price)
    if not notional and shares and limit_price:
        notional = shares * limit_price
    if not shares or not limit_price:
        return {"error": "missing_fields", "message": "Need shares + limit_price OR notional + limit_price"}
    if notional and notional > MAX_NOTIONAL:
        return {"error": "notional_capped", "message": f"Notional ${notional:,.0f} exceeds cap of ${MAX_NOTIONAL:,.0f}"}

    args_norm = {
        "ticker": ticker, "side": side, "shares": shares, "limit_price": limit_price,
        "tp_price": tp_price, "sl_price": sl_price, "notional": notional,
    }

    if _alpaca_enabled():
        try:
            return await _place_alpaca_order(args_norm, user_id)
        except Exception as e:
            return {"error": "alpaca_submit_failed", "message": str(e)}

    # Mock path — write to local JSON, simulate fill at limit price.
    # Proposal 005: back-date `filled_at` (and `submitted_at`, to keep
    # submitted ≤ filled) so the `news_since_fill` window `[filled_at, now]`
    # is non-empty. Otherwise mock fills are synchronous (filled_at == now)
    # and the "Since you bought" section can never render in the immediate
    # post-fill turn. Real Alpaca fills are unaffected — this is the mock branch.
    order_id = f"mock_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)
    fill_time = (now - timedelta(minutes=_mock_fill_backdate_minutes())).isoformat()
    order = {
        "order_id": order_id,
        "user_id": user_id,
        "ticker": ticker,
        "side": side,
        "shares": shares,
        "limit_price": limit_price,
        "fill_price": limit_price,  # mock: fills at limit
        "tp_price": tp_price,
        "sl_price": sl_price,
        "notional": notional,
        "status": "filled",
        "submitted_at": fill_time,
        "filled_at": fill_time,
        "is_paper": True,
        "is_mock": True,
    }
    orders = _load_mock_orders()
    orders[order_id] = order
    _save_mock_orders(orders)
    return order


async def get_open_position(args: dict[str, Any], user_id: str) -> dict[str, Any]:
    """Get the live state of one open position (real or mock).

    Returns current price, unrealised P&L, TP/SL armed status. Used by the
    trade monitor and live_trade widgets.
    """
    ticker = (args.get("ticker") or "").upper()

    if _alpaca_enabled():
        try:
            from alpaca.trading.client import TradingClient
            client = TradingClient(
                api_key=os.environ["ALPACA_API_KEY"],
                secret_key=os.environ["ALPACA_API_SECRET"],
                paper=True,
            )
            p = client.get_open_position(ticker)
            return {
                "ticker": ticker,
                "shares": float(p.qty),
                "side": "long" if float(p.qty) > 0 else "short",
                "fill_price": float(p.avg_entry_price),
                "current_price": float(p.current_price),
                "unrealized_pnl": float(p.unrealized_pl),
                "unrealized_pnl_pct": float(p.unrealized_plpc) * 100,
                "is_mock": False,
            }
        except Exception as e:
            return {"error": "alpaca_position_fetch_failed", "message": str(e), "ticker": ticker}

    # Mock path
    orders = _load_mock_orders()
    user_orders = [o for o in orders.values() if o["ticker"] == ticker and o["user_id"] == user_id]
    if not user_orders:
        return {"error": "no_position", "ticker": ticker}
    latest = user_orders[-1]
    quote = MOCK_QUOTES.get(ticker)
    current = quote["price"] if quote else latest["limit_price"]
    fill = latest["fill_price"]
    shares = latest["shares"]
    pnl = (current - fill) * shares * (1 if latest["side"] == "buy" else -1)
    pnl_pct = (current - fill) / fill * 100 * (1 if latest["side"] == "buy" else -1)
    return {
        "ticker": ticker,
        "order_id": latest["order_id"],
        "side": "long" if latest["side"] == "buy" else "short",
        "shares": shares,
        "fill_price": fill,
        "current_price": current,
        "unrealized_pnl": round(pnl, 2),
        "unrealized_pnl_pct": round(pnl_pct, 2),
        "tp_armed_at": latest.get("tp_price"),
        "sl_armed_at": latest.get("sl_price"),
        "filled_at": latest["filled_at"],
        "is_mock": True,
    }


async def list_open_positions(args: dict[str, Any], user_id: str) -> dict[str, Any]:
    """List all open positions for the user."""
    if _alpaca_enabled():
        try:
            from alpaca.trading.client import TradingClient
            client = TradingClient(
                api_key=os.environ["ALPACA_API_KEY"],
                secret_key=os.environ["ALPACA_API_SECRET"],
                paper=True,
            )
            positions = client.get_all_positions()
            return {
                "positions": [
                    {
                        "ticker": p.symbol,
                        "shares": float(p.qty),
                        "fill_price": float(p.avg_entry_price),
                        "current_price": float(p.current_price),
                        "unrealized_pnl": float(p.unrealized_pl),
                    }
                    for p in positions
                ],
                "is_mock": False,
            }
        except Exception as e:
            return {"error": "alpaca_list_failed", "message": str(e)}

    orders = _load_mock_orders()
    rows: list[dict[str, Any]] = []
    for o in orders.values():
        if o["user_id"] != user_id or o["status"] != "filled":
            continue
        quote = MOCK_QUOTES.get(o["ticker"])
        current = quote["price"] if quote else o["fill_price"]
        rows.append({
            "ticker": o["ticker"],
            "shares": o["shares"],
            "fill_price": o["fill_price"],
            "current_price": current,
            "unrealized_pnl": round((current - o["fill_price"]) * o["shares"], 2),
        })
    return {"positions": rows, "is_mock": True}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

register(
    ToolDef(
        name="place_paper_order",
        description=(
            "Submit a paper-trading order for the user. Always paper, always limit, "
            "always with OCO bracket if TP and SL are provided. Use this when the "
            "user explicitly confirms a trade. The agent should NEVER call this "
            "without showing an order_ticket widget first and waiting for user "
            "confirmation."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "side": {"type": "string", "enum": ["buy", "sell"]},
                "shares": {"type": "number", "minimum": 1},
                "notional": {"type": "number", "minimum": 1},
                "limit_price": {"type": "number", "minimum": 0.01},
                "tp_price": {"type": "number", "minimum": 0.01},
                "sl_price": {"type": "number", "minimum": 0.01},
            },
            "required": ["ticker", "side", "limit_price"],
            "additionalProperties": False,
        },
        callable=place_paper_order,
        thought_template="Submitting paper order: {side} {shares} {ticker} @ {limit_price}",
    )
)

register(
    ToolDef(
        name="get_open_position",
        description=(
            "Get live state of one position: current price, unrealised P&L, TP/SL "
            "armed levels. Use for live_trade widgets and trade-monitor overlays."
        ),
        input_schema={
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
            "additionalProperties": False,
        },
        callable=get_open_position,
        thought_template="Fetching live state of {ticker} position",
    )
)

register(
    ToolDef(
        name="list_open_positions",
        description=(
            "List all open positions for the user with current P&L. Use this for "
            "the live-trades dashboard section."
        ),
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        callable=list_open_positions,
        thought_template="Listing your open positions",
    )
)
