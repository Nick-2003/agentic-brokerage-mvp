"""Claude agent loop.

One async generator: `run_agent(user_message, user_id, tracer=…)`. It calls
Claude in a streaming tool-use loop and yields SSE event dicts.

Event types yielded:
    {"event": "thought",     "data": {"text": "..."}}
    {"event": "tool_call",   "data": {"id": "...", "name": "...", "args": {...}}}
    {"event": "tool_result", "data": {"id": "...", "ok": True, "summary": "..."}}
    {"event": "widget",      "data": {"type": "...", "data": {...}, "sources": [...]}}
    {"event": "message",     "data": {"text": "markdown response"}}
    {"event": "error",       "data": {"message": "..."}}
    {"event": "done",        "data": {"elapsed_ms": N, "iterations": N}}

P4.4 (proposal 017): an optional `tracer` parameter receives a Langfuse-backed
``Tracer`` (or the silent ``NOOP_TRACER``). The loop records one *generation*
per Anthropic call and one *tool* span per executed tool. Read-only; never
alters behaviour. Default = NOOP_TRACER, so existing callers keep working.

Proposal 024: large `screenshot_url` data-URL fields are stripped from tool
results BEFORE re-injecting them into the LLM message history (they balloon
the context to 200K+ tokens after two real-mode chart calls). The full URLs
are restored on the terminal widget emission so the frontend still gets the
rendered image. See `_compact_for_llm()` and the screenshot-restore block
inside `run_agent`.

P4.3 (proposal 025): an optional `memory_context` string — this user's recalled
Mem0 facts, already formatted as a system-prompt block by `memory.recall()` —
is appended to the system prompt for this turn only. The agent itself stays
memory-agnostic: `main.py` does the per-user search (scoped by the authenticated
`user_id`) and the post-turn store; `run_agent` only injects what it's handed.
Default `""` → behaviour identical to pre-025.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from anthropic import (
    APIConnectionError,
    APIStatusError,
    AsyncAnthropic,
    AuthenticationError,
    PermissionDeniedError,
    RateLimitError,
)
from anthropic.types import MessageParam, ToolUseBlock

import deepseek_client  # 069 — DeepSeek fallback rail
import llm_limits  # 073 — shared, env-driven output-token caps
import openai_client  # 071 — OpenAI rail (selectable primary)
import validation  # 067 — numeric-provenance validator (trust #1/#3)
from observability import NOOP_TRACER, Tracer
from tools import TOOL_REGISTRY, anthropic_tool_specs, render_thought  # noqa: F401

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 069 — provider failover (Anthropic → DeepSeek on a usage-limit error)
# ---------------------------------------------------------------------------


class ProviderFailover(Exception):
    """Raised inside the Anthropic loop when the turn should restart on DeepSeek.
    Carries the classified `reason` (billing/rate_limit/overloaded) for the UI."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _failover_reasons() -> set[str]:
    raw = os.getenv("LLM_FAILOVER_ON", "billing,rate_limit,overloaded")
    return {r.strip() for r in raw.split(",") if r.strip()}


# 071 — OpenAI-specific usage-limit markers. The 068 README flagged exactly this
# trap for Vertex: 065's markers are Anthropic-DIRECT phrasing, so a rail that
# words exhaustion differently would never trigger failover — the failure mode
# would be invisible on the very rail we moved to. OpenAI says `insufficient_quota`
# / "exceeded your current quota", neither of which matches `_BILLING_MARKERS`.
_OPENAI_BILLING_MARKERS = (
    "insufficient_quota",
    "exceeded your current quota",
    "billing_hard_limit_reached",
)


def _failover_reason(e: Exception) -> str | None:
    """Map a PRIMARY-rail error to a coarse failover reason, or None if it's not a
    usage-limit failure (auth/bad-request/etc. must NOT fail over — a config bug
    should stay loud, and a genuine 400 would just fail on the fallback too).

    Handles BOTH primaries (071): Anthropic SDK exceptions and OpenAI HTTP errors,
    whose bodies `openai_client.complete` preserves for exactly this reason.
    """
    text = str(e).lower()
    if any(m in text for m in _BILLING_MARKERS) or any(
        m in text for m in _OPENAI_BILLING_MARKERS
    ):
        return "billing"
    if isinstance(e, RateLimitError) or "rate limit" in text or "429" in text:
        return "rate_limit"
    status = getattr(e, "status_code", None)
    if status in (503, 529) or "overloaded" in text:
        return "overloaded"
    # OpenAI surfaces 5xx/429 through httpx, where the status lives on .response.
    resp = getattr(e, "response", None)
    rstatus = getattr(resp, "status_code", None)
    if rstatus == 429:
        return "rate_limit"
    if rstatus in (500, 502, 503, 529):
        return "overloaded"
    return None


def _should_failover(e: Exception, attachments: list[dict[str, Any]] | None) -> str | None:
    """The reason to fail over, or None. Requires: fallback on, DeepSeek usable,
    this turn is fall-back-eligible (no images — DeepSeek has no vision), and the
    error is a configured usage-limit reason."""
    if not deepseek_client.fallback_enabled() or not deepseek_client.deepseek_available():
        return None
    if not deepseek_client.can_fall_back(attachments):
        return None
    reason = _failover_reason(e)
    if reason and reason in _failover_reasons():
        return reason
    return None


