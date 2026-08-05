#!/usr/bin/env python3
"""Static contract guard for staged or applied proposal 101 callback code."""
from pathlib import Path


TEST_FILE = Path(__file__).resolve()
IN_PROPOSAL = ".proposed_changes" in TEST_FILE.parts
ROOT = TEST_FILE.parents[3] if IN_PROPOSAL else TEST_FILE.parents[1]
PROPOSAL = ROOT / ".proposed_changes/101-snaptrade-callback-single-processing"
CALLBACK = (
    PROPOSAL / "frontend/app/settings/brokerage/snaptrade/callback/page.tsx"
    if IN_PROPOSAL
    else ROOT / "frontend/app/settings/brokerage/snaptrade/callback/page.tsx"
)

source = CALLBACK.read_text()

# Provider callback values are captured before replaceState can update
# useSearchParams, and the effect no longer depends on that mutable object.
assert "callbackSnapshotRef" in source
assert "params.get('status')" in source
assert "params.get('connection_id')" in source
assert source.index("callbackSnapshotRef.current =") < source.index("window.history.replaceState")
assert "}, [router]);" in source
assert "[params, router]" not in source

# Strict Mode effect replay shares one verification promise instead of either
# issuing a second request or permanently cancelling the first continuation.
assert "verificationPromiseRef" in source
assert "verificationPromiseRef.current === null" in source
assert "verificationPromiseRef.current = verify()" in source
assert "verificationPromiseRef.current.then" in source
assert source.count("verifySnapTradeConnection(token, callback.connectionId)") == 1

# Exactly one terminal result owns UI, analytics, and navigation.
assert "terminalOutcomeRef.current !== null" in source
assert "terminalOutcomeRef.current = 'failed'" in source
assert "terminalOutcomeRef.current = 'completed'" in source
assert source.count("trackBrokerConnectionFailed('snaptrade', outcome.error)") == 1
assert source.count("trackBrokerConnectionCompleted('snaptrade', outcome.accountCount)") == 1
assert source.count("router.replace('/connect?snaptrade=connected')") == 1

# URL scrubbing remains immediate and provider identifiers are never rendered.
assert "urlScrubbedRef" in source
assert "window.history.replaceState(null, '', window.location.pathname)" in source
rendered = source[source.index("  return (") :]
assert "connectionId" not in rendered
assert "connection_id" not in rendered

print("101 snaptrade callback single processing: PASS")
