"""Offline test for W6.1 — Twilio inbound STOP/START webhook. Fully offline:
the DB write is stubbed; signatures are computed with the real Twilio validator.

    backend/.venv/bin/python scripts/test_W6_webhook.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_COLOCATED_BACKEND = _HERE.parents[1] / "backend"          # webhooks.py, connections.py
_repo_backend = next(
    (up / "backend" for up in _HERE.parents if (up / "backend" / "db.py").exists()),
    None,
)
if _repo_backend is not None:
    sys.path.insert(0, str(_repo_backend))
sys.path.insert(0, str(_COLOCATED_BACKEND))

# Dummy Supabase config so connections.py imports cleanly (the write is stubbed).
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import connections as conn  # noqa: E402
import webhooks  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from twilio.request_validator import RequestValidator  # noqa: E402

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


# Stub the DB write — record (number, opt_in) calls.
CALLS: list[tuple[str, bool]] = []


async def fake_set_opt_in_by_whatsapp(number: str, opt_in: bool) -> int:
    CALLS.append((number, opt_in))
    return 1


conn.set_opt_in_by_whatsapp = fake_set_opt_in_by_whatsapp  # type: ignore[assignment]
webhooks.connections.set_opt_in_by_whatsapp = fake_set_opt_in_by_whatsapp  # type: ignore[assignment]

app = FastAPI()
app.include_router(webhooks.router)
client = TestClient(app)

_URL = "https://test.example.com/api/twilio/inbound"
_TOKEN = "test_auth_token_123"


def _signed_headers(params: dict[str, str]) -> dict[str, str]:
    return {"X-Twilio-Signature": RequestValidator(_TOKEN).compute_signature(_URL, params)}


def post(params, headers=None):
    return client.post("/api/twilio/inbound", data=params, headers=headers or {})


def main() -> None:
    # ---- validation disabled (local dev) ----
    os.environ["TWILIO_WEBHOOK_VALIDATE"] = "0"
    os.environ.pop("TWILIO_AUTH_TOKEN", None)
    CALLS.clear()
    r = post({"From": "whatsapp:+85291234567", "Body": "STOP"})
    check("dev (no validate): 200", r.status_code == 200)
    check("dev: empty TwiML body", "<Response>" in r.text)
    check("dev: STOP → opt_in False for the number", CALLS == [("+85291234567", False)])

    CALLS.clear()
    post({"From": "whatsapp:+85291234567", "Body": "start"})
    check("dev: START (any case) → opt_in True", CALLS == [("+85291234567", True)])

    CALLS.clear()
    post({"From": "whatsapp:+85291234567", "Body": "hello there"})
    check("dev: unknown body → no opt_in change", CALLS == [])

    # W6.1b — PAUSE/RESUME (the keywords Twilio actually forwards to the webhook).
    CALLS.clear()
    post({"From": "whatsapp:+85291234567", "Body": "PAUSE"})
    check("dev: PAUSE → opt_in False", CALLS == [("+85291234567", False)])
    CALLS.clear()
    post({"From": "whatsapp:+85291234567", "Body": "Resume"})
    check("dev: RESUME (any case) → opt_in True", CALLS == [("+85291234567", True)])

    CALLS.clear()
    post({"From": "", "Body": "STOP"})
    check("dev: no From → no-op", CALLS == [])

    # ---- validation ON: real signature required ----
    os.environ["TWILIO_WEBHOOK_VALIDATE"] = "1"
    os.environ["TWILIO_AUTH_TOKEN"] = _TOKEN
    os.environ["TWILIO_WEBHOOK_URL"] = _URL  # fixed URL so the signature matches

    CALLS.clear()
    p = {"From": "whatsapp:+85291234567", "Body": "STOP"}
    r = post(p, _signed_headers(p))
    check("validate: valid signature → 200 + flip", r.status_code == 200 and CALLS == [("+85291234567", False)])

    CALLS.clear()
    r = post(p, {"X-Twilio-Signature": "obviously-wrong"})
    check("validate: bad signature → 403", r.status_code == 403)
    check("validate: bad signature → no DB write", CALLS == [])

    CALLS.clear()
    r = post(p, {})  # missing signature header
    check("validate: missing signature → 403", r.status_code == 403)

    # tampered params (signature computed for a different number) → reject
    CALLS.clear()
    good = _signed_headers({"From": "whatsapp:+85291234567", "Body": "STOP"})
    r = post({"From": "whatsapp:+85299999999", "Body": "STOP"}, good)
    check("validate: tampered params → 403", r.status_code == 403 and CALLS == [])

    # ---- validate.helper directly ----
    os.environ["TWILIO_WEBHOOK_VALIDATE"] = "1"
    params = {"From": "whatsapp:+1", "Body": "STOP"}
    sig = RequestValidator(_TOKEN).compute_signature(_URL, params)
    check("helper: valid sig True", webhooks.validate_twilio_signature(_URL, params, sig) is True)
    check("helper: wrong sig False", webhooks.validate_twilio_signature(_URL, params, "x") is False)
    os.environ["TWILIO_WEBHOOK_VALIDATE"] = "0"
    check("helper: disabled → True regardless", webhooks.validate_twilio_signature(_URL, params, "x") is True)


if __name__ == "__main__":
    main()
    print(f"\n{_PASS}/{_PASS + _FAIL} passed")
    sys.exit(1 if _FAIL else 0)
