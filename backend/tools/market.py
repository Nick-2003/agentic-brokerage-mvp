"""Market data tools — quotes, recent news, macro snapshot.

Real implementation: yfinance for quotes/history, Anthropic web search (or
NewsAPI) for news. Mock fallback for offline dev.

To force mock mode set USE_MOCK_MARKET=1.
"""

from __future__ import annotations

import os
import random
from datetime import datetime, timedelta, timezone
from typing import Any

from . import ToolDef, register

# ---------------------------------------------------------------------------
# Hand-tuned mock prices matching the demo HTML so flows are deterministic.
# ---------------------------------------------------------------------------

MOCK_QUOTES: dict[str, dict[str, Any]] = {
    "NVDA":  {"name": "NVIDIA Corp.",          "price": 942.50, "change": 18.30,  "change_pct": 1.98,  "after_hours": 943.33, "after_hours_pct": 0.09},
    "AAPL":  {"name": "Apple Inc.",            "price": 232.18, "change": 1.04,   "change_pct": 0.45,  "after_hours": 232.40, "after_hours_pct": 0.09},
    "MSFT":  {"name": "Microsoft Corp.",       "price": 438.21, "change": -2.14,  "change_pct": -0.49, "after_hours": 437.80, "after_hours_pct": -0.09},
    "TSLA":  {"name": "Tesla, Inc.",           "price": 248.50, "change": 6.22,   "change_pct": 2.57,  "after_hours": 249.10, "after_hours_pct": 0.24},
    "AMD":   {"name": "Advanced Micro Devices","price": 162.40, "change": 3.18,   "change_pct": 2.00,  "after_hours": 162.85, "after_hours_pct": 0.28},
    "GOOGL": {"name": "Alphabet Inc.",         "price": 178.30, "change": 1.50,   "change_pct": 0.85,  "after_hours": 178.70, "after_hours_pct": 0.22},
    "TCEHY": {"name": "Tencent Holdings (ADR)","price": 58.20,  "change": 0.96,   "change_pct": 1.68,  "after_hours": 58.32,  "after_hours_pct": 0.21},
    "META":  {"name": "Meta Platforms",        "price": 542.00, "change": -3.10,  "change_pct": -0.57, "after_hours": 541.80, "after_hours_pct": -0.04},
    "AMZN":  {"name": "Amazon.com Inc.",       "price": 218.40, "change": 2.20,   "change_pct": 1.02,  "after_hours": 218.60, "after_hours_pct": 0.09},
    "SPY":   {"name": "S&P 500 ETF",           "price": 583.20, "change": 1.85,   "change_pct": 0.32,  "after_hours": 583.50, "after_hours_pct": 0.05},
    "QQQ":   {"name": "Invesco QQQ Trust",     "price": 495.10, "change": 2.40,   "change_pct": 0.49,  "after_hours": 495.40, "after_hours_pct": 0.06},
}


def _mock_quote(ticker: str) -> dict[str, Any] | None:
    t = ticker.upper()
    if t in MOCK_QUOTES:
        return {"ticker": t, "currency": "$", **MOCK_QUOTES[t]}
    # Plausible-ish synthetic for unknown tickers (still labelled is_mock)
    base = 50 + (hash(t) % 400)
    delta = (hash(t + "d") % 1000) / 100.0 - 5
    return {
        "ticker": t,
        "name": f"{t} Inc.",
        "currency": "$",
        "price": round(base + delta, 2),
        "change": round(delta, 2),
        "change_pct": round(delta / base * 100, 2),
        "after_hours": round(base + delta + (random.random() - 0.5), 2),
        "after_hours_pct": round((random.random() - 0.5) * 0.3, 2),
    }


def _use_mock() -> bool:
    return os.getenv("USE_MOCK_MARKET", "0") == "1" or not _yfinance_available()


def _yfinance_available() -> bool:
    try:
        import yfinance  # noqa: F401
        return True
    except Exception:
        return False


