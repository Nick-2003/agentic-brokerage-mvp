# NEXT SESSION — start here

**This is the single pickup doc.** Read it, then `CLAUDE.md`, then `SESSION_LOG.md`. Goal of the next session: **(1) test the filled-order path during US market hours, (2) wire the frontend and walk the full demo.**

> Prior goal (fix two prompt bugs + test the paper-trade flow) is **done** — see "Resolved last session" below.

---

## Where things stand (verified 2026-05-20)

| Layer | State |
|---|---|
| Backend — FastAPI, agent loop, 15 tools, SSE | ✅ Running, real Claude verified |
| Anthropic API | ✅ Live — key in `backend/.env`, real call streamed end-to-end |
| Alpaca paper trading | ✅ Live — `get_portfolio` + `place_paper_order` (simple, bracket, cap) all verified against the real $100k paper account |
| Widget emission | ✅ Fixed — every intent reliably emits the right widget (11/11 verified) |
| Frontend — Next.js, 8 widget renderers | ✅ Built. **Tom is running `pnpm install` himself.** |
| Supabase (auth) / PostHog (analytics) | ⏳ Not wired. Backend runs fine without them. Deferred. |
| Security audit | ✅ Done (`docs/SECURITY_AUDIT.md`) — one XSS fixed, rest are known pre-launch items |

---

## Resolved last session (2026-05-20, prompt-fix session)

- **Bug A (markdown instead of widget) — FIXED.** `backend/prompts/system.md` rewritten with a forceful widget mandate + intent→widget table. 11/11 widgets verified.
- **Bug B (wrong numbers) — was a MISDIAGNOSIS, not a bug.** The `$220.61` in the report was the *real* yfinance NVDA price (yfinance is installed, so `get_quote` skips the mock unless `USE_MOCK_MARKET=1`). The agent copies tool numbers faithfully — verified across ~10 widgets. No validator built (it would target a non-bug and risk false-failing `order_ticket` math).
- **Bug C (fabricated fill) — FOUND & FIXED.** Agent was inventing a `fill_price`/`filled_at` on an `accepted` (unfilled) order. New `system.md` rule: only emit `live_trade` on `status: filled`; otherwise report "order working, not filled yet" in markdown.
- **`place_paper_order` exercised against real Alpaca** — simple, bracket (TP/SL legs verified), and the $50k notional cap. All test orders cancelled; account clean.

⚠️ **Not yet verified:** the `filled` → `live_trade` path. Market was closed all session, so real orders only reached `accepted`. **This is Priority 1 below.**

**uv sync gotcha:** `supabase` + `posthog` were moved to an optional `auth` dependency group because `cryptography` kept timing out on a slow network. Backend code imports neither. When auth work begins: `cd backend && uv sync --group auth`.

---

## How to run it

```bash
# Backend (terminal 1)
cd /Users/tom/Code/agentic-brokerage-mvp/backend
.venv/bin/uvicorn main:app --reload --port 8000
# verify: curl -s localhost:8000/healthz   → should show anthropic_key_present:true, alpaca_configured:true

# Frontend (terminal 2) — after `pnpm install`
cd /Users/tom/Code/agentic-brokerage-mvp/frontend
pnpm dev
# open http://localhost:3000

# Quick backend-only smoke test (no frontend needed):
bash /Users/tom/Code/agentic-brokerage-mvp/scripts/smoke_test.sh
```

Model is `claude-opus-4-5` (set in `backend/.env` as `ANTHROPIC_MODEL`). Change there if needed.

---

## PRIORITY 1 — verify the filled-order → live_trade path (market hours)

`place_paper_order` is verified against real Alpaca, but only the `accepted` (unfilled) branch — the market was closed last session, so no real order ever reached `status: filled`. The `system.md` rule for the `filled` case (call `get_open_position` → emit a `live_trade` widget with the real fill) is written but **unverified end-to-end**.

**Test (during US market hours, 09:30–16:00 ET):**
1. Place a *marketable* limit so it actually fills, e.g. confirm an order to buy 1–2 shares of a liquid name at a limit at/above the ask.
   ```
   curl -s -N -X POST localhost:8000/api/chat -H 'Content-Type: application/json' \
     -d '{"message":"I reviewed the ticket and confirm — place a paper buy of 2 NVDA at limit <ask+1>"}'
   ```
2. Expect: `thought → tool_call(place_paper_order) → tool_result → thought → tool_call(get_open_position) → tool_result → widget(live_trade) → done`.
3. Verify the `live_trade` widget's `fill_price` is the **real** Alpaca fill (not the limit), and `current_price`/`unrealized_pnl` are populated.
4. Clean up: cancel/close the position afterward (or keep one to seed the portfolio demo — see below).

**Account seeding:** the real Alpaca paper account is empty ($100k, 0 positions). A rich `morning_brief`/`portfolio_risk` demo needs holdings — either fill a few orders during market hours, or run the backend **without** Alpaca keys to use the hand-tuned mock portfolio (semis-heavy, matches the demo HTML).

