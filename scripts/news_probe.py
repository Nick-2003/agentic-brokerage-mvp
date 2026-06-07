"""Live probe for the W2 real-news layer (news_context.fetch_recent_news).

Dumps yfinance's raw `.news` shape for the first ticker (so the defensive parse
in `_parse_yf_news_item` can be reconciled if Yahoo changes the shape again), then
prints the normalised headlines per ticker. Pass tickers as args; defaults to the
live IBKR account's movers.

    backend/.venv/bin/python scripts/news_probe.py EOSE GWRE NBIS CLSK
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_COLOCATED_BACKEND = _HERE.parents[1] / "backend"
_repo_backend = next(
    (up / "backend" for up in _HERE.parents if (up / "backend" / "ibkr_flex.py").exists()),
    None,
)
if _repo_backend is not None:
    sys.path.insert(0, str(_repo_backend))
sys.path.insert(0, str(_COLOCATED_BACKEND))

import news_context as nc  # noqa: E402


async def main() -> None:
    tickers = sys.argv[1:] or ["EOSE", "GWRE", "NBIS", "CLSK"]
    print(f"yfinance available: {nc._yfinance_available()}")

    # Raw shape dump for the first ticker — the "verify against the live dep" step.
    if nc._yfinance_available():
        import yfinance as yf

        raw = await asyncio.to_thread(lambda: yf.Ticker(tickers[0]).news) or []
        print(f"\n=== raw yfinance .news for {tickers[0]} (count {len(raw)}) ===")
        if raw:
            print("top-level keys:", list(raw[0].keys()))
            print(json.dumps(raw[0], default=str, indent=2)[:1400])

    res = await nc.fetch_recent_news(tickers, limit=3)
    print("\n=== normalised (fetch_recent_news) ===")
    print(f"is_mock={res['is_mock']}  source={res.get('source')}  error={res.get('error')}")
    for sym, items in res["news_by_ticker"].items():
        print(f"\n{sym}: {len(items)} headline(s)")
        for it in items:
            print(f"  • [{it['ts']}] {it['headline']}  — {it['source']}")


if __name__ == "__main__":
    asyncio.run(main())
