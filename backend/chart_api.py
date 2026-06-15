"""Chart-data endpoint (044) — daily OHLCV candles for the in-app chart.

`GET /api/chart-data?ticker=1398.HK&timeframe=1D` → a daily candle series for the
frontend `ta_chart` widget to render with TradingView's `lightweight-charts`
(client-side, no TradingView Desktop). This is the OTHER half of 043: 043 returns
indicator *values* the agent narrates; this returns the *series* the chart draws —
deliberately **out-of-band** (NOT through the agent loop), so a ~250-bar array
never bloats the LLM context and the `ta_chart` widget JSON stays small.

Reuses 043's shared `_fetch_ohlcv` (the same yfinance pull behind `get_technical_levels`)
so the chart and the indicator values come from the same bars. Public market data —
no auth (the W6.6 per-IP rate-limit still guards `/api/*`). Short TTL cache (yfinance
is end-of-day; mirrors the 040 in-memory cache posture, single-replica).
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

from fastapi import APIRouter, HTTPException

from tools.technicals import _ccy_symbol, _fetch_ohlcv

log = logging.getLogger(__name__)

router = APIRouter(tags=["chart"])

_MAX_BARS = int(os.getenv("CHART_DATA_MAX_BARS", "300"))
_CACHE_TTL_S = float(os.getenv("CHART_DATA_CACHE_TTL_S", "600"))  # 10 min
_cache: dict[tuple[str, str], dict[str, Any]] = {}


def _serialise_candles(df) -> list[dict[str, Any]]:  # noqa: ANN001 — pandas DataFrame
    """DataFrame tail → lightweight-charts candle dicts (time `YYYY-MM-DD` + OHLCV)."""
    out: list[dict[str, Any]] = []
    tail = df.tail(_MAX_BARS)
    for ts, row in tail.iterrows():
        try:
            close = float(row["Close"])
        except (TypeError, ValueError):
            continue  # skip a NaN/blank bar
        vol = row.get("Volume")
        out.append({
            "time": ts.strftime("%Y-%m-%d"),
            "open": round(float(row["Open"]), 4),
            "high": round(float(row["High"]), 4),
            "low": round(float(row["Low"]), 4),
            "close": round(close, 4),
            "volume": int(vol) if vol == vol and vol is not None else 0,  # vol==vol filters NaN
        })
    return out


@router.get("/api/chart-data")
async def chart_data(ticker: str = "", timeframe: str = "1D") -> dict[str, Any]:
    """Daily candle series for a ticker. Returns `{ticker, timeframe, currency,
    candles, count}`. On no/unavailable data → `candles: []` (the frontend falls
    back to the inline SVG — never a fabricated series)."""
    t = (ticker or "").strip().upper()
    if not t:
        raise HTTPException(status_code=422, detail="ticker required")
    tf = (timeframe or "1D").strip().upper()
    key = (t, tf)

    now = time.monotonic()
    ent = _cache.get(key)
    if ent and (now - ent["at"]) < _CACHE_TTL_S:
        return ent["data"]

    df, ccy = await _fetch_ohlcv(t, tf)
    if df is None or getattr(df, "empty", True) or "Close" not in getattr(df, "columns", []):
        data = {"ticker": t, "timeframe": tf, "currency": _ccy_symbol(ccy), "candles": [], "count": 0}
    else:
        candles = _serialise_candles(df)
        data = {"ticker": t, "timeframe": tf, "currency": _ccy_symbol(ccy),
                "candles": candles, "count": len(candles)}
    _cache[key] = {"at": now, "data": data}
    return data
