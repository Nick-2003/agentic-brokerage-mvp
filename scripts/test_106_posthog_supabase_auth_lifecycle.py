#!/usr/bin/env python3
"""Contract guard for staged or applied Supabase-to-PostHog identity wiring."""
from pathlib import Path


TEST_FILE = Path(__file__).resolve()
IN_PROPOSAL = ".proposed_changes" in TEST_FILE.parts
ROOT = TEST_FILE.parents[3] if IN_PROPOSAL else TEST_FILE.parents[1]
PROPOSAL = ROOT / ".proposed_changes/106-posthog-supabase-auth-lifecycle"
HELPERS_PROPOSAL = ROOT / ".proposed_changes/105-posthog-analytics-identity-helpers"
COMPONENT = (
    PROPOSAL / "frontend/components/Analytics.tsx"
    if IN_PROPOSAL
    else ROOT / "frontend/components/Analytics.tsx"
)
HELPERS = (
    HELPERS_PROPOSAL / "frontend/lib/analytics.ts"
    if IN_PROPOSAL
    else ROOT / "frontend/lib/analytics.ts"
)

component = COMPONENT.read_text()
helpers = HELPERS.read_text()

# Proposal 105 provides order-independent, PII-minimal identity primitives.
assert "export function identifyAnalyticsUser(userId: string): void" in helpers
assert "export function resetAnalyticsUser(): void" in helpers
assert "posthog.identify(normalized)" in helpers
assert "if (identifiedUserId) posthog.reset()" in helpers
assert "if (initialized) posthog.reset()" in helpers

# The global root-mounted analytics component is the single wiring point, covering
# chat, connect, and callback routes without duplicating page-specific listeners.
assert "identifyAnalyticsUser" in component
assert "resetAnalyticsUser" in component
assert "getSupabase" in component
assert component.count("initAnalytics()") == 1
assert component.index("initAnalytics()") < component.index("getSupabase()")

# Initial restored session and all later auth changes converge on the same
# session-presence rule. Repeated refresh/sign-in events remain idempotent in 105.
assert "supabase.auth.getSession()" in component
assert "supabase.auth.onAuthStateChange" in component
assert component.count("syncIdentity(data.session?.user.id)") == 1
assert component.count("syncIdentity(session?.user.id)") == 1
assert "if (userId) identifyAnalyticsUser(userId)" in component
assert "else resetAnalyticsUser()" in component

# Missing Supabase configuration is anonymous/reset, and Strict Mode cleanup only
# unsubscribes. Cleanup must not reset an active identity during effect replay.
assert "if (!supabase)" in component
assert "subscription.subscription.unsubscribe()" in component
cleanup = component[component.index("return () => {") :]
assert "resetAnalyticsUser" not in cleanup
assert "active = false" in cleanup

# No PII or brokerage identifiers enter the identity component.
for forbidden in ("email", "account_id", "connection_id", "userSecret"):
    assert forbidden not in component

print("106 posthog supabase auth lifecycle: PASS")
