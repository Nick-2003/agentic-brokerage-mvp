"""Kimi (Moonshot AI) LLM client — a funded, vision-capable rail (080).

Why this exists: with Anthropic and OpenAI both credit-blocked, DeepSeek was the
only funded rail — and 074 made it the primary with **nothing beneath it**. Kimi
K2.6 is the first alternative that is simultaneously funded, vision-capable, and
strong at agentic tool use, so it both restores a real fallback chain and gives
back a feature DeepSeek cannot serve.

Probe-verified against a live key (`scripts/openai_probe.py --model kimi-k2.6 --vision`):
  ✓ basic completion, token param is `max_tokens` (NOT max_completion_tokens)
  ✓ tool calling — and **parallel**: 2 calls returned in one assistant message
  ✓ vision accepted → 059 image turns can run on this rail

Speaks the OpenAI chat-completions wire format, so this is a thin `httpx` client
(no `openai` SDK — house style) reusing the shared `openai_compat` translations.
Mock-first like every other provider here.

⚠️ HOST IS A CONFIG CHOICE, NOT A CODE CHOICE. Kimi K2.6 is open-weights, so the
same model is served by Moonshot directly *and* by US hosts (Together, DeepInfra,
NVIDIA NIM) behind the same OpenAI-compatible contract. `KIMI_BASE_URL` +
`KIMI_MODEL` switch between them with **no code change** — so the jurisdiction
question stays an operator decision, revisitable at any time. Default is Moonshot.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx  # noqa: F401 — re-exported timeout helpers keep httpx in this module's API

import llm_transport  # 083 — shared timeout/retry/error policy for every OpenAI-format rail
import openai_compat

log = logging.getLogger(__name__)

# Moonshot direct. For a US-hosted alternative set KIMI_BASE_URL + KIMI_MODEL, e.g.
#   Together   https://api.together.xyz/v1          moonshotai/Kimi-K2.6
#   DeepInfra  https://api.deepinfra.com/v1/openai  moonshotai/Kimi-K2.6
DEFAULT_BASE_URL = "https://api.moonshot.ai/v1"
DEFAULT_MODEL = "kimi-k2.6"

# Re-exported so the agent loop can treat any OpenAI-compatible rail uniformly.
to_openai_tools = openai_compat.to_openai_tools
to_openai_messages = openai_compat.to_openai_messages


def _base_url() -> str:
    return os.getenv("KIMI_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def kimi_model() -> str:
    return os.getenv("KIMI_MODEL", DEFAULT_MODEL)


# Uniform accessors so the rail dispatcher can hold this module without branching.
model = kimi_model


def provider_name() -> str:
    """Env-var prefix for this rail's token-cap lookup (`KIMI_MAX_OUTPUT_TOKENS`)."""
    return "kimi"


def _timeout() -> float:
    """TOTAL wall-clock budget for one model call, retries included (083).

    Was a flat 60s per-attempt read timeout — measured to be right at the edge of
    a healthy turn (live iterations completed in ~55s), so an ordinary slow turn
    surfaced as `KimiError: kimi request failed:` with an EMPTY message. Kept as a
    thin delegator so anything reading it keeps working.
    """
    return llm_transport.budget_seconds(provider_name().upper())


def kimi_available() -> bool:
    """True iff the real Kimi path is usable (key present, mock not forced)."""
    if os.getenv("USE_MOCK_KIMI") == "1":
        return False
    key = os.getenv("KIMI_API_KEY", "")
    return bool(key) and not key.endswith("REPLACE")


available = kimi_available


def supports_vision() -> bool:
    """True — K2.6 is natively multimodal (MoonViT encoder) and the probe confirmed
    the API accepts image parts. This is the capability DeepSeek lacks, so under
    `LLM_RAIL=kimi` the 059 image turns work again instead of hard-erroring.

    Escape hatch: `KIMI_VISION=0` if you pin a text-only model — the loop then
    REFUSES image turns rather than silently dropping the attachment (069's rule).
    """
    return os.getenv("KIMI_VISION", "1") == "1"


def can_fall_back(attachments: list[dict[str, Any]] | None) -> bool:
    """Whether THIS turn is eligible to run on Kimi. Image turns are fine when the
    pinned model is vision-capable."""
    return supports_vision() or not attachments


class KimiError(Exception):
    """Any Kimi fetch/parse failure. Caught by the rail dispatcher.

    083: carries a machine-readable `reason` (`timeout`/`network`/`rate_limit`/
    `overloaded`/None) so `agent._failover_reason` can route a TRANSPORT failure
    to the DeepSeek fallback. Before this, a timeout matched none of the billing/
    429/5xx markers and simply ended the turn. `reason` is keyword-only and
    optional, so every existing single-arg `KimiError("…")` still works.
    """

    def __init__(self, message: str, *, reason: str | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.failover_reason = reason


def _mock_complete(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Deterministic, tool-less completion for offline plumbing tests."""
    return {
        "text": "[mock Kimi reply — set KIMI_API_KEY and USE_MOCK_KIMI=0 for the real rail]",
        "tool_calls": [],
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }


async def complete(
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """One Kimi turn. Returns the agent loop's uniform response shape.

    Signature is IDENTICAL to `openai_client.complete` / `deepseek_client.complete`
    so `run_agent_openai_compat` can hold any of them without branching.
    `max_tokens=None` resolves the cap from env via `llm_limits` (073).

    The probe confirmed Kimi takes `max_tokens` (not the reasoning-model
    `max_completion_tokens`), so there is no rename branch here.
    """
    import llm_limits  # local import keeps this module importable standalone

    if max_tokens is None:
        max_tokens = llm_limits.max_output_tokens(provider_name())
    if not kimi_available():
        return _mock_complete(messages)

    key = os.getenv("KIMI_API_KEY", "")
    payload: dict[str, Any] = {
        "model": kimi_model(),
        "max_tokens": max_tokens,
        "messages": openai_compat.to_openai_messages(system, messages),
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    # 083 — one shared transport: deadline-bounded retries, and an error that is
    # NEVER empty. The response body is still preserved inside the message, which
    # is what 065's verbose-error path and the failover classifier read to tell a
    # usage limit from a config bug.
    try:
        data = await llm_transport.post_chat_completion(
            url=f"{_base_url()}/chat/completions",
            api_key=key,
            payload=payload,
            provider="kimi",
            prefix=provider_name().upper(),
            model=kimi_model(),
        )
    except llm_transport.LLMTransportError as e:
        # Re-raise as this rail's own type (callers/tests catch KimiError), keeping
        # the original httpx exception as __cause__ so the class name survives.
        raise KimiError(str(e), reason=e.reason) from (e.__cause__ or e)
    return openai_compat.parse_choice(data)
