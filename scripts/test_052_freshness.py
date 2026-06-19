#!/usr/bin/env python3
"""Offline test for Proposal 052 — brief freshness note (+ cron retune).

Self-contained: temp-applies the proposal's briefing.py over the live file, checks
the freshness-note facts + mock render, then restores it. Also validates the cron
JSON. No network.

Run with the backend venv:
    backend/.venv/bin/python proposed_changes/052-brief-timing-freshness/scripts/test_052_freshness.py
"""
import asyncio
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))

# Deterministic generation instant for the freshness-note assertions.
_NOW = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)


def _local_hhmm(tz_name: str) -> str:
    return _NOW.astimezone(ZoneInfo(tz_name)).strftime("%H:%M")


def _find_repo(start: str) -> str:
    d = start
    while True:
        if os.path.isfile(os.path.join(d, "backend", "news_context.py")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            raise RuntimeError(f"repo root not found above {start}")
        d = parent


REPO = _find_repo(HERE)
BACKEND = os.path.join(REPO, "backend")
# The proposal's own backend dir, derived from THIS test's location (HERE/../backend)
# — robust to the proposal folder name (proposed_changes vs .proposed_changes) and
# to the test being applied into scripts/ (then PROP == live → temp-apply skipped).
PROP = os.path.normpath(os.path.join(HERE, os.pardir, "backend"))
LIVE_BRIEFING = os.path.join(BACKEND, "briefing.py")
PROP_BRIEFING = os.path.join(PROP, "briefing.py")
PROP_CRON = os.path.join(PROP, "railway.cron.json")

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results: list[bool] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{PASS if cond else FAIL}] {name}" + (f"  — {detail}" if detail else ""))
    results.append(bool(cond))


async def run(briefing):
    os.environ.pop("BRIEFING_TZ", None)  # exercise the default
    sess = datetime(2026, 6, 16).strftime("%d %b %Y")  # "16 Jun 2026" (computed, not hardcoded)
    gmt = _NOW.strftime("%d %b %H:%M")                   # "17 Jun 12:00"

    print("\n=== _freshness_note: session date + local & GMT (default tz) ===")
    note = briefing._freshness_note("2026-06-16", _NOW)
    check("note names the US session date", note and sess in note, str(note))
    check("note says end-of-day / not live", note and "end-of-day" in note and "not live/intraday" in note)
    check("note shows GMT generation time", note and f"{gmt} GMT" in note, str(note))
    hk = _local_hhmm("Asia/Hong_Kong")  # 20:00 for _NOW
    check("note shows local (default Asia/Hong_Kong) time", note and hk in note, f"{hk} in {note}")
    check("local differs from GMT (offset applied)", hk != _NOW.strftime("%H:%M"))
    check("no as_of → None", briefing._freshness_note(None, _NOW) is None)

    print("\n=== timezone is configurable (param + BRIEFING_TZ env) ===")
    note_ny = briefing._freshness_note("2026-06-16", _NOW, tz_name="America/New_York")
    ny = _local_hhmm("America/New_York")  # 08:00 for _NOW
    check("param tz changes the local time", note_ny and ny in note_ny and ny != hk, f"{ny} in {note_ny}")
    check("GMT part unchanged across tz", note_ny and f"{gmt} GMT" in note_ny)
    os.environ["BRIEFING_TZ"] = "America/New_York"
    note_env = briefing._freshness_note("2026-06-16", _NOW)
    check("BRIEFING_TZ env drives the local time", note_env and ny in note_env, str(note_env))
    os.environ.pop("BRIEFING_TZ", None)

    print("\n=== compute_brief_facts carries the note (now-injected) ===")
    snap = {"base_currency": "HKD", "as_of": "2026-06-16",
            "positions": [{"symbol": "AAPL", "day_pnl": 705.0}]}
    facts = briefing.compute_brief_facts(snap, {}, now=_NOW)
    fn = facts.get("data_freshness_note")
    check("facts.data_freshness_note set", bool(fn), str(fn))
    check("facts note has local + GMT", fn and hk in fn and f"{gmt} GMT" in fn)

    print("\n=== mock render closes with the note (italic, last) ===")
    text = briefing._render_mock_briefing(facts)
    check("note present in body", fn in text)
    check("note italicised + last line", text.rstrip().endswith("_")
          and text.strip().split("\n\n")[-1].startswith("_"))

    print("\n=== no as_of → no note line ===")
    facts2 = briefing.compute_brief_facts({"base_currency": "HKD",
                                           "positions": [{"symbol": "X", "day_pnl": 1.0}]}, {}, now=_NOW)
    check("facts2 note None", facts2.get("data_freshness_note") is None)
    check("mock render has no italic note", not briefing._render_mock_briefing(facts2).rstrip().endswith("_"))

    print("\n=== cron retuned to the morning-after-US slot (~12:00 UTC) ===")
    cron = json.load(open(PROP_CRON))
    sched = cron.get("deploy", {}).get("cronSchedule")
    check("cronSchedule == '0 12 * * 1-5'", sched == "0 12 * * 1-5", str(sched))


def main() -> int:
    backup = None
    try:
        if os.path.isfile(PROP_BRIEFING) and os.path.abspath(PROP_BRIEFING) != os.path.abspath(LIVE_BRIEFING):
            with open(LIVE_BRIEFING, "rb") as fh:
                backup = fh.read()
            shutil.copyfile(PROP_BRIEFING, LIVE_BRIEFING)
        sys.path.insert(0, BACKEND)
        import briefing  # noqa: E402
        asyncio.run(run(briefing))
    finally:
        if backup is not None:
            with open(LIVE_BRIEFING, "wb") as fh:
                fh.write(backup)

    total, passed = len(results), sum(results)
    print(f"\n{'='*48}\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
