"""get_portfolio tool — pulls the user's current positions and equity.

Source is switchable via `PORTFOLIO_SOURCE` (039):
  • `ibkr`  (DEFAULT) — read-only holdings/NAV from the IBKR Flex Web Service.
    **Per-user (040):** resolves the *authenticated* `user_id` → their own
    `/connect` IBKR connection (decrypted Flex token, service key) and fetches
    THEIR account. **A user who hasn't connected IBKR gets a nil portfolio** (no
    fabricated demo number). Values are in the account's BASE currency
    (e.g. HKD → `HK$`). Read-only — no trading.
  • `alpaca` — the legacy Alpaca paper path (kept, reversible).

`get_portfolio` is the single source of truth for BOTH the agent's `morning_brief`
and the Hero header (`GET /api/portfolio`), so flipping the source updates both.

Mock-first: a *connected* user's snapshot fetch is mock-gated inside `ibkr_flex`
(`USE_MOCK_IBKR=1` → the bundled fixture); the Alpaca path falls back to
`MOCK_PORTFOLIO`. A real-path failure surfaces an `error` over nil values (never
silently serve mock/stale as if real) — same discipline as the rest of the codebase.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

import freshness  # 061 — shared "figures are end-of-day / generated at" note

from broker_provider import (
    CallbackPortfolioProvider,
    PortfolioRequest,
    portfolio_providers,
)

from . import ToolDef, account_label, register, tag_account  # 076 — venue labelling

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
    "account_kind": "mock",              # 076
    "account_label": "Demo data",        # 076 — model copies this into the widget chip
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

    return tag_account({  # 076 — paper_alpaca
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
    }, "paper_alpaca")


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
# 040: keyed PER USER (`{user_id: {at, snap}}`) so one user's snapshot can't be
# served to another.
_CACHE_TTL_S = float(os.getenv("IBKR_PORTFOLIO_CACHE_TTL_S", "600"))  # 10 min
_ibkr_cache: dict[str, dict[str, Any]] = {}


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
        # 061 — ready-made data-freshness line (same wording as the email/WhatsApp
        # brief, via the shared `freshness` module). Computed server-side with the
        # real current time so the agent copies it VERBATIM into the morning_brief
        # widget's `as_of_note` (it must not fabricate a generation timestamp).
        "freshness_note": freshness.freshness_note(
            snap.get("as_of"), datetime.now(timezone.utc)
        ),
        "source": "ibkr",
        "read_only": True,              # no trading via Flex
        "connected": True,              # 040: this user has an IBKR connection
        "is_paper": False,
        "is_mock": bool(snap.get("is_mock")),
        "account_kind": "real_ibkr",    # 076
        "account_label": account_label("real_ibkr"),  # 076 — "Real · IBKR"
        "positions": positions,
    }


def _nil_portfolio() -> dict[str, Any]:
    """The 'no IBKR connection yet' portfolio — all values nil, no positions, so the
    Hero shows nothing (not a fabricated demo number) and the agent says 'connect a
    brokerage'. `connected: False` is the flag the frontend + system prompt key on."""
    return {
        "total_equity": None,
        "cash": None,
        "buying_power": None,
        "day_pnl": None,
        "day_pnl_pct": None,
        "currency": None,
        "base_currency": None,
        "account_id": None,
        "as_of": None,
        "freshness_note": None,  # 061 — nil until connected
        "source": "ibkr",
        "read_only": True,
        "connected": False,
        "is_paper": False,
        "is_mock": False,
        # 076 — not connected: `none` kind, NO label (nothing to badge; the widget
        # isn't emitted anyway — the model replies "connect a brokerage").
        "account_kind": "none",
        "positions": [],
    }


async def _ibkr_snapshot_cached(user_id: str, token: str, query_id: str | None) -> dict:
    """This user's IBKR snapshot, cached per-user for `_CACHE_TTL_S` (Flex is slow +
    end-of-day). Fetched with the USER's decrypted Flex token (mock-first inside
    `ibkr_flex` when USE_MOCK_IBKR=1)."""
    now = time.monotonic()
    ent = _ibkr_cache.get(user_id)
    if ent and ent.get("snap") is not None and (now - ent["at"]) < _CACHE_TTL_S:
        return ent["snap"]
    import ibkr_flex  # top-level module (backend/ on sys.path); lazy so boot is cheap

    snap = await ibkr_flex.get_portfolio_snapshot(token, query_id)
    _ibkr_cache[user_id] = {"snap": snap, "at": now}
    return snap


async def _fetch_ibkr_portfolio(user_id: str) -> dict[str, Any]:
    """The authenticated user's OWN read-only IBKR portfolio (040, per-user).

    Resolves `user_id` → their `/connect` IBKR connection (decrypted token, service
    key). **Not connected → a nil portfolio** (no fabricated demo number). Connected
    → fetch + map their snapshot; a real-path failure surfaces an `error` (nil values,
    never a fake), so a Flex hiccup can't show stale/wrong data as if it were theirs.
    """
    try:
        import connections
        conn = await connections.get_connection_with_token_admin(user_id)
    except Exception as e:  # noqa: BLE001 — Supabase/service-key unavailable → treat as not connected
        log.info("portfolio: connection lookup unavailable for %s: %s", user_id, e)
        return _nil_portfolio()
    if not conn or not conn.get("flex_token"):
        return _nil_portfolio()  # no connection (or token won't decrypt) → nil

    try:
        snap = await _ibkr_snapshot_cached(user_id, conn["flex_token"], conn.get("flex_query_id"))
        return _map_ibkr_snapshot(snap)
    except Exception as e:  # noqa: BLE001 — IBKRFlexError or transport
        log.warning("IBKR portfolio fetch failed for %s: %s", user_id, e)
        return {
            **_nil_portfolio(),
            "error": getattr(e, "code", "ibkr_fetch_failed"),
            "message": f"Could not reach IBKR Flex: {e}",
        }

