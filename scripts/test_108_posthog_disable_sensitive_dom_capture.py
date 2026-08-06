#!/usr/bin/env python3
"""Contract guard for staged or applied Proposal 108."""
from pathlib import Path


TEST_FILE = Path(__file__).resolve()
IN_PROPOSAL = ".proposed_changes" in TEST_FILE.parts
ROOT = TEST_FILE.parents[3] if IN_PROPOSAL else TEST_FILE.parents[1]
PROPOSAL = ROOT / ".proposed_changes/108-posthog-disable-sensitive-dom-capture"

if IN_PROPOSAL:
    source = (PROPOSAL / "frontend/lib/analytics.ts.patch").read_text()
    assert "diff --git a/frontend/lib/analytics.ts b/frontend/lib/analytics.ts" in source
    assert "+    autocapture: false," in source
    assert "+    disable_session_recording: true," in source
    assert " capture_pageview: true," in source
    assert " sanitize_properties: (props) =>" not in source  # unchanged, not removed
else:
    source = (ROOT / "frontend/lib/analytics.ts").read_text()
    init_block = source[
        source.index("posthog.init(key, {") : source.index("initialized = true")
    ]

    # PostHog must never infer events from rendered DOM text or attributes. This
    # blocks the observed avatar-email and person-bearing account-label leaks.
    assert "autocapture: false" in init_block
    assert "disable_session_recording: true" in init_block

    # Deliberate pageviews and typed events remain available, with URL credential
    # sanitization and identity lifecycle protections intact.
    assert "capture_pageview: true" in init_block
    assert "sanitize_properties" in init_block
    assert "props[k] = scrubUrl(props[k])" in init_block
    assert "posthog.capture(name, props ?? {})" in source
    assert "posthog.identify(normalized)" in source
    assert "if (initialized) posthog.reset()" in source

    # No code should manually recreate the event disabled above.
    assert "posthog.capture('$autocapture'" not in source
    assert 'posthog.capture("$autocapture"' not in source

print("108 posthog sensitive DOM capture disabled: PASS")
