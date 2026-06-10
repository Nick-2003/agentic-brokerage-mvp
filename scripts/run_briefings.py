"""W5 entrypoint — run the daily briefing job (cron target / manual run).

    # SAFE dry run (build every brief, send NOTHING, log NOTHING):
    backend/.venv/bin/python scripts/run_briefings.py --dry-run

    # fully offline dry run (no spend, no real broker), e.g. against the mock fixture:
    USE_MOCK_IBKR=1 USE_MOCK_BRIEFING=1 USE_MOCK_WHATSAPP=1 \\
        backend/.venv/bin/python scripts/run_briefings.py --dry-run

    # REAL run (fetch + Claude + WhatsApp send + delivery log), capped:
    backend/.venv/bin/python scripts/run_briefings.py --max-users 5

For a daily cron: Railway cron service / a scheduled worker invoking this exact
command once each morning. (The deploy wiring is P6 — see the W5 README.)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_COLOCATED_BACKEND = _HERE.parents[1] / "backend"          # scheduler.py
_repo_backend = next(
    (up / "backend" for up in _HERE.parents if (up / "backend" / "db.py").exists()),
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

import scheduler  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser(description="Run the daily WhatsApp briefing job.")
    ap.add_argument("--dry-run", action="store_true", help="build briefs but DON'T send or log")
    ap.add_argument("--max-users", type=int, default=None, help="cap users this run (cost ceiling)")
    ap.add_argument("--force", action="store_true",
                    help="bypass the W6.5 resend-window guard (intentional re-send / manual test)")
    args = ap.parse_args()

    try:
        summary = await scheduler.run_daily_briefings(
            dry_run=args.dry_run, max_users=args.max_users, force=args.force
        )
    except Exception as e:  # noqa: BLE001 — couldn't even read the connection list
        print(f"run aborted: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    print(json.dumps(summary, indent=2, default=str))
    head = {k: summary[k] for k in ("total", "sent", "built", "skipped", "failed", "capped", "dry_run")}
    print(f"\n{head}", file=sys.stderr)
    # Non-zero exit if anyone failed, so a cron/monitor can alert.
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
