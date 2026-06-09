"""Twilio inbound webhook (W6) — honour WhatsApp STOP / START.

WhatsApp + Twilio require honouring an inbound STOP (legal/ethical — see
DECISION_pivot_waitlist.md "Consent + STOP"). Twilio handles the platform-level
STOP (it stops *delivering* to a STOPped number and auto-replies), but we must
ALSO mirror it in our DB so the W5 cron stops *generating/attempting* briefs for
that number and our consent state stays accurate. START re-subscribes.

A SYSTEM webhook, not an agent tool. Twilio POSTs form-encoded params with an
`X-Twilio-Signature` header; we validate it (anti-spoof — otherwise anyone could
POST a STOP for someone else's number) using `TWILIO_AUTH_TOKEN`, then flip
`opt_in` for that number via the service-key admin path (the webhook has no user
JWT — the number is the identity).

Configure in Twilio: Messaging → your WhatsApp sender/Sandbox → "When a message
comes in" → `https://<backend>/api/twilio/inbound` (POST).
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Request, Response

import connections

log = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])

# Inbound keywords → opt-in state. (Twilio's own STOP set, lowercased.)
_STOP_WORDS = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit"}
_START_WORDS = {"start", "yes", "unstop", "resume"}

_EMPTY_TWIML = "<?xml version='1.0' encoding='UTF-8'?><Response></Response>"


def _public_url(request: Request) -> str:
    """The exact URL Twilio signed. Behind a proxy (Railway) `request.url` can have
    the wrong scheme/host, breaking validation — so an explicit `TWILIO_WEBHOOK_URL`
    override wins when set."""
    return os.getenv("TWILIO_WEBHOOK_URL") or str(request.url)


def validate_twilio_signature(url: str, params: dict[str, str], signature: str | None) -> bool:
    """True if the request is a genuine Twilio call (or validation is disabled).

    Skipped when `TWILIO_WEBHOOK_VALIDATE=0` or no real `TWILIO_AUTH_TOKEN`
    (local dev) — documented in `.env.example`. In production both are set, so a
    spoofed unsubscribe is rejected.
    """
    token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    if os.getenv("TWILIO_WEBHOOK_VALIDATE") == "0" or not token or token.endswith("REPLACE"):
        return True
    from twilio.request_validator import RequestValidator

    return RequestValidator(token).validate(url, params, signature or "")


@router.post("/api/twilio/inbound")
async def twilio_inbound(request: Request) -> Response:
    form = await request.form()
    params = {k: str(v) for k, v in form.items()}
    if not validate_twilio_signature(
        _public_url(request), params, request.headers.get("X-Twilio-Signature")
    ):
        log.warning("rejected inbound webhook: bad Twilio signature")
        return Response(status_code=403, content="invalid signature")

    body = (params.get("Body") or "").strip().lower()
    frm = params.get("From") or ""  # "whatsapp:+E164"
    number = frm[len("whatsapp:"):] if frm.startswith("whatsapp:") else frm

    # Always 200 with empty TwiML — Twilio handles the user-facing STOP/HELP reply.
    twiml = Response(content=_EMPTY_TWIML, media_type="application/xml")
    if not number:
        return twiml

    if body in _STOP_WORDS:
        n = await connections.set_opt_in_by_whatsapp(number, False)
        log.info("inbound STOP from %s → opted-out %d connection(s)", number, n)
    elif body in _START_WORDS:
        n = await connections.set_opt_in_by_whatsapp(number, True)
        log.info("inbound START from %s → opted-in %d connection(s)", number, n)
    # else: HELP / freeform — ignore (Twilio auto-replies to HELP); no state change.
    return twiml