# ---------------------------------------------------------------------------
# 071 — RAIL SELECT. `LLM_RAIL` picks the PRIMARY provider; DeepSeek stays the
# fallback beneath whichever primary is chosen.
#
# Why this exists: 069 hardcoded Anthropic-first, which was right when Anthropic
# was healthy and DeepSeek covered rare outages. With 068 confirmed geo-ineligible
# and Anthropic credits durably empty, "call Anthropic, watch it fail, restart on
# DeepSeek" would run on 100% of turns — a doomed API call per turn and a UI that
# permanently claims to be a transient "fallback". Selecting the primary directly
# is both cheaper and honest.
#
#   LLM_RAIL=anthropic  (default — unchanged 069 behaviour)
#   LLM_RAIL=openai     (071 — OpenAI primary; vision-capable, US/EU jurisdiction)
# ---------------------------------------------------------------------------

_VALID_RAILS = ("anthropic", "openai")


def _rail() -> str:
    """The configured primary rail. Falls back to `anthropic` on an unknown value
    (fail-safe: a typo must not silently route a live product to a rail nobody
    intended) and logs loudly."""
    raw = (os.getenv("LLM_RAIL", "anthropic") or "anthropic").strip().lower()
    if raw not in _VALID_RAILS:
        log.warning("unknown LLM_RAIL=%r — falling back to 'anthropic'", raw)
        return "anthropic"
    return raw

_PROMPT_DIR = Path(__file__).parent / "prompts"


def _load_system_prompt() -> str:
    system = (_PROMPT_DIR / "system.md").read_text()
    contract = (_PROMPT_DIR / "widget_contract.md").read_text()
    return f"{system}\n\n---\n\n# Widget JSON contract\n\n{contract}"


SYSTEM_PROMPT = _load_system_prompt()
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-5")
MAX_ITERATIONS = int(os.getenv("MAX_TOOL_ITERATIONS", "10"))


_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic()  # reads ANTHROPIC_API_KEY from env
    return _client


# ---------------------------------------------------------------------------
# Widget JSON extraction — Claude wraps widget JSON in a ```json … ``` block.
# ---------------------------------------------------------------------------

_JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)
_RAW_JSON_RE = re.compile(r"^\s*(\{.*\})\s*$", re.DOTALL)


def _repair_json_quotes(s: str) -> str:
    """Best-effort repair of the #1 widget-JSON failure: an **unescaped double
    quote inside a string value** (063). The model writes prose like
    ``… even with "infinite money." CoreWeave …`` inside a `"paragraphs"` entry;
    those inner quotes break `json.loads`, the widget doesn't render, and the raw
    JSON leaks to the user as a code block.

    Heuristic: walk the text tracking string state. Inside a string, a `"` is a
    REAL terminator only when the next non-whitespace char is structural
    (`,` `]` `}` `:`) or end-of-input; otherwise it's a content quote and gets
    escaped (`\\"`). This precisely fixes the observed case while leaving valid
    JSON (and already-escaped quotes) untouched.

    Imperfect by nature (prose containing `",` — a quote immediately before a
    comma — can still be misread), so the caller re-parses AND re-validates the
    shape; a bad repair simply fails to parse and falls back to the plain-text
    path (never worse than today, never a corrupted widget).
    """
    out: list[str] = []
    in_str = False
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if not in_str:
            out.append(c)
            if c == '"':
                in_str = True
            i += 1
        elif c == "\\":  # keep any escape pair verbatim
            out.append(c)
            if i + 1 < n:
                out.append(s[i + 1])
                i += 2
            else:
                i += 1
        elif c == '"':
            j = i + 1
            while j < n and s[j] in " \t\r\n":
                j += 1
            if j >= n or s[j] in ",]}:":
                out.append('"')  # real terminator
                in_str = False
            else:
                out.append('\\"')  # content quote → escape
            i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _loads_widget(raw: str) -> dict[str, Any] | None:
    """`json.loads(raw)`, with a one-shot quote-repair retry on failure. Returns a
    dict only if it parses AND matches the widget envelope (`type` + `data`);
    otherwise None (fail-closed)."""
    for candidate in (raw, _repair_json_quotes(raw)):
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "type" in obj and "data" in obj:
            return obj
        return None  # parsed but not a widget → don't bother repairing further
    return None


def _extract_widget_json(text: str) -> dict[str, Any] | None:
    """Try to parse a widget JSON from the model's final message.

    Returns None if no widget found (fall through to plain markdown message).
    063: tolerates an unescaped inner double quote via `_loads_widget`'s repair.
    """
    if not text:
        return None
    # Prefer a fenced block, fall back to a single bare JSON object.
    m = _JSON_BLOCK_RE.search(text) or _RAW_JSON_RE.match(text.strip())
    if not m:
        return None
    return _loads_widget(m.group(1))


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------


async def _call_tool(name: str, args: dict[str, Any], user_id: str) -> tuple[bool, Any]:
    """Execute one tool. Returns (ok, content). content is JSON-serialisable."""
    t = TOOL_REGISTRY.get(name)
    if not t:
        return False, {"error": "unknown_tool", "name": name}
    try:
        result = await t["callable"](args, user_id)
        return True, result
    except Exception as e:  # pragma: no cover — diagnostic catch
        return False, {"error": "tool_exception", "name": name, "message": str(e)}


def _summarize_tool_result(name: str, ok: bool, result: Any) -> str:
    """Short human-readable line shown in the streaming UI breadcrumbs (dev mode)."""
    if not ok:
        return f"{name} failed"
    if isinstance(result, dict):
        if result.get("error"):
            return f"{name} → {result['error']}"
        if "positions" in result and isinstance(result["positions"], list):
            eq = result.get("total_equity")
            eq_str = f", ${eq:,.0f} equity" if isinstance(eq, (int, float)) else ""
            return f"{name} → {len(result['positions'])} positions{eq_str}"
        if "quotes" in result and isinstance(result["quotes"], list):
            return f"{name} → {len(result['quotes'])} quotes"
        if "rating" in result:
            return f"{name} → {result.get('ticker', '')} {result['rating']}"
        if "order_id" in result:
            return f"{name} → order {result['order_id']}"
        if "risk_score" in result:
            return f"{name} → risk {result['risk_score']}/10"
    mock = " (mock)" if isinstance(result, dict) and result.get("is_mock") else ""
    return f"{name} → ok{mock}"

