"""Offline test for W6.4 — WhatsApp template send path. Fully offline: no Twilio
(the real path runs through a stubbed `_make_client` capturing `messages.create`).

Covers: send_whatsapp template vs freeform (mock + real kwargs), empty-body skipped
in template mode but enforced in freeform, send_briefing picks template when
`TWILIO_BRIEF_TEMPLATE_SID` + permalink are present (else freeform / fallback),
the date formatter, and the scheduler passing `permalink` through to send_briefing.

    backend/.venv/bin/python .proposed_changes/W6.4-template-send/scripts/test_W6_4_template.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import types
from pathlib import Path

_HERE = Path(__file__).resolve()
_COLOCATED_BACKEND = _HERE.parents[1] / "backend"          # W6.4 proposal copies
_repo_backend = next(
    (up / "backend" for up in _HERE.parents if (up / "backend" / "db.py").exists()),
    None,
)
if _repo_backend is not None:
    sys.path.insert(0, str(_repo_backend))
sys.path.insert(0, str(_COLOCATED_BACKEND))

import whatsapp as wa  # noqa: E402

_PASS = 0
_FAIL = 0


def check(name: str, cond: bool) -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ✓ {name}")
    else:
        _FAIL += 1
        print(f"  ✗ {name}")


def _set(**env):
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


# Real-path Twilio stub — captures the kwargs passed to messages.create.
CREATE: dict = {}


def _install_fake_client():
    CREATE.clear()

    class _Msgs:
        def create(self, **kwargs):
            CREATE.update(kwargs)
            return types.SimpleNamespace(sid="SMfake", status="queued")

    class _Client:
        messages = _Msgs()

    wa._make_client = lambda sid, token: _Client()  # type: ignore[assignment]


async def main() -> None:
    # ===== send_whatsapp — MOCK mode (no creds needed) =====
    _set(USE_MOCK_WHATSAPP="1")

    r = await wa.send_whatsapp("+15551230000", content_sid="HX1",
                               content_variables={"1": "d", "2": "u"})
    check("mock template: logged, is_mock", r["is_mock"] and r["status"] == "logged")

    r = await wa.send_whatsapp("+15551230000", "hello world")
    check("mock freeform: logged", r["is_mock"] and r["status"] == "logged")

    # empty body is fine in TEMPLATE mode (the template is the content)…
    r = await wa.send_whatsapp("+15551230000", "", content_sid="HX1")
    check("template mode: empty body allowed", r["is_mock"])
    # …but rejected in FREEFORM mode
    try:
        await wa.send_whatsapp("+15551230000", "   ")
        check("freeform empty body raises", False)
    except wa.WhatsAppError as e:
        check("freeform empty body raises", e.code == "whatsapp_empty_body")

    # ===== send_whatsapp — REAL path (stubbed client) =====
    _set(USE_MOCK_WHATSAPP=None, TWILIO_ACCOUNT_SID="ACfake",
         TWILIO_AUTH_TOKEN="tok", TWILIO_WHATSAPP_FROM="+14155238886",
         TWILIO_STATUS_CALLBACK_URL=None)
    check("not in mock mode now", not wa.whatsapp_mock_enabled())

    _install_fake_client()
    await wa.send_whatsapp("+15551230000", content_sid="HXtmpl",
                           content_variables={"1": "9 Jun 2026", "2": "https://x/b/tok"})
    check("real template: content_sid set", CREATE.get("content_sid") == "HXtmpl")
    check("real template: content_variables JSON-encoded",
          CREATE.get("content_variables") == '{"1": "9 Jun 2026", "2": "https://x/b/tok"}')
    check("real template: NO body key", "body" not in CREATE)
    check("real template: from/to wired",
          CREATE.get("from_") == "whatsapp:+14155238886" and CREATE.get("to") == "whatsapp:+15551230000")

    _install_fake_client()
    await wa.send_whatsapp("+15551230000", "plain text brief")
    check("real freeform: body set", CREATE.get("body") == "plain text brief")
    check("real freeform: NO content_sid", "content_sid" not in CREATE)

    # ===== send_briefing — template vs freeform decision =====
    sent: dict = {}

    async def _capture(to, body="", *, content_sid=None, content_variables=None):
        sent.clear()
        sent.update(to=to, body=body, content_sid=content_sid, content_variables=content_variables)
        return {"is_mock": True, "to": to, "sid": "SMx", "status": "logged"}

    wa.send_whatsapp = _capture  # type: ignore[assignment]
    brief = {"text": "the full narrative…", "account_id": "U1", "as_of": "2026-06-09",
             "permalink": "https://app/b/TOKEN"}

    # template configured + permalink present → TEMPLATE
    _set(TWILIO_BRIEF_TEMPLATE_SID="HXbrief")
    out = await wa.send_briefing(brief, "+15551230000")
    check("briefing→template: content_sid used", sent["content_sid"] == "HXbrief")
    # Format-agnostic: {1} is whatever _fmt_as_of produces (operator-customizable),
    # {2} is the permalink. Don't pin a cosmetic date format.
    check("briefing→template: vars {1}=formatted date {2}=permalink",
          sent["content_variables"] == {"1": wa._fmt_as_of("2026-06-09"), "2": "https://app/b/TOKEN"})
    check("briefing→template: no freeform body", not sent["body"])
    check("briefing→template: delivery record carries as_of/account",
          out["as_of"] == "2026-06-09" and out["account_id"] == "U1")

    # template configured but NO permalink → FREEFORM fallback
    out = await wa.send_briefing({**brief, "permalink": None}, "+15551230000")
    check("briefing→fallback (no permalink): freeform body", sent["body"] == "the full narrative…")
    check("briefing→fallback: no content_sid", sent["content_sid"] is None)

    # template NOT configured → FREEFORM
    _set(TWILIO_BRIEF_TEMPLATE_SID=None)
    await wa.send_briefing(brief, "+15551230000")
    check("briefing→no template env: freeform", sent["content_sid"] is None and sent["body"])

    # ===== _fmt_as_of =====
    # Format-agnostic: a valid ISO date is transformed to a non-empty string that
    # still contains the year (whatever strftime format the operator chose).
    _fmt = wa._fmt_as_of("2026-06-09")
    check("fmt date: ISO transformed, year present", bool(_fmt) and "2026" in _fmt)
    check("fmt date: garbage passthrough", wa._fmt_as_of("not-a-date") == "not-a-date")

    # ===== scheduler passes `permalink` through to send_briefing =====
    import scheduler as sch  # noqa: E402

    sch._RETRY_DELAYS = []
    sch._DEDUP = False  # don't let the W6.5 guard skip
    sch._PUBLISH = True

    captured: dict = {}

    async def _list():
        return [{"user_id": "A", "whatsapp_number": "+A", "flex_query_id": "Q", "flex_token": "tokA"}]

    async def _build(token, query_id):
        return {"text": "brief", "account_id": "ACC", "as_of": "2026-06-09"}

    async def _publish(uid, text, **kw):
        return {"permalink": "https://app/b/SCHED"}

    async def _send_briefing(b, to):
        captured.clear(); captured.update(b)
        return {"is_mock": True, "to": to, "sid": "SMz", "status": "logged"}

    async def _log(uid, **kw):
        pass

    sch.connections.list_active_connections_admin = _list      # type: ignore[assignment]
    sch.connections.log_delivery_admin = _log                  # type: ignore[assignment]
    sch.briefing.build_briefing = _build                       # type: ignore[assignment]
    sch.published_briefs.publish_brief = _publish              # type: ignore[assignment]
    sch.whatsapp.send_briefing = _send_briefing                # type: ignore[assignment]

    s = await sch.run_daily_briefings()
    check("scheduler: user sent", s["sent"] == 1)
    check("scheduler: permalink passed to send_briefing", captured.get("permalink") == "https://app/b/SCHED")

    print(f"\n{_PASS} passed, {_FAIL} failed")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())
