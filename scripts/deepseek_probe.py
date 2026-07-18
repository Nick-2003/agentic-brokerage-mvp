#!/usr/bin/env python3
"""DeepSeek go/no-go probe (069, phase 0) — verify the tool-calling contract.

Before wiring DeepSeek into the agent loop, this confirms the two things the loop
depends on, against a REAL key:

  1. basic chat completion works;
  2. `deepseek-chat` returns FUNCTION/TOOL calls — and ideally MULTIPLE in one
     assistant message (the loop executes tool batches in parallel). If it only
     ever returns one, the loop still works but never batches; if it returns none,
     the agent is unusable on this rail.

`deepseek-reasoner` is NOT probed by default — it historically lacks function
calling; pin `deepseek-chat`.

Usage (backend venv):
    DEEPSEEK_API_KEY=sk-... backend/.venv/bin/python scripts/deepseek_probe.py

Read-only-ish: two small completions. Prints the raw tool_calls so you can see the
exact shape the loop will parse.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, "backend"))


async def main() -> int:
    if not os.getenv("DEEPSEEK_API_KEY"):
        print("Set DEEPSEEK_API_KEY first (get one at platform.deepseek.com).")
        return 2
    os.environ.setdefault("USE_MOCK_DEEPSEEK", "0")

    import deepseek_client as ds  # noqa: E402

    # A couple of real tools the app has, translated to OpenAI shape.
    tools = ds.to_openai_tools([
        {"name": "get_quote", "description": "Get a live stock quote.",
         "input_schema": {"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]}},
        {"name": "get_macro_snapshot", "description": "Get index futures / yields / VIX.",
         "input_schema": {"type": "object", "properties": {}}},
    ])

    print(f"model: {ds.deepseek_model()}   base: {ds._base_url()}\n")

    print("=== 1. plain chat ===")
    try:
        r = await ds.complete("You are terse.", [{"role": "user", "content": "Reply with the single word OK."}], max_tokens=8)
        print("  text:", repr(r["text"])[:80], "| usage:", r["usage"])
    except ds.DeepSeekError as e:
        print("  FAIL:", e); return 1

    print("\n=== 2. tool calling (asks for two quotes → wants parallel calls) ===")
    try:
        r = await ds.complete(
            "You have tools. To answer, CALL the tools; do not guess numbers.",
            [{"role": "user", "content": "Get live quotes for NVDA and AMD, then a macro snapshot."}],
            tools=tools,
            max_tokens=1024,
        )
    except ds.DeepSeekError as e:
        print("  FAIL:", e); return 1

    tcs = r["tool_calls"]
    print(f"  tool_calls returned: {len(tcs)}")
    for tc in tcs:
        print(f"    - {tc['name']}({json.dumps(tc['input'])})  id={tc['id']}")

    if not tcs:
        print("\n  ❌ NO tool calls — deepseek-chat did not use tools. The agent loop "
              "cannot run on this rail as configured. Do NOT wire failover.")
        return 1
    if len(tcs) >= 2:
        print("\n  ✅ Parallel tool calls confirmed — the loop's batch execution works.")
    else:
        print("\n  ⚠️  Only one tool call — loop works but never batches (more round-trips). Acceptable.")
    print("\nGreen enough to proceed to 069 phase 2 (loop failover wiring).")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
