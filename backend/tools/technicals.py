"""Technical analysis tools.

Two paths per tool, gated by USE_MOCK_TA:

  USE_MOCK_TA=1  → deterministic mock data (preserved, matches the demo HTML).
  USE_MOCK_TA=0  → real TradingView MCP via mcp_client. Requires
                   TradingView Desktop running locally with CDP enabled and
                   the tradesdontlie/tradingview-mcp Node server installed.

Mock-first preserved per CLAUDE.md / SCOPE.md discipline — never delete the
mock path. Real path is the wedge; mock keeps the deterministic demo working
when TV Desktop isn't around (and is the Railway production default until
containerised TV ships, see .proposed_changes/applied/002-tradingview-mcp/README.md §3).

New tools added for the "talk to your chart" wedge:
    - chart_apply_indicator     "add RSI to NVDA"
    - chart_draw_levels         "draw support at 220 and resistance at 250"
    - chart_scroll_to_date      "scroll to March 2024"
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
from pathlib import Path
from typing import Any

from . import ToolDef, register

# Reuse mock quote prices for consistency
from .market import MOCK_QUOTES


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Indicator name translation
#
# Our schema uses short names ("SMA 50", "RSI 14"). TradingView MCP's
# `chart_manage_indicator` requires the full study names verbatim. Single
# source of truth — if TradingView ever renames an indicator, this is the
# only constant that changes.
# ---------------------------------------------------------------------------

_INDICATOR_MAP: dict[str, tuple[str, dict[str, Any]]] = {
    "SMA 50":  ("Moving Average Simple",       {"length": 50}),
    "SMA 200": ("Moving Average Simple",       {"length": 200}),
    "EMA 20":  ("Moving Average Exponential",  {"length": 20}),
    "RSI 14":  ("Relative Strength Index",     {"length": 14}),
    "VWAP":    ("VWAP",                        {}),
}

# 054 — indicators are now period-parameterised ("SMA 20", "EMA 50", "RSI 9",
# "BB 20") so the agent can request ANY length and the chart computes + draws it.
# Families we chart from candles client-side. VWAP is intentionally NOT here — it's
# an intraday measure and the chart is daily, so it isn't charted (per request).
_MA_FULL = {
    "SMA": "Moving Average Simple",
    "EMA": "Moving Average Exponential",
    "RSI": "Relative Strength Index",
    "BB": "Bollinger Bands",
}
_IND_RE = re.compile(r"^(SMA|EMA|RSI|BB)\s+(\d+)$", re.IGNORECASE)
_BB_MULT = 2  # standard Bollinger Band width (±2σ)


def _parse_indicator(name: str) -> tuple[str, int] | None:
    """('SMA', 50) for 'SMA 50' / 'EMA 20' / 'RSI 14' / 'BB 20' (case-insensitive),
    else None (e.g. 'VWAP' — not charted on a daily timeframe)."""
    m = _IND_RE.match((name or "").strip())
    return (m.group(1).upper(), int(m.group(2))) if m else None


def _renderable_applied(indicators: list[str] | None) -> list[str]:
    """The requested indicators the chart can actually draw (SMA/EMA/RSI/BB of any
    period), normalised; VWAP and unknowns dropped. Falls back to the SMA 50/200
    staples when nothing chartable was requested."""
    out: list[str] = []
    for ind in indicators or []:
        p = _parse_indicator(ind)
        if p and (norm := f"{p[0]} {p[1]}") not in out:
            out.append(norm)
    return out or ["SMA 50", "SMA 200"]


def _translate_indicator(short_name: str) -> tuple[str, dict[str, Any]]:
    """(full TradingView study name, params) for a short name — used by the local
    TradingView-MCP path. Parses any period ("SMA 20" → length 20); BB carries the
    ±2σ mult. Raises KeyError if unrecognised."""
    s = (short_name or "").strip()
    if s in _INDICATOR_MAP:
        return _INDICATOR_MAP[s]
    p = _parse_indicator(s)
    if p:
        fam, n = p
        params = {"length": n, "mult": _BB_MULT} if fam == "BB" else {"length": n}
        return _MA_FULL[fam], params
    raise KeyError(short_name)


# ---------------------------------------------------------------------------
# Mock helpers — unchanged from the pre-002 file. Used when USE_MOCK_TA=1
# OR when the real path errors and we want to keep the demo flowing during
# dev (we DON'T silently fall back in prod — see _real_or_error()).
# ---------------------------------------------------------------------------


def _key_levels(price: float) -> dict[str, list[float]]:
    return {
        "resistance": [round(price * 1.018, 2), round(price * 1.06, 2)],
        "support":    [round(price * 0.935, 2), round(price * 0.88, 2)],
    }


def _sma_series(base_price: float, periods: int = 50, slope_per_day: float = 0.6) -> list[float]:
    return [round(base_price - (periods - 1 - i) * slope_per_day, 2) for i in range(periods)]


async def _mock_technical_levels(
    ticker: str, timeframe: str, indicators: list[str],
) -> dict[str, Any]:
    quote = MOCK_QUOTES.get(ticker)
    if not quote:
        return {"error": "no_coverage", "ticker": ticker}

    price = quote["price"]
    sma_values: dict[str, float] = {}
    if "SMA 50" in indicators:
        sma_values["SMA 50"] = round(price * 0.97, 2)
    if "SMA 200" in indicators:
        sma_values["SMA 200"] = round(price * 0.91, 2)
    if "EMA 20" in indicators:
        sma_values["EMA 20"] = round(price * 0.99, 2)

    return {
        "ticker": ticker,
        "timeframe": timeframe,
        "current_price": price,
        "currency": "$",
        # 054 — echo the requested (chartable) indicators so the chart draws what
        # was asked for, not just the mock-valued ones (series come from real
        # candles via /api/chart-data regardless of this mock path).
        "indicators_applied": _renderable_applied(indicators),
        "indicator_values": sma_values,
        "key_levels": _key_levels(price),
        "trend": "bullish",
        "golden_cross_recent": True,
        "is_mock": True,
        # Empty string is the canonical "no real screenshot — frontend shows the
        # inline <MockChartSvg/> fallback" value (matches the real path's default
        # on line ~195). Previously this was f"/api/mock-chart/{ticker}.svg",
        # but no such backend route was ever registered, so the frontend <img>
        # 404'd into a broken-image icon. Proposal 019.
        "screenshot_url": "",
        "source": "tradingview_mcp_mocked",
        "sources": [{"name": "TradingView (mocked)"}],
    }


# ---------------------------------------------------------------------------
# Real path — orchestrates 5-7 MCP calls behind a single agent-visible tool.
# ---------------------------------------------------------------------------


def _use_mock_ta() -> bool:
    return os.getenv("USE_MOCK_TA", "0") == "1"


# ---------------------------------------------------------------------------
# yfinance-computed indicators (043) — the real, key-less, prod-ready TA path.
#
# Covers ANY yfinance ticker (US + Hong Kong + … ) by computing SMA/RSI/MACD
# from daily candles in Python (pure pandas, via the DataFrame yfinance returns
# — no new dependency, no TradingView Desktop). This is what lets the agent
# assess HK-listed names like 1398.HK; before 043 the only real path was
# TradingView (local-only) and the mock covered ~11 US tickers, so HK → no_coverage.
# ---------------------------------------------------------------------------

# Base-ccy symbol for the price/level display (HK accounts are HKD → "HK$").
_CCY_SYMBOL = {"USD": "$", "HKD": "HK$", "EUR": "€", "GBP": "£", "JPY": "¥", "CNY": "¥", "CNH": "¥"}

# yfinance period/interval per requested timeframe (enough bars for SMA 200).
_TF_PERIOD = {"1D": "1y", "1W": "5y", "4H": "60d", "1H": "30d"}
_TF_INTERVAL = {"1D": "1d", "1W": "1wk", "4H": "60m", "1H": "60m"}


def _ccy_symbol(code: str | None) -> str:
    if not code:
        return "$"
    return _CCY_SYMBOL.get(code.upper(), f"{code.upper()} ")


def _yfinance_ta_available() -> bool:
    try:
        import yfinance  # noqa: F401
        return True
    except Exception:
        return False


def _tradingview_configured() -> bool:
    """True when a TradingView MCP server is set up (local dev). When it isn't
    (e.g. Railway prod), the real path skips TradingView and computes from
    yfinance — so we don't surface `tradingview_mcp_unreachable` for HK/US alike.
    Gated on the absolute path to the MCP server, the unambiguous signal."""
    return bool(os.getenv("TRADINGVIEW_MCP_ARGS", "").strip())


def _last(series) -> float | None:  # noqa: ANN001 — pandas Series
    """Last non-NaN value of a pandas Series, rounded, or None."""
    s = series.dropna()
    return round(float(s.iloc[-1]), 2) if len(s) else None


def _rsi_last(closes, period: int = 14):  # noqa: ANN001 — pandas Series of closes
    """Last RSI value (Wilder's smoothing) for an arbitrary period, or None."""
    delta = closes.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    # No-loss windows → avg_loss 0 → rs +inf → RSI 100 (the standard result); let it
    # fall out naturally (don't NaN it, which dropped RSI for an all-gains series).
    rs = avg_gain / avg_loss
    return _last(100 - 100 / (1 + rs))


def _compute_indicators(closes, indicators: list[str] | None = None) -> dict[str, float]:  # noqa: ANN001
    """Last values for SMA/EMA/RSI/BB + MACD, for the trend-summary text. Computes
    the **staple** periods (SMA 10/20/50/200, EMA 20, RSI 14) PLUS any extra periods
    the caller requested via `indicators` ("SMA 20", "EMA 50", "RSI 9", "BB 20"), so
    the agent can narrate exactly what was asked for. Pure pandas; keys absent when
    there isn't enough history. (The CHART draws its own series client-side from the
    candles — this dict is for the narrative, not the lines.)"""
    out: dict[str, float | None] = {}
    n = len(closes)
    sma_p, ema_p, rsi_p, bb_p = {10, 20, 50, 200}, {20}, {14}, set()
    for ind in indicators or []:
        p = _parse_indicator(ind)
        if not p:
            continue
        fam, w = p
        {"SMA": sma_p, "EMA": ema_p, "RSI": rsi_p, "BB": bb_p}[fam].add(w)

    for w in sorted(sma_p):
        out[f"SMA {w}"] = _last(closes.rolling(w).mean()) if n >= w else None
    for w in sorted(ema_p):
        out[f"EMA {w}"] = _last(closes.ewm(span=w, adjust=False).mean()) if n >= w else None
    for w in sorted(rsi_p):
        out[f"RSI {w}"] = _rsi_last(closes, w) if n >= w + 1 else None
    for w in sorted(bb_p):  # Bollinger Bands (±2σ) — upper/mid/lower last values
        if n >= w:
            mid = closes.rolling(w).mean()
            sd = closes.rolling(w).std()
            out[f"BB {w} upper"] = _last(mid + _BB_MULT * sd)
            out[f"BB {w} mid"] = _last(mid)
            out[f"BB {w} lower"] = _last(mid - _BB_MULT * sd)
    if n >= 26:  # MACD 12/26/9
        macd_line = closes.ewm(span=12, adjust=False).mean() - closes.ewm(span=26, adjust=False).mean()
        signal = macd_line.ewm(span=9, adjust=False).mean()
        out["MACD"] = _last(macd_line)
        out["MACD signal"] = _last(signal)
        out["MACD hist"] = _last(macd_line - signal)
    return {k: v for k, v in out.items() if v is not None}


def _swing_levels(highs, lows, price: float, lookback: int = 60) -> dict[str, list[float]]:  # noqa: ANN001
    """Support/resistance from the recent swing high/low (last `lookback` bars),
    falling back to a ±4% band around price if the swing is on the wrong side."""
    rec_high = round(float(highs.tail(lookback).max()), 2)
    rec_low = round(float(lows.tail(lookback).min()), 2)
    resistance = [rec_high] if rec_high > price else [round(price * 1.04, 2)]
    support = [rec_low] if rec_low < price else [round(price * 0.96, 2)]
    return {"resistance": resistance, "support": support}


async def _fetch_ohlcv(ticker: str, timeframe: str):  # noqa: ANN201 — (DataFrame|None, str|None)
    """Fetch daily candles from yfinance → `(DataFrame, currency_code)`, or
    `(None, None)` when yfinance is unavailable / the fetch errors. **Shared (044)**
    by the indicator-compute path (`_yfinance_technical_levels`) and the chart-data
    endpoint (`chart_api.py`) so both read the SAME bars. Sync yfinance runs in a
    worker thread; never raises."""
    if not _yfinance_ta_available():
        return None, None
    import yfinance as yf

    period = _TF_PERIOD.get(timeframe, "1y")
    interval = _TF_INTERVAL.get(timeframe, "1d")

    def _pull():
        t = yf.Ticker(ticker)
        df = t.history(period=period, interval=interval)
        ccy = None
        try:
            ccy = (t.fast_info or {}).get("currency")
        except Exception:  # noqa: BLE001 — fast_info is best-effort
            ccy = None
        return df, ccy

    try:
        return await asyncio.to_thread(_pull)
    except Exception as e:  # noqa: BLE001 — network/transport
        log.warning("yfinance OHLCV fetch failed for %s: %s", ticker, e)
        return None, None


async def _yfinance_technical_levels(
    ticker: str, timeframe: str, indicators: list[str],
) -> dict[str, Any]:
    """Compute indicators from yfinance daily candles. Real, key-less, covers
    HK + US. Same widget contract as the mock/TradingView paths, plus `currency`
    (base ccy) and the full `indicator_values` (SMA/RSI/MACD) for the agent to
    narrate. Honest `error` on no data — never a fabricated number."""
    df, ccy = await _fetch_ohlcv(ticker, timeframe)
    if df is None or getattr(df, "empty", True) or "Close" not in getattr(df, "columns", []):
        return {"error": "no_coverage", "ticker": ticker, "message": f"no candles for {ticker}"}
    closes = df["Close"].dropna()
    if len(closes) < 20:
        return {"error": "insufficient_history", "ticker": ticker,
                "message": f"only {len(closes)} bars for {ticker}"}

    indicator_values = _compute_indicators(closes, indicators)
    last_close = round(float(closes.iloc[-1]), 2)
    key_levels = _swing_levels(df["High"], df["Low"], last_close)
    trend = _infer_trend(last_close, indicator_values)
    sma200 = indicator_values.get("SMA 200")
    # indicators_applied = the requested, chartable indicators (any SMA/EMA/RSI/BB
    # period; RSI renders in a lower pane, BB as a band — see TAChart.tsx). 054.
    applied = _renderable_applied(indicators)
    return {
        "ticker": ticker,
        "timeframe": timeframe,
        "current_price": last_close,
        "currency": _ccy_symbol(ccy),
        "indicators_applied": applied,
        "indicator_values": indicator_values,
        "key_levels": key_levels,
        "trend": trend,
        "price_above_sma200": (sma200 is not None and last_close > sma200),
        "bars": len(closes),
        "is_mock": False,
        "screenshot_url": "",  # no TradingView screenshot on this path
        "source": "yfinance_computed",
        "sources": [{"name": f"Daily OHLC via yfinance · {len(closes)}d"}],
    }


# ----- Parsers for the actual `tradesdontlie/tradingview-mcp` response shapes
# (sourced from src/core/data.js + src/core/capture.js in the sibling repo,
# verified pre-apply against the JS source; proposal 023). -----


def _first_numeric_value(values: dict[str, Any]) -> float | None:
    """Return the first parseable numeric value from a TradingView data-window
    `values` dict (`{title: value}` — value can be a string like "211.30")."""
    for v in values.values():
        try:
            return round(float(str(v).replace(",", "").strip()), 2)
        except (TypeError, ValueError):
            continue
    return None


def _extract_indicator_values(
    applied: list[str], studies: list[dict[str, Any]],
) -> dict[str, float]:
    """Map our short indicator names → current values, from the real
    `data_get_study_values` response (`{studies: [{name, values: {title: value}}]}`).

    The MCP server returns ALL visible studies at once (no per-indicator arg).
    `name` is `meta.description` from TradingView — usually just the indicator's
    family name ("Moving Average Simple"), often WITHOUT the length. So for
    SMA 50 vs SMA 200 we disambiguate in two passes:

    Pass 1 — length-aware: prefer studies where the length appears either in
    `name` (e.g. "Moving Average Simple (50)") OR in any title in `values`
    (e.g. `{"MA(50)": "211.30"}`).
    Pass 2 — positional: fall back to the first unused matching study, taking
    studies in the order the MCP server returned them (chart-layer order,
    which matches add-order for our use case).

    A study is "consumed" once mapped, so SMA 50 and SMA 200 can never collide.
    """
    result: dict[str, float] = {}
    used: set[int] = set()

    def _resolve(short: str, *, length_aware: bool) -> None:
        try:
            full_name, params = _translate_indicator(short)
        except KeyError:
            return
        length_str = str(params.get("length")) if "length" in params else None

        for i, s in enumerate(studies):
            if i in used:
                continue
            name = s.get("name") or ""
            values = s.get("values") or {}
            if full_name not in name:
                continue
            if length_aware and length_str is not None:
                in_desc = length_str in name
                in_title = any(length_str in (t or "") for t in values.keys())
                if not (in_desc or in_title):
                    continue
            v = _first_numeric_value(values)
            if v is not None:
                result[short] = v
                used.add(i)
                return

    # Pass 1: prefer length-disambiguated matches (SMA 50 vs 200, RSI 14 vs others).
    for short in applied:
        _resolve(short, length_aware=True)
    # Pass 2: positional fallback for anything still unmatched (no length, or
    # length not surfaced in the description/values titles).
    for short in applied:
        if short not in result:
            _resolve(short, length_aware=False)
    return result


def _partition_pine_levels(
    studies: list[dict[str, Any]], current_price: float,
) -> dict[str, list[float]] | None:
    """Flatten `data_get_pine_lines` `studies[].horizontal_levels` and split
    by side relative to `current_price`.

    Real response shape: `{studies: [{name, total_lines, horizontal_levels: [num, ...]}]}`
    — no labels, no support/resistance distinction inline. Heuristic: levels
    above the current price are resistance (closest first); below are support
    (closest first). Returns None if no usable horizontal levels — caller
    falls back to swing-derived S/R from the same `current_price`.
    """
    all_levels: list[float] = []
    for s in studies or []:
        for lvl in s.get("horizontal_levels") or []:
            try:
                all_levels.append(float(lvl))
            except (TypeError, ValueError):
                continue
    if not all_levels:
        return None
    resistance = sorted({lvl for lvl in all_levels if lvl > current_price})[:2]
    support = sorted({lvl for lvl in all_levels if lvl < current_price}, reverse=True)[:2]
    if not resistance and not support:
        return None
    return {"resistance": resistance, "support": support}


async def _encode_screenshot_file(file_path: str) -> str:
    """Read a PNG from disk and return a `data:image/png;base64,…` URL.

    The MCP server's `capture_screenshot` writes the PNG to its own
    `screenshots/<fname>.png` directory and returns the absolute path —
    NOT inline base64. We read the file in a threadpool to keep the event
    loop free (chart capture happens on the same host as the backend, so
    the file is locally readable).

    Returns "" if the file is missing/unreadable/empty.
    """
    if not file_path:
        return ""
    p = Path(file_path)
    if not p.is_file():
        log.info("capture_screenshot file not found: %s", file_path)
        return ""
    try:
        data = await asyncio.to_thread(p.read_bytes)
    except OSError as e:
        log.warning("could not read screenshot file %s: %s", file_path, e)
        return ""
    if not data:
        return ""
    return f"data:image/png;base64,{base64.b64encode(data).decode('ascii')}"


async def _real_technical_levels(
    ticker: str, timeframe: str, indicators: list[str],
) -> dict[str, Any]:
    """Drive a real TradingView Desktop chart via the MCP server.

    Sequence (each call serialised by the per-session lock in mcp_client.py —
    CDP is single-controller, see .proposed_changes/applied/002 §4.1):

      1. tv_health_check          — fast fail if TV Desktop / CDP isn't ready
      2. chart_set_symbol         — load the ticker
      3. chart_set_timeframe      — switch timeframe
      4. chart_manage_indicator   — add each requested indicator
      5. quote_get                — live current price (needed early so the
                                    S/R fallback below has it)
      6. data_get_study_values    — ONE call (no args) returns all visible
                                    studies; we map each applied indicator
                                    to its value via _extract_indicator_values
      7. data_get_pine_lines      — `studies[].horizontal_levels` partitioned
                                    by `current_price` for S/R
      8. capture_screenshot       — returns a `file_path`; we read + base64-
                                    encode via _encode_screenshot_file

    Returns the same shape as `_mock_technical_levels` (same widget contract).
    The only differences are `is_mock: False` and `source: "tradingview_mcp"`.

    P1.2 first-real-run history:
      - Pre-022: `quote_get` ran last → S/R fallback used MOCK_QUOTES (trust-#3
        violation). 022 moved quote_get to step 5 and made silent shape
        mismatches audible.
      - Pre-023: the parsers for `data_get_study_values` / `pine_lines`
        / `capture_screenshot` were built against guessed shapes that didn't
        match the actual MCP server (`tradesdontlie/tradingview-mcp`). 023
        rewrote them against the real shapes from the JS source.
      - 029 (this): when `quote_get` fails (e.g. TV Desktop closed while
        USE_MOCK_TA=0), `current_price` + the swing-derived S/R fall back to
        the mock cache. We now flag that (`price_is_mock`) and DOWNGRADE the
        `sources` so the card never shows mock numbers under a "live
        TradingView" label — closing the trust-#3 / rule-#7 gap.
    """
    from mcp_client import MCPClientError, tv_call

    try:
        # 1. Health probe
        health = await tv_call("tv_health_check", {})
        if not health.get("ok", True):
            return {"error": "tradingview_mcp_unreachable", "ticker": ticker,
                    "message": "TV Desktop reachable but CDP reports not ready"}

        # 2-3. Set symbol and timeframe
        await tv_call("chart_set_symbol", {"symbol": ticker})
        await tv_call("chart_set_timeframe", {"timeframe": timeframe})

        # 4. Apply each indicator (translate short names → full TradingView names)
        applied: list[str] = []
        for short in indicators:
            try:
                full_name, params = _translate_indicator(short)
            except KeyError:
                log.warning("Unknown indicator %r — skipping", short)
                continue
            await tv_call("chart_manage_indicator",
                          {"action": "add", "name": full_name, **params})
            applied.append(short)

        # Tracks whether a headline number (price → and the swing S/R derived
        # from it) came from the mock cache rather than the live MCP. Drives the
        # `sources` downgrade at the return so we never present mock numbers as
        # "Live OHLC via TradingView MCP" (proposal 029).
        price_is_mock = False

        # 5. Live price — needed for both the response AND the S/R fallback.
        # The MCP server's quote_get returns `{success, symbol, last, close, …}`
        # (no `price` field); we also probe `price` for forward-compat.
        try:
            quote = await tv_call("quote_get", {"symbol": ticker})
            current_price = float(
                quote.get("last") or quote.get("close") or quote.get("price") or 0
            )
            if not current_price:
                log.info(
                    "quote_get(%s) returned no price; falling back to mock cache. raw: %r",
                    ticker, quote,
                )
                current_price = _extract_price(ticker)
                price_is_mock = True
        except MCPClientError as e:
            log.warning(
                "quote_get(%s) failed: %s — falling back to mock cache for current_price",
                ticker, e,
            )
            current_price = _extract_price(ticker)
            price_is_mock = True

        # 6. Indicator values — ONE call (no args). Map by name + length.
        indicator_values: dict[str, float] = {}
        try:
            sv = await tv_call("data_get_study_values", {})
            studies = sv.get("studies") or []
            log.info(
                "data_get_study_values returned %d studies: %s",
                len(studies), [s.get("name") for s in studies],
            )
            indicator_values = _extract_indicator_values(applied, studies)
            for short in applied:
                if short not in indicator_values:
                    log.info(
                        "could not extract %s from studies; titles available: %s",
                        short, [list((s.get("values") or {}).keys()) for s in studies],
                    )
        except MCPClientError as e:
            log.warning("data_get_study_values failed: %s", e)

        # 7. S/R levels — MCP-drawn Pine lines partitioned by current_price;
        # fall back to swing-derived (from the REAL current_price, post-022).
        try:
            pl = await tv_call("data_get_pine_lines", {})
            studies = pl.get("studies") or []
            parsed = _partition_pine_levels(studies, current_price)
            if parsed is None:
                log.info(
                    "data_get_pine_lines returned no usable horizontal_levels; "
                    "computing swing-derived S/R from real price %.2f. studies: %s",
                    current_price, [s.get("name") for s in studies],
                )
                key_levels = _key_levels(current_price)
            else:
                swing = _key_levels(current_price)
                key_levels = {
                    "resistance": parsed["resistance"] or swing["resistance"],
                    "support":    parsed["support"]    or swing["support"],
                }
        except MCPClientError as e:
            log.warning(
                "data_get_pine_lines failed: %s — computing swing-derived S/R from "
                "real price %.2f", e, current_price,
            )
            key_levels = _key_levels(current_price)

        # 8. Screenshot — MCP writes to disk, returns `file_path`. We read +
        # base64-encode. Use region="chart" so we capture just the chart pane,
        # not the whole TV window.
        screenshot_url = ""
        try:
            shot = await tv_call("capture_screenshot", {"region": "chart"})
            file_path = shot.get("file_path") or ""
            if file_path:
                screenshot_url = await _encode_screenshot_file(file_path)
                if not screenshot_url:
                    log.info("capture_screenshot file empty/unreadable: %s", file_path)
            else:
                shape_hint = (
                    f"keys={list(shot.keys())}" if isinstance(shot, dict)
                    else f"type={type(shot).__name__}"
                )
                log.info(
                    "capture_screenshot returned no file_path; screenshot omitted. "
                    "%s. raw: %r", shape_hint, shot,
                )
        except MCPClientError as e:
            log.warning("capture_screenshot failed: %s", e)

        # When the live price degraded to the mock cache, the headline numbers
        # (price + the swing-derived S/R computed from it) are NOT live. Label
        # the source honestly instead of claiming "Live OHLC via TradingView
        # MCP" (trust principle #3 / rule #7). Proposal 029.
        if price_is_mock:
            source = "tradingview_mcp_degraded"
            sources = [{"name": "TradingView (mocked — live data unavailable)"}]
        else:
            source = "tradingview_mcp"
            sources = [
                {"name": "TradingView Desktop", "url": "https://www.tradingview.com"},
                {"name": "Live OHLC via TradingView MCP"},
            ]

        return {
            "ticker": ticker,
            "timeframe": timeframe,
            "current_price": round(current_price, 2),
            "currency": "$",
            "indicators_applied": applied,
            "indicator_values": indicator_values,
            "key_levels": key_levels,
            "trend": _infer_trend(current_price, indicator_values),
            "is_mock": False,
            "screenshot_url": screenshot_url,
            "source": source,
            "sources": sources,
        }
    except MCPClientError as e:
        # Surface error honestly — no silent fall-through to mock per the
        # "no silent fallback" rule (SESSION_LOG 2026-05-20).
        return {"error": e.code, "ticker": ticker, "message": str(e)}


def _encode_screenshot(b64_payload: str) -> str:
    """Wrap a base64-encoded PNG as a data URL the frontend <img> can render.

    Accepts either a raw base64 string or one already prefixed with `data:`.
    Kept for forward-compat / direct base64 callers; the real path (023+)
    goes through `_encode_screenshot_file` because the MCP server writes to
    disk rather than returning base64 inline.
    """
    if b64_payload.startswith("data:"):
        return b64_payload
    # Validate it's actually base64 to avoid feeding garbage to the browser
    try:
        base64.b64decode(b64_payload, validate=True)
    except Exception:
        return ""
    return f"data:image/png;base64,{b64_payload}"


def _extract_price(ticker: str) -> float:
    """Best-effort current price from local mock cache. Used only as the LAST
    fallback when `quote_get` (the live MCP price call) itself fails — never
    for the S/R fallback alongside a successful `quote_get` (that was the
    pre-022 bug). Returns 100.0 for unknown tickers."""
    q = MOCK_QUOTES.get(ticker.upper())
    return float(q["price"]) if q else 100.0


def _infer_trend(price: float, indicators: dict[str, float]) -> str:
    """Cheap trend heuristic from SMA position. Mock returns 'bullish' always;
    real path uses indicators if available."""
    sma50 = indicators.get("SMA 50")
    sma200 = indicators.get("SMA 200")
    if sma50 and sma200 and price > sma50 > sma200:
        return "bullish"
    if sma50 and sma200 and price < sma50 < sma200:
        return "bearish"
    return "neutral"


# ---------------------------------------------------------------------------
# Public tools
# ---------------------------------------------------------------------------


async def get_technical_levels(args: dict[str, Any], user_id: str) -> dict[str, Any]:
    """Get current price + indicator values + S/R levels for a ticker.

    Real path drives a TradingView Desktop chart via MCP; mock returns
    deterministic data that matches the demo HTML.
    """
    ticker = (args.get("ticker") or "").upper()
    timeframe = args.get("timeframe", "1D")
    indicators = args.get("indicators") or ["SMA 50", "SMA 200"]

    # Source priority (043):
    #   1. Deterministic mock (USE_MOCK_TA=1) — offline/demo. Only covers the
    #      US MOCK_QUOTES set; a non-covered ticker (e.g. 1398.HK) FALLS THROUGH
    #      to the real yfinance path, so HK works even in the mock default
    #      (incl. today's Railway USE_MOCK_TA=1) — no flag change needed.
    #   2. TradingView Desktop (local dev, when configured) — the rich
    #      screenshot/"talk to charts" path. On failure, fall through (don't
    #      lose the assessment).
    #   3. yfinance-computed indicators — real, key-less, covers HK + US,
    #      production-ready (no TradingView). The default real path.
    if _use_mock_ta():
        m = await _mock_technical_levels(ticker, timeframe, indicators)
        if "error" not in m:
            return m
        if not _yfinance_ta_available():
            return m  # honest no_coverage when fully offline
        log.info("mock TA has no coverage for %s; computing from yfinance", ticker)
        return await _yfinance_technical_levels(ticker, timeframe, indicators)

    if _tradingview_configured():
        tv = await _real_technical_levels(ticker, timeframe, indicators)
        if "error" not in tv:
            return tv
        log.info("TradingView TA failed for %s (%s); computing from yfinance",
                 ticker, tv.get("error"))

    return await _yfinance_technical_levels(ticker, timeframe, indicators)


async def chart_apply_indicator(args: dict[str, Any], user_id: str) -> dict[str, Any]:
    """Add (or remove) an indicator and return the updated ta_chart payload."""
    ticker = (args.get("ticker") or "").upper()
    indicator = args.get("indicator") or "SMA 50"
    timeframe = args.get("timeframe", "1D")
    action = args.get("action", "add")  # "add" | "remove"

    if _use_mock_ta():
        # Mock: behave identically to get_technical_levels but acknowledge the
        # action in the response so the agent can compose a confirmatory widget.
        result = await _mock_technical_levels(ticker, timeframe, [indicator])
        result["_action"] = action
        return result

    try:
        from mcp_client import MCPClientError, tv_call

        full_name, params = _translate_indicator(indicator)
        await tv_call("chart_set_symbol", {"symbol": ticker})
        await tv_call("chart_set_timeframe", {"timeframe": timeframe})
        await tv_call("chart_manage_indicator",
                      {"action": action, "name": full_name, **params})
        # Re-read the chart state so the agent gets a fresh, complete payload
        return await _real_technical_levels(ticker, timeframe, [indicator])
    except MCPClientError as e:
        return {"error": e.code, "ticker": ticker, "message": str(e)}


async def chart_draw_levels(args: dict[str, Any], user_id: str) -> dict[str, Any]:
    """Draw user-specified support and/or resistance lines on the chart."""
    ticker = (args.get("ticker") or "").upper()
    timeframe = args.get("timeframe", "1D")
    support: list[float] = args.get("support") or []
    resistance: list[float] = args.get("resistance") or []

    if _use_mock_ta():
        result = await _mock_technical_levels(ticker, timeframe, ["SMA 50", "SMA 200"])
        # Replace mock-computed levels with user-specified ones
        result["key_levels"] = {
            "support": [round(float(v), 2) for v in support],
            "resistance": [round(float(v), 2) for v in resistance],
        }
        return result

    try:
        from mcp_client import MCPClientError, tv_call

        await tv_call("chart_set_symbol", {"symbol": ticker})
        await tv_call("chart_set_timeframe", {"timeframe": timeframe})
        for y in support:
            await tv_call("draw_shape", {
                "shape": "horizontal_line", "y1": y, "y2": y,
                "label": f"Support {y}", "color": "#0F6E56",
            })
        for y in resistance:
            await tv_call("draw_shape", {
                "shape": "horizontal_line", "y1": y, "y2": y,
                "label": f"Resistance {y}", "color": "#C0392B",
            })
        return await _real_technical_levels(ticker, timeframe, ["SMA 50", "SMA 200"])
    except MCPClientError as e:
        return {"error": e.code, "ticker": ticker, "message": str(e)}


async def chart_scroll_to_date(args: dict[str, Any], user_id: str) -> dict[str, Any]:
    """Scroll the chart's visible range to centre on a target date."""
    ticker = (args.get("ticker") or "").upper()
    timeframe = args.get("timeframe", "1D")
    target_date = args.get("date")  # ISO-8601 (YYYY-MM-DD or full datetime)

    if not target_date:
        return {"error": "missing_date", "ticker": ticker,
                "message": "Pass `date` as an ISO-8601 string."}

    if _use_mock_ta():
        result = await _mock_technical_levels(ticker, timeframe, ["SMA 50", "SMA 200"])
        result["_scrolled_to"] = target_date
        return result

    try:
        from mcp_client import MCPClientError, tv_call

        await tv_call("chart_set_symbol", {"symbol": ticker})
        await tv_call("chart_set_timeframe", {"timeframe": timeframe})
        await tv_call("chart_scroll_to_date", {"date": target_date})
        return await _real_technical_levels(ticker, timeframe, ["SMA 50", "SMA 200"])
    except MCPClientError as e:
        return {"error": e.code, "ticker": ticker, "message": str(e)}


# ---------------------------------------------------------------------------
# get_correlation_matrix — unchanged; mock-only for MVP (per
# docs/TRUENORTH_MCP_INTEGRATION.md §4 footnote).
# ---------------------------------------------------------------------------


async def get_correlation_matrix(args: dict[str, Any], user_id: str) -> dict[str, Any]:
    """Compute the rolling-60-day correlation matrix for a list of tickers (mock)."""
    tickers = args.get("tickers") or []
    if not tickers:
        return {"error": "no_tickers"}
    tickers = [t.upper() for t in tickers[:12]]

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


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

register(
    ToolDef(
        name="get_technical_levels",
        description=(
            "Get technical analysis for one ticker: current price (in the ticker's "
            "own currency — see `currency`), indicator values (`indicator_values`: "
            "the requested SMA/EMA/RSI/Bollinger periods + MACD), key support and "
            "resistance levels, a trend label, `price_above_sma200`, and (in local "
            "dev with TradingView) a chart screenshot. Pass `indicators` with ANY "
            "period — e.g. 'SMA 20', 'SMA 100', 'EMA 50', 'RSI 9', 'BB 20' (Bollinger "
            "Bands). The chart draws each: SMA/EMA/BB overlay the price, RSI renders "
            "in a lower pane. (VWAP isn't charted on the daily timeframe.) Works for "
            "US AND non-US tickers — including Hong Kong, e.g. 1398.HK. Use this to "
            "build a ta_chart widget; cite the indicator values in `trend_summary_html`."
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
                    "items": {
                        "type": "string",
                        "description": ("An indicator with its period: 'SMA <n>', "
                                        "'EMA <n>', 'RSI <n>', or 'BB <n>' (Bollinger "
                                        "Bands, ±2σ). E.g. 'SMA 20', 'EMA 50', 'RSI 14'."),
                    },
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
        name="chart_apply_indicator",
        description=(
            "Add or remove a technical indicator on the user's chart for one ticker. "
            "Use when the user says things like 'add the 20-day EMA' or 'remove the "
            "SMA 200'. The indicator carries its period: 'SMA <n>', 'EMA <n>', "
            "'RSI <n>', or 'BB <n>' (Bollinger Bands). Returns the updated chart state "
            "— use it to emit an updated ta_chart widget."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "indicator": {
                    "type": "string",
                    "description": "'SMA <n>', 'EMA <n>', 'RSI <n>', or 'BB <n>' — e.g. 'EMA 20'.",
                },
                "action": {"type": "string", "enum": ["add", "remove"], "default": "add"},
                "timeframe": {
                    "type": "string",
                    "enum": ["1D", "4H", "1H", "1W"],
                    "default": "1D",
                },
            },
            "required": ["ticker", "indicator"],
            "additionalProperties": False,
        },
        callable=chart_apply_indicator,
        thought_template="Applying {indicator} to {ticker}",
    )
)

register(
    ToolDef(
        name="chart_draw_levels",
        description=(
            "Draw horizontal support and/or resistance lines on the user's chart. "
            "Use when the user names specific price levels — e.g. 'draw support at "
            "220 and resistance at 250'. Returns the updated chart state for a "
            "ta_chart widget."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "support": {
                    "type": "array",
                    "items": {"type": "number"},
                    "maxItems": 4,
                    "default": [],
                },
                "resistance": {
                    "type": "array",
                    "items": {"type": "number"},
                    "maxItems": 4,
                    "default": [],
                },
                "timeframe": {
                    "type": "string",
                    "enum": ["1D", "4H", "1H", "1W"],
                    "default": "1D",
                },
            },
            "required": ["ticker"],
            "additionalProperties": False,
        },
        callable=chart_draw_levels,
        thought_template="Drawing support/resistance on {ticker}",
    )
)

