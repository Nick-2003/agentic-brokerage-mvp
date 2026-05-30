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
