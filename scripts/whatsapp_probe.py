"""Live probe for W3 — WhatsApp delivery. The first-real-run "can we deliver?" tool.

Sends one WhatsApp message and prints the result. Honours the same switches as the app:
  • USE_MOCK_WHATSAPP=1 (or missing TWILIO_* creds) → LOGS the body, sends nothing.
  • unset + real creds (and `uv sync --group whatsapp`) → real Twilio send.

The recipient must have joined your Twilio Sandbox first (send the "join <phrase>"
code once — see .self_management/TWILIO_SETUP.md).

    # log-only smoke (no creds needed):
    USE_MOCK_WHATSAPP=1 backend/.venv/bin/python scripts/whatsapp_probe.py +85291234567

    # real send of a short test body (creds in backend/.env, group installed):
    backend/.venv/bin/python scripts/whatsapp_probe.py +85291234567

    # real send of the actual daily brief (builds W2 over live/mock IBKR):
    backend/.venv/bin/python scripts/whatsapp_probe.py +85291234567 --brief
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_COLOCATED_BACKEND = _HERE.parents[1] / "backend"
_repo_backend = next(
    (up / "backend" for up in _HERE.parents if (up / "backend" / "ibkr_flex.py").exists()),
    None,
)
if _repo_backend is not None:
    sys.path.insert(0, str(_repo_backend))
    try:
        from dotenv import load_dotenv

        load_dotenv(_repo_backend / ".env")
    except Exception:
        pass
sys.path.insert(0, str(_COLOCATED_BACKEND))

import whatsapp as wa  # noqa: E402


async def main() -> None:
    args = [a for a in sys.argv[1:]]
    use_brief = "--brief" in args
    args = [a for a in args if a != "--brief"]
    if not args:
        print("usage: whatsapp_probe.py <+E164 recipient> [body] [--brief]")
        sys.exit(2)
    to = args[0]
    print(f"mode: {'mock(log)' if wa.whatsapp_mock_enabled() else 'live(Twilio send)'}")

    try:
        if use_brief:
            import briefing  # noqa: PLC0415 — optional, only when --brief

            brief = await briefing.build_briefing()
            print(f"built brief: account={brief['account_id']} as_of={brief['as_of']} "
                  f"({len(brief['text'])} chars)")
            rec = await wa.send_briefing(brief, to)
        else:
            body = args[1] if len(args) > 1 else "✅ W3 test — your daily portfolio brief will arrive here."
            rec = await wa.send_whatsapp(to, body)
    except wa.WhatsAppError as e:
        print(f"\nSend failed [{e.code}]: {e}")
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        print(f"\nFailed: {type(e).__name__}: {e}")
        sys.exit(1)

    print(f"\nresult: is_mock={rec['is_mock']}  to={rec['to']}  sid={rec.get('sid')}  "
          f"status={rec.get('status')}")
    if rec["is_mock"]:
        print("(mock mode — nothing sent; the body was logged. Set creds + unset "
              "USE_MOCK_WHATSAPP for a real send.)")


if __name__ == "__main__":
    asyncio.run(main())
