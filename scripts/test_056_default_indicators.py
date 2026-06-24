#!/usr/bin/env python3
"""Offline test for Proposal 056 — chart default = NO indicators.

Temp-applies the proposal's tools/technicals.py, asserts the no-default behavior,
restores. Run with the backend venv:
    backend/.venv/bin/python .proposed_changes/056-chart-default-no-indicators/scripts/test_056_default_indicators.py
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
    ra = technicals._renderable_applied
    print("\n=== _renderable_applied: NO default (056) ===")
    check("empty → [] (no SMA 50/200 fallback)", ra([]) == [], str(ra([])))
    check("None → []", ra(None) == [])
    check("only VWAP/junk → []", ra(["VWAP", "junk"]) == [], str(ra(["VWAP", "junk"])))
    print("\n=== explicit requests still honored ===")
    check("SMA 20 + RSI 9 preserved", ra(["SMA 20", "RSI 9"]) == ["SMA 20", "RSI 9"])
    check("BB 20 preserved", ra(["BB 20"]) == ["BB 20"])

    print("\n=== tool schema default is [] ===")
    from tools import TOOL_REGISTRY  # noqa: E402
    spec = TOOL_REGISTRY["get_technical_levels"]["input_schema"]
    check("indicators default == []",
          spec["properties"]["indicators"].get("default") == [],
          str(spec["properties"]["indicators"].get("default")))


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
