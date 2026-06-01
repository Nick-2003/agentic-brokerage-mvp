"""Langfuse observability (P4.4 — proposal 017, fix in 018).

Wraps each ``/api/chat`` turn in a Langfuse trace so the agent loop is debuggable
turn-by-turn: which tools fired, the prompts/outputs of each Anthropic call,
token usage, latencies, errors. Per the PRIORITIES_EXPLAINED ranking this is
the *highest-leverage* P4 item — it unblocks per-step debugging, the latency
work (the 12–19s turn), and the P7-LLM eval gate.

Design:

* **Read-only side effect.** Observability NEVER alters the agent's behaviour,
  inputs, or outputs. Every Langfuse call is wrapped in try/except; any failure
  is logged at debug level and the user-facing stream is never disturbed.
* **Graceful no-op when unconfigured.** When ``LANGFUSE_PUBLIC_KEY`` /
  ``LANGFUSE_SECRET_KEY`` aren't set, ``trace_chat`` yields a ``Tracer`` that
  no-ops on every call. Same shape as ``analytics.ts`` and the existing
  ``USE_MOCK_*`` pattern.
* **Soft dependency on auth (P4.1).** ``user_id`` comes from the authenticated
  ``AuthCtx`` when present; an anonymous fallback (``"anonymous"``) keeps demo
  turns traced too. Per PRIORITIES_EXPLAINED §P4.4, Langfuse "consumes the
  user_id but doesn't require it — traces still work anonymously."
* **OTel-based.** Built on Langfuse v4's OpenTelemetry surface
  (``start_as_current_observation`` / ``start_observation`` for spans, and the
  **module-level** ``langfuse.propagate_attributes`` for trace-level ``user.id``
  / ``session.id``). The client is constructed lazily, once.
* **Lazy import.** ``langfuse`` is imported only inside ``_get_client`` /
  ``trace_chat``, so a backend without it installed still boots (the no-op
  path kicks in).

Surface (used by ``main.py`` + ``agent.py``):

    async with trace_chat(user_id=..., conversation_id=..., message=...) as tracer:
        async for ev in run_agent(message, user_id, tracer=tracer):
            ...
        tracer.set_output({"widgets": [...], "messages": [...]})

    # Inside run_agent:
    tracer.record_generation(name=..., model=..., input=..., output=..., usage=...)
    tracer.record_tool(name=..., args=..., result=..., ok=..., latency_ms=...)
"""

from __future__ import annotations

import contextlib
import logging
import os
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:  # pragma: no cover
    from langfuse import Langfuse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _truthy(env_value: str) -> bool:
    return bool(env_value) and not env_value.endswith("REPLACE")


def langfuse_configured() -> bool:
    """True when both Langfuse keys are real (non-placeholder).

    Surfaced in ``/healthz`` so a deploy that expects tracing but has no keys
    is visible. Used inside ``_get_client`` as the gate to actually init.
    """
    return _truthy(os.getenv("LANGFUSE_PUBLIC_KEY", "")) and _truthy(
        os.getenv("LANGFUSE_SECRET_KEY", "")
    )


# ---------------------------------------------------------------------------
# Lazy singleton client
# ---------------------------------------------------------------------------


_client: Langfuse | None = None
_client_unavailable = False  # sticky: don't keep retrying after a hard import/init failure


def _get_client() -> Langfuse | None:
    """Return the cached Langfuse client, or None if unconfigured / unavailable.

    Import is lazy — a backend without ``langfuse`` installed still works (the
    function returns None and ``trace_chat`` falls back to the no-op tracer).
    """
    global _client, _client_unavailable
    if _client_unavailable:
        return None
    if _client is not None:
        return _client
    if not langfuse_configured():
        return None
    try:
        from langfuse import Langfuse  # type: ignore[import-not-found]

        _client = Langfuse(
            public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
            secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
            host=os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
        )
        return _client
    except Exception:  # noqa: BLE001
        logger.warning("langfuse init failed; tracing disabled this process", exc_info=True)
        _client_unavailable = True
        return None


# ---------------------------------------------------------------------------
# Tracer protocol + implementations
# ---------------------------------------------------------------------------


