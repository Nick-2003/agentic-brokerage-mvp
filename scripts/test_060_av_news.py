#!/usr/bin/env python3
"""Offline guard for Proposal 060 — Alpha Vantage news supplement (briefing-only).

Network-free. Covers:

  A. `av_news_enabled()` env gating (mock forced / no key / REPLACE / real key).
  B. Pure parsers: AV time ↔ ISO, feed-item normalise, feed→per-ticker
     distribution (relevance + since + cap), and the throttle/`Information`
     payload → AVError guard.
  C. `news_context._merge_news` — yfinance+AV union, dedup by (title, host),
     newest-first, per-symbol cap.
  D. Integration `fetch_recent_news(use_av=…)` with a FAKE yfinance module +
     monkeypatched `alphavantage_client.fetch_news`:
       - use_av=False           → AV NOT called, source "yfinance"
       - use_av=True + enabled   → merged, source "yfinance+alphavantage"
       - use_av=True + AV raises  → best-effort, source "yfinance" (no crash)
  E. `fetch_news` TTL cache + soft daily cap with a FAKE httpx (no network):
       - cache hit → one upstream call for two invocations
       - daily cap (=1) → second call raises AVError
       - AV `{"Information": …}` throttle body → AVError

Self-contained: temp-applies the proposal's backend/{alphavantage_client.py [new],
news_context.py} over live, imports, asserts, restores (and DELETES the new file)
in a finally. Anchored on backend/auth.py (not in 060's mirror).

Run with the backend venv:
    backend/.venv/bin/python scripts/test_060_av_news.py
"""
import asyncio
import os
import shutil
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))


def _find_repo(start: str) -> str:
    d = start
    while True:
        if os.path.isfile(os.path.join(d, "backend", "auth.py")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            raise RuntimeError(f"could not locate repo root (backend/auth.py) above {start}")
        d = parent


REPO = _find_repo(HERE)
BACKEND = os.path.join(REPO, "backend")
PROP = os.path.join(REPO, ".proposed_changes", "060-alphavantage-news-supplement")
FILES = [
    (os.path.join(BACKEND, "alphavantage_client.py"),
     os.path.join(PROP, "backend", "alphavantage_client.py")),
    (os.path.join(BACKEND, "news_context.py"),
     os.path.join(PROP, "backend", "news_context.py")),
]

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results: list[bool] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{PASS if cond else FAIL}] {name}" + (f"  — {detail}" if detail else ""))
    results.append(bool(cond))


FEED = {
    "feed": [
        {"title": "Apple hits high", "source": "Reuters", "url": "https://www.reuters.com/x",
         "time_published": "20260701T120000", "overall_sentiment_label": "Bullish",
         "overall_sentiment_score": "0.42",
         "ticker_sentiment": [{"ticker": "AAPL", "relevance_score": "0.9"}]},
        {"title": "MSFT cloud grows", "source": "Bloomberg", "url": "https://bloomberg.com/y",
         "time_published": "20260701T110000", "overall_sentiment_label": "Neutral",
         "overall_sentiment_score": "0.1",
         "ticker_sentiment": [{"ticker": "MSFT", "relevance_score": "0.8"}]},
        {"title": "Old apple item", "source": "X", "url": "https://x.com/z",
         "time_published": "20260101T000000", "overall_sentiment_label": "Neutral",
         "overall_sentiment_score": "0.0",
         "ticker_sentiment": [{"ticker": "AAPL", "relevance_score": "0.2"}]},
    ]
}


