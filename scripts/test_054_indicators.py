#!/usr/bin/env python3
"""Offline test for Proposal 054 — period-parameterised indicators + Bollinger.

Self-contained: temp-applies the proposal's tools/technicals.py over the live file,
exercises the pure parse/translate/renderable/compute helpers (no network), then
restores the live file in a finally.

Run with the backend venv:
    backend/.venv/bin/python scripts/test_054_indicators.py
"""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROP_BACKEND = os.path.normpath(os.path.join(HERE, os.pardir, "backend"))


def _find_repo(start: str) -> str:
    d = start
    while True:
        if os.path.isfile(os.path.join(d, "backend", "news_context.py")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            raise RuntimeError(f"repo root not found above {start}")
        d = parent


REPO = _find_repo(HERE)
BACKEND = os.path.join(REPO, "backend")
LIVE = os.path.join(BACKEND, "tools", "technicals.py")
PROP = os.path.join(PROP_BACKEND, "tools", "technicals.py")

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results: list[bool] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{PASS if cond else FAIL}] {name}" + (f"  — {detail}" if detail else ""))
    results.append(bool(cond))


def run(technicals):
    import pandas as pd

    print("\n=== _parse_indicator ===")
    pi = technicals._parse_indicator
    check("SMA 20", pi("SMA 20") == ("SMA", 20))
    check("ema 50 (case-insensitive)", pi("ema 50") == ("EMA", 50))
    check("RSI 9", pi("RSI 9") == ("RSI", 9))
    check("BB 20", pi("BB 20") == ("BB", 20))
    check("VWAP → None", pi("VWAP") is None)
    check("garbage → None", pi("not an indicator") is None)

    print("\n=== _translate_indicator (arbitrary periods) ===")
    ti = technicals._translate_indicator
    check("SMA 100 → length 100", ti("SMA 100") == ("Moving Average Simple", {"length": 100}))
    check("EMA 50 → length 50", ti("EMA 50") == ("Moving Average Exponential", {"length": 50}))
    check("BB 20 → length+mult", ti("BB 20") == ("Bollinger Bands", {"length": 20, "mult": 2}))
    check("exact 'RSI 14' still maps", ti("RSI 14") == ("Relative Strength Index", {"length": 14}))
    try:
        ti("WUTANG 5")
        check("unknown raises KeyError", False)
    except KeyError:
        check("unknown raises KeyError", True)

    print("\n=== _renderable_applied (VWAP dropped, arbitrary kept, fallback) ===")
    ra = technicals._renderable_applied
    check("keeps SMA/RSI/BB, drops VWAP + junk, preserves order",
          ra(["SMA 20", "RSI 9", "VWAP", "BB 20", "junk"]) == ["SMA 20", "RSI 9", "BB 20"],
          str(ra(["SMA 20", "RSI 9", "VWAP", "BB 20", "junk"])))
    check("dedups", ra(["SMA 20", "SMA 20"]) == ["SMA 20"])
    check("empty → SMA 50/200 fallback", ra([]) == ["SMA 50", "SMA 200"])
    check("only-VWAP → fallback", ra(["VWAP"]) == ["SMA 50", "SMA 200"])

    print("\n=== _compute_indicators (staples + requested periods + BB) ===")
    closes = pd.Series([100 + i * 0.5 + (i % 7) for i in range(260)], dtype=float)
    vals = technicals._compute_indicators(closes, ["SMA 100", "EMA 50", "RSI 9", "BB 20"])
    check("staple SMA 50 present", "SMA 50" in vals)
    check("staple SMA 200 present", "SMA 200" in vals)
    check("staple RSI 14 present", "RSI 14" in vals)
    check("requested SMA 100 present", "SMA 100" in vals, str("SMA 100" in vals))
    check("requested EMA 50 present", "EMA 50" in vals)
    check("requested RSI 9 present", "RSI 9" in vals)
    check("BB 20 upper/mid/lower present",
          all(f"BB 20 {x}" in vals for x in ("upper", "mid", "lower")),
          str([k for k in vals if k.startswith("BB ")]))
    check("BB upper > mid > lower", vals["BB 20 upper"] > vals["BB 20 mid"] > vals["BB 20 lower"])
    check("MACD present", "MACD" in vals)
    check("RSI in [0,100]", 0 <= vals["RSI 14"] <= 100, str(vals["RSI 14"]))

    print("\n=== default (no indicators) still computes the staples ===")
    base = technicals._compute_indicators(closes)
    check("defaults give SMA 10/20/50/200", all(f"SMA {w}" in base for w in (10, 20, 50, 200)))
    check("defaults give EMA 20 + RSI 14", "EMA 20" in base and "RSI 14" in base)


def main() -> int:
    backup = None
    try:
        if os.path.isfile(PROP) and os.path.abspath(PROP) != os.path.abspath(LIVE):
            with open(LIVE, "rb") as fh:
                backup = fh.read()
            shutil.copyfile(PROP, LIVE)
        sys.path.insert(0, BACKEND)
        from tools import technicals  # noqa: E402
        run(technicals)
    finally:
        if backup is not None:
            with open(LIVE, "wb") as fh:
                fh.write(backup)

    total, passed = len(results), sum(results)
    print(f"\n{'='*48}\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
