"""Offline test for W6.5 — per-user cost-cap idempotency. Fully offline: the
collaborators (W4 read/log, W2 build, W3 send) and the new dedup probe
(`connections.already_delivered_since_admin`) are stubbed; no DB, LLM, or Twilio.

Covers: a user briefed within the window is SKIPPED before building (no IBKR
fetch / Claude / send); a fresh user sends normally; dry-run ignores the guard
(builds for testing); --force bypasses it; BRIEFING_DEDUP=0 disables it; and the
probe failing is FAIL-OPEN (send proceeds). Also asserts the new connections
query is wired (awaitable, present).

    backend/.venv/bin/python proposed_changes/W6.5-cost-caps/scripts/test_W6_5_cost_caps.py
"""
from __future__ import annotations

import asyncio
import inspect
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_COLOCATED_BACKEND = _HERE.parents[1] / "backend"          # the W6.5 proposal copies
_repo_backend = next(
    (up / "backend" for up in _HERE.parents if (up / "backend" / "db.py").exists()),
    None,
)
if _repo_backend is not None:
    sys.path.insert(0, str(_repo_backend))                 # db/briefing/whatsapp/published_briefs
sys.path.insert(0, str(_COLOCATED_BACKEND))                # scheduler + connections (proposal, win)

import connections as conns_mod  # noqa: E402  — the proposal copy (has the new query)
import scheduler as sch          # noqa: E402

# Capture the REAL query before the stub override below, so the wiring check
# (#7) asserts the proposal's function, not the test stub.
_REAL_ALREADY = conns_mod.already_delivered_since_admin

sch._RETRY_DELAYS = []   # no retries / no sleeps
sch._PUBLISH = False     # skip the best-effort permalink publish in tests

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


STATE: dict = {}


def reset(*, already_set: set | None = None, probe_raises: bool = False):
    STATE.clear()
    STATE.update(build_calls=0, send_calls=0, logs=[],
                 already=already_set or set(), probe_raises=probe_raises, probe_calls=0)


async def fake_list():
    # two opted-in, decryptable users every run
    return [
        {"user_id": "A", "whatsapp_number": "+A", "flex_query_id": "Q", "flex_token": "tokA"},
        {"user_id": "B", "whatsapp_number": "+B", "flex_query_id": "Q", "flex_token": "tokB"},
    ]


async def fake_build(token, query_id):
    STATE["build_calls"] += 1
    return {"text": f"brief {token}", "account_id": f"ACC-{token}", "as_of": "2026-06-09"}


async def fake_send(brief, to):
    STATE["send_calls"] += 1
    return {"is_mock": False, "to": to, "sid": f"SM-{brief['account_id']}", "status": "queued"}


async def fake_log(user_id, **kw):
    STATE["logs"].append({"user_id": user_id, **kw})


async def fake_already(user_id, *, since_iso):
    STATE["probe_calls"] += 1
    if STATE["probe_raises"]:
        raise RuntimeError("dedup probe DB error")
    return user_id in STATE["already"]


sch.connections.list_active_connections_admin = fake_list      # type: ignore[assignment]
sch.connections.log_delivery_admin = fake_log                  # type: ignore[assignment]
sch.connections.already_delivered_since_admin = fake_already   # type: ignore[assignment]
sch.briefing.build_briefing = fake_build                       # type: ignore[assignment]
sch.whatsapp.send_briefing = fake_send                         # type: ignore[assignment]


async def main() -> None:
    # ---- 1. dedup ON: A already briefed within window → A skipped, B sent ----
    sch._DEDUP = True
    reset(already_set={"A"})
    s = await sch.run_daily_briefings()
    check("dedup: 1 sent 1 skipped", s["sent"] == 1 and s["skipped"] == 1)
    check("dedup: skipped user never built (cost saved)", STATE["build_calls"] == 1)
    check("dedup: only the fresh user sent", STATE["send_calls"] == 1)
    arow = next(r for r in s["results"] if r["user_id"] == "A")
    check("dedup: A result skipped/already_sent_recently",
          arow["status"] == "skipped" and arow["error"] == "already_sent_recently")
    check("dedup: A logged skipped w/ reason",
          any(l["user_id"] == "A" and l["status"] == "skipped"
              and l["error"] == "already_sent_recently" for l in STATE["logs"]))
    check("dedup: probe consulted per user", STATE["probe_calls"] == 2)

    # ---- 2. dedup ON, none recently briefed → both send ----
    reset(already_set=set())
    s = await sch.run_daily_briefings()
    check("fresh: both sent", s["sent"] == 2 and s["skipped"] == 0)
    check("fresh: both built+sent", STATE["build_calls"] == 2 and STATE["send_calls"] == 2)

    # ---- 3. dry-run IGNORES the guard (build for testing, no send/log) ----
    reset(already_set={"A", "B"})
    s = await sch.run_daily_briefings(dry_run=True)
    check("dry-run: ignores dedup, builds both", STATE["build_calls"] == 2)
    check("dry-run: nothing sent/logged", STATE["send_calls"] == 0 and STATE["logs"] == [])
    check("dry-run: probe not consulted", STATE["probe_calls"] == 0)

    # ---- 4. --force bypasses the guard (send even though already briefed) ----
    reset(already_set={"A", "B"})
    s = await sch.run_daily_briefings(force=True)
    check("force: both sent despite recent briefs", s["sent"] == 2 and s["skipped"] == 0)
    check("force: probe not consulted", STATE["probe_calls"] == 0)

    # ---- 5. BRIEFING_DEDUP=0 disables the guard entirely ----
    sch._DEDUP = False
    reset(already_set={"A", "B"})
    s = await sch.run_daily_briefings()
    check("disabled: both sent", s["sent"] == 2)
    check("disabled: probe never consulted", STATE["probe_calls"] == 0)
    sch._DEDUP = True

    # ---- 6. probe error is FAIL-OPEN (send proceeds, never blocks a brief) ----
    reset(already_set={"A", "B"}, probe_raises=True)
    s = await sch.run_daily_briefings()
    check("fail-open: both sent despite probe error", s["sent"] == 2 and s["skipped"] == 0)

    # ---- 7. the new connections query exists + is async (real, pre-stub) ----
    check("wiring: already_delivered_since_admin present + async",
          inspect.iscoroutinefunction(_REAL_ALREADY))

    print(f"\n{_PASS} passed, {_FAIL} failed")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())
