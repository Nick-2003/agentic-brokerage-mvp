"""Mem0 per-user memory (P4.3 — proposal 025).

Gives the Analyst a memory of *the user* (their risk tolerance, watchlist, that
they hold NVDA) that persists across brand-new conversations — the qualitative
jump from "stateless Q&A box" to "agentic". Distinct from P4.2 persistence,
which remembers *the conversation*; this remembers *the user*.

Two hooks bracket the LLM call (the Node repo's "MEMORY BLOCK 1/2" pattern):

    recall   — BEFORE the agent loop: search this user's memories for the turn's
               query and return a short system-prompt block to inject.
    remember — AFTER the turn: store the salient new facts from the exchange.

────────────────────────────────────────────────────────────────────────────
PRIVACY IS THE WHOLE POINT (PRIORITIES_EXPLAINED §"Mem0 ↔ auth"):

Every ``search`` and every ``add`` is scoped by the **authenticated** ``user_id``
— the trusted Supabase UUID from P4.1 (012/015), threaded through ``AuthCtx``.
NEVER a client-supplied value. If memory ran under a shared/spoofable scope,
User B's brand-new chat could surface User A's holdings injected straight into
the system prompt — an *invisible* cross-user leak (it leaks via the model's
context, not an API response RLS could catch). So the scope key is load-bearing
in exactly the way RLS is for P4.2. Build it as carefully.

This module never invents or rewrites the scope: ``recall``/``remember`` pass the
caller's ``user_id`` to Mem0 verbatim, and refuse to run under an empty scope.
With Langfuse (017/018) live, a wrong-scope bug would surface immediately as the
wrong ``user.id`` on the trace — caller passes the SAME ``auth.user_id`` to the
tracer and to memory.
────────────────────────────────────────────────────────────────────────────

Design (mirrors ``observability.py`` — the 017 soft-dependency template):

* **Graceful no-op when unconfigured.** No real ``MEM0_API_KEY`` →
  ``get_memory()`` returns ``NOOP_MEMORY`` (``recall`` → ``""``, ``remember`` →
  no-op). Same shape as the ``USE_MOCK_*`` / Langfuse no-op pattern. Zero cost
  on the deterministic mock demo and local/curl checks.
* **Failure-tolerant.** Memory shapes the prompt and stores facts, but a Mem0
  outage must NEVER break the chat stream. Every Mem0 call is wrapped in
  try/except; a failed ``recall`` injects nothing, a failed ``remember`` drops
  the write. The user's turn always completes.
* **Lazy import.** ``mem0`` is imported only inside ``_Mem0Memory`` (it lives in
  the optional ``memory`` dependency group), so a backend without it installed
  still boots — the no-op path kicks in.
* **Bounded prompt growth.** Injected memories grow the system prompt → token
  cost (PRIORITIES_EXPLAINED P4.3 gotcha). ``recall`` caps both the number of
  facts (``MEM0_SEARCH_LIMIT``, default 5) and the total characters
  (``MEM0_MAX_CHARS``, default 1500).

Demo mode: memory is gated only by ``memory_configured()`` (NOT by a Supabase
token like persistence). When configured, demo turns store/recall under the
literal ``"demo"`` scope — a single shared local bucket, harmless because
production runs ``REQUIRE_AUTH=1`` so every real user is a distinct UUID. Local
devs all sharing one "demo" memory bucket is expected.

Surface (used by ``main.py``):

    mem = get_memory()
    block = await mem.recall(user_id=auth.user_id, query=req.message)   # "" if none
    # ... run the agent with the block appended to the system prompt ...
    await mem.remember(user_id=auth.user_id,
                       user_message=req.message,
                       assistant_text=final_markdown_or_None)
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:  # pragma: no cover
    from mem0 import AsyncMemoryClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _truthy(env_value: str) -> bool:
    return bool(env_value) and not env_value.endswith("REPLACE")


def memory_configured() -> bool:
    """True when a real (non-placeholder) ``MEM0_API_KEY`` is set.

    Surfaced in ``/healthz`` so a deploy that expects memory but has no key is
    visible. Used inside ``get_memory`` as the gate to actually init.
    """
    return _truthy(os.getenv("MEM0_API_KEY", ""))


def _search_limit() -> int:
    try:
        return max(1, int(os.getenv("MEM0_SEARCH_LIMIT", "5")))
    except ValueError:
        return 5


def _max_chars() -> int:
    try:
        return max(200, int(os.getenv("MEM0_MAX_CHARS", "1500")))
    except ValueError:
        return 1500


# ---------------------------------------------------------------------------
# MemoryStore protocol + implementations
# ---------------------------------------------------------------------------


class MemoryStore(Protocol):
    """Per-user memory interface.

    Two concrete implementations: ``_Mem0Memory`` (real) and ``_NoopMemory``
    (silent fallback). The caller uses the same methods either way; the choice
    happens once in ``get_memory()``.
    """

    async def recall(self, *, user_id: str, query: str) -> str: ...

    async def remember(
        self, *, user_id: str, user_message: str, assistant_text: str | None = None
    ) -> None: ...


class _NoopMemory:
    """Silent fallback — demo mode / when Mem0 isn't configured or installed."""

    async def recall(self, *, user_id: str, query: str) -> str:
        return ""

    async def remember(
        self, *, user_id: str, user_message: str, assistant_text: str | None = None
    ) -> None:
        return None


NOOP_MEMORY: MemoryStore = _NoopMemory()


