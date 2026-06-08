"""IBKR connection + waitlist storage (W4 — pivot track).

The persistence layer behind the connect flow (W4) and the daily cron (W5):

  • USER-FACING (the connect endpoint) — writes/reads the caller's OWN
    `ibkr_connections` row via the **user's JWT**, so RLS (`auth.uid() = user_id`)
    enforces isolation. Mirrors `db.py`'s `_client_for_user`. The plaintext Flex
    token is encrypted (token_crypto, Fernet) BEFORE storage; user reads never
    return the token (encrypted or not).

  • SYSTEM-FACING (the W5 cron) — `*_admin` functions read ALL opted-in
    connections + write the delivery log via the **service key**. service_role
    bypasses RLS; this is the ONE legitimate admin path (a backend job iterating
    every connected user, not a user request). `db.py` deliberately never used the
    service key — W4 introduces it, isolated to these clearly-named functions.

Tables: `backend/db/schema_waitlist.sql` (run once in Supabase).
"""
from __future__ import annotations

import logging
import os
from typing import Any

from postgrest.types import ReturnMethod
from supabase import AsyncClient, acreate_client

import token_crypto
from db import _supabase_anon_key, _supabase_url, _client_for_user  # reuse P4.2 config/clients

log = logging.getLogger(__name__)


def _service_key() -> str:
    k = os.getenv("SUPABASE_SERVICE_KEY", "").strip()
    if not k or k.endswith("REPLACE"):
        raise RuntimeError("SUPABASE_SERVICE_KEY not configured (required for the W5 system read)")
    return k


def connect_storage_configured() -> bool:
    """True when the connect/cron storage can work: Supabase reachable + a Flex
    encryption key set. (Service key is only needed by the cron, checked there.)"""
    try:
        _supabase_url()
        _supabase_anon_key()
    except RuntimeError:
        return False
    return token_crypto.token_crypto_configured()


async def _admin_client() -> AsyncClient:
    """Service-key client — SYSTEM JOBS ONLY (bypasses RLS). Never build this in
    response to a user request; use `_client_for_user` (RLS) there."""
    return await acreate_client(_supabase_url(), _service_key())


_PUBLIC_COLS = ("user_id", "flex_query_id", "whatsapp_number", "opt_in", "status",
                "created_at", "updated_at")


def _public_view(row: dict[str, Any]) -> dict[str, Any]:
    """A connection row WITHOUT the token field — safe to return from an endpoint."""
    return {k: row.get(k) for k in _PUBLIC_COLS if k in row}


# ---------------------------------------------------------------------------
# User-facing (connect endpoint) — RLS via the user's JWT.
# ---------------------------------------------------------------------------

async def upsert_my_connection(
    user_jwt: str,
    user_id: str,
    *,
    flex_token: str,
    flex_query_id: str,
    whatsapp_number: str,
    opt_in: bool = True,
) -> dict[str, Any] | None:
    """Create/replace THIS user's IBKR connection. The Flex token is encrypted
    here; only ciphertext is sent to the DB. Returns the public view (no token)."""
    row = {
        "user_id": user_id,
        "flex_token_encrypted": token_crypto.encrypt_token(flex_token),
        "flex_query_id": flex_query_id,
        "whatsapp_number": whatsapp_number,
        "opt_in": opt_in,
        "status": "active",
    }
    c = await _client_for_user(user_jwt)
    res = await c.table("ibkr_connections").upsert(row, on_conflict="user_id").execute()
    return _public_view(res.data[0]) if res.data else None


async def get_my_connection(user_jwt: str) -> dict[str, Any] | None:
    """This user's connection (public view — no token), or None if not connected.
    RLS guarantees a user can only ever see their own row."""
    c = await _client_for_user(user_jwt)
    res = (
        await c.table("ibkr_connections")
        .select(",".join(_PUBLIC_COLS))
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


async def set_opt_in(user_jwt: str, user_id: str, opt_in: bool) -> dict[str, Any] | None:
    """Flip this user's opt-in (the in-app unsubscribe; STOP-webhook handling is W6).

    The `.eq("user_id", …)` filter is required: PostgREST refuses an unfiltered
    UPDATE (mass-update guard) — RLS alone scopes the rows but PostgREST still
    wants an explicit filter. (Real-run fix: a bare update 500'd.)
    """
    c = await _client_for_user(user_jwt)
    res = (
        await c.table("ibkr_connections")
        .update({"opt_in": opt_in})
        .eq("user_id", user_id)
        .execute()
    )
    return _public_view(res.data[0]) if res.data else None


# ---------------------------------------------------------------------------
# System-facing (W5 cron) — service key, bypasses RLS. Decrypts the token.
# ---------------------------------------------------------------------------

async def list_active_connections_admin() -> list[dict[str, Any]]:
    """Every opted-in, active connection WITH the decrypted Flex token + query id —
    what the W5 cron iterates to fetch holdings (W1) and send (W3). SERVICE KEY.

    A row whose token won't decrypt (wrong/rotated key) is skipped with its
    user_id flagged, not silently dropped into a crash — the cron logs + continues.
    """
    c = await _admin_client()
    res = (
        await c.table("ibkr_connections")
        .select("*")
        .eq("opt_in", True)
        .eq("status", "active")
        .execute()
    )
    out: list[dict[str, Any]] = []
    for r in res.data or []:
        rec = _public_view(r)
        try:
            rec["flex_token"] = token_crypto.decrypt_token(r["flex_token_encrypted"])
        except token_crypto.TokenCryptoError as e:
            rec["flex_token"] = None
            rec["decrypt_error"] = e.code
        out.append(rec)
    return out


async def log_delivery_admin(
    user_id: str,
    *,
    status: str,
    account_id: str | None = None,
    as_of: str | None = None,
    channel: str = "whatsapp",
    provider_id: str | None = None,
    error: str | None = None,
) -> None:
    """Append a delivery-log row (metadata only — never the brief body). SERVICE KEY."""
    c = await _admin_client()
    await c.table("briefing_deliveries").insert({
        "user_id": user_id,
        "account_id": account_id,
        "as_of": as_of,
        "channel": channel,
        "status": status,
        "provider_id": provider_id,
        "error": error,
    }).execute()


# ---------------------------------------------------------------------------
# Waitlist — pre-auth interest capture (anon insert allowed by RLS).
# ---------------------------------------------------------------------------

async def add_waitlist_signup(email: str, *, source: str | None = None) -> bool:
    """Record an email on the waitlist (idempotent on lower(email)). Uses the
    anon key — the RLS policy allows insert-only (no read/harvest).

    `returning=minimal` is REQUIRED here: the table has an INSERT policy but NO
    SELECT policy for anon (deliberate — no list harvest), so supabase-py's default
    `return=representation` would do a post-insert SELECT that RLS denies and the
    whole call errors. Minimal skips that read. (Real-run fix: waitlist returned
    `{"ok": false}` for exactly this reason.) A duplicate email (unique index,
    SQLSTATE 23505) is treated as idempotent success.
    """
    client = await acreate_client(_supabase_url(), _supabase_anon_key())
    try:
        await client.table("waitlist_signups").insert(
            {"email": email.strip().lower(), "source": source},
            returning=ReturnMethod.minimal,
        ).execute()
        return True
    except Exception as e:  # noqa: BLE001
        if "23505" in str(e) or "duplicate" in str(e).lower():
            return True  # already on the list — idempotent
        log.info("waitlist signup failed: %s", e)
        return False
