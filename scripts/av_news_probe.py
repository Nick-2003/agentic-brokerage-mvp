#!/usr/bin/env python3
"""Alpha Vantage NEWS_SENTIMENT probe (060) — verify the real response shape
before trusting the field mappings in backend/alphavantage_client.py.

AV's `feed[]` / `ticker_sentiment[]` field names couldn't be verified without a
live key at draft time. Run this with a real key to dump the actual top-level
keys, one raw feed item, and the normalised {SYM: [...]} the client produces.

Usage (backend venv):
    ALPHAVANTAGE_API_KEY=your_key backend/.venv/bin/python scripts/av_news_probe.py AAPL,MSFT

Read-only; makes ONE GET call (mind the 25/day free-tier limit).
"""
import asyncio
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, "backend"))

import alphavantage_client as av  # noqa: E402


async def probe(tickers: list[str]) -> None:
    if not os.getenv("ALPHAVANTAGE_API_KEY"):
        print("Set ALPHAVANTAGE_API_KEY first (see module docstring).")
        sys.exit(1)

    # Force-enable regardless of USE_MOCK_NEWS for the probe.
    os.environ.pop("USE_MOCK_NEWS", None)

    import httpx

    params = {
        "function": "NEWS_SENTIMENT",
        "tickers": ",".join(tickers),
        "apikey": os.environ["ALPHAVANTAGE_API_KEY"],
        "sort": "LATEST",
        "limit": "50",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(av.AV_URL, params=params)
    print(f"HTTP {resp.status_code}")
    data = resp.json()

    print("\n=== top-level keys ===")
    print(list(data.keys()) if isinstance(data, dict) else type(data))

    feed = data.get("feed") if isinstance(data, dict) else None
    if not feed:
        # Throttle / error bodies live here.
        print("\n=== no feed — raw body (first 500 chars) ===")
        print(json.dumps(data)[:500])
        return

    print(f"\n=== feed length: {len(feed)} ===")
    print("\n=== raw feed[0] keys ===")
    print(list(feed[0].keys()))
    print("\n=== raw feed[0].ticker_sentiment[0] ===")
    ts = feed[0].get("ticker_sentiment") or []
    print(json.dumps(ts[0], indent=2) if ts else "(none)")

    print("\n=== normalised _feed_to_news_by_ticker ===")
    norm = av._feed_to_news_by_ticker(data, tickers, limit=3, since=None)
    print(json.dumps(norm, indent=2)[:1500])


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "AAPL,MSFT"
    asyncio.run(probe([t.strip().upper() for t in arg.split(",") if t.strip()]))
