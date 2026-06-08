"""Offline test for W5 — daily briefing scheduler. Fully offline: the four
collaborators (W4 read/log, W2 build, W3 send) are stubbed; no DB, LLM, or Twilio.

Covers: happy path + delivery logging, per-user failure isolation (build & send),
tokenless/decrypt-failed skip, retry-then-succeed + retry-exhausted, dry-run
(build only, no send/log), and the cost cap.

    backend/.venv/bin/python proposed_changes/W5-scheduler/scripts/test_W5_scheduler.py
"""
from __future__ import annotations

import asyncio
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

import scheduler as sch  # noqa: E402

sch._RETRY_DELAYS = []  # default: no retries / no sleeps (overridden per-scenario)

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


# --- configurable stubs ------------------------------------------------------

STATE: dict = {}


def reset(conns):
    STATE.clear()
    STATE.update(conns=conns, build_calls=0, send_calls=0, logs=[],
                 build_fail_tokens=set(), build_fail_count=0, send_fail_tokens=set())


async def fake_list():
    return STATE["conns"]


async def fake_build(token, query_id):
    STATE["build_calls"] += 1
    if STATE["build_fail_count"] > 0:
        STATE["build_fail_count"] -= 1
        raise RuntimeError("transient build error")
    if token in STATE["build_fail_tokens"]:
        raise RuntimeError("permanent build error")
    return {"text": f"brief for {token}", "account_id": f"ACC-{token}", "as_of": "2026-06-05"}


async def fake_send(brief, to):
    STATE["send_calls"] += 1
    if brief["account_id"].replace("ACC-", "") in STATE["send_fail_tokens"]:
        raise RuntimeError("twilio reject")
    return {"is_mock": False, "to": to, "sid": f"SM-{brief['account_id']}", "status": "queued"}


async def fake_log(user_id, **kw):
    STATE["logs"].append({"user_id": user_id, **kw})


sch.connections.list_active_connections_admin = fake_list   # type: ignore[assignment]
sch.connections.log_delivery_admin = fake_log               # type: ignore[assignment]
sch.briefing.build_briefing = fake_build                    # type: ignore[assignment]
sch.whatsapp.send_briefing = fake_send                      # type: ignore[assignment]


def _conn(uid, token, *, opted_decrypt_ok=True, decrypt_error=None):
    c = {"user_id": uid, "whatsapp_number": f"+{uid}", "flex_query_id": "Q",
         "flex_token": token if opted_decrypt_ok else None}
    if decrypt_error:
        c["decrypt_error"] = decrypt_error
    return c


async def main() -> None:
    # ---- 1. happy path: 2 users sent + logged ----
    reset([_conn("A", "tokA"), _conn("B", "tokB")])
    s = await sch.run_daily_briefings()
    check("happy: total 2", s["total"] == 2)
    check("happy: sent 2", s["sent"] == 2)
    check("happy: 0 failed/skipped", s["failed"] == 0 and s["skipped"] == 0)
    check("happy: 2 sends", STATE["send_calls"] == 2)
    check("happy: 2 delivery logs", len(STATE["logs"]) == 2)
    check("happy: log carries provider sid", STATE["logs"][0]["provider_id"] == "SM-ACC-tokA")
    check("happy: log status queued", STATE["logs"][0]["status"] == "queued")
    check("happy: result has sid + account", s["results"][0]["sid"] == "SM-ACC-tokA")

    # ---- 2. tokenless / decrypt-failed → skipped, never built ----
    reset([_conn("A", "tokA"), _conn("C", None, opted_decrypt_ok=False,
                                     decrypt_error="token_crypto_decrypt_failed")])
    s = await sch.run_daily_briefings()
    check("skip: 1 sent 1 skipped", s["sent"] == 1 and s["skipped"] == 1)
    check("skip: only 1 build (skipped never built)", STATE["build_calls"] == 1)
    skiplog = next(x for x in STATE["logs"] if x["user_id"] == "C")
    check("skip: logged skipped w/ decrypt error",
          skiplog["status"] == "skipped" and skiplog["error"] == "token_crypto_decrypt_failed")

    # ---- 3. build failure isolated to that user ----
    reset([_conn("A", "tokA"), _conn("B", "tokB"), _conn("D", "tokD")])
    STATE["build_fail_tokens"] = {"tokB"}
    s = await sch.run_daily_briefings()
    check("build-fail: 2 sent 1 failed", s["sent"] == 2 and s["failed"] == 1)
    failrow = next(x for x in s["results"] if x["status"] == "failed")
    check("build-fail: failed row is B", failrow["user_id"] == "B")
    check("build-fail: failed delivery logged",
          any(l["user_id"] == "B" and l["status"] == "failed" for l in STATE["logs"]))
    check("build-fail: B never sent", STATE["send_calls"] == 2)

    # ---- 4. send failure → failed + logged ----
    reset([_conn("A", "tokA"), _conn("B", "tokB")])
    STATE["send_fail_tokens"] = {"tokB"}
    s = await sch.run_daily_briefings()
    check("send-fail: 1 sent 1 failed", s["sent"] == 1 and s["failed"] == 1)
    check("send-fail: B failed logged",
          any(l["user_id"] == "B" and l["status"] == "failed" for l in STATE["logs"]))

    # ---- 5. retry then succeed ----
    sch._RETRY_DELAYS = [0, 0]  # two retries, no real sleep
    reset([_conn("A", "tokA")])
    STATE["build_fail_count"] = 2  # fail twice, succeed on the 3rd
    s = await sch.run_daily_briefings()
    check("retry: succeeds after 2 retries", s["sent"] == 1 and s["failed"] == 0)
    check("retry: build attempted 3×", STATE["build_calls"] == 3)

    # ---- 6. retry exhausted → failed ----
    reset([_conn("A", "tokA")])
    STATE["build_fail_count"] = 99  # always fails
    s = await sch.run_daily_briefings()
    check("retry-exhausted: failed", s["failed"] == 1 and s["sent"] == 0)
    check("retry-exhausted: build attempted 3× (1 + 2 retries)", STATE["build_calls"] == 3)
    sch._RETRY_DELAYS = []

    # ---- 7. dry-run: build only, no send, no log ----
    reset([_conn("A", "tokA"), _conn("B", "tokB")])
    s = await sch.run_daily_briefings(dry_run=True)
    check("dry-run: built 2", s["built"] == 2 and s["sent"] == 0)
    check("dry-run: NOTHING sent", STATE["send_calls"] == 0)
    check("dry-run: NOTHING logged", STATE["logs"] == [])
    check("dry-run: result status built + char count",
          s["results"][0]["status"] == "built" and s["results"][0]["chars"] > 0)

    # ---- 8. cost cap ----
    reset([_conn(str(i), f"tok{i}") for i in range(5)])
    s = await sch.run_daily_briefings(max_users=2)
    check("cap: only 2 processed", s["total"] == 2 and s["sent"] == 2)
    check("cap: capped flag set", s["capped"] is True)
    check("cap: only 2 builds", STATE["build_calls"] == 2)


if __name__ == "__main__":
    asyncio.run(main())
    print(f"\n{_PASS}/{_PASS + _FAIL} passed")
    sys.exit(1 if _FAIL else 0)
