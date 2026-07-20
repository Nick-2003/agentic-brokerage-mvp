"""Shared OpenAI chat-completions wire translations (071).

Extracted from `deepseek_client.py` (069) because DeepSeek and OpenAI speak the
*same* wire format — the only real differences are base URL, key, model id, and
vision support. Keeping one copy of the translations means the two rails can
never drift apart in how they present tools or history to the model, which
matters because both feed the SAME 067 trust check.

House precedent: 061 did exactly this when 052's freshness-note logic moved to
`backend/freshness.py` and `briefing.py` kept thin delegators — no behaviour
change, one implementation.

Pure functions only: no I/O, no env reads. That makes them cheap to test and
means neither client module depends on the other.
"""
from __future__ import annotations

import json
from typing import Any


def to_openai_tools(anthropic_specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`anthropic_tool_specs()` → OpenAI `tools` array."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
            },
        }
        for t in anthropic_specs
    ]


def to_image_part(attachment: dict[str, Any]) -> dict[str, Any]:
    """One 059 attachment → an OpenAI image content part.

    059 stores `{media_type, data (base64, NO `data:` prefix), name?}`; OpenAI
    wants a single `image_url.url` carrying a full data URI. Only rails that
    actually support vision should call this (DeepSeek must not — see
    `openai_client.supports_vision()` / `deepseek_client.can_fall_back()`).
    """
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{attachment['media_type']};base64,{attachment['data']}"},
    }


def to_openai_messages(
    system: str, neutral: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Neutral message history → OpenAI `messages`.

    `neutral` items use the plain-dict shape the agent loop builds (turn-restart
    rebuilds history from text, so there are never Anthropic SDK objects here):
      {"role":"user"|"assistant", "content": str}
      {"role":"user", "content": str, "attachments":[{media_type,data}]}  # vision rails
      {"role":"assistant", "tool_calls":[{id,name,input}]}
      {"role":"tool", "tool_call_id":…, "content": str}
    A leading system message is prepended.
    """
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for m in neutral:
        role = m.get("role")
        if role == "tool":
            out.append({
                "role": "tool",
                "tool_call_id": m.get("tool_call_id", ""),
                "content": m.get("content", ""),
            })
        elif role == "assistant" and m.get("tool_calls"):
            out.append({
                "role": "assistant",
                "content": m.get("content") or None,
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc.get("input") or {}),
                        },
                    }
                    for tc in m["tool_calls"]
                ],
            })
        elif role == "user" and m.get("attachments"):
            # Multi-part user turn (text + images). An image-only turn omits the
            # text part entirely, mirroring `agent._build_user_content`.
            parts: list[dict[str, Any]] = []
            if m.get("content"):
                parts.append({"type": "text", "text": m["content"]})
            parts.extend(to_image_part(a) for a in m["attachments"])
            out.append({"role": "user", "content": parts})
        else:
            out.append({"role": role or "user", "content": m.get("content", "")})
    return out


def parse_choice(data: dict[str, Any]) -> dict[str, Any]:
    """OpenAI chat-completion response → the agent loop's uniform shape
    `{text, tool_calls:[{id,name,input}], usage:{input_tokens,output_tokens}}`."""
    choices = data.get("choices") or []
    msg = (choices[0].get("message") if choices else {}) or {}
    text = msg.get("content") or ""
    tool_calls: list[dict[str, Any]] = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except json.JSONDecodeError:
            # A model that emits malformed tool arguments gets an empty input
            # rather than crashing the turn; the tool then fails its own
            # validation and the loop surfaces it normally.
            args = {}
        tool_calls.append({
            "id": tc.get("id") or "",
            "name": fn.get("name") or "",
            "input": args,
        })
    usage = data.get("usage") or {}
    return {
        "text": text,
        "tool_calls": tool_calls,
        "usage": {
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
        },
    }