# Added for Langfuse tracking; we want to record the full content blocks for each generation, but they can contain unserialisable objects, so we convert them to simple dicts with best-effort extraction of key info.
def _serialise_blocks(content: list[Any]) -> list[dict[str, Any]]:
    """Render Anthropic content blocks into Langfuse-friendly JSON dicts."""
    out: list[dict[str, Any]] = []
    for b in content:
        try:
            if b.type == "text":
                out.append({"type": "text", "text": b.text})
            elif b.type == "tool_use":
                out.append(
                    {
                        "type": "tool_use",
                        "id": getattr(b, "id", None),
                        "name": getattr(b, "name", None),
                        "input": getattr(b, "input", None),
                    }
                )
            else:
                out.append({"type": getattr(b, "type", "unknown")})
        except Exception:  # noqa: BLE001  — observability is best-effort
            out.append({"type": "unserialisable"})
    return out


# ---------------------------------------------------------------------------
# Proposal 024 — context compaction for the LLM message history.
#
# Tool results can contain very large fields (TradingView chart screenshots
# come back as `data:image/png;base64,…` URLs, ~30K-300K tokens each). When
# these are echoed back to the LLM in subsequent iterations via the
# `tool_result` message content, two parallel chart calls easily exceed the
# 200K context cap. We strip such fields BEFORE serialising into the message
# history, and restore the originals on the terminal widget emission so the
# frontend still gets the rendered image.
# ---------------------------------------------------------------------------


# Threshold below which a `data:` URL is small enough to be irrelevant
# (placeholder / 1×1 transparent / empty). Above this we treat it as a real
# screenshot and strip from LLM context.
_SCREENSHOT_STRIP_THRESHOLD = 1024


def _compact_for_llm(result: Any) -> Any:
    """Return a copy of ``result`` with heavy opaque fields replaced by short
    placeholders so the LLM context doesn't blow past 200K tokens.

    Two things are removed before a tool result enters the LLM context:
    - ``screenshot_url`` — large data URLs (blow past 200K tokens); replaced with
      "" (the "frontend renders MockChartSvg" sentinel, 019/023) and held in
      ``screenshot_urls_by_tool`` for re-attach on the terminal widget.
    - ``account_id`` (069) — `get_portfolio`'s IBKR account number. The model
      never needs it and it must not leak to ANY provider (Anthropic today,
      DeepSeek on fallback). Redacted here; the raw value still reaches the 067
      validator via `tool_facts`, but never the LLM.

    Idempotent: returns the input unchanged when nothing needs stripping.
    """
    if not isinstance(result, dict):
        return result
    out = result
    su = result.get("screenshot_url")
    if isinstance(su, str) and su.startswith("data:") and len(su) > _SCREENSHOT_STRIP_THRESHOLD:
        out = {**out, "screenshot_url": ""}
    if result.get("account_id") not in (None, ""):
        out = {**out, "account_id": "[redacted]"}
    return out


def _restore_screenshot_in_widget(
    widget: dict[str, Any], urls_by_tool_id: dict[str, str]
) -> dict[str, Any]:
    """If the terminal widget has a `screenshot_url` field (only `ta_chart`
    does today) and we stripped one in this turn, restore the most recent
    real data URL we saw — overriding whatever the LLM emitted (it can only
    have emitted the empty-string sentinel since it never saw the real URL).

    "Most recent" = last-inserted in `urls_by_tool_id`. Dict insertion order
    is preserved (Python 3.7+), so this naturally tracks the order tools
    completed in. Mutates `widget` in-place AND returns it for chainability.
    """
    if not urls_by_tool_id:
        return widget
    data = widget.get("data")
    if not isinstance(data, dict):
        return widget
    if "screenshot_url" not in data:
        return widget
    # Use the most recent screenshot from this turn — that's the chart state
    # the user is looking at right now.
    data["screenshot_url"] = next(reversed(urls_by_tool_id.values()))
    return widget


# ---------------------------------------------------------------------------
# 069 — shared terminal emission (widget/message + 067 trust validation).
# Extracted so BOTH provider loops (Anthropic and DeepSeek) run the IDENTICAL
# trust check + fail-closed behaviour — a weaker fallback model must not get a
# weaker validator. Behaviour is byte-for-byte the pre-069 Anthropic path.
# ---------------------------------------------------------------------------


