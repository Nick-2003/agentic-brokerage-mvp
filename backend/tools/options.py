"""get_option_chain tool — read-only options data for a ticker (077, data-only).

Returns an options chain (strikes, bid/ask/last, implied volatility, open interest,
volume) around the money for one expiration, from yfinance. The agent renders it as
a compact **markdown table** in a plain reply — there is no options widget yet, and
`system.md` carves an explicit exception into its "no markdown tables" rule for
exactly this case.

⚠️ NO GREEKS. yfinance's option chain provides implied volatility but **not**
delta/gamma/theta/vega. This tool returns `greeks_available: false` + a note so the
agent tells the user honestly instead of inventing them (trust #1/#3). Greeks are a
deliberate, separate later change sourced from a connected options MCP server — NOT
hand-rolled Black-Scholes.

Mock-first, same discipline as `market.py`: `_use_mock()` (USE_MOCK_OPTIONS or the
USE_MOCK_MARKET master switch or no yfinance) serves a deterministic sample chain so
offline tests + demo mode work. A real-path exception surfaces
`source: "yfinance_options_error"` — never silently mock-as-real (the proposal-003
rule). Quotes are ~15-min delayed (yfinance).
"""
from __future__ import annotations

import os
from typing import Any

from . import ToolDef, register

# Default number of strikes to return each side of at-the-money (kept small so the
# markdown table stays readable and the LLM payload stays cheap). Capped in-code.
_DEFAULT_STRIKES = 6
_MAX_STRIKES = 12

# 079 — implied-volatility sanity floor.
#
# When the market is closed the delayed feed carries NO book: every bid/ask comes
# back 0.0. yfinance's IV solver has no price to solve from, so it returns
# placeholder/bisection artifacts rather than an error. Observed live on NVDA:
#     bid=0.0 ask=0.0  IV=1.0000000000000003e-05   (solver floor sentinel)
#     bid=0.0 ask=0.0  IV=0.007822421875           (~2^-7)
#     bid=0.0 ask=0.0  IV=0.062509375              (~2^-4)
#     bid=0.0 ask=0.0  IV=0.2500075                (~2^-2)
# Passed through, those render as "0.8% / 6.3% / 25.0%" implied vol for a name
# whose real IV is ~40-60% — a 0.8% IV implies an essentially free option, which
# a trader could act on.
#
# NOTE this is precisely the blind spot in trust-principle #1: the number DID come
# from a tool, so provenance is intact and the 067 validator would pass it. Sourced
# is not the same as meaningful. So we suppress at the tool boundary: a field that
# isn't there can't be misread, whereas a garbage number carries false authority.
_IV_FLOOR = 1e-4


def _yfinance_available() -> bool:
    try:
        import yfinance  # noqa: F401
        return True
    except Exception:
        return False


def _use_mock() -> bool:
    """Mock when explicitly forced, in the deterministic demo, or yfinance is absent.
    Mirrors `market._use_mock()` with a dedicated USE_MOCK_OPTIONS override."""
    if os.getenv("USE_MOCK_OPTIONS") == "1":
        return True
    if os.getenv("USE_MOCK_MARKET", "0") == "1":
        return True
    return not _yfinance_available()


# --- mock chain ----------------------------------------------------------------
# A deterministic, hand-tuned sample so the tool is demonstrable offline. Only a
# couple of names; anything else in mock mode returns a clear "no sample" error
# (never a fabricated chain for an arbitrary ticker).
_MOCK_SPOT = {"NVDA": 942.50, "AAPL": 232.10}
_MOCK_EXPIRATIONS = ["2026-08-21", "2026-09-18", "2026-12-18"]


def _mock_row(strike: float, spot: float, is_call: bool) -> dict[str, Any]:
    itm = (strike < spot) if is_call else (strike > spot)
    intrinsic = max(0.0, (spot - strike) if is_call else (strike - spot))
    last = round(intrinsic + max(1.0, spot * 0.02), 2)
    return {
        "strike": round(strike, 2),
        "last": last,
        "bid": round(last - 0.6, 2),
        "ask": round(last + 0.6, 2),
        "volume": 1200 - int(abs(strike - spot)),
        "open_interest": 5400 - int(abs(strike - spot) * 3),
        "implied_vol_pct": round(42.0 + abs(strike - spot) / spot * 100, 1),
        "in_the_money": itm,
    }


