#!/usr/bin/env bash
# Smoke test the full agentic-brokerage stack after keys are pasted.
#
# Usage:
#   ./scripts/smoke_test.sh
#
# Prereqs:
#   - backend/.env has real ANTHROPIC_API_KEY
#   - Backend running on http://localhost:8000
#
# What it tests:
#   1. /healthz returns 200 with anthropic_key_present=true
#   2. /api/chat streams SSE events for a real Claude call
#   3. The agent fires at least one tool_call event
#   4. The agent emits a widget event (or message) before done

set -euo pipefail

BACKEND="${BACKEND:-http://localhost:8000}"
GREEN='\033[0;32m'
RED='\033[0;31m'
YEL='\033[0;33m'
NC='\033[0m'

pass() { echo -e "  ${GREEN}✓${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; exit 1; }
warn() { echo -e "  ${YEL}!${NC} $1"; }

echo "Smoke testing $BACKEND"
echo

# Test 1: healthz
echo "▸ Test 1: healthcheck"
HEALTH=$(curl -fsS "$BACKEND/healthz" 2>&1 || true)
if [ -z "$HEALTH" ]; then
  fail "backend not reachable at $BACKEND — is uvicorn running?"
fi

if echo "$HEALTH" | grep -q '"ok":true'; then pass "/healthz returns ok"
else fail "/healthz did not return ok=true: $HEALTH"
fi

if echo "$HEALTH" | grep -q '"anthropic_key_present":true'; then pass "Anthropic key present"
else fail "ANTHROPIC_API_KEY missing or still set to placeholder — see backend/.env"
fi

if echo "$HEALTH" | grep -q '"alpaca_configured":true'; then pass "Alpaca configured"
else warn "Alpaca not configured — trade flows will use mock (set ALPACA_API_KEY)"
fi

if echo "$HEALTH" | grep -q '"tools_registered"'; then
  NTOOLS=$(echo "$HEALTH" | grep -oE '"tools_registered":\[[^]]+\]' | tr ',' '\n' | wc -l | tr -d ' ')
  pass "$NTOOLS tools registered"
else
  warn "tools_registered field missing in /healthz"
fi
echo

# Test 2: streaming chat
echo "▸ Test 2: /api/chat streams SSE"
EVENTS_FILE=$(mktemp)
TIMEOUT=60

# POST and stream the response; record event names line-by-line
(
  curl -fsS -N -X POST "$BACKEND/api/chat" \
    -H "Content-Type: application/json" \
    -d '{"message":"give me a tldr on my portfolio"}' \
    --max-time "$TIMEOUT" 2>&1 \
    | grep -E "^event:" \
    | tee "$EVENTS_FILE" > /dev/null
) || true

if [ ! -s "$EVENTS_FILE" ]; then
  fail "no SSE events received — check backend logs"
fi

NEV=$(wc -l < "$EVENTS_FILE" | tr -d ' ')
pass "$NEV SSE events received"

# Test 3: tool_call fired
if grep -q "^event: tool_call$" "$EVENTS_FILE"; then
  pass "agent fired ≥1 tool_call"
else
  fail "no tool_call event — agent isn't using tools"
fi

# Test 4: widget OR message before done
if grep -qE "^event: (widget|message)$" "$EVENTS_FILE"; then
  pass "agent emitted a widget or message"
else
  warn "no widget/message event — agent may have just done thoughts + tool calls"
fi

if grep -q "^event: done$" "$EVENTS_FILE"; then
  pass "agent reached done"
else
  fail "agent did not reach done — possible timeout"
fi

echo
echo "Event timeline:"
nl -ba "$EVENTS_FILE" | sed 's/^/  /'

rm -f "$EVENTS_FILE"
echo
echo -e "${GREEN}All smoke tests passed.${NC}"
