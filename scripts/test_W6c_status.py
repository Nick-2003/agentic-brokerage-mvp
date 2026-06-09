"""Offline test for W6.1b status-callback path — POST /api/twilio/status flips
opt_in on an async opted-out delivery (failed/undelivered + opted-out ErrorCode).
Fully offline (FastAPI TestClient + real RequestValidator; DB write stubbed).

    backend/.venv/bin/python proposed_changes/W6.1b-optout/scripts/test_W6c_status.py
"""
from __future__ import annotations

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
os.environ["TWILIO_OPTED_OUT_CODES"] = "21610,63024,63015"

import webhooks  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from twilio.request_validator import RequestValidator  # noqa: E402

# refresh the module's code set from the env we just set
webhooks._OPTED_OUT_CODES = {"21610", "63024", "63015"}

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


FLIPS: list[tuple[str, bool]] = []


async def fake_flip(number, opt_in):  # noqa: ANN001
    FLIPS.append((number, opt_in))
    return 1


webhooks.connections.set_opt_in_by_whatsapp = fake_flip  # type: ignore[assignment]

app = FastAPI()
app.include_router(webhooks.router)
client = TestClient(app)

_URL = "https://test.example.com/api/twilio/status"
_TOKEN = "test_auth_token_123"


def post(params, headers=None):
    return client.post("/api/twilio/status", data=params, headers=headers or {})


def main() -> None:
    os.environ["TWILIO_WEBHOOK_VALIDATE"] = "0"  # dev: skip signature for the logic tests

    # failed + sandbox-not-joined (63015 — what a real STOP produced live) → flip off
    FLIPS.clear()
    r = post({"MessageStatus": "failed", "ErrorCode": "63015", "To": "whatsapp:+85296177054",
              "MessageSid": "SM1"})
    check("failed+63015 → 204", r.status_code == 204)
    check("failed+63015 → opt_in flipped off", FLIPS == [("+85296177054", False)])

    # undelivered + 21610 → flip
    FLIPS.clear()
    post({"MessageStatus": "undelivered", "ErrorCode": "21610", "To": "whatsapp:+1"})
    check("undelivered+21610 → flip", FLIPS == [("+1", False)])

    # delivered → no flip
    FLIPS.clear()
    post({"MessageStatus": "delivered", "To": "whatsapp:+85296177054"})
    check("delivered → no flip", FLIPS == [])

    # sent → no flip
    FLIPS.clear()
    post({"MessageStatus": "sent", "To": "whatsapp:+85296177054"})
    check("sent → no flip", FLIPS == [])

    # failed but a NON-opt-out (transient) code → no flip
    FLIPS.clear()
    post({"MessageStatus": "failed", "ErrorCode": "30001", "To": "whatsapp:+85296177054"})
    check("failed+transient code → no flip", FLIPS == [])

    # failed + opt-out code but no To → no-op
    FLIPS.clear()
    post({"MessageStatus": "failed", "ErrorCode": "63015", "To": ""})
    check("failed+no To → no-op", FLIPS == [])

    # ---- signature validation on ----
    os.environ["TWILIO_WEBHOOK_VALIDATE"] = "1"
    os.environ["TWILIO_AUTH_TOKEN"] = _TOKEN
    os.environ["TWILIO_STATUS_CALLBACK_URL"] = _URL

    FLIPS.clear()
    p = {"MessageStatus": "failed", "ErrorCode": "63015", "To": "whatsapp:+85296177054"}
    sig = RequestValidator(_TOKEN).compute_signature(_URL, p)
    r = post(p, {"X-Twilio-Signature": sig})
    check("valid signature → 204 + flip", r.status_code == 204 and FLIPS == [("+85296177054", False)])

    FLIPS.clear()
    r = post(p, {"X-Twilio-Signature": "wrong"})
    check("bad signature → 403, no flip", r.status_code == 403 and FLIPS == [])


if __name__ == "__main__":
    main()
    print(f"\n{_PASS}/{_PASS + _FAIL} passed")
    sys.exit(1 if _FAIL else 0)
