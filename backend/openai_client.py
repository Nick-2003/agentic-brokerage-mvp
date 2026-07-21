"""OpenAI LLM client — the durable non-Anthropic PRIMARY rail (071).

Context: 068 confirmed Claude-via-Vertex is **geo-ineligible** for this HK-billed
account, and the Anthropic-direct credit balance can't be topped up in the near
future. So 069's DeepSeek rail — designed as a *transient outage* fallback — is
otherwise about to become the permanent primary, which makes each of its accepted
costs permanent too: no vision (059 breaks), PRC jurisdiction over named users'
holdings on EVERY turn, and weaker schema adherence at the exact moment 067 is in
`enforce` (a fumbled widget is now a DEAD TURN, not a logged warning).

This module is the alternative primary: same OpenAI wire format DeepSeek already
speaks (so the translations are shared via `openai_compat`), but vision-capable
and US/EU-jurisdiction.

Mock-first, same discipline as `deepseek_client` / `fmp_client` / `whatsapp`:
`openai_available()` is False unless `USE_MOCK_OPENAI != "1"` AND a real
`OPENAI_API_KEY` is set. Raw `httpx` — NO `openai` SDK dep (house style; 066
rejected the SDK explicitly and the loop needs no streaming).

⚠️ MODEL ID: `OPENAI_MODEL` defaults to `gpt-5`, which is a STARTING POINT, not a
verified id — model ids move. Run `scripts/openai_probe.py --list` to enumerate
what this key can actually reach, and confirm tool-calling + vision with a full
probe run BEFORE setting `LLM_RAIL=openai`. This is the same discipline that made
068 cheap (probe first) and the trap 069's README named: never build loop surgery
on an unverified tool-calling contract.

⚠️ REASONING MODELS: some OpenAI reasoning models reject `max_tokens` and require
`max_completion_tokens`. Set `OPENAI_USE_COMPLETION_TOKENS=1` for those; the probe
reports which parameter the chosen model accepts.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

import llm_limits
import openai_compat

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.openai.com/v1"

# Re-exported so the agent loop can treat any OpenAI-compatible rail uniformly.
to_openai_tools = openai_compat.to_openai_tools
to_openai_messages = openai_compat.to_openai_messages


def _base_url() -> str:
    return os.getenv("OPENAI_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def openai_model() -> str:
    return os.getenv("OPENAI_MODEL", "gpt-5")


# Uniform name so `agent.py` can ask any rail module for its model id.
model = openai_model


def provider_name() -> str:
    """073 — the rail's env-var prefix, so the parameterised loop can resolve this
    provider's token cap without an identity check against the module object."""
    return "openai"


def _timeout() -> float:
    return float(os.getenv("OPENAI_TIMEOUT_S", "60"))


def _use_completion_tokens() -> bool:
    return os.getenv("OPENAI_USE_COMPLETION_TOKENS", "0") == "1"


def openai_available() -> bool:
    """True iff the real OpenAI path is usable (key present, mock not forced)."""
    if os.getenv("USE_MOCK_OPENAI") == "1":
        return False
    key = os.getenv("OPENAI_API_KEY", "")
    return bool(key) and not key.endswith("REPLACE")


available = openai_available  # uniform accessor for the rail dispatcher


def supports_vision() -> bool:
    """OpenAI chat models take image parts, so 059 attachment turns CAN run here.

    Escape hatch: set `OPENAI_VISION=0` if you pin a text-only model. When False
    the loop refuses attachment turns rather than silently dropping the image —
    the same non-negotiable rule 069 applied to DeepSeek.
    """
    return os.getenv("OPENAI_VISION", "1") == "1"


def can_fall_back(attachments: list[dict[str, Any]] | None) -> bool:
    """Whether THIS turn is eligible to run on OpenAI. Unlike DeepSeek, an image
    turn is fine — provided the pinned model is vision-capable."""
    return supports_vision() or not attachments


class OpenAIError(Exception):
    """Any OpenAI fetch/parse failure. Caught by the rail dispatcher."""


def _mock_complete(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Deterministic, tool-less completion for offline plumbing tests."""
    return {
        "text": "[mock OpenAI reply — set OPENAI_API_KEY and USE_MOCK_OPENAI=0 for the real rail]",
        "tool_calls": [],
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }


async def complete(
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """One OpenAI turn. Returns the agent loop's uniform response shape.

    `messages` are NEUTRAL dicts (see `openai_compat.to_openai_messages`); `tools`
    are OpenAI-shaped. Raises `OpenAIError` on any transport/HTTP/parse failure.
    Signature is IDENTICAL to `deepseek_client.complete` so the loop can hold
    either module without branching.

    073: `max_tokens=None` resolves the cap from env via `llm_limits` (default
    4096, unchanged). An explicit value still wins, so no caller is forced to change.
    """
    if max_tokens is None:
        max_tokens = llm_limits.max_output_tokens(provider_name())
    if not openai_available():
        return _mock_complete(messages)

    key = os.getenv("OPENAI_API_KEY", "")
    payload: dict[str, Any] = {
        "model": openai_model(),
        "messages": openai_compat.to_openai_messages(system, messages),
    }
    # Reasoning models renamed this parameter; see the module docstring.
    payload["max_completion_tokens" if _use_completion_tokens() else "max_tokens"] = max_tokens
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    try:
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            resp = await client.post(
                f"{_base_url()}/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=payload,
            )
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        # Preserve the response body when there is one — the 065 verbose-error
        # path needs the provider's own reason (e.g. `insufficient_quota`) to
        # classify this as a usage limit rather than a generic API failure.
        detail = ""
        r = getattr(e, "response", None)
        if r is not None:
            try:
                detail = f" — {r.text[:300]}"
            except Exception:  # noqa: BLE001 — detail is best-effort only
                detail = ""
        raise OpenAIError(f"openai request failed: {e}{detail}") from e
    return openai_compat.parse_choice(data)
