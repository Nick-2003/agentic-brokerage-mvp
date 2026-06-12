"""Offline test for 034 — per-user daily LLM token budget. Pure in-memory; no DB,
no network. Drives `token_budget` directly with a controlled day + env.

    backend/.venv/bin/python proposed_changes/034-chat-token-budget/scripts/test_034_token_budget.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_COLOCATED_BACKEND = _HERE.parents[1] / "backend"          # the 034 proposal copy
_repo_backend = next(
    (up / "backend" for up in _HERE.parents if (up / "backend" / "db.py").exists()),
    None,
)
if _repo_backend is not None:
    sys.path.insert(0, str(_repo_backend))
sys.path.insert(0, str(_COLOCATED_BACKEND))

import token_budget as tb  # noqa: E402

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


DAY = ["2026-06-11"]  # mutable so we can roll the day
tb._today = lambda: DAY[0]  # type: ignore[assignment]


def reset(budget: str | None):
    tb._usage.clear()
    DAY[0] = "2026-06-11"
    if budget is None:
        os.environ.pop("CHAT_DAILY_TOKEN_BUDGET", None)
    else:
        os.environ["CHAT_DAILY_TOKEN_BUDGET"] = budget


def main() -> None:
    U, V = "user-A", "user-B"

    # ---- 1. disabled (no budget) → always under, record is a no-op ----
    reset(None)
    check("disabled: enabled() False", not tb.token_budget_enabled())
    tb.record(U, 999999, 999999)
    check("disabled: over_budget False even after huge record", not tb.over_budget(U))
    check("disabled: nothing tracked", U not in tb._usage)

    # ---- 2. enabled: accumulate to the cap ----
    reset("1000")
    check("enabled: enabled() True", tb.token_budget_enabled())
    check("enabled: fresh user under", not tb.over_budget(U))
    tb.record(U, 600, 0)
    check("enabled: 600 < 1000 → under", not tb.over_budget(U))
    tb.record(U, 0, 500)               # → 1100 total
    check("enabled: 1100 ≥ 1000 → over", tb.over_budget(U))

    # ---- 3. per-user isolation ----
    check("isolation: V independent of U → under", not tb.over_budget(V))
    tb.record(V, 1000, 0)
    check("isolation: V now over, U still over", tb.over_budget(V) and tb.over_budget(U))

    # ---- 4. day rollover resets ----
    DAY[0] = "2026-06-12"
    check("rollover: U under again on the new day", not tb.over_budget(U))
    tb.record(U, 200, 0)
    day, used = tb._usage[U]
    check("rollover: counter restarted (200, not 1300)", day == "2026-06-12" and used == 200)

    # ---- 5. edge cases ----
    reset("1000")
    tb.record("", 5000, 5000)
    check("empty user: not tracked", "" not in tb._usage)
    check("empty user: over_budget False", not tb.over_budget(""))
    tb.record(U, 0, 0)
    check("zero-delta record: no-op", U not in tb._usage)
    tb.record(U, -50, -50)
    check("negative tokens clamped to 0 → no-op", U not in tb._usage)

    # ---- 6. boundary: exactly at cap is 'over' (>=) ----
    reset("100")
    tb.record(U, 100, 0)
    check("exact cap (100 == 100) → over", tb.over_budget(U))

    print(f"\n{_PASS} passed, {_FAIL} failed")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
