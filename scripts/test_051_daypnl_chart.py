#!/usr/bin/env python3
"""Offline test for Proposal 051 — day-P&L bar chart on the web brief.

Self-contained: temp-applies the proposal's briefing.py + published_briefs.py over
the live files, runs offline (Supabase admin client stubbed; mock IBKR fixture),
then restores them in a finally.

Run with the backend venv:
    backend/.venv/bin/python scripts/test_051_daypnl_chart.py
"""
import asyncio
import os
import shutil
import sys
import types
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))


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
# The proposal's own backend dir, derived from THIS test's location (HERE/../backend)
# — robust to the proposal folder name (proposed_changes vs .proposed_changes) and
# to the test being applied into scripts/ (then PROP == live → temp-apply skipped).
PROP = os.path.normpath(os.path.join(HERE, os.pardir, "backend"))
FILES = ["briefing.py", "published_briefs.py"]

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results: list[bool] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{PASS if cond else FAIL}] {name}" + (f"  — {detail}" if detail else ""))
    results.append(bool(cond))


# --- a tiny fake of the Supabase admin client (insert + select-by-token) -------
class _FakeTable:
    def __init__(self, store):
        self.store = store
    def insert(self, payload):
        self.store["insert"] = payload
        return self
    def select(self, cols):
        self.store["select"] = cols
        return self
    def eq(self, *a, **k):
        return self
    def limit(self, *a, **k):
        return self
    async def execute(self):
        return types.SimpleNamespace(data=self.store.get("rows", []))


class _FakeClient:
    def __init__(self, store):
        self.store = store
    def table(self, _name):
        return _FakeTable(self.store)


async def run(briefing, published_briefs):
    print("\n=== _chart_data (pure) ===")
    snap = {
        "base_currency": "HKD",
        "positions": [
            {"symbol": "AAPL", "day_pnl": 705.0},
            {"symbol": "NVDA", "day_pnl": -1065.6},
            {"symbol": "CASH", "day_pnl": None},  # skipped
        ],
    }
    cd = briefing._chart_data(snap)
    check("chart_data present", cd is not None)
    check("kind=day_pnl", cd["kind"] == "day_pnl")
    check("base currency carried", cd["base_currency"] == "HKD")
    check("only holdings with day_pnl (CASH skipped)", len(cd["bars"]) == 2, str(cd["bars"]))
    check("sorted gainers first", cd["bars"][0]["symbol"] == "AAPL", str([b["symbol"] for b in cd["bars"]]))
    check("numeric + display", cd["bars"][0]["day_pnl"] == 705.0
          and cd["bars"][0]["day_pnl_display"] == "+HK$705.00", str(cd["bars"][0]))
    check("loser display signed", cd["bars"][1]["day_pnl_display"] == "-HK$1,065.60",
          cd["bars"][1]["day_pnl_display"])
    check("empty positions → None", briefing._chart_data({"positions": []}) is None)

    print("\n=== generate_briefing (mock) emits chart_data ===")
    os.environ["USE_MOCK_BRIEFING"] = "1"
    brief = await briefing.generate_briefing(snap, {})
    check("brief carries chart_data", isinstance(brief.get("chart_data"), dict))
    check("chart_data has bars", len(brief["chart_data"]["bars"]) == 2)
    os.environ.pop("USE_MOCK_BRIEFING", None)

    print("\n=== publish_brief stores chart_data; get_published_brief returns it ===")
    store: dict = {}
    published_briefs._admin_client = lambda: _async_return(_FakeClient(store))
    pub = await published_briefs.publish_brief(
        "u1", "the brief body", account_id="U1", as_of="2026-06-05",
        chart_data=cd,
    )
    check("publish returns token + permalink", bool(pub.get("token")) and bool(pub.get("permalink")))
    check("insert payload included chart_data", store.get("insert", {}).get("chart_data") == cd,
          str(store.get("insert", {}).get("chart_data") is not None))

    future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    store["rows"] = [{"body": "the brief body", "account_id": "U1", "as_of": "2026-06-05",
                      "chart_data": cd, "created_at": future, "expires_at": future}]
    got = await published_briefs.get_published_brief("sometoken")
    check("select includes chart_data column", "chart_data" in (store.get("select") or ""),
          store.get("select"))
    check("get returns chart_data", got is not None and got.get("chart_data") == cd)
    check("get still returns text/as_of", got.get("text") == "the brief body" and got.get("as_of") == "2026-06-05")


def _async_return(value):
    async def _coro():
        return value
    return _coro()


def main() -> int:
    backups: dict[str, bytes] = {}
    try:
        for rel in FILES:
            live = os.path.join(BACKEND, rel)
            prop = os.path.join(PROP, rel)
            if os.path.isfile(prop) and os.path.abspath(prop) != os.path.abspath(live):
                with open(live, "rb") as fh:
                    backups[live] = fh.read()
                shutil.copyfile(prop, live)
        sys.path.insert(0, BACKEND)
        import briefing  # noqa: E402
        import published_briefs  # noqa: E402
        asyncio.run(run(briefing, published_briefs))
    finally:
        for live, data in backups.items():
            with open(live, "wb") as fh:
                fh.write(data)

    total, passed = len(results), sum(results)
    print(f"\n{'='*48}\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
