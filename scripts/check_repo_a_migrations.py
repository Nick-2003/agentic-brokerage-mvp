#!/usr/bin/env python3
"""Enforce Repo A migration ownership and the declared migration floor."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "supabase" / "migrations"
BASE_REF = "a1d3846ec2299369263a45b2671ed17ac580cfc1"
FROZEN_HEAD = "20260806071948_sanitize_broker_account_display_names.sql"


def main() -> int:
    files = sorted(path.name for path in MIGRATIONS.glob("*.sql"))
    errors: list[str] = []
    if FROZEN_HEAD not in files:
        errors.append(f"frozen migration head is missing: {FROZEN_HEAD}")
    if files and files[-1] < FROZEN_HEAD:
        errors.append(f"migration history regressed to {files[-1]}")

    diff = subprocess.run(
        ["git", "diff", "--name-status", f"{BASE_REF}...HEAD", "--", "supabase/migrations"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    for line in diff:
        status, *_paths = line.split("\t")
        if status.startswith(("D", "M", "R")):
            errors.append(f"existing migration changed or removed: {line}")

    if errors:
        print("Repo A migration freeze violation(s):", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"Repo A migration ownership: PASS (floor {FROZEN_HEAD})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
