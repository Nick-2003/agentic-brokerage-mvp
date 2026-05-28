#!/usr/bin/env python3
"""Deterministic unit test for Proposal 003 (backend/tools/market.py mock-data fixes).

Unlike scripts/smoke_test.sh (which needs a running backend), this imports the
tools package directly — no server required.

Run with the backend venv:
    backend/.venv/bin/python scripts/test_proposal_003.py

Exit code 0 = all pass, 1 = a check failed.
"""
import asyncio, os, sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, "backend"))
from tools import market  # noqa: E402

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results = []

def check(name, cond, detail=""):
    print(f"  [{PASS if cond else FAIL}] {name}" + (f"  — {detail}" if detail else ""))
    results.append(bool(cond))

async def test_bug_a():
    print("\n=== Bug A — get_quote failure honesty ===")
    orig = market._fetch_yfinance_quote
    os.environ.pop("USE_MOCK_MARKET", None)

    async def raising(_t): raise RuntimeError("HTTP 401 Invalid Crumb")
    market._fetch_yfinance_quote = raising
    q = (await market.get_quote({"tickers": ["NVDA"]}, "demo"))["quotes"][0]
    check("raise → yfinance_fetch_failed", q.get("error") == "yfinance_fetch_failed", str(q))
    check("raise → source=yfinance_error", q.get("source") == "yfinance_error")
    check("raise → NOT silently mock", q.get("source") != "mock")

    async def none_q(_t): return None
    market._fetch_yfinance_quote = none_q
    q = (await market.get_quote({"tickers": ["NVDA"]}, "demo"))["quotes"][0]
    check("None → falls to mock", q.get("source") == "mock", str(q))
    check("None → no error field", "error" not in q)

    async def ok_q(t): return {"ticker": t.upper(), "name": "T", "currency": "$",
        "price": 123.45, "change": 1.0, "change_pct": 0.8, "after_hours": 123.5, "after_hours_pct": 0.04}
    market._fetch_yfinance_quote = ok_q
    q = (await market.get_quote({"tickers": ["NVDA"]}, "demo"))["quotes"][0]
    check("quote → used verbatim", q.get("source") == "yfinance" and q.get("price") == 123.45, str(q))

    os.environ["USE_MOCK_MARKET"] = "1"
    market._fetch_yfinance_quote = raising
    r = await market.get_quote({"tickers": ["NVDA"]}, "demo")
    check("USE_MOCK_MARKET=1 → mock", r["is_mock"] and r["quotes"][0]["source"] == "mock", str(r["is_mock"]))
    os.environ.pop("USE_MOCK_MARKET", None)
    market._fetch_yfinance_quote = orig

async def test_bug_b():
    print("\n=== Bug B — get_company_news per-call timestamps ===")
    now = datetime.now(timezone.utc)

    items = (await market.get_company_news({"tickers": ["NVDA"], "limit": 10}, "demo"))["news_by_ticker"]["NVDA"]
    fresh = all(timedelta(0) <= (now - datetime.fromisoformat(i["ts"])) <= timedelta(hours=25) for i in items)
    check("timestamps fresh (call-time)", bool(items) and fresh, f"{len(items)} items")

    since_1h = (now - timedelta(hours=1)).isoformat()
    items = (await market.get_company_news({"tickers": ["NVDA"], "since": since_1h, "limit": 10}, "demo"))["news_by_ticker"]["NVDA"]
    check("since=1h ago → NON-empty (bug B fixed)", len(items) >= 1, f"{len(items)} items")

    items = (await market.get_company_news({"tickers": ["NVDA"], "since": now.isoformat()}, "demo"))["news_by_ticker"]["NVDA"]
    check("since=now → empty (005 still needed)", items == [], f"{len(items)} items")

    items = (await market.get_company_news({"tickers": ["NVDA"], "since": (now - timedelta(days=2)).isoformat(), "limit": 10}, "demo"))["news_by_ticker"]["NVDA"]
    check("since=2d ago → all 4", len(items) == 4, f"{len(items)} items")

    items = (await market.get_company_news({"tickers": ["F"], "since": since_1h}, "demo"))["news_by_ticker"]["F"]
    check("unknown ticker F → empty, no crash", items == [])

async def main():
    print("Proposal 003 verification —", datetime.now(timezone.utc).isoformat())
    await test_bug_a()
    await test_bug_b()
    print("=" * 48)
    ok = all(results)
    print(f"RESULT: {sum(results)}/{len(results)} passed", "— ALL PASS ✅" if ok else "— FAILED ❌")
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    asyncio.run(main())