class Tracer(Protocol):
    """Turn-scoped tracer interface.

    Two concrete implementations: ``_LangfuseTracer`` (real) and ``_NoopTracer``
    (silent fallback). The agent calls the same methods either way; the choice
    happens once at request entry inside ``trace_chat``.
    """

    def record_generation(
        self,
        *,
        name: str,
        model: str,
        input: Any,
        output: Any,
        usage_details: dict[str, int] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None: ...

    def record_tool(
        self,
        *,
        name: str,
        args: Any,
        result: Any,
        ok: bool,
        latency_ms: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None: ...

    def set_output(self, output: Any) -> None: ...


class _NoopTracer:
    """Silent fallback used in demo mode / when Langfuse isn't configured."""

    def record_generation(self, **_: Any) -> None:
        return None

    def record_tool(self, **_: Any) -> None:
        return None

    def set_output(self, output: Any) -> None:
        return None


NOOP_TRACER: Tracer = _NoopTracer()


class _LangfuseTracer:
    """Langfuse-backed tracer holding the root span for this turn.

    Every method is failure-tolerant — any Langfuse hiccup is logged at debug
    level and swallowed so the user-facing stream is never broken.
    """

    def __init__(self, client: Langfuse, root_span: Any) -> None:
        self._client = client
        self._root = root_span
        self._pending_output: Any = None

    @property
    def pending_output(self) -> Any:
        return self._pending_output

    def record_generation(
        self,
        *,
        name: str,
        model: str,
        input: Any,
        output: Any,
        usage_details: dict[str, int] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        try:
            obs = self._client.start_observation(
                name=name,
                as_type="generation",
                input=input,
                output=output,
                model=model,
                usage_details=usage_details,
                metadata=metadata,
            )
            obs.end()
        except Exception:  # noqa: BLE001
            logger.debug("langfuse record_generation failed", exc_info=True)

    def record_tool(
        self,
        *,
        name: str,
        args: Any,
        result: Any,
        ok: bool,
        latency_ms: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        try:
            meta = dict(metadata or {})
            if latency_ms is not None:
                meta["latency_ms"] = latency_ms
            obs = self._client.start_observation(
                name=f"tool:{name}",
                as_type="tool",
                input=args,
                output=result,
                level=("DEFAULT" if ok else "WARNING"),
                status_message=(None if ok else "tool_failed"),
                metadata=meta or None,
            )
            obs.end()
        except Exception:  # noqa: BLE001
            logger.debug("langfuse record_tool failed", exc_info=True)

    def set_output(self, output: Any) -> None:
        """Stash the terminal output; ``trace_chat`` applies it on exit."""
        self._pending_output = output


# ---------------------------------------------------------------------------
# trace_chat context manager — the only entry point main.py calls.
# ---------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def trace_chat(
    *,
    user_id: str | None,
    conversation_id: str | None,
    message: str,
) -> AsyncIterator[Tracer]:
    """Open a Langfuse trace for one ``/api/chat`` turn; yield a ``Tracer``.

    Behaviour matrix:

    | langfuse_configured | yields                | trace in Langfuse |
    |---------------------|-----------------------|-------------------|
    | False               | NOOP_TRACER           | none              |
    | True, init OK       | _LangfuseTracer       | one root span "chat" with `record_generation` / `record_tool` children, tagged user_id + session_id |
    | True, init fails    | NOOP_TRACER           | none (logged)     |

    On exit, applies the tracer's accumulated output to the root span (when
    real) and flushes the Langfuse client so the dashboard sees this turn
    promptly. Any exception in setup or teardown is swallowed.
    """
    client = _get_client()
    if client is None:
        yield NOOP_TRACER
        return

    # ── setup ──
    obs_ctx = None
    attrs_ctx = None
    root_span: Any = None
    started_at = time.monotonic()
    try:
        # `propagate_attributes` is a **module-level** helper (NOT an instance
        # method on the client) — calling it via `client.propagate_attributes`
        # raises AttributeError. Fix landed in proposal 018.
        import langfuse  # type: ignore[import-not-found]

        obs_ctx = client.start_as_current_observation(
            name="chat",
            as_type="span",
            input={"message": message},
        )
        root_span = obs_ctx.__enter__()
        # Trace-level attributes — user.id + session.id show up as filterable
        # dimensions in the Langfuse dashboard (per-user latency/cost, etc.).
        attrs_ctx = langfuse.propagate_attributes(
            user_id=(user_id or "anonymous"),
            session_id=conversation_id,
            tags=(["demo"] if (not user_id or user_id == "demo") else None),
        )
        attrs_ctx.__enter__()
    except Exception:  # noqa: BLE001
        logger.warning("langfuse trace setup failed; turn untraced", exc_info=True)
        # Clean up anything we partially opened.
        if attrs_ctx is not None:
            try:
                attrs_ctx.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
        if obs_ctx is not None:
            try:
                obs_ctx.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
        yield NOOP_TRACER
        return

    # ── body ──
    tracer = _LangfuseTracer(client, root_span)
    try:
        yield tracer
    finally:
        # ── teardown ──
        # Attach the accumulated output (final widget / message) and elapsed time.
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        try:
            if tracer.pending_output is not None:
                root_span.update(
                    output=tracer.pending_output,
                    metadata={"elapsed_ms": elapsed_ms},
                )
            else:
                root_span.update(metadata={"elapsed_ms": elapsed_ms})
        except Exception:  # noqa: BLE001
            logger.debug("langfuse root update failed", exc_info=True)

        for ctx in (attrs_ctx, obs_ctx):
            try:
                ctx.__exit__(None, None, None)  # type: ignore[union-attr]
            except Exception:  # noqa: BLE001
                pass
        try:
            client.flush()
        except Exception:  # noqa: BLE001
            pass
