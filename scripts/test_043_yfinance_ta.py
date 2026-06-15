"""Offline test for 043 — yfinance-computed technicals (HK + US coverage).

Fully offline: indicator math runs on a synthetic pandas series; the yfinance
fetch is exercised with a STUBBED `yfinance` module (no network); routing is
verified with stubbed source functions. Like 039/040, imports the package-relative
`tools.technicals`, so run with the 043 files applied (temp-apply→test→restore).

    backend/.venv/bin/python scripts/test_043_yfinance_ta.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import types
from pathlib import Path

_HERE = Path(__file__).resolve()
_repo_backend = next(
    (up / "backend" for up in _HERE.parents if (up / "backend" / "ibkr_flex.py").exists()),
    None,
)
if _repo_backend is not None:
    sys.path.insert(0, str(_repo_backend))

import pandas as pd  # installed transitively via yfinance

from tools import technicals as T  # noqa: E402

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


def _set(var: str, val: str | None) -> None:
    if val is None:
        os.environ.pop(var, None)
    else:
        os.environ[var] = val


def _uptrend(n: int = 260) -> pd.Series:
    # Monotonic-ish uptrend: all gains → RSI≈100, MACD>0, SMAs below last price.
    return pd.Series([100.0 + i * 0.5 for i in range(n)])


def test_indicator_math() -> None:
    print("indicator math (_compute_indicators / _swing_levels / _ccy_symbol)")
    closes = _uptrend()
    ind = T._compute_indicators(closes)
    last = float(closes.iloc[-1])
    check("SMA 10/20/50/200 all present", all(f"SMA {w}" in ind for w in (10, 20, 50, 200)))
    check("SMA 200 below price in an uptrend", ind["SMA 200"] < last)
    check("RSI 14 present and high (~100) for all-gains", 95 <= ind["RSI 14"] <= 100)
    # MACD line is positive in an uptrend (fast EMA leads); the histogram settles
    # to ~0 on a perfectly linear ramp, so only assert it's present, not its sign.
    check("MACD line positive in an uptrend; hist present",
          ind["MACD"] > 0 and "MACD hist" in ind)

    # Short history → only the indicators with enough bars appear.
    short = T._compute_indicators(_uptrend(30))
    check("short history: SMA 200 absent, SMA 20 present", "SMA 200" not in short and "SMA 20" in short)

    levels = T._swing_levels(_uptrend(), _uptrend(), last)
    check("swing resistance ≥ price, support ≤ price",
          levels["resistance"][0] >= last and levels["support"][0] <= last)

    check("ccy symbol HKD→HK$, USD→$, unknown passthrough",
          T._ccy_symbol("HKD") == "HK$" and T._ccy_symbol("USD") == "$" and T._ccy_symbol(None) == "$")


async def test_yfinance_path() -> None:
    print("_yfinance_technical_levels with a stubbed yfinance (no network)")
    n = 250
    idx = pd.date_range("2025-01-01", periods=n, freq="D")
    closes = [7.0 + i * 0.01 for i in range(n)]
    df = pd.DataFrame(
        {"Open": closes, "High": [c + 0.05 for c in closes],
         "Low": [c - 0.05 for c in closes], "Close": closes,
         "Volume": [1_000_000] * n},
        index=idx,
    )

    class _FakeTicker:
        def __init__(self, sym): self.sym = sym
        def history(self, period=None, interval=None): return df
        @property
        def fast_info(self): return {"currency": "HKD"}

    fake = types.ModuleType("yfinance")
    fake.Ticker = _FakeTicker  # type: ignore[attr-defined]
    sys.modules["yfinance"] = fake
    try:
        r = await T._yfinance_technical_levels("1398.HK", "1D", ["SMA 50", "SMA 200"])
    finally:
        sys.modules.pop("yfinance", None)

    check("no error", "error" not in r)
    check("currency HK$ (from HKD fast_info)", r.get("currency") == "HK$")
    check("current_price = last close", r.get("current_price") == round(closes[-1], 2))
    check("indicator_values has SMA 200 + RSI 14 + MACD",
          all(k in r.get("indicator_values", {}) for k in ("SMA 200", "RSI 14", "MACD")))
    check("source yfinance_computed, is_mock False", r.get("source") == "yfinance_computed" and r.get("is_mock") is False)
    check("bars reported", r.get("bars") == n)
    check("key_levels present", bool(r.get("key_levels", {}).get("resistance")))


async def test_routing() -> None:
    print("get_technical_levels routing (mock / TradingView / yfinance)")

    async def _yf_stub(t, tf, ind): return {"source": "YF_STUB", "ticker": t}
    async def _tv_stub(t, tf, ind): return {"source": "TV_STUB", "ticker": t}

    orig_yf, orig_tv = T._yfinance_technical_levels, T._real_technical_levels
    orig_avail, orig_tvcfg = T._yfinance_ta_available, T._tradingview_configured
    T._yfinance_technical_levels = _yf_stub          # type: ignore[assignment]
    T._real_technical_levels = _tv_stub              # type: ignore[assignment]
    T._yfinance_ta_available = lambda: True          # type: ignore[assignment]
    try:
        # 1. mock mode + covered US ticker → deterministic mock
        _set("USE_MOCK_TA", "1")
        r = await T.get_technical_levels({"ticker": "NVDA"}, "u")
        check("mock + NVDA → mock (is_mock True)", r.get("is_mock") is True and "error" not in r)

        # 2. mock mode + HK (not covered) → falls through to yfinance
        r = await T.get_technical_levels({"ticker": "1398.HK"}, "u")
        check("mock + 1398.HK → yfinance fallthrough", r.get("source") == "YF_STUB")

        # 3. real mode + TradingView configured → TradingView path
        _set("USE_MOCK_TA", "0")
        T._tradingview_configured = lambda: True     # type: ignore[assignment]
        r = await T.get_technical_levels({"ticker": "1398.HK"}, "u")
        check("real + TV configured → TradingView", r.get("source") == "TV_STUB")

        # 4. real mode + no TradingView → yfinance
        T._tradingview_configured = lambda: False    # type: ignore[assignment]
        r = await T.get_technical_levels({"ticker": "1398.HK"}, "u")
        check("real + no TV → yfinance", r.get("source") == "YF_STUB")
    finally:
        T._yfinance_technical_levels, T._real_technical_levels = orig_yf, orig_tv
        T._yfinance_ta_available, T._tradingview_configured = orig_avail, orig_tvcfg
        _set("USE_MOCK_TA", None)


async def main() -> int:
    test_indicator_math()
    await test_yfinance_path()
    await test_routing()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
