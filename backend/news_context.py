"""Real per-ticker news for the W2 briefing's "why it moved" layer.

A SYSTEM-SIDE helper (like `ibkr_flex.py` / `briefing.py`), deliberately NOT an
agent tool and NOT part of the `tools/` registry — the briefing is a system job
(SECURITY threat 1). Keeping it out of `tools/` also leaves the chat product's
`get_company_news` mock tool (and its `test_P1_003` regression) completely
untouched: the briefing fetches REAL headlines here for live briefs, and uses the
mock tool only for labelled demos.

Source = yfinance (already a backend dependency, no API tier — so it covers the
small/mid-caps the FMP free tier may not). yfinance's `.news` shape changed across
versions: items are now {"id", "content": {"title", "pubDate", "provider":
{"displayName"}, "canonicalUrl": {"url"}}} (older versions were flat: {"title",
"publisher", "providerPublishTime", "link"}). `_parse_yf_news_item` tolerates
both — verify with `scripts/news_probe.py` against a real ticker.

Best-effort by design: a per-ticker fetch error yields an empty list for that name
(an honest "no headlines"), never a silent substitution of mock data — so a live
brief describes the move without a cause rather than inventing one.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any


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


async def fetch_recent_news(
    tickers: list[str] | str, limit: int = 2, since: str | None = None
) -> dict[str, Any]:
    """REAL per-ticker headlines via yfinance. Always returns `is_mock: False`.

    Returns {"news_by_ticker": {SYM: [{headline, source, ts, url}, ...]},
             "is_mock": False, "source": "yfinance"} — newest first, capped at
    `limit` per symbol. If yfinance isn't importable, returns an explicit
    `error: "yfinance_unavailable"` (still `is_mock: False`) so the caller can
    tell "no news today" apart from "no news source".
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
    return {"news_by_ticker": out, "is_mock": False, "source": "yfinance"}
