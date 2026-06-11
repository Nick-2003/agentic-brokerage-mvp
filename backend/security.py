"""W6.6 — security hardening for the LIVE waitlist product.

A single **pure-ASGI** middleware (deliberately NOT Starlette `BaseHTTPMiddleware`,
which buffers the response and breaks the `/api/chat` SSE stream) that does two
things on every HTTP request:

  • **Rate limiting** — a per-client-IP fixed window over the public/auth'd API
    paths, so the live `/connect` surface (waitlist signup, IBKR connect, brief
    permalink, chat) can't be hammered. **Excludes** the Twilio webhooks
    (signature-gated, and Twilio retries from shared IPs → don't 429 them) and
    `/healthz` (Railway probes it every 30s). In-memory — fine for Railway's single
    replica; swap for a Redis store if you ever scale to multiple instances.
    **Fail-open**: any internal limiter error allows the request (a limiter bug
    must never take the API down).

  • **Security headers** — `X-Content-Type-Options`, `X-Frame-Options`,
    `Referrer-Policy`, `Permissions-Policy`, `HSTS` — added (set-if-absent) to
    every response, including the 429 and the SSE stream.

Config (all optional; safe defaults):
    RATE_LIMIT_ENABLED=1
    RATE_LIMIT_WINDOW_SECONDS=60
    RATE_LIMIT_MAX_REQUESTS=60     # per IP per window, across the guarded /api paths
"""
from __future__ import annotations

import json
import logging
import os
import time

log = logging.getLogger(__name__)


def _enabled() -> bool:
    return os.getenv("RATE_LIMIT_ENABLED", "1") != "0"


def _window() -> float:
    return float(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))


def _max() -> int:
    return int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "60"))


# Guard everything under /api/ EXCEPT the Twilio webhooks (signature-gated +
# retried from shared IPs). /healthz isn't under /api/, so it's already exempt.
_PROTECTED_PREFIX = "/api/"
_EXCLUDE_PREFIXES = ("/api/twilio/",)
_EXCLUDE_EXACT = {"/healthz"}

# Sent on every response (lowercased keys, bytes — ASGI form). set-if-absent, so
# anything CORS/the platform already set is preserved.
_SECURITY_HEADERS: list[tuple[bytes, bytes]] = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy", b"geolocation=(), microphone=(), camera=()"),
    (b"strict-transport-security", b"max-age=31536000; includeSubDomains"),
]

# ip -> (window_start_monotonic, count). In-memory; swept when it grows large.
_hits: dict[str, tuple[float, int]] = {}
_SWEEP_AT = 10_000


def _client_ip(scope) -> str:
    """Best-effort client IP. Behind Railway's proxy the real client is the first
    hop of X-Forwarded-For; fall back to the ASGI peer."""
    for k, v in scope.get("headers") or []:
        if k == b"x-forwarded-for" and v:
            return v.decode("latin1").split(",")[0].strip()
    client = scope.get("client")
    return client[0] if client else "unknown"


def _over_limit(ip: str) -> bool:
    """Fixed-window per-IP counter. Returns True when this request exceeds the cap."""
    now = time.monotonic()
    start, count = _hits.get(ip, (now, 0))
    if now - start >= _window():
        start, count = now, 0
    count += 1
    _hits[ip] = (start, count)
    if len(_hits) > _SWEEP_AT:
        win = _window()
        for stale in [k for k, (s, _) in _hits.items() if now - s >= win]:
            _hits.pop(stale, None)
    return count > _max()


def _guarded(path: str) -> bool:
    if path in _EXCLUDE_EXACT:
        return False
    if any(path.startswith(p) for p in _EXCLUDE_PREFIXES):
        return False
    return path.startswith(_PROTECTED_PREFIX)


def _with_security_headers(message: dict) -> dict:
    existing = list(message.get("headers") or [])
    have = {k.lower() for k, _ in existing}
    existing += [(k, v) for k, v in _SECURITY_HEADERS if k not in have]
    out = dict(message)
    out["headers"] = existing
    return out


class SecurityMiddleware:
    """Pure-ASGI rate limit + security headers. Add via `app.add_middleware(...)`."""

    def __init__(self, app) -> None:  # noqa: ANN001 — ASGI app
        self.app = app

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)

        method = scope.get("method", "GET")
        path = scope.get("path", "")

        # Rate limit before handing to the app. Skip CORS preflight (OPTIONS).
        if _enabled() and method != "OPTIONS" and _guarded(path):
            try:
                if _over_limit(_client_ip(scope)):
                    return await self._too_many(send)
            except Exception as e:  # noqa: BLE001 — fail-open; never break the API
                log.warning("rate-limit check failed (allowing request): %s", e)

        async def send_wrapper(message):
            if message.get("type") == "http.response.start":
                message = _with_security_headers(message)
            await send(message)

        await self.app(scope, receive, send_wrapper)

    @staticmethod
    async def _too_many(send) -> None:
        body = json.dumps({"detail": "rate limit exceeded"}).encode()
        headers = [
            (b"content-type", b"application/json"),
            (b"retry-after", str(int(_window())).encode()),
            *_SECURITY_HEADERS,
        ]
        await send({"type": "http.response.start", "status": 429, "headers": headers})
        await send({"type": "http.response.body", "body": body})