async def _fetch_yfinance_quote(ticker: str) -> dict[str, Any] | None:
    """Pull a single quote from yfinance. Returns None if not found."""
    import yfinance as yf

    t = yf.Ticker(ticker)
    info = t.info or {}
    price = info.get("regularMarketPrice") or info.get("currentPrice")
    if price is None:
        return None
    prev_close = info.get("regularMarketPreviousClose") or info.get("previousClose") or price
    change = price - prev_close
    return {
        "ticker": ticker.upper(),
        "name": info.get("longName") or info.get("shortName") or ticker.upper(),
        "currency": info.get("currency", "USD").replace("USD", "$"),
        "price": round(float(price), 2),
        "change": round(float(change), 2),
        "change_pct": round(float(change) / float(prev_close) * 100, 2) if prev_close else 0.0,
        "after_hours": round(float(info.get("postMarketPrice") or price), 2),
        "after_hours_pct": round(float(info.get("postMarketChangePercent") or 0), 2),
    }


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


async def get_quote(args: dict[str, Any], user_id: str) -> dict[str, Any]:
    """Get one or more current quotes."""
    tickers = args.get("tickers") or []
    if isinstance(tickers, str):
        tickers = [tickers]
    if not tickers:
        return {"error": "no_tickers", "message": "Pass tickers as a list"}

    use_mock = _use_mock()
    results: list[dict[str, Any]] = []
    for t in tickers[:25]:  # bound
        if not use_mock:
            try:
                q = await _fetch_yfinance_quote(t)
                if q is not None:
                    results.append({**q, "source": "yfinance"})
                    continue
            except Exception:
                pass
        m = _mock_quote(t)
        if m is not None:
            results.append({**m, "source": "mock"})
    return {"quotes": results, "is_mock": use_mock}


# ---------------------------------------------------------------------------
# Mock news — hand-tuned headlines tied to demo flows.
# ---------------------------------------------------------------------------

_NOW = datetime.now(timezone.utc)
MOCK_NEWS: dict[str, list[dict[str, Any]]] = {
    "NVDA": [
        {"headline": "Blackwell production ramps faster than expected; lead times shrink to 8 weeks", "source": "Bloomberg", "ts": (_NOW - timedelta(hours=2)).isoformat()},
        {"headline": "NVIDIA secures multi-year deal with Saudi PIF for sovereign AI infrastructure", "source": "FT", "ts": (_NOW - timedelta(hours=6)).isoformat()},
        {"headline": "Unusual call volume at $960 strike ahead of earnings next Wednesday", "source": "CNBC", "ts": (_NOW - timedelta(days=1)).isoformat()},
        {"headline": "Goldman raises NVDA price target to $1,200, citing data-center share gains", "source": "Bloomberg", "ts": (_NOW - timedelta(hours=2)).isoformat()},
    ],
    "AAPL": [
        {"headline": "Apple ships Vision Pro 2 to enterprise customers ahead of consumer launch", "source": "Bloomberg", "ts": (_NOW - timedelta(hours=3)).isoformat()},
        {"headline": "iPhone 17 supply chain checks point to record Q4 shipments", "source": "Reuters", "ts": (_NOW - timedelta(hours=8)).isoformat()},
        {"headline": "Services revenue crosses $100B annual run-rate for first time", "source": "WSJ", "ts": (_NOW - timedelta(days=1)).isoformat()},
    ],
    "MSFT": [
        {"headline": "EU regulators open new probe into Microsoft–OpenAI partnership structure", "source": "Reuters", "ts": (_NOW - timedelta(hours=4)).isoformat()},
        {"headline": "Azure AI revenue tops $10B annual run-rate, up 280% YoY", "source": "WSJ", "ts": (_NOW - timedelta(hours=12)).isoformat()},
        {"headline": "Copilot enterprise seats hit 50M as Office bundling kicks in", "source": "Bloomberg", "ts": (_NOW - timedelta(days=1)).isoformat()},
    ],
    "TSLA": [
        {"headline": "Robotaxi event teased for August; Musk confirms 'unsupervised FSD' demo", "source": "CNBC", "ts": (_NOW - timedelta(hours=1)).isoformat()},
        {"headline": "Cybertruck deliveries to surpass 30K in Q2 — analyst note", "source": "Reuters", "ts": (_NOW - timedelta(hours=7)).isoformat()},
    ],
    "AMD": [
        {"headline": "MI325 production samples shipping to hyperscale customers", "source": "Bloomberg", "ts": (_NOW - timedelta(hours=5)).isoformat()},
        {"headline": "AMD takes share in server CPU market, hits 33% unit share", "source": "WSJ", "ts": (_NOW - timedelta(hours=11)).isoformat()},
        {"headline": "Earnings preview: Q1 guide watched for AI accelerator ramp", "source": "CNBC", "ts": (_NOW - timedelta(days=1)).isoformat()},
    ],
    "GOOGL": [
        {"headline": "Waymo expands to Phoenix, ride volume up 5× YoY", "source": "Reuters", "ts": (_NOW - timedelta(hours=4)).isoformat()},
        {"headline": "DOJ search remedy ruling expected Q3", "source": "WSJ", "ts": (_NOW - timedelta(days=1)).isoformat()},
    ],
    "TCEHY": [
        {"headline": "Tencent games revenue re-accelerates on stronger domestic release slate", "source": "Bloomberg", "ts": (_NOW - timedelta(hours=3)).isoformat()},
        {"headline": "Video Accounts ad load rising — analysts lift advertising estimates", "source": "FT", "ts": (_NOW - timedelta(hours=9)).isoformat()},
        {"headline": "Tencent extends HK$100B+ buyback; board signals continued repurchases", "source": "Reuters", "ts": (_NOW - timedelta(days=1)).isoformat()},
    ],
}


