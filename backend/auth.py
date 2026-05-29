"""Supabase magic-link JWT verification (P4.1, asymmetric-aware — proposal 015).

Resolves the *trusted* user_id for a request from an
``Authorization: Bearer <jwt>`` header — replacing the spoofable ``"demo"``
body field (SECURITY_AUDIT HIGH-2).

Verification dispatches on the token's header ``alg``:

  • **ES256 / RS256 / EdDSA** — Supabase's "JWT Signing Keys" (the current
    default). Verified against the project's published **public keys (JWKS)** at
    ``<SUPABASE_URL>/auth/v1/.well-known/jwks.json``. Keys are fetched once and
    cached by ``PyJWKClient``; the fetch runs in FastAPI's threadpool (this
    dependency is sync), so it never blocks the event loop. Needs ``SUPABASE_URL``
    (+ the anon key, sent as the ``apikey`` header). No shared secret.

  • **HS256** — the legacy symmetric secret. Verified offline with
    ``SUPABASE_JWT_SECRET``. Kept so HS256 projects (or a mid-migration project)
    still work.

Two env knobs (mirroring the codebase's USE_MOCK_* kill-switches):

    REQUIRE_AUTH          "1"/"true"/… → reject unauthenticated requests (401).
                          Unset/"0" → fall back to "demo" when no token is sent,
                          so the deterministic mock demo, scripts/smoke_test.sh
                          and curl checks keep working.
    SUPABASE_URL          project URL — used to build the JWKS endpoint (asym).
    SUPABASE_JWT_SECRET   HS256 secret — only for legacy symmetric projects.

Policy (resolve_user_id):
    token present                       → verify; valid → sub (UUID); invalid → 401
    no token + REQUIRE_AUTH truthy      → 401
    no token + REQUIRE_AUTH falsy       → "demo"

Identity is ALWAYS derived from the token, never from the request body. A
provided-but-invalid token is a 401 regardless of REQUIRE_AUTH (we never
silently downgrade a bad token to demo).
"""

from __future__ import annotations

import os

import jwt  # PyJWT (with the `crypto` extra → ES256/RS256 supported)
from fastapi import Header, HTTPException

DEMO_USER_ID = "demo"
_JWT_AUDIENCE = "authenticated"  # Supabase sets aud="authenticated" for logged-in users
_TRUTHY = {"1", "true", "yes", "on"}
_ASYM_ALGS = {"ES256", "RS256", "EdDSA"}  # Supabase asymmetric "JWT Signing Keys"

# Lazily-built, cached JWKS client (None until the first asymmetric token).
_jwks_client: jwt.PyJWKClient | None = None


def require_auth() -> bool:
    """True when unauthenticated requests must be rejected (production posture)."""
    return os.getenv("REQUIRE_AUTH", "0").strip().lower() in _TRUTHY


def _supabase_url() -> str:
    u = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    return "" if (not u or "REPLACE" in u) else u


def _jwt_secret() -> str:
    s = os.getenv("SUPABASE_JWT_SECRET", "")
    return "" if (not s or s.endswith("REPLACE")) else s


def auth_configured() -> bool:
    """True when we can verify a token — a JWKS URL (asym) or an HS256 secret.

    Surfaced in /healthz so a deploy that requires auth but has neither is visible.
    """
    return bool(_supabase_url() or _jwt_secret())


def _get_jwks_client() -> jwt.PyJWKClient:
    """Build (once) a cached JWKS client for the project's public keys.

    Sends the anon key as the `apikey` header — Supabase's API gateway may
    require it on `/auth/v1/*`. The anon key is public, so this is safe.
    """
    global _jwks_client
    if _jwks_client is None:
        url = _supabase_url()
        if not url:
            raise HTTPException(status_code=500, detail="auth_not_configured")
        anon = os.getenv("SUPABASE_ANON_KEY", "")
        headers = {"apikey": anon} if (anon and not anon.endswith("REPLACE")) else None
        _jwks_client = jwt.PyJWKClient(
            f"{url}/auth/v1/.well-known/jwks.json",
            headers=headers,
        )
    return _jwks_client


def verify_jwt(token: str) -> str:
    """Verify a Supabase access token and return its `sub` (user UUID).

    Dispatches on the token's `alg` (asymmetric via JWKS, HS256 via secret).
    Raises HTTPException(401) for an invalid/expired/unsupported token,
    HTTPException(500) if HS256 is used but no secret is configured,
    HTTPException(503) if the JWKS public keys can't be fetched.
    """
    try:
        alg = jwt.get_unverified_header(token).get("alg")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="invalid_token")

    if alg in _ASYM_ALGS:
        try:
            key = _get_jwks_client().get_signing_key_from_jwt(token).key
        except HTTPException:
            raise
        except Exception:
            # JWKS unreachable, or the token's `kid` isn't in the published set.
            raise HTTPException(status_code=503, detail="jwks_unavailable")
        algs = [alg]
    elif alg == "HS256":
        key = _jwt_secret()
        if not key:
            raise HTTPException(status_code=500, detail="auth_not_configured")
        algs = ["HS256"]
    else:
        # Reject `none` and anything we don't explicitly allow.
        raise HTTPException(status_code=401, detail="unsupported_alg")

    try:
        payload = jwt.decode(
            token,
            key,
            algorithms=algs,
            audience=_JWT_AUDIENCE,
            leeway=10,  # small clock-skew tolerance
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="token_expired")
    except jwt.InvalidTokenError:
        # bad signature, wrong/missing audience, malformed token, etc.
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