register(
    ToolDef(
        name="chart_scroll_to_date",
        description=(
            "Scroll the user's chart to centre the visible range on a target date. "
            "Use when the user says things like 'scroll to March 2024' or 'show me "
            "the 2022 drawdown'. Returns the updated chart state for a ta_chart widget."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "date": {
                    "type": "string",
                    "format": "date",
                    "description": "ISO-8601 date (YYYY-MM-DD) to centre the chart on.",
                },
                "timeframe": {
                    "type": "string",
                    "enum": ["1D", "4H", "1H", "1W"],
                    "default": "1D",
                },
            },
            "required": ["ticker", "date"],
            "additionalProperties": False,
        },
        callable=chart_scroll_to_date,
        thought_template="Scrolling {ticker} chart to {date}",
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

# register(
#     ToolDef(
#         name="get_sector_exposure",
#         description=(
#             "Compute a 60-day rolling sector exposure for a set of tickers. Returns "
#             "the sector mix plus the average exposure. Use this for portfolio_risk "
#             "widgets when the user asks about sector exposure."
#         ),
#         input_schema={
#             "type": "object",
#             "properties": {
#                 "tickers": {
#                     "type": "array",
#                     "items": {"type": "string"},
#                     "minItems": 2,
#                     "maxItems": 12,
#                 }
#             },
#             "required": ["tickers"],
#             "additionalProperties": False,
#         },
#         callable=get_sector_exposure,
#         thought_template="Computing 60-day sector exposure",
#     )
# )
