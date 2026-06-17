"""Market data tools — quotes, recent news, macro snapshot.

Real implementation: yfinance for quotes/history AND for news/macro (proposal
045 — the real news/macro engine already lived in `backend/news_context.py`,
built + live-verified for the W2 briefing; 045 routes these two chat tools to
it instead of returning hand-tuned mock). Mock fallback for offline dev.

To force mock mode set USE_MOCK_MARKET=1 (deterministic demo — quotes, news,
and macro all stay hand-tuned). USE_MOCK_NEWS=1 forces mock for news/macro
specifically while leaving quotes real.

Mock-first discipline (same as the rest of the file): the real path runs only
when yfinance is importable and mock isn't forced; the deterministic demo
(USE_MOCK_MARKET=1) is byte-identical to pre-045.
"""

from __future__ import annotations

import os
import random
from datetime import datetime, timedelta, timezone
from typing import Any

from . import ToolDef, register

# Top-level sibling module (backend/ is on sys.path; NOT a package — same import
# convention as research.py's `from fmp_client import …`). `news_context` is the
# system-side real news/macro helper (pure stdlib at import time; yfinance is
# lazy-imported inside it), so importing it here is safe and cheap.
import news_context  # noqa: E402

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


def _use_mock_news() -> bool:
    """Whether news/macro should return hand-tuned mock (proposal 045).

    Mirrors `_use_mock()` for the news/macro tools, with one extra explicit
    override so an operator can keep quotes real but pin news to mock:
      - USE_MOCK_NEWS=1                → force mock (news/macro only)
      - USE_MOCK_MARKET=1              → force mock (the deterministic demo)
      - yfinance not importable        → mock (can't fetch real news)
    Otherwise the real `news_context` (yfinance) path runs.
    """
    if os.getenv("USE_MOCK_NEWS") == "1":
        return True
    if os.getenv("USE_MOCK_MARKET", "0") == "1":
        return True
    return not _yfinance_available()


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
    """Get one or more current quotes.

    Proposal 003 — bug A: when the real (yfinance) path RAISES we surface the
    failure per-ticker as `source: "yfinance_error"` + `error: "yfinance_fetch_failed"`,
    rather than silently falling through to `_mock_quote`. The `None` return
    path (unknown ticker) still falls through to mock — that's a "ticker
    coverage" signal, not a "provider broken" signal. Same discipline as
    `alpaca_fetch_failed` in execution.py.
    """
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
                # yfinance returned None → unknown ticker. Fall through to
                # _mock_quote (synthetic) is acceptable for the demo.
            except Exception as e:
                # yfinance was expected to work but raised. Surface the
                # per-ticker failure honestly — do NOT silently fall through
                # to mock, per the "no silent fallback" rule
                # (SESSION_LOG.md 2026-05-20).
                results.append({
                    "ticker": t.upper(),
                    "error": "yfinance_fetch_failed",
                    "message": str(e),
                    "source": "yfinance_error",
                })
                continue
        m = _mock_quote(t)
        if m is not None:
            results.append({**m, "source": "mock"})
    return {"quotes": results, "is_mock": use_mock}


# ---------------------------------------------------------------------------
# Mock news — templates with offset-from-now (minutes); timestamps computed
# at call time so the `since=filled_at` filter introduced by proposal 001
# can actually match recent items.
#
# Pre-003, mock news timestamps were anchored to `_NOW = datetime.now(...)` at
# module-import time, so they were strictly before the moment the backend
# booted. Any `since=filled_at` filter (filled_at always > module-load _NOW)
# returned the empty set — news_since_fill could never populate in mock mode.
#
# Headlines and sources are preserved verbatim from the pre-003 MOCK_NEWS;
# only the timestamp encoding changed. Each ticker keeps at least one entry
# with `minutes_ago` ≤ 30 so news_since_fill can populate for flows where
# filled_at is a few minutes in the past (e.g. once execution.py back-dates
# its mock fills — see proposal 003 README's "Follow-ups").
# ---------------------------------------------------------------------------

