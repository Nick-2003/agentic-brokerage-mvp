"""Technical analysis tools.

Real implementation (Phase 4-live, post key handoff):
    - Connect TradingView MCP server as a sub-client of our agent loop
    - Tools above proxy: chart_set_symbol, chart_manage_indicator, draw_shape,
      data_get_study_values, chart_scroll_to_date, capture_screenshot

Current implementation:
    - Returns deterministic mock data (SMA values, S/R levels) shaped like the
      real MCP responses so the agent can build the ta_chart widget today.
    - Screenshot URL points to a placeholder we generate via a static SVG.

When real TradingView MCP is wired up, this file shrinks to thin pass-through
wrappers. Schema for the agent stays the same.
"""

from __future__ import annotations

import math
from typing import Any

from . import ToolDef, register

# Reuse mock quote prices for consistency
from .market import MOCK_QUOTES


def _key_levels(price: float) -> dict[str, list[float]]:
    """Compute synthetic S/R levels around the current price.

    Real TradingView MCP would extract these via pine_get_lines or
    data_get_pine_lines. Deterministic for now.
    """
    return {
        "resistance": [round(price * 1.018, 2), round(price * 1.06, 2)],
        "support":    [round(price * 0.935, 2), round(price * 0.88, 2)],
    }


def _sma_series(base_price: float, periods: int = 50, slope_per_day: float = 0.6) -> list[float]:
    """Generate a synthetic SMA series of length `periods`."""
    return [round(base_price - (periods - 1 - i) * slope_per_day, 2) for i in range(periods)]


async def get_technical_levels(args: dict[str, Any], user_id: str) -> dict[str, Any]:
    """Get current price + computed SMA values + S/R levels for a ticker."""
    ticker = (args.get("ticker") or "").upper()
    indicators = args.get("indicators") or ["SMA 50", "SMA 200"]

    quote = MOCK_QUOTES.get(ticker)
    if not quote:
        return {"error": "no_coverage", "ticker": ticker}

    price = quote["price"]
    sma_values = {}
    if "SMA 50" in indicators:
        sma_values["SMA 50"] = round(price * 0.97, 2)  # Below price = bullish
    if "SMA 200" in indicators:
        sma_values["SMA 200"] = round(price * 0.91, 2)  # Below 50 = golden cross
    if "EMA 20" in indicators:
        sma_values["EMA 20"] = round(price * 0.99, 2)

    levels = _key_levels(price)
    return {
        "ticker": ticker,
        "timeframe": args.get("timeframe", "1D"),
        "current_price": price,
        "currency": "$",
        "indicators_applied": list(sma_values.keys()),
        "indicator_values": sma_values,
        "key_levels": levels,
        "trend": "bullish",  # mock
        "golden_cross_recent": True,  # mock
        "is_mock": True,
        # Placeholder screenshot URL — replaced when TradingView MCP is live
        "screenshot_url": f"/api/mock-chart/{ticker}.svg",
        "source": "tradingview_mcp_mocked",
    }


async def get_correlation_matrix(args: dict[str, Any], user_id: str) -> dict[str, Any]:
    """Compute the rolling-60-day correlation matrix for a list of tickers.

    Mock implementation returns plausible AI-cluster correlations.
    """
    tickers = args.get("tickers") or []
    if not tickers:
        return {"error": "no_tickers"}
    tickers = [t.upper() for t in tickers[:12]]

    # Hand-tuned correlations matching demo's "AI cluster" risk flag
    high_corr_cluster = {"NVDA", "AMD", "MSFT", "GOOGL", "META"}
    matrix: dict[str, dict[str, float]] = {}
    for a in tickers:
        matrix[a] = {}
        for b in tickers:
            if a == b:
                matrix[a][b] = 1.0
            elif a in high_corr_cluster and b in high_corr_cluster:
                matrix[a][b] = 0.78
            elif a in {"TSLA"} or b in {"TSLA"}:
                matrix[a][b] = 0.42
            else:
                # Hash-derived stable value in [0.3, 0.55]
                v = ((hash(a + b) % 100) / 400) + 0.3
                matrix[a][b] = round(v, 2)

    avg_corr = (
        sum(v for row in matrix.values() for k, v in row.items() if k != list(matrix.keys())[0])
        / max(1, len(tickers) * (len(tickers) - 1))
    )
    return {
        "tickers": tickers,
        "matrix": matrix,
        "average_correlation": round(avg_corr, 2),
        "window_days": 60,
        "is_mock": True,
    }


register(
    ToolDef(
        name="get_technical_levels",
        description=(
            "Get technical analysis for one ticker: current price, configured "
            "indicator values (SMA 50, SMA 200, EMA 20, etc.), key support and "
            "resistance levels, trend label, and a chart screenshot URL. Use this "
            "to build a ta_chart widget."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "timeframe": {
                    "type": "string",
                    "enum": ["1D", "4H", "1H", "1W"],
                    "default": "1D",
                },
                "indicators": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["SMA 50", "SMA 200", "EMA 20", "RSI 14", "VWAP"]},
                    "default": ["SMA 50", "SMA 200"],
                },
            },
            "required": ["ticker"],
            "additionalProperties": False,
        },
        callable=get_technical_levels,
        thought_template="Pulling {ticker} daily candles and computing indicators",
    )
)

register(
    ToolDef(
        name="get_correlation_matrix",
        description=(
            "Compute a 60-day rolling correlation matrix between a set of tickers. "
            "Returns the matrix plus the average correlation. Use this for "
            "portfolio_risk widgets when the user asks about diversification or "
            "correlated positions."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "tickers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 2,
                    "maxItems": 12,
                }
            },
            "required": ["tickers"],
            "additionalProperties": False,
        },
        callable=get_correlation_matrix,
        thought_template="Computing 60-day correlation matrix",
    )
)
