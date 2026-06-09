"""WhatsApp delivery for the daily briefing (W3) — Twilio.

A SYSTEM-SIDE sender (like `ibkr_flex.py` / `briefing.py` / `news_context.py`),
deliberately NOT an agent tool and NOT in the `tools/` registry. This is THE
hard rule of the pivot (SECURITY threat 1): the agent has no outbound-comms tool,
so a prompt injection can't exfiltrate the portfolio. The LLM *writes* the brief
(W2); the scheduler (W5) imports this and *sends* it.

Mock-first, same discipline as every other client:
  • `USE_MOCK_WHATSAPP=1` or missing Twilio creds → LOG the message body, don't
    send (offline dev + lets W5's scheduler be exercised end-to-end without
    spending/spamming). `twilio` is never imported on this path, so the offline
    test + a keyless dev box work without the package installed.
  • real path → Twilio `messages.create`; failure raises `WhatsAppError` (no
    silent swallow — same rule as `IBKRFlexError` / `BriefingError`).

Phase: this targets the Twilio **Sandbox** (freeform text to opted-in numbers —
fine for the validation cohort and a ~700-char narrative). The production path
(registered WhatsApp Business sender + approved templates for business-initiated
sends outside the 24h window) is a W6 task; see `self_management/TWILIO_SETUP.md`.

`twilio` is an OPTIONAL dep group (`uv sync --group whatsapp`) — mirrors the
`memory` group so the default install / chat backend stays lean.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

log = logging.getLogger(__name__)

# Twilio caps a single WhatsApp message body at 1600 chars; the briefing is
# already bounded (BRIEFING_MAX_CHARS, default 1500), so this is just a guard.
_MAX_BODY = 1600
_E164 = re.compile(r"^\+\d{6,15}$")

# Twilio error codes that mean "recipient has opted out / is blocked" — the user
# texted STOP (Twilio intercepts the keyword + blocks the number, so we learn of
# it HERE on the send, not via the inbound webhook). 21610 = opted-out across
# channels; 63024 = WhatsApp recipient can't receive. Confirm/extend on the first
# real opted-out send (verify-against-live-dependency). Configurable.
_OPTED_OUT_CODES = {
    c.strip() for c in os.getenv("TWILIO_OPTED_OUT_CODES", "21610,63024,63015").split(",") if c.strip()
}
# Error codes whose retry is pointless (permanent) — the W5 scheduler reads
# `WhatsAppError.retryable` to skip backoff on these.
_NON_RETRYABLE_CODES = {
    "whatsapp_bad_recipient", "whatsapp_empty_body", "whatsapp_body_too_long",
    "whatsapp_twilio_not_installed", "whatsapp_recipient_opted_out",
}


class WhatsAppError(Exception):
    """Any WhatsApp send/validation failure. Surfaced to W5 for retry/logging.

    `retryable` is False for permanent failures (bad number, opted-out, …) so the
    scheduler doesn't waste its backoff budget re-attempting them.
    """

    def __init__(self, message: str, code: str = "whatsapp_send_failed") -> None:
        self.code = code
        self.retryable = code not in _NON_RETRYABLE_CODES
        super().__init__(message)


def _creds() -> tuple[str, str, str] | None:
    sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
    tok = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    frm = os.getenv("TWILIO_WHATSAPP_FROM", "").strip()
    bad = any(not v or v.endswith("REPLACE") for v in (sid, tok, frm))
    return None if bad else (sid, tok, frm)


def whatsapp_mock_enabled() -> bool:
    """True iff sends should be LOGGED, not delivered.

    Forced by `USE_MOCK_WHATSAPP=1`, or implied when Twilio creds are absent (so
    offline dev / a keyless box logs instead of erroring).
    """
    if os.getenv("USE_MOCK_WHATSAPP") == "1":
        return True
    return _creds() is None


def _normalize_addr(number: str) -> str:
    """Return a Twilio WhatsApp address `whatsapp:+<E164>`.

    Accepts a bare E.164 (`+14155238886`) or an already-prefixed
    `whatsapp:+14155238886` (idempotent). Raises `WhatsAppError` on anything that
    isn't valid E.164 — better to fail loudly than to send to a malformed number.
    """
    n = (number or "").strip()
    addr = n[len("whatsapp:"):] if n.startswith("whatsapp:") else n
    if not _E164.match(addr):
        raise WhatsAppError(f"recipient not E.164: {number!r}", code="whatsapp_bad_recipient")
    return f"whatsapp:{addr}"


def _make_client(sid: str, token: str):  # noqa: ANN202 — twilio Client (lazy import)
    """Construct a Twilio REST client. Isolated so tests can monkeypatch it
    without installing `twilio` (the real path lazy-imports it here)."""
    from twilio.rest import Client

    return Client(sid, token)


async def send_whatsapp(to: str, body: str) -> dict[str, Any]:
    """Send (or, in mock mode, log) one WhatsApp message.

    Returns {is_mock, to, sid, status}. Raises `WhatsAppError` on a bad recipient,
    empty body, or a real-path Twilio failure. The Twilio SDK is synchronous, so
    the real send runs in a worker thread (`asyncio.to_thread`) to keep W5's async
    loop unblocked.
    """
    if not body or not body.strip():
        raise WhatsAppError("refusing to send an empty body", code="whatsapp_empty_body")
    to_addr = _normalize_addr(to)
    if len(body) > _MAX_BODY:
        # Don't silently truncate financial content mid-number; fail loud so W5
        # logs it. (The briefing is capped well under this upstream.)
        raise WhatsAppError(
            f"body {len(body)} chars exceeds WhatsApp's {_MAX_BODY} limit",
            code="whatsapp_body_too_long",
        )

    if whatsapp_mock_enabled():
        log.info("WhatsApp (mock) → %s:\n%s", to_addr, body)
        return {"is_mock": True, "to": to_addr, "sid": None, "status": "logged"}

    sid, token, frm = _creds()  # type: ignore[misc]  — not None when not mock
    from_addr = _normalize_addr(frm)
    try:
        def _send():  # runs in a thread
            kwargs: dict[str, Any] = {"from_": from_addr, "to": to_addr, "body": body}
            # Wire the delivery-status callback so an async opted-out/blocked result
            # (e.g. WhatsApp 63015/63024 — Twilio swallows the STOP keyword but blocks
            # the number) reaches POST /api/twilio/status, which flips opt_in off.
            cb = os.getenv("TWILIO_STATUS_CALLBACK_URL", "").strip()
            if cb and not cb.endswith("REPLACE"):
                kwargs["status_callback"] = cb
            return _make_client(sid, token).messages.create(**kwargs)

        msg = await asyncio.to_thread(_send)
    except WhatsAppError:
        raise
    except ImportError as e:
        # Creds are set (we're past the mock gate) but the optional `twilio`
        # group isn't installed — distinguish this from a Twilio-side failure so
        # the operator gets the actionable fix, not a buried ModuleNotFoundError.
        raise WhatsAppError(
            "twilio not installed — run `uv sync --group whatsapp` (or set "
            "USE_MOCK_WHATSAPP=1 to log instead of send)",
            code="whatsapp_twilio_not_installed",
        ) from e
    except Exception as e:  # noqa: BLE001 — wrap any Twilio/transport error
        # Recipient opted out (texted STOP — Twilio intercepted the keyword + now
        # blocks the number). This is how we LEARN of a STOP (the webhook never
        # sees it). Distinct, non-retryable code so W5 flips opt_in instead of
        # retrying. Match by Twilio error code or message text (defensive).
        code = str(getattr(e, "code", "") or "")
        low = str(e).lower()
        if code in _OPTED_OUT_CODES or "opted out" in low or "unsubscrib" in low:
            raise WhatsAppError(
                f"recipient opted out: {e}", code="whatsapp_recipient_opted_out"
            ) from e
        raise WhatsAppError(f"Twilio send failed: {e}") from e
    return {
        "is_mock": False,
        "to": to_addr,
        "sid": getattr(msg, "sid", None),
        "status": getattr(msg, "status", None),
    }


async def send_briefing(briefing: dict, to: str) -> dict[str, Any]:
    """Convenience for W5: send a W2 briefing dict's `text` and return a delivery
    record (send result + the brief's account/as_of for the `briefing_deliveries`
    log). Raises `WhatsAppError` if the brief has no text."""
    text = (briefing or {}).get("text") or ""
    if not text.strip():
        raise WhatsAppError("briefing has no text to send", code="whatsapp_empty_body")
    result = await send_whatsapp(to, text)
    return {**result, "account_id": briefing.get("account_id"), "as_of": briefing.get("as_of")}
