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
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from anthropic import AsyncAnthropic
from anthropic.types import MessageParam, ToolUseBlock

from observability import NOOP_TRACER, Tracer
from tools import TOOL_REGISTRY, anthropic_tool_specs, render_thought  # noqa: F401

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


def _extract_widget_json(text: str) -> dict[str, Any] | None:
    """Try to parse a widget JSON from the model's final message.

    Returns None if no widget found (fall through to plain markdown message).
    """
    if not text:
        return None
    # Prefer a fenced block, fall back to a single bare JSON object.
    m = _JSON_BLOCK_RE.search(text) or _RAW_JSON_RE.match(text.strip())
    if not m:
        return None
    try:
        obj = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or "type" not in obj or "data" not in obj:
        return None
    return obj


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
# Main agent loop
# ---------------------------------------------------------------------------


async def run_agent(
    user_message: str,
    user_id: str = "demo",
    tracer: Tracer = NOOP_TRACER,
) -> AsyncGenerator[dict[str, Any], None]:
    """Run one user turn through the Claude tool-use loop.

    Yields SSE event dicts. ``tracer`` (P4.4) receives generations + tool spans;
    pass ``NOOP_TRACER`` (the default) when observability isn't wanted.
    """
    start_time = time.monotonic()
    client = _get_client()
    messages: list[MessageParam] = [{"role": "user", "content": user_message}]
    iterations = 0
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
                system=SYSTEM_PROMPT,
                tools=anthropic_tool_specs(),
                messages=messages,
            ) as stream:
                final_msg = await stream.get_final_message()

            # Record the generation for this iteration (no-op when tracer is NOOP).
            usage = getattr(final_msg, "usage", None)
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
        tracer.set_output({"kind": "error", "message": str(e)})
        yield {"event": "error", "data": {"message": f"agent error: {e}"}}

    elapsed_ms = int((time.monotonic() - start_time) * 1000)
    yield {
        "event": "done",
        "data": {"elapsed_ms": elapsed_ms, "iterations": iterations},
    }
