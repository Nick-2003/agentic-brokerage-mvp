# Session log

5 minutes per session. End every Claude Code session with a fresh entry below.

---

## 2026-05-20 · Phase 0 — Architecture docs

**Built:**
- Repo scaffold (`backend/`, `frontend/`, `docs/`)
- `CLAUDE.md` — architecture, trust principles, widget JSON contract, SSE event protocol
- `SCOPE.md` — six in-scope flows, explicit non-goals, scope amendment rule
- `METRICS.md` — D1/D7/D30 retention targets, activation funnel, Sean Ellis test, PostHog event list
- `SECURITY.md` — 8 threats with mitigations, pre-launch lockdown checklist
- `README.md` — setup + run + env var templates

**Decisions surfaced:**
- TradingView MCP is **in MVP** (initially almost punted to v2 — Tom corrected; this is the defensible wedge so it must be in)
- Each user gets their own Alpaca paper account (created server-side on first trade) — avoids cross-user trade routing risk
- Numeric validator on widget outputs: every numeric field in a widget must trace back to a tool call ID (hallucination defence)
- Magic link expiry at 1h; sensitive-op re-auth deferred to v2 (acceptable risk for paper-only)

**Assumptions introduced:**
- TradingView Desktop is acceptable for Phase 4 (real chart manipulation); containerised server-side TradingView is a v2 problem
- `claude-agent-sdk` supports mounting external MCP servers via stdio transport (need to verify in Phase 4)
- Alpaca paper API supports OCO bracket orders with TP + SL (need to verify in Phase 5)
- yfinance is reliable enough for production traffic at 5–10 users (it might rate-limit; have Polygon free tier as fallback)
- PostHog browser SDK + Python SDK both supported (verified — both exist in PostHog Cloud docs)

**Next session:** Phase 1 — backend skeleton: FastAPI app, SSE chat endpoint, claude-agent-sdk integration, one tool (`get_portfolio`) end-to-end.

---

## 2026-05-20 (late, post-midnight) · Phases 1–7 + deploy configs

**Built:**
- Phase 1 backend skeleton: `pyproject.toml`, system prompt, widget JSON contract (8 schemas), `tools/__init__.py` registry, FastAPI `main.py` with SSE endpoint, agent loop in `agent.py` using Anthropic SDK directly (not claude-agent-sdk — see decision below).
- Phase 2: market tools (`get_quote`, `get_company_news`, `get_macro_snapshot`) with hand-tuned mock data matching the demo + yfinance fallback.
- Phase 3: research tools (`get_company_fundamentals`, `get_consensus_targets`, `get_full_research`, `get_peer_set`) with tuned data for NVDA/AAPL/MSFT/TSLA/AMD/GOOGL.
- Phase 4: TA tools (`get_technical_levels`, `get_correlation_matrix`) with mock chart data shaped like real TradingView MCP responses.
- Phase 5: execution tools (`place_paper_order`, `get_open_position`, `list_open_positions`) with mock JSON path + real Alpaca path side-by-side.
- Phase 6: risk tools (`compute_portfolio_risk`, `compose_thesis`).
- Phase 7: Full Next.js 15 frontend. Phone-frame UI matching the demo. SSE consumer, all 8 widget renderers, animated chat bar with cycling placeholder, thinking breadcrumb card, pin-to-home, side panel with prompt cheat sheet.
- Deployment configs: `backend/Dockerfile`, `backend/railway.json`, `frontend/vercel.json`, `frontend/.env.local.example`.
- Morning briefing doc at `docs/MORNING_BRIEFING.md` — first thing to read on wakeup.

