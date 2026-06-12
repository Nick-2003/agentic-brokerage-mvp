"""Email delivery for the daily briefing (037) — SendGrid.

A SECOND delivery channel for the W2 brief, alongside WhatsApp (W3). Same hard
rule as `whatsapp.py`: a SYSTEM-SIDE sender, deliberately NOT an agent tool and
NOT in the `tools/` registry (SECURITY threat 1 — the agent has no outbound-comms
tool, so a prompt injection can't exfiltrate the portfolio). The LLM *writes* the
brief (W2); the scheduler (W5) imports this and *sends* it. P7-NOTIFY, email-first.

Provider = **SendGrid** (Twilio's email product — one billing relationship with the
existing WhatsApp stack). Talks to the v3 `mail/send` REST API over **httpx**
(already a core dep), the same httpx+stdlib house style as `fmp_client.py` /
`ibkr_flex.py` — so NO new dependency and NO optional dep group. The provider is
behind a thin seam (`_send_via_provider`); swapping to Resend/Postmark/SES later is
a localized change.

Mock-first, same discipline as every other client:
  • `USE_MOCK_EMAIL=1` or missing SendGrid creds → LOG the message, don't send
    (offline dev + lets W5 run end-to-end without spending / emailing).
  • real path → SendGrid `POST /v3/mail/send`; a non-2xx raises `EmailError`
    (no silent swallow — same rule as `WhatsAppError` / `BriefingError`).

CAN-SPAM / one-click unsubscribe: every briefing email carries a `List-Unsubscribe`
header (+ `List-Unsubscribe-Post` for RFC 8058 one-click) and a visible footer
link, both pointing at the 037 unsubscribe endpoint (`email_unsubscribe.py`). The
scheduler passes the per-user URL in; this module just wires it onto the message.
"""
from __future__ import annotations

import html as _html
import logging
import os
import re
from typing import Any

import httpx

log = logging.getLogger(__name__)

_SENDGRID_URL = "https://api.sendgrid.com/v3/mail/send"
_TIMEOUT = float(os.getenv("EMAIL_HTTP_TIMEOUT", "15"))
# Light email check (mirrors waitlist_api._EMAIL) — avoids the email-validator dep.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Error codes whose retry is pointless (permanent) — the W5 scheduler reads
# `EmailError.retryable` to skip backoff on these.
_NON_RETRYABLE_CODES = {
    "email_bad_recipient", "email_empty_body", "email_auth_failed",
}


class EmailError(Exception):
    """Any email send/validation failure. Surfaced to W5 for retry/logging.

    `retryable` is False for permanent failures (bad address, bad API key) so the
    scheduler doesn't waste its backoff budget re-attempting them.
    """

    def __init__(self, message: str, code: str = "email_send_failed") -> None:
        self.code = code
        self.retryable = code not in _NON_RETRYABLE_CODES
        super().__init__(message)


def _creds() -> tuple[str, str, str] | None:
    """(api_key, from_email, from_name) or None when not real-send-ready."""
    key = os.getenv("SENDGRID_API_KEY", "").strip()
    frm = os.getenv("EMAIL_FROM", "").strip()
    name = os.getenv("EMAIL_FROM_NAME", "Daily Portfolio Briefing").strip()
    bad = any(not v or v.endswith("REPLACE") for v in (key, frm))
    return None if bad else (key, frm, name)


def email_mock_enabled() -> bool:
    """True iff sends should be LOGGED, not delivered.

    Forced by `USE_MOCK_EMAIL=1`, or implied when SendGrid creds are absent (so
    offline dev / a keyless box logs instead of erroring).
    """
    if os.getenv("USE_MOCK_EMAIL") == "1":
        return True
    return _creds() is None


def email_configured() -> bool:
    """True when the real email path can send: SendGrid creds present. (The
    unsubscribe-secret check lives in `email_unsubscribe.configured()`; the
    scheduler requires BOTH before sending a real email — no unsubscribe, no send.)
    """
    return _creds() is not None


def _to_html(text: str) -> str:
    """Render the brief's WhatsApp-style body as minimal, safe HTML.

    The brief uses `*bold*` and `\\n\\n` paragraph breaks (same as the `/b/[token]`
    web page). We HTML-escape FIRST (defence-in-depth, though the body is our own
    generated content), then convert `*…*` → <strong> and newlines → <br>/<p>.
    """
    esc = _html.escape(text or "")
    # `*bold*` → <strong>bold</strong> (non-greedy, single-line spans).
    esc = re.sub(r"\*([^*\n]+)\*", r"<strong>\1</strong>", esc)
    paras = [p.replace("\n", "<br>") for p in esc.split("\n\n") if p.strip()]
    body = "".join(f"<p>{p}</p>" for p in paras)
    return (
        '<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,'
        'sans-serif;font-size:15px;line-height:1.5;color:#1a1a1a;max-width:560px">'
        f"{body}</div>"
    )


def _validate_addr(to: str) -> str:
    addr = (to or "").strip()
    if not _EMAIL.match(addr):
        raise EmailError(f"recipient not a valid email: {to!r}", code="email_bad_recipient")
    return addr


