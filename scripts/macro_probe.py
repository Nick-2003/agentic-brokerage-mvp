"""Live probe for the W2 macro layer (news_context.fetch_macro_context).

Fetches the real index-futures / VIX / 10Y / commodity indicators via yfinance
and prints them as the briefing would see them. The "verify against the live
dependency" step for the macro fetch (mirrors news_probe.py).

    backend/.venv/bin/python scripts/macro_probe.py
"""
from __future__ import annotations

import asyncio
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
    print(f"yfinance available: {nc._yfinance_available()}")
    res = await nc.fetch_macro_context()
    print(f"is_mock={res['is_mock']}  source={res.get('source')}  error={res.get('error')}")
    print(f"\n{len(res['indicators'])} indicator(s):")
    for ind in res["indicators"]:
        print(f"  • {ind['display']:<32}  (price={ind['price']}  chg%={ind['change_pct']}  {ind['symbol']})")


if __name__ == "__main__":
    asyncio.run(main())
