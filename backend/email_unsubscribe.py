"""Email unsubscribe tokens (037) — stateless, signed, no DB row needed.

A one-click unsubscribe link must verifiably identify the user WITHOUT a login
(it's clicked from an inbox) and WITHOUT being guessable/forgeable. We mint a
short HMAC-signed token over the `user_id` and verify it on the unsubscribe
endpoint (`email_api.py`), which then flips `email_opt_in` off (service key).

  token = b64url(user_id) + "." + b64url(HMAC_SHA256(secret, user_id))

Stdlib only (`hmac`/`hashlib`/`base64`); constant-time compare. No expiry — an
unsubscribe link should keep working indefinitely (CAN-SPAM expects ≥30 days; the
honest choice is "forever"). The secret is `EMAIL_UNSUBSCRIBE_SECRET`; with it
unset, tokens can't be minted/verified → `configured()` is False and the scheduler
won't send a real email (no unsubscribe, no send — same fail-loud posture as the
rest of the pivot).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os


class UnsubscribeError(Exception):
    pass


def _secret() -> bytes | None:
    s = os.getenv("EMAIL_UNSUBSCRIBE_SECRET", "").strip()
    if not s or s.endswith("REPLACE"):
        return None
    return s.encode("utf-8")


def configured() -> bool:
    """True when unsubscribe tokens can be minted/verified (secret present)."""
    return _secret() is not None


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _b64u_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def make_token(user_id: str) -> str:
    """Sign `user_id` into an unsubscribe token. Raises if no secret is set."""
    secret = _secret()
    if secret is None:
        raise UnsubscribeError("EMAIL_UNSUBSCRIBE_SECRET not configured")
    if not user_id:
        raise UnsubscribeError("user_id required")
    msg = user_id.encode("utf-8")
    sig = hmac.new(secret, msg, hashlib.sha256).digest()
    return f"{_b64u(msg)}.{_b64u(sig)}"


def verify_token(token: str) -> str | None:
    """Return the `user_id` iff the token is well-formed and the signature matches
    the current secret; otherwise None. Never raises on a malformed token."""
    secret = _secret()
    if secret is None or not token or "." not in token:
        return None
    try:
        msg_b64, sig_b64 = token.split(".", 1)
        msg = _b64u_decode(msg_b64)
        sig = _b64u_decode(sig_b64)
    except Exception:  # noqa: BLE001 — any decode error = invalid token
        return None
    expected = hmac.new(secret, msg, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        return msg.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _base_url() -> str:
    """Origin the unsubscribe link points at.

    Prefer `PUBLIC_BACKEND_URL` (the Railway backend, where `/api/email/unsubscribe`
    actually lives — most robust, no proxy hop). Fall back to `PUBLIC_BASE_URL` (the
    FRONTEND origin), which works because 037 adds an `/api/email/:path*` rewrite that
    proxies to the backend. Set `PUBLIC_BACKEND_URL` on the cron for directness.
    """
    base = os.getenv("PUBLIC_BACKEND_URL", "").strip() or os.getenv(
        "PUBLIC_BASE_URL", "http://localhost:3000"
    ).strip()
    return base.rstrip("/")


def unsubscribe_url_for(user_id: str) -> str | None:
    """Full one-click unsubscribe URL for a user, or None if not configured."""
    if not configured():
        return None
    return f"{_base_url()}/api/email/unsubscribe?token={make_token(user_id)}"
