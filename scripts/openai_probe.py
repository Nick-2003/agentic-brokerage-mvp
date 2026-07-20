#!/usr/bin/env python3
"""Probe the OpenAI rail against a REAL key before depending on it (071).

House pattern: `fmp_probe.py`, `av_news_probe.py`, `deepseek_probe.py`,
`vertex_probe.py`. The rule this encodes — earned the hard way by 026 (Mem0's
v3 API) and stated outright in 069's README — is: never build loop surgery on an
unverified provider contract. 068 stayed cheap precisely because it probed first.

Answers the four questions that decide whether `LLM_RAIL=openai` is viable:

  1. Which model ids can this key actually reach?      (--list)
  2. Does the pinned model do TOOL CALLING, in parallel?  (the agent is useless otherwise)
  3. Does it accept `max_tokens`, or does it require `max_completion_tokens`?
     (reasoning models renamed it — sets OPENAI_USE_COMPLETION_TOKENS)
  4. Does it accept IMAGE parts?  (--vision — this is the 059 fix, the main
     reason to prefer OpenAI over DeepSeek as the durable primary)

Read-only apart from the tiny completions it issues.

    export OPENAI_API_KEY=sk-...
    backend/.venv/bin/python scripts/openai_probe.py --list
    backend/.venv/bin/python scripts/openai_probe.py --model gpt-5
    backend/.venv/bin/python scripts/openai_probe.py --model gpt-5 --vision
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys

import httpx

BASE = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")

# A 1x1 PNG — enough to prove the image part is ACCEPTED (not to test acuity).
_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_quote",
            "description": "Get the latest quote for a ticker.",
            "parameters": {
                "type": "object",
                "properties": {"ticker": {"type": "string"}},
                "required": ["ticker"],
            },
        },
    }
]


def _headers() -> dict[str, str]:
    key = os.getenv("OPENAI_API_KEY", "")
    if not key or key.endswith("REPLACE"):
        sys.exit("OPENAI_API_KEY is not set (or is still the placeholder).")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


async def list_models() -> None:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{BASE}/models", headers=_headers())
    if r.status_code != 200:
        print(f"✗ /models → {r.status_code}: {r.text[:400]}")
        return
    ids = sorted(m["id"] for m in r.json().get("data", []))
    chat = [i for i in ids if i.startswith(("gpt", "o1", "o3", "o4"))]
    print(f"✓ {len(ids)} models reachable; {len(chat)} look like chat models:\n")
    for i in chat:
        print(f"    {i}")
    print("\nPick one for OPENAI_MODEL and re-run with --model <id>.")


async def _post(payload: dict) -> tuple[int, dict | str]:
    async with httpx.AsyncClient(timeout=90) as c:
        r = await c.post(f"{BASE}/chat/completions", headers=_headers(), json=payload)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, r.text[:500]


async def probe(model: str, vision: bool) -> None:
    print(f"→ model: {model}\n")

    # --- 3. which max-tokens parameter does it accept? ------------------------
    token_param = "max_tokens"
    status, body = await _post({
        "model": model,
        "max_tokens": 64,
        "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
    })
    if status != 200 and "max_completion_tokens" in json.dumps(body):
        token_param = "max_completion_tokens"
        print("⚠️  model rejects `max_tokens` → set OPENAI_USE_COMPLETION_TOKENS=1")
        status, body = await _post({
            "model": model,
            "max_completion_tokens": 64,
            "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
        })
    if status != 200:
        print(f"✗ basic completion → {status}: {json.dumps(body)[:500]}")
        print("\n  401 = bad key · 403 = not entitled · 404 = wrong model id · 429 = quota")
        return
    print(f"✓ basic completion OK (token param: `{token_param}`)")

    # --- 2. tool calling, ideally parallel ------------------------------------
    status, body = await _post({
        "model": model,
        token_param: 512,
        "tools": TOOLS,
        "tool_choice": "auto",
        "messages": [{
            "role": "user",
            "content": "Get quotes for NVDA and AAPL. Call the tool for each.",
        }],
    })
    if status != 200:
        print(f"✗ tool-calling request → {status}: {json.dumps(body)[:500]}")
        return
    calls = ((body.get("choices") or [{}])[0].get("message") or {}).get("tool_calls") or []
    if not calls:
        print("✗ NO tool_calls returned — this model cannot drive the agent loop.")
        print("  Do NOT set LLM_RAIL=openai with this model.")
        return
    names = [f"{c.get('function', {}).get('name')}({c.get('function', {}).get('arguments')})" for c in calls]
    print(f"✓ tool calling works — {len(calls)} call(s): {names}")
    if len(calls) < 2:
        print("  ⚠️  only ONE call for a two-ticker prompt — parallel tool use may be")
        print("     unsupported. The loop still works (it just takes more iterations);")
        print("     consider raising MAX_TOOL_ITERATIONS.")

    # --- 4. vision ------------------------------------------------------------
    if vision:
        data_uri = "data:image/png;base64," + base64.b64encode(_PNG_1X1).decode()
        status, body = await _post({
            "model": model,
            token_param: 64,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "What colour is this image? One word."},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            }],
        })
        if status != 200:
            print(f"✗ vision → {status}: {json.dumps(body)[:400]}")
            print("  Set OPENAI_VISION=0 (image turns will be refused, not silently dropped).")
        else:
            print("✓ vision accepted — 059 image turns can run on this rail.")

    print("\nAll green → safe to set LLM_RAIL=openai.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=os.getenv("OPENAI_MODEL", "gpt-5"))
    ap.add_argument("--list", action="store_true", help="enumerate reachable model ids")
    ap.add_argument("--vision", action="store_true", help="also probe image input")
    args = ap.parse_args()
    asyncio.run(list_models() if args.list else probe(args.model, args.vision))


if __name__ == "__main__":
    main()
