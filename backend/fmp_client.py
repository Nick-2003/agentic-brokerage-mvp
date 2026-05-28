"""Financial Modeling Prep (FMP) REST client — real research data for P2b.

FMP is the chosen research/fundamentals provider (PRIORITIES.md P2b; pragmatic
MVP default over Fiscal.ai). It supplies the *research* layer the research_card
needs — analyst consensus, price targets, valuation multiples, margins, sector,
SEC filings — that Alpaca/yfinance (price layer) don't.

Mock-first discipline (same as market.py ↔ yfinance, execution.py ↔ Alpaca):
- Research tools call into here ONLY when `fmp_enabled()` is true:
  `USE_MOCK_RESEARCH != "1"` AND a real `FMP_API_KEY` is set.
- On any failure, surface `{"error": "fmp_fetch_failed", ...}` — NO silent
  fall-through to mock (same rule as `alpaca_fetch_failed` / `yfinance_fetch_failed`).
- With `USE_MOCK_RESEARCH=1` (or no key), the hand-tuned mocks in research.py
  are used — the deterministic demo path is preserved.

⚠️ FIELD-NAME VERIFICATION REQUIRED ON FIRST LIVE CALL.
FMP migrated to the "stable" API (https://financialmodelingprep.com/stable/...)
and renamed many fields (TTM suffixes, etc.). The exact response field names
below are coded *defensively* (each value tries several candidate keys) because
they could not be verified without a live API key at draft time. Run
`scripts/fmp_probe.py` with a real FMP_API_KEY to dump actual response shapes
and tighten `_pick()` candidate lists if needed. See proposal 006 README §"Field
verification".

Uses httpx (already a backend dependency — no pyproject change).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

FMP_BASE = "https://financialmodelingprep.com/stable"
_TIMEOUT = float(os.getenv("FMP_TIMEOUT_S", "10"))


class FMPError(Exception):
    """Any FMP fetch/parse failure. Surfaced to the agent as fmp_fetch_failed."""

    code = "fmp_fetch_failed"


def fmp_enabled() -> bool:
    """True iff the real FMP path should be used (key present, mock not forced)."""
    if os.getenv("USE_MOCK_RESEARCH") == "1":
        return False
    key = os.getenv("FMP_API_KEY", "")
    return bool(key) and not key.endswith("REPLACE")


def _pick(d: dict[str, Any], *candidates: str, default: Any = None) -> Any:
    """Return the first present, non-None candidate key from a dict.

    Defensive against FMP's stable-API field renames — pass several plausible
    names, get the first that exists. E.g. _pick(row, "targetMedian", "targetConsensus").
    """
    for c in candidates:
        if c in d and d[c] is not None:
            return d[c]
    return default


def _pct(v: Any) -> float | None:
    """FMP margins/growth are decimals (0.46); our schema uses percent (46.0)."""
    try:
        return round(float(v) * 100, 2)
    except (TypeError, ValueError):
        return None


def _num(v: Any, ndigits: int = 2) -> float | None:
    try:
        return round(float(v), ndigits)
    except (TypeError, ValueError):
        return None


async def _get(path: str, params: dict[str, Any]) -> Any:
    """GET {FMP_BASE}/{path} with the API key appended. Raises FMPError on failure."""
    key = os.getenv("FMP_API_KEY", "")
    if not key:
        raise FMPError("FMP_API_KEY not set")
    q = {**params, "apikey": key}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(f"{FMP_BASE}/{path}", params=q)
    except httpx.HTTPError as e:
        raise FMPError(f"FMP {path} request error: {e}") from e
    if resp.status_code != 200:
        # FMP returns 401 for a bad/over-quota key, 403 for plan-gating.
        raise FMPError(f"FMP {path} → HTTP {resp.status_code}: {resp.text[:160]}")
    try:
        data = resp.json()
    except ValueError as e:
        raise FMPError(f"FMP {path} → non-JSON body: {resp.text[:160]}") from e
    return data


def _first(data: Any) -> dict[str, Any]:
    """Most FMP endpoints return a list of one row for a single symbol."""
    if isinstance(data, list):
        return data[0] if data else {}
    if isinstance(data, dict):
        return data
    return {}


# ---------------------------------------------------------------------------
# Endpoint helpers — one per FMP endpoint we use. Each returns a normalised
# dict in OUR shape (so research.py doesn't deal with FMP field names).
# ---------------------------------------------------------------------------


async def profile(symbol: str) -> dict[str, Any]:
    """Company profile — sector, price, name. /stable/profile?symbol="""
    row = _first(await _get("profile", {"symbol": symbol}))
    return {
        "sector": _pick(row, "sector", default="Unknown"),
        "industry": _pick(row, "industry"),
        "company_name": _pick(row, "companyName", "name"),
        "price": _num(_pick(row, "price")),
        "market_cap": _pick(row, "marketCap", "mktCap"),
    }


def _ratio(num: Any, den: Any) -> float | None:
    """Safe ratio helper for derived metrics. None if either side missing/zero."""
    try:
        n, d = float(num), float(den)
        return round(n / d, 2) if d else None
    except (TypeError, ValueError):
        return None


async def ratios_ttm(symbol: str) -> dict[str, Any]:
    """Valuation multiples + margins. /stable/ratios-ttm?symbol=

    Proposal 009 — field names confirmed against a live AAPL probe:
    - FMP's ratios-ttm has NO free-cash-flow-margin field → derive it from
      freeCashFlowPerShareTTM / revenuePerShareTTM.
    - It has NO EV/Sales field → the previous `_pick` fell through to
      priceToSalesRatioTTM, silently mislabelling P/S as EV/Sales. Derive true
      EV/Sales = enterpriseValueMultipleTTM (EV/EBITDA) × ebitdaMarginTTM
      (EBITDA/Sales). None if either input is missing — never mislabel P/S.
    """
    row = _first(await _get("ratios-ttm", {"symbol": symbol}))

    ev_ebitda = _num(_pick(row, "enterpriseValueMultipleTTM", "evToEBITDATTM",
                           "enterpriseValueOverEBITDATTM"))

    # EV/Sales = EV/EBITDA × EBITDA-margin (EBITDA/Sales). None if either missing
    # — never mislabel P/S (priceToSalesRatioTTM) as EV/Sales.
    ebitda_margin = _pick(row, "ebitdaMarginTTM")  # decimal
    ev_sales = (round(ev_ebitda * float(ebitda_margin), 2)
                if ev_ebitda is not None and ebitda_margin is not None else None)

    # No direct FCF-margin field in ratios-ttm → FCF/share ÷ revenue/share × 100.
    fcf_margin_pct = _ratio(_pick(row, "freeCashFlowPerShareTTM"), _pick(row, "revenuePerShareTTM"))
    if fcf_margin_pct is not None:
        fcf_margin_pct = round(fcf_margin_pct * 100, 2)

    return {
        "pe": _num(_pick(row, "priceToEarningsRatioTTM", "peRatioTTM")),
        "ev_ebitda": ev_ebitda,
        "ev_sales": ev_sales,
        "peg": _num(_pick(row, "priceToEarningsGrowthRatioTTM", "pegRatioTTM")),
        "gross_margin_pct": _pct(_pick(row, "grossProfitMarginTTM", "grossMarginTTM")),
        "op_margin_pct": _pct(_pick(row, "operatingProfitMarginTTM", "operatingMarginTTM")),
        "fcf_margin_pct": fcf_margin_pct,
        "net_margin_pct": _pct(_pick(row, "netProfitMarginTTM", "netMarginTTM")),
        # P/S kept available (real field) in case the research card wants it later.
        "ps_ratio": _num(_pick(row, "priceToSalesRatioTTM")),
    }


async def financial_growth(symbol: str) -> dict[str, Any]:
    """YoY growth. /stable/financial-growth?symbol= (period=annual, limit=1)"""
    data = await _get("financial-growth", {"symbol": symbol, "period": "annual", "limit": 1})
    row = _first(data)
    return {"revenue_growth_pct": _pct(_pick(row, "revenueGrowth", "growthRevenue"))}


async def price_target_consensus(symbol: str) -> dict[str, Any]:
    """Sell-side target distribution. /stable/price-target-consensus?symbol="""
    row = _first(await _get("price-target-consensus", {"symbol": symbol}))
    return {
        "high_target": _num(_pick(row, "targetHigh"), 0),
        "low_target": _num(_pick(row, "targetLow"), 0),
        "median_target": _num(_pick(row, "targetMedian", "targetConsensus"), 0),
        "mean_target": _num(_pick(row, "targetConsensus", "targetMean", "targetMedian"), 0),
    }


_RATING_MAP = {
    "strong buy": "BUY", "buy": "BUY", "outperform": "BUY", "overweight": "BUY",
    "hold": "HOLD", "neutral": "HOLD", "market perform": "HOLD",
    "sell": "SELL", "underperform": "SELL", "underweight": "SELL", "strong sell": "SELL",
}


async def grades_consensus(symbol: str) -> dict[str, Any]:
    """Analyst rating distribution + consensus label. /stable/grades-consensus?symbol="""
    row = _first(await _get("grades-consensus", {"symbol": symbol}))
    strong_buy = int(_pick(row, "strongBuy", default=0) or 0)
    buy = int(_pick(row, "buy", default=0) or 0)
    hold = int(_pick(row, "hold", default=0) or 0)
    sell = int(_pick(row, "sell", default=0) or 0)
    strong_sell = int(_pick(row, "strongSell", default=0) or 0)
    label = str(_pick(row, "consensus", "rating", default="")).strip().lower()
    rating = _RATING_MAP.get(label)
    if rating is None:
        # Derive from the distribution if no clean label.
        bull = strong_buy + buy
        bear = sell + strong_sell
        rating = "BUY" if bull > bear and bull >= hold else ("SELL" if bear > bull else "HOLD")
    return {
        "consensus_rating": rating,
        "n_analysts": strong_buy + buy + hold + sell + strong_sell,
        "distribution": {"strong_buy": strong_buy, "buy": buy, "hold": hold,
                         "sell": sell, "strong_sell": strong_sell},
    }


async def stock_peers(symbol: str) -> list[str]:
    """Peer tickers. /stable/stock-peers?symbol="""
    data = await _get("stock-peers", {"symbol": symbol})
    row = _first(data)
    peers = _pick(row, "peersList", "peers", default=None)
    if isinstance(peers, list):
        return [str(p).upper() for p in peers]
    # Some shapes return a flat list of {symbol:...}
    if isinstance(data, list):
        out = [str(_pick(r, "symbol", default="")).upper() for r in data]
        return [p for p in out if p and p != symbol.upper()]
    return []


async def sec_filings(symbol: str, limit: int = 3) -> list[dict[str, Any]]:
    """Recent SEC filings. /stable/sec-filings-search/symbol?symbol=

    Proposal 009 — the probe showed this returned [] with only `symbol`+`limit`.
    FMP's stable filings-by-symbol search is a DATE-RANGE query, so we now pass
    `from`/`to` (last ~18 months) + `page`. Still best-effort / defensive on the
    path; re-run `scripts/fmp_probe.py` (now dumps raw sec-filings) to confirm —
    if it's still empty, the endpoint is premium-gated on the current FMP tier
    and filings stay an optional enrichment (the agent synthesises from news).
    """
    now = datetime.now(timezone.utc)
    date_params = {
        "from": (now - timedelta(days=550)).strftime("%Y-%m-%d"),
        "to": now.strftime("%Y-%m-%d"),
        "page": 0,
        "limit": limit,
    }
    for path in ("sec-filings-search/symbol", "sec-filings"):
        try:
            data = await _get(path, {"symbol": symbol, **date_params})
        except FMPError:
            continue
        rows = data if isinstance(data, list) else []
        out = []
        for r in rows[:limit]:
            out.append({
                "type": _pick(r, "type", "formType", "form"),
                "date": _pick(r, "fillingDate", "filingDate", "date", "acceptedDate"),
                "url": _pick(r, "finalLink", "link", "url"),
            })
        if out:
            return out
    return []
