"""Real market-context for the W2 briefing: the "why it moved" (news) AND the
"what it means" (macro) layers.

A SYSTEM-SIDE helper (like `ibkr_flex.py` / `briefing.py`), deliberately NOT an
agent tool and NOT part of the `tools/` registry — the briefing is a system job
(SECURITY threat 1). Keeping it out of `tools/` also leaves the chat product's
`get_company_news` / `get_macro_snapshot` mock tools (and the `test_P1_003`
regression) completely untouched: the briefing fetches REAL data here for live
briefs, and uses the mock tools only for labelled demos.

Source = yfinance (already a backend dependency, no API tier):
  • `fetch_recent_news` — per-ticker headlines (the "why"). `.news` shape changed
    across versions: items are now {"id", "content": {"title", "pubDate",
    "provider": {"displayName"}, "canonicalUrl": {"url"}}} (older versions were
    flat: {"title", "publisher", "providerPublishTime", "link"}).
    `_parse_yf_news_item` tolerates both — verify with `scripts/news_probe.py`.
  • `fetch_macro_context` — index futures / VIX / 10Y yield / commodities (the
    "what it means"), via `Ticker(sym).fast_info` (last + previous close →
    overnight % move). `fast_info` avoids the `.info` crumb-401 flakiness.
    Verify with `scripts/macro_probe.py`.

Best-effort by design: a per-item fetch error is skipped (an honest "no data"),
never a silent substitution of mock data — so a live brief describes a move
without a cause / drops a macro line rather than inventing one.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)


def _yfinance_available() -> bool:
    try:
        import yfinance  # noqa: F401

        return True
    except Exception:
        return False


def _parse_yf_news_item(r: dict[str, Any]) -> dict[str, Any] | None:
    """Normalise one yfinance news entry → {headline, source, ts, url} or None.

    Handles both the nested-`content` shape (current) and the flat shape (older).
    `ts` is an ISO-8601 UTC string (yfinance's `pubDate` already is; the legacy
    epoch `providerPublishTime` is converted) so a `since` lexicographic filter
    behaves like the mock path's.
    """
    if not isinstance(r, dict):
        return None
    c = r.get("content") if isinstance(r.get("content"), dict) else r
    title = c.get("title") or r.get("title")
    if not title:
        return None
    prov = c.get("provider")
    source = (
        (prov.get("displayName") if isinstance(prov, dict) else None)
        or r.get("publisher")
        or "Yahoo Finance"
    )
    ts = c.get("pubDate") or c.get("displayTime")
    if not ts and r.get("providerPublishTime"):
        try:
            ts = datetime.fromtimestamp(r["providerPublishTime"], tz=timezone.utc).isoformat()
        except (TypeError, ValueError, OSError):
            ts = None
    cu = c.get("canonicalUrl")
    url = (cu.get("url") if isinstance(cu, dict) else None) or r.get("link")
    return {"headline": title, "source": source, "ts": ts, "url": url}


def _merge_news(
    yf_by_ticker: dict[str, list[dict[str, Any]]],
    av_by_ticker: dict[str, list[dict[str, Any]]],
    limit: int,
) -> dict[str, list[dict[str, Any]]]:
    """Union yfinance + Alpha Vantage headlines per ticker (060).

    yfinance is the base; AV items are appended, then duplicates are dropped by
    (normalised headline, url host) — keeping the FIRST seen (yfinance wins ties,
    so a story present in both keeps yfinance's fields). Newest-first, capped at
    `limit` per symbol. Pure/deterministic (unit-tested)."""
    def _key(it: dict[str, Any]) -> tuple[str, str]:
        title = (it.get("headline") or "").strip().lower()
        url = it.get("url") or ""
        host = ""
        if url:
            try:
                from urllib.parse import urlparse

                host = urlparse(url).netloc.lower().removeprefix("www.")
            except ValueError:
                host = ""
        return (title, host)

    out: dict[str, list[dict[str, Any]]] = {}
    tickers = list(yf_by_ticker.keys()) + [k for k in av_by_ticker if k not in yf_by_ticker]
    for sym in tickers:
        merged: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for it in (yf_by_ticker.get(sym, []) + av_by_ticker.get(sym, [])):
            k = _key(it)
            if k in seen:
                continue
            seen.add(k)
            merged.append(it)
        merged.sort(key=lambda it: it.get("ts") or "", reverse=True)
        out[sym] = merged[:limit]
    return out


async def fetch_recent_news(
    tickers: list[str] | str,
    limit: int = 2,
    since: str | None = None,
    use_av: bool = False,
) -> dict[str, Any]:
    """REAL per-ticker headlines via yfinance. Always returns `is_mock: False`.

    Returns {"news_by_ticker": {SYM: [{headline, source, ts, url}, ...]},
             "is_mock": False, "source": "yfinance"} — newest first, capped at
    `limit` per symbol. If yfinance isn't importable, returns an explicit
    `error: "yfinance_unavailable"` (still `is_mock: False`) so the caller can
    tell "no news today" apart from "no news source".

    060 — `use_av` (default False) opts THIS caller into supplementing yfinance
    with Alpha Vantage's NEWS_SENTIMENT feed. Only the daily **briefing** passes
    `use_av=True`; the chat `get_company_news` tool leaves it False → yfinance-only
    (off AV's free-tier quota). yfinance stays the base; AV is best-effort — any AV
    failure/cap keeps the yfinance-only result. `source` becomes
    `"yfinance+alphavantage"` when AV actually contributed.
    """
    if isinstance(tickers, str):
        tickers = [tickers]
    syms = [t for t in tickers if t][:10]
    if not _yfinance_available():
        return {"news_by_ticker": {}, "is_mock": False, "error": "yfinance_unavailable"}
    import yfinance as yf

    out: dict[str, list[dict[str, Any]]] = {}
    for t in syms:
        try:
            raw = await asyncio.to_thread(lambda sym=t: yf.Ticker(sym).news) or []
        except Exception:  # noqa: BLE001 — per-ticker best-effort
            out[t.upper()] = []
            continue
        items = [p for p in (_parse_yf_news_item(r) for r in raw) if p]
        if since:
            items = [it for it in items if (it.get("ts") or "") >= since]
        items.sort(key=lambda it: it.get("ts") or "", reverse=True)
        out[t.upper()] = items[:limit]

    source = "yfinance"
    # 060 — supplement with Alpha Vantage (briefing only, best-effort). Any AV
    # error/cap is swallowed → the yfinance-only result stands (never breaks a brief).
    if use_av:
        try:
            import alphavantage_client as av

            if av.av_news_enabled():
                av_res = await av.fetch_news(syms, limit=limit, since=since)
                av_by = av_res.get("news_by_ticker", {})
                if any(av_by.values()):
                    out = _merge_news(out, av_by, limit)
                    source = "yfinance+alphavantage"
        except Exception as e:  # noqa: BLE001 — AV is a best-effort supplement
            log.info("news_context: alphavantage supplement skipped: %s", e)

    return {"news_by_ticker": out, "is_mock": False, "source": source}


# --- macro context (the "what it means" layer) --------------------------------

# (label, yfinance symbol, kind). `kind` drives the human `display`:
#   "move"      → quote the overnight % move (futures / commodities)
#   "level"     → quote the level + its % move (VIX — the spike is the story)
#   "pct_level" → the value IS a percent; quote it as a level (the 10Y yield)
_MACRO_SPEC: list[tuple[str, str, str]] = [
    ("S&P 500 futures", "ES=F", "move"),
    ("Nasdaq futures", "NQ=F", "move"),
    ("VIX", "^VIX", "level"),
    ("US 10Y yield", "^TNX", "pct_level"),
    ("Gold", "GC=F", "move"),
    ("WTI crude", "CL=F", "move"),
]


def _fast_info_quote(sym: str) -> tuple[float, float | None] | None:
    """(last_price, previous_close) for a symbol via yfinance fast_info, or None.

    Tolerates both fast_info access styles (mapping `.get('lastPrice')` and
    attribute `.last_price`) — they vary across yfinance versions.
    """
    import yfinance as yf

    fi = yf.Ticker(sym).fast_info
    last = prev = None
    for key, attr in (("lastPrice", "last_price"), ("previousClose", "previous_close")):
        val = None
        if hasattr(fi, "get"):
            try:
                val = fi.get(key)
            except Exception:  # noqa: BLE001
                val = None
        if val is None:
            val = getattr(fi, attr, None)
        if key == "lastPrice":
            last = val
        else:
            prev = val
    if last is None:
        return None
    try:
        return float(last), (float(prev) if prev is not None else None)
    except (TypeError, ValueError):
        return None


def _macro_display(label: str, kind: str, price: float, chg: float | None) -> str:
    if kind == "pct_level":
        return f"{label} {price:.2f}%"
    if kind == "level":
        return f"{label} {price:.2f}" + (f" ({chg:+.1f}%)" if chg is not None else "")
    return f"{label} {chg:+.2f}%" if chg is not None else f"{label} {price:.2f}"


async def fetch_macro_context() -> dict[str, Any]:
    """REAL market indicators via yfinance fast_info. Always `is_mock: False`.

    Returns {"indicators": [{label, symbol, price, change_pct, display}, ...],
             "is_mock": False, "source": "yfinance"} — only the indicators that
    fetched cleanly (best-effort; a failed symbol is skipped, not faked). Note
    there is deliberately NO Fed-events / earnings-calendar data: there's no
    reliable free source, and inventing a "FOMC at 14:00" is exactly the
    hallucination fix #1 caught.
    """
    if not _yfinance_available():
        return {"indicators": [], "is_mock": False, "error": "yfinance_unavailable"}

    def _collect() -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for label, sym, kind in _MACRO_SPEC:
            try:
                q = _fast_info_quote(sym)
            except Exception:  # noqa: BLE001 — per-symbol best-effort
                q = None
            if q is None:
                continue
            last, prev = q
            chg = round((last - prev) / prev * 100, 2) if prev else None
            out.append({
                "label": label,
                "symbol": sym,
                "price": round(last, 2),
                "change_pct": chg,
                "display": _macro_display(label, kind, last, chg),
            })
        return out

    indicators = await asyncio.to_thread(_collect)
    return {"indicators": indicators, "is_mock": False, "source": "yfinance"}
