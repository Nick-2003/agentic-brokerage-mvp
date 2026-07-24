#!/usr/bin/env python3
"""Offline guard for Proposal 081 — chart verbs degrade instead of hard-erroring.

Network-free (yfinance is stubbed). Temp-apply → assert → restore-in-`finally`,
with the 078 LIVE-MODE and non-destructive (`_created`) guards. Confirm with
`git status` after running.

**The bug.** `get_technical_levels` has three tiers (mock → TradingView MCP →
yfinance-computed) and degrades gracefully. The `chart_*` verbs had only two
(mock | MCP) and returned `{"error": "tradingview_mcp_unreachable"}` whenever TV
Desktop wasn't reachable — so "add RSI to NVDA" failed outright, and the model
narrated its own invented status line at the user. Since 044 renders charts
in-app from data, the indicator VALUES never needed TradingView at all.

Covers:
  A. chart_apply_indicator degrades — real values + `chart_control: unavailable`
     + a reason, instead of an error; `_action` preserved;
  B. chart_draw_levels keeps the USER's own support/resistance through the outage;
  C. chart_scroll_to_date reports `_scroll_requested` and NEVER `_scrolled_to`
     — the viewport genuinely did not move, so claiming it did would be a lie;
  D. honest failure — when the computed tier ALSO fails, the ORIGINAL MCP error
     is returned (no hollow success, no second-order error);
  E. no regression — a SUCCESSFUL MCP call is untouched (no chart_control key);
  F. sources never claim live TradingView on a degraded payload (029 discipline);
  G. the prompt rules are present (chart_control handling + relay-don't-invent).

Run:
    backend/.venv/bin/python scripts/test_081_chart_degradation.py
"""
import asyncio
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def _find_repo(start: str) -> str:
    d = start
    while True:
        if os.path.isfile(os.path.join(d, "backend", "auth.py")):
            return d
        p = os.path.dirname(d)
        if p == d:
            raise RuntimeError("repo root not found")
        d = p


REPO = _find_repo(HERE)
BACKEND = os.path.join(REPO, "backend")
PROP = os.path.join(REPO, ".proposed_changes", "081-chart-degradation", "backend")

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results: list[bool] = []


def check(name, cond, detail=""):
    print(f"  [{PASS if cond else FAIL}] {name}" + (f"  — {detail}" if detail else ""))
    results.append(bool(cond))


OVERWRITE = ["tools/technicals.py", "prompts/system.md"]
_created: list[str] = []
LIVE_MODE = not os.path.isdir(PROP)


def apply_proposal(backup_dir: str) -> None:
    if LIVE_MODE:
        print("  (staged dir absent — 081 is applied; asserting against the LIVE tree)")
        return
    for f in OVERWRITE:
        bak = os.path.join(backup_dir, f.replace("/", "__"))
        shutil.copy2(os.path.join(BACKEND, f), bak)
        shutil.copy2(os.path.join(PROP, f), os.path.join(BACKEND, f))


def restore(backup_dir: str) -> None:
    if LIVE_MODE:
        return
    for f in OVERWRITE:
        bak = os.path.join(backup_dir, f.replace("/", "__"))
        if os.path.isfile(bak):
            shutil.copy2(bak, os.path.join(BACKEND, f))
    for p in _created:
        if os.path.isfile(p):
            os.remove(p)


COMPUTED = {
    "ticker": "NVDA",
    "timeframe": "1D",
    "current_price": 212.06,
    "currency": "$",
    "indicator_values": {"RSI 14": 58.2},
    "indicators_applied": ["RSI 14"],
    "trend": "up",
    "price_above_sma200": True,
    "key_levels": {"support": [205.0], "resistance": [220.0]},
    "is_mock": False,
    "source": "yfinance_computed",
    "sources": [{"name": "Daily OHLC via yfinance · 250d"}],
}


