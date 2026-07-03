"""Alpha Vantage NEWS_SENTIMENT client — a news SUPPLEMENT to yfinance (060).

Scope (decided 2026-06-29): the **daily briefing only**. `news_context.fetch_recent_news`
calls in here **only** when its caller opts in with `use_av=True` (the briefing does;
the chat `get_company_news` tool does not), so the chat path stays on quota-free
yfinance and AV's tight free-tier budget is spent on the few top-mover calls per brief.

Mock-first discipline (same as fmp_client.py ↔ FMP, news_context ↔ yfinance):
- `av_news_enabled()` is true only when `USE_MOCK_NEWS != "1"` AND a real
  `ALPHAVANTAGE_API_KEY` is set.
- Best-effort: any failure (throttle, key, network, daily cap) raises `AVError`;
  the caller (`news_context`) catches it and keeps the yfinance-only result. AV
  NEVER breaks or blocks a brief.

Free-tier budget (25 requests/day) is managed here:
- an in-process **TTL cache** keyed by (sorted tickers, since) collapses repeats;
- a **soft daily-call cap** (`ALPHAVANTAGE_DAILY_CAP`) — once hit, `fetch_news`
  raises `AVError` for the rest of the UTC day (→ yfinance-only), never fatal.

⚠️ FIELD-NAME VERIFICATION on first live key: run `scripts/av_news_probe.py AAPL,MSFT`
to confirm the `feed[]` / `ticker_sentiment[]` shapes below against a real response.

Uses httpx (already a backend dependency — no pyproject change).
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)

AV_URL = "https://www.alphavantage.co/query"


def _timeout() -> float:
    return float(os.getenv("ALPHAVANTAGE_TIMEOUT_S", "10"))


def _cache_ttl() -> float:
    return float(os.getenv("ALPHAVANTAGE_CACHE_TTL_S", "600"))


def _daily_cap() -> int:
    return int(os.getenv("ALPHAVANTAGE_DAILY_CAP", "22"))


class AVError(Exception):
    """Any AV fetch/parse/cap failure. Caught by news_context → yfinance-only."""

    code = "alphavantage_fetch_failed"


def av_news_enabled() -> bool:
    """True iff the real AV path should run (key present, mock not forced)."""
    if os.getenv("USE_MOCK_NEWS") == "1":
        return False
    key = os.getenv("ALPHAVANTAGE_API_KEY", "")
    return bool(key) and not key.endswith("REPLACE")


# --- small pure helpers (unit-tested offline) ---------------------------------

def _num(v: Any) -> float | None:
    try:
        return round(float(v), 4)
    except (TypeError, ValueError):
        return None


def _parse_av_ts(s: str | None) -> str | None:
    """AV `time_published` ("YYYYMMDDTHHMMSS") → ISO-8601 UTC (matches yfinance)."""
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def _since_to_time_from(since: str | None) -> str | None:
    """Our ISO-8601 `since` → AV `time_from` ("YYYYMMDDTHHMM")."""
    if not since:
        return None
    try:
        dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        return dt.strftime("%Y%m%dT%H%M")
    except (TypeError, ValueError):
        return None


def _host(url: str | None) -> str:
    if not url:
        return ""
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except ValueError:
        return ""


def _parse_av_feed_item(item: dict[str, Any]) -> dict[str, Any] | None:
    """One AV `feed[]` entry → {headline, source, ts, url, sentiment} or None."""
    if not isinstance(item, dict):
        return None
    title = item.get("title")
    if not title:
        return None
    return {
        "headline": title,
        "source": item.get("source") or "Alpha Vantage",
        "ts": _parse_av_ts(item.get("time_published")),
        "url": item.get("url"),
        "sentiment": {
            "label": item.get("overall_sentiment_label"),
            "score": _num(item.get("overall_sentiment_score")),
        },
    }


def _raise_if_error(data: Any) -> dict[str, Any]:
    """AV returns 200 + a `{"Information"|"Note"|"Error Message": …}` body when
    throttled / keyed wrong, instead of an HTTP error. Turn those into AVError."""
    if not isinstance(data, dict) or "feed" not in data:
        msg = "no feed in response"
        if isinstance(data, dict):
            msg = data.get("Information") or data.get("Note") or data.get("Error Message") or msg
        raise AVError(f"alphavantage: {msg}")
    return data


def _feed_to_news_by_ticker(
    data: dict[str, Any], syms: list[str], limit: int, since: str | None
) -> dict[str, list[dict[str, Any]]]:
    """Re-key AV's flat `feed[]` into {SYM: [item, …]} using each article's
    `ticker_sentiment[]` (which names the tickers it's about + a relevance score).
    Newest-first, `since`-filtered, capped at `limit` per symbol."""
    want = {s.upper() for s in syms}
    out: dict[str, list[dict[str, Any]]] = {s.upper(): [] for s in syms}
    for item in data.get("feed", []):
        norm = _parse_av_feed_item(item)
        if not norm:
            continue
        for ts_entry in item.get("ticker_sentiment", []) or []:
            sym = (ts_entry.get("ticker") or "").upper()
            if sym in want:
                out[sym].append({**norm, "relevance": _num(ts_entry.get("relevance_score"))})
    for sym, items in out.items():
        if since:
            items = [it for it in items if (it.get("ts") or "") >= since]
        items.sort(key=lambda it: it.get("ts") or "", reverse=True)
        out[sym] = items[:limit]
    return out


# --- TTL cache + daily-call cap -----------------------------------------------

# {(sorted tickers tuple, since): (result, monotonic_ts)}
_cache: dict[tuple[tuple[str, ...], str], tuple[dict[str, Any], float]] = {}
# mutated in place so the module holds one rolling counter for the UTC day
_calls: dict[str, Any] = {"day": None, "count": 0}


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _under_daily_cap() -> bool:
    if _calls["day"] != _today():
        _calls["day"] = _today()
        _calls["count"] = 0
    return _calls["count"] < _daily_cap()


def _record_call() -> None:
    if _calls["day"] != _today():
        _calls["day"] = _today()
        _calls["count"] = 0
    _calls["count"] += 1


# --- public API ---------------------------------------------------------------

async def fetch_news(
    tickers: list[str], limit: int = 5, since: str | None = None
) -> dict[str, Any]:
    """REAL AV headlines for `tickers`. Returns
    {"news_by_ticker": {SYM: [{headline, source, ts, url, sentiment, relevance}]},
     "source": "alphavantage"} or raises AVError (throttle/cap/network/key).

    One multi-ticker upstream call (AV `tickers=` is comma-separated); cached for
    `ALPHAVANTAGE_CACHE_TTL_S`; skipped (AVError) once the daily cap is hit."""
    syms = [t.upper() for t in tickers if t][:10]
    if not syms:
        return {"news_by_ticker": {}, "source": "alphavantage"}

    ck = (tuple(sorted(syms)), since or "")
    hit = _cache.get(ck)
    if hit and (time.monotonic() - hit[1]) < _cache_ttl():
        return hit[0]

    if not _under_daily_cap():
        raise AVError(f"daily cap reached ({_daily_cap()}/day)")

    key = os.getenv("ALPHAVANTAGE_API_KEY", "")
    if not key:
        raise AVError("ALPHAVANTAGE_API_KEY not set")
    params = {
        "function": "NEWS_SENTIMENT",
        "tickers": ",".join(syms),
        "apikey": key,
        "sort": "LATEST",
        # AV mixes all tickers into one feed; over-fetch so each symbol still
        # has enough after re-keying + per-symbol capping.
        "limit": str(min(1000, max(50, limit * len(syms) * 5))),
    }
    tf = _since_to_time_from(since)
    if tf:
        params["time_from"] = tf

    _record_call()  # count before the request so repeated failures can't blow the quota
    try:
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            resp = await client.get(AV_URL, params=params)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        raise AVError(f"alphavantage request failed: {e}") from e

    _raise_if_error(data)
    out = _feed_to_news_by_ticker(data, syms, limit, since)
    result = {"news_by_ticker": out, "source": "alphavantage"}
    _cache[ck] = (result, time.monotonic())
    return result
