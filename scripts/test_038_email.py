"""Offline test for 038 — email briefing channel (Resend).

Fully offline: never sends, never hits the DB/network. The real Resend path is
never reached (mock mode or the compliance gate stops short); the scheduler's
email leg is exercised with `connections.get_user_email_admin` / `log_delivery_admin`
monkeypatched to async stubs.

    backend/.venv/bin/python scripts/test_038_email.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
# Co-located backend (proposal copy pre-apply, repo copy post-apply) wins for the
# 038 modules; the repo backend (has ibkr_flex.py) is the fallback for the rest.
_COLOCATED_BACKEND = _HERE.parents[1] / "backend"
_repo_backend = next(
    (up / "backend" for up in _HERE.parents if (up / "backend" / "ibkr_flex.py").exists()),
    None,
)
if _repo_backend is not None:
    sys.path.insert(0, str(_repo_backend))
sys.path.insert(0, str(_COLOCATED_BACKEND))

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


def _set(var: str, val: str | None) -> None:
    if val is None:
        os.environ.pop(var, None)
    else:
        os.environ[var] = val


# ---------------------------------------------------------------------------

def test_unsubscribe_tokens() -> None:
    import email_unsubscribe as eu

    print("email_unsubscribe — token sign/verify")
    _set("EMAIL_UNSUBSCRIBE_SECRET", None)
    check("configured() False with no secret", eu.configured() is False)
    check("verify_token returns None with no secret", eu.verify_token("anything") is None)
    try:
        eu.make_token("u1")
        raised = False
    except eu.UnsubscribeError:
        raised = True
    check("make_token raises with no secret", raised)
    check("unsubscribe_url_for None with no secret", eu.unsubscribe_url_for("u1") is None)

    _set("EMAIL_UNSUBSCRIBE_SECRET", "s3cret-aaa")
    uid = "9f4c-user-uuid"
    tok = eu.make_token(uid)
    check("round-trip verify returns the user_id", eu.verify_token(tok) == uid)
    check("tampered token → None", eu.verify_token(tok[:-2] + ("zz" if not tok.endswith("zz") else "aa")) is None)
    check("garbage token → None (no raise)", eu.verify_token("not.a.token") is None)

    # A token minted under a DIFFERENT secret must not verify under the current one.
    _set("EMAIL_UNSUBSCRIBE_SECRET", "other-secret")
    check("token from another secret → None", eu.verify_token(tok) is None)

    _set("EMAIL_UNSUBSCRIBE_SECRET", "s3cret-aaa")
    # URL base: PUBLIC_BACKEND_URL preferred, else PUBLIC_BASE_URL.
    _set("PUBLIC_BACKEND_URL", "https://api.example.com")
    _set("PUBLIC_BASE_URL", "https://front.example.com")
    url = eu.unsubscribe_url_for(uid)
    check("url uses PUBLIC_BACKEND_URL", url.startswith("https://api.example.com/api/email/unsubscribe?token="))
    check("url carries a verifiable token", eu.verify_token(url.split("token=")[1]) == uid)
    _set("PUBLIC_BACKEND_URL", None)
    url2 = eu.unsubscribe_url_for(uid)
    check("falls back to PUBLIC_BASE_URL", url2.startswith("https://front.example.com/api/email/unsubscribe?token="))


async def test_email_delivery() -> None:
    import email_delivery as ed

    print("email_delivery — mock send + helpers")
    _set("USE_MOCK_EMAIL", "1")
    _set("RESEND_API_KEY", None)
    _set("EMAIL_FROM", None)
    check("mock enabled via USE_MOCK_EMAIL", ed.email_mock_enabled() is True)
    check("email_configured False without creds", ed.email_configured() is False)

    # _to_html: escapes HTML and renders *bold*.
    html = ed._to_html("Up *5%* on <NVDA> & more")
    check("_to_html escapes angle brackets", "&lt;NVDA&gt;" in html and "<NVDA>" not in html)
    check("_to_html renders *bold* → <strong>", "<strong>5%</strong>" in html)

    # mock send → logged, is_mock True.
    r = await ed.send_email("a@b.com", "Subj", text="hello")
    check("mock send is_mock True", r["is_mock"] is True and r["status"] == "logged")

    # empty body → EmailError, non-retryable.
    try:
        await ed.send_email("a@b.com", "S", text="   ")
        e1 = None
    except ed.EmailError as e:
        e1 = e
    check("empty body raises email_empty_body (non-retryable)",
          e1 is not None and e1.code == "email_empty_body" and e1.retryable is False)

    # bad recipient → EmailError, non-retryable.
    try:
        await ed.send_email("not-an-email", "S", text="hi")
        e2 = None
    except ed.EmailError as e:
        e2 = e
    check("bad recipient raises email_bad_recipient (non-retryable)",
          e2 is not None and e2.code == "email_bad_recipient" and e2.retryable is False)

    # send_briefing_email (mock) → channel=email, subject + body wiring.
    brief = {"text": "Book at *HK$1.2m*.", "as_of": "2026-06-12",
             "account_id": "U123", "permalink": "https://front.example.com/b/tok"}
    unsub = "https://api.example.com/api/email/unsubscribe?token=xyz"
    rec = await ed.send_briefing_email(brief, "user@x.com", unsubscribe_url=unsub)
    check("send_briefing_email channel=email", rec["channel"] == "email")
    check("send_briefing_email is_mock True (no creds)", rec["is_mock"] is True)

    text, html2 = ed._compose_body(brief, unsubscribe_url=unsub)
    check("body text includes the permalink", "https://front.example.com/b/tok" in text)
    check("body text includes the unsubscribe url", unsub in text)
    check("html includes unsubscribe link", unsub in html2)
    check("subject carries as_of", ed._subject(brief) == "Your portfolio briefing — 2026-06-12")

    # empty brief → raise.
    try:
        await ed.send_briefing_email({"text": ""}, "user@x.com", unsubscribe_url=unsub)
        e3 = None
    except ed.EmailError as e:
        e3 = e
    check("empty brief raises", e3 is not None and e3.code == "email_empty_body")


async def test_scheduler_email_leg() -> None:
    import scheduler
    import connections

    print("scheduler._maybe_email — channel gating (monkeypatched I/O)")

    logged: list[dict] = []

    async def _fake_log(user_id, **kw):
        logged.append({"user_id": user_id, **kw})

    async def _email_ok(user_id):
        return "user@example.com"

    async def _email_none(user_id):
        return None

    connections.log_delivery_admin = _fake_log  # type: ignore[assignment]

    brief = {"text": "Book at *HK$1.2m*.", "as_of": "2026-06-12",
             "account_id": "U123", "permalink": "https://f/b/tok"}

    # 1) email_opt_in off → leg doesn't run.
    _set("EMAIL_BRIEFINGS", "1")
    r0 = await scheduler._maybe_email({"user_id": "u1", "email_opt_in": False}, brief)
    check("email_opt_in off → None (no leg)", r0 is None)

    # 2) opted in, mock, secret set, email resolves → sent + logged channel=email.
    _set("USE_MOCK_EMAIL", "1")
    _set("EMAIL_UNSUBSCRIBE_SECRET", "s3cret-aaa")
    connections.get_user_email_admin = _email_ok  # type: ignore[assignment]
    logged.clear()
    r1 = await scheduler._maybe_email({"user_id": "u2", "email_opt_in": True}, brief)
    check("opted-in mock send → status sent/logged", r1 and r1["status"] in ("sent", "logged"))
    check("opted-in mock send → is_mock True", r1 and r1.get("is_mock") is True)
    check("logged a channel=email row", any(l.get("channel") == "email" for l in logged))

    # 3) opted in, no account email → skipped no_email (logged, not sent).
    connections.get_user_email_admin = _email_none  # type: ignore[assignment]
    logged.clear()
    r2 = await scheduler._maybe_email({"user_id": "u3", "email_opt_in": True}, brief)
    check("no email → skipped/no_email", r2 and r2["status"] == "skipped" and r2["error"] == "no_email")
    check("no_email logged channel=email status=skipped",
          any(l.get("channel") == "email" and l.get("status") == "skipped" for l in logged))

    # 4) real creds present (NOT mock) but no unsubscribe secret → refuse to send.
    _set("USE_MOCK_EMAIL", None)
    _set("RESEND_API_KEY", "re_realish")
    _set("EMAIL_FROM", "briefings@x.com")
    _set("EMAIL_UNSUBSCRIBE_SECRET", None)
    connections.get_user_email_admin = _email_ok  # type: ignore[assignment]
    logged.clear()
    r3 = await scheduler._maybe_email({"user_id": "u4", "email_opt_in": True}, brief)
    check("real send w/o unsubscribe secret → skipped/no_unsubscribe_secret",
          r3 and r3["status"] == "skipped" and r3["error"] == "no_unsubscribe_secret")

    # 5) EMAIL_BRIEFINGS=0 disables the leg even when opted in.
    #    (re-import isn't needed: the gate is read at module import, so emulate by
    #    checking the documented env contract via a fresh value through importlib.)
    import importlib
    _set("EMAIL_BRIEFINGS", "0")
    sched2 = importlib.reload(scheduler)
    sched2.connections.log_delivery_admin = _fake_log  # type: ignore[assignment]
    sched2.connections.get_user_email_admin = _email_ok  # type: ignore[assignment]
    _set("USE_MOCK_EMAIL", "1")
    _set("EMAIL_UNSUBSCRIBE_SECRET", "s3cret-aaa")
    r4 = await sched2._maybe_email({"user_id": "u5", "email_opt_in": True}, brief)
    check("EMAIL_BRIEFINGS=0 → leg disabled (None)", r4 is None)
    _set("EMAIL_BRIEFINGS", "1")
    importlib.reload(scheduler)  # restore for any later import


async def main() -> int:
    test_unsubscribe_tokens()
    await test_email_delivery()
    await test_scheduler_email_leg()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
