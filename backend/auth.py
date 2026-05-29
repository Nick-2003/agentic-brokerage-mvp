"""Supabase magic-link JWT verification (P4.1).

Resolves the *trusted* user_id for a request from an
``Authorization: Bearer <jwt>`` header — replacing the spoofable ``"demo"``
body field (SECURITY_AUDIT HIGH-2).

Verification is OFFLINE (HS256): the frontend's supabase-js holds the access
token after magic-link login; this module checks its signature with the
project's JWT secret. No per-request network call, and no supabase client
needed on the backend for P4.1 (that lands with P4.2 persistence).

Two env knobs (mirroring the codebase's USE_MOCK_* kill-switches):

    REQUIRE_AUTH          "1"/"true"/… → reject unauthenticated requests (401).
                          Unset/"0" → fall back to "demo" when no token is sent,
                          so the deterministic mock demo, scripts/smoke_test.sh
                          and curl checks keep working locally.
    SUPABASE_JWT_SECRET   HS256 secret — Supabase → Settings → API → "JWT Secret".
                          Required whenever a token is actually verified.

Policy (resolve_user_id):
    token present                       → verify; valid → sub (UUID); invalid → 401
    no token + REQUIRE_AUTH truthy      → 401
    no token + REQUIRE_AUTH falsy       → "demo"

Note: identity is ALWAYS derived from the token, never from the request body —
a token, once present, is authoritative. A *provided-but-invalid* token is a
401 regardless of REQUIRE_AUTH (we never silently downgrade a bad token to demo).
"""

from __future__ import annotations

import os

import jwt  # PyJWT
from fastapi import Header, HTTPException

DEMO_USER_ID = "demo"
_JWT_AUDIENCE = "authenticated"  # Supabase sets aud="authenticated" for logged-in users
_TRUTHY = {"1", "true", "yes", "on"}


def require_auth() -> bool:
    """True when unauthenticated requests must be rejected (production posture)."""
    return os.getenv("REQUIRE_AUTH", "0").strip().lower() in _TRUTHY


def auth_configured() -> bool:
    """True when a real (non-placeholder) JWT secret is set — for /healthz diagnostics."""
    secret = os.getenv("SUPABASE_JWT_SECRET", "")
    return bool(secret) and not secret.endswith("REPLACE")


def verify_jwt(token: str) -> str:
    """Verify a Supabase access token (HS256) and return its `sub` (user UUID).

    Raises HTTPException(401) for an invalid/expired token,
    HTTPException(500) if the backend has no JWT secret configured.
    """
    secret = os.getenv("SUPABASE_JWT_SECRET", "")
    if not secret or secret.endswith("REPLACE"):
        # Misconfiguration, not a client error: a token arrived but we can't check it.
        raise HTTPException(status_code=500, detail="auth_not_configured")
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience=_JWT_AUDIENCE,
            leeway=10,  # small clock-skew tolerance
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="token_expired")
    except jwt.InvalidTokenError:
        # covers bad signature, wrong/missing audience, malformed token, etc.
        raise HTTPException(status_code=401, detail="invalid_token")
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="token_missing_sub")
    return str(sub)


def _extract_bearer(authorization: str | None) -> str | None:
    """Pull the token out of an `Authorization: Bearer <token>` header.

    Returns None for a missing/malformed/non-Bearer header (the caller then
    applies the no-token policy).
    """
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        return None
    return parts[1].strip()


def resolve_user_id(authorization: str | None = Header(default=None)) -> str:
    """FastAPI dependency → the trusted user_id for this request.

    Inject into a route: `user_id: str = Depends(resolve_user_id)`.
    """
    token = _extract_bearer(authorization)
    if token:
        return verify_jwt(token)  # spoof-proof identity from the signed token
    if require_auth():
        raise HTTPException(status_code=401, detail="authentication_required")
    return DEMO_USER_ID  # demo fallback (REQUIRE_AUTH off) — keeps mock demos working
