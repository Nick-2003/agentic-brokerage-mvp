#!/usr/bin/env python3
"""Offline test for Proposal 050 — executed trades in the brief.

Self-contained: temp-applies the proposal's ibkr_flex.py + briefing.py + the
mock fixture (with a <Trades> section) over the live files, runs offline (no
network — uses the mock IBKR fixture), then restores them in a finally.

Run with the backend venv:
    backend/.venv/bin/python scripts/test_050_executed_trades.py
"""
import asyncio
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


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
PROP = os.path.join(REPO, "proposed_changes", "050-briefing-executed-trades", "backend")
FILES = ["ibkr_flex.py", "briefing.py", os.path.join("data", "mock_flex_statement.xml")]

_NO_TRADES_XML = """<FlexQueryResponse><FlexStatements count="1">
<FlexStatement accountId="U1" toDate="2026-06-05" currency="HKD">
  <EquitySummaryInBase><EquitySummaryByReportDateInBase currency="HKD" reportDate="2026-06-05"
      cash="1" stock="9" bonds="0" total="10"/></EquitySummaryInBase>
  <OpenPositions><OpenPosition currency="USD" assetCategory="STK" symbol="AAPL" position="1"
      markPrice="200" percentOfNAV="50" levelOfDetail="SUMMARY"/></OpenPositions>
</FlexStatement></FlexStatements></FlexQueryResponse>"""

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results: list[bool] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{PASS if cond else FAIL}] {name}" + (f"  — {detail}" if detail else ""))
    results.append(bool(cond))


async def run(ibkr_flex, briefing):
    print("\n=== parse_flex_statement: <Trades> ===")
    os.environ["USE_MOCK_IBKR"] = "1"
    snap = await ibkr_flex.get_portfolio_snapshot()  # reads the temp-applied fixture
    trades = snap.get("trades")
    check("trades present on snapshot", isinstance(trades, list))
    check("CLOSED_LOT skipped → 2 executions", len(trades) == 2, f"got {len(trades or [])}")
    by_sym = {t["symbol"]: t for t in trades or []}
    check("BUY NVDA parsed", by_sym.get("NVDA", {}).get("side") == "BUY", str(by_sym.get("NVDA")))
    check("NVDA qty 20", by_sym.get("NVDA", {}).get("quantity") == 20.0)
    check("NVDA price 1175.30", by_sym.get("NVDA", {}).get("price") == 1175.30)
    check("SELL AAPL parsed", by_sym.get("AAPL", {}).get("side") == "SELL")
    check("AAPL qty -50 (signed)", by_sym.get("AAPL", {}).get("quantity") == -50.0)

    print("\n=== empty Trades section → [] ===")
    snap_none = ibkr_flex.parse_flex_statement(_NO_TRADES_XML)
    check("no <Trades> → trades == []", snap_none.get("trades") == [], str(snap_none.get("trades")))

    print("\n=== compute_brief_facts → display strings ===")
    facts = briefing.compute_brief_facts(snap)
    ftr = facts.get("trades") or []
    disp = {t["symbol"]: t["display"] for t in ftr}
    check("facts has 2 trades", len(ftr) == 2, str(ftr))
    check("Bought 20 NVDA @ $1,175.30",
          disp.get("NVDA") == "Bought 20 NVDA @ $1,175.30", disp.get("NVDA", ""))
    check("Sold 50 AAPL @ $201.10 (abs qty, side carries direction)",
          disp.get("AAPL") == "Sold 50 AAPL @ $201.10", disp.get("AAPL", ""))

    print("\n=== mock render includes the Executed line ===")
    text = briefing._render_mock_briefing(facts)
    check("'Executed:' line rendered", "Executed:" in text, text)
    check("trade detail in body", "Bought 20 NVDA" in text)

    print("\n=== no-trades facts → no Executed line ===")
    facts_none = briefing.compute_brief_facts(snap_none)
    check("empty trades → facts trades []", facts_none.get("trades") == [])
    check("mock render omits 'Executed:'", "Executed:" not in briefing._render_mock_briefing(facts_none))
    os.environ.pop("USE_MOCK_IBKR", None)


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
        import ibkr_flex  # noqa: E402
        import briefing  # noqa: E402
        asyncio.run(run(ibkr_flex, briefing))
    finally:
        for live, data in backups.items():
            with open(live, "wb") as fh:
                fh.write(data)

    total, passed = len(results), sum(results)
    print(f"\n{'='*48}\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
