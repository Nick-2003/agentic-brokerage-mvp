"""Supabase persistence (P4.2 — proposal 016).

User-scoped Postgres access via the Supabase async client. Every query runs
under the *user's* JWT — set with ``client.postgrest.auth(token)`` — so the
RLS policy (``auth.uid() = user_id``) on every user-data table physically
prevents User A from reading User B's rows. The Supabase **service key is
NEVER used here**: it bypasses RLS and is reserved for admin tasks (none yet).

Tables (DDL: ``backend/db/schema.sql`` — run once in the Supabase SQL Editor):

    conversations    one row per chat session (id, user_id, title, …)
    messages         one row per turn (conversation_id, user_id, role, content, widgets)
    pinned_widgets   (RLS on; frontend wiring deferred to a follow-up)
    user_profiles    (RLS on; not used by code yet)

Demo mode (``REQUIRE_AUTH=0`` + no token → ``AuthCtx.token is None``) → main.py
skips persistence entirely; the helpers below are never called.

The Supabase client is imported at module top-level, so ``supabase`` is now a
*main* backend dependency (pinned ``<2.28.0`` to dodge pyiceberg — see pyproject).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from supabase import AsyncClient, acreate_client


# ---------------------------------------------------------------------------
# Config — readers used by both persistence and /healthz diagnostics.
# ---------------------------------------------------------------------------


def _supabase_url() -> str:
    u = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    if not u or "REPLACE" in u:
        raise RuntimeError("SUPABASE_URL not configured")
    return u


def _supabase_anon_key() -> str:
    k = os.getenv("SUPABASE_ANON_KEY", "").strip()
    if not k or k.endswith("REPLACE"):
        raise RuntimeError("SUPABASE_ANON_KEY not configured")
    return k


def persistence_configured() -> bool:
    """True when SUPABASE_URL + SUPABASE_ANON_KEY are real values."""
    try:
        _supabase_url()
        _supabase_anon_key()
        return True
    except RuntimeError:
        return False


# ---------------------------------------------------------------------------
# Per-request user-scoped client.
# ---------------------------------------------------------------------------


async def _client_for_user(user_jwt: str) -> AsyncClient:
    """Build a Supabase async client whose every PostgREST request rides on
    the *user's* JWT, so RLS sees ``auth.uid() = the_user``.

    Per-request construction is cheap (no eager network call); the service key
    is never used here (RLS-bypassing).
    """
    client = await acreate_client(_supabase_url(), _supabase_anon_key())
    client.postgrest.auth(user_jwt)
    return client


# ---------------------------------------------------------------------------
# Conversations + messages — the P4.2 surface.
# ---------------------------------------------------------------------------


async def get_or_create_conversation(
    user_jwt: str,
    user_id: str,
    conversation_id: str | None,
    title: str | None = None,
) -> dict[str, Any] | None:
    """Fetch the existing conversation (RLS-filtered to this user) or create one.

    If ``conversation_id`` is given but the row isn't visible (doesn't exist, or
    belongs to another user — RLS hides it), we create a fresh one rather than
    leak that information. Returns the row, or None on insert failure.
    """
    c = await _client_for_user(user_jwt)
    if conversation_id:
        res = (
            await c.table("conversations")
            .select("id, user_id, title, created_at, updated_at")
            .eq("id", conversation_id)
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0]
    res = (
        await c.table("conversations")
        .insert({"user_id": user_id, "title": title})
        .execute()
    )
    return res.data[0] if res.data else None


async def add_message(
    user_jwt: str,
    conversation_id: str,
    user_id: str,
    *,
    role: str,
    content: str | None = None,
    widgets: list[dict[str, Any]] | None = None,
) -> None:
    """Append a message and touch the conversation's `updated_at`.

    The conversations `set_updated_at` trigger fires on the update below and
    sets `updated_at = now()` server-side, so list views bubble active
    conversations to the top.
    """
    c = await _client_for_user(user_jwt)
    await c.table("messages").insert(
        {
            "conversation_id": conversation_id,
            "user_id": user_id,
            "role": role,
            "content": content,
            "widgets": widgets,
        }
    ).execute()
    # Touch the parent conversation (trigger overwrites the value with now()).
    await c.table("conversations").update(
        {"updated_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", conversation_id).execute()


async def list_conversations(user_jwt: str, limit: int = 50) -> list[dict[str, Any]]:
    """List this user's conversations, most-recently-active first.

    RLS guarantees rows from another user can't appear here.
    """
    c = await _client_for_user(user_jwt)
    res = (
        await c.table("conversations")
        .select("id, title, created_at, updated_at")
        .order("updated_at", desc=True)
        .limit(limit)
        .execute()
    )
    return res.data or []


async def get_conversation_messages(
    user_jwt: str, conversation_id: str
) -> list[dict[str, Any]]:
    """List the messages in a conversation, oldest-first.

    RLS guarantees this returns rows only if the requesting user owns the
    conversation; otherwise an empty list.
    """
    c = await _client_for_user(user_jwt)
    res = (
        await c.table("messages")
        .select("id, role, content, widgets, created_at")
        .eq("conversation_id", conversation_id)
        .order("created_at", desc=False)
        .execute()
    )
    return res.data or []


# ---------------------------------------------------------------------------
# Conversation memory (proposal 046) — turn persisted rows into a Claude-ready
# history the agent loop can be seeded with, so the agent remembers earlier
# turns in the SAME chat. Pure + side-effect-free (easy to unit-test): main.py
# loads the rows (RLS-scoped) and calls this; agent.run_agent seeds `messages`.
# ---------------------------------------------------------------------------


def to_agent_history(
    rows: list[dict[str, Any]],
    *,
    max_messages: int | None = None,
    max_chars: int | None = None,
) -> list[dict[str, str]]:
    """Convert persisted message rows (oldest-first, as `get_conversation_messages`
    returns) into a list of ``{"role", "content"}`` dicts safe to prepend to the
    Anthropic ``messages`` array.

    Guarantees (Anthropic requires strict user/assistant alternation, starting
    with user):
      - assistant rows use their stored markdown ``content``; a widget-only turn
        (no text) becomes a short ``[Assistant showed a <type> widget]`` line —
        we deliberately do NOT replay the widget's numeric JSON, so stale numbers
        can't masquerade as fresh (trust principle #1) while continuity is kept;
      - empty rows are skipped; consecutive same-role rows are merged;
      - a leading assistant / trailing user is dropped, so the caller can append
        the fresh user turn and preserve `user → assistant → … → user` ordering;
      - bounded to the last ``max_messages`` turns and ``max_chars`` characters
        (most-recent kept; oldest trimmed) to cap token cost. Defaults come from
        ``CHAT_HISTORY_MAX_MESSAGES`` (20) / ``CHAT_HISTORY_MAX_CHARS`` (12000);
        either ≤ 0 disables history (returns []).
    """
    if max_messages is None:
        max_messages = int(os.getenv("CHAT_HISTORY_MAX_MESSAGES", "20"))
    if max_chars is None:
        max_chars = int(os.getenv("CHAT_HISTORY_MAX_CHARS", "12000"))
    if not rows or max_messages <= 0 or max_chars <= 0:
        return []

    # 1. rows → (role, text), skipping empties; widget-only assistant → placeholder.
    pairs: list[tuple[str, str]] = []
    for r in rows:
        role = r.get("role")
        if role not in ("user", "assistant"):
            continue
        text = (r.get("content") or "").strip()
        if not text and role == "assistant":
            widgets = r.get("widgets") or []
            types = [w.get("type") for w in widgets
                     if isinstance(w, dict) and w.get("type")]
            if types:
                text = f"[Assistant showed a {', '.join(types)} widget]"
        if not text:
            continue
        pairs.append((role, text))

    # 2. merge consecutive same-role messages.
    merged: list[tuple[str, str]] = []
    for role, text in pairs:
        if merged and merged[-1][0] == role:
            merged[-1] = (role, merged[-1][1] + "\n\n" + text)
        else:
            merged.append((role, text))

    def _trim(seq: list[tuple[str, str]]) -> list[tuple[str, str]]:
        s = list(seq)
        while s and s[0][0] != "user":       # must start with a user turn
            s.pop(0)
        while s and s[-1][0] != "assistant":  # caller appends the fresh user turn
            s.pop()
        return s

    merged = _trim(merged)

    # 3. bound by message count (keep most recent), then re-trim.
    if len(merged) > max_messages:
        merged = _trim(merged[-max_messages:])

    # 4. bound by total chars (keep most recent; always keep ≥1), then re-trim.
    total = 0
    kept_rev: list[tuple[str, str]] = []
    for role, text in reversed(merged):
        total += len(text)
        if kept_rev and total > max_chars:
            break
        kept_rev.append((role, text))
    merged = _trim(list(reversed(kept_rev)))

    return [{"role": role, "content": text} for role, text in merged]
