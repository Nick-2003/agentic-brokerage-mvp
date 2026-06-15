"""Offline test for 044 — the /api/chart-data endpoint (in-app charts).

Fully offline: `_fetch_ohlcv` is monkeypatched (no network). Imports the
top-level `chart_api` (which imports `tools.technicals._fetch_ohlcv`), so run with
the 044 files applied (temp-apply→test→restore).

    backend/.venv/bin/python scripts/test_044_chart_data.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_repo_backend = next(
    (up / "backend" for up in _HERE.parents if (up / "backend" / "ibkr_flex.py").exists()),
    None,
)
if _repo_backend is not None:
    sys.path.insert(0, str(_repo_backend))

import pandas as pd  # installed transitively via yfinance
from fastapi import HTTPException

import chart_api  # noqa: E402

_PASS = 0
_FAIL = 0


def check(name: str, cond: bool) -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ✓ {name}")
    else:
        _FAIL += 1
        print(f"  ✗ {name}")


def _df(n: int = 250) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=n, freq="D")
    base = [7.0 + i * 0.01 for i in range(n)]
    return pd.DataFrame(
        {"Open": base, "High": [b + 0.05 for b in base], "Low": [b - 0.05 for b in base],
         "Close": base, "Volume": [1_000_000 + i for i in range(n)]},
        index=idx,
    )


async def main() -> int:
    print("chart_api — /api/chart-data")

    # _serialise_candles shape
    rows = chart_api._serialise_candles(_df(5))
    check("serialise → 5 candles with time/ohlcv keys",
          len(rows) == 5 and set(rows[0]) == {"time", "open", "high", "low", "close", "volume"})
    check("time is YYYY-MM-DD", rows[0]["time"] == "2025-01-01")

    calls = {"n": 0}

    async def _stub_ok(ticker, timeframe):
        calls["n"] += 1
        return _df(250), "HKD"

    async def _stub_none(ticker, timeframe):
        return None, None

    # 1. Happy path — HK ticker, candles + HK$ currency.
    chart_api._fetch_ohlcv = _stub_ok  # type: ignore[assignment]
    chart_api._cache.clear()
    r = await chart_api.chart_data(ticker="1398.HK", timeframe="1D")
    check("currency HK$ (from HKD)", r["currency"] == "HK$")
    check("candles present + count matches", r["count"] == len(r["candles"]) == 250)
    check("ticker uppercased + echoed", r["ticker"] == "1398.HK" and r["timeframe"] == "1D")

    # 2. Cache — second call serves from cache (no extra _fetch_ohlcv).
    calls["n"] = 0
    chart_api._cache.clear()
    await chart_api.chart_data(ticker="AAPL")
    await chart_api.chart_data(ticker="AAPL")
    check("second call cached (1 underlying fetch)", calls["n"] == 1)

    # 3. No data → empty candles (frontend falls back to SVG), not an error.
    chart_api._fetch_ohlcv = _stub_none  # type: ignore[assignment]
    chart_api._cache.clear()
    r = await chart_api.chart_data(ticker="NOPE")
    check("no data → candles [] + count 0", r["candles"] == [] and r["count"] == 0)

    # 4. Missing ticker → 422.
    try:
        await chart_api.chart_data(ticker="")
        raised = False
    except HTTPException as e:
        raised = e.status_code == 422
    check("empty ticker → HTTPException 422", raised)

    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