def test_enabled(av) -> None:
    print("\n=== A. av_news_enabled() gating ===")
    saved = {k: os.environ.get(k) for k in ("USE_MOCK_NEWS", "ALPHAVANTAGE_API_KEY")}
    try:
        os.environ["USE_MOCK_NEWS"] = "1"
        os.environ["ALPHAVANTAGE_API_KEY"] = "realkey"
        check("mock forced → disabled", av.av_news_enabled() is False)
        os.environ["USE_MOCK_NEWS"] = "0"
        os.environ.pop("ALPHAVANTAGE_API_KEY", None)
        check("no key → disabled", av.av_news_enabled() is False)
        os.environ["ALPHAVANTAGE_API_KEY"] = "REPLACE"
        check("REPLACE sentinel → disabled", av.av_news_enabled() is False)
        os.environ["ALPHAVANTAGE_API_KEY"] = "realkey"
        check("real key + not mock → enabled", av.av_news_enabled() is True)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_parsers(av) -> None:
    print("\n=== B. parsers / distribution / error guard ===")
    check("AV ts → ISO", av._parse_av_ts("20260701T120000") == "2026-07-01T12:00:00+00:00",
          str(av._parse_av_ts("20260701T120000")))
    check("bad ts → None", av._parse_av_ts("nope") is None)
    check("since → AV time_from", av._since_to_time_from("2026-07-01T12:00:00+00:00") == "20260701T1200",
          str(av._since_to_time_from("2026-07-01T12:00:00+00:00")))

    item = av._parse_av_feed_item(FEED["feed"][0])
    check("feed item normalised", item is not None and item["headline"] == "Apple hits high")
    check("sentiment carried", item["sentiment"]["label"] == "Bullish" and item["sentiment"]["score"] == 0.42,
          str(item["sentiment"]))

    dist = av._feed_to_news_by_ticker(FEED, ["AAPL", "MSFT"], limit=5, since=None)
    check("AAPL gets its 2 items", len(dist["AAPL"]) == 2, str(len(dist["AAPL"])))
    check("MSFT gets its 1 item", len(dist["MSFT"]) == 1, str(len(dist["MSFT"])))
    check("AAPL newest-first", dist["AAPL"][0]["headline"] == "Apple hits high")
    check("relevance attached", dist["AAPL"][0].get("relevance") == 0.9, str(dist["AAPL"][0].get("relevance")))

    since_dist = av._feed_to_news_by_ticker(FEED, ["AAPL"], limit=5, since="2026-06-01T00:00:00+00:00")
    check("since filters the old item", len(since_dist["AAPL"]) == 1, str(len(since_dist["AAPL"])))

    cap_dist = av._feed_to_news_by_ticker(FEED, ["AAPL"], limit=1, since=None)
    check("per-symbol cap honoured", len(cap_dist["AAPL"]) == 1)

    try:
        av._raise_if_error({"Information": "throttled: 25/day"})
        check("throttle payload → AVError", False, "no raise")
    except av.AVError:
        check("throttle payload → AVError", True)


def test_merge() -> None:
    print("\n=== C. news_context._merge_news ===")
    import news_context as nc  # noqa: E402 — after temp-apply

    yf = {"AAPL": [{"headline": "Apple hits high", "source": "Yahoo",
                    "ts": "2026-07-01T12:00:00+00:00", "url": "https://www.reuters.com/x"}]}
    av_by = {"AAPL": [
        {"headline": "APPLE HITS HIGH", "source": "Reuters", "ts": "2026-07-01T12:05:00+00:00",
         "url": "https://reuters.com/x"},  # same (title, host) as yf → dedup, yf wins
        {"headline": "Fresh AV scoop", "source": "Bloomberg", "ts": "2026-07-02T00:00:00+00:00",
         "url": "https://bloomberg.com/n"},
    ]}
    merged = nc._merge_news(yf, av_by, limit=5)["AAPL"]
    check("dedup by (title, host)", len(merged) == 2, f"n={len(merged)}")
    check("newest-first after merge", merged[0]["headline"] == "Fresh AV scoop")
    check("yfinance wins the tie", any(m["source"] == "Yahoo" for m in merged)
          and not any(m["source"] == "Reuters" for m in merged))
    capped = nc._merge_news(yf, av_by, limit=1)["AAPL"]
    check("merge respects limit", len(capped) == 1 and capped[0]["headline"] == "Fresh AV scoop")


def _install_fake_yfinance() -> None:
    """Inject a no-network yfinance whose `.news` is always empty."""
    fake = types.ModuleType("yfinance")

    class _Ticker:
        def __init__(self, sym):
            self.sym = sym

        @property
        def news(self):
            return []

    fake.Ticker = _Ticker  # type: ignore[attr-defined]
    sys.modules["yfinance"] = fake