**Decisions surfaced:**
- **Anthropic SDK directly over claude-agent-sdk for Phase 1.** Reason: simpler streaming control for SSE conversion; less reliance on SDK abstractions we'd need to reverse-engineer when MCP comes in. Tradeoff: when TradingView MCP integration goes live (Phase 4-live), we'll either switch to claude-agent-sdk or use Anthropic's beta MCP support directly. Documented in CLAUDE.md.
- **Auth deferred.** Phase 7 plan included Supabase magic link sign-in — pushed to next session because keys not available yet anyway. Currently `user_id` is the hardcoded string `"demo"`.
- **Mock-first strategy is explicit:** every tool has a mock fallback when its real provider's key isn't set. Detection is `if key.startswith("PK"): use_real() else: use_mock()`. No silent fallback to mock when real path errors — we surface `error: "alpaca_fetch_failed"` so we know real is broken vs not configured.
- **One `user_id` field, no auth context.** Per scope amendment rule, won't add auth UI until at least one tester says they need it.
- **Mocked tool data lives next to real fetcher** — same file. Made it easy to diff what's mocked when keys arrive.

**Assumptions introduced:**
- yfinance is acceptable for production traffic at 5–10 users (might rate-limit at scale; Polygon free tier is the fallback when that happens — not built yet).
- Mock orders persist to `backend/data/mock_orders.json` between backend restarts. Real Alpaca persists in their cloud.
- Frontend can talk to backend via Next.js rewrites (`/api/chat` → backend `/api/chat`). This means we don't need CORS in production if the frontend proxies — only for `pnpm dev`.
- The widget JSON contract is the source of truth for both sides. If we change a schema, change it in `backend/prompts/widget_contract.md` AND `frontend/lib/widgets.ts` simultaneously.

**Did NOT do (out of scope this session):**
- Did NOT run live smoke test (`curl /api/chat`) — needs `ANTHROPIC_API_KEY`.
- Did NOT actually deploy to Railway / Vercel — configs ready, awaiting accounts.
- Did NOT install frontend deps (`pnpm install`) — ~200 MB download; network was slow.
- Did NOT wire Supabase auth.
- Did NOT wire PostHog event tracking (instrumentation lives in METRICS.md as a checklist; lib calls deferred).
- Did NOT wire real TradingView MCP integration. Frontend `TAChart` widget already looks like a real chart via inline SVG.

**Next session:**
- Paste API keys, write to `backend/.env` + `frontend/.env.local`.
- Run end-to-end smoke test (90-second walkthrough in MORNING_BRIEFING.md).
- If clean: deploy to Railway + Vercel.
- If issues: debug Anthropic key / model alias / Alpaca creds.
- Then: wire Supabase magic link auth (Phase 7 completion).

---

## 2026-05-20 (later) · Hermes evaluation + security audit

**Hermes Agent framework — evaluated, decided NOT to adopt as backend.**
- Tom asked about building on Nous Research's Hermes Agent (hermes-agent.nousresearch.com).
- Findings: it's a single-user personal agent (MIT licensed), CLI + messaging-platform-first, no HTTP API/SDK, not multi-tenant. Self-improving loop + autonomous skill creation + `execute_code` + 70 tools.
- Decision: do NOT put Hermes in the backend. Single-user architecture, no embeddable API, and the autonomy (self-rewriting agent with code execution) is a security liability when wired to trade execution. Fails the SCOPE.md amendment rule too — no user has asked.
- DO adopt the *vision*: "pinned widgets ARE standing skills/agents." This is an extension of our existing pin-to-home. Cheap path: add `skill_id` + `refresh_policy` to the pinned-widget data model; add a `user_profile` table the agent reads+updates (lightweight Honcho-style user modelling). Logged as a near-term enhancement, not an MVP scope change.

**Security audit — first pass run (see `docs/SECURITY_AUDIT.md`).**
- 2 HIGH, 4 MEDIUM, 3 LOW, 2 INFO findings.
- HIGH-1 (XSS in `SafeHtml` — allowed tags kept their attributes, so `<strong onclick=...>` survived) — **FIXED this session.** Rewrote sanitiser to allow only attribute-free `<strong>`/`<em>`; added the rule to SECURITY.md checklist.
- HIGH-2 (no auth, `user_id` spoofable) — known, it's Phase 7's unfinished half.
- MEDIUM: no rate limiting, shared Alpaca account, cross-user mock-order leakage, future prompt-injection vector. All deferred to pre-launch, all tracked in SECURITY.md.
- Audit confirmed SECURITY.md's pre-launch checklist is the right list. Dependency audit (pip-audit/pnpm audit) deferred — deps weren't installed.

