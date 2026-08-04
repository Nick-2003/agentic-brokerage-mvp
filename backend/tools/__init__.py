"""Tool registry — every tool the Claude agent can call.

To add a tool:
1. Define an async function `def my_tool(args: dict, user_id: str) -> dict` in a module here.
2. Add a `TOOL_DEFINITIONS` entry: name, description, input_schema, callable, thought_template.
3. Import this module in main.py so the tool gets registered.

The registry is the contract between Claude and our system. Keep it small and explicit.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypedDict


class ToolDef(TypedDict):
    name: str
    description: str
    input_schema: dict[str, Any]
    callable: Callable[..., Awaitable[dict[str, Any]]]
    # A human-readable template the SSE 'thought' breadcrumb uses when this tool fires.
    # E.g. "Pulling overnight quotes for {tickers}" — args can be referenced as {name}.
    thought_template: str


# Registry is populated by submodule imports below.
TOOL_REGISTRY: dict[str, ToolDef] = {}


# ---------------------------------------------------------------------------
# 076 — account venue labelling. The product reads REAL holdings from IBKR
# (read-only) but ALSO exposes Alpaca PAPER positions (the execution reads) and
# demo/sample books. Every portfolio/position payload carries `account_kind`
# (machine) + `account_label` (the human chip the model copies into a widget),
# so a paper position can never be mistaken for the real book, or vice versa.
# One source of truth for the mapping, shared by portfolio.py + execution.py.
# ---------------------------------------------------------------------------

# kind → the short human label rendered as a widget chip. `none` (not connected)
# and unknown kinds intentionally have NO label (nothing to badge).
ACCOUNT_LABELS: dict[str, str] = {
    "real_ibkr": "Real · IBKR",
    "real_snaptrade": "Real · SnapTrade",
    "paper_snaptrade": "Paper · SnapTrade",
    "paper_alpaca": "Paper · Alpaca",
    "sample": "Sample · Alpaca paper",
    "mock": "Demo data",
}


def account_label(kind: str | None) -> str | None:
    """Human chip for an `account_kind`, or None when there's nothing to badge."""
    return ACCOUNT_LABELS.get(kind or "")


def tag_account(payload: dict[str, Any], kind: str) -> dict[str, Any]:
    """Stamp `account_kind` + `account_label` onto a portfolio/position payload
    (in place) and return it. Keeps the two fields in lockstep so a caller can't
    set one and forget the other."""
    payload["account_kind"] = kind
    label = account_label(kind)
    if label is not None:
        payload["account_label"] = label
    return payload


def register(tool_def: ToolDef) -> None:
    """Register a tool. Called at import time by tool modules."""
    name = tool_def["name"]
    if name in TOOL_REGISTRY:
        raise ValueError(f"Tool {name!r} already registered")
    TOOL_REGISTRY[name] = tool_def


def anthropic_tool_specs() -> list[dict[str, Any]]:
    """Format tool defs for the Anthropic API."""
    return [
        {
            "name": t["name"],
            "description": t["description"],
            "input_schema": t["input_schema"],
        }
        for t in TOOL_REGISTRY.values()
    ]


def render_thought(name: str, args: dict[str, Any]) -> str:
    """Render the human-readable breadcrumb for a tool call."""
    t = TOOL_REGISTRY.get(name)
    if not t:
        return f"Calling {name}…"
    try:
        return t["thought_template"].format(**args)
    except (KeyError, IndexError):
        return t["thought_template"]


# Import tools so they register on package load. Order matters only for human-readable
# ordering of tools shown to Claude.
from . import portfolio  # noqa: E402, F401
from . import market  # noqa: E402, F401
from . import research  # noqa: E402, F401
from . import technicals  # noqa: E402, F401
from . import execution  # noqa: E402, F401
from . import risk  # noqa: E402, F401
from . import options  # noqa: E402, F401  — 077 (data-only options chain)
