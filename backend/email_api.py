"""Public email-unsubscribe endpoint (038).

`/api/email/unsubscribe?token=…` — turns OFF a user's email briefings. No auth (it's
clicked from an inbox); the signed token (`email_unsubscribe.py`) IS the capability
and identifies the user. Two methods, both idempotent:

  • GET  — a human clicking the footer link → flips off + returns a tiny HTML page.
  • POST — RFC 8058 List-Unsubscribe one-click (Gmail/Apple Mail POST automatically
           via the `List-Unsubscribe-Post` header) → flips off + returns 200.

Flips ONLY `email_opt_in` (service key) — a WhatsApp briefing, if enabled, is
unaffected (channels unsubscribe independently). An invalid/garbage token returns a
neutral page/200 (no enumeration signal, and we never want an unsubscribe to error).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

import connections
import email_unsubscribe

log = logging.getLogger(__name__)

router = APIRouter(tags=["email"])

_PAGE = (
    "<!doctype html><html><head><meta charset='utf-8'>"
    "<meta name='viewport' content='width=device-width,initial-scale=1'>"
    "<meta name='robots' content='noindex'><title>Email briefings</title></head>"
    "<body style='font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;"
    "max-width:520px;margin:48px auto;padding:0 20px;color:#1a1a1a;line-height:1.5'>"
    "<h2>{heading}</h2><p>{body}</p></body></html>"
)


async def _unsubscribe(token: str) -> bool:
    """Verify the token and flip email_opt_in off. Returns True iff a valid token
    resolved to a user (whether or not a row was updated — idempotent)."""
    user_id = email_unsubscribe.verify_token(token)
    if not user_id:
        return False
    try:
        await connections.set_email_opt_in_admin(user_id, False)
    except Exception as e:  # noqa: BLE001 — never let an unsubscribe error out
        log.warning("email unsubscribe flip failed for %s: %s", user_id, e)
    return True


@router.get("/api/email/unsubscribe", response_class=HTMLResponse)
async def unsubscribe_get(token: str = "") -> HTMLResponse:
    ok = await _unsubscribe(token)
    if ok:
        html = _PAGE.format(
            heading="You're unsubscribed",
            body="You will no longer receive email briefings. Any WhatsApp briefing "
                 "is unaffected. You can re-enable email anytime from the connect page.",
        )
    else:
        html = _PAGE.format(
            heading="Link not recognised",
            body="This unsubscribe link is invalid or expired. You can manage email "
                 "briefings from the connect page.",
        )
    return HTMLResponse(content=html, status_code=200)


@router.post("/api/email/unsubscribe")
async def unsubscribe_post(request: Request, token: str = "") -> Response:
    # RFC 8058 one-click: the token rides the query string; the POST body is
    # `List-Unsubscribe=One-Click` (ignored). Always 200 so the mail client is happy.
    if not token:
        token = (request.query_params.get("token") or "").strip()
    await _unsubscribe(token)
    return Response(status_code=200)