**Assumption introduced:** none new. Audit was review-only except the one XSS fix.

**Note:** `uv sync` still failing to complete on a very slow network (40+ min). Not a blocker — MORNING_BRIEFING.md tells Tom to re-run `UV_HTTP_TIMEOUT=300 uv sync` if the venv is empty.

---

## 2026-05-20 (later still) · Backend LIVE — first real Claude smoke test

**Done:**
- `uv sync` finally succeeded after moving `supabase` + `posthog` into an optional `auth` dependency group. They pulled in `cryptography` (7.6 MB wheel) which kept timing out on the slow network, and neither is imported by backend code yet (auth deferred). Install later with `uv sync --group auth`.
- Anthropic API key received from Tom, written to `backend/.env` (gitignored — verified with `git check-ignore`).
- Backend booted: `/healthz` returns ok, 15 tools registered, `anthropic_key_present: true`.
- **First real end-to-end Claude smoke test PASSED.** `POST /api/chat` with "give me a tldr on my portfolio" → streamed `thought → tool_call → tool_result` (4 tools: get_portfolio, get_quote, get_company_news, get_macro_snapshot) → terminal response → `done` in ~19s, 3 iterations. SSE pipeline, agent loop, tool execution all work with the real API.

**Two prompt-tuning issues found (NOT architecture bugs — Phase 2 polish):**
1. **Agent emitted a plain markdown `message`, not a `morning_brief` widget.** For "tldr on my portfolio" Claude answered with a markdown table instead of the widget JSON. Fix: strengthen the system prompt / add a few-shot example so portfolio/research/brief intents reliably produce widget JSON. Consider: detect intent server-side and inject "respond with a {widget_type} widget" into the turn.
2. **Data quality — wrong "current" prices in the output.** The agent showed NVDA current price as $220.61 (mock quote is $942.50); other tickers similarly off. The agent mis-transcribed / mis-computed numbers from tool results despite trust principle #3. Fix: tighten the system prompt's "no number without source" rule; possibly have the widget schema reference tool-call IDs so a validator can catch number drift (the validator described in CLAUDE.md isn't built yet).

**Model note:** running `claude-opus-4-5` (the `.env` default). Worked fine. Tom can bump to a newer Opus alias if desired.

**Next session:**
- Fix the two prompt issues above (Phase 2 polish) — highest priority, it's what makes the demo land.
- `cd frontend && pnpm install && pnpm dev` — wire the frontend to the now-working backend.
- Build the widget-output validator (CLAUDE.md describes it; not yet built).
- Then Alpaca keys → real trade flow; Supabase → auth (`uv sync --group auth`).

---

## 2026-05-20 · Alpaca paper trading connected

- Alpaca paper credentials received + written to `backend/.env`.
- Verified: `get_portfolio` connects to the REAL Alpaca paper API — $100,000 equity, 0 positions (fresh account). Mock→real toggle worked automatically (detected the `PK` key prefix).
- `place_paper_order` not yet exercised against real Alpaca (uses the same `TradingClient` + auth as the verified portfolio fetch, so low risk) — will be tested next session via the frontend trade flow.
- Still pending: Supabase (auth), PostHog (analytics). Backend runs fine without them.

---

## 2026-05-20 (later still) · Prompt-bug fixes + paper-trade flow tested

**Priority 1 — the two prompt bugs:**

- **Bug A (agent emits markdown, not a widget) — FIXED.** Rewrote `backend/prompts/system.md`: added a forceful "your final response is ALWAYS a widget" section with an explicit intent→widget-type table, a "never emit a markdown table of numbers" rule, and a worked example. Verified: 11/11 widget emissions across the session — `morning_brief`, `research_card`, `ta_chart`, `order_ticket`, `portfolio_risk`, `thesis` all emit correctly; markdown is now reserved for genuine non-actionable replies only.

