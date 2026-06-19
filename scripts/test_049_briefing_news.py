#!/usr/bin/env python3
"""Offline test for Proposal 049 — briefing news window + Flex→yfinance symbol resolution.

Self-contained: temp-applies the proposal's briefing.py over the live file, imports,
runs with `fetch_recent_news`/`fetch_macro_context` STUBBED (no network), then restores
the live file in a finally.

Run with the backend venv:
    backend/.venv/bin/python proposed_changes/049-briefing-news-window/scripts/test_049_briefing_news.py
"""
import asyncio
import os
import shutil
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))


def _expected_since(briefing, as_of: str) -> str:
    """Expected news cutoff = as_of − _NEWS_MAX_AGE_DAYS, computed by DAY-DIFFERENCE
    (not a hardcoded calendar date) so the test tracks whatever window is
    configured instead of breaking when the default changes."""
    d = datetime.strptime(as_of, "%Y-%m-%d") - timedelta(days=briefing._NEWS_MAX_AGE_DAYS)
    return d.strftime("%Y-%m-%d")


def _find_repo(start: str) -> str:
    # Anchor on backend/news_context.py — NOT in this proposal's mirror tree.
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
LIVE = os.path.join(BACKEND, "briefing.py")
PROP = os.path.join(REPO, "proposed_changes", "049-briefing-news-window", "backend", "briefing.py")

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results: list[bool] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{PASS if cond else FAIL}] {name}" + (f"  — {detail}" if detail else ""))
    results.append(bool(cond))


async def run(briefing):
    print("\n=== _yf_symbol (Flex → yfinance) ===")
    f = briefing._yf_symbol
    check("US passes through", f("AAPL", "USD") == "AAPL")
    check("None currency passes through", f("TSLA", None) == "TSLA")
    check("HKD numeric → zero-padded .HK", f("700", "HKD") == "0700.HK", f("700", "HKD"))
    check("HKD already 4-digit → .HK", f("1398", "HKD") == "1398.HK", f("1398", "HKD"))
    check("already suffixed untouched", f("0700.HK", "HKD") == "0700.HK")
    check("HKD non-numeric passes through", f("TCEHY", "HKD") == "TCEHY")
    check("empty → empty", f("", "HKD") == "")

    print(f"\n=== news window = {briefing._NEWS_MAX_AGE_DAYS} day(s); cutoff by day-difference ===")
    check("window is a positive int",
          isinstance(briefing._NEWS_MAX_AGE_DAYS, int) and briefing._NEWS_MAX_AGE_DAYS > 0,
          str(briefing._NEWS_MAX_AGE_DAYS))
    _as_of = "2026-06-16"
    _exp = _expected_since(briefing, _as_of)
    check(f"_news_since({_as_of}) == as_of − {briefing._NEWS_MAX_AGE_DAYS}d ({_exp})",
          briefing._news_since(_as_of) == _exp,
          f"{briefing._news_since(_as_of)} (expected {_exp})")

    print("\n=== gather_market_context re-keys HK news back to the Flex symbol ===")
    captured = {}

    async def fake_news(tickers, limit=2, since=None):
        captured["tickers"] = list(tickers)
        captured["limit"] = limit
        captured["since"] = since
        # yfinance returns news keyed by the YF symbol we asked for.
        return {"news_by_ticker": {"0700.HK": [{"headline": "Tencent buyback", "source": "Reuters",
                                                "ts": "2026-06-15T00:00:00+00:00"}]},
                "is_mock": False, "source": "yfinance"}

    async def fake_macro():
        return {"indicators": [], "is_mock": False}

    briefing.fetch_recent_news = fake_news
    briefing.fetch_macro_context = fake_macro

    snapshot = {
        "is_mock": False,
        "as_of": "2026-06-16",
        "positions": [
            {"symbol": "700", "currency": "HKD", "day_pnl": -1200.0},
        ],
    }
    ctx = await briefing.gather_market_context(snapshot)
    check("fetched by the yfinance symbol", captured.get("tickers") == ["0700.HK"], str(captured.get("tickers")))
    check(f"per-ticker limit = {briefing._NEWS_PER_TICKER}",
          captured.get("limit") == briefing._NEWS_PER_TICKER, str(captured.get("limit")))
    check(f"since uses the window (as_of − {briefing._NEWS_MAX_AGE_DAYS}d)",
          captured.get("since") == _expected_since(briefing, "2026-06-16"),
          str(captured.get("since")))
    check("news re-keyed back to Flex symbol '700'", "700" in ctx["news_by_ticker"],
          str(list(ctx["news_by_ticker"])))
    check("headline preserved",
          ctx["news_by_ticker"].get("700", [{}])[0].get("headline") == "Tencent buyback")


def main() -> int:
    backup = None
    try:
        if os.path.isfile(PROP) and os.path.abspath(PROP) != os.path.abspath(LIVE):
            with open(LIVE, "rb") as fh:
                backup = fh.read()
            shutil.copyfile(PROP, LIVE)
        sys.path.insert(0, BACKEND)
        import briefing  # noqa: E402
        asyncio.run(run(briefing))
    finally:
        if backup is not None:
            with open(LIVE, "wb") as fh:
                fh.write(backup)

    total, passed = len(results), sum(results)
    print(f"\n{'='*48}\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
