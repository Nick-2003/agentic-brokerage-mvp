#!/usr/bin/env python3
"""Mem0 LIVE diagnostic — pinpoints WHY store→recall came back empty (needs MEM0_API_KEY).

`mem0_probe.py` uses the high-level `memory.py` wrappers, which swallow errors by
design (best-effort). When the round-trip is empty, that hides *which* stage
failed. This script talks to the raw `AsyncMemoryClient` directly and surfaces
everything — raw responses + un-swallowed exceptions — so we can tell apart:

  (a) add extracted NOTHING        → add response `results` is empty
  (b) stored, but search latency   → get_all shows it, search empty (then retries)
  (c) stored, but search shape off → get_all shows it, search never returns it
  (d) auth/scope/transport error   → an exception is printed (not swallowed)

Run (from repo root, after `uv sync --group memory` + real MEM0_API_KEY in backend/.env):
    backend/.venv/bin/python scripts/mem0_diag.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(_BACKEND))
try:
    from dotenv import load_dotenv

    load_dotenv(_BACKEND / ".env")
except Exception:  # noqa: BLE001
    pass

USER = "diag-user-025"
FACT = "I hold NVDA and F, and I prefer conservative entries with tight stops."


def _dump(label: str, obj) -> None:
    print(f"\n── {label} ──")
    try:
        print(json.dumps(obj, indent=2, default=str)[:2000])
    except Exception:  # noqa: BLE001
        print(repr(obj)[:2000])


async def main() -> int:
    key = os.getenv("MEM0_API_KEY", "")
    if not key or key.endswith("REPLACE"):
        print("✗ MEM0_API_KEY not set in backend/.env")
        return 2

    from mem0 import AsyncMemoryClient

    client = AsyncMemoryClient(api_key=key)
    print(f"✓ client constructed (key validated). user_id = {USER!r}")

    # Clean slate so a stale memory from a prior run doesn't mask the result.
    try:
        await client.delete_all(filters={"user_id": USER})
    except Exception as e:  # noqa: BLE001
        try:
            await client.delete_all(user_id=USER)
        except Exception as e2:  # noqa: BLE001
            print(f"  (pre-clean skipped: {type(e).__name__}/{type(e2).__name__})")

    # ── STAGE 1: add ──
    print(f"\n→ add(messages=[user: {FACT!r}], user_id={USER!r})")
    add_res = await client.add([{"role": "user", "content": FACT}], user_id=USER)
    _dump("RAW add() response", add_res)
    # NOTE: add() is ASYNCHRONOUS — it returns {"event_id", "status": "PENDING"}
    # and queues server-side LLM extraction; it does NOT return the memories.
    # So judge success by get_all/search below (which poll for the result), not
    # by this response's shape.
    if isinstance(add_res, dict) and add_res.get("status"):
        print(f"  → add queued: status={add_res.get('status')} event_id={add_res.get('event_id')}"
              " (memories appear via get_all/search once extraction completes)")

    # ── STAGE 2: poll get_all (existence, independent of search relevance) ──
    print("\n→ polling get_all(filters={'user_id': …}) for up to 45s …")
    found = 0
    for i in range(9):  # 9 × 5s = 45s
        await asyncio.sleep(5)
        try:
            ga = await client.get_all(filters={"user_id": USER})
        except Exception as e:  # noqa: BLE001
            print(f"  [{(i+1)*5:>2}s] get_all raised: {type(e).__name__}: {e}")
            continue
        results = ga.get("results", ga) if isinstance(ga, dict) else ga
        found = len(results) if isinstance(results, list) else 0
        count = ga.get("count") if isinstance(ga, dict) else None
        print(f"  [{(i+1)*5:>2}s] get_all → count={count} results={found}")
        if found:
            _dump("FIRST get_all results (the actual stored memories)", results[:5])
            break

    # ── STAGE 3: search (what recall() actually calls) ──
    print("\n→ search('what do I hold?', filters={'user_id': …}, top_k=5)")
    try:
        sr = await client.search("what do I hold and how do I trade?",
                                 filters={"user_id": USER}, top_k=5)
        _dump("RAW search() response", sr)
        sresults = sr.get("results", sr) if isinstance(sr, dict) else sr
        print(f"  → search returned {len(sresults) if isinstance(sresults, list) else '?'} item(s)")
    except Exception as e:  # noqa: BLE001
        print(f"  search RAISED (this is what recall() would swallow): {type(e).__name__}: {e}")

    # ── verdict ──
    print("\n================ VERDICT ================")
    if found:
        print("Memory WAS stored (get_all found it). If search/recall is empty, it's")
        print("either (b) latency — re-run search a few seconds later — or (c) a search")
        print("filter/top_k shape issue. The raw search response above tells which.")
    else:
        print("Memory NOT found by get_all within 45s → either (a) add extracted nothing")
        print("(see RAW add results: empty?) or extraction is slower than 45s. If add's")
        print("results were non-empty but get_all is empty, the filter shape for")
        print("get_all/search differs from add's user_id kwarg — surface to fix memory.py.")

    # cleanup
    try:
        await client.delete_all(filters={"user_id": USER})
        print("\ncleaned up diag memories.")
    except Exception:  # noqa: BLE001
        try:
            await client.delete_all(user_id=USER)
            print("\ncleaned up diag memories (via user_id kwarg).")
        except Exception:  # noqa: BLE001
            print("\n(cleanup skipped — delete via the Mem0 dashboard if needed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
