#!/usr/bin/env python3
"""Verify applied SnapTrade test paths without proposal-folder dependencies."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

TEST_FILE = Path(__file__).resolve()
IN_PROPOSAL = ".proposed_changes" in TEST_FILE.parts
ROOT = TEST_FILE.parents[3] if IN_PROPOSAL else TEST_FILE.parents[1]
LIVE_SCRIPTS = ROOT / "scripts"

test_089 = (LIVE_SCRIPTS / "test_089_snaptrade_client_routes.py").read_text()
test_092 = (LIVE_SCRIPTS / "test_092_snaptrade_connection_ui.py").read_text()

# These assertions guard the original 091/094 failure mode directly in the
# applied scripts. They do not require the historical proposal patches to exist.
root_selector = "ROOT = TEST_FILE.parents[3] if IN_PROPOSAL else TEST_FILE.parents[1]"
assert root_selector in test_089
assert root_selector in test_092
assert "BACKEND_UNDER_TEST = PROPOSED_BACKEND if IN_PROPOSAL else LIVE_BACKEND" in test_089
assert 'FRONTEND = PROPOSAL / "frontend" if IN_PROPOSAL else ROOT / "frontend"' in test_092
assert 'analytics_source = (FRONTEND / "lib/analytics.ts").read_text()' in test_092
assert 'connect_source = (FRONTEND / "app/connect/page.tsx").read_text()' in test_092
assert 'next_source = (FRONTEND / "next.config.js").read_text()' in test_092

environment = os.environ.copy()
environment["PYTHONDONTWRITEBYTECODE"] = "1"
for name, expected in (
    ("test_089_snaptrade_client_routes.py", "089 snaptrade client/routes: PASS"),
    ("test_092_snaptrade_connection_ui.py", "092 snaptrade connection UI: PASS"),
):
    completed = subprocess.run(
        [sys.executable, str(LIVE_SCRIPTS / name)],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert expected in completed.stdout

print("094 snaptrade post-apply verification paths: PASS")
