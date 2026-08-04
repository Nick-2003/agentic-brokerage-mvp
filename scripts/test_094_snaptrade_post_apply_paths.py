#!/usr/bin/env python3
"""Offline guard for proposal 094; does not import the application."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TEST_FILE = Path(__file__).resolve()
ROOT = TEST_FILE.parents[3]
PROPOSAL = ROOT / ".proposed_changes/094-post-apply-snaptrade-verification-paths"
PATCH = PROPOSAL / "scripts/snaptrade_post_apply_paths.patch"

source = PATCH.read_text()

assert "BACKEND_UNDER_TEST = PROPOSED_BACKEND if IN_PROPOSAL else LIVE_BACKEND" in source
assert '(BACKEND_UNDER_TEST / "snaptrade_client.py").read_text()' in source
assert 'ROOT = TEST_FILE.parents[3] if IN_PROPOSAL else TEST_FILE.parents[1]' in source
assert 'FRONTEND = PROPOSAL / "frontend" if IN_PROPOSAL else ROOT / "frontend"' in source
assert 'analytics_source = (FRONTEND / "lib/analytics.ts").read_text()' in source
assert 'connect_source = (FRONTEND / "app/connect/page.tsx").read_text()' in source
assert 'next_source = (FRONTEND / "next.config.js").read_text()' in source

# Confirm both location formulas against paths with the same nesting as the repo.
staged = ROOT / ".proposed_changes/094-post-apply-snaptrade-verification-paths/scripts/test.py"
live = ROOT / "scripts/test.py"
assert staged.parents[3] == ROOT
assert live.parents[1] == ROOT

# Apply the proposal to disposable copies and execute both corrected tests from
# their post-apply location. Symlinks expose read-only application inputs without
# copying or modifying the live backend/frontend trees.
with tempfile.TemporaryDirectory(prefix="snaptrade-094-") as temporary:
    sandbox = Path(temporary)
    scripts = sandbox / "scripts"
    scripts.mkdir()
    for name in (
        "test_089_snaptrade_client_routes.py",
        "test_092_snaptrade_connection_ui.py",
    ):
        shutil.copy2(ROOT / "scripts" / name, scripts / name)

    subprocess.run(["git", "init", "--quiet", str(sandbox)], check=True)
    check = subprocess.run(
        ["git", "apply", "--check", str(PATCH)],
        cwd=sandbox,
        capture_output=True,
        text=True,
    )
    if check.returncode == 0:
        subprocess.run(["git", "apply", str(PATCH)], cwd=sandbox, check=True)
    else:
        # Keep the guard repeatable after 094 itself has been applied.
        reverse_check = subprocess.run(
            ["git", "apply", "--check", "--reverse", str(PATCH)],
            cwd=sandbox,
            capture_output=True,
            text=True,
        )
        assert reverse_check.returncode == 0, check.stderr

    (sandbox / "backend").symlink_to(ROOT / "backend", target_is_directory=True)
    (sandbox / "frontend").symlink_to(ROOT / "frontend", target_is_directory=True)
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    for name, expected in (
        ("test_089_snaptrade_client_routes.py", "089 snaptrade client/routes: PASS"),
        ("test_092_snaptrade_connection_ui.py", "092 snaptrade connection UI: PASS"),
    ):
        completed = subprocess.run(
            [sys.executable, str(scripts / name)],
            cwd=sandbox,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        assert expected in completed.stdout

print("094 snaptrade post-apply verification paths: PASS")
