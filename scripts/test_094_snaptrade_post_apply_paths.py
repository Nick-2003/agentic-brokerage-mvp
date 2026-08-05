#!/usr/bin/env python3
"""Verify applied SnapTrade tests without archived-proposal dependencies."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

TEST_FILE = Path(__file__).resolve()
IN_PROPOSAL = ".proposed_changes" in TEST_FILE.parts
ROOT = TEST_FILE.parents[3] if IN_PROPOSAL else TEST_FILE.parents[1]
LIVE_SCRIPTS = ROOT / "scripts"
PROPOSED_SCRIPTS = TEST_FILE.parent if IN_PROPOSAL else LIVE_SCRIPTS


def script_path(name: str) -> Path:
    """Use a replacement staged beside this test, otherwise the applied script."""
    proposed = PROPOSED_SCRIPTS / name
    return proposed if IN_PROPOSAL and proposed.exists() else LIVE_SCRIPTS / name


test_089 = script_path("test_089_snaptrade_client_routes.py").read_text()
test_092 = script_path("test_092_snaptrade_connection_ui.py").read_text()
test_101 = script_path("test_101_snaptrade_callback_single_processing.py").read_text()

# Guard the original 091/094 path failure in every applied script. Tests staged in
# a later proposal may override individual live tests without requiring copies of
# unrelated historical proposals.
root_selector = "ROOT = TEST_FILE.parents[3] if IN_PROPOSAL else TEST_FILE.parents[1]"
assert root_selector in test_089
assert root_selector in test_092
assert root_selector in test_101
assert "BACKEND_UNDER_TEST = PROPOSED_BACKEND if IN_PROPOSAL else LIVE_BACKEND" in test_089

# 102 makes 092 an applied-state contract test. It deliberately reads the live
# frontend both before and after application instead of reopening archived 092.
assert 'FRONTEND = ROOT / "frontend"' in test_092
assert 'analytics_source = (FRONTEND / "lib/analytics.ts").read_text()' in test_092
assert 'connect_source = (FRONTEND / "app/connect/page.tsx").read_text()' in test_092
assert 'next_source = (FRONTEND / "next.config.js").read_text()' in test_092
assert 'callback.count("verifySnapTradeConnection(token,") == 1' in test_092

# 101 must inspect its staged callback while staged and the live callback after
# application. This prevents the path-above-repository failure that prompted 102.
assert "CALLBACK = (" in test_101
assert "if IN_PROPOSAL" in test_101
assert 'else ROOT / "frontend/app/settings/brokerage/snaptrade/callback/page.tsx"' in test_101

environment = os.environ.copy()
environment["PYTHONDONTWRITEBYTECODE"] = "1"
for name, expected in (
    ("test_089_snaptrade_client_routes.py", "089 snaptrade client/routes: PASS"),
    ("test_092_snaptrade_connection_ui.py", "092 snaptrade connection UI: PASS"),
    (
        "test_101_snaptrade_callback_single_processing.py",
        "101 snaptrade callback single processing: PASS",
    ),
):
    completed = subprocess.run(
        [sys.executable, str(script_path(name))],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert expected in completed.stdout

print("094 snaptrade post-apply verification paths: PASS")
