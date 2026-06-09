#!/usr/bin/env python3
"""P1.2 / Proposal 024 — LLM-context screenshot strip regression test.

Targets the two helpers that solve the 200K-token blowup after 023 (where
real-mode chart prompts attach ~hundreds of KB of base64 PNG to the message
history, hitting the Claude context cap on iteration 2 or 3).

Runs entirely offline — no Anthropic API, no MCP, no Supabase. Just exercises
the helpers + serialisation pattern.

Run (after applying 024):
    backend/.venv/bin/python scripts/test_P1_024_screenshot_context.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path


def _find_repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "backend").is_dir() and (parent / "API_CONTRACT.md").is_file():
            return parent
    raise RuntimeError("repo root not found")


_REPO_ROOT = _find_repo_root()
_LIVE_BACKEND = _REPO_ROOT / "backend"
_PROPOSED = _REPO_ROOT / "backend/agent.py"

# Stage the proposed agent.py over a temp copy of the live backend so the
# package-relative imports (`from observability import …`, `from tools import …`)
# resolve. Order matters — _TMP wins resolution (LIFO insert).
_TMP = Path(tempfile.mkdtemp(prefix="agent024_"))
shutil.copytree(_LIVE_BACKEND, _TMP / "backend", symlinks=False)
if _PROPOSED.exists():
    shutil.copy(_PROPOSED, _TMP / "backend" / "agent.py")
sys.path.insert(0, str(_LIVE_BACKEND))
sys.path.insert(0, str(_TMP / "backend"))

# Avoid importing the full module (which would try to load Anthropic SDK + ENV
# + system.md). We only need the two helpers, which sit above the agent-loop
# imports.  Pull them via importlib so the heavyweight imports happen lazily.
import importlib.util

_agent_path = _TMP / "backend" / "agent.py"
_spec = importlib.util.spec_from_file_location("agent024_helpers", _agent_path)
# Stop the module from importing the rest of the world: stub out `tools` /
# `observability` / `anthropic` BEFORE exec_module runs.
import sys as _sys
import types as _types

for _stub in ("tools", "observability", "anthropic", "anthropic.types"):
    if _stub not in _sys.modules:
        _m = _types.ModuleType(_stub)
        _sys.modules[_stub] = _m
# Populate the bare symbols the agent module imports at top.
_sys.modules["observability"].NOOP_TRACER = object()
_sys.modules["observability"].Tracer = object  # type: ignore[attr-defined]
_sys.modules["tools"].TOOL_REGISTRY = {}
_sys.modules["tools"].anthropic_tool_specs = lambda: []
_sys.modules["tools"].render_thought = lambda *a, **k: ""
_sys.modules["anthropic"].AsyncAnthropic = object
_sys.modules["anthropic.types"].MessageParam = object
_sys.modules["anthropic.types"].ToolUseBlock = object

# Also stub `backend/prompts/*.md` reads — the module reads them at import.
# Easiest: set _PROMPT_DIR's expected files in a temp prompts dir on the path.
_prompts_dir = _TMP / "backend" / "prompts"
if not (_prompts_dir / "system.md").exists():
    _prompts_dir.mkdir(parents=True, exist_ok=True)
    (_prompts_dir / "system.md").write_text("stub system prompt for tests\n")
    (_prompts_dir / "widget_contract.md").write_text("stub widget contract for tests\n")

agent024 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(agent024)  # type: ignore[union-attr]

_passed = 0
_failed = 0


def check(name: str, cond: bool, *extra) -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ✓ {name}")
    else:
        _failed += 1
        print(f"  ✗ {name}", *extra)


print("P1.2 / 024 — LLM-context screenshot strip")


# Build a realistic ta_chart tool result with a large base64 screenshot.
def _huge_data_url(kb: int) -> str:
    payload = "A" * (kb * 1024)  # placeholder base64-ish content
    return "data:image/png;base64," + payload


big_screenshot = _huge_data_url(200)  # 200KB
assert len(big_screenshot) > 200_000

real_tool_result = {
    "ticker": "NVDA",
    "timeframe": "1D",
    "current_price": 211.14,
    "currency": "$",
    "indicators_applied": ["SMA 50"],
    "indicator_values": {"SMA 50": 211.30},
    "key_levels": {"resistance": [215.5, 220.0], "support": [205.0, 195.0]},
    "trend": "neutral",
    "is_mock": False,
    "screenshot_url": big_screenshot,
    "source": "tradingview_mcp",
    "sources": [
        {"name": "TradingView Desktop", "url": "https://www.tradingview.com"},
        {"name": "Live OHLC via TradingView MCP"},
    ],
}

# ── 1) _compact_for_llm replaces a large data URL with "" ──
compact = agent024._compact_for_llm(real_tool_result)
check("compact replaces large data URL with empty string",
      compact["screenshot_url"] == "")
check("compact preserves all other fields",
      {k: v for k, v in compact.items() if k != "screenshot_url"} ==
      {k: v for k, v in real_tool_result.items() if k != "screenshot_url"})
check("compact does NOT mutate the original (no aliasing)",
      real_tool_result["screenshot_url"] == big_screenshot)

# ── 2) Token-savings sanity: serialised compact form is <<200KB ──
raw_len = len(json.dumps(real_tool_result))
compact_len = len(json.dumps(compact))
check(f"compact saves >99% of bytes ({raw_len:,} → {compact_len:,} bytes)",
      compact_len < raw_len * 0.01)
check("compact serialised is small enough for many iterations",
      compact_len < 1024)

# ── 3) Small data URL is NOT stripped (e.g. mock 1x1 placeholder) ──
small_url = "data:image/png;base64," + "A" * 100
small_result = {**real_tool_result, "screenshot_url": small_url}
check("compact leaves small data URLs alone (below threshold)",
      agent024._compact_for_llm(small_result)["screenshot_url"] == small_url)

# ── 4) Empty string passes through unchanged (mock-mode / no screenshot) ──
empty_result = {**real_tool_result, "screenshot_url": ""}
check("compact leaves empty screenshot_url unchanged",
      agent024._compact_for_llm(empty_result)["screenshot_url"] == "")

# ── 5) Non-dict input passes through (defensive) ──
check("compact passes through non-dicts", agent024._compact_for_llm("hello") == "hello")
check("compact passes through None", agent024._compact_for_llm(None) is None)
check("compact passes through list", agent024._compact_for_llm([1, 2, 3]) == [1, 2, 3])

# ── 6) Idempotent (compact of compact == compact) ──
twice = agent024._compact_for_llm(compact)
check("compact is idempotent", twice == compact)


# ── 7) _restore_screenshot_in_widget puts the real URL back ──
widget = {
    "type": "ta_chart",
    "data": {
        "ticker": "NVDA",
        "timeframe": "1D",
        "current_price": 211.14,
        "screenshot_url": "",  # LLM emitted the empty sentinel
        "indicators_applied": ["SMA 50"],
        "indicator_values": {"SMA 50": 211.30},
        "key_levels": {"resistance": [215.5, 220.0], "support": [205.0, 195.0]},
        "trend_summary_html": "...",
    },
    "sources": [
        {"name": "TradingView Desktop", "url": "https://www.tradingview.com"},
        {"name": "Live OHLC via TradingView MCP"},
    ],
}
urls = {"toolu_1": big_screenshot}
agent024._restore_screenshot_in_widget(widget, urls)
check("restore puts real data URL back into widget.data.screenshot_url",
      widget["data"]["screenshot_url"] == big_screenshot)

# ── 8) Most-recent URL wins when multiple tool calls produced screenshots ──
url_a = _huge_data_url(50)
url_b = _huge_data_url(60)
widget2 = {"type": "ta_chart", "data": {"screenshot_url": ""}, "sources": []}
agent024._restore_screenshot_in_widget(widget2, {"toolu_A": url_a, "toolu_B": url_b})
check("restore picks the LAST-inserted URL (most recent tool result)",
      widget2["data"]["screenshot_url"] == url_b)

# ── 9) No screenshots tracked → widget left alone ──
widget3 = {"type": "ta_chart", "data": {"screenshot_url": ""}, "sources": []}
agent024._restore_screenshot_in_widget(widget3, {})
check("restore no-ops when urls dict is empty",
      widget3["data"]["screenshot_url"] == "")

# ── 10) Non-ta_chart widgets (no screenshot_url field) are untouched ──
widget4 = {
    "type": "morning_brief",
    "data": {"headline": "x", "paragraphs": ["y"]},
    "sources": [],
}
agent024._restore_screenshot_in_widget(widget4, urls)
check("restore leaves widgets without screenshot_url untouched",
      "screenshot_url" not in widget4["data"])

# ── 11) Widget with weird shape is robust ──
widget5 = {"type": "ta_chart", "sources": []}  # no .data dict
agent024._restore_screenshot_in_widget(widget5, urls)
check("restore is defensive against missing .data", widget5 == {"type": "ta_chart", "sources": []})


# Cleanup
shutil.rmtree(_TMP, ignore_errors=True)

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