async def _finalize_terminal_widget(
    full_text: str,
    tool_facts: list[dict[str, Any]],
    screenshot_urls_by_tool: dict[str, str],
    tracer: Tracer,
) -> AsyncGenerator[dict[str, Any], None]:
    """Parse the terminal message → widget/plain-text, restore screenshots, run
    the 067 numeric-provenance validator, and emit the right SSE event."""
    widget = _extract_widget_json(full_text)
    if widget is not None:
        _restore_screenshot_in_widget(widget, screenshot_urls_by_tool)

        mode = validation.validator_mode()
        blocked = False
        audit: dict[str, Any] | None = None
        if mode != validation.MODE_OFF:
            vres = validation.validate_widget(widget, tool_facts)
            audit = {
                "mode": mode,
                "ok": vres.ok,
                "checked": vres.checked,
                "violations": [str(v) for v in vres.violations],
                "provenance": vres.provenance,
                "warn_unverified": vres.warn_unverified[:20],
            }
            if vres.violations or vres.warn_unverified:
                log.warning(
                    "widget validation [%s] type=%s checked=%d violations=%s warn=%s",
                    mode, widget.get("type"), vres.checked,
                    [str(v) for v in vres.violations], vres.warn_unverified[:8],
                )
            if mode == validation.MODE_ENFORCE and not vres.ok:
                blocked = True
                fields = ", ".join(v.path for v in vres.violations)
                detail = f" Unverified: {fields}." if _verbose_errors() else ""
                msg = (
                    "I couldn't verify some figures in that card against the "
                    f"data my tools returned, so I'm not showing it.{detail} "
                    "Please try again."
                )
                tracer.set_output(
                    {"kind": "error", "code": "widget_unverified", "validation": audit}
                )
                yield {"event": "error", "data": {"message": msg, "code": "widget_unverified"}}

        if not blocked:
            out: dict[str, Any] = {"kind": "widget", "widget": widget}
            if audit is not None:
                out["validation"] = audit
            tracer.set_output(out)
            yield {"event": "widget", "data": widget}
    elif full_text:
        tracer.set_output({"kind": "message", "text": full_text})
        yield {"event": "message", "data": {"text": full_text}}


# ---------------------------------------------------------------------------
# 059 — build the current user turn's content (vision input)
# ---------------------------------------------------------------------------


def _build_user_content(
    user_message: str, attachments: list[dict[str, Any]] | None
) -> str | list[dict[str, Any]]:
    """The `content` for the current user message.

    No attachments → the plain string (unchanged pre-059 behaviour). With
    attachment(s) → a list of content blocks: an optional leading text block
    (omitted for an image-only turn) then one base64 `image` block per attachment.
    Each attachment is ``{media_type, data (base64, no data: prefix), name?}``.
    """
    if not attachments:
        return user_message
    blocks: list[dict[str, Any]] = []
    if user_message:
        blocks.append({"type": "text", "text": user_message})
    for att in attachments:
        blocks.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": att["media_type"],
                "data": att["data"],
            },
        })
    return blocks


# ---------------------------------------------------------------------------
# 064 — provider-error classification. The Anthropic API can fail for reasons
# the user must NOT see verbatim: an out-of-credit account returns
# `400 … "Your credit balance is too low …"`, and leaking that (or a raw
# request_id / model name) both confuses the user and exposes account state.
# Map every failure to a short, safe, actionable message + a stable `code`;
# the full exception is logged server-side (and to the tracer) for debugging.
# ---------------------------------------------------------------------------

# Substrings that mark a billing/quota problem regardless of the SDK error class
# (the credit-balance error arrives as a plain 400 BadRequestError).
_BILLING_MARKERS = ("credit balance", "billing", "quota", "insufficient", "payment")

# 065 — VERBOSE mode (default on). Shows the specific error type + the provider's
# own reason so the operator/tester can act (top up credits, fix the key, wait out
# a rate limit). Set CHAT_VERBOSE_ERRORS=0 to fall back to the generic, non-leaky
# messages below (recommended once real/untrusted users are on it).
_MSG_UNAVAILABLE = (
    "The assistant is temporarily unavailable. Our team has been notified — "
    "please try again a little later."
)
_MSG_BUSY = "The assistant is busy right now — please try again in a moment."
_MSG_GENERIC = "Something went wrong generating that. Please try again."

_DETAIL_MAX = 300  # cap a provider message so it can't dump a huge blob


def _verbose_errors() -> bool:
    return os.getenv("CHAT_VERBOSE_ERRORS", "1") != "0"