async def _fetch_ibkr_provider(request: PortfolioRequest) -> dict[str, Any]:
    """Adapt the existing Flex implementation to the provider-neutral boundary.

    Flex currently has one connection/account per user, so the optional IDs are
    intentionally ignored. SnapTrade will use them when its provider is added.
    """
    return await _fetch_ibkr_portfolio(request.user_id)


portfolio_providers.register(
    CallbackPortfolioProvider(
        name="ibkr_flex",
        data_source="Interactive Brokers Flex Web Service",
        fetch=_fetch_ibkr_provider,
    ),
    # Safe when tests reload this module in the same interpreter.
    replace=True,
)


# ---------------------------------------------------------------------------
# Sample portfolio (053) — a dedicated read-only Alpaca PAPER book for GUEST/DEMO
# users so a not-yet-connected visitor sees a realistic (clearly-labelled) demo
# instead of an empty hero. Flag-gated, default OFF; signed-in users never see it.
# ---------------------------------------------------------------------------

_sample_cache: dict[str, Any] = {"at": 0.0, "data": None}


def _sample_enabled() -> bool:
    return os.getenv("SAMPLE_PORTFOLIO_ENABLED", "0") == "1"


def _sample_creds() -> tuple[str, str] | None:
    """(key, secret) for the dedicated sample paper account, or None if unset/placeholder."""
    key = os.getenv("SAMPLE_ALPACA_API_KEY", "").strip()
    secret = os.getenv("SAMPLE_ALPACA_API_SECRET", "").strip()
    if not key or not secret or key.endswith("REPLACE"):
        return None
    return key, secret


def _sample_mock() -> dict[str, Any]:
    """The curated static demo book, tagged as a sample — used when the sample is
    enabled but no SAMPLE_ALPACA_* creds are set (so guests still see SOMETHING)."""
    # 076 — the sample book is Alpaca paper, not the demo mock; override the kind.
    return tag_account(
        {**MOCK_PORTFOLIO, "is_sample": True, "source": "alpaca_sample", "read_only": True},
        "sample",
    )


async def _fetch_sample_alpaca_portfolio(api_key: str, api_secret: str) -> dict[str, Any]:
    """Read-only positions + equity from the dedicated SAMPLE Alpaca paper account.
    Same shape as `_fetch_alpaca_portfolio`, tagged `is_sample`/read-only. TTL-cached
    (the sample book is shared across all guests, so one fetch serves everyone)."""
    now = time.monotonic()
    ent = _sample_cache
    if ent.get("data") is not None and (now - ent["at"]) < _CACHE_TTL_S:
        return ent["data"]
    # Lazy import so the backend boots even if alpaca-py isn't installed.
    from alpaca.trading.client import TradingClient

    client = TradingClient(api_key=api_key, secret_key=api_secret, paper=True)
    account = client.get_account()
    positions = client.get_all_positions()

    equity = float(account.equity)
    last_equity = float(getattr(account, "last_equity", 0) or 0)
    day_pnl = equity - last_equity
    day_pnl_pct = (day_pnl / last_equity * 100) if last_equity else 0.0

    data = {
        "total_equity": equity,
        "cash": float(account.cash),
        "buying_power": float(account.buying_power),
        "day_pnl": day_pnl,
        "day_pnl_pct": day_pnl_pct,
        "currency": "$",
        "is_paper": True,
        "is_mock": False,
        "is_sample": True,          # 053 — clearly NOT the user's own money
        "source": "alpaca_sample",
        "read_only": True,
        "account_kind": "sample",   # 076
        "account_label": account_label("sample"),  # 076 — "Sample · Alpaca paper"
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
    _sample_cache.update(at=now, data=data)
    return data


async def _sample_portfolio() -> dict[str, Any]:
    """The guest sample book: live dedicated Alpaca paper account when configured,
    else the curated static `_sample_mock()`. Never raises into the caller."""
    creds = _sample_creds()
    if not creds:
        return _sample_mock()
    try:
        return await _fetch_sample_alpaca_portfolio(*creds)
    except Exception as e:  # noqa: BLE001 — demo book; degrade to the static sample
        log.warning("sample Alpaca portfolio fetch failed: %s", e)
        return {**_sample_mock(), "is_mock_fallback": True,
                "message": f"sample account unavailable: {e}"}


# ---------------------------------------------------------------------------

async def get_portfolio(args: dict[str, Any], user_id: str) -> dict[str, Any]:
    """Get the user's current portfolio holdings, cash, and unrealized P&L.

    Source is `PORTFOLIO_SOURCE` (default `ibkr`, read-only). Returns a dict with a
    positions list; top-line figures are in the account's base currency.
    """
    # 053 — GUEST/DEMO fallback: show the read-only sample book instead of nil, so a
    # not-yet-signed-in visitor sees a realistic (labelled) portfolio. Signed-in users
    # (a real UUID) skip this and get their own IBKR account (or nil). Default OFF.
    if user_id == "demo" and _sample_enabled():
        return await _sample_portfolio()

    if portfolio_source() == "ibkr":
        return await portfolio_providers.get_snapshot(
            "ibkr_flex",
            PortfolioRequest(user_id=user_id),
        )

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
        # 076 — was "Reading your paper portfolio", which was wrong: the default
        # source is the user's REAL read-only IBKR book, not paper. Rail-neutral now.
        thought_template="Reading your portfolio",
    )
)