def run() -> None:
    sys.path.insert(0, BACKEND)
    os.environ["USE_MOCK_TA"] = "0"          # force the real/MCP path
    os.environ["TRADINGVIEW_MCP_ARGS"] = "/tmp/fake-tv-server.js"   # "configured"

    import tools.technicals as T

    # --- stub the tiers -------------------------------------------------------
    async def _computed_ok(ticker, timeframe, indicators):
        out = dict(COMPUTED)
        out["indicators_applied"] = list(indicators)
        return out

    async def _computed_fail(ticker, timeframe, indicators):
        return {"error": "market_data_fetch_failed", "ticker": ticker}

    T._yfinance_ta_available = lambda: True
    T._tradingview_configured = lambda: True

    import mcp_client
    real_tv_call = mcp_client.tv_call

    # The real class the verbs catch: MCPUnreachable(MCPClientError),
    # code == "tradingview_mcp_unreachable".
    async def _tv_down(tool, args):
        raise mcp_client.MCPUnreachable("TradingView Desktop not reachable on :9222")

    try:
        mcp_client.tv_call = _tv_down
        T._yfinance_technical_levels = _computed_ok

        # ------------------------------------------------------------ A
        print("\nA. chart_apply_indicator degrades instead of erroring")
        r = asyncio.run(T.chart_apply_indicator(
            {"ticker": "NVDA", "indicator": "RSI 14", "action": "add"}, "u"))
        check("no error field", "error" not in r, str(r.get("error")))
        check("chart_control == unavailable", r.get("chart_control") == "unavailable")
        check("reason names the cause",
              "tradingview_mcp_unreachable" in (r.get("chart_control_reason") or ""))
        check("REAL indicator values present", r.get("indicator_values") == {"RSI 14": 58.2})
        check("requested indicator honoured", r.get("indicators_applied") == ["RSI 14"])
        check("_action preserved", r.get("_action") == "add")

        # ------------------------------------------------------------ B
        print("\nB. chart_draw_levels keeps the USER's levels through the outage")
        r = asyncio.run(T.chart_draw_levels(
            {"ticker": "NVDA", "support": [200.5], "resistance": [225.25]}, "u"))
        check("no error field", "error" not in r)
        check("chart_control unavailable", r.get("chart_control") == "unavailable")
        check("user support/resistance survive",
              r.get("key_levels") == {"support": [200.5], "resistance": [225.25]},
              str(r.get("key_levels")))

        # ------------------------------------------------------------ C
        print("\nC. chart_scroll_to_date never claims a scroll that didn't happen")
        r = asyncio.run(T.chart_scroll_to_date(
            {"ticker": "NVDA", "date": "2026-03-01"}, "u"))
        check("no error field", "error" not in r)
        check("_scroll_requested set", r.get("_scroll_requested") == "2026-03-01")
        check("_scrolled_to NOT set (viewport never moved)", "_scrolled_to" not in r)

        # ------------------------------------------------------------ F
        print("\nF. degraded payload never claims live TradingView")
        names = " ".join(s.get("name", "") for s in (r.get("sources") or []))
        check("sources cite yfinance, not TradingView",
              "yfinance" in names.lower() and "tradingview" not in names.lower(), names)

        # ------------------------------------------------------------ D
        print("\nD. computed tier ALSO fails → honest ORIGINAL error")
        T._yfinance_technical_levels = _computed_fail
        r = asyncio.run(T.chart_apply_indicator({"ticker": "ZZZZ", "indicator": "RSI 14"}, "u"))
        check("error is the ORIGINAL MCP cause",
              r.get("error") == "tradingview_mcp_unreachable", str(r.get("error")))
        check("no hollow success (no chart_control)", "chart_control" not in r)

        T._yfinance_ta_available = lambda: False
        r = asyncio.run(T.chart_apply_indicator({"ticker": "NVDA", "indicator": "RSI 14"}, "u"))
        check("no yfinance at all → original error too",
              r.get("error") == "tradingview_mcp_unreachable")
        T._yfinance_ta_available = lambda: True

        # ------------------------------------------------------------ E
        print("\nE. a SUCCESSFUL MCP call is untouched (no regression)")
        T._yfinance_technical_levels = _computed_ok

        async def _tv_ok(tool, args):
            return {}

        async def _real_ok(ticker, timeframe, indicators):
            return {**COMPUTED, "source": "tradingview_mcp",
                    "sources": [{"name": "Live OHLC via TradingView"}]}

        mcp_client.tv_call = _tv_ok
        T._real_technical_levels = _real_ok
        r = asyncio.run(T.chart_apply_indicator({"ticker": "NVDA", "indicator": "RSI 14"}, "u"))
        check("no chart_control on the happy path", "chart_control" not in r)
        check("source still tradingview_mcp", r.get("source") == "tradingview_mcp")
    finally:
        mcp_client.tv_call = real_tv_call

    # ---------------------------------------------------------------- G
    print("\nG. prompt rules present")
    sm = open(os.path.join(BACKEND, "prompts", "system.md")).read()
    check("chart_control rule documented", "chart_control" in sm)
    check("_scroll_requested distinction documented", "_scroll_requested" in sm)
    check("relay-don't-invent rule present",
          "never invent status prose" in sm.lower() or "relay tool errors" in sm.lower())


def main() -> None:
    backup = tempfile.mkdtemp(prefix="081-backup-")
    try:
        apply_proposal(backup)
        run()
    finally:
        restore(backup)
        shutil.rmtree(backup, ignore_errors=True)

    total, ok = len(results), sum(results)
    print(f"\n{'=' * 62}\n  {ok}/{total} checks passed\n{'=' * 62}")
    print("Live tree restored — confirm with: git status --short")
    sys.exit(0 if ok == total else 1)


if __name__ == "__main__":
    main()