async def send_email(
    to: str,
    subject: str,
    *,
    text: str,
    html: str | None = None,
    list_unsubscribe_url: str | None = None,
) -> dict[str, Any]:
    """Send (or, in mock mode, log) one email. Returns {is_mock, to, sid, status}.

    Raises `EmailError` on a bad recipient, an empty body, or a real-path SendGrid
    failure. SendGrid accepts with **HTTP 202** (empty body); the message id comes
    back in the `X-Message-Id` header.
    """
    addr = _validate_addr(to)
    if not text or not text.strip():
        raise EmailError("refusing to send an empty body", code="email_empty_body")

    headers_extra: dict[str, str] = {}
    if list_unsubscribe_url:
        # RFC 2369 + RFC 8058 one-click. Lets Gmail/Apple Mail show a native
        # "Unsubscribe" control and POST it without the user opening the link.
        headers_extra["List-Unsubscribe"] = f"<{list_unsubscribe_url}>"
        headers_extra["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

    if email_mock_enabled():
        log.info("Email (mock) → %s | subj=%r | unsub=%s\n%s",
                 addr, subject, bool(list_unsubscribe_url), text)
        return {"is_mock": True, "to": addr, "sid": None, "status": "logged"}

    return await _send_via_provider(addr, subject, text, html, headers_extra)


async def _send_via_provider(
    addr: str, subject: str, text: str, html: str | None, headers_extra: dict[str, str]
) -> dict[str, Any]:
    """SendGrid v3 `mail/send`. Isolated so the provider is a single seam to swap."""
    key, from_email, from_name = _creds()  # type: ignore[misc]  — not None when not mock
    content = [{"type": "text/plain", "value": text}]
    if html:
        content.append({"type": "text/html", "value": html})  # text/plain MUST come first
    payload: dict[str, Any] = {
        "personalizations": [{"to": [{"email": addr}]}],
        "from": {"email": from_email, "name": from_name},
        "subject": subject,
        "content": content,
    }
    if headers_extra:
        payload["headers"] = headers_extra
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                _SENDGRID_URL,
                json=payload,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            )
    except Exception as e:  # noqa: BLE001 — transport/timeout → retryable
        raise EmailError(f"SendGrid request failed: {e}") from e

    if resp.status_code in (200, 201, 202):
        return {
            "is_mock": False,
            "to": addr,
            "sid": resp.headers.get("X-Message-Id"),
            "status": "sent",
        }
    # A bad API key is a config error, not a transient one — don't burn retries.
    if resp.status_code in (401, 403):
        raise EmailError(
            f"SendGrid auth rejected ({resp.status_code}) — check SENDGRID_API_KEY / "
            "verified sender (EMAIL_FROM)",
            code="email_auth_failed",
        )
    body = (resp.text or "")[:300]
    raise EmailError(f"SendGrid send failed ({resp.status_code}): {body}")


def _subject(brief: dict) -> str:
    as_of = (brief.get("as_of") or "").strip()
    return f"Your portfolio briefing — {as_of}" if as_of else "Your portfolio briefing"


def _compose_body(brief: dict, *, unsubscribe_url: str | None) -> tuple[str, str]:
    """Build (text, html) from a W2 briefing dict, appending a permalink line (if
    present on the brief) and a plain-language unsubscribe footer."""
    body = (brief.get("text") or "").strip()
    permalink = brief.get("permalink")
    text_parts = [body]
    if permalink:
        text_parts.append(f"📄 View this brief on the web: {permalink}")
    if unsubscribe_url:
        text_parts.append(
            "—\nYou're receiving this because you enabled email briefings. "
            f"Unsubscribe from email: {unsubscribe_url}\n"
            "(This only stops emails; any WhatsApp briefing is unaffected.)"
        )
    text = "\n\n".join(text_parts)

    html_body = _to_html(body)
    tail = ""
    if permalink:
        tail += (
            f'<p style="font-size:13px"><a href="{_html.escape(permalink)}">'
            "View this brief on the web</a></p>"
        )
    if unsubscribe_url:
        tail += (
            '<hr style="border:none;border-top:1px solid #e5e5e5;margin:16px 0">'
            '<p style="font-size:12px;color:#888">You\'re receiving this because you '
            "enabled email briefings. "
            f'<a href="{_html.escape(unsubscribe_url)}">Unsubscribe from email</a>. '
            "This only stops emails; any WhatsApp briefing is unaffected.</p>"
        )
    return text, html_body + tail


async def send_briefing_email(
    brief: dict, to_email: str, *, unsubscribe_url: str | None = None
) -> dict[str, Any]:
    """Convenience for W5: send a W2 briefing dict by email and return a delivery
    record (send result + the brief's account/as_of for the `briefing_deliveries`
    log, with `channel="email"`). Raises `EmailError` if the brief has no text.
    """
    b = brief or {}
    if not (b.get("text") or "").strip():
        raise EmailError("briefing has no text to send", code="email_empty_body")
    text, html = _compose_body(b, unsubscribe_url=unsubscribe_url)
    result = await send_email(
        to_email, _subject(b), text=text, html=html, list_unsubscribe_url=unsubscribe_url
    )
    return {**result, "channel": "email", "account_id": b.get("account_id"), "as_of": b.get("as_of")}
