#!/usr/bin/env python3
"""FMP field-name probe — confirm the real response shapes before trusting the
defensive mappings in backend/fmp_client.py (proposal 006).

FMP's docs block automated fetch and the field names couldn't be verified at
draft time, so fmp_client.py uses _pick(...) with several candidate keys per
value. Run this with a real key to print the ACTUAL keys each endpoint returns,
then tighten the candidate lists if anything is mapping to None.

Usage:
    FMP_API_KEY=your_key backend/.venv/bin/python scripts/fmp_probe.py AAPL

Prints, per endpoint: the raw top-level keys + the values fmp_client maps out.
Read-only; makes ~7 GET calls (mind free-tier rate limits).
"""
import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, "backend"))

import fmp_client as fc  # noqa: E402


async def probe(symbol: str):
    if not os.getenv("FMP_API_KEY"):
        print("Set FMP_API_KEY first:  FMP_API_KEY=... backend/.venv/bin/python scripts/fmp_probe.py AAPL")
        sys.exit(1)

    # 1) raw keys per endpoint — so you can see FMP's actual field names
    raw_calls = {
        "profile": ("profile", {"symbol": symbol}),
        "ratios-ttm": ("ratios-ttm", {"symbol": symbol}),
        "financial-growth": ("financial-growth", {"symbol": symbol, "period": "annual", "limit": 1}),
        "price-target-consensus": ("price-target-consensus", {"symbol": symbol}),
        "grades-consensus": ("grades-consensus", {"symbol": symbol}),
        "stock-peers": ("stock-peers", {"symbol": symbol}),
    }
    print(f"\n===== RAW FMP RESPONSE KEYS for {symbol} =====")
    for label, (path, params) in raw_calls.items():
        try:
            data = await fc._get(path, params)
            row = fc._first(data)
            keys = sorted(row.keys()) if isinstance(row, dict) else f"(list len {len(data)})"
            print(f"\n[{label}] /{path}")
            print(f"  keys: {keys}")
        except fc.FMPError as e:
            print(f"\n[{label}] /{path}  → FMPError: {e}")

    # 2) normalised output — what fmp_client hands to research.py (None = mapping miss)
    print(f"\n===== NORMALISED (what research.py receives) for {symbol} =====")
    for name, coro in [
        ("profile()", fc.profile(symbol)),
        ("ratios_ttm()", fc.ratios_ttm(symbol)),
        ("financial_growth()", fc.financial_growth(symbol)),
        ("price_target_consensus()", fc.price_target_consensus(symbol)),
        ("grades_consensus()", fc.grades_consensus(symbol)),
    ]:
        try:
            out = await coro
            misses = [k for k, v in out.items() if v is None]
            flag = f"  ⚠ None for: {misses}" if misses else "  ✓ all mapped"
            print(f"\n{name}: {out}{flag}")
        except fc.FMPError as e:
            print(f"\n{name} → FMPError: {e}")

    try:
        peers = await fc.stock_peers(symbol)
        print(f"\nstock_peers(): {peers}")
        filings = await fc.sec_filings(symbol, 3)
        print(f"sec_filings(): {filings}")
    except fc.FMPError as e:
        print(f"\npeers/filings → FMPError: {e}")


if __name__ == "__main__":
    sym = sys.argv[1].upper() if len(sys.argv) > 1 else "AAPL"
    asyncio.run(probe(sym))
    print("\nDone.")