_NEWS_TEMPLATES: dict[str, list[dict[str, Any]]] = {
    "NVDA": [
        {"headline": "Goldman raises NVDA price target to $1,200, citing data-center share gains",
         "source": "Bloomberg", "minutes_ago": 5},
        {"headline": "Blackwell production ramps faster than expected; lead times shrink to 8 weeks",
         "source": "Bloomberg", "minutes_ago": 18},
        {"headline": "NVIDIA secures multi-year deal with Saudi PIF for sovereign AI infrastructure",
         "source": "FT", "minutes_ago": 360},  # 6 hours
        {"headline": "Unusual call volume at $960 strike ahead of earnings next Wednesday",
         "source": "CNBC", "minutes_ago": 1440},  # 24 hours
    ],
    "AAPL": [
        {"headline": "Apple ships Vision Pro 2 to enterprise customers ahead of consumer launch",
         "source": "Bloomberg", "minutes_ago": 12},
        {"headline": "iPhone 17 supply chain checks point to record Q4 shipments",
         "source": "Reuters", "minutes_ago": 480},  # 8 hours
        {"headline": "Services revenue crosses $100B annual run-rate for first time",
         "source": "WSJ", "minutes_ago": 1440},
    ],
    "MSFT": [
        {"headline": "EU regulators open new probe into Microsoft–OpenAI partnership structure",
         "source": "Reuters", "minutes_ago": 22},
        {"headline": "Azure AI revenue tops $10B annual run-rate, up 280% YoY",
         "source": "WSJ", "minutes_ago": 720},  # 12 hours
        {"headline": "Copilot enterprise seats hit 50M as Office bundling kicks in",
         "source": "Bloomberg", "minutes_ago": 1440},
    ],
    "TSLA": [
        {"headline": "Robotaxi event teased for August; Musk confirms 'unsupervised FSD' demo",
         "source": "CNBC", "minutes_ago": 8},
        {"headline": "Cybertruck deliveries to surpass 30K in Q2 — analyst note",
         "source": "Reuters", "minutes_ago": 420},  # 7 hours
    ],
    "AMD": [
        {"headline": "MI325 production samples shipping to hyperscale customers",
         "source": "Bloomberg", "minutes_ago": 28},
        {"headline": "AMD takes share in server CPU market, hits 33% unit share",
         "source": "WSJ", "minutes_ago": 660},  # 11 hours
        {"headline": "Earnings preview: Q1 guide watched for AI accelerator ramp",
         "source": "CNBC", "minutes_ago": 1440},
    ],
    "GOOGL": [
        {"headline": "Waymo expands to Phoenix, ride volume up 5× YoY",
         "source": "Reuters", "minutes_ago": 24},
        {"headline": "DOJ search remedy ruling expected Q3",
         "source": "WSJ", "minutes_ago": 1440},
    ],
    "TCEHY": [
        {"headline": "Tencent games revenue re-accelerates on stronger domestic release slate",
         "source": "Bloomberg", "minutes_ago": 18},
        {"headline": "Video Accounts ad load rising — analysts lift advertising estimates",
         "source": "FT", "minutes_ago": 540},  # 9 hours
        {"headline": "Tencent extends HK$100B+ buyback; board signals continued repurchases",
         "source": "Reuters", "minutes_ago": 1440},
    ],
}


async def get_company_news(args: dict[str, Any], user_id: str) -> dict[str, Any]:
    """Get recent news for one or more tickers.

    Real path (proposal 045): per-ticker headlines from yfinance via
    `news_context.fetch_recent_news` — covers any yfinance symbol (US + HK,
    e.g. 0700.HK). Mock path: the hand-tuned `_NEWS_TEMPLATES` (deterministic
    demo / offline), selected by `_use_mock_news()`.

    Optional `since` (ISO-8601) filters to items at or after that timestamp —
    used by the filled-trade flow to surface what's happened since the fill.
    Both paths honour it; ISO-8601-UTC lexicographic compare is correct.

    Proposal 003 — bug B (mock path): timestamps are computed at CALL TIME, not
    at module-import time, so the `since=filled_at` filter can actually match
    recent items.
    """
    tickers = args.get("tickers") or []
    if isinstance(tickers, str):
        tickers = [tickers]
    limit = int(args.get("limit", 5))
    since = args.get("since")  # ISO-8601 string; lexicographic compare works for ISO-8601 UTC

    if not _use_mock_news():
        # Real path — yfinance headlines. `fetch_recent_news` is best-effort
        # per ticker (a failed symbol → empty list, never a fabricated item)
        # and returns the SAME `news_by_ticker` shape, plus a per-item `url`.
        # No silent fall-through to mock on failure: if yfinance isn't usable
        # it surfaces `error: yfinance_unavailable` (still is_mock:False), which
        # we pass through honestly (same rule as get_quote's yfinance_error).
        res = await news_context.fetch_recent_news(tickers, limit=limit, since=since)
        res.setdefault("source", "yfinance")
        return res

    out: dict[str, list[dict[str, Any]]] = {}
    now = datetime.now(timezone.utc)
    for t in tickers[:10]:
        templates = _NEWS_TEMPLATES.get(t.upper(), [])
        items = [
            {
                "headline": tpl["headline"],
                "source": tpl["source"],
                "ts": (now - timedelta(minutes=tpl["minutes_ago"])).isoformat(),
            }
            for tpl in templates
        ]
        if since:
            items = [it for it in items if it["ts"] >= since]
        # Newest first
        items = sorted(items, key=lambda it: it["ts"], reverse=True)
        out[t.upper()] = items[:limit]
    return {"news_by_ticker": out, "is_mock": True}


