"""Offline test for W6.1b — send-time opt-out detection (whatsapp) + scheduler
mirroring (flip opt_in, no retry, count skipped). Fully offline; stubs the Twilio
client and the W4 admin write.

    backend/.venv/bin/python proposed_changes/W6.1b-optout/scripts/test_W6b_optout.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_COLOCATED_BACKEND = _HERE.parents[1] / "backend"
_repo_backend = next(
    (up / "backend" for up in _HERE.parents if (up / "backend" / "db.py").exists()),
    None,
)
if _repo_backend is not None:
    sys.path.insert(0, str(_repo_backend))
sys.path.insert(0, str(_COLOCATED_BACKEND))

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import scheduler as sch  # noqa: E402
import whatsapp as wa  # noqa: E402

sch._RETRY_DELAYS = [0, 0]  # retries enabled (to prove opted-out does NOT retry)

_PASS = 0
_FAIL = 0


def check(name, cond):  # noqa: ANN001
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ✓ {name}")
    else:
        _FAIL += 1
        print(f"  ✗ {name}")


async def main() -> None:
    # ---- whatsapp.send_whatsapp: classify a Twilio opted-out error ----
    os.environ["TWILIO_WEBHOOK_VALIDATE"] = "0"
    os.environ.update(TWILIO_ACCOUNT_SID="AC_x", TWILIO_AUTH_TOKEN="tok",
                      TWILIO_WHATSAPP_FROM="whatsapp:+14155238886")
    os.environ.pop("USE_MOCK_WHATSAPP", None)

    class _OptOutErr(Exception):
        code = 21610  # Twilio "recipient opted out"

    class _Msgs:
        def create(self, **kw):  # noqa: ANN003
            raise _OptOutErr("Message blocked: recipient opted out")

    class _Client:
        messages = _Msgs()

    wa._make_client = lambda sid, tok: _Client()  # type: ignore[assignment]
    try:
        await wa.send_whatsapp("+85291234567", "brief body")
        check("whatsapp: opted-out send raises", False)
    except wa.WhatsAppError as e:
        check("whatsapp: code whatsapp_recipient_opted_out", e.code == "whatsapp_recipient_opted_out")
        check("whatsapp: opted-out is NOT retryable", e.retryable is False)

    # match by message text when the code isn't in the set
    class _MsgErr(Exception):
        code = 99999

    class _Msgs2:
        def create(self, **kw):  # noqa: ANN003
            raise _MsgErr("The recipient has unsubscribed from messages")

    wa._make_client = lambda sid, tok: type("C", (), {"messages": _Msgs2()})()  # type: ignore[assignment]
    try:
        await wa.send_whatsapp("+85291234567", "b")
        check("whatsapp: text-match opted-out raises", False)
    except wa.WhatsAppError as e:
        check("whatsapp: 'unsubscribed' text → opted_out", e.code == "whatsapp_recipient_opted_out")

    # a generic error stays a retryable send failure
    class _Boom:
        def create(self, **kw):  # noqa: ANN003
            raise RuntimeError("503 service unavailable")

    wa._make_client = lambda sid, tok: type("C", (), {"messages": _Boom()})()  # type: ignore[assignment]
    try:
        await wa.send_whatsapp("+85291234567", "b")
        check("whatsapp: generic send raises", False)
    except wa.WhatsAppError as e:
        check("whatsapp: generic → send_failed + retryable",
              e.code == "whatsapp_send_failed" and e.retryable is True)

    # ---- scheduler: opted-out → flip opt_in, count skipped, NO retry ----
    flips: list[tuple[str, bool]] = []
    logs: list[dict] = []
    sends = {"n": 0}

    async def fake_list():
        return [{"user_id": "A", "whatsapp_number": "+85291234567",
                 "flex_token": "tokA", "flex_query_id": "Q"}]

    async def fake_build(token, qid):  # noqa: ANN001
        return {"text": "b", "account_id": "ACC", "as_of": "2026-06-05"}

    async def fake_send(brief, to):  # noqa: ANN001
        sends["n"] += 1
        raise wa.WhatsAppError("recipient opted out", code="whatsapp_recipient_opted_out")

    async def fake_flip(num, opt_in):  # noqa: ANN001
        flips.append((num, opt_in))
        return 1

    async def fake_log(uid, **kw):  # noqa: ANN001
        logs.append({"user_id": uid, **kw})

    sch.connections.list_active_connections_admin = fake_list      # type: ignore[assignment]
    sch.connections.set_opt_in_by_whatsapp = fake_flip             # type: ignore[assignment]
    sch.connections.log_delivery_admin = fake_log                  # type: ignore[assignment]
    sch.briefing.build_briefing = fake_build                       # type: ignore[assignment]
    sch.whatsapp.send_briefing = fake_send                         # type: ignore[assignment]

    s = await sch.run_daily_briefings()
    check("scheduler: opted-out counted skipped (not failed)", s["skipped"] == 1 and s["failed"] == 0)
    check("scheduler: result status opted_out", s["results"][0]["status"] == "opted_out")
    check("scheduler: opt_in flipped off", flips == [("+85291234567", False)])
    check("scheduler: logged skipped/recipient_opted_out",
          any(l["status"] == "skipped" and l.get("error") == "recipient_opted_out" for l in logs))
    check("scheduler: send attempted ONCE (no retry on non-retryable)", sends["n"] == 1)


if __name__ == "__main__":
    asyncio.run(main())
    print(f"\n{_PASS}/{_PASS + _FAIL} passed")
    sys.exit(1 if _FAIL else 0)
