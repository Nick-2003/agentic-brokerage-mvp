#!/usr/bin/env python3
"""P4.3 / Proposals 025+026 — live Mem0 probe (needs a real MEM0_API_KEY).

The offline test (`test_P4_025_memory.py`) stubs Mem0 and proves the *call
shapes* + scope guards. This probe is the complementary **live** check: it
drives the REAL `backend/memory.py` against the real Mem0 platform to confirm
the network leg the offline test can't — store-then-recall actually round-trips,
and the cross-user scope isolation actually holds.

It exercises the real code path (imports `memory`, calls `recall`/`remember`),
so it validates the 026 v3 fix (`filters={"user_id":…}` / `top_k`) end-to-end.

Setup:
    1. Put a real key in backend/.env:  MEM0_API_KEY=m0-...   (app.mem0.ai → API keys)
    2. cd backend && uv sync --group memory
Run (from repo root):
    backend/.venv/bin/python scripts/mem0_probe.py
    backend/.venv/bin/python scripts/mem0_probe.py --keep   # don't delete the test memories

What it does (two synthetic user_ids — NOT real users):
    • remember(A, "I hold NVDA and F and prefer conservative entries")
    • wait for Mem0's server-side extraction
    • recall(A, "what do I hold?")      → expect a block mentioning NVDA / F
    • recall(B, "what do I hold?")      → expect A's holdings ABSENT (isolation)
Exit code 0 only if A recalls something AND B does not see A's NVDA.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Load backend/.env, put backend/ on the path (top-level import convention).
_BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(_BACKEND))

try:
    from dotenv import load_dotenv

    load_dotenv(_BACKEND / ".env")
except Exception:  # noqa: BLE001
    pass

import memory  # noqa: E402

# Distinct synthetic scopes. Prefixed so they're obvious in the Mem0 dashboard
# and can't collide with a real Supabase UUID.
USER_A = "probe-user-A-025"
USER_B = "probe-user-B-025"
# Mem0 extracts facts server-side ASYNCHRONOUSLY after add() — often 10–30s on a
# cold project, well past any fixed sleep. So we POLL recall() until it lands
# rather than waiting a fixed interval (a fixed 8s wait gives false failures).
POLL_EVERY_S = 5
POLL_MAX_S = 45


async def main() -> int:
    if not memory.memory_configured():
        print("✗ MEM0_API_KEY not set (or placeholder). Set it in backend/.env first.")
        return 2

    store = memory.get_memory()
    if store is memory.NOOP_MEMORY:
        print("✗ get_memory() returned NOOP — key present but client init failed.")
        print("  (AsyncMemoryClient validates the key over the network at construction;")
        print("   an invalid key raises and falls back to NOOP. Check the key.)")
        return 2
    print(f"✓ memory configured; real store = {type(store).__name__}")

    # ── 1) store a fact for user A ──
    fact = "I hold NVDA and F, and I prefer conservative entries with tight stops."
    print(f"\n→ remember(A): {fact!r}")
    await store.remember(user_id=USER_A, user_message=fact, assistant_text=None)

    # ── 2) recall for A — POLL until the async extraction lands (or timeout) ──
    print(f"  polling recall(A) every {POLL_EVERY_S}s up to {POLL_MAX_S}s "
          "(Mem0 extraction is async)…")
    block_a = ""
    waited = 0
    while waited < POLL_MAX_S:
        await asyncio.sleep(POLL_EVERY_S)
        waited += POLL_EVERY_S
        block_a = await store.recall(user_id=USER_A, query="what do I hold and how do I trade?")
        if block_a and "NVDA" in block_a.upper():
            print(f"  → recalled after ~{waited}s")
            break
        print(f"  [{waited:>2}s] not yet…")
    print("\n── recall(A) block ──")
    print(block_a or "  (empty after timeout)")
    a_ok = bool(block_a) and ("NVDA" in block_a.upper())
    print(f"  A recalls its own holdings: {'✓' if a_ok else '✗'}")

    # ── 3) recall for B — must NOT see A's holdings (the isolation gate) ──
    block_b = await store.recall(user_id=USER_B, query="what do I hold and how do I trade?")
    print("\n── recall(B) block (should NOT contain A's NVDA/F) ──")
    print(block_b or "  (empty — expected for a fresh user)")
    b_isolated = "NVDA" not in (block_b or "").upper()
    print(f"  B does NOT see A's holdings: {'✓' if b_isolated else '✗ LEAK'}")

    # ── cleanup (best-effort; --keep to skip) ──
    if "--keep" not in sys.argv:
        try:
            client = store._client  # type: ignore[attr-defined]
            for uid in (USER_A, USER_B):
                await client.delete_all(user_id=uid)
            print("\n  cleaned up probe memories (delete_all A + B).")
        except Exception as e:  # noqa: BLE001
            print(f"\n  (cleanup skipped: {type(e).__name__} — delete via the Mem0 dashboard if needed)")

    ok = a_ok and b_isolated
    print(f"\n{'✓ PASS' if ok else '✗ FAIL'} — store/recall round-trip + cross-user isolation")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
