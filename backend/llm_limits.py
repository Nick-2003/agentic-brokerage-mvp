"""Shared LLM output-token caps (073).

Before this, `max_tokens` was a magic number duplicated across four files —
`agent.py` (both rails), `deepseek_client.py`, `openai_client.py` — plus three
more for the brief in `briefing.py`. The two chat rails happened to agree on
4096, but nothing *enforced* that: the parity was coincidental, and any future
edit could silently desync the rails.

073 makes it explicit and env-driven, with a per-rail override so a pinned model
with a smaller context window can be capped independently:

    max_output_tokens("openai")
        OPENAI_MAX_OUTPUT_TOKENS  →  LLM_MAX_OUTPUT_TOKENS  →  4096
    brief_max_output_tokens("anthropic")
        ANTHROPIC_BRIEF_MAX_OUTPUT_TOKENS → BRIEF_MAX_OUTPUT_TOKENS → 1024

**Unset ⇒ 4096 / 1024 ⇒ behaviour identical to pre-073.** That equivalence is
the headline assertion in `scripts/test_073_openai_cutover.py`.

Deliberately stdlib-only with NO provider imports, for two reasons:
  • the briefing cron must import it without dragging in a provider module
    (`briefing.py` is careful never to import `agent` — see 070);
  • the Anthropic rail needs it too, so it cannot live under an `openai_*` name.
House precedent: 061 moving 052's freshness logic into `freshness.py`.

⚠️ These cap OUTPUT only. Nothing here bounds INPUT — see the note in
`_env_int` callers and `.env.example`: context is bounded by CHARACTERS
(`CHAT_HISTORY_MAX_CHARS`) and by `MAX_TOOL_ITERATIONS`, never by a token count.
Adding a real input-token guard would need a tokenizer, which breaks this repo's
"raw httpx, no provider SDK" rule.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

DEFAULT_MAX_OUTPUT_TOKENS = 4096
DEFAULT_BRIEF_MAX_OUTPUT_TOKENS = 1024


def _env_int(name: str, default: int | None) -> int | None:
    """Parse an int env var, or return `default` when unset/blank/invalid.

    Fail-safe rather than fail-loud, matching `agent._rail()`: a typo in a cap
    must not take the product down, but it must be visible in the logs. A value
    of <= 0 is treated as invalid (0 would mean "no output allowed", which is
    never what anyone intends).
    """
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        val = int(raw.strip())
    except ValueError:
        log.warning("%s=%r is not an integer — ignoring", name, raw)
        return default
    if val <= 0:
        log.warning("%s=%d must be > 0 — ignoring", name, val)
        return default
    return val


def _resolve(provider: str, specific_suffix: str, general: str, default: int) -> int:
    """Per-rail override wins over the general cap, which wins over the default."""
    prefix = (provider or "").strip().upper()
    if prefix:
        val = _env_int(f"{prefix}_{specific_suffix}", None)
        if val is not None:
            return val
    return _env_int(general, default) or default


def max_output_tokens(provider: str) -> int:
    """Per-call output cap for a CHAT turn on `provider` ('anthropic'|'openai'|'deepseek')."""
    return _resolve(provider, "MAX_OUTPUT_TOKENS", "LLM_MAX_OUTPUT_TOKENS",
                    DEFAULT_MAX_OUTPUT_TOKENS)


def brief_max_output_tokens(provider: str) -> int:
    """Per-call output cap for the DAILY BRIEF on `provider`.

    Separate from the chat cap: the brief is a single tool-less completion whose
    natural length is a few paragraphs, so it has always been capped far lower
    (1024). Tying it to the chat cap would silently inflate every brief.
    """
    return _resolve(provider, "BRIEF_MAX_OUTPUT_TOKENS", "BRIEF_MAX_OUTPUT_TOKENS",
                    DEFAULT_BRIEF_MAX_OUTPUT_TOKENS)
