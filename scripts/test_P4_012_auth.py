#!/usr/bin/env python3
"""P4.1 / Proposal 012 — Supabase magic-link JWT auth regression test.

Unit-level checks of backend/auth.py with NO Supabase account and NO network:
mints HS256 tokens with a throwaway secret (the same algorithm Supabase uses)
and asserts verification + the REQUIRE_AUTH policy behave correctly.

Run (after applying 012, from repo root):
    backend/.venv/bin/python scripts/test_P4_012_auth.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# backend/ is on sys.path (not a package) — mirror the app's import convention.
# When run after apply this resolves to the repo's backend/; a PYTHONPATH entry
# (see docstring) takes precedence for pre-apply runs against the proposed copy.
_BACKEND = Path(__file__).resolve().parent.parent / "backend"
if _BACKEND.is_dir():
    sys.path.insert(0, str(_BACKEND))

import jwt  # PyJWT
from fastapi import HTTPException

SECRET = "test-jwt-secret-not-a-real-supabase-secret-but-long-enough-for-sha256"
UID = "11111111-2222-3333-4444-555555555555"

os.environ["SUPABASE_JWT_SECRET"] = SECRET
os.environ.pop("REQUIRE_AUTH", None)  # start in demo posture

import auth  # noqa: E402  — resolved from backend/ (or PYTHONPATH proposed copy)

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


def mint(
    sub: str = UID,
    *,
    secret: str = SECRET,
    aud: str = "authenticated",
    exp_delta: int = 3600,
    drop_aud: bool = False,
) -> str:
    now = int(time.time())
    claims: dict = {"sub": sub, "iat": now, "exp": now + exp_delta}
    if not drop_aud:
        claims["aud"] = aud
    return jwt.encode(claims, secret, algorithm="HS256")


print("P4.1 / 012 — backend/auth.py")

# 1) valid token → sub (UUID)
check("valid token → sub", auth.verify_jwt(mint()) == UID)

# 2) expired token → 401 token_expired
try:
    auth.verify_jwt(mint(exp_delta=-10))
    check("expired token → 401", False)
except HTTPException as e:
    check("expired token → 401", e.status_code == 401 and e.detail == "token_expired")

# 3) wrong-secret signature → 401
try:
    auth.verify_jwt(mint(secret="someone-elses-secret"))
    check("bad signature → 401", False)
except HTTPException as e:
    check("bad signature → 401", e.status_code == 401)

# 4) missing aud claim → 401 (Supabase always sets aud=authenticated)
try:
    auth.verify_jwt(mint(drop_aud=True))
    check("missing aud → 401", False)
except HTTPException as e:
    check("missing aud → 401", e.status_code == 401)

# 5) bearer extraction
tok = mint()
check("extract bearer ok", auth._extract_bearer(f"Bearer {tok}") == tok)
check("extract bearer is case-insensitive", auth._extract_bearer(f"bearer {tok}") == tok)
check("extract bearer None for missing header", auth._extract_bearer(None) is None)
check("extract bearer None for Basic scheme", auth._extract_bearer("Basic abc") is None)
check("extract bearer None for empty bearer", auth._extract_bearer("Bearer ") is None)

# 6) policy — REQUIRE_AUTH off (local/demo)
os.environ.pop("REQUIRE_AUTH", None)
check("require_auth() false when unset", auth.require_auth() is False)
check("no token + REQUIRE_AUTH off → demo", auth.resolve_user_id(None) == "demo")
check("valid token → real user_id (auth off)", auth.resolve_user_id(f"Bearer {tok}") == UID)

# 7) policy — REQUIRE_AUTH on (production)
os.environ["REQUIRE_AUTH"] = "1"
check("require_auth() true when '1'", auth.require_auth() is True)
try:
    auth.resolve_user_id(None)
    check("no token + REQUIRE_AUTH on → 401", False)
except HTTPException as e:
    check("no token + REQUIRE_AUTH on → 401", e.status_code == 401)
check("valid token + REQUIRE_AUTH on → user_id", auth.resolve_user_id(f"Bearer {tok}") == UID)
# a provided-but-bad token is 401 even though a token was present
try:
    auth.resolve_user_id("Bearer not-a-jwt")
    check("garbage token → 401 (never downgraded to demo)", False)
except HTTPException as e:
    check("garbage token → 401 (never downgraded to demo)", e.status_code == 401)

# 8) misconfig — token present but no secret → 500 (not a silent pass)
os.environ.pop("SUPABASE_JWT_SECRET", None)
try:
    auth.verify_jwt(tok)
    check("no secret configured → 500", False)
except HTTPException as e:
    check("no secret configured → 500", e.status_code == 500)
os.environ["SUPABASE_JWT_SECRET"] = SECRET  # restore
check("auth_configured() true with real secret", auth.auth_configured() is True)

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
