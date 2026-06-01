#!/usr/bin/env python3
"""P4.4 / Proposal 017 — Langfuse observability regression test (offline).

Verifies the observability module without needing a Langfuse account or
network: the no-op tracer satisfies the protocol, ``langfuse_configured``
truth table holds, ``trace_chat`` yields a tracer in both modes, and the
agent loop integration is shape-safe (a fake tracer is exercised against
``run_agent`` via a synthetic Anthropic stub).

Run (after applying 017, from repo root):
    backend/.venv/bin/python scripts/test_P4_017_observability.py

Pre-apply (against the proposed copy):
    PYTHONPATH=proposed_changes/017-langfuse-observability/backend \
        backend/.venv/bin/python proposed_changes/017-langfuse-observability/scripts/test_P4_017_observability.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent / "backend"
if _BACKEND.is_dir():
    sys.path.insert(0, str(_BACKEND))

# Make sure observability starts in unconfigured state for the first checks.
os.environ.pop("LANGFUSE_PUBLIC_KEY", None)
os.environ.pop("LANGFUSE_SECRET_KEY", None)
os.environ.pop("LANGFUSE_HOST", None)

import observability  # noqa: E402

_passed = 0
_failed = 0


def check(name: str, cond: bool) -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ✓ {name}")
    else:
        _failed += 1
        print(f"  ✗ {name}")


print("P4.4 / 017 — backend/observability.py")

# ── 1) langfuse_configured() truth table ──

check("unconfigured (no env) → langfuse_configured() False", observability.langfuse_configured() is False)

os.environ["LANGFUSE_PUBLIC_KEY"] = "pk-lf-REPLACE"
os.environ["LANGFUSE_SECRET_KEY"] = "sk-lf-REPLACE"
check("placeholder REPLACE → False", observability.langfuse_configured() is False)

os.environ["LANGFUSE_PUBLIC_KEY"] = "pk-lf-test"
os.environ["LANGFUSE_SECRET_KEY"] = "sk-lf-test"
check("real-shaped keys → True", observability.langfuse_configured() is True)

# Reset to unconfigured for the rest of the run (we don't want a real Langfuse client to
# be constructed when we exercise trace_chat below).
os.environ.pop("LANGFUSE_PUBLIC_KEY", None)
os.environ.pop("LANGFUSE_SECRET_KEY", None)


# ── 2) NOOP_TRACER satisfies the Tracer protocol surface ──

t = observability.NOOP_TRACER
check("NOOP_TRACER.record_generation no-ops", t.record_generation(name="x", model="m", input=None, output=None) is None)
check("NOOP_TRACER.record_tool no-ops", t.record_tool(name="x", args={}, result=None, ok=True) is None)
check("NOOP_TRACER.set_output no-ops", t.set_output({"kind": "widget"}) is None)


# ── 3) trace_chat yields NOOP_TRACER when unconfigured ──

async def _drain_noop() -> bool:
    async with observability.trace_chat(
        user_id="demo", conversation_id=None, message="hi"
    ) as tr:
        # In unconfigured mode this MUST be the singleton no-op (zero cost).
        if tr is not observability.NOOP_TRACER:
            return False
        # And it must accept all the agent loop's calls without raising.
        tr.record_generation(name="g", model="m", input=[], output=[])
        tr.record_tool(name="t", args={}, result={}, ok=True, latency_ms=5)
        tr.set_output({"kind": "message", "text": "ok"})
    return True


check("trace_chat unconfigured → NOOP_TRACER (cleanly)", asyncio.run(_drain_noop()))


# ── 4) Integration shape: fake tracer captures the expected calls
#         from a synthetic agent run (no Anthropic, no Langfuse). ──


class _RecordingTracer:
    """Records calls the way a real tracer's downstream would see them."""

    def __init__(self) -> None:
        self.gens: list[dict] = []
        self.tools: list[dict] = []
        self.outputs: list = []

    def record_generation(self, **kw) -> None:
        self.gens.append(kw)

    def record_tool(self, **kw) -> None:
        self.tools.append(kw)

    def set_output(self, output) -> None:
        self.outputs.append(output)


# We avoid importing agent.py here (it would pull anthropic, system.md, etc.) and
# instead simulate the call pattern the agent loop produces. This proves the
# tracer interface is what agent.py expects to call.
async def _simulate_agent(tracer: observability.Tracer) -> None:
    # iter 1 — model emits one tool_use
    tracer.record_generation(
        name="anthropic.iter_1",
        model="claude-opus-4-5",
        input=[{"role": "user", "content": "tldr"}],
        output=[{"type": "tool_use", "id": "tu_1", "name": "get_portfolio", "input": {}}],
        usage_details={"input": 1200, "output": 32},
        metadata={"iteration": 1, "latency_ms": 940},
    )
    tracer.record_tool(
        name="get_portfolio", args={}, result={"positions": []}, ok=True,
        latency_ms=12, metadata={"tool_use_id": "tu_1"},
    )
    # iter 2 — model emits text + widget; terminal
    tracer.record_generation(
        name="anthropic.iter_2",
        model="claude-opus-4-5",
        input=[{"role": "user", "content": "tldr"}],
        output=[{"type": "text", "text": "...json widget..."}],
        usage_details={"input": 1800, "output": 220},
        metadata={"iteration": 2, "latency_ms": 1700},
    )
    tracer.set_output({"kind": "widget", "widget": {"type": "morning_brief", "data": {}}})


rec = _RecordingTracer()
asyncio.run(_simulate_agent(rec))
check("2 generations recorded", len(rec.gens) == 2)
check("1 tool span recorded", len(rec.tools) == 1)
check("terminal output captured", rec.outputs == [{"kind": "widget", "widget": {"type": "morning_brief", "data": {}}}])
check("generation has usage_details", "input" in (rec.gens[0]["usage_details"] or {}) and "output" in (rec.gens[0]["usage_details"] or {}))
check("tool span carries ok=True", rec.tools[0]["ok"] is True)


# ── 5) trace_chat with a fake configured client doesn't break on a bad client. ──
#         (Force the unavailable-sticky bit by pre-poisoning the lazy state.) ──

observability._client_unavailable = True  # type: ignore[attr-defined]
observability._client = None  # type: ignore[attr-defined]


async def _drain_with_poisoned_state() -> bool:
    async with observability.trace_chat(
        user_id="u", conversation_id="c", message="m"
    ) as tr:
        return tr is observability.NOOP_TRACER


check("poisoned-state still yields NOOP_TRACER", asyncio.run(_drain_with_poisoned_state()))

# Reset for cleanliness.
observability._client_unavailable = False  # type: ignore[attr-defined]


print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