def _provider_detail(e: Exception) -> tuple[int | None, str | None, str | None]:
    """Best-effort (http_status, provider_error_type, provider_message) from an
    Anthropic APIStatusError. (None, None, None) for non-API errors."""
    status = getattr(e, "status_code", None)
    etype = pmsg = None
    body = getattr(e, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            etype = err.get("type")
            pmsg = err.get("message")
    return status, etype, pmsg


def _classify_agent_error(e: Exception) -> tuple[str, str]:
    """Map an exception to (user_message, code).

    Default (CHAT_VERBOSE_ERRORS != "0"): the message NAMES the specific error and
    WHY it's happening (e.g. "Anthropic API — billing/credits: Your credit balance
    is too low …"). With CHAT_VERBOSE_ERRORS=0 it returns the generic, non-leaky
    copy so provider/billing detail never reaches untrusted users. The full
    exception is always logged server-side + on the tracer regardless."""
    text = str(e).lower()
    status, etype, pmsg = _provider_detail(e)
    verbose = _verbose_errors()

    def _reason(fallback: str) -> str:
        return (pmsg or fallback).strip()[:_DETAIL_MAX]

    # Billing / quota — the account can't make calls.
    if any(m in text for m in _BILLING_MARKERS):
        if verbose:
            return (
                f"Anthropic API — billing/credits: {_reason('your credit balance is too low.')} "
                "(Fix: add credits at console.anthropic.com → Plans & Billing, then retry.)",
                "provider_unavailable",
            )
        return _MSG_UNAVAILABLE, "provider_unavailable"

    # Rate limited — transient; invite a retry.
    if isinstance(e, RateLimitError) or "rate limit" in text or "429" in text:
        if verbose:
            return (
                f"Anthropic API — rate limited (HTTP {status or 429}): too many requests. "
                "Wait a moment and try again.",
                "rate_limited",
            )
        return _MSG_BUSY, "rate_limited"

    # Auth / permission — a key/config problem, not the user.
    if isinstance(e, (AuthenticationError, PermissionDeniedError)):
        return (
            (
                f"Anthropic API — authentication (HTTP {status or 401}): the API key was "
                f"rejected. {_reason('Check ANTHROPIC_API_KEY.')}"
                if verbose
                else _MSG_UNAVAILABLE
            ),
            "provider_unavailable",
        )

    # Connectivity — couldn't reach the provider.
    if isinstance(e, APIConnectionError):
        return (
            (
                f"Anthropic API — connection failed ({type(e).__name__}): couldn't reach the "
                "provider. Check connectivity and retry."
                if verbose
                else _MSG_UNAVAILABLE
            ),
            "provider_unavailable",
        )

    # Any other API status error (5xx, unexpected 4xx).
    if isinstance(e, APIStatusError):
        if verbose:
            label = f"HTTP {status}" + (f" {etype}" if etype else "")
            return (f"Anthropic API error ({label}): {_reason(str(e))}", "provider_error")
        return _MSG_UNAVAILABLE, "provider_error"

    # Non-provider failure (a bug in our loop / an unexpected crash).
    if verbose:
        return (f"Error: {type(e).__name__}: {str(e)[:_DETAIL_MAX]}", "agent_error")
    return _MSG_GENERIC, "agent_error"


# ---------------------------------------------------------------------------
# Main agent loop
# ---------------------------------------------------------------------------


async def run_agent(
    user_message: str,
    user_id: str = "demo",
    tracer: Tracer = NOOP_TRACER,
    memory_context: str = "",
    history: list[MessageParam] | None = None,
    attachments: list[dict[str, Any]] | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Run one user turn through the Claude tool-use loop.

    Yields SSE event dicts. ``tracer`` (P4.4) receives generations + tool spans;
    pass ``NOOP_TRACER`` (the default) when observability isn't wanted.

    ``memory_context`` (P4.3) is this user's recalled Mem0 facts, pre-formatted
    as a system-prompt block by ``memory.recall()``. It's appended to the system
    prompt for THIS turn only; ``""`` (the default) leaves the prompt unchanged.

    ``history`` (proposal 046 — conversation memory) is this conversation's
    prior turns as Anthropic ``{"role", "content"}`` messages (built by
    ``db.to_agent_history`` from the persisted thread — alternation-safe and
    bounded), seeded BEFORE the current user message so the agent remembers
    earlier turns in the same chat. ``None`` (the default) → behaviour identical
    to pre-046 (single-message turn). ``main.py`` only supplies it for persisted
    (authenticated) conversations; demo turns stay historyless.

    ``attachments`` (059 — vision input) are the user's uploaded image(s) for THIS
    turn: a list of ``{media_type, data (base64, no data: prefix), name?}``. When
    present, the current user message is built as a LIST of content blocks (text +
    one ``image`` block per attachment) instead of a plain string — the Anthropic
    SDK/model already accept this (it's the same list-content form used for
    ``tool_result`` turns). EPHEMERAL by design: used only this turn, never
    persisted or replayed (``main.py`` stores the message text only), so later turns
    don't re-bill image tokens. ``None`` (the default) → the plain-string path.
    """
    start_time = time.monotonic()
    client = _get_client()
    # P4.3: per-turn system prompt = the static prompt + this user's recalled
    # memories (already scoped to the authenticated user by main.py). Built once
    # here, reused across every iteration of the tool loop below.
    system_prompt = (
        f"{SYSTEM_PROMPT}{memory_context}"
        if isinstance(memory_context, str) and memory_context
        else SYSTEM_PROMPT
    )
    # Proposal 046: seed with this conversation's prior turns (if any) so the
    # agent has multi-turn memory, then append the current user message. `list(...)`
    # copies the caller's history so the in-loop appends (assistant/tool turns)
    # don't mutate it.
    messages: list[MessageParam] = list(history or [])
    # 059 — vision input. With attachment(s) the current turn is a list of content
    # blocks (text + one `image` block per image); otherwise a plain string.
    messages.append(  # type: ignore[typeddict-item]
        {"role": "user", "content": _build_user_content(user_message, attachments)}
    )
    iterations = 0
    # P5 (034): accumulate this turn's LLM token usage across iterations so the
    # `done` event can surface it for the per-user daily token budget.
    total_input_tokens = 0
    total_output_tokens = 0
    # Per-turn screenshot accounting (proposal 024). Maps `tool_use_id` → real
    # `data:image/png;base64,…` URL we stripped from the LLM-bound payload.
    # Restored into the terminal widget below.
    screenshot_urls_by_tool: dict[str, str] = {}
    # 067 — every successful tool result this turn, kept RAW (pre-`_compact_for_llm`)
    # so the numeric-provenance validator sees exactly what the tool returned.
    tool_facts: list[dict[str, Any]] = []

    try:
        while iterations < MAX_ITERATIONS:
            iterations += 1

            # Stream the next assistant turn. We don't yield content_block deltas
            # to the client — we only emit thoughts on tool calls and the final
            # parsed widget/message. Lower noise; less to render.
            iter_started = time.monotonic()
            messages_snapshot = list(messages)
            async with client.messages.stream(
                model=MODEL,
                max_tokens=llm_limits.max_output_tokens("anthropic"),  # 073
                system=system_prompt,
                tools=anthropic_tool_specs(),
                messages=messages,
            ) as stream:
                final_msg = await stream.get_final_message()

            # Record the generation for this iteration (no-op when tracer is NOOP).
            usage = getattr(final_msg, "usage", None)
            total_input_tokens += getattr(usage, "input_tokens", 0) or 0
            total_output_tokens += getattr(usage, "output_tokens", 0) or 0
            tracer.record_generation(
                name=f"anthropic.iter_{iterations}",
                model=MODEL,
                input=messages_snapshot,
                output=_serialise_blocks(final_msg.content),
                usage_details=(
                    {
                        "input": getattr(usage, "input_tokens", 0) or 0,
                        "output": getattr(usage, "output_tokens", 0) or 0,
                    }
                    if usage is not None
                    else None
                ),
                metadata={
                    "iteration": iterations,
                    "latency_ms": int((time.monotonic() - iter_started) * 1000),
                },
            )

            # Separate text and tool_use blocks from the response
            text_parts: list[str] = []
            tool_uses: list[ToolUseBlock] = []
            for block in final_msg.content:
                if block.type == "text":
                    text_parts.append(block.text)
                elif block.type == "tool_use":
                    tool_uses.append(block)

            # Append the full assistant turn to message history
            messages.append({"role": "assistant", "content": final_msg.content})

            # Terminal turn — no more tool calls. Emit widget or text (069: via the
            # shared finalizer so the Anthropic + DeepSeek rails validate identically).
            if not tool_uses:
                full_text = "".join(text_parts).strip()
                async for ev in _finalize_terminal_widget(
                    full_text, tool_facts, screenshot_urls_by_tool, tracer
                ):
                    yield ev
                break

            # Non-terminal turn — execute tool calls (in parallel for batches).
            tool_tasks = []
            for tu in tool_uses:
                # Emit thought + tool_call breadcrumbs before execution
                yield {
                    "event": "thought",
                    "data": {"text": render_thought(tu.name, tu.input or {})},
                }
                yield {
                    "event": "tool_call",
                    "data": {"id": tu.id, "name": tu.name, "args": tu.input},
                }
                tool_tasks.append(_call_tool(tu.name, tu.input or {}, user_id))

            tools_started = time.monotonic()
            results = await asyncio.gather(*tool_tasks)
            tools_elapsed_ms = int((time.monotonic() - tools_started) * 1000)

            # Emit results + build tool_result message for next turn
            tool_results_payload = []
            for tu, (ok, result) in zip(tool_uses, results, strict=True):
                # 067 — remember the RAW result (before `_compact_for_llm` strips
                # screenshots) so the validator can trace widget numbers back to
                # the tool_use id that produced them. Only successful calls count
                # as a source of truth.
                if ok:
                    tool_facts.append({"id": tu.id, "name": tu.name, "result": result})
                # Record one tool span per call.
                tracer.record_tool(
                    name=tu.name,
                    args=tu.input or {},
                    result=result,
                    ok=ok,
                    latency_ms=tools_elapsed_ms,  # batch total; per-call timing isn't tracked yet
                    metadata={"tool_use_id": tu.id},
                )
                yield {
                    "event": "tool_result",
                    "data": {
                        "id": tu.id,
                        "ok": ok,
                        "summary": _summarize_tool_result(tu.name, ok, result),
                    },
                }

                # Proposal 024: compact heavy fields out of the LLM-bound payload
                # (today: large `screenshot_url` data URLs) and remember the
                # original for restoration on widget emission. The yielded
                # `tool_result` event above only carries a short summary so the
                # frontend SSE consumer is unaffected.
                if (
                    isinstance(result, dict)
                    and isinstance(result.get("screenshot_url"), str)
                    and result["screenshot_url"].startswith("data:")
                    and len(result["screenshot_url"]) > _SCREENSHOT_STRIP_THRESHOLD
                ):
                    screenshot_urls_by_tool[tu.id] = result["screenshot_url"]
                compact_result = _compact_for_llm(result)

                tool_results_payload.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": json.dumps(compact_result),
                    "is_error": not ok,
                })

            messages.append({"role": "user", "content": tool_results_payload})

        else:
            # Hit max iterations without a terminal response
            tracer.set_output({"kind": "error", "message": f"max iterations ({MAX_ITERATIONS})"})
            yield {
                "event": "error",
                "data": {"message": f"agent stopped after {MAX_ITERATIONS} iterations"},
            }

    except ProviderFailover:
        raise  # 069 — let run_chat restart the turn on DeepSeek
    except Exception as e:
        # 069 — usage-limit failure + fallback armed → restart this turn on
        # DeepSeek (run_chat catches). Only for eligible reasons and non-image
        # turns; otherwise fall through to the safe classified error below.
        reason = _should_failover(e, attachments)
        if reason is not None:
            log.warning("run_agent failing over to DeepSeek [%s]: %s", reason, e)
            raise ProviderFailover(reason) from e
        # 064: log the FULL error server-side (+ tracer) for debugging, but send
        # the user only a safe, classified message — never the raw provider text
        # (e.g. "credit balance is too low").
        user_msg, code = _classify_agent_error(e)
        log.warning("run_agent failed [%s]: %s", code, e)
        tracer.set_output({"kind": "error", "code": code, "message": str(e)})
        yield {"event": "error", "data": {"message": user_msg, "code": code}}

    elapsed_ms = int((time.monotonic() - start_time) * 1000)
    yield {
        "event": "done",
        "data": {
            "elapsed_ms": elapsed_ms,
            "iterations": iterations,
            # P5 (034): turn token totals — consumed by the daily token budget.
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
        },
    }


# ---------------------------------------------------------------------------
# 069/071 — OpenAI-format agent loop. A structurally-parallel loop to the
# Anthropic one that talks OpenAI wire format via a PROVIDER MODULE and builds
# NEUTRAL history as it goes. It reuses every shared leaf (_call_tool,
# _compact_for_llm, _finalize_terminal_widget, tool_facts/screenshot bookkeeping,
# tracer), so the trust check and tool handling are identical to the Anthropic rail.
#
# 071 parameterises the provider (`client`) instead of hardcoding DeepSeek, so
# OpenAI reuses this loop verbatim rather than adding a THIRD copy — deliberate,
# because a third loop is a third place the 067 trust check could drift. Any module
# exposing `complete/to_openai_tools/model/supports_vision` can drive it.
#
# Reached either as the selected primary (`LLM_RAIL=openai`) or via run_chat after
# a ProviderFailover, in which case the turn is restarted from scratch (tools
# re-run — safe, all reads while TRADING_ENABLED=0).
#
# Images: only passed through when the provider `supports_vision()`. DeepSeek
# never does, and run_chat guarantees its turns are attachment-free.
# ---------------------------------------------------------------------------


async def run_agent_openai_compat(
    user_message: str,
    user_id: str = "demo",
    tracer: Tracer = NOOP_TRACER,
    memory_context: str = "",
    history: list[dict[str, Any]] | None = None,
    attachments: list[dict[str, Any]] | None = None,
    client: Any = deepseek_client,
) -> AsyncGenerator[dict[str, Any], None]:
    start_time = time.monotonic()
    system_prompt = (
        f"{SYSTEM_PROMPT}{memory_context}"
        if isinstance(memory_context, str) and memory_context
        else SYSTEM_PROMPT
    )
    # Neutral history (db.to_agent_history → text-only dicts) + this turn's text.
    messages: list[dict[str, Any]] = list(history or [])
    user_turn: dict[str, Any] = {"role": "user", "content": user_message}
    # 071 — attach images only on a vision-capable rail. Silently dropping an
    # attached chart would be a correctness failure (069's rule); run_chat refuses
    # the turn upstream instead, so reaching here with attachments on a blind rail
    # is a bug, not a user-visible path.
    if attachments and client.supports_vision():
        user_turn["attachments"] = attachments
    messages.append(user_turn)
    tools = client.to_openai_tools(anthropic_tool_specs())

    iterations = 0
    total_input_tokens = 0
    total_output_tokens = 0
    screenshot_urls_by_tool: dict[str, str] = {}
    tool_facts: list[dict[str, Any]] = []

    try:
        while iterations < MAX_ITERATIONS:
            iterations += 1
            iter_started = time.monotonic()
            resp = await client.complete(
                system_prompt, messages, tools=tools,
                max_tokens=llm_limits.max_output_tokens(client.provider_name()),  # 073
            )
            text = resp.get("text") or ""
            tool_calls = resp.get("tool_calls") or []
            usage = resp.get("usage") or {}
            # 073 — THIS iteration's tokens. Previously the cumulative running
            # totals were handed to `record_generation`, so a multi-iteration turn
            # over-counted in Langfuse (iter2 logged t1+t2, iter3 logged t1+t2+t3).
            # The Anthropic rail passes per-iteration deltas; both now match.
            iter_input_tokens = int(usage.get("input_tokens") or 0)
            iter_output_tokens = int(usage.get("output_tokens") or 0)
            total_input_tokens += iter_input_tokens
            total_output_tokens += iter_output_tokens
            tracer.record_generation(
                name=f"{client.model()}.iter_{iterations}",
                model=client.model(),
                input=list(messages),
                output=[{"type": "text", "text": text}] + [
                    {"type": "tool_use", "id": t["id"], "name": t["name"], "input": t["input"]}
                    for t in tool_calls
                ],
                usage_details={"input": iter_input_tokens, "output": iter_output_tokens},
                metadata={"iteration": iterations, "latency_ms": int((time.monotonic() - iter_started) * 1000)},
            )

            # Terminal turn — emit via the SHARED finalizer (identical trust check).
            if not tool_calls:
                messages.append({"role": "assistant", "content": text})
                async for ev in _finalize_terminal_widget(
                    text.strip(), tool_facts, screenshot_urls_by_tool, tracer
                ):
                    yield ev
                break

            # Record the assistant turn (with its tool_calls) BEFORE the tool msgs.
            messages.append({"role": "assistant", "content": text or None, "tool_calls": tool_calls})

            tool_tasks = []
            for tc in tool_calls:
                yield {"event": "thought", "data": {"text": render_thought(tc["name"], tc["input"] or {})}}
                yield {"event": "tool_call", "data": {"id": tc["id"], "name": tc["name"], "args": tc["input"]}}
                tool_tasks.append(_call_tool(tc["name"], tc["input"] or {}, user_id))

            tools_started = time.monotonic()
            results = await asyncio.gather(*tool_tasks)
            tools_elapsed_ms = int((time.monotonic() - tools_started) * 1000)

            for tc, (ok, result) in zip(tool_calls, results, strict=True):
                if ok:
                    tool_facts.append({"id": tc["id"], "name": tc["name"], "result": result})
                tracer.record_tool(
                    name=tc["name"], args=tc["input"] or {}, result=result, ok=ok,
                    latency_ms=tools_elapsed_ms, metadata={"tool_use_id": tc["id"]},
                )
                yield {
                    "event": "tool_result",
                    "data": {"id": tc["id"], "ok": ok, "summary": _summarize_tool_result(tc["name"], ok, result)},
                }
                # Proposal 024 screenshot bookkeeping (same as the Anthropic rail).
                if (
                    isinstance(result, dict)
                    and isinstance(result.get("screenshot_url"), str)
                    and result["screenshot_url"].startswith("data:")
                    and len(result["screenshot_url"]) > _SCREENSHOT_STRIP_THRESHOLD
                ):
                    screenshot_urls_by_tool[tc["id"]] = result["screenshot_url"]
                compact = _compact_for_llm(result)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(compact),
                })
        else:
            # 073 — mirror the Anthropic rail's `set_output` on this path; without
            # it a max-iteration stall was invisible in Langfuse on this rail only.
            tracer.set_output({"kind": "error", "message": f"max iterations ({MAX_ITERATIONS})"})
            yield {"event": "error", "data": {"message": f"agent stopped after {MAX_ITERATIONS} iterations"}}

    except Exception as e:
        # 071 — when THIS rail is the selected primary (OpenAI), a usage-limit
        # failure should still drop to DeepSeek, exactly as the Anthropic rail
        # does. When this loop IS DeepSeek it is the last resort and must not
        # fail over to itself — guarded by the `client is not deepseek_client`
        # check, which also prevents an infinite restart loop.
        if client is not deepseek_client:
            reason = _should_failover(e, attachments)
            if reason is not None:
                log.warning("run_agent_openai_compat failing over to DeepSeek [%s]: %s", reason, e)
                raise ProviderFailover(reason) from e
        user_msg, code = _classify_agent_error(e)
        log.warning("run_agent_openai_compat[%s] failed [%s]: %s", client.model(), code, e)
        tracer.set_output({"kind": "error", "code": code, "message": str(e)})
        yield {"event": "error", "data": {"message": user_msg, "code": code}}

    elapsed_ms = int((time.monotonic() - start_time) * 1000)
    yield {
        "event": "done",
        "data": {
            "elapsed_ms": elapsed_ms,
            "iterations": iterations,
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
        },
    }


# 069 back-compat: the DeepSeek fallback is just this loop bound to that client.
# Kept so existing callers/tests (`test_069_phase2_failover.py`) keep working.
async def run_agent_deepseek(
    user_message: str,
    user_id: str = "demo",
    tracer: Tracer = NOOP_TRACER,
    memory_context: str = "",
    history: list[dict[str, Any]] | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    async for ev in run_agent_openai_compat(
        user_message, user_id, tracer=tracer, memory_context=memory_context,
        history=history, attachments=None, client=deepseek_client,
    ):
        yield ev


# ---------------------------------------------------------------------------
# 069/071 — run_chat: the entry point main.py calls. Emits a `provider` event so
# the UI shows which model answered, runs the SELECTED primary rail (`LLM_RAIL`),
# and on a ProviderFailover restarts the turn on DeepSeek (announcing the switch).
# ---------------------------------------------------------------------------

_MODEL_LABELS = {
    "claude-opus-4-5": "Claude Opus 4.5",
    "deepseek-chat": "DeepSeek V3",
    # 071 — OpenAI ids are configurable; label the ones we expect and fall through
    # to the raw id for anything else (better an honest id than a wrong name).
    "gpt-5": "GPT-5",
    "gpt-5-mini": "GPT-5 mini",
    "gpt-4.1": "GPT-4.1",
    "gpt-4o": "GPT-4o",
}


def _provider_label(model: str) -> str:
    return _MODEL_LABELS.get(model, model)


async def run_chat(
    user_message: str,
    user_id: str = "demo",
    tracer: Tracer = NOOP_TRACER,
    memory_context: str = "",
    history: list[Any] | None = None,
    attachments: list[dict[str, Any]] | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Provider-aware chat turn. Yields the same events as run_agent PLUS a
    `provider` event (and a second one if it fails over to DeepSeek).

    071: the primary is chosen by `LLM_RAIL` rather than hardcoded to Anthropic.
    """
    rail = _rail()
    if rail == "openai":
        primary_model = openai_client.model()
        # A vision-incapable pinned model must REFUSE an image turn, never drop
        # the image silently (069's non-negotiable rule, applied per-rail).
        if attachments and not openai_client.can_fall_back(attachments):
            yield {
                "event": "provider",
                "data": {"provider": "openai", "model": primary_model,
                         "label": _provider_label(primary_model),
                         "fallback": False, "reason": None},
            }
            yield {
                "event": "error",
                "data": {
                    "message": (
                        "Image analysis isn't available on the current model. "
                        "Remove the attachment and try again."
                    ),
                    "code": "vision_unavailable",
                },
            }
            return
    else:
        primary_model = MODEL

    yield {
        "event": "provider",
        "data": {"provider": rail, "model": primary_model,
                 "label": _provider_label(primary_model), "fallback": False, "reason": None},
    }
    try:
        if rail == "openai":
            async for ev in run_agent_openai_compat(
                user_message, user_id, tracer=tracer, memory_context=memory_context,
                history=history, attachments=attachments, client=openai_client,
            ):
                yield ev
        else:
            async for ev in run_agent(
                user_message, user_id, tracer=tracer, memory_context=memory_context,
                history=history, attachments=attachments,
            ):
                yield ev
    except ProviderFailover as pf:
        ds_model = deepseek_client.deepseek_model()
        yield {
            "event": "provider",
            "data": {"provider": "deepseek", "model": ds_model,
                     "label": _provider_label(ds_model), "fallback": True,
                     "reason": f"{rail}_{pf.reason}"},
        }
        # Turn-restart: DeepSeek gets the text history + user message only (never
        # images — the failover guard guarantees this turn is attachment-free).
        async for ev in run_agent_openai_compat(
            user_message, user_id, tracer=tracer, memory_context=memory_context,
            history=history, attachments=None, client=deepseek_client,
        ):
            yield ev
