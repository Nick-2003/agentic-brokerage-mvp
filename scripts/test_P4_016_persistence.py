#!/usr/bin/env python3
"""P4.2 / Proposal 016 — auth + db unit tests (no Supabase needed).

Covers the bits that don't need a live Supabase: the `resolve_auth` policy
(parallels the 012 `resolve_user_id` tests), `AuthCtx` shape, and that
`db.py` imports cleanly + reads config consistently.

The **integration test** — sign in as user A, chat, restart backend, GET
`/api/conversations`, then sign in as user B and confirm A's rows aren't
visible — is the README runbook (needs two real Supabase users + the
applied schema).

Run (after applying 016, from repo root):
    backend/.venv/bin/python scripts/test_P4_016_persistence.py

Pre-apply (against the proposed copy):
    PYTHONPATH=proposed_changes/016-supabase-persistence-rls/backend \
        backend/.venv/bin/python proposed_changes/016-supabase-persistence-rls/scripts/test_P4_016_persistence.py
"""

from __future__ import annotations

import dataclasses
import os
import sys
import time
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent / "backend"
if _BACKEND.is_dir():
    sys.path.insert(0, str(_BACKEND))

import jwt  # PyJWT
from fastapi import HTTPException

SECRET = "test-jwt-secret-not-a-real-supabase-secret-but-long-enough-for-sha256"
UID = "11111111-2222-3333-4444-555555555555"

os.environ["SUPABASE_JWT_SECRET"] = SECRET
os.environ.pop("REQUIRE_AUTH", None)

import auth  # noqa: E402
import db  # noqa: E402

_passed = 0
_failed = 0


def check(name: str, cond: bool) -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ✓ {name}")
    else:
        _failed += 1
        print(f"  ✗ {name}")


def _is_frozen(obj) -> bool:
    """True iff `obj` is a frozen dataclass instance."""
    return dataclasses.is_dataclass(obj) and obj.__dataclass_params__.frozen


def mint_hs256(sub: str = UID, exp_delta: int = 3600) -> str:
    now = int(time.time())
    return jwt.encode(
        {"sub": sub, "aud": "authenticated", "iat": now, "exp": now + exp_delta},
        SECRET,
        algorithm="HS256",
    )


print("P4.2 / 016 — backend/auth.py::resolve_auth + backend/db.py")

# ── 1) AuthCtx + resolve_auth policy (parallels 012's resolve_user_id tests) ──

# Valid HS256 token (uses 015's auth.py HS256 branch — proves resolve_auth + verify_jwt + AuthCtx).
tok = mint_hs256()
ctx = auth.resolve_auth(f"Bearer {tok}")
check("AuthCtx instance", isinstance(ctx, auth.AuthCtx))
check("AuthCtx.user_id from token", ctx.user_id == UID)
check("AuthCtx.token = the raw JWT", ctx.token == tok)
check("AuthCtx is frozen (dataclass)", _is_frozen(ctx))

# No token + REQUIRE_AUTH off → demo, token=None
os.environ.pop("REQUIRE_AUTH", None)
ctx = auth.resolve_auth(None)
check("no token + REQUIRE_AUTH off → demo", ctx.user_id == "demo" and ctx.token is None)

# No token + REQUIRE_AUTH on → 401
os.environ["REQUIRE_AUTH"] = "1"
try:
    auth.resolve_auth(None)
    check("no token + REQUIRE_AUTH on → 401", False)
except HTTPException as e:
    check("no token + REQUIRE_AUTH on → 401", e.status_code == 401)

# Garbage token → 401 even with REQUIRE_AUTH off (provided-but-bad never downgrades)
os.environ.pop("REQUIRE_AUTH", None)
try:
    auth.resolve_auth("Bearer not-a-jwt")
    check("garbage token → 401 (never downgraded to demo)", False)
except HTTPException as e:
    check("garbage token → 401 (never downgraded to demo)", e.status_code == 401)

# Expired token → 401 token_expired
try:
    auth.resolve_auth(f"Bearer {mint_hs256(exp_delta=-10)}")
    check("expired token → 401 token_expired", False)
except HTTPException as e:
    check(
        "expired token → 401 token_expired",
        e.status_code == 401 and e.detail == "token_expired",
    )

# resolve_user_id (back-compat) still works alongside resolve_auth
check("resolve_user_id (back-compat) still returns sub", auth.resolve_user_id(f"Bearer {tok}") == UID)

# ── 2) db.py config + persistence_configured() ──

# Not configured (placeholder env)
os.environ["SUPABASE_URL"] = "https://REPLACE.supabase.co"
os.environ["SUPABASE_ANON_KEY"] = "eyJREPLACE"
check("persistence_configured() False for placeholders", db.persistence_configured() is False)

os.environ.pop("SUPABASE_URL", None)
os.environ.pop("SUPABASE_ANON_KEY", None)
check("persistence_configured() False when unset", db.persistence_configured() is False)

# Configured (real-shaped values)
os.environ["SUPABASE_URL"] = "https://example.supabase.co"
os.environ["SUPABASE_ANON_KEY"] = "eyJtest-anon-key-shape"
check("persistence_configured() True with real values", db.persistence_configured() is True)

# Reader strips trailing slash
os.environ["SUPABASE_URL"] = "https://example.supabase.co/"
check(
    "SUPABASE_URL trailing slash stripped",
    db._supabase_url() == "https://example.supabase.co",
)

# ── 3) db.py callable surface (no network — just shape) ──

check("db.get_or_create_conversation is callable", callable(db.get_or_create_conversation))
check("db.add_message is callable", callable(db.add_message))
check("db.list_conversations is callable", callable(db.list_conversations))
check("db.get_conversation_messages is callable", callable(db.get_conversation_messages))

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
