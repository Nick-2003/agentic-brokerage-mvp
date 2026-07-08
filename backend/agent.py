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

from observability import NOOP_TRACER, Tracer
from tools import TOOL_REGISTRY, anthropic_tool_specs, render_thought  # noqa: F401

log = logging.getLogger(__name__)

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

    Today only ``screenshot_url`` is large enough to need stripping; we
    replace it with an empty string ("" — the canonical "no real screenshot,
    frontend renders MockChartSvg" sentinel from proposals 019/023). The full
    data URL is held in ``screenshot_urls_by_tool`` inside ``run_agent`` and
    re-attached when the terminal widget is emitted.

    Idempotent: returns the input unchanged when nothing needs stripping.
    """
    if not isinstance(result, dict):
        return result
    su = result.get("screenshot_url")
    if isinstance(su, str) and su.startswith("data:") and len(su) > _SCREENSHOT_STRIP_THRESHOLD:
        return {**result, "screenshot_url": ""}
    return result


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
                max_tokens=4096,
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

            # Terminal turn — no more tool calls. Emit widget or text.
            if not tool_uses:
                full_text = "".join(text_parts).strip()
                widget = _extract_widget_json(full_text)
                if widget is not None:
                    # Proposal 024: substitute the real screenshot data URL back
                    # in (the LLM emitted the empty sentinel because we stripped
                    # the real URL from its context to avoid the 200K cap).
                    _restore_screenshot_in_widget(widget, screenshot_urls_by_tool)
                    tracer.set_output({"kind": "widget", "widget": widget})
                    yield {"event": "widget", "data": widget}
                elif full_text:
                    tracer.set_output({"kind": "message", "text": full_text})
                    yield {"event": "message", "data": {"text": full_text}}
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

    except Exception as e:
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