# ---------------------------------------------------------------------------
# Macro snapshot — futures, yields, FX, key levels.
# ---------------------------------------------------------------------------

# Map news_context's macro indicators (keyed by yfinance symbol) → this tool's
# legacy flat fields. Only the indicators news_context actually fetches are
# mappable; the rest stay absent rather than fabricated (trust principle #1).
#   ES=F → sp_futures_pct   NQ=F → nasdaq_futures_pct   ^TNX → 10Y yield level
#   ^VIX → vix level        CL=F → WTI ($)              GC=F → gold ($)
# Deliberately NOT mapped (no real free source → never invent): dow_futures_pct,
# dxy_level, btc_usd, and fed_events_today / earnings_today (the latter was the
# exact fabrication the W2 briefing fix #1 removed — an invented "FOMC 14:00").
def _macro_from_indicators(indicators: list[dict[str, Any]]) -> dict[str, Any]:
    by_sym = {i.get("symbol"): i for i in indicators if isinstance(i, dict)}

    def pct(sym: str) -> float | None:
        i = by_sym.get(sym)
        return i.get("change_pct") if i else None

    def level(sym: str) -> float | None:
        i = by_sym.get(sym)
        return i.get("price") if i else None

    flat = {
        "sp_futures_pct": pct("ES=F"),
        "nasdaq_futures_pct": pct("NQ=F"),
        "treasury_10y_yield_pct": level("^TNX"),
        "vix": level("^VIX"),
        "wti_crude_usd": level("CL=F"),
        "gold_usd": level("GC=F"),
    }
    # Drop fields we couldn't source (don't emit nulls the agent might quote).
    return {k: v for k, v in flat.items() if v is not None}


async def get_macro_snapshot(args: dict[str, Any], user_id: str) -> dict[str, Any]:
    """Current macro picture: index futures, 10Y yield, VIX, oil, gold.

    Real path (proposal 045): live values from yfinance via
    `news_context.fetch_macro_context` (ES=F/NQ=F/^VIX/^TNX/GC=F/CL=F through
    `fast_info`), mapped to the legacy flat fields PLUS the richer `indicators`
    list (each with a human `display` string) for the morning brief's
    "what it means" line. Mock path: the deterministic demo dict.

    Honest by design: the real path NEVER fabricates a Fed calendar or earnings
    list (no reliable free source — this is the hallucination W2 fix #1 removed),
    and only quotes indicators that actually fetched. If yfinance returns
    nothing, surfaces `macro_unavailable` rather than blank/fake numbers.
    """
    if not _use_mock_news():
        ctx = await news_context.fetch_macro_context()
        if ctx.get("error"):
            return {"is_mock": False, "source": "yfinance", **ctx}
        indicators = ctx.get("indicators") or []
        if not indicators:
            return {
                "is_mock": False,
                "source": "yfinance",
                "error": "macro_unavailable",
                "message": "No macro indicators fetched cleanly from yfinance.",
            }
        return {
            "is_mock": False,
            "source": "yfinance",
            "indicators": indicators,
            **_macro_from_indicators(indicators),
        }

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
            "Get the latest news headlines for one or more tickers. Pass yfinance-valid "
            "ticker symbols only — resolve any company name, sector, or nickname "
            "(e.g. 'Apple'->AAPL, 'Mag 7'->the seven symbols) to symbols before calling, "
            "and prefer the symbol the user holds for dual-listed names. Returns a list of "
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
            "Get the current macro picture: S&P/Nasdaq index futures, 10Y yield, VIX, "
            "WTI crude, gold (live via yfinance). Also returns an `indicators` list, "
            "each with a ready-to-quote `display` string. Use for morning briefs and "
            "risk context. Quote only the fields present — do not assume a Fed calendar "
            "or earnings list (not a real-data field)."
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

# register(
#     ToolDef(
#         name="get_news_by_sector",
#         description=(
#             "Get the latest news headlines for one or more sectors. Returns a list of "
#             "headlines per sector, each with source and timestamp. Use this to explain "
#             "moves or surface catalysts. Pass `since` (ISO-8601) to filter to news at "
#             "or after that time — e.g. since a trade's fill timestamp."
#         ),
#         input_schema={
#             "type": "object",
#             "properties": {
#                 "sectors": {
#                     "type": "array",
#                     "items": {"type": "string"},
#                     "minItems": 1,
#                     "maxItems": 10,
#                 },
#             },
#             "required": ["sectors"],
#             "additionalProperties": False,
#         },
#         callable=get_news_by_sector,
#         thought_template="Scanning news catalysts for {sectors}",
#     )
# )