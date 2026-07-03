#!/usr/bin/env python3
"""Offline guard for Proposal 061 — freshness note in the in-app morning_brief.

061 surfaces 052's "figures are end-of-day / generated at" note in the chat
`morning_brief` widget. The note logic moves to a shared `freshness` module;
`get_portfolio` returns a ready-made `freshness_note` the agent copies verbatim.

Covers:
  A. `freshness.freshness_note` — session date + local & GMT wording; None as_of.
  B. `briefing._freshness_note` still delegates to it (052 parity — same output;
     `briefing._briefing_tz_name` still resolves).
  C. `tools.portfolio._map_ibkr_snapshot` puts a real `freshness_note` on a
     connected book; `_nil_portfolio` leaves it None.
  D. Prompt contract: widget_contract.md + system.md instruct copying it verbatim
     into `as_of_note`.

Self-contained: temp-applies the proposal's backend/{freshness.py [new],
briefing.py, tools/portfolio.py, prompts/widget_contract.md, prompts/system.md}
over live, imports, asserts, restores (deletes the new file) in a finally.
Anchored on backend/auth.py (not in 061's mirror).

Run with the backend venv:
    backend/.venv/bin/python scripts/test_061_brief_freshness_note.py
"""
import os
import shutil
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
# Deterministic generation instant: 02 Jul 2026 12:01 UTC → 20:01 HKT.
_NOW = datetime(2026, 7, 2, 12, 1, tzinfo=timezone.utc)


def _find_repo(start: str) -> str:
    d = start
    while True:
        if os.path.isfile(os.path.join(d, "backend", "auth.py")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            raise RuntimeError(f"could not locate repo root (backend/auth.py) above {start}")
        d = parent


REPO = _find_repo(HERE)
BACKEND = os.path.join(REPO, "backend")
PROP = os.path.join(REPO, ".proposed_changes", "061-app-brief-freshness-note")
FILES = [
    (os.path.join(BACKEND, "freshness.py"), os.path.join(PROP, "backend", "freshness.py")),
    (os.path.join(BACKEND, "briefing.py"), os.path.join(PROP, "backend", "briefing.py")),
    (os.path.join(BACKEND, "tools", "portfolio.py"),
     os.path.join(PROP, "backend", "tools", "portfolio.py")),
    (os.path.join(BACKEND, "prompts", "widget_contract.md"),
     os.path.join(PROP, "backend", "prompts", "widget_contract.md")),
    (os.path.join(BACKEND, "prompts", "system.md"),
     os.path.join(PROP, "backend", "prompts", "system.md")),
]

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results: list[bool] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{PASS if cond else FAIL}] {name}" + (f"  — {detail}" if detail else ""))
    results.append(bool(cond))


def test_freshness_module() -> None:
    print("\n=== A. freshness.freshness_note ===")
    import freshness  # noqa: E402 — after temp-apply

    note = freshness.freshness_note("2026-07-01", _NOW, tz_name="Asia/Hong_Kong")
    check("has the session date", note is not None and "Wed 01 Jul 2026" in note, str(note))
    check("says end-of-day / not live", "end-of-day" in note and "not live/intraday" in note)
    check("shows local HKT time", "20:01 HKT" in note, str(note))
    check("shows GMT time", "12:01 GMT" in note, str(note))
    check("None as_of → None", freshness.freshness_note(None, _NOW) is None)
    ny = freshness.freshness_note("2026-07-01", _NOW, tz_name="America/New_York")
    check("respects tz override", ny is not None and "EDT" in ny, str(ny))


def test_briefing_parity() -> None:
    print("\n=== B. briefing._freshness_note delegates (052 parity) ===")
    import briefing  # noqa: E402
    import freshness  # noqa: E402

    b = briefing._freshness_note("2026-07-01", _NOW, tz_name="Asia/Hong_Kong")
    f = freshness.freshness_note("2026-07-01", _NOW, tz_name="Asia/Hong_Kong")
    check("briefing wrapper == shared output", b == f and b is not None, str(b))
    check("briefing._freshness_note(None) → None", briefing._freshness_note(None, _NOW) is None)
    check("briefing._briefing_tz_name resolves", isinstance(briefing._briefing_tz_name(), str))


def test_portfolio_field() -> None:
    print("\n=== C. get_portfolio result carries freshness_note ===")
    import tools.portfolio as portfolio  # noqa: E402

    snap = {
        "base_currency": "HKD",
        "nav": {"total": 869000.0},
        "change_in_nav": {"starting": 869000.0, "ending": 860327.0},
        "positions": [],
        "as_of": "2026-07-01",
        "account_id": "U123",
        "is_mock": True,
    }
    shaped = portfolio._map_ibkr_snapshot(snap)
    note = shaped.get("freshness_note")
    check("connected book has a freshness_note", bool(note), str(note))
    check("note names the session date", note is not None and "Wed 01 Jul 2026" in note)
    check("note has a Generated … GMT stamp", note is not None and "Generated" in note and "GMT" in note)
    check("as_of still present", shaped.get("as_of") == "2026-07-01")

    nil = portfolio._nil_portfolio()
    check("nil portfolio → freshness_note None", nil.get("freshness_note") is None)
    check("nil portfolio still has the key (shape parity)", "freshness_note" in nil)


def test_prompt_contract() -> None:
    print("\n=== D. prompt contract mentions as_of_note ===")
    wc = open(os.path.join(BACKEND, "prompts", "widget_contract.md"), encoding="utf-8").read()
    sysmd = open(os.path.join(BACKEND, "prompts", "system.md"), encoding="utf-8").read()
    check("widget_contract has as_of_note", "as_of_note" in wc)
    check("widget_contract says copy verbatim", "verbatim" in wc.lower() and "freshness_note" in wc)
    check("system.md has as_of_note guidance", "as_of_note" in sysmd and "freshness_note" in sysmd)


def main() -> int:
    backups: list[tuple[str, str, bool]] = []
    try:
        for live, prop in FILES:
            if not os.path.isfile(prop):
                print(f"missing proposal file: {prop}")
                return 1
            existed = os.path.isfile(live)
            bak = live + ".061bak"
            if existed:
                shutil.copy2(live, bak)
            backups.append((live, bak, existed))
            shutil.copy2(prop, live)

        if BACKEND not in sys.path:
            sys.path.insert(0, BACKEND)

        test_freshness_module()
        test_briefing_parity()
        test_portfolio_field()
        test_prompt_contract()
    finally:
        for live, bak, existed in backups:
            if existed:
                shutil.copy2(bak, live)
                os.remove(bak)
            elif os.path.isfile(live):
                os.remove(live)  # new freshness.py — remove

    total, passed = len(results), sum(results)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
