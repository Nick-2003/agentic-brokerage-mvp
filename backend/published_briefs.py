"""Published-brief store (W6.3) — the web permalink behind the WhatsApp message.

A brief is published under a high-entropy **capability token**; the recipient opens
`/<PUBLIC_BASE_URL>/b/<token>` (no sign-in) to read the full narrative. The body is
sensitive (holdings/P&L), so this is deliberately:
  • token-gated — `secrets.token_urlsafe(32)` (~256-bit), unguessable;
  • expiring — `BRIEF_PERMALINK_TTL_DAYS` (default 7) so a leaked link goes stale;
  • service-role only — reads go through `_admin_client` (RLS has no anon policy),
    by EXACT token; no enumeration, no direct PostgREST access.

System-side (like `connections.py`'s admin path); not an agent tool. Mirrors the
W4 admin model and reuses its service-key client.
"""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from connections import _admin_client  # service-key client (RLS-bypassing; system only)

_TTL_DAYS = int(os.getenv("BRIEF_PERMALINK_TTL_DAYS", "7"))


def _public_base() -> str:
    return os.getenv("PUBLIC_BASE_URL", "http://localhost:3000").rstrip("/")


def permalink_for(token: str) -> str:
    return f"{_public_base()}/b/{token}"


async def publish_brief(
    user_id: str | None,
    body: str,
    *,
    account_id: str | None = None,
    as_of: str | None = None,
    chart_data: dict | None = None,
) -> dict[str, Any]:
    """Store the brief under a fresh capability token. Returns {token, permalink}.
    SERVICE KEY. Raises on a DB failure (caller decides whether to proceed).

    `chart_data` (051) is an optional per-holding day-P&L payload rendered as a
    bar chart on /b/<token>, stored under the same token gate as the body.
    None → no chart on the page.
    """
    if not body or not body.strip():
        raise ValueError("refusing to publish an empty brief")
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(days=_TTL_DAYS)).isoformat()
    c = await _admin_client()
    await c.table("published_briefs").insert({
        "token": token,
        "user_id": user_id,
        "account_id": account_id,
        "as_of": as_of,
        "body": body,
        "chart_data": chart_data,
        "expires_at": expires_at,
    }).execute()
    return {"token": token, "permalink": permalink_for(token)}


async def get_published_brief(token: str) -> dict[str, Any] | None:
    """Fetch a published brief by its EXACT token, or None if missing/expired.
    SERVICE KEY (the only reader — RLS denies anon). The token is the capability."""
    if not token:
        return None
    c = await _admin_client()
    res = (
        await c.table("published_briefs")
        .select("body, account_id, as_of, chart_data, created_at, expires_at")
        .eq("token", token)
        .limit(1)
        .execute()
    )
    row = res.data[0] if res.data else None
    if not row:
        return None
    exp = row.get("expires_at")
    if exp and _parse(exp) is not None and _parse(exp) < datetime.now(timezone.utc):
        return None  # expired — treat as gone (fail-closed)
    return {
        "text": row.get("body"),
        "account_id": row.get("account_id"),
        "as_of": row.get("as_of"),
        "chart_data": row.get("chart_data"),  # 051 — day-P&L bars (None if absent)
        "generated_at": row.get("created_at"),
    }


def _parse(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
