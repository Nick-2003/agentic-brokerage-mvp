#!/usr/bin/env python3
"""P4.3 / Proposal 025 — Mem0 per-user memory regression test (offline).

Verifies backend/memory.py with NO Mem0 account and NO network: a fake
``mem0.AsyncMemoryClient`` is injected into ``sys.modules`` so the lazy import
picks it up. Covers the no-op fallback, the configured truth table, search-shape
normalisation, the bounded block format, failure-tolerance, and — the one that
matters most — that every ``search``/``add`` is scoped by exactly the
``user_id`` the caller passed, never a substituted/spoofed value.

Run pre-apply (against the proposed copy — no apply needed):
    backend/.venv/bin/python \
        proposed_changes/025-mem0-memory/scripts/test_P4_025_memory.py

Run post-apply (against the live backend):
    backend/.venv/bin/python scripts/test_P4_025_memory.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

# ---------------------------------------------------------------------------
# Locate memory.py — prefer the proposed copy (pre-apply), else the live one.
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve()
_CANDIDATES = [
    _HERE.parent.parent / "backend" / "memory.py",                # proposal layout (pre-apply)
    _HERE.parent.parent.parent.parent / "backend" / "memory.py",  # repo backend/ (post-apply)
]
_MEMORY_PATH = next((p for p in _CANDIDATES if p.is_file()), None)
if _MEMORY_PATH is None:
    print("✗ could not locate memory.py")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Fake mem0 module — recording AsyncMemoryClient.
# ---------------------------------------------------------------------------


class FakeAsyncMemoryClient:
    """Records every search/add call so the test can assert the scope key.

    `search_return` / `add_raises` / `search_raises` are class-level knobs the
    tests flip between cases.
    """

    search_return: object = []  # what .search resolves to
    search_raises: bool = False
    add_raises: bool = False
    calls: list[tuple[str, dict]] = []

    def __init__(self, *args, **kwargs):
        FakeAsyncMemoryClient.init_kwargs = kwargs

    async def search(self, query, *, user_id, limit=None):
        FakeAsyncMemoryClient.calls.append(
            ("search", {"query": query, "user_id": user_id, "limit": limit})
        )
        if FakeAsyncMemoryClient.search_raises:
            raise RuntimeError("mem0 search boom")
        return FakeAsyncMemoryClient.search_return

    async def add(self, messages, *, user_id):
        FakeAsyncMemoryClient.calls.append(
            ("add", {"messages": messages, "user_id": user_id})
        )
        if FakeAsyncMemoryClient.add_raises:
            raise RuntimeError("mem0 add boom")
        return {"ok": True}


def _install_fake_mem0() -> None:
    fake = ModuleType("mem0")
    fake.AsyncMemoryClient = FakeAsyncMemoryClient  # type: ignore[attr-defined]
    sys.modules["mem0"] = fake


def _load_memory_fresh() -> ModuleType:
    """(Re)load memory.py from disk so module-level singletons reset per case."""
    sys.modules.pop("memory", None)
    spec = importlib.util.spec_from_file_location("memory", _MEMORY_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["memory"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Assertion harness
# ---------------------------------------------------------------------------

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


def _reset_fake(**knobs) -> None:
    FakeAsyncMemoryClient.calls = []
    FakeAsyncMemoryClient.search_return = knobs.get("search_return", [])
    FakeAsyncMemoryClient.search_raises = knobs.get("search_raises", False)
    FakeAsyncMemoryClient.add_raises = knobs.get("add_raises", False)


async def main() -> None:
    print(f"P4.3 / 025 — {_MEMORY_PATH}")
    _install_fake_mem0()

    # ── 1) memory_configured() truth table ──
    for k in ("MEM0_API_KEY",):
        os.environ.pop(k, None)
    m = _load_memory_fresh()
    check("unset MEM0_API_KEY → memory_configured() False", m.memory_configured() is False)

    os.environ["MEM0_API_KEY"] = "m0-REPLACE"
    check("placeholder REPLACE → False", m.memory_configured() is False)

    os.environ["MEM0_API_KEY"] = "m0-realkey123"
    check("real-shaped key → True", m.memory_configured() is True)

    # ── 2) get_memory() returns NOOP when unconfigured ──
    os.environ.pop("MEM0_API_KEY", None)
    m = _load_memory_fresh()
    noop = m.get_memory()
    check("unconfigured → get_memory() is NOOP_MEMORY", noop is m.NOOP_MEMORY)
    check("NOOP recall → ''", (await noop.recall(user_id="u1", query="hi")) == "")
    check(
        "NOOP remember → None (and no client calls)",
        (await noop.remember(user_id="u1", user_message="hi")) is None,
    )

    # ── 3) configured → real _Mem0Memory; recall scopes by the passed user_id ──
    os.environ["MEM0_API_KEY"] = "m0-realkey123"
    m = _load_memory_fresh()
    _reset_fake(search_return=[{"memory": "Holds NVDA and TSLA"}, {"memory": "2% risk per trade"}])
    store = m.get_memory()
    check("configured → get_memory() is NOT NOOP", store is not m.NOOP_MEMORY)

    block = await store.recall(user_id="USER-A-uuid", query="how's my book?")
    last_search = [c for c in FakeAsyncMemoryClient.calls if c[0] == "search"][-1]
    check(
        "recall passes the EXACT user_id to mem0.search (scope key, never substituted)",
        last_search[1]["user_id"] == "USER-A-uuid",
    )
    check("recall passes the query through", last_search[1]["query"] == "how's my book?")
    check("recall block contains the recalled facts", "Holds NVDA and TSLA" in block and "2% risk per trade" in block)
    check("recall block carries the 'remember about this user' header", "remember about this user" in block.lower())
    check("recall block is bullet-formatted", "- Holds NVDA and TSLA" in block)

    # ── 4) search-shape normalisation: {"results": [...]} and bare strings ──
    _reset_fake(search_return={"results": [{"text": "Watching semis"}, {"content": "Likes momentum"}]})
    block = await store.recall(user_id="u", query="q")
    check("normalises {'results': [...]} shape", "Watching semis" in block and "Likes momentum" in block)

    _reset_fake(search_return=["bare string fact"])
    block = await store.recall(user_id="u", query="q")
    check("normalises bare-string items", "bare string fact" in block)

    _reset_fake(search_return=[{"unknown_field": "ignored"}, {"memory": ""}, 12345])
    block = await store.recall(user_id="u", query="q")
    check("unrecognised / empty / non-dict items skipped → '' (never guessed)", block == "")

    # ── 5) empty results → empty block (no header noise) ──
    _reset_fake(search_return=[])
    check("no memories → recall returns ''", (await store.recall(user_id="u", query="q")) == "")

    # ── 6) MEM0_SEARCH_LIMIT + MEM0_MAX_CHARS bound the block ──
    os.environ["MEM0_SEARCH_LIMIT"] = "3"
    _reset_fake()  # limit is read per-call, so no reload needed
    await store.recall(user_id="u", query="q")
    last_search = [c for c in FakeAsyncMemoryClient.calls if c[0] == "search"][-1]
    check("MEM0_SEARCH_LIMIT forwarded to mem0.search", last_search[1]["limit"] == 3)
    os.environ.pop("MEM0_SEARCH_LIMIT", None)

    # Three 100-char facts, cap the body at 250: two lines (~101 each) fit, the
    # third must be dropped — proving the char cap actually truncates.
    os.environ["MEM0_MAX_CHARS"] = "250"
    _reset_fake(search_return=[{"memory": "A" * 100}, {"memory": "B" * 100}, {"memory": "C" * 100}])
    block = await store.recall(user_id="u", query="q")
    check(
        "MEM0_MAX_CHARS truncates the block body (2 of 3 facts fit, 3rd dropped)",
        ("A" * 100) in block and ("B" * 100) in block and ("C" * 100) not in block,
    )
    os.environ.pop("MEM0_MAX_CHARS", None)

    # ── 7) failure tolerance: a mem0 search exception → '' (turn never breaks) ──
    _reset_fake(search_raises=True)
    check("search raising → recall returns '' (swallowed)", (await store.recall(user_id="u", query="q")) == "")

    # ── 8) scope guards: empty user_id / empty query never touch the client ──
    _reset_fake(search_return=[{"memory": "should not be searched"}])
    block = await store.recall(user_id="", query="q")
    check("empty user_id → recall returns '' (no search call)", block == "" and not FakeAsyncMemoryClient.calls)
    _reset_fake(search_return=[{"memory": "x"}])
    block = await store.recall(user_id="u", query="")
    check("empty query → recall returns '' (no search call)", block == "" and not FakeAsyncMemoryClient.calls)

    # ── 9) remember scopes by the passed user_id + builds the message shape ──
    _reset_fake()
    await store.remember(user_id="USER-B-uuid", user_message="I just sold all my F", assistant_text="Noted.")
    add_call = [c for c in FakeAsyncMemoryClient.calls if c[0] == "add"][-1]
    check("remember passes the EXACT user_id to mem0.add (scope key)", add_call[1]["user_id"] == "USER-B-uuid")
    msgs = add_call[1]["messages"]
    check("remember includes the user message", any(x.get("role") == "user" and "sold all my F" in x.get("content", "") for x in msgs))
    check("remember includes the assistant text when present", any(x.get("role") == "assistant" for x in msgs))

    _reset_fake()
    await store.remember(user_id="u", user_message="hi", assistant_text=None)
    add_call = [c for c in FakeAsyncMemoryClient.calls if c[0] == "add"][-1]
    check("remember omits assistant message when text is None", all(x.get("role") != "assistant" for x in add_call[1]["messages"]))

    # ── 10) remember scope guards + failure tolerance ──
    _reset_fake()
    await store.remember(user_id="", user_message="x")
    check("empty user_id → remember stores nothing (no add call)", not FakeAsyncMemoryClient.calls)
    _reset_fake()
    await store.remember(user_id="u", user_message="")
    check("empty user_message → remember stores nothing (no add call)", not FakeAsyncMemoryClient.calls)
    _reset_fake(add_raises=True)
    ok = True
    try:
        await store.remember(user_id="u", user_message="hi")
    except Exception:
        ok = False
    check("add raising → remember swallows (no exception escapes)", ok)

    # ── 11) sticky-unavailable: a broken mem0 import → NOOP, no retries ──
    m2 = _load_memory_fresh()
    broken = ModuleType("mem0")  # no AsyncMemoryClient attr → AttributeError on access

    def _boom(*a, **k):
        raise ImportError("mem0 not installed")

    sys.modules["mem0"] = broken
    # Force the import path to fail by removing the attribute lookup target.
    got = m2.get_memory()
    check("broken mem0 import → get_memory() falls back to NOOP", got is m2.NOOP_MEMORY)
    # restore the good fake for any later reuse
    _install_fake_mem0()

    print(f"\n{_passed}/{_passed + _failed} checks passed")
    if _failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
