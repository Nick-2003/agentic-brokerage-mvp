"""Offline test for W3 — WhatsApp delivery. Fully offline; does NOT require the
`twilio` package (the real path is reached only via a stubbed `_make_client`).

    backend/.venv/bin/python proposed_changes/W3-whatsapp-delivery/scripts/test_W3_whatsapp.py
    # (or, once applied:  backend/.venv/bin/python scripts/test_W3_whatsapp.py)
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
# Co-located backend (proposal copy pre-apply, repo copy post-apply) wins for
# `import whatsapp`; the repo backend (has ibkr_flex.py) is the fallback location.
_COLOCATED_BACKEND = _HERE.parents[1] / "backend"
_repo_backend = next(
    (up / "backend" for up in _HERE.parents if (up / "backend" / "ibkr_flex.py").exists()),
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


def _env(mock=None, sid=None, tok=None, frm=None) -> None:
    for var, val in [
        ("USE_MOCK_WHATSAPP", mock), ("TWILIO_ACCOUNT_SID", sid),
        ("TWILIO_AUTH_TOKEN", tok), ("TWILIO_WHATSAPP_FROM", frm),
    ]:
        if val is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = val


# A fake Twilio client so the real path needs no `twilio` package + makes no call.
class _FakeMsg:
    sid = "SM_test_123"
    status = "queued"


class _FakeMessages:
    def create(self, **kwargs):  # noqa: ANN003
        _FakeMessages.last = kwargs
        return _FakeMsg()


class _FakeClient:
    def __init__(self, sid, token):  # noqa: ANN001
        _FakeClient.init_args = (sid, token)
        self.messages = _FakeMessages()


async def main() -> None:
    # ---- mock-gate truth table ----
    _env(mock="1", sid="AC_x", tok="tok", frm="whatsapp:+14155238886")
    check("gate: USE_MOCK_WHATSAPP=1 → True", wa.whatsapp_mock_enabled() is True)
    _env(sid="AC_x", tok="tok", frm="whatsapp:+14155238886")
    check("gate: creds present, not forced → False", wa.whatsapp_mock_enabled() is False)
    _env(sid="AC_REPLACE", tok="tok", frm="whatsapp:+14155238886")
    check("gate: placeholder creds → True", wa.whatsapp_mock_enabled() is True)
    _env()  # nothing
    check("gate: no creds → True", wa.whatsapp_mock_enabled() is True)

    # ---- _normalize_addr ----
    check("addr: bare E164 → whatsapp:+", wa._normalize_addr("+14155238886") == "whatsapp:+14155238886")
    check("addr: already-prefixed idempotent",
          wa._normalize_addr("whatsapp:+14155238886") == "whatsapp:+14155238886")
    check("addr: strips surrounding space", wa._normalize_addr("  +85291234567 ") == "whatsapp:+85291234567")
    for bad in ("", "14155238886", "+12", "not-a-number", "whatsapp:1555"):
        try:
            wa._normalize_addr(bad)
            check(f"addr: invalid {bad!r} raises", False)
        except wa.WhatsAppError as e:
            check(f"addr: invalid {bad!r} → whatsapp_bad_recipient", e.code == "whatsapp_bad_recipient")

    # ---- mock send (no twilio import, no network) ----
    _env(mock="1")
    r = await wa.send_whatsapp("+14155238886", "hello brief")
    check("mock send: is_mock True", r["is_mock"] is True)
    check("mock send: status logged", r["status"] == "logged")
    check("mock send: to normalised", r["to"] == "whatsapp:+14155238886")
    check("mock send: no sid", r["sid"] is None)

    # ---- empty body rejected (mock or not) ----
    for body in ("", "   "):
        try:
            await wa.send_whatsapp("+14155238886", body)
            check(f"send: empty body {body!r} raises", False)
        except wa.WhatsAppError as e:
            check(f"send: empty body {body!r} → whatsapp_empty_body", e.code == "whatsapp_empty_body")

    # ---- over-length body rejected ----
    try:
        await wa.send_whatsapp("+14155238886", "x" * (wa._MAX_BODY + 1))
        check("send: over-length raises", False)
    except wa.WhatsAppError as e:
        check("send: over-length → whatsapp_body_too_long", e.code == "whatsapp_body_too_long")

    # ---- REAL path with a stubbed client (creds present, mock OFF, no twilio dep) ----
    _env(sid="AC_real", tok="tok_real", frm="whatsapp:+14155238886")
    wa._make_client = lambda sid, token: _FakeClient(sid, token)  # type: ignore[assignment]
    r = await wa.send_whatsapp("+85291234567", "real brief body")
    check("real send: is_mock False", r["is_mock"] is False)
    check("real send: returns sid", r["sid"] == "SM_test_123")
    check("real send: returns status", r["status"] == "queued")
    check("real send: client built with creds", _FakeClient.init_args == ("AC_real", "tok_real"))
    check("real send: from_ is sandbox addr", _FakeMessages.last["from_"] == "whatsapp:+14155238886")
    check("real send: to normalised", _FakeMessages.last["to"] == "whatsapp:+85291234567")
    check("real send: body passed through", _FakeMessages.last["body"] == "real brief body")

    # ---- real send Twilio failure → WhatsAppError ----
    class _BoomMessages:
        def create(self, **kwargs):  # noqa: ANN003
            raise RuntimeError("21608 number not opted in")

    class _BoomClient:
        def __init__(self, sid, token):  # noqa: ANN001
            self.messages = _BoomMessages()

    wa._make_client = lambda sid, token: _BoomClient(sid, token)  # type: ignore[assignment]
    try:
        await wa.send_whatsapp("+85291234567", "body")
        check("real send: failure raises", False)
    except wa.WhatsAppError as e:
        check("real send: failure → whatsapp_send_failed", e.code == "whatsapp_send_failed")

    # ---- twilio not installed → distinct, actionable error ----
    def _raise_import(sid, token):  # noqa: ANN001
        raise ModuleNotFoundError("No module named 'twilio'")

    wa._make_client = _raise_import  # type: ignore[assignment]
    try:
        await wa.send_whatsapp("+85291234567", "body")
        check("real send: missing twilio raises", False)
    except wa.WhatsAppError as e:
        check("real send: missing twilio → whatsapp_twilio_not_installed",
              e.code == "whatsapp_twilio_not_installed")

    # ---- send_briefing convenience ----
    wa._make_client = lambda sid, token: _FakeClient(sid, token)  # type: ignore[assignment]
    rec = await wa.send_briefing(
        {"text": "📉 your book…", "account_id": "U123", "as_of": "2026-06-05"}, "+85291234567"
    )
    check("send_briefing: sent the brief text", _FakeMessages.last["body"] == "📉 your book…")
    check("send_briefing: carries account_id for log", rec["account_id"] == "U123")
    check("send_briefing: carries as_of for log", rec["as_of"] == "2026-06-05")
    try:
        await wa.send_briefing({"text": "", "account_id": "U123"}, "+85291234567")
        check("send_briefing: empty text raises", False)
    except wa.WhatsAppError as e:
        check("send_briefing: empty text → whatsapp_empty_body", e.code == "whatsapp_empty_body")

    _env()  # clean up


if __name__ == "__main__":
    asyncio.run(main())
    print(f"\n{_PASS}/{_PASS + _FAIL} passed")
    sys.exit(1 if _FAIL else 0)
