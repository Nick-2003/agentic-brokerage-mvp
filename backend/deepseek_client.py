"""DeepSeek fallback LLM client — the non-Anthropic rail (069).

Used ONLY when Anthropic fails on a usage-limit error and `LLM_FALLBACK_ENABLED=1`
(proposal 066/069). DeepSeek speaks the OpenAI chat-completions wire format, so this
is a thin httpx client (no `openai` SDK — house style: fmp_client / whatsapp / the
060 AV client) plus the two translations the agent loop needs:

  • tool specs:  Anthropic `{name, description, input_schema}` → OpenAI
                 `{"type":"function","function":{name, description, parameters}}`
  • response:    OpenAI `choices[0].message` (text + `tool_calls`) → the SAME uniform
                 shape the loop already handles: `{"text", "tool_calls":[{id,name,input}],
                 "usage":{input_tokens,output_tokens}}`

Mock-first (like every other provider here): `deepseek_available()` is False unless
`USE_MOCK_DEEPSEEK != "1"` AND a real `DEEPSEEK_API_KEY` is set. The mock path returns
a deterministic, tool-less completion so the failover *plumbing* can be exercised
offline without a key.

⚠️ NOT WIRED INTO THE LOOP YET. `run_agent` failover is deferred to 069 phase 2,
gated on (a) 067's validator in `enforce`, and (b) `scripts/deepseek_probe.py`
confirming `deepseek-chat` actually does (parallel) tool calls against a live key —
building the loop surgery on an unverified tool-calling contract is the exact trap
068 avoided with Vertex.

NO VISION: DeepSeek's chat API takes no image blocks. A turn carrying 059 image
attachments must NOT fall back — see `can_fall_back()`.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx  # noqa: F401 — kept in this module's API surface (probes/tests import it here)

import llm_limits
import llm_transport  # 083 — shared timeout/retry/error policy for every OpenAI-format rail
import openai_compat

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.deepseek.com"


def _base_url() -> str:
    return os.getenv("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def deepseek_model() -> str:
    # deepseek-chat (V3) supports function calling; deepseek-reasoner may NOT —
    # pin chat unless a probe proves otherwise.
    return os.getenv("DEEPSEEK_MODEL", "deepseek-chat")


def _timeout() -> float:
    """TOTAL wall-clock budget for one model call, retries included (083).
    Was a flat 60s per-attempt read timeout. Kept as a thin delegator."""
    return llm_transport.budget_seconds(provider_name().upper())


def deepseek_available() -> bool:
    """True iff the real DeepSeek path is usable (key present, mock not forced)."""
    if os.getenv("USE_MOCK_DEEPSEEK") == "1":
        return False
    key = os.getenv("DEEPSEEK_API_KEY", "")
    return bool(key) and not key.endswith("REPLACE")


def fallback_enabled() -> bool:
    """Master switch. OFF by default — must stay OFF until 067 is in `enforce`
    (a weaker model with no trust validator would render fabricated numbers)."""
    return os.getenv("LLM_FALLBACK_ENABLED", "0") == "1"


# 071 — uniform accessors so the rail dispatcher can hold either provider module
# without branching (`openai_client` exposes the same three names).
model = deepseek_model
available = deepseek_available


def provider_name() -> str:
    """073 — env-var prefix for this rail's token-cap lookup."""
    return "deepseek"


def supports_vision() -> bool:
    """Always False — DeepSeek's chat API takes no image blocks. Kept as a real
    function (not a constant) so both rail modules present an identical surface."""
    return False


def can_fall_back(attachments: list[dict[str, Any]] | None) -> bool:
    """Whether THIS turn is eligible to fall back to DeepSeek. False when the turn
    carries image attachments — DeepSeek can't see them, and silently dropping a
    chart the user attached would be a correctness failure. The caller surfaces a
    clear 'image analysis needs Claude' error instead."""
    return not attachments


class DeepSeekError(Exception):
    """Any DeepSeek fetch/parse failure. Caught by the failover orchestrator.

    083: carries a machine-readable `reason` like `KimiError` does. DeepSeek is
    the LAST rail, so nothing falls back beneath it — but the reason still reaches
    `_classify_agent_error`, which is the difference between a user reading
    "timed out, try again" and reading an empty error. `reason` is keyword-only
    and optional, so existing single-arg constructions still work.
    """

    def __init__(self, message: str, *, reason: str | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.failover_reason = reason


# --- translations -------------------------------------------------------------
# 071: the wire translations moved to `openai_compat` so the DeepSeek and OpenAI
# rails can never drift in how they present tools/history — both feed the SAME
# 067 trust check. These are thin delegators; behaviour is unchanged (same house
# pattern as 061 moving 052's freshness logic to `freshness.py`).
to_openai_tools = openai_compat.to_openai_tools
to_openai_messages = openai_compat.to_openai_messages
_parse_choice = openai_compat.parse_choice


def _mock_complete(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Deterministic, tool-less completion for offline plumbing tests."""
    return {
        "text": "[mock DeepSeek reply — set DEEPSEEK_API_KEY and USE_MOCK_DEEPSEEK=0 for the real rail]",
        "tool_calls": [],
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }


async def complete(
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """One DeepSeek turn. Returns the loop's uniform response shape.

    `messages` are NEUTRAL dicts (see `to_openai_messages`); `tools` are OpenAI-shaped
    (see `to_openai_tools`). Raises `DeepSeekError` on any transport/HTTP/parse failure.

    073: `max_tokens=None` resolves the cap from env via `llm_limits` (default
    4096, unchanged). An explicit value still wins.
    """
    if max_tokens is None:
        max_tokens = llm_limits.max_output_tokens(provider_name())
    if not deepseek_available():
        return _mock_complete(messages)

    key = os.getenv("DEEPSEEK_API_KEY", "")
    payload: dict[str, Any] = {
        "model": deepseek_model(),
        "max_tokens": max_tokens,
        "messages": to_openai_messages(system, messages),
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    # 083 — shared transport. This rail had it WORST: no response body was kept at
    # all, so a DeepSeek 4xx surfaced with the provider's reason discarded — and a
    # timeout surfaced with nothing at all. It is the fallback, so a blind spot
    # here turns one failed turn into two.
    try:
        data = await llm_transport.post_chat_completion(
            url=f"{_base_url()}/chat/completions",
            api_key=key,
            payload=payload,
            provider="deepseek",
            prefix=provider_name().upper(),
            model=deepseek_model(),
        )
    except llm_transport.LLMTransportError as e:
        raise DeepSeekError(str(e), reason=e.reason) from (e.__cause__ or e)
    return _parse_choice(data)
