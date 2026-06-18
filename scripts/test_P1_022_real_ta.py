#!/usr/bin/env python3
"""P1.2 / Proposal 022 — real-mode TA fallback regression test (offline).

Stubs `mcp_client.tv_call` so we can drive `_real_technical_levels` end-to-end
WITHOUT needing TradingView Desktop + the sibling MCP server. Locks the
contract that:

  - S/R fallback uses the REAL `current_price` (not `_extract_price` →
    `MOCK_QUOTES`).  ← the trust-#3 violation 022 fixes
  - `quote_get` runs before `data_get_pine_lines`, so the fallback has the
    real price available.
  - Unexpected MCP response shapes are logged (not silently swallowed) at
    `data_get_study_values`, `data_get_pine_lines`, `capture_screenshot`.
  - `quote_get` failure cleanly falls back to `_extract_price` (mock cache) —
    that path is still correct as a last resort.

Run (after applying 022, from repo root):
    backend/.venv/bin/python scripts/test_P1_022_real_ta.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

# Walk up parents until we find one containing `backend/` — robust to both
# pre-apply (script under `.proposed_changes/.../scripts/`) and post-apply
# (script at repo root `scripts/`) layouts.
def _find_repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "backend").is_dir() and (parent / ".proposed_changes").is_dir():
            return parent
        if (parent / "backend").is_dir() and (parent / "API_CONTRACT.md").is_file():
            return parent
    raise RuntimeError("repo root not found (no parent has both backend/ and .proposed_changes/)")


_REPO_ROOT = _find_repo_root()

# Stage the proposed technicals.py on top of a temp copy of the live tools/
# package so package-relative imports (`from . import ToolDef, register`)
# resolve, while the OTHER tools modules + mcp_client come from the live tree.
_LIVE_BACKEND = _REPO_ROOT / "backend"
_PROPOSED_TECH = (
    _REPO_ROOT / "backend/tools/technicals.py"
)

_TMP = Path(tempfile.mkdtemp(prefix="ta022_"))
shutil.copytree(_LIVE_BACKEND / "tools", _TMP / "tools")
if _PROPOSED_TECH.exists():  # pre-apply layout
    shutil.copy(_PROPOSED_TECH, _TMP / "tools" / "technicals.py")
# Order matters: _TMP MUST come before _LIVE_BACKEND so the staged proposed
# technicals.py wins package resolution. (Live mcp_client / market / etc. are
# still found via _LIVE_BACKEND.) `sys.path.insert(0, …)` twice = LIFO, so we
# insert _LIVE_BACKEND first and _TMP last to leave _TMP at index 0.
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


# ---------------------------------------------------------------------------
# tv_call stub — script-driven by a per-test response map
# ---------------------------------------------------------------------------


def stub_tv_call(responses: dict[str, Any] | list[Any]):
    """Build a stub for `tv_call(tool, args)` driven by a response map.

    `responses` is either:
      - dict {tool_name: response_dict_or_callable_or_Exception}, or
      - list of (predicate, response) pairs for fine-grained per-args control.

    A response that is a callable is invoked with (tool, args). A response
    that is an Exception subclass instance is raised.
    """
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        calls.append((tool, args))
        if isinstance(responses, dict):
            r = responses.get(tool, {"ok": True})
        else:
            r = next((resp for pred, resp in responses if pred(tool, args)), {"ok": True})
        if isinstance(r, Exception):
            raise r
        if callable(r):
            return r(tool, args)
        return r

    return fake, calls


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


print("P1.2 / 022 — _real_technical_levels (real-mode TA fallbacks)")


async def _run_real(ticker: str, indicators: list[str]) -> dict[str, Any]:
    from tools.technicals import _real_technical_levels  # local import — uses the patched tv_call

    return await _real_technical_levels(ticker, "1D", indicators)


# ── 1) The big one: S/R fallback must use the REAL price, not MOCK_QUOTES ──
# Reproduces Tom's NVDA trace: real price $211.14, no usable pine lines.
# Pre-022 returned resistance $959.47 (MOCK_QUOTES["NVDA"]=$942.50 × 1.018).
# Post-022 must return resistance $214.97 (real $211.14 × 1.018).

responses_real_price_no_pine = {
    "tv_health_check": {"ok": True},
    "chart_set_symbol": {"ok": True},
    "chart_set_timeframe": {"ok": True},
    "chart_manage_indicator": {"ok": True},
    "quote_get": {"price": 211.14},
    "data_get_study_values": {"value": 197.30},
    "data_get_pine_lines": {"lines": []},  # no user-drawn lines → fallback fires
    "capture_screenshot": {"data": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgAAIAAAUAAeImBZsAAAAASUVORK5CYII="},
}
fake, calls = stub_tv_call(responses_real_price_no_pine)
mcp_client.tv_call = fake

r1 = asyncio.run(_run_real("NVDA", ["SMA 50"]))
check("real current_price flows through (211.14)", r1["current_price"] == 211.14)
check("S/R derived from REAL price, not MOCK_QUOTES",
      r1["key_levels"]["resistance"] == [round(211.14 * 1.018, 2), round(211.14 * 1.06, 2)],
      "actual:", r1["key_levels"]["resistance"])
check("S/R is NOT the pre-022 mock-derived $959/$881",
      r1["key_levels"]["resistance"] != [959.47, 999.05]
      and r1["key_levels"]["support"]    != [881.24, 829.40])
check("indicator value parsed from {'value': 197.30}", r1["indicator_values"] == {"SMA 50": 197.30})
check("screenshot_url is a data: URL", r1["screenshot_url"].startswith("data:image/png;base64,"))
check("quote_get runs BEFORE data_get_pine_lines (so fallback has real price)",
      next(i for i, (t, _) in enumerate(calls) if t == "quote_get") <
      next(i for i, (t, _) in enumerate(calls) if t == "data_get_pine_lines"))


# ── 2) quote_get failure → falls back to _extract_price (mock cache) ──
# When the live price call itself fails, _extract_price IS the right last
# resort. The S/R fallback then uses that mock price — that's acceptable;
# it's the "everything live failed" case.

responses_quote_fails = dict(responses_real_price_no_pine)
responses_quote_fails["quote_get"] = mcp_client.MCPCallFailed("simulated quote_get failure")
fake, _ = stub_tv_call(responses_quote_fails)
mcp_client.tv_call = fake

r2 = asyncio.run(_run_real("NVDA", ["SMA 50"]))
check("quote_get failure → current_price falls back to MOCK_QUOTES[NVDA]",
      r2["current_price"] == 942.50)  # the mock cache value
check("S/R then matches mock baseline (acceptable last-resort)",
      r2["key_levels"]["resistance"] == [959.47, 999.05])


# ── 3) Unexpected MCP shapes are LOGGED (was silent pre-022) ──

class _LogCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record.getMessage())


cap = _LogCapture()
cap.setLevel(logging.INFO)
ta_log = logging.getLogger("tools.technicals")
ta_log.addHandler(cap)
ta_log.setLevel(logging.INFO)

responses_weird_shapes = {
    "tv_health_check":         {"ok": True},
    "chart_set_symbol":        {"ok": True},
    "chart_set_timeframe":     {"ok": True},
    "chart_manage_indicator":  {"ok": True},
    "quote_get":               {"price": 211.14},
    # Unexpected shapes — each should produce a log.info, not a silent swallow.
    "data_get_study_values":   {"unexpected_field": "no value here"},
    "data_get_pine_lines":     {"unexpected_field": "no lines here"},
    "capture_screenshot":      {"unexpected_field": "no data here"},
}
fake, _ = stub_tv_call(responses_weird_shapes)
mcp_client.tv_call = fake
cap.records.clear()

r3 = asyncio.run(_run_real("NVDA", ["SMA 50"]))
check("indicator_values empty when shape unexpected (no crash)", r3["indicator_values"] == {})
check("screenshot_url empty when shape unexpected (no crash)", r3["screenshot_url"] == "")
check("data_get_study_values shape mismatch logged",
      any("data_get_study_values" in m and "unexpected shape" in m for m in cap.records),
      "records:", cap.records)
check("data_get_pine_lines empty logged with real price reference",
      any("data_get_pine_lines" in m and "swing-derived" in m for m in cap.records),
      "records:", cap.records)
check("capture_screenshot shape mismatch logged",
      any("capture_screenshot" in m and "no `data`" in m for m in cap.records),
      "records:", cap.records)


# ── 4) Pine lines with usable labels DO override the swing fallback ──

responses_with_pine_lines = dict(responses_real_price_no_pine)
responses_with_pine_lines["data_get_pine_lines"] = {
    "lines": [
        {"y1": 220.0, "y2": 220.0, "label": "Resistance major"},
        {"y1": 205.0, "y2": 205.0, "label": "Support strong"},
    ],
}
fake, _ = stub_tv_call(responses_with_pine_lines)
mcp_client.tv_call = fake

r4 = asyncio.run(_run_real("NVDA", ["SMA 50"]))
check("pine-lines-derived resistance preferred over swing fallback",
      r4["key_levels"]["resistance"] == [220.0])
check("pine-lines-derived support preferred over swing fallback",
      r4["key_levels"]["support"] == [205.0])


# ── 5) Sources passed through verbatim (020 contract still holds) ──

check("real-mode sources still the canonical pair",
      r1["sources"] == [
          {"name": "TradingView Desktop", "url": "https://www.tradingview.com"},
          {"name": "Live OHLC via TradingView MCP"},
      ])
check("is_mock: False in real mode",
      r1["is_mock"] is False and r2["is_mock"] is False and r3["is_mock"] is False)


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

shutil.rmtree(_TMP, ignore_errors=True)

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