---

## PRIORITY 2 — wire the frontend, walk the full demo

Backend is solid. Connect the frontend and walk the validated 90-second demo flow end to end.

- `cd frontend && pnpm dev` → `http://localhost:3000` (Tom is handling `pnpm install`).
- **Demo recommendation:** run the backend with `USE_MOCK_MARKET=1` for a deterministic demo whose numbers match the demo HTML ($942.50 etc.). Real yfinance returns messy pre-market quotes with wide spreads (NVDA ask 235.79 / bid 208.56) — fine for correctness, ugly for a demo.
- Walk: morning brief → research card → chart → order ticket → confirm → (live trade, if market open) → thesis → portfolio risk. All widget types verified emitting correctly from the backend.

---

## PRIORITY 3 — real research data via TrueNorth's MCP server

Research is hand-tuned mock for 7 tickers only — "analyze CRM" returns nothing. TrueNorth's MCP server (live, verified 2026-05-21) serves real analyst consensus, price targets, financials, and SEC filings for *any* US ticker. It slots into the existing mock-first pattern — only the data behind the research tools changes; no prompt/widget/schema changes.

**Full task spec: [`TRUENORTH_MCP_INTEGRATION.md`](./TRUENORTH_MCP_INTEGRATION.md)** — connection options, tool-by-tool mapping, ordered steps, acceptance criteria.

Prerequisite: confirm TrueNorth MCP access (hosted endpoint, or self-host from a pulled `discovery-agents` copy). Not a blocker for Priorities 1–2.

---

## Key files map

| Need to touch | File |
|---|---|
| Agent prompt / behaviour rules | `backend/prompts/system.md`, `backend/prompts/widget_contract.md` |
| Agent loop / intent detection | `backend/agent.py` |
| Trade execution | `backend/tools/execution.py` |
| Widget validator (deferred — see decision below) | new — `backend/agent.py` or `backend/validation.py` |
| All tools | `backend/tools/*.py` (15 tools across 6 modules) |
| Frontend widgets | `frontend/components/widgets/*.tsx` |
| Frontend ↔ backend types | `frontend/lib/widgets.ts` (must match `widget_contract.md`) |

---

## Decisions locked in (don't re-litigate)

- **Anthropic SDK directly, not claude-agent-sdk** — simpler SSE control. Revisit only when TradingView MCP integration starts.
- **Hermes Agent framework — rejected as backend.** Single-user, no API, autonomy is a liability for a brokerage. Adopt the *idea* (pinned widgets = standing skills) via `skill_id` + `refresh_policy` on the widget model later. Not an MVP change.
- **Mock-first per tool** — every tool has a mock fallback; real path activates on key-prefix detection. Don't remove the mocks; they're how the thing runs without keys.
- **Auth deferred** — `user_id` is currently the hardcoded string `"demo"`, client-spoofable. Phase 7's unfinished half. Don't ship to real users until Supabase JWT auth lands (SECURITY_AUDIT.md HIGH-2).
- **Widget JSON contract is the cross-boundary truth** — change `backend/prompts/widget_contract.md` AND `frontend/lib/widgets.ts` together or they drift.
- **Widget numeric validator — deferred, not built.** "Bug B" (agent mis-copies numbers) was a misdiagnosis: the agent copies tool numbers faithfully (verified across ~10 widgets). A fail-closed validator targeting a non-existent bug would also risk false-failing `order_ticket`'s legitimately-computed fields (notional, R:R). It stays in `CLAUDE.md` as planned architecture; build it only if real number drift is ever observed.

---

## Full doc index

- `CLAUDE.md` — architecture, trust principles, SSE protocol, widget contract overview
- `SCOPE.md` — what's in / out / the amendment rule
- `METRICS.md` — PMF benchmarks, PostHog events
- `SECURITY.md` — threat model + pre-launch lockdown checklist
- `SECURITY_AUDIT.md` — first-pass audit findings (2 HIGH, 4 MED, 3 LOW)
- `SESSION_LOG.md` — chronological decision log, every session
- `MORNING_BRIEFING.md` — earlier wakeup doc (now partly superseded by this file)
- `ACCOUNTS_SETUP_GUIDE.md` — how the API keys were obtained
- `TRUENORTH_MCP_INTEGRATION.md` — task spec: real research data via TrueNorth's MCP server (Priority 3)
- Plan: `/Users/tom/.claude/plans/nifty-moseying-aho.md`

---

## One-line summary for the fresh session

> Backend is live, verified against real Claude + real Alpaca. Prompt bugs fixed: widgets now emit reliably (Bug A), "Bug B" was a misdiagnosis, and a fabricated-fill bug (C) was found & fixed. `place_paper_order` verified (simple + bracket + cap). Remaining: verify the `filled` → `live_trade` path during market hours, then wire the frontend and walk the demo. Don't touch architecture — it works.