# Block header injected into the system prompt. The wording is deliberate: it
# tells the model these are SOFT facts about the user, not a numeric source —
# trust principle #1 (no number without a tool) still wins. system.md carries
# the matching rule so the two can't drift.
_BLOCK_HEADER = (
    "\n\n---\n\n## What you remember about this user\n\n"
    "These facts were extracted from this user's PRIOR conversations. Treat them "
    "as soft context about who they are, what they hold, and how they trade — "
    "NOT as a data source. Trust principles still apply: never put a remembered "
    "number (price, cost basis, P&L, target) into a widget without re-fetching it "
    "from a tool THIS turn. If a remembered fact conflicts with a fresh tool "
    "result, the tool wins.\n\n"
)


def _normalise_search(res: Any) -> list[str]:
    """Pull memory strings out of whatever shape Mem0's search returned.

    Defensive against version drift (same discipline as ``fmp_client._pick``):
    the hosted client returns either a bare ``list[dict]`` or
    ``{"results": [dict, ...]}``; each item carries the text under ``memory``
    (current) / ``text`` / ``content`` (older). Bare strings are accepted too.
    Anything unrecognised is skipped, never guessed.
    """
    items = res.get("results", []) if isinstance(res, dict) else res
    out: list[str] = []
    for it in items or []:
        if isinstance(it, str):
            t = it.strip()
        elif isinstance(it, dict):
            raw = it.get("memory") or it.get("text") or it.get("content") or ""
            t = str(raw).strip()
        else:
            t = ""
        if t:
            out.append(t)
    return out


def _build_block(facts: list[str], max_chars: int) -> str:
    """Render facts into a bounded bulleted block. Returns "" for no facts."""
    if not facts:
        return ""
    lines: list[str] = []
    used = 0
    for f in facts:
        line = f"- {f}"
        if used + len(line) + 1 > max_chars:
            break
        lines.append(line)
        used += len(line) + 1
    if not lines:
        return ""
    return _BLOCK_HEADER + "\n".join(lines) + "\n"


class _Mem0Memory:
    """Mem0-backed memory. Holds a lazily-built ``AsyncMemoryClient``.

    Every method is failure-tolerant — a Mem0 hiccup is logged at debug level
    and swallowed so the user-facing stream is never broken. Recall degrades to
    ``""`` (inject nothing); remember degrades to a dropped write.
    """

    def __init__(self, client: AsyncMemoryClient) -> None:
        self._client = client

    async def recall(self, *, user_id: str, query: str) -> str:
        # Refuse to search under an empty scope — that's the cross-user-leak
        # class of bug. No scope → no recall.
        if not user_id or not query:
            return ""
        try:
            # mem0 v3 client (2.x): the scope key MUST go inside `filters` —
            # passing `user_id=` as a top-level kwarg raises ValueError (it's an
            # "entity param", banned in search()). The result limit is `top_k`,
            # not `limit`. Verified against the installed mem0 2.0.4 source
            # (`client/main.py::AsyncMemoryClient.search`, which checks
            # `ENTITY_PARAMS & kwargs` and tells you to use filters). Getting
            # this wrong fails CLOSED — the except below swallows the ValueError
            # and recall silently returns nothing, so the feature looks alive
            # but never recalls. That's why it's pinned + tested explicitly.
            res = await self._client.search(
                query, filters={"user_id": user_id}, top_k=_search_limit()
            )
        except Exception:  # noqa: BLE001 — memory is best-effort
            logger.debug("mem0 search failed; injecting no memories", exc_info=True)
            return ""
        facts = _normalise_search(res)
        return _build_block(facts, _max_chars())

    async def remember(
        self, *, user_id: str, user_message: str, assistant_text: str | None = None
    ) -> None:
        # Same scope guard as recall — never store under an empty scope.
        if not user_id or not user_message:
            return None
        messages = [{"role": "user", "content": user_message}]
        if assistant_text:
            messages.append({"role": "assistant", "content": assistant_text})
        try:
            # Mem0 does its own LLM-based salient-fact extraction server-side;
            # we hand it the raw exchange and let it decide what's worth keeping.
            # NB (asymmetry with search above): `add()` in mem0 2.x DOES accept
            # `user_id=` as a top-level kwarg — it has no ENTITY_PARAMS guard
            # (verified against the installed source + docstring). Do NOT
            # "fix" this to `filters={"user_id": ...}` to match recall.
            await self._client.add(messages, user_id=user_id)
        except Exception:  # noqa: BLE001 — memory is best-effort
            logger.debug("mem0 add failed; memory not stored this turn", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Lazy singleton accessor — the only entry point main.py calls.
# ---------------------------------------------------------------------------


_memory: MemoryStore | None = None
_unavailable = False  # sticky: don't keep retrying after a hard import/init failure


def get_memory() -> MemoryStore:
    """Return the cached ``MemoryStore`` — real when configured, else ``NOOP_MEMORY``.

    | memory_configured | mem0 import/init | returns        |
    |-------------------|------------------|----------------|
    | False             | —                | NOOP_MEMORY    |
    | True              | OK               | _Mem0Memory    |
    | True              | fails            | NOOP_MEMORY (logged, sticky) |

    Construction is lazy and cached; the ``mem0`` import lives inside this
    function so a backend without the optional ``memory`` dependency group
    installed still boots.
    """
    global _memory, _unavailable
    if _unavailable:
        return NOOP_MEMORY
    if _memory is not None:
        return _memory
    if not memory_configured():
        return NOOP_MEMORY
    try:
        from mem0 import AsyncMemoryClient  # type: ignore[import-not-found]

        client = AsyncMemoryClient(api_key=os.getenv("MEM0_API_KEY"))
        _memory = _Mem0Memory(client)
        return _memory
    except Exception:  # noqa: BLE001
        logger.warning("mem0 init failed; memory disabled this process", exc_info=True)
        _unavailable = True
        return NOOP_MEMORY
