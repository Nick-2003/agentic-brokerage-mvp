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

# Inbound keywords → opt-in state, lowercased.
#   STOP/START family: Twilio's reserved Advanced-Opt-Out words. Twilio INTERCEPTS
#     these (blocks delivery + auto-replies) and does NOT forward them to this
#     webhook on the Sandbox / a Messaging Service with opt-out on — so they only
#     reach us where opt-out is disabled. Kept for that path.
#   PAUSE/RESUME: NON-reserved → Twilio FORWARDS them, so this is the in-WhatsApp
#     control users can actually trigger today (verified: PAUSE reached the webhook).
# The real reserved STOP is mirrored on the SEND side instead (Twilio swallows the
# keyword but blocks the number) — see whatsapp.send_whatsapp opted-out detection.
_STOP_WORDS = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit", "pause"}
_START_WORDS = {"start", "yes", "unstop", "resume", "unpause", "go"}

_EMPTY_TWIML = "<?xml version='1.0' encoding='UTF-8'?><Response></Response>"

# Delivery-status error codes that mean "recipient opted out / can't receive" →
# flip opt_in off. 63015 = sandbox recipient not joined (what a STOP produces in
# the sandbox — confirmed live); 63024 = WhatsApp not opted in; 21610 = opted out.
# In production drop 63015 (sandbox-only) and rely on 63024/21610. Configurable.
_OPTED_OUT_CODES = {
    c.strip() for c in os.getenv("TWILIO_OPTED_OUT_CODES", "21610,63024,63015").split(",") if c.strip()
}


def _public_url(request: Request, override_env: str) -> str:
    """The exact URL Twilio signed (per endpoint). Behind a proxy (Railway)
    `request.url` has the wrong scheme/host → an explicit override env var wins.
    Each endpoint has its OWN configured URL (inbound vs status callback), so the
    caller names which env var to use."""
    return os.getenv(override_env) or str(request.url)


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
        _public_url(request, "TWILIO_WEBHOOK_URL"), params,
        request.headers.get("X-Twilio-Signature"),
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


@router.post("/api/twilio/status")
async def twilio_status(request: Request) -> Response:
    """Delivery-status callback — THE real STOP mirror for WhatsApp.

    Twilio intercepts the STOP keyword (so the inbound webhook never sees it) but
    blocks the number; the block surfaces ASYNCHRONOUSLY here as a `failed`/
    `undelivered` status with an opted-out `ErrorCode` (e.g. 63015 sandbox-not-
    joined / 63024 / 21610). On those we flip `opt_in` off so the W5 cron stops
    attempting. Wired per-message via `status_callback` on the send (whatsapp.py),
    so no Twilio-console config is needed. Returns 204 (Twilio ignores the body).
    """
    form = await request.form()
    params = {k: str(v) for k, v in form.items()}
    if not validate_twilio_signature(
        _public_url(request, "TWILIO_STATUS_CALLBACK_URL"), params,
        request.headers.get("X-Twilio-Signature"),
    ):
        log.warning("rejected status callback: bad Twilio signature")
        return Response(status_code=403, content="invalid signature")

    status = (params.get("MessageStatus") or "").strip().lower()
    code = str(params.get("ErrorCode") or "").strip()
    to = params.get("To") or ""  # "whatsapp:+E164"
    number = to[len("whatsapp:"):] if to.startswith("whatsapp:") else to

    if status in {"failed", "undelivered"} and code in _OPTED_OUT_CODES and number:
        n = await connections.set_opt_in_by_whatsapp(number, False)
        log.info("delivery %s for %s (code %s) → opted-out %d connection(s)",
                 status, number, code, n)
    # Other statuses (queued/sent/delivered) or non-opt-out failures → no-op.
    return Response(status_code=204)