def _mock_chain(ticker: str, option_type: str, strikes: int) -> dict[str, Any]:
    spot = _MOCK_SPOT.get(ticker)
    if spot is None:
        return {
            "error": "no_sample_options",
            "ticker": ticker,
            "message": f"No mock options chain for {ticker} (sample: {sorted(_MOCK_SPOT)}).",
            "source": "mock",
            "is_mock": True,
        }
    step = round(spot * 0.01, 2) or 1.0
    grid = [round(spot + (i - strikes) * step, 2) for i in range(strikes * 2 + 1)]
    out: dict[str, Any] = {
        "ticker": ticker,
        "expiration": _MOCK_EXPIRATIONS[0],
        "expirations": list(_MOCK_EXPIRATIONS),
        "spot": spot,
        "currency": "$",
        "greeks_available": False,
        "note": "Greeks (delta/gamma/theta/vega) aren't available from this data source yet.",
        "source": "mock",
        "is_mock": True,
        # 079 — the demo chain carries a full synthetic book, so it's always usable.
        "quote_status": "live",
        "iv_available": True,
    }
    if option_type in ("calls", "both"):
        out["calls"] = [_mock_row(k, spot, True) for k in grid]
    if option_type in ("puts", "both"):
        out["puts"] = [_mock_row(k, spot, False) for k in grid]
    return out


# --- real path -----------------------------------------------------------------
def _spot_from(t: Any, chain: Any) -> float | None:
    """Best-effort underlying last price, for centring the strike window."""
    try:
        fi = getattr(t, "fast_info", None)
        if fi:
            for k in ("last_price", "lastPrice", "regularMarketPrice"):
                v = fi.get(k) if hasattr(fi, "get") else getattr(fi, k, None)
                if v:
                    return float(v)
    except Exception:  # noqa: BLE001 — fast_info is flaky; fall through
        pass
    try:
        u = getattr(chain, "underlying", None) or {}
        v = u.get("regularMarketPrice") or u.get("regularMarketPreviousClose")
        if v:
            return float(v)
    except Exception:  # noqa: BLE001
        pass
    return None


def _rows_around(df: Any, spot: float | None, strikes: int) -> list[dict[str, Any]]:
    """DataFrame → a window of `strikes` rows each side of ATM (or the middle rows
    when spot is unknown). IV fraction → percent."""
    recs = df.to_dict("records")
    if not recs:
        return []
    recs.sort(key=lambda r: r.get("strike", 0))
    if spot is not None:
        # index of the strike nearest spot
        atm = min(range(len(recs)), key=lambda i: abs(float(recs[i].get("strike", 0)) - spot))
    else:
        atm = len(recs) // 2
    lo, hi = max(0, atm - strikes), min(len(recs), atm + strikes + 1)
    window = recs[lo:hi]

    def _num(v: Any) -> float | None:
        try:
            f = float(v)
            return f if f == f else None  # drop NaN
        except (TypeError, ValueError):
            return None

    out = []
    for r in window:
        iv = _num(r.get("impliedVolatility"))
        bid, ask = _num(r.get("bid")), _num(r.get("ask"))

        # 079 — no NBBO: the feed returned no book (both sides 0/absent). Emit
        # None rather than 0.0 so the model can't render a misleading "0.0" price.
        has_book = bool((bid or 0) > 0 or (ask or 0) > 0)
        if not has_book:
            bid = ask = None

        # 079 — IV is only meaningful if it was solved from a real price. Suppress
        # when there's no book, or when it's at/below yfinance's solver floor.
        iv_pct = None
        if iv is not None and iv > _IV_FLOOR and has_book:
            iv_pct = round(iv * 100, 1)

        # 079 — open interest degrades with the same snapshot. Observed live: OI
        # was 0 on EVERY strike across EVERY expiration while volume ran 27k-92k.
        # A contract with 92,363 volume cannot have zero open interest, and "0 OI"
        # reads as "nobody holds this" — wrong in a way a reader would act on. When
        # there's no book the whole snapshot is stale, so report OI as unavailable.
        # A genuine 0 (new strike, live book) is still passed through untouched.
        oi = _num(r.get("openInterest")) if has_book else None

        out.append({
            "strike": _num(r.get("strike")),
            "last": _num(r.get("lastPrice")),
            "bid": bid,
            "ask": ask,
            "volume": _num(r.get("volume")),   # real + intraday-populated; kept
            "open_interest": oi,
            "implied_vol_pct": iv_pct,
            "in_the_money": bool(r.get("inTheMoney")),
        })
    return out


