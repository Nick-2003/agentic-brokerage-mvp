#!/usr/bin/env python3
"""Seed the dedicated SAMPLE Alpaca paper account with a few holdings (053).

A fresh Alpaca paper account starts at virtual $100k cash and NO positions, so the
guest "Sample portfolio" would look empty. Run this ONCE (during US market hours,
so market orders fill) to give it a realistic book. Idempotent-ish: it only buys
symbols not already held.

Setup:
  1. Create a DEDICATED Alpaca paper account (separate from your operator ALPACA_*).
  2. Generate its PAPER API key + secret.
  3. Export them, then run:
       export SAMPLE_ALPACA_API_KEY=PK...   SAMPLE_ALPACA_API_SECRET=...
       backend/.venv/bin/python .proposed_changes/053-sample-portfolio/scripts/seed_sample_alpaca.py

Needs `alpaca-py` (already a backend dep). Paper only — never live.
"""
import os
import sys

# A small, recognizable demo book (qty chosen to look like a ~5-figure account).
_SEED = [("NVDA", 8), ("AAPL", 20), ("MSFT", 10), ("TSLA", 12), ("AMD", 25)]


def main() -> int:
    key = os.getenv("SAMPLE_ALPACA_API_KEY", "").strip()
    secret = os.getenv("SAMPLE_ALPACA_API_SECRET", "").strip()
    if not key or not secret or key.endswith("REPLACE"):
        print("Set SAMPLE_ALPACA_API_KEY and SAMPLE_ALPACA_API_SECRET first.", file=sys.stderr)
        return 2

    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import MarketOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce

    client = TradingClient(api_key=key, secret_key=secret, paper=True)
    held = {p.symbol for p in client.get_all_positions()}
    print(f"Already held: {sorted(held) or '(none)'}")

    for sym, qty in _SEED:
        if sym in held:
            print(f"  skip {sym} (already held)")
            continue
        try:
            client.submit_order(MarketOrderRequest(
                symbol=sym, qty=qty, side=OrderSide.BUY, time_in_force=TimeInForce.DAY,
            ))
            print(f"  bought {qty} {sym}")
        except Exception as e:  # noqa: BLE001 — surface per-symbol, keep going
            print(f"  FAILED {sym}: {e}", file=sys.stderr)

    print("Done. Orders fill during US market hours; check the Alpaca paper dashboard.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