- **Bug B (wrong numbers in output) — NOT A REAL BUG. Misdiagnosis.** The original report saw NVDA current price `$220.61` and compared it to the *mock* quote `$942.50`. But `get_quote` only uses mock data when `USE_MOCK_MARKET=1` OR yfinance is unavailable — and yfinance **is** installed, so the original smoke test used the **real** yfinance price. Verified this session: `_fetch_yfinance_quote("NVDA")` returns exactly `220.61`, and a live agent research query in real-market mode emits `current_price: 220.61` — i.e. the agent copied the real tool output **faithfully**. Tested number fidelity across ~10 widgets (research cards, morning briefs, order math) in mock mode: every number matched the tool result exactly. No transcription bug exists.
  - Still strengthened `system.md` trust principles with an explicit "copy numbers verbatim, digit for digit" rule + an order-math carve-out (sizing arithmetic is the only thing the agent may compute). Cheap insurance, zero downside.
  - **The widget numeric validator (CLAUDE.md trust principle #3) was NOT built** — it targets a bug that doesn't exist, and a fail-closed validator would risk false-failing `order_ticket`'s legitimately-computed fields (notional, R:R) and break the trade demo. Left as planned-but-deferred architecture. Revisit only if real number drift ever shows up.

**Priority 2 — paper-trade flow tested against real Alpaca:**

- Exercised `place_paper_order` via our own tool code (not raw alpaca-py): simple limit buy OK, **bracket buy with TP+SL** OK (verified on Alpaca's side: parent `class=bracket` + a `limit sell` TP leg @ 300 + a `stop sell` SL leg @ 200, both `held`), notional cap OK ($240k order blocked at the $50k cap). Real Alpaca order IDs returned; all test orders cancelled afterward — account back to clean $100k / 0 positions.
- Agent execution path OK — on an explicit confirmation message the agent calls `place_paper_order` (does NOT auto-execute on a bare "buy X" — that correctly produces an `order_ticket` proposal first).

- **Bug C found + fixed (new, surfaced during testing).** With the market closed, `place_paper_order` returns `status: accepted` (no `fill_price`, no `filled_at`). The agent was emitting a `live_trade` widget with an **invented** `fill_price: 240` (= the limit) and a fake `filled_at` — a trust-principle-#3 violation. Fixed via a new `system.md` section ("Placing orders — report the order status honestly"): `filled` → read the real fill via `get_open_position` then emit `live_trade`; `accepted`/`new`/`pending_new` → plain-markdown "order placed, working, not filled yet"; `rejected`/error → say it didn't go through. Verified: confirmed order now returns an honest markdown "has not filled yet — resting limit order" message, no fabricated `live_trade`.

**Decisions surfaced:**
- **No widget validator this session** — see Bug B above. Documented so it isn't re-litigated.
- The hand-tuned mock market data (`USE_MOCK_MARKET=1`) is the better choice for a deterministic demo that matches the validated demo HTML ($942.50 etc.). Real yfinance returns messy pre-market quotes with wide spreads (NVDA ask 235.79 / bid 208.56). Recommendation to Tom — his call.

**Assumptions / not done:**
- The `filled` → `get_open_position` → `live_trade` branch could NOT be exercised: the market was closed all session, so real paper orders only ever reached `accepted` (they fill at the next 09:30 ET open). The prompt rule for the `filled` case is written but unverified end-to-end. **Test during market hours next session.**
- Real Alpaca paper account is empty ($100k, 0 positions). A rich portfolio demo (`morning_brief`/`portfolio_risk` with holdings) needs the account seeded — either place a few trades during market hours, or run without Alpaca keys to use the hand-tuned mock portfolio.

**Next session:**
- Test the filled-order path during US market hours (place a marketable limit, confirm `live_trade` renders with a real fill).
- Wire the frontend to the verified backend (`pnpm dev`) and walk the full demo.
- Then: Supabase auth (`uv sync --group auth`), PostHog.

---

## 2026-05-20 (frontend live) · SSE bug fix + demo portfolio (TSLA/NVDA/TCEHY)

**Built / fixed:**
- `pnpm install` + `pnpm dev` — frontend live at `localhost:3000`, proxying `/api/chat` to the backend.
- **Frontend "no response" bug — FIXED.** `lib/sse.ts` split SSE frames on `\n\n`, but the backend (sse-starlette) terminates lines with `\r\n`, so frames are separated by `\r\n\r\n` — which contains no `\n\n` substring. Result: zero events ever parsed, the chat showed the user bubble and nothing else, and `streaming` never reset. Fix: split frames on `/\r?\n\r?\n/` and parse lines on `/\r?\n/`. Confirmed via `od -c` on the raw stream.
- **Demo portfolio is now TSLA + NVDA + TCEHY** (Tom's request — these three "always in the portfolio"). Rewrote `MOCK_PORTFOLIO` (equity $51,000.00, all three positions green). Added `TCEHY` (Tencent ADR) to `MOCK_QUOTES` + `MOCK_NEWS` (market.py) and full research coverage to `RESEARCH` (research.py) — so quote / chart / research / news / risk all work for Tencent. Updated the frontend Hero header to match ($51,000.00 / +$964.10).
- **New `USE_MOCK_BROKER` env toggle** — sibling of `USE_MOCK_MARKET`. When `=1`, `get_portfolio` and all execution tools use their mock paths even though Alpaca keys are configured. Needed because Tencent is not tradeable on Alpaca (US-equities only) — a coherent TCEHY-holding demo must run on the mock broker. Demo run command is now `USE_MOCK_MARKET=1 USE_MOCK_BROKER=1 uvicorn ...`.

**Decisions surfaced:**
- **Tencent ticker = `TCEHY`** (US ADR), not `0700.HK` — the app is US-equity framed ("holds US equities" in the system prompt) and ADR keeps quotes/yfinance consistent. Easy to switch if Tom wants the HK line.
- The frontend "Portfolio value" header (`app/page.tsx` Hero) is still a **hardcoded** number — now consistent with the mock portfolio, but not live. Wiring it to a real `get_portfolio` call needs a small REST endpoint or an on-mount fetch — deferred, offered to Tom.
- Real-Alpaca paper trading still works (verified earlier this session) — `USE_MOCK_BROKER` is just a demo toggle; drop the flag to go back to the real broker.

**Next session:** unchanged from above — filled-order path during market hours; consider making the Hero portfolio value live.

---

## 2026-05-27 · P1.1 prep + P1.2 TradingView MCP applied + `proposed_changes/` workflow

**Built / applied (P1.2 — TradingView MCP / `PRIORITIES.md` P1.2):**

- Proposal 002 drafted then applied to the live repo. The deferred SDK question (`claude-agent-sdk` vs Anthropic-beta-MCP) is **resolved: neither** — stay on the raw Anthropic SDK and host MCP servers as backend-side stdio clients. Mirrors `docs/TRUENORTH_MCP_INTEGRATION.md` §3. Verified agent loop and SSE protocol unchanged; Bug A (widget emission) and Bug C (status-honest fills) are not re-opened.
- **NEW `backend/mcp_client.py`** — long-lived stdio MCP session manager. Single `asyncio.Lock` per server (CDP is single-controller; serialises every `tv_call`). Lazy init, retry+backoff on spawn, graceful close on FastAPI shutdown. Surfaces `tradingview_mcp_unreachable` / `tradingview_mcp_call_failed` / `tradingview_cdp_refused` to the agent — **no silent fall-through to mock**, same discipline as `alpaca_fetch_failed`.
- **EDIT `backend/tools/technicals.py`** — real path beside the existing mock, gated by `USE_MOCK_TA`. `get_technical_levels` orchestrates 5–7 MCP calls (`tv_health_check → chart_set_symbol → chart_set_timeframe → chart_manage_indicator × N → data_get_study_values → data_get_pine_lines → capture_screenshot`) into a single agent-visible tool result. Indicator name translation table (`"SMA 50"` → `"Moving Average Simple"` length=50; etc.) — single source of truth. Plus three new agent-visible verb tools for the wedge: `chart_apply_indicator`, `chart_draw_levels`, `chart_scroll_to_date`.
- **EDIT `backend/prompts/system.md`** — new row in the intent→widget table for chart-modification verbs; new section requiring honest error reporting on MCP failures (don't fabricate a chart change that didn't happen). Built on top of 001's `news_since_fill` rule — no regression.
- **EDIT `backend/.env.example` + `backend/pyproject.toml`** — added `mcp>=1.0` to main deps; four new env vars (`USE_MOCK_TA=1` default, `TRADINGVIEW_MCP_COMMAND`, `TRADINGVIEW_MCP_ARGS`, `TRADINGVIEW_MCP_CDP_PORT=9222`).
- **FIX `frontend/components/widgets/TAChart.tsx`** — latent bug surfaced while drafting the proposal. The component hardcoded `<MockChartSvg />` and **never read `data.screenshot_url`**. Without this fix the entire P1.2 effort would have been invisible — backend could emit real screenshots, frontend would still draw the SVG. Now renders `<img src={screenshot_url}>` when present, falls back to `MockChartSvg` otherwise. `data:image/png;base64,…` works natively in `<img>`, no plumbing.
- **EDIT `README.md`** — new "Talk-to-your-charts (TradingView MCP)" local-setup section (TV Desktop install, CDP flag, sibling clone of `tradesdontlie/tradingview-mcp`, npm install, env vars). Explicitly calls out the local-only/mock-in-prod posture.
- **EDIT `CLAUDE.md` tech-stack table** — Charts row updated to name the canonical fork (`tradesdontlie/tradingview-mcp`) and lock the deployment posture: *"Real chart in local dev; mock in production until containerised TV Desktop (v2)."*

**Built / applied (P1.1 — filled → live_trade / `PRIORITIES.md` P1.1):**

- Proposal 001 drafted then applied to the live repo — closes SCOPE flow 5's third sub-item ("new info surfaced since trade"), which was previously unwired (no schema field, no system-prompt rule, no `get_company_news(since=...)` arg). Now: on `status: filled`, the agent fires `get_open_position` AND `get_company_news(tickers=[ticker], since=filled_at, limit=3)` **in parallel**; the `live_trade` widget includes `news_since_fill` (top 3, newest first) only when the news call returned items post-dating `filled_at`.
- **Actual filled-path market-hours verification is still pending.** The full session pre-dated US RTH (started ~06:02 EDT; market opens 09:30 EDT). A runbook was built in chat for the operator to execute: boot real-Alpaca backend (no `USE_MOCK_*` flags), `curl` a marketable limit on F at `ask+$0.05`, confirm SSE sequence `place_paper_order(filled) → get_open_position → widget(live_trade)` with `fill_price ≠ limit_price`, cross-check Alpaca dashboard, optionally keep 2 shares of F as a real-portfolio seed. The `accepted` and bracket branches are already verified (2026-05-20); only the `filled → live_trade` branch is outstanding.

**Process — `proposed_changes/` workflow established:**

- New rule: any proposed file change goes into `proposed_changes/<NNN-slug>/` (mirroring the repo tree) **before** being applied to the live repo. Top-level `proposed_changes/README.md` documents the rule; `proposed_changes/STATUS.md` tracks in-flight + applied proposals.
- On apply: the proposal subfolder is moved to `proposed_changes/applied/<slug>/`. Proposal 001 already there. Proposal 002 moves on the next commit pass.
- Drove the discipline this session — both 001 and 002 went through the `proposed_changes/` tree before landing live.

**Decisions surfaced:**

- **SDK decision for MCP — locked: stay on Anthropic SDK, add backend-side MCP client.** Same pattern for every external MCP server (TradingView today, TrueNorth tomorrow). Neither `claude-agent-sdk` nor Anthropic-beta-MCP add enough to justify re-validating widget emission / fill-honesty / SSE control.
- **Canonical TradingView MCP fork — `tradesdontlie/tradingview-mcp`.** ~68 tools, CDP-based via `localhost:9222`, has its own integration docs. Competing forks rejected (Lewis/Jackson is a thinner fork; `harshil1502/` has fewer tools; `atilaahmettaner/` is crypto-focused — wrong fit). Sibling clone, pull-only, never pushed — same rule as TrueNorth.
- **Concurrency model — single `asyncio.Lock` per MCP session, no timeout.** Chrome DevTools Protocol is single-controller; two simultaneous `chart_set_symbol` would clobber state. Lock serialises every `tv_call`. Reject-when-busy and per-user TV instances both rejected for MVP scale.
- **Screenshot encoding — inline base64 `data:image/png;base64,…`** in widget JSON. Zero infra; not cacheable but chart screenshots stale immediately anyway. Switch to a `/api/screenshots/<hash>.png` static route only when SSE bandwidth becomes a real problem or pinned-widget caching demands it. Schema field is format-agnostic; one-file swap when the time comes.
- **Chart-modify intents — three new verb tools, not param-expansion.** Separates read from write semantics; prompt-tunes per intent; cheap to add more verbs later. Costs ~100 tokens per turn in tool descriptions — worth it.
- **Production posture — local-demo-only, mock in Railway.** TradingView Desktop is a local Electron app; Railway containers cannot run it. `USE_MOCK_TA=1` in Railway env. The wedge is gated to in-person demos and dev laptops until containerised TV ships (v2). This single fact appears in four doc surfaces (proposal §3, `.env.example`, README "Production posture", CLAUDE.md Charts row).

**Assumptions introduced:**

- `mcp>=1.0` Python SDK is stable enough to depend on. Worth re-checking at the next live-apply pass — if PyPI is still <1.0, pin to a specific minor.
- `tradesdontlie/tradingview-mcp::capture_screenshot` returns base64-encoded PNG in a `data` or `base64` field of its result. Defensive parsing handles both; if upstream uses a different shape, the screenshot will be empty and the chart falls back to the inline SVG (graceful).
- `data_get_study_values` returns either a scalar `value` or a series whose last element is the current. Both shapes handled defensively.
- TradingView Desktop with CDP on port 9222 is reachable from the spawned MCP subprocess on the same machine — untested end-to-end this session; needs a developer-laptop run with TV Desktop open + the sibling repo cloned.

**Did NOT do:**

- Did NOT run the actual P1.1 filled-order verification — market hours.
- Did NOT exercise the real TradingView MCP path end-to-end — stayed at proposal-and-code level. First integration test is the next session.
- Did NOT fix the silent-fallback bug in `backend/tools/market.py::get_quote` (surfaced this session by Yahoo's crumb 401 — `yfinance.info` raised `TypeError`, the path caught it silently and returned mock with no `error` field). Queued as **Proposal 003** for next session.
- Did NOT wire the widget numeric validator — still deferred per CLAUDE.md trust principle #3 (decision unchanged from 2026-05-20 prompt-fix session).

**Next session:**

- **P1.1:** run the runbook at next US market open to verify `filled → live_trade`; update this log + tick the P1.1 checkbox in `PRIORITIES.md`.
- **P1.2:** first end-to-end real run on a dev machine with TV Desktop + the sibling repo cloned. Verify each of the four acceptance prompts (show NVDA daily / add RSI / draw S/R / scroll to date) returns a real screenshot in the `ta_chart` widget.
- **Proposal 003:** draft the `get_quote` silent-fallback fix (small change — surface `error: "yfinance_fetch_failed"` instead of falling through to mock).
- **Supabase planning:** the question "how was Supabase initially implemented + how is it intended to be used overall after the priority list" was paused mid-session in plan mode; resume.

