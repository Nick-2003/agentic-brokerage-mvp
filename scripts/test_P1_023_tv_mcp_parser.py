#!/usr/bin/env python3
"""P1.2 / Proposal 023 — TradingView MCP real-shape parser regression test.

Drives `_real_technical_levels` end-to-end with `mcp_client.tv_call` stubbed
to return the EXACT response shapes the `tradesdontlie/tradingview-mcp`
server actually produces (sourced from its `src/core/data.js` +
`src/core/capture.js`). Locks the parser against future drift in those
sub-call shapes.

Run (after applying 023):
    backend/.venv/bin/python scripts/test_P1_023_tv_mcp_parser.py
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any


def _find_repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "backend").is_dir() and (parent / "API_CONTRACT.md").is_file():
            return parent
    raise RuntimeError("repo root not found")


_REPO_ROOT = _find_repo_root()
_LIVE_BACKEND = _REPO_ROOT / "backend"
_PROPOSED_TECH = (
    _REPO_ROOT / "backend/tools/technicals.py"
)

# Stage the proposed file over a temp copy of the live tools/ package so the
# package-relative `from . import ToolDef, register` resolves; mcp_client/
# market.py come from the live backend tree. Order matters — _TMP MUST win
# resolution (LIFO insert: live first, _TMP last, so _TMP ends at index 0).
_TMP = Path(tempfile.mkdtemp(prefix="ta023_"))
shutil.copytree(_LIVE_BACKEND / "tools", _TMP / "tools")
if _PROPOSED_TECH.exists():
    shutil.copy(_PROPOSED_TECH, _TMP / "tools" / "technicals.py")
sys.path.insert(0, str(_LIVE_BACKEND))
sys.path.insert(0, str(_TMP))

os.environ.pop("USE_MOCK_TA", None)  # force real path

import mcp_client  # noqa: E402  — for monkey-patching tv_call

_passed = 0
_failed = 0


def check(name: str, cond: bool, *extra: Any) -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ✓ {name}")
    else:
        _failed += 1
        print(f"  ✗ {name}", *extra)


# A tiny real PNG byte stream for screenshot tests (1×1 transparent pixel).
_PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgAAIAAAUAAeImBZsAAAAASUVORK5CYII="
)


def stub_tv_call(responses: dict[str, Any]):
    """Build a tv_call stub driven by a {tool_name: response} map.

    Response can be a dict, an Exception (will be raised), or a callable
    `fn(tool, args) -> dict`.
    """
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        calls.append((tool, args))
        r = responses.get(tool, {"ok": True})
        if isinstance(r, Exception):
            raise r
        if callable(r):
            return r(tool, args)
        return r

    return fake, calls


async def _run_real(ticker: str, indicators: list[str]) -> dict[str, Any]:
    from tools.technicals import _real_technical_levels
    return await _real_technical_levels(ticker, "1D", indicators)


print("P1.2 / 023 — _real_technical_levels (TradingView MCP real shapes)")


# ── 1) data_get_study_values: ONE call returns ALL visible studies. ──
# Sourced from `core/data.js::getStudyValues()`:
#   { success: true, study_count: N, studies: [{ name, values: {title: value} }] }
# We add SMA 50 then SMA 200 — server returns them in add-order. Their `name`
# is "Moving Average Simple" for both (no length in description); we use a
# value-title heuristic + positional fallback to disambiguate.

# Make a real PNG file on disk for the capture_screenshot tests below.
_shot_dir = Path(tempfile.mkdtemp(prefix="shots_"))
_shot_path = _shot_dir / "tv_chart_test.png"
_shot_path.write_bytes(_PNG_1x1)

happy_responses = {
    "tv_health_check":        {"ok": True},
    "chart_set_symbol":       {"ok": True},
    "chart_set_timeframe":    {"ok": True},
    "chart_manage_indicator": {"ok": True},
    # Real shape: studies[].name = "Moving Average Simple"; values dict carries
    # the period in its title (e.g. "MA(50)") so length-aware matching works.
    "data_get_study_values":  {
        "success": True,
        "study_count": 2,
        "studies": [
            {"name": "Moving Average Simple", "values": {"MA(50)": "211.30"}},
            {"name": "Moving Average Simple", "values": {"MA(200)": "197.42"}},
        ],
    },
    # Real shape: studies[].horizontal_levels = [num, num, ...]; no labels.
    "data_get_pine_lines": {
        "success": True, "study_count": 1,
        "studies": [{
            "name": "User Levels", "total_lines": 4,
            "horizontal_levels": [220.0, 215.5, 205.0, 195.0],  # mix of above + below
        }],
    },
    "quote_get": {"success": True, "symbol": "NVDA", "last": 211.14, "close": 211.14},
    "capture_screenshot": {
        "success": True, "method": "cdp", "region": "chart",
        "file_path": str(_shot_path), "size_bytes": len(_PNG_1x1),
    },
}

fake, calls = stub_tv_call(happy_responses)
mcp_client.tv_call = fake

r1 = asyncio.run(_run_real("NVDA", ["SMA 50", "SMA 200"]))

check("current_price flows through `quote.last`", r1["current_price"] == 211.14)
check("SMA 50 mapped to 211.30 (length-aware: MA(50) in value title)",
      r1["indicator_values"].get("SMA 50") == 211.30)
check("SMA 200 mapped to 197.42 (length-aware: MA(200) in value title)",
      r1["indicator_values"].get("SMA 200") == 197.42)
def _trend_expected(price: float, sma50: float, sma200: float) -> str:
    if price > sma50 > sma200:
        return "bullish"
    if price < sma50 < sma200:
        return "bearish"
    return "neutral"


check("trend computed from real indicator values",
      r1["trend"] == _trend_expected(211.14, 211.30, 197.42))

# Pine-line partition: levels above current = resistance (asc), below = support (desc).
check("pine-lines: resistance = sorted asc, above price",
      r1["key_levels"]["resistance"] == [215.5, 220.0])
check("pine-lines: support = sorted desc, below price",
      r1["key_levels"]["support"] == [205.0, 195.0])

# Screenshot: file read, base64-encoded into data URL.
check("screenshot decoded from file_path → data: URL",
      r1["screenshot_url"].startswith("data:image/png;base64,"))
check("screenshot data URL matches the PNG bytes on disk",
      r1["screenshot_url"] == "data:image/png;base64," + base64.b64encode(_PNG_1x1).decode())

# Call-shape contract — `data_get_study_values` MUST be called with NO args.
sv_call = next(c for c in calls if c[0] == "data_get_study_values")
check("data_get_study_values called with empty args ({})", sv_call[1] == {})

# capture_screenshot is called with region='chart'.
ss_call = next(c for c in calls if c[0] == "capture_screenshot")
check("capture_screenshot called with region='chart'",
      ss_call[1].get("region") == "chart")

# Sources still verbatim from the real path (020 contract).
check("real-mode sources unchanged",
      r1["sources"] == [
          {"name": "TradingView Desktop", "url": "https://www.tradingview.com"},
          {"name": "Live OHLC via TradingView MCP"},
      ])
check("is_mock False", r1["is_mock"] is False)


# ── 2) SMA disambiguation by positional fallback ──
# What if TradingView doesn't put the length in any title? Then the parser
# must fall back to positional matching: applied[i] → i-th matching study.

ambiguous_responses = dict(happy_responses)
ambiguous_responses["data_get_study_values"] = {
    "success": True,
    "study_count": 2,
    "studies": [
        # Both have identical name; neither title carries the length.
        {"name": "Moving Average Simple", "values": {"Plot": "211.30"}},
        {"name": "Moving Average Simple", "values": {"Plot": "197.42"}},
    ],
}
fake, _ = stub_tv_call(ambiguous_responses)
mcp_client.tv_call = fake

r2 = asyncio.run(_run_real("NVDA", ["SMA 50", "SMA 200"]))
check("positional fallback: SMA 50 → 1st study (211.30)",
      r2["indicator_values"].get("SMA 50") == 211.30)
check("positional fallback: SMA 200 → 2nd study (197.42)",
      r2["indicator_values"].get("SMA 200") == 197.42)
check("no collision — both SMAs distinct",
      len({r2["indicator_values"]["SMA 50"], r2["indicator_values"]["SMA 200"]}) == 2)


# ── 3) Pine lines empty → swing-derived from real current_price ──

no_pine_responses = dict(happy_responses)
no_pine_responses["data_get_pine_lines"] = {
    "success": True, "study_count": 0, "studies": [],
}
fake, _ = stub_tv_call(no_pine_responses)
mcp_client.tv_call = fake

r3 = asyncio.run(_run_real("NVDA", ["SMA 50"]))
expected_r = [round(211.14 * 1.018, 2), round(211.14 * 1.06, 2)]
expected_s = [round(211.14 * 0.935, 2), round(211.14 * 0.88, 2)]
check("no pine lines → swing R from real price",
      r3["key_levels"]["resistance"] == expected_r)
check("no pine lines → swing S from real price",
      r3["key_levels"]["support"] == expected_s)
check("S/R is NOT the pre-022 mock-derived $959/$881",
      r3["key_levels"]["resistance"] != [959.47, 999.05])


# ── 4) Mixed pine lines: only ABOVE-price levels → support falls back to swing ──

mixed_responses = dict(happy_responses)
mixed_responses["data_get_pine_lines"] = {
    "success": True, "study_count": 1,
    "studies": [{"name": "Targets", "total_lines": 2,
                 "horizontal_levels": [225.0, 230.0]}],  # both above $211.14
}
fake, _ = stub_tv_call(mixed_responses)
mcp_client.tv_call = fake

r4 = asyncio.run(_run_real("NVDA", ["SMA 50"]))
check("partial pine: above-price levels → resistance",
      r4["key_levels"]["resistance"] == [225.0, 230.0])
check("partial pine: empty below-price → support falls back to swing",
      r4["key_levels"]["support"] == expected_s)


# ── 5) Screenshot — file missing on disk → empty URL, logged ──

missing_file_responses = dict(happy_responses)
missing_file_responses["capture_screenshot"] = {
    "success": True, "method": "cdp", "region": "chart",
    "file_path": "/tmp/definitely-not-a-real-file-987654321.png",
    "size_bytes": 0,
}
cap_log = logging.getLogger("tools.technicals")


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[str] = []

    def emit(self, record):
        self.records.append(record.getMessage())


h = _Capture()
h.setLevel(logging.INFO)
cap_log.addHandler(h)
cap_log.setLevel(logging.INFO)

fake, _ = stub_tv_call(missing_file_responses)
mcp_client.tv_call = fake
h.records.clear()

r5 = asyncio.run(_run_real("NVDA", ["SMA 50"]))
check("missing screenshot file → screenshot_url empty (no crash)",
      r5["screenshot_url"] == "")
check("missing file logged",
      any("file not found" in m for m in h.records),
      "records:", h.records)


# ── 6) Screenshot — server returned no file_path → empty URL, logged ──

no_path_responses = dict(happy_responses)
no_path_responses["capture_screenshot"] = {"success": True}  # no file_path
fake, _ = stub_tv_call(no_path_responses)
mcp_client.tv_call = fake
h.records.clear()

r6 = asyncio.run(_run_real("NVDA", ["SMA 50"]))
check("no file_path key → empty URL", r6["screenshot_url"] == "")
check("no file_path logged",
      any("no file_path" in m for m in h.records),
      "records:", h.records)


# ── 7) quote_get failure → last-resort fallback to MOCK_QUOTES ──

quote_fail_responses = dict(happy_responses)
quote_fail_responses["quote_get"] = mcp_client.MCPCallFailed("sim quote_get")
fake, _ = stub_tv_call(quote_fail_responses)
mcp_client.tv_call = fake

r7 = asyncio.run(_run_real("NVDA", ["SMA 50"]))
check("quote_get failure → current_price = MOCK_QUOTES[NVDA] (942.50)",
      r7["current_price"] == 942.50)


# ── Cleanup ──
shutil.rmtree(_TMP, ignore_errors=True)
shutil.rmtree(_shot_dir, ignore_errors=True)

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
