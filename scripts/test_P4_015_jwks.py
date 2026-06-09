#!/usr/bin/env python3
"""P4.1 / Proposal 015 — asymmetric (ES256) JWT verification regression test.

Round-trips a real ES256 token against a generated EC P-256 key, with the JWKS
client stubbed so NO Supabase account / network is needed. Complements
scripts/test_P4_012_auth.py (which covers the HS256 path + the REQUIRE_AUTH
policy — both still pass against the 015 auth.py).

Run (after applying 015, from repo root):
    backend/.venv/bin/python scripts/test_P4_015_jwks.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent / "backend"
if _BACKEND.is_dir():
    sys.path.insert(0, str(_BACKEND))

import jwt  # PyJWT
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException

UID = "11111111-2222-3333-4444-555555555555"

# Asymmetric verification uses SUPABASE_URL (for the JWKS endpoint), not a secret.
os.environ["SUPABASE_URL"] = "https://example.supabase.co"
os.environ["SUPABASE_ANON_KEY"] = "eyJtest-anon-key"
os.environ.pop("SUPABASE_JWT_SECRET", None)
os.environ.pop("REQUIRE_AUTH", None)

import auth  # noqa: E402

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


# --- generate an EC P-256 keypair and stub the JWKS client to return its public key ---
priv = ec.generate_private_key(ec.SECP256R1())
pub = priv.public_key()
other_priv = ec.generate_private_key(ec.SECP256R1())  # a DIFFERENT key (for the bad-sig test)


class _StubKey:
    def __init__(self, key):
        self.key = key


class _StubJWKS:
    """Stands in for PyJWKClient — returns our public key for any token."""

    def __init__(self, key):
        self._key = key

    def get_signing_key_from_jwt(self, _token):
        return _StubKey(self._key)


auth._jwks_client = _StubJWKS(pub)  # bypass the network fetch


def mint(*, key=priv, exp_delta=3600, aud="authenticated", drop_aud=False, kid="test-kid") -> str:
    now = int(time.time())
    claims: dict = {"sub": UID, "iat": now, "exp": now + exp_delta}
    if not drop_aud:
        claims["aud"] = aud
    return jwt.encode(claims, key, algorithm="ES256", headers={"kid": kid})


print("P4.1 / 015 — backend/auth.py (ES256 / JWKS)")

# 1) valid ES256 token verified via the (stubbed) JWKS public key → sub
check("valid ES256 token → sub", auth.verify_jwt(mint()) == UID)

# 2) expired ES256 token → 401 token_expired
try:
    auth.verify_jwt(mint(exp_delta=-10))
    check("expired ES256 → 401", False)
except HTTPException as e:
    check("expired ES256 → 401", e.status_code == 401 and e.detail == "token_expired")

# 3) signed by a DIFFERENT key (JWKS returns `pub`) → bad signature → 401
try:
    auth.verify_jwt(mint(key=other_priv))
    check("wrong-key ES256 → 401", False)
except HTTPException as e:
    check("wrong-key ES256 → 401", e.status_code == 401 and e.detail == "invalid_token")

# 4) missing aud → 401
try:
    auth.verify_jwt(mint(drop_aud=True))
    check("missing aud → 401", False)
except HTTPException as e:
    check("missing aud → 401", e.status_code == 401)

# 5) `alg: none` (and other unsupported algs) → 401 unsupported_alg
import base64
import json


def _b64(d: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()


none_token = f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{_b64({'sub': UID})}."
try:
    auth.verify_jwt(none_token)
    check("alg=none → 401 unsupported_alg", False)
except HTTPException as e:
    check("alg=none → 401 unsupported_alg", e.status_code == 401 and e.detail == "unsupported_alg")

# 6) JWKS unreachable (stub raises) → 503 jwks_unavailable
class _BoomJWKS:
    def get_signing_key_from_jwt(self, _token):
        raise RuntimeError("network down")


auth._jwks_client = _BoomJWKS()
try:
    auth.verify_jwt(mint())
    check("JWKS unreachable → 503", False)
except HTTPException as e:
    check("JWKS unreachable → 503", e.status_code == 503 and e.detail == "jwks_unavailable")
auth._jwks_client = _StubJWKS(pub)  # restore

# 7) policy still works with the asymmetric setup
check("auth_configured() true via SUPABASE_URL (no secret)", auth.auth_configured() is True)
check("no token + REQUIRE_AUTH off → demo", auth.resolve_user_id(None) == "demo")
check("valid ES256 bearer → user_id", auth.resolve_user_id(f"Bearer {mint()}") == UID)
os.environ["REQUIRE_AUTH"] = "1"
try:
    auth.resolve_user_id(None)
    check("no token + REQUIRE_AUTH on → 401", False)
except HTTPException as e:
    check("no token + REQUIRE_AUTH on → 401", e.status_code == 401)
os.environ.pop("REQUIRE_AUTH", None)

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