def test_fetch_recent_news_gate(av) -> None:
    print("\n=== D. fetch_recent_news(use_av=…) gating + merge ===")
    import news_context as nc  # noqa: E402
    _install_fake_yfinance()
    os.environ["USE_MOCK_NEWS"] = "0"
    os.environ["ALPHAVANTAGE_API_KEY"] = "realkey"

    calls = {"n": 0}

    async def _fake_fetch_news(tickers, limit=2, since=None):
        calls["n"] += 1
        return {"news_by_ticker": {"AAPL": [
            {"headline": "AV only story", "source": "Reuters",
             "ts": "2026-07-02T00:00:00+00:00", "url": "https://reuters.com/av"}]},
            "source": "alphavantage"}

    orig = av.fetch_news
    av.fetch_news = _fake_fetch_news  # type: ignore[assignment]
    try:
        # use_av=False → AV not consulted
        r = asyncio.run(nc.fetch_recent_news(["AAPL"], limit=5, use_av=False))
        check("use_av=False → AV not called", calls["n"] == 0)
        check("use_av=False → source yfinance", r.get("source") == "yfinance", str(r.get("source")))

        # use_av=True + enabled → merged in
        r = asyncio.run(nc.fetch_recent_news(["AAPL"], limit=5, use_av=True))
        check("use_av=True → AV called once", calls["n"] == 1)
        check("use_av=True → source yfinance+alphavantage",
              r.get("source") == "yfinance+alphavantage", str(r.get("source")))
        check("AV item merged into AAPL",
              any(it["headline"] == "AV only story" for it in r["news_by_ticker"]["AAPL"]))

        # use_av=True + AV raises → best-effort fallback to yfinance-only
        async def _boom(tickers, limit=2, since=None):
            raise av.AVError("throttled")

        av.fetch_news = _boom  # type: ignore[assignment]
        r = asyncio.run(nc.fetch_recent_news(["AAPL"], limit=5, use_av=True))
        check("AV failure → source falls back to yfinance", r.get("source") == "yfinance",
              str(r.get("source")))
    finally:
        av.fetch_news = orig  # type: ignore[assignment]


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200
        self.text = ""

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None):
        _FakeClient.calls += 1  # type: ignore[attr-defined]
        return _FakeResp(self._payload)


_FakeClient.calls = 0  # type: ignore[attr-defined]


def test_fetch_news_cache_and_cap(av) -> None:
    print("\n=== E. fetch_news TTL cache + daily cap (fake httpx) ===")
    os.environ["ALPHAVANTAGE_API_KEY"] = "realkey"
    orig_client = av.httpx.AsyncClient

    def _factory(*a, **k):
        return _FakeClient(FEED)

    av.httpx.AsyncClient = _factory  # type: ignore[assignment]
    try:
        # cache hit — two calls, one upstream GET
        av._cache.clear()
        av._calls["day"] = None
        av._calls["count"] = 0
        _FakeClient.calls = 0  # type: ignore[attr-defined]
        os.environ["ALPHAVANTAGE_DAILY_CAP"] = "22"
        r1 = asyncio.run(av.fetch_news(["AAPL", "MSFT"], limit=3))
        r2 = asyncio.run(av.fetch_news(["MSFT", "AAPL"], limit=3))  # same set, sorted key
        check("second identical call is cached (1 upstream GET)", _FakeClient.calls == 1,
              f"gets={_FakeClient.calls}")
        check("cached result equal", r1 == r2)

        # daily cap = 1 → second (uncached) call raises
        av._cache.clear()
        av._calls["day"] = None
        av._calls["count"] = 0
        os.environ["ALPHAVANTAGE_DAILY_CAP"] = "1"
        asyncio.run(av.fetch_news(["AAPL"], limit=3))
        try:
            asyncio.run(av.fetch_news(["MSFT"], limit=3))  # different key → not cached
            check("daily cap blocks the 2nd call", False, "no raise")
        except av.AVError:
            check("daily cap blocks the 2nd call", True)

        # throttle body (200 + Information) → AVError
        av._cache.clear()
        av._calls["day"] = None
        av._calls["count"] = 0
        os.environ["ALPHAVANTAGE_DAILY_CAP"] = "22"
        av.httpx.AsyncClient = lambda *a, **k: _FakeClient({"Information": "rate limit 25/day"})
        try:
            asyncio.run(av.fetch_news(["AAPL"], limit=3))
            check("throttle body → AVError", False, "no raise")
        except av.AVError:
            check("throttle body → AVError", True)
    finally:
        av.httpx.AsyncClient = orig_client  # type: ignore[assignment]
        os.environ.pop("ALPHAVANTAGE_DAILY_CAP", None)


def main() -> int:
    backups: list[tuple[str, str, bool]] = []  # (live, backup, existed_before)
    try:
        for live, prop in FILES:
            if not os.path.isfile(prop):
                print(f"missing proposal file: {prop}")
                return 1
            existed = os.path.isfile(live)
            bak = live + ".060bak"
            if existed:
                shutil.copy2(live, bak)
            backups.append((live, bak, existed))
            shutil.copy2(prop, live)

        if BACKEND not in sys.path:
            sys.path.insert(0, BACKEND)
        import alphavantage_client as av  # noqa: E402 — after temp-apply

        test_enabled(av)
        test_parsers(av)
        test_merge()
        test_fetch_recent_news_gate(av)
        test_fetch_news_cache_and_cap(av)
    finally:
        for live, bak, existed in backups:
            if existed:
                shutil.copy2(bak, live)
                os.remove(bak)
            else:
                # new file (alphavantage_client.py) — remove it + its cache
                if os.path.isfile(live):
                    os.remove(live)
        sys.modules.pop("yfinance", None)

    total, passed = len(results), sum(results)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
