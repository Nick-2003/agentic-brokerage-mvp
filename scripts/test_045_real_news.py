#!/usr/bin/env python3
"""Offline unit test for Proposal 045 — route get_company_news / get_macro_snapshot
to the real news_context (yfinance) engine, mock-first.

Self-contained: temp-applies the proposal's backend/tools/market.py over the live
file, imports `tools.market`, runs the checks with `news_context` STUBBED (no
network / no yfinance needed), then restores the live file in a finally block.

Run with the backend venv (so `tools` package deps import cleanly):
    backend/.venv/bin/python .proposed_changes/045-real-news-data/scripts/test_045_real_news.py

Exit code 0 = all pass, 1 = a check failed.
"""
import asyncio
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _find_repo(start: str) -> str:
    """Walk up from `start` until we find the real repo root.

    Anchored on backend/news_context.py (NOT market.py): the proposal mirror
    tree itself contains backend/tools/market.py, so anchoring on that would
    stop too early at the proposal folder. news_context.py exists only at the
    real backend/ root — which is exactly the module this test imports. Works
    whether run from the proposal folder
    (.proposed_changes/045-real-news-data/scripts/) or the applied scripts/.
    """
    d = start
    while True:
        if os.path.isfile(os.path.join(d, "backend", "news_context.py")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            raise RuntimeError(
                f"could not locate repo root (backend/news_context.py) above {start}"
            )
        d = parent


REPO = _find_repo(HERE)
BACKEND = os.path.join(REPO, "backend")
LIVE_MARKET = os.path.join(BACKEND, "tools", "market.py")
# The proposal copy. May be absent once the proposal is archived/removed — in
# that case we just test the live file in place (no temp-apply).
PROP_MARKET = os.path.join(
    REPO, ".proposed_changes", "045-real-news-data", "backend", "tools", "market.py"
)

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results: list[bool] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{PASS if cond else FAIL}] {name}" + (f"  — {detail}" if detail else ""))
    results.append(bool(cond))


# --- stub builders ---------------------------------------------------------

def _news_stub(record: dict):
    async def fake(tickers, limit=2, since=None):
        record["tickers"] = tickers
        record["limit"] = limit
        record["since"] = since
        return {
            "news_by_ticker": {"NVDA": [{"headline": "Real headline", "source": "Reuters",
                                         "ts": "2026-06-16T10:00:00+00:00", "url": "http://x"}]},
            "is_mock": False,
            "source": "yfinance",
        }
    return fake


def _news_err_stub():
    async def fake(tickers, limit=2, since=None):
        return {"news_by_ticker": {}, "is_mock": False, "error": "yfinance_unavailable"}
    return fake


def _macro_stub(indicators):
    async def fake():
        return {"indicators": indicators, "is_mock": False, "source": "yfinance"}
    return fake


def _macro_ret_stub(ret):
    async def fake():
        return ret
    return fake


_MACRO_INDICATORS = [
    {"label": "S&P 500 futures", "symbol": "ES=F", "price": 5500.0, "change_pct": 0.42,
     "display": "S&P 500 futures +0.42%"},
    {"label": "Nasdaq futures", "symbol": "NQ=F", "price": 19000.0, "change_pct": 0.55,
     "display": "Nasdaq futures +0.55%"},
    {"label": "VIX", "symbol": "^VIX", "price": 13.9, "change_pct": -2.1,
     "display": "VIX 13.90 (-2.1%)"},
    {"label": "US 10Y yield", "symbol": "^TNX", "price": 4.27, "change_pct": None,
     "display": "US 10Y yield 4.27%"},
    {"label": "Gold", "symbol": "GC=F", "price": 2410.5, "change_pct": 0.3,
     "display": "Gold +0.30%"},
    {"label": "WTI crude", "symbol": "CL=F", "price": 78.1, "change_pct": -0.5,
     "display": "WTI crude -0.50%"},
]


