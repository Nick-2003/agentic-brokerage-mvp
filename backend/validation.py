"""Numeric-provenance validator for widget JSON — trust principles #1 and #3.

`CLAUDE.md` promises: *"a validator that scans widget JSON for numeric fields and
confirms they trace back to a tool call ID. Validator fails closed."* Until 067
that validator did **not exist** — trust #1 ("no number without a source") and #3
("no hallucinated data") were enforced by the system prompt alone, i.e. by the
model's instruction-following. This module makes the claim true, and is the
prerequisite gate for ever routing a turn to a weaker model (proposal 066).

## Why tiers (and not "every number must match")

A naive "every numeric value in the widget must appear verbatim in a tool result"
check FAILS CLOSED ON GOOD WIDGETS. The agent legitimately *derives* numbers:
position sizing, `notional = shares × price`, `pct_of_nav`, `rr_ratio`, a 0–10
`risk_score` judgment, proposed `tp_price`/`sl_price`. And widget prose is full of
numbers that are not claims about market data at all ("10-K", "60-day", "FY25",
"Mag 7", "2028"). Enforcing those would destroy the product with false positives.

So fields are tiered:

* **ENFORCED (tier 1)** — the hard market/account facts the agent must *never*
  invent: live prices, fill prices, share counts, unrealized P&L, analyst target.
  Each must trace to a number that some tool actually returned this turn. A miss
  here **fails closed**: the widget is suppressed (in `enforce` mode).
* **WARN (tier 2)** — every other numeric leaf (derived sizing, `%`-of-NAV,
  `risk_score`, proposed stops). Reported + logged so you can measure the real
  false-positive rate, but **never blocks**.
* **Prose numbers are not checked at all** — deliberately out of scope; the
  signal-to-noise is far too poor (years, filing names, indicator periods).

## Modes (`WIDGET_VALIDATOR_MODE`)

* `off`     — no checking.
* `warn`    — **default.** Check, log/trace violations, still emit the widget.
              Run here first to measure the false-positive rate on real traffic.
* `enforce` — fail closed: a tier-1 violation suppresses the widget.

Flip to `enforce` only once `warn` shows a clean baseline. Proposal 066's DeepSeek
fallback must not be enabled until `enforce` is on.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

MODE_OFF = "off"
MODE_WARN = "warn"
MODE_ENFORCE = "enforce"
_MODES = (MODE_OFF, MODE_WARN, MODE_ENFORCE)


def validator_mode() -> str:
    """Read per-call so env changes/tests take effect without reimport."""
    m = (os.getenv("WIDGET_VALIDATOR_MODE") or MODE_WARN).strip().lower()
    return m if m in _MODES else MODE_WARN


# --- tier 1: numeric fields that MUST trace to a tool result ------------------
# Keyed by widget `type`; values are dotted paths inside `data`.
# Deliberately excluded (agent-derived or judgment, never enforced):
#   order_ticket.*      — sizing, limit/tp/sl are PROPOSALS, not observed facts
#   portfolio_risk.*    — risk_score is a 0-10 judgment; sector pct is derived
#   thesis.weight_pct_nav, morning_brief.*  — derived / prose
_ENFORCED: dict[str, tuple[str, ...]] = {
    "research_card": ("current_price", "target_price"),
    "ta_chart": ("current_price",),
    "live_trade": (
        "shares",
        "fill_price",
        "current_price",
        "unrealized_pnl",
        "unrealized_pnl_pct",
    ),
    "tracker": (
        "trade.shares",
        "trade.fill_price",
        "trade.current_price",
        "trade.unrealized_pnl",
        "trade.unrealized_pnl_pct",
    ),
}

# Numbers inside strings (e.g. "$1,200", "-12.99%") widen the *pool* of known
# tool numbers, which only makes matching more permissive (fewer false positives).
_NUM_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_MAX_POOL = 20_000  # cap so a giant tool result can't blow up matching


@dataclass
class Violation:
    path: str
    value: float
    reason: str = "no tool result this turn contains this value"

    def __str__(self) -> str:  # for logs
        return f"{self.path}={self.value!r} ({self.reason})"


@dataclass
class ValidationResult:
    ok: bool                                   # tier-1 clean?
    violations: list[Violation] = field(default_factory=list)   # tier-1 misses
    provenance: dict[str, str] = field(default_factory=dict)    # path -> tool_use id
    checked: int = 0                           # tier-1 fields checked
    warn_unverified: list[str] = field(default_factory=list)    # tier-2 misses (informational)


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _to_float(s: str) -> float | None:
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def collect_tool_numbers(tool_facts: list[dict[str, Any]]) -> dict[float, str]:
    """Every number any tool actually returned this turn → the tool_use id that
    produced it (first writer wins; that's the "trace back to a tool call ID").

    `tool_facts` items: {"id": tool_use_id, "name": tool_name, "result": <raw result>}
    Numbers are harvested from numeric leaves AND from digits inside strings, so a
    value the tool only rendered as text (e.g. "$1,200") still counts as sourced.
    """
    pool: dict[float, str] = {}

    def add(n: float, tid: str) -> None:
        if len(pool) < _MAX_POOL:
            pool.setdefault(n, tid)

    def walk(v: Any, tid: str) -> None:
        if len(pool) >= _MAX_POOL:
            return
        if _is_num(v):
            add(float(v), tid)
        elif isinstance(v, str):
            # Skip giant blobs (e.g. base64 screenshots) — no real numbers there.
            if len(v) > 4096:
                return
            for m in _NUM_RE.findall(v):
                f = _to_float(m)
                if f is not None:
                    add(f, tid)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x, tid)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x, tid)

    for fct in tool_facts:
        walk(fct.get("result"), str(fct.get("id") or fct.get("name") or "?"))
    return pool


def _match(x: float, pool: dict[float, str]) -> str | None:
    """Return the tool id that sourced `x`, or None.

    Tolerance absorbs honest rounding (the model writes 945.00 for 945.0, or
    8,673 for 8672.61) without admitting invented numbers, which differ wildly:
    a hit needs |x-p| <= max(0.01, 0.1% of |x|), or equality at 2dp.
    """
    if x in pool:
        return pool[x]
    tol = max(0.01, abs(x) * 0.001)
    rx = round(x, 2)
    for p, tid in pool.items():
        if abs(x - p) <= tol or rx == round(p, 2):
            return tid
    return None


def _resolve(data: Any, dotted: str) -> Any:
    cur = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _numeric_leaves(v: Any, prefix: str = "") -> list[tuple[str, float]]:
    """All numeric leaves under `data`, as (dotted_path, value)."""
    out: list[tuple[str, float]] = []
    if _is_num(v):
        out.append((prefix or "<root>", float(v)))
    elif isinstance(v, dict):
        for k, x in v.items():
            out.extend(_numeric_leaves(x, f"{prefix}.{k}" if prefix else k))
    elif isinstance(v, list):
        for i, x in enumerate(v):
            out.extend(_numeric_leaves(x, f"{prefix}[{i}]"))
    return out


def validate_widget(
    widget: dict[str, Any], tool_facts: list[dict[str, Any]]
) -> ValidationResult:
    """Check a widget's numeric fields against what the tools actually returned.

    `ok` reflects TIER 1 only. Tier-2 misses land in `warn_unverified` and never
    make `ok` False. Pure/deterministic — no I/O, no env reads (the caller picks
    the mode), so it's cheap to unit-test.
    """
    wtype = widget.get("type")
    data = widget.get("data")
    if not isinstance(data, dict):
        return ValidationResult(ok=True)

    pool = collect_tool_numbers(tool_facts)
    res = ValidationResult(ok=True)

    enforced_paths = _ENFORCED.get(str(wtype), ())
    for path in enforced_paths:
        val = _resolve(data, path)
        if val is None or not _is_num(val):
            continue  # optional/absent field — omission is allowed, invention isn't
        res.checked += 1
        tid = _match(float(val), pool)
        if tid is None:
            res.violations.append(Violation(path=path, value=float(val)))
        else:
            res.provenance[path] = tid

    # Tier 2 — informational only.
    enforced_set = set(enforced_paths)
    for path, val in _numeric_leaves(data):
        if path in enforced_set:
            continue
        if _match(val, pool) is None:
            res.warn_unverified.append(f"{path}={val}")

    res.ok = not res.violations
    return res
