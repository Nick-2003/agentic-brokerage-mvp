#!/usr/bin/env python3
"""Contract guard for staged or applied PostHog identity helpers."""
from pathlib import Path


TEST_FILE = Path(__file__).resolve()
IN_PROPOSAL = ".proposed_changes" in TEST_FILE.parts
ROOT = TEST_FILE.parents[3] if IN_PROPOSAL else TEST_FILE.parents[1]
PROPOSAL = ROOT / ".proposed_changes/105-posthog-analytics-identity-helpers"
ANALYTICS = (
    PROPOSAL / "frontend/lib/analytics.ts"
    if IN_PROPOSAL
    else ROOT / "frontend/lib/analytics.ts"
)
source = ANALYTICS.read_text()

# Identity can arrive before or after init without being lost.
assert "let queuedUserId: string | null = null" in source
assert "let identifiedUserId: string | null = null" in source
assert source.index("initialized = true") < source.index("if (queuedUserId)")
assert "posthog.identify(queuedUserId)" in source
assert "identifiedUserId = queuedUserId" in source

# Only an explicit opaque user ID crosses the helper boundary; no PII fields are
# accepted or attached as PostHog person properties.
assert "export function identifyAnalyticsUser(userId: string): void" in source
identify_block = source[
    source.index("export function identifyAnalyticsUser") :
    source.index("export function resetAnalyticsUser")
]
assert "userId.trim()" in identify_block
assert "posthog.identify(normalized)" in identify_block
assert "email" not in identify_block.lower()
assert "account" not in identify_block.lower()
assert "setPersonProperties" not in source

# A direct user change and explicit sign-out both reset anonymous persistence so
# events from two authenticated people cannot merge in one browser profile.
assert "if (identifiedUserId) posthog.reset()" in identify_block
reset_block = source[
    source.index("export function resetAnalyticsUser") :
    source.index("// ── account lifecycle")
]
assert "queuedUserId = null" in reset_block
assert "identifiedUserId = null" in reset_block
assert "if (initialized) posthog.reset()" in reset_block

# Existing URL credential sanitization and event capture remain present.
assert "sanitize_properties" in source
assert "refresh_token" in source
assert "connection_id" in source
assert "posthog.capture(name, props ?? {})" in source

print("105 posthog analytics identity helpers: PASS")