async def run(market, news_context):
    # Force the real path's gate to pass without yfinance installed.
    market._yfinance_available = lambda: True
    for k in ("USE_MOCK_MARKET", "USE_MOCK_NEWS"):
        os.environ.pop(k, None)

    print("\n=== _use_mock_news truth table ===")
    check("default (real)", market._use_mock_news() is False)
    os.environ["USE_MOCK_NEWS"] = "1"
    check("USE_MOCK_NEWS=1 → mock", market._use_mock_news() is True)
    os.environ.pop("USE_MOCK_NEWS")
    os.environ["USE_MOCK_MARKET"] = "1"
    check("USE_MOCK_MARKET=1 → mock", market._use_mock_news() is True)
    os.environ.pop("USE_MOCK_MARKET")
    market._yfinance_available = lambda: False
    check("no yfinance → mock", market._use_mock_news() is True)
    market._yfinance_available = lambda: True
    check("yfinance back → real", market._use_mock_news() is False)

    print("\n=== get_company_news — real path ===")
    rec: dict = {}
    news_context.fetch_recent_news = _news_stub(rec)
    r = await market.get_company_news({"tickers": ["NVDA"], "limit": 4, "since": "2026-06-01T00:00:00+00:00"}, "demo")
    check("routes to news_context", rec.get("tickers") == ["NVDA"], str(rec))
    check("passes limit through", rec.get("limit") == 4)
    check("passes since through", rec.get("since") == "2026-06-01T00:00:00+00:00")
    check("is_mock False", r.get("is_mock") is False, str(r.get("is_mock")))
    check("real headline returned", r["news_by_ticker"]["NVDA"][0]["headline"] == "Real headline")
    check("source set", r.get("source") == "yfinance")

    print("\n=== get_company_news — real error is surfaced, NOT masked by mock ===")
    news_context.fetch_recent_news = _news_err_stub()
    r = await market.get_company_news({"tickers": ["NVDA"]}, "demo")
    check("error passed through", r.get("error") == "yfinance_unavailable", str(r))
    check("still is_mock False (no silent mock)", r.get("is_mock") is False)

    print("\n=== get_company_news — mock path (USE_MOCK_NEWS=1) ===")
    os.environ["USE_MOCK_NEWS"] = "1"
    r = await market.get_company_news({"tickers": ["NVDA"], "limit": 5}, "demo")
    check("is_mock True", r.get("is_mock") is True)
    check("hand-tuned template used", any("NVDA" in k for k in r["news_by_ticker"]),
          str(list(r["news_by_ticker"])))
    check("template headline present",
          r["news_by_ticker"]["NVDA"][0]["headline"].startswith("Goldman raises"),
          r["news_by_ticker"]["NVDA"][0]["headline"])
    os.environ.pop("USE_MOCK_NEWS")

    print("\n=== _macro_from_indicators mapping ===")
    flat = market._macro_from_indicators(_MACRO_INDICATORS)
    check("ES=F → sp_futures_pct", flat.get("sp_futures_pct") == 0.42, str(flat))
    check("NQ=F → nasdaq_futures_pct", flat.get("nasdaq_futures_pct") == 0.55)
    check("^TNX → 10Y yield level", flat.get("treasury_10y_yield_pct") == 4.27)
    check("^VIX → vix level", flat.get("vix") == 13.9)
    check("CL=F → WTI", flat.get("wti_crude_usd") == 78.1)
    check("GC=F → gold", flat.get("gold_usd") == 2410.5)
    check("no fabricated dxy/btc/dow", not any(
        k in flat for k in ("dxy_level", "btc_usd", "dow_futures_pct")))
    check("missing symbol → field absent", "sp_futures_pct" not in market._macro_from_indicators(
        [{"symbol": "^VIX", "price": 14.0, "change_pct": 0.0}]))

    print("\n=== get_macro_snapshot — real path ===")
    news_context.fetch_macro_context = _macro_stub(_MACRO_INDICATORS)
    r = await market.get_macro_snapshot({}, "demo")
    check("is_mock False", r.get("is_mock") is False)
    check("indicators list present", isinstance(r.get("indicators"), list) and len(r["indicators"]) == 6)
    check("flat sp_futures_pct mapped", r.get("sp_futures_pct") == 0.42)
    check("NO fabricated fed calendar", "fed_events_today" not in r)
    check("NO fabricated earnings", "earnings_today" not in r)

    print("\n=== get_macro_snapshot — empty indicators → honest error ===")
    news_context.fetch_macro_context = _macro_stub([])
    r = await market.get_macro_snapshot({}, "demo")
    check("macro_unavailable surfaced", r.get("error") == "macro_unavailable", str(r))
    check("is_mock False", r.get("is_mock") is False)

    print("\n=== get_macro_snapshot — context error passed through ===")
    news_context.fetch_macro_context = _macro_ret_stub(
        {"indicators": [], "is_mock": False, "error": "yfinance_unavailable"})
    r = await market.get_macro_snapshot({}, "demo")
    check("yfinance_unavailable passed through", r.get("error") == "yfinance_unavailable")

    print("\n=== get_macro_snapshot — mock path keeps the deterministic demo ===")
    os.environ["USE_MOCK_NEWS"] = "1"
    r = await market.get_macro_snapshot({}, "demo")
    check("is_mock True", r.get("is_mock") is True)
    check("demo vix value", r.get("vix") == 14.8)
    check("demo fed calendar present (mock only)", bool(r.get("fed_events_today")))
    os.environ.pop("USE_MOCK_NEWS")

    print("\n=== quotes unaffected by 045 ===")
    os.environ["USE_MOCK_MARKET"] = "1"
    q = await market.get_quote({"tickers": ["NVDA"]}, "demo")
    check("get_quote still works (mock)", q.get("is_mock") is True and q["quotes"][0]["price"] == 942.50)
    os.environ.pop("USE_MOCK_MARKET")


def main() -> int:
    backup = None
    try:
        # Temp-apply the proposal copy over the live file ONLY if it exists and
        # differs (i.e. the proposal isn't applied yet). Once applied/archived,
        # PROP_MARKET may be gone — then we test the live file in place.
        if (
            os.path.isfile(PROP_MARKET)
            and os.path.abspath(PROP_MARKET) != os.path.abspath(LIVE_MARKET)
        ):
            with open(LIVE_MARKET, "rb") as f:
                backup = f.read()
            shutil.copyfile(PROP_MARKET, LIVE_MARKET)
        sys.path.insert(0, BACKEND)
        import news_context  # noqa: E402
        from tools import market  # noqa: E402
        asyncio.run(run(market, news_context))
    finally:
        if backup is not None:
            with open(LIVE_MARKET, "wb") as f:
                f.write(backup)  # restore live file — never leave it modified

    total = len(results)
    passed = sum(results)
    print(f"\n{'='*48}\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