async def get_company_news(args: dict[str, Any], user_id: str) -> dict[str, Any]:
    """Get recent news for one or more tickers (mock for now).

    Optional `since` (ISO-8601) filters to items at or after that timestamp —
    used by the filled-trade flow to surface what's happened since the fill.
    """
    tickers = args.get("tickers") or []
    if isinstance(tickers, str):
        tickers = [tickers]
    limit = int(args.get("limit", 5))
    since = args.get("since")  # ISO-8601 string; lexicographic compare works for ISO-8601 UTC
    out: dict[str, list[dict[str, Any]]] = {}
    for t in tickers[:10]:
        items = MOCK_NEWS.get(t.upper(), [])
        if since:
            items = [it for it in items if it["ts"] >= since]
        # Newest first
        items = sorted(items, key=lambda it: it["ts"], reverse=True)
        out[t.upper()] = items[:limit]
    return {"news_by_ticker": out, "is_mock": True}


# ---------------------------------------------------------------------------
# Macro snapshot — futures, yields, FX, key levels.
# ---------------------------------------------------------------------------

async def get_macro_snapshot(args: dict[str, Any], user_id: str) -> dict[str, Any]:
    """Pre-market futures and macro reference points. Mock data for MVP."""
    return {
        "is_mock": True,
        "sp_futures_pct": 0.4,
        "nasdaq_futures_pct": 0.5,
        "dow_futures_pct": 0.2,
        "treasury_10y_yield_pct": 4.32,
        "dxy_level": 105.21,
        "vix": 14.8,
        "wti_crude_usd": 78.42,
        "gold_usd": 2412.5,
        "btc_usd": 67450.0,
        "fed_events_today": [
            {"time_et": "14:00", "event": "FOMC minutes release"}
        ],
        "earnings_today": ["AMAT (after close)", "PANW (after close)"],
    }


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

register(
    ToolDef(
        name="get_quote",
        description=(
            "Get current price, day change, and after-hours for one or more tickers. "
            "Returns a list of quotes. Use this for any numeric price reference."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "tickers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 25,
                    "description": "List of ticker symbols, e.g. ['NVDA', 'AAPL'].",
                }
            },
            "required": ["tickers"],
            "additionalProperties": False,
        },
        callable=get_quote,
        thought_template="Pulling overnight quotes for {tickers}",
    )
)

register(
    ToolDef(
        name="get_company_news",
        description=(
            "Get the latest news headlines for one or more tickers. Returns a list of "
            "headlines per ticker, each with source and timestamp. Use this to explain "
            "moves or surface catalysts. Pass `since` (ISO-8601) to filter to news at "
            "or after that time — e.g. since a trade's fill timestamp."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "tickers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 10,
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "default": 5,
                },
                "since": {
                    "type": "string",
                    "format": "date-time",
                    "description": "ISO-8601 timestamp; only return news with ts >= this value.",
                },
            },
            "required": ["tickers"],
            "additionalProperties": False,
        },
        callable=get_company_news,
        thought_template="Scanning news catalysts for {tickers}",
    )
)

register(
    ToolDef(
        name="get_macro_snapshot",
        description=(
            "Get the current macro picture: index futures, 10Y yield, DXY, VIX, oil, "
            "gold, BTC, plus today's Fed events and earnings. Use for morning briefs "
            "and risk context."
        ),
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        callable=get_macro_snapshot,
        thought_template="Reading S&P futures, 10Y yield, DXY, and Fed calendar",
    )
)