def _quote_health(*row_lists: list[dict[str, Any]]) -> tuple[str, bool]:
    """079 — (quote_status, iv_available) across every returned row.

    `live` = at least one row carried a real bid/ask; `no_nbbo` = the feed had no
    book at all (market closed / delayed feed without NBBO), which is also why IV
    would be unsolvable.
    """
    rows = [r for lst in row_lists for r in (lst or [])]
    if not rows:
        return "no_nbbo", False
    has_book = any(r.get("bid") is not None or r.get("ask") is not None for r in rows)
    has_iv = any(r.get("implied_vol_pct") is not None for r in rows)
    return ("live" if has_book else "no_nbbo"), has_iv


_NO_NBBO_NOTE = (
    " Bid/ask, implied volatility and open interest are unavailable — the source "
    "returned no live book (the market is likely closed). Last-trade prices and "
    "volume are still real."
)


async def _fetch_chain(ticker: str, expiration: str | None, option_type: str, strikes: int) -> dict[str, Any]:
    import yfinance as yf

    t = yf.Ticker(ticker)
    expirations = list(getattr(t, "options", None) or [])
    if not expirations:
        return {"error": "no_options_for_ticker", "ticker": ticker,
                "message": f"No listed options for {ticker}.", "source": "yfinance"}
    exp = expiration if expiration in expirations else expirations[0]
    chain = t.option_chain(exp)
    spot = _spot_from(t, chain)
    out: dict[str, Any] = {
        "ticker": ticker,
        "expiration": exp,
        "expirations": expirations,
        "spot": round(spot, 2) if spot is not None else None,
        "currency": "$",
        "greeks_available": False,
        "note": "Greeks (delta/gamma/theta/vega) aren't available from this data source yet.",
        "source": "yfinance",
        "is_mock": False,
    }
    if option_type in ("calls", "both"):
        out["calls"] = _rows_around(chain.calls, spot, strikes)
    if option_type in ("puts", "both"):
        out["puts"] = _rows_around(chain.puts, spot, strikes)

    # 079 — tell the model whether these quotes are usable, so it can say
    # "unavailable, market closed" instead of printing blanks as if they were data.
    status, iv_ok = _quote_health(out.get("calls"), out.get("puts"))
    out["quote_status"] = status
    out["iv_available"] = iv_ok
    if status == "no_nbbo":
        out["note"] += _NO_NBBO_NOTE
    return out


# --- tool ----------------------------------------------------------------------
async def get_option_chain(args: dict[str, Any], user_id: str) -> dict[str, Any]:
    """Read-only options chain for a ticker around the money (data-only, no Greeks)."""
    ticker = (args.get("ticker") or "").upper().strip()
    if not ticker:
        return {"error": "no_ticker", "message": "A ticker is required."}
    option_type = (args.get("option_type") or "both").lower()
    if option_type not in ("calls", "puts", "both"):
        option_type = "both"
    try:
        strikes = int(args.get("strikes") or _DEFAULT_STRIKES)
    except (TypeError, ValueError):
        strikes = _DEFAULT_STRIKES
    strikes = max(1, min(_MAX_STRIKES, strikes))
    expiration = args.get("expiration") or None

    if _use_mock():
        return _mock_chain(ticker, option_type, strikes)
    try:
        return await _fetch_chain(ticker, expiration, option_type, strikes)
    except Exception as e:  # noqa: BLE001 — honest error, never silent mock (003 rule)
        return {
            "error": "yfinance_options_failed",
            "ticker": ticker,
            "message": str(e),
            "source": "yfinance_options_error",
        }


register(
    ToolDef(
        name="get_option_chain",
        description=(
            "Get the options chain for a ticker around the money: strikes, bid/ask/last "
            "price, implied volatility, open interest, and volume, for one expiration. "
            "Read-only, ~15-min delayed. Does NOT include Greeks (delta/gamma/theta/vega) "
            "— the result's `greeks_available` is false; say so if the user asks for them. "
            "There is no options widget: present the result as a compact markdown table."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Underlying symbol, e.g. 'NVDA'."},
                "expiration": {
                    "type": "string",
                    "description": "Expiration date YYYY-MM-DD. Omit for the nearest. "
                                   "Available dates come back in `expirations`.",
                },
                "option_type": {
                    "type": "string",
                    "enum": ["calls", "puts", "both"],
                    "description": "Which side(s) to return. Default 'both'.",
                },
                "strikes": {
                    "type": "integer",
                    "description": f"Strikes each side of ATM (default {_DEFAULT_STRIKES}, max {_MAX_STRIKES}).",
                },
            },
            "required": ["ticker"],
            "additionalProperties": False,
        },
        callable=get_option_chain,
        thought_template="Pulling the {ticker} options chain",
    )
)
