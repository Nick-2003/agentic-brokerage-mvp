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

- TradingView MCP is **in MVP** (initially almost punted to v2 — Nicholas corrected; this is the defensible wedge so it must be in)
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

- Nicholas asked about building on Nous Research's Hermes Agent (hermes-agent.nousresearch.com).
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

**Note:** `uv sync` still failing to complete on a very slow network (40+ min). Not a blocker — MORNING_BRIEFING.md tells Nicholas to re-run `UV_HTTP_TIMEOUT=300 uv sync` if the venv is empty.

---

## 2026-05-20 (later still) · Backend LIVE — first real Claude smoke test

**Done:**

- `uv sync` finally succeeded after moving `supabase` + `posthog` into an optional `auth` dependency group. They pulled in `cryptography` (7.6 MB wheel) which kept timing out on the slow network, and neither is imported by backend code yet (auth deferred). Install later with `uv sync --group auth`.
- Anthropic API key received from Nicholas, written to `backend/.env` (gitignored — verified with `git check-ignore`).
- Backend booted: `/healthz` returns ok, 15 tools registered, `anthropic_key_present: true`.
- **First real end-to-end Claude smoke test PASSED.** `POST /api/chat` with "give me a tldr on my portfolio" → streamed `thought → tool_call → tool_result` (4 tools: get_portfolio, get_quote, get_company_news, get_macro_snapshot) → terminal response → `done` in ~19s, 3 iterations. SSE pipeline, agent loop, tool execution all work with the real API.

**Two prompt-tuning issues found (NOT architecture bugs — Phase 2 polish):**

1. **Agent emitted a plain markdown `message`, not a `morning_brief` widget.** For "tldr on my portfolio" Claude answered with a markdown table instead of the widget JSON. Fix: strengthen the system prompt / add a few-shot example so portfolio/research/brief intents reliably produce widget JSON. Consider: detect intent server-side and inject "respond with a {widget_type} widget" into the turn.
2. **Data quality — wrong "current" prices in the output.** The agent showed NVDA current price as $220.61 (mock quote is $942.50); other tickers similarly off. The agent mis-transcribed / mis-computed numbers from tool results despite trust principle #3. Fix: tighten the system prompt's "no number without source" rule; possibly have the widget schema reference tool-call IDs so a validator can catch number drift (the validator described in CLAUDE.md isn't built yet).

**Model note:** running `claude-opus-4-5` (the `.env` default). Worked fine. Nicholas can bump to a newer Opus alias if desired.

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
- The hand-tuned mock market data (`USE_MOCK_MARKET=1`) is the better choice for a deterministic demo that matches the validated demo HTML ($942.50 etc.). Real yfinance returns messy pre-market quotes with wide spreads (NVDA ask 235.79 / bid 208.56). Recommendation to Nicholas — his call.

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
- **Demo portfolio is now TSLA + NVDA + TCEHY** (Nicholas' request — these three "always in the portfolio"). Rewrote `MOCK_PORTFOLIO` (equity $51,000.00, all three positions green). Added `TCEHY` (Tencent ADR) to `MOCK_QUOTES` + `MOCK_NEWS` (market.py) and full research coverage to `RESEARCH` (research.py) — so quote / chart / research / news / risk all work for Tencent. Updated the frontend Hero header to match ($51,000.00 / +$964.10).
- **New `USE_MOCK_BROKER` env toggle** — sibling of `USE_MOCK_MARKET`. When `=1`, `get_portfolio` and all execution tools use their mock paths even though Alpaca keys are configured. Needed because Tencent is not tradeable on Alpaca (US-equities only) — a coherent TCEHY-holding demo must run on the mock broker. Demo run command is now `USE_MOCK_MARKET=1 USE_MOCK_BROKER=1 uvicorn ...`.

**Decisions surfaced:**

- **Tencent ticker = `TCEHY`** (US ADR), not `0700.HK` — the app is US-equity framed ("holds US equities" in the system prompt) and ADR keeps quotes/yfinance consistent. Easy to switch if Nicholas wants the HK line.
- The frontend "Portfolio value" header (`app/page.tsx` Hero) is still a **hardcoded** number — now consistent with the mock portfolio, but not live. Wiring it to a real `get_portfolio` call needs a small REST endpoint or an on-mount fetch — deferred, offered to Nicholas.
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

---

## 2026-05-28 · Proposals 003–008 applied · P2b FMP research live · P1.1 blocked · active front → P2b

**Built / applied (proposals 003–008 — all drafted by claude through `proposed_changes/`, applied by Nicholas):**

- **003 + 004 applied** — the two `market.py` mock-data fixes (`get_quote` surfaces `yfinance_fetch_failed` instead of silently masking yfinance exceptions; `MOCK_NEWS` → `_NEWS_TEMPLATES` with per-call timestamps so `since=filled_at` can match) and the `technicals.py` `_branch` coroutine-leak fix (delete the helper, inline `if _use_mock_ta()`). 003 verified **12/12** via a deterministic test now saved at `scripts/test_P1_003.py`. Both applied with inert user-added commented-out stubs at EOF (`get_news_by_sector`, `get_sector_exposure`) — not part of the proposals.
- **005 drafted + applied** — `execution.py` mock path back-dates `filled_at` by `MOCK_FILL_BACKDATE_MINUTES` (default 30) so the `news_since_fill` window `[filled_at, now]` is non-empty in the immediate post-fill turn. Completes the SCOPE-flow-5 "since you bought" mock demo end-to-end (**001 + 003 + 005** chain). Real Alpaca path untouched.
- **006 drafted + applied + VERIFIED — P2b: real research data via Financial Modeling Prep.** New `backend/fmp_client.py` (httpx REST on FMP's `/stable/` API; `USE_MOCK_RESEARCH` + `FMP_API_KEY` gating; `fmp_fetch_failed` on error — no silent mock; defensive `_pick(...)` field mapping). Real paths added to all four research tools (`get_company_fundamentals`, `get_consensus_targets`, `get_full_research`, `get_peer_set`); the hand-tuned 7-ticker mock preserved. **`get_full_research` real path returns raw facts** (`needs_synthesis:true`, no thesis/catalysts/risks strings) and the agent synthesises the narrative — new `system.md` section "Research cards — synthesise the narrative when the data is real". `.env.example` + `scripts/fmp_probe.py` (field-name confirmation helper). No pyproject/widget/frontend change. **Verified:** claude ran the key-independent layers (imports/18 tools, mock-first preserved, gating, `fmp_fetch_failed` on all 4 tools); Nicholas ran Layers 1–3 — a live `analyze AAPL` fired `get_full_research` → real FMP data (BUY, target $324, P/E 37.31x, PEG 1.29, margins 47.86%/32.64%, 70 buy / 7 sell / 110 analysts) → `research_card` with an agent-synthesised, fully-cited thesis. P2b's core works.
- **007 applied** — latent import bug in the applied 002 `technicals.py`: `from backend.mcp_client` (×4) but `backend/` is **not** a package → `ModuleNotFoundError: No module named 'backend'`. Invisible in mock mode (lazy imports), but would crash the real TradingView path (`USE_MOCK_TA=0`). Fixed to `from mcp_client`. Found while determining 006's import path.
- **008 applied** — `research_card` price fix, found in 006's Layer-3 run: `analyze AAPL` returned `current_price: null` (yfinance crumb-401 → 003 surfaced the error → agent honestly nulled the price), which **crashes `ResearchCard.tsx`** (`null.toFixed()` / divide-by-null) in the browser. Fix: expose FMP `profile.price` as `current_price` in `get_full_research`/`get_company_fundamentals` real paths (a price source independent of the broken yfinance) + null-guard the renderer + `current_price: number | null` in `widgets.ts` and a note in `widget_contract.md`.

**P1.1 — `filled → live_trade`:**

- The **`accepted` (unfilled) branch is verified against real Alpaca** — a confirmation-style prompt placed a real order (real Alpaca UUID, not `mock_…`) and the agent emitted the honest markdown "working/queued" reply per the system-prompt rule. (An earlier run had hit the mock broker because `USE_MOCK_BROKER=1` was set in the shell env — confirmed via `/healthz` `alpaca_configured:true`.)
- The **`filled → live_trade` branch is now BLOCKED** — Nicholas' Alpaca paper account is closed; no fill is possible until it's reopened. Parked (not just pending) in `PRIORITIES.md`.

**Decisions surfaced:**

- **Active front pivoted P4 → P2b.** With P1.1 blocked on the Alpaca account, the build focus moved to P2b (real research data). P4 (auth → persistence + RLS) remains the next track, P4.1 Supabase magic-link auth first (it gates persistence/RLS/memory).
- **The earlier "how was Supabase initially implemented" scoping exercise is cancelled** — superseded by the decision to implement P4 directly when its turn comes.
- **Research provider = FMP** (US MVP default). Nicholas expanded the P2b decision matrix in `PRIORITIES.md` (12 candidates; `DECISION_p2_data_sources.md`) — FMP is the only contender exposing analyst consensus on a free tier; Twelve Data / EODHD are the HK-capable alternatives parked for P7-HK.
- **`get_full_research` real path returns raw facts; the agent writes the thesis.** FMP has no pre-written thesis. Keeps trust principle #3 intact — agent composes *prose*, never *numbers*. (Pattern from `docs/TRUENORTH_MCP_INTEGRATION.md` §7.)
- **Import convention locked (codebase-wide):** `backend/` is not a package; the app runs `uvicorn main:app` with `backend/` on `sys.path` (dev *and* Docker/Railway). Sibling modules import top-level — `from mcp_client import`, `from fmp_client import` — **never** `from backend.X` (verified: that raises `ModuleNotFoundError`). 007 fixed the one violation.
- **`proposed_changes/applied/` archival convention** — on apply, the proposal's `README.md` is renamed `README-<slug>.md`, moved to `applied/`, and the rest of the folder deleted (the code is live). **Nicholas keeps this manual.**

**Assumptions introduced:**

- **FMP "stable" API field names** are coded defensively (`_pick` with candidate keys) because the docs 403-block automated fetch and no key was available at draft time. `scripts/fmp_probe.py` confirms them on first live run; tighten `_pick` lists if anything maps to `None`.
- **FMP free tier = ~87 sample symbols (AAPL/TSLA/AMZN…) + 250 calls/day.** Consensus/targets for arbitrary tickers (e.g. CRM) need **FMP Starter (~$22/mo)** — on the free tier a non-sample symbol 403s the consensus calls → `fmp_fetch_failed` (correct, not a bug).
- **FMP `profile.price` is acceptable as the research-card `current_price`** — a recent close, not a live tick; fine as a context anchor for a research card.
- **mock-fill back-date (30 min)** must exceed the largest recent-tier `minutes_ago` in `_NEWS_TEMPLATES` (currently 28). Documented cross-file coupling.

**A theme this session — bugs caught by trust-but-verify (each became a proposal):**

- yfinance silently masking failures with mock (Yahoo crumb-401) → **003**.
- `_branch` leaking an un-awaited coroutine → **004**.
- `from backend.mcp_client` crashing the real TV path → **007**.
- `current_price: null` crashing `ResearchCard.tsx` → **008**.

Every one was found by diffing applied vs proposed, running key-independent tests, or reading the actual widget output — not by the happy-path demo.

**Did NOT do:**

- Did NOT verify P1.1's `filled → live_trade` — Alpaca account closed (blocked).
- Did NOT run P1.2's real TradingView integration test — needs TV Desktop + the sibling `tradesdontlie/tradingview-mcp` clone on a dev machine. **007 now unblocks it** (the import no longer crashes).
- Did NOT verify FMP on a **non-sample** ticker (CRM / full universe) — needs FMP Starter.
- Did NOT start P4 (auth) — queued behind P2b.

**Next session:**

- **P2b:** verify a non-sample ticker (CRM) on FMP Starter; eyeball synthesised-thesis quality vs the hand-tuned mocks and tune `system.md` if the prose is thin.
- **P1.2:** first real TradingView integration test on a dev machine (007 applied → import safe).
- **P4.1:** Supabase magic-link auth — the next track, gates persistence/RLS/memory.
- **P1.1:** unblock + verify the `filled → live_trade` path when the Alpaca account is reopened.

---

## 2026-05-28 · P2b DONE — FMP research verified end-to-end (009 applied) · active front → P4.1

**Built / applied:**

- **Proposal 009 applied & verified** — FMP field-mapping fixes drafted *from the live `fmp_probe.py AAPL` output* (not guessed): (a) `fcf_margin_pct` had **no** FMP field at all → derived `freeCashFlowPerShareTTM ÷ revenuePerShareTTM × 100`; (b) `ev_sales` was **silently P/S** — `ratios-ttm` has no `evToSalesTTM`, so the old `_pick` fell through to `priceToSalesRatioTTM` and the card cited the wrong multiple → now derived true `EV/Sales = enterpriseValueMultipleTTM × ebitdaMarginTTM`, with P/S kept separately as `ps_ratio`; (c) `sec_filings` returned `[]` → added `from`/`to` date params + enhanced the probe to dump the raw response — confirmed **402 premium-gated** on the current FMP tier (filings stay an optional enrichment). Probe re-run confirmed: `fcf_margin 29.0`, `ev_sales 10.23 ≠ ps_ratio 10.13`, all other fields mapped.
- **P2b verified end-to-end (the milestone).** Tool level: all four research tools return real FMP data for AAPL (`is_mock:false`). Agent level: `analyze AAPL` → `research_card` with real BUY / target $324 / P/E 37.4 / PEG 1.29, **`current_price` $310.58 from FMP profile (008), coherent ~4% upside**, and an agent-synthesised thesis citing FMP numbers verbatim ("FCF margin 29.0%", "10.21× EV/Sales"). The whole **006 + 008 + 009** chain works against real FMP for any symbol the tier permits.
- **`scripts/test_P2_006.py`** — expanded the user's earlier 006-era test into a full P2b regression suite (006 real-data + raw-facts, 008 numeric `current_price`, 009 `fcf_margin` non-None + `ev_sales != ps_ratio`, consensus/peers sanity, mock-first gating). Anchors paths on `__file__` and loads `backend/.env` explicitly (fixing the dotenv-resolution gotcha). Harness validated 4/4 in forced-mock mode (no FMP calls); the live section runs when `FMP_API_KEY` is set.
- **Updated `API_CONTRACT.md` to properly reflect real Python contract** - Contract now matches Python backend information more accurately
  - All 8 widget schemas with current fields — including `001's news_since_fill?` on `live_trade` and `008's current_price: number | null` on `research_card` (with the "render `—`, don't fabricate" note).
  - The 7 SSE events with real payloads (`tool_result` summary format, parallel tool calls share `id`, exactly one terminal `widget` or `message` then `done`).
  - SSE framing gotchas that actually bit this project — `\r\n\r\n` frame separation (the `sse.ts` bug) and the `: ping` keep-alive comments — written down so the brother doesn't rediscover them.
  - Tool-level failures (`alpaca_fetch_failed` / `yfinance_fetch_failed` / `fmp_fetch_failed` / `tradingview_mcp_unreachable`) are not stream errors — they ride inside `tool_result` / the tool's error field, and the agent surfaces them honestly.
  - healthz real shape (`ok`, `model`, `tools_registered` = 18, `alpaca_configured`, `anthropic_key_present`).
  - Planned-not-built section so the HTTP boundary is unambiguous: auth (P4.1) and `/api/conversations` (P4.2) are flagged as coming, not present.

**Decisions surfaced:**

- **P2b is DONE.** Real research data flows for any FMP-tier-permitted symbol; the implementation is **symbol-agnostic**. The only limits are FMP *billing*, not code: free tier = ~87 sample US symbols (AAPL/AMZN/TSLA…); arbitrary names (CRM) need **FMP Starter (~$22/mo)**; SEC filings are premium-gated.
- **Active front moves to P4** (auth → persistence + RLS). P4.1 Supabase magic-link auth first — it produces the real `user_id` that P4.2/P4.3 key off. P1.2's first real TradingView integration test is also unblocked (007), but not the focus. P1.1 stays parked on the Alpaca account.
- **ev_sales mislabel is the kind of bug only verification catches** — the probe's `⚠ None` flag only caught `fcf_margin`; `ev_sales` reported "all mapped" because P/S quietly filled it. Reading the *raw* `ratios-ttm` keys (no `evToSalesTTM`) is what exposed it.

**Assumptions / notes:**

- `fcf_margin` and `ev_sales` are deterministic data-layer derivations from FMP-sourced fields (same class as the existing `×100`), not agent-side number invention — trust principle #3 holds.
- The mixed-mode upside artifact (mock quote vs real FMP target) is avoided by running market + research on the same mode; with 008, real-market mode + yfinance-down still yields a coherent price from FMP profile.

**Did NOT do:**

- Did NOT verify a non-sample ticker (CRM) — needs FMP Starter (free tier 402s consensus for non-sample names; not a code gap).
- Did NOT fix the minor `sources` nit — `_fmp_full_research` cites "SEC EDGAR — recent filings" even when filings are `[]` (402). Logged as candidate **Proposal 010** (drop the source entry when empty); not drafted.
- Did NOT run `test_P2_006.py` against the live key (burns quota; P2b already manually verified this session).

**Next session:**

- **P4.1:** Supabase magic-link auth — backend JWT verification → real `user_id`; frontend magic-link login + token in `lib/sse.ts`. `uv sync --group auth` to install the client.
- **P1.2:** first real TradingView integration test on a dev machine (TV Desktop + sibling clone; 007 makes the import safe).
- **P1.1:** unblock + verify `filled → live_trade` when the Alpaca account reopens.
- Optional: Proposal 010 (empty-filings source nit); FMP Starter → verify CRM.

---

## 2026-05-29 (later) · P1.1 DONE — `live_trade` verified · Proposal 010 · all six flows real

**What happened:**

- **Alpaca paper account restored**; the resting **2× F @ $15.85** runbook order **filled** (F since ~$16.70).
- **`live_trade` widget verified.** A `"show me my F position as a live trade card"` prompt routed `get_open_position` → a real **`live_trade` widget**: `long 2 F, fill $15.85, current ~$16.70, +$1.70 (+5.36%)`, Alpaca-sourced. Both rendering paths now confirmed against the **real** book: `morning_brief` (portfolio overview) *and* the `live_trade` card. **P1.1 is DONE** — and with it, **all six SCOPE flows are real.**
- **Routing needed no `system.md` change** — the agent mapped "monitor my position" → `get_open_position` → `live_trade` on its own (steered by `get_open_position`'s tool description). The earlier "candidate 010 routing rule" was therefore *not* needed.

**Proposal 010 — drafted + applied (`live_trade` `order_id`/`filled_at` optional):**

- The monitoring `live_trade` (from `get_open_position`) **omits `order_id`/`filled_at`** — real Alpaca returns a *position*, not its originating order, so those order-level fields legitimately don't exist when monitoring. Both were marked **required** in `widget_contract.md` + non-optional in `widgets.ts`. `LiveTrade.tsx` never reads them, so this was **contract drift, not a crash.** Fix: mark both optional in the contract + TS type (2 files). Applied + confirmed (`order_id?`/`filled_at?` in `widgets.ts`; optional note in `widget_contract.md`).
- **Clarification logged:** the recurring `KeyError: 'order_id'` was my **throwaway chat parser** doing `d['order_id']`, never a repo file — 010 couldn't "fix" it. The widget is correct; the parser just needed `.get()`. The `live_trade` check is now a **saved regression test in `scripts/`** (added by Nicholas) so verification no longer leans on the ad-hoc snippet.

**Decisions / notes:**

- **Candidate Proposal 011** (re-tagged from the earlier "candidate 010"): `_fmp_full_research` lists the `"SEC EDGAR — recent filings"` source even when `recent_filings` is `[]` (402 premium-gated) — drop the entry when empty. One-file (`research.py`). Not drafted.
- Proposals **001–010 applied**; **011** candidate.

**Next session:**

- **P4.1** (Supabase magic-link auth) — the active build front. `uv sync --group auth`; backend JWT → real `user_id` (kills `"demo"`); frontend magic-link + token in `lib/sse.ts`. Gates P4.2 persistence/RLS + P4.3 memory.
- Optional: Proposal 011 (empty-filings source nit); FMP Starter → verify a non-sample ticker (CRM); P1.2 first real TradingView run on a dev machine.

---

## 2026-05-29 (later) · P1.1 UNBLOCKED — real Alpaca fill renders in-app

**What happened:**

- **Alpaca paper account is back** and **a real paper order filled.** The resting **2× F @ $15.85** limit from the P1.1 runbook executed; F later traded up (~$16.68).
- **It renders in the app.** A `morning_brief` (real broker, *not* mock) shows the position: 2 F at **$15.85** avg cost, **~$33.36** value, **+$1.66** unrealized P&L, **~$99,968** cash, **~$100,002** book. Every figure is coherent and Alpaca-sourced — no fabrication (arithmetic checks: $31.70 basis; $33.36 − $31.70 = $1.66 P&L; $100k − $31.70 cash).

**What this verifies for P1.1:**

- ✅ Account works end-to-end; a marketable limit actually fills.
- ✅ **Real fill price + live P&L flow through `get_portfolio`/`get_open_position`** — the exact data path the `live_trade` widget depends on.
- ✅ The "seed a position for the portfolio demo" goal — the 2× F is kept as the live-Alpaca demo holding.
- ✅ Bonus: the `morning_brief` flow is now proven against a **real** Alpaca book, not just the `USE_MOCK_BROKER=1` mock (TSLA/NVDA/TCEHY).
- (Already had: the `accepted`/working branch + Bug C honesty, 2026-05-27.)

**Still strictly open (a formality):** the `live_trade` **widget** itself — the synchronous `place_paper_order(filled) → get_open_position → widget(live_trade)` SSE sequence. The screenshot is the `morning_brief` (portfolio-overview) path, which reads the same Alpaca position, so the data is proven; only the specific widget emission hasn't been shown.

**Next session:**

- **P1.1 close-out:** ask *"show me my F trade"* (→ `get_open_position` → `live_trade`) or place a fresh marketable order during RTH → confirm the `live_trade` widget renders with the real fill + P&L. Then P1.1 is fully done.
- **P4.1** (Supabase auth) remains the active build front.

**Close-out (same day):** ✅ **P1.1 DONE.** `"show me my F position as a live trade card"` → `get_open_position` → a real **`live_trade` widget** (`long 2 F, fill $15.85, current $16.68, +$1.66 / +5.24%`, Alpaca-sourced). The "monitor → live_trade" routing needed **no** `system.md` change — the agent did it unprompted (steered by `get_open_position`'s tool description). The close-out also surfaced one contract nit: the monitoring `live_trade` omits `order_id`/`filled_at` (real `get_open_position` returns a position, not its order; both were marked required). Renderer tolerates it (`LiveTrade.tsx` doesn't read them) — drafted as **Proposal 010** (make both optional). With this, **all six SCOPE flows are real**; remaining work is the launch track (P4 auth/persistence + P5 lockdown). Also re-tagged the empty-filings SEC-EDGAR source nit as candidate **011**.

---

## 2026-05-29 (later) · P4.1 drafted — Proposal 012 (Supabase magic-link auth)

**Built (drafted through `proposed_changes/`, awaiting Nicholas' review/apply):**

- **Proposal 012 — Supabase magic-link auth (P4.1).** The active-front task. Closes SECURITY_AUDIT **HIGH-2** (spoofable `"demo"`); produces the real `user_id` that P4.2 persistence/RLS and P4.3 Mem0 key off. 10 files + a regression test, mirrored under `proposed_changes/012-supabase-magic-link-auth/`.
- **Backend:** new `backend/auth.py` — verifies the Supabase JWT **offline (HS256, PyJWT + `SUPABASE_JWT_SECRET`)** and returns the token `sub` (UUID) as the trusted `user_id`. `main.py` injects it via `Depends(resolve_user_id)`, **removes the client-supplied `ChatRequest.user_id`** (identity now comes from the signed token, never the body), and adds `require_auth`/`auth_configured` to `/healthz`. `pyproject.toml` declares `pyjwt>=2.9` in main deps (pure-Python for HS256 — does **not** pull `cryptography`; the heavier supabase client stays in the `auth` group for P4.2). `.env.example` gains `SUPABASE_JWT_SECRET` + `REQUIRE_AUTH`.
- **Frontend:** new `lib/supabase.ts` (client singleton + `getAccessToken`/`signOut`, graceful no-op when unconfigured → demo mode), new `components/AuthGate.tsx` (magic-link login screen + `useAuth()`), `lib/sse.ts` attaches `Authorization: Bearer <jwt>` (and shows a friendly 401 message), `app/page.tsx` wraps in `<AuthGate>`, makes `handleSubmit` async to fetch the token, and adds a header sign-out.

**Decisions surfaced (confirmed with Nicholas this session):**

- **JWT verification = local HS256** (PyJWT + `SUPABASE_JWT_SECRET`), **not** `supabase.auth.get_user()`. Lighter (no supabase install for P4.1 — PyJWT already in the venv), faster (no per-turn round-trip), canonical FastAPI+Supabase pattern, offline-testable. Assumes the project's **default symmetric JWT secret**; if it ever moves to asymmetric (ES256/RS256) signing keys, swap `verify_jwt` to a JWKS fetch (flagged in the proposal).
- **Enforcement = flag-gated `REQUIRE_AUTH`**, mirroring the `USE_MOCK_*` kill-switches — **not** a hard cutover. `REQUIRE_AUTH=0` (local): a Bearer token is still verified if sent, but a token-less request falls back to `"demo"` so the deterministic mock demo / `smoke_test.sh` / curl checks keep working. `REQUIRE_AUTH=1` (Railway): unauthenticated `POST /api/chat` → **401**. A *provided-but-invalid* token is **always** 401 (never downgraded to demo); a token with no secret configured → **500** `auth_not_configured` (surfaced, not silently passed — same honesty discipline as `alpaca_fetch_failed`).

**Verified pre-apply (no Supabase account / no network):**

- `py_compile` of `auth.py` + `main.py` ✓.
- `scripts/test_P4_012_auth.py` vs the proposed `auth.py` → **18/18** (valid/expired/bad-sig/missing-aud tokens; bearer extraction incl. case + malformed; REQUIRE_AUTH on/off policy; garbage-token-never-demo; no-secret→500).
- Frontend supabase-js API surface typechecked clean against the project `tsconfig` (throwaway file exercising `createClient`/`getSession`/`signInWithOtp`/`onAuthStateChange`/`signOut`/`Session.user.email`).

**Assumptions / notes:**

- `pyjwt` showed as present in the venv (2.12.1) but isn't a declared backend dep — 012 declares it so `uv sync` keeps it.
- CORS already permits the new `Authorization` header: `allow_headers=["*"]` echoes requested headers on preflight even with `allow_credentials=True` (to confirm on the post-apply frontend run).
- **Pre-existing, unrelated:** `pnpm typecheck` surfaces 3 `ResearchCard.tsx` "`current_price` possibly null" errors — a proposal-008 renderer-guard leftover, **not** introduced by 012 (which doesn't touch that file). Flagged for a separate fix.

**Did NOT do:**

- Did NOT apply 012 to the live repo (Nicholas applies + archives manually) or touch live `backend/`/`frontend/` files.
- Did NOT update `API_CONTRACT.md` — it's a reference doc, but it would describe unbuilt behaviour until 012 is applied (the exact problem P3 fixed). Listed in the proposal's "on-apply" checklist instead.
- Did NOT start P4.2/P4.3 — they depend on 012 landing.

**Follow-up (same session) — Nicholas applied 012, hit a hydration bug → Proposal 013 drafted.** 012 applied; `test_P4_012_auth.py` 18/18; magic-link login verified in-app. But the browser logged a React **hydration mismatch** in `AuthGate.tsx`: `getSupabase()` is client-only (`typeof window` branch), so the server rendered `children` (`authConfigured()` false) while the client's first paint rendered `<Splash>` (`authConfigured()` true) — server text ≠ client text. **Proposal 013** (`013-authgate-hydration-fix/`, one file) fixes it with the canonical `mounted`-flag pattern: defer the gate decision to after mount so the server and first client render emit an identical `<Splash>`, then the real gate (children/login) appears. Fixed file typechecks clean (project: 0 errors — the earlier pre-existing `ResearchCard.tsx` null-guard errors are also resolved now). Awaiting Nicholas' apply of 013.

**Follow-up 2 — 013 applied (hydration gone), then the email rate limit locked Nicholas out → Proposal 014 drafted.** After applying 013 the hydration warning was gone, but testing login repeatedly tripped Supabase's built-in email cap (429 / "email rate limit exceeded", ~a few magic links/hour). Because the login gate is frontend-only and no session ever got stored, every restart dropped Nicholas back on the login page with no way through (can't "sign out" of a session you never had). Diagnosis also surfaced a still-open backend question: when signed in, an authed `/api/chat` call may 401 — confirmed `auth.verify_jwt` returns `401 invalid_token` for *both* an asymmetric (ES256) token **and** a wrong-secret HS256 token, i.e. "login works but every authed call fails" ⇒ either the project signs JWTs with asymmetric **JWT Signing Keys** (→ needs JWKS verification, the 012 follow-up) or `SUPABASE_JWT_SECRET` is wrong. Left for Nicholas to confirm via the token `alg` + the 401 `detail` (demo mode sends no token, so it sidesteps this for now).

- **Proposal 014** (`014-login-demo-mode/`, one file, supersedes 013's `AuthGate.tsx`): a **dev-only** ("Continue in demo mode") escape hatch on the login screen so a developer isn't locked out when email is rate-limited. `NODE_ENV==='development'`-gated (compiled out of prod builds), **persisted in localStorage** (survives restarts), with a fixed "Demo mode · exit" pill to return to login. Frontend-gate only — the backend still enforces `REQUIRE_AUTH` independently (prod: demo sends no token → every `/api/chat` 401s), so it's a convenience, not a bypass. Typechecks clean (project: 0 errors). Awaiting Nicholas' apply.

**Follow-up 3 — 014 applied; root-caused the authed-401 to ES256 signing keys → Proposal 015 drafted.** With demo mode unblocking the dev loop, ran the sign-in-free JWKS check: `GET <SUPABASE_URL>/auth/v1/.well-known/jwks.json` returned a key with `"kty":"EC","crv":"P-256","alg":"ES256"` — the project signs JWTs with **asymmetric "JWT Signing Keys"**, so 012's HS256-only `verify_jwt` rejected every token (`invalid_token` → 401: "login works, every authed call fails"). Exactly the 012 follow-up.

- **Proposal 015** (`015-jwks-asymmetric-jwt/`, supersedes 012's `auth.py`/`pyproject.toml`/`.env.example` + new `scripts/test_P4_015_jwks.py`): `verify_jwt` now **dispatches on the token's header `alg`** — `ES256/RS256/EdDSA` → verified against the project's published **JWKS public key** for the token's `kid` (`PyJWKClient`, cached after first fetch, runs in FastAPI's threadpool since the dep is sync; sends the anon key as the `apikey` header); `HS256` → the legacy secret (kept); `none`/other → `401 unsupported_alg` (blocks alg-none). No alg-confusion (different key material + per-alg allowlist). `auth_configured()` now also true via `SUPABASE_URL`. New details `503 jwks_unavailable` / `401 unsupported_alg`. **`pyjwt`→`pyjwt[crypto]`** (needs `cryptography` for ES256 — the 012 "avoid cryptography" rationale no longer applies; `cryptography 48.0.0` already in the venv). Verified: 015 ES256 round-trip test **10/10** (real EC P-256 key, JWKS client stubbed → no network), and the applied 012 HS256 test still **18/18** against the new `auth.py`. Awaiting Nicholas' apply (`uv sync` to pull `pyjwt[crypto]`; then a signed-in portfolio call should 200, not 401). This unblocks P4.1 → P4.2.
- Also clarified for Nicholas: "localhost:3000 goes straight into the app even with `REQUIRE_AUTH=1`" is **not a bug** — `REQUIRE_AUTH` is backend-only (the frontend can't see it); the app loads via the 014 demo override (persisted `ab-demo-mode` flag → click the "Demo mode · exit" pill to clear) or because `NEXT_PUBLIC_SUPABASE_*` is still commented out in `.env.local` (→ demo-by-no-env; un-comment + restart). With `REQUIRE_AUTH=1` the app loads but authed calls 401 until 015 lands + a real sign-in.

**Follow-up 4 — 015 applied, signed-in portfolio 200, P4.1 ✅ → Proposal 016 drafted for P4.2.** Nicholas applied 015, signed in, the authenticated `/api/chat` portfolio call returns 200, both saved tests (012 HS256 18/18, 015 ES256 10/10) clean. **P4.1 is closed**; moving to **P4.2 — Supabase persistence + RLS** on the same Supabase project.

- **Proposal 016** (`016-supabase-persistence-rls/`): 7 files + a unit test.
  - **Backend:** `auth.py` (additive: `AuthCtx(user_id, token)` + `resolve_auth`; `resolve_user_id` kept for back-compat). New `db.py` — **async**, **user-scoped**: `acreate_client(URL, ANON_KEY)` then `client.postgrest.auth(user_jwt)` so every PostgREST request rides on the user's JWT and RLS (`auth.uid() = user_id`) physically isolates rows. The **Supabase service key is never used** in `db.py` (RLS-bypassing; reserved for admin tasks). New `db/schema.sql` — 4 tables (`conversations`, `messages`, `pinned_widgets`, `user_profiles`), all RLS-on with `for all to authenticated using/with check (auth.uid() = user_id)`, plus a `set_updated_at` trigger. `main.py` wires persistence into `/api/chat` (pre-stream: get-or-create conversation + persist user message; post-stream: persist assistant accumulated widgets+text), emits a new `conversation` SSE event with `{id, title}` so the frontend can capture and echo the id, adds `GET /api/conversations` (RLS-filtered list) + `GET /api/conversations/{id}` (messages), and `/healthz.persistence_configured`. Demo mode (`auth.token is None`) **skips persistence entirely** — no DB writes, no DB reads.
  - **pyproject.toml:** `supabase` moves from `auth` group → **main deps**, pinned **`>=2.10.0,<2.28.0`**. **Discovery this session:** `supabase 2.28+` pulls `pyiceberg` via storage3 (Apache Iceberg/Arrow stack), which fails to build on macOS/py3.14 — `uv sync --group auth` with `supabase 2.30` reproducibly errors on the pyiceberg wheel build. Verified `2.10`–`2.25` install clean; bump the cap when storage3 decouples the iceberg extra.
  - **Frontend:** `sse.ts` adds the `conversation` event variant + optional `conversation_id` on `ChatRequest`; `page.tsx` extracts a `ChatScreen` inner component, tracks `conversationId` in state, echoes it on subsequent turns, and **clears it (+ the turn history) when the signed-in user changes** (incl. sign-out → null) so one user's id can't be reused under another.
  - **Verified pre-apply (no live Supabase needed):** `py_compile` clean; new `scripts/test_P4_016_persistence.py` **17/17** (AuthCtx shape + frozen, `resolve_auth` policy in both modes, expired/garbage/back-compat, `persistence_configured` truth table, `db.py` callable surface, URL trailing-slash strip); **applied 012 HS256 18/18 + 015 ES256 10/10 still pass** against the new `auth.py`; frontend integrates cleanly — with the proposed `sse.ts`+`page.tsx` swapped in, the project typechecks at **0 errors** (live `sse.ts` restored after). **Supabase 2.10 AsyncClient surface verified live** in the venv: `acreate_client` → `AsyncClient` → `.postgrest.auth(jwt)` accepted; `.table().select().eq().order().limit()`/`.insert()`/`.update().eq()` builders typed as expected.
  - **Post-apply** is the actual P4.2 "done when" — runbook in the README: (a) `uv sync` + paste `db/schema.sql` into the Supabase SQL Editor; (b) sign in as A, chat, restart backend, `GET /api/conversations` still returns A's conversation; (c) **two-account isolation** — sign in as B, B sees only B's data, `GET /api/conversations/{A's-id}` returns 404. The two-account test is the hard pre-launch gate from `SECURITY_AUDIT`/PRIORITIES_EXPLAINED.
- **What's intentionally NOT in 016:** conversation-history sidebar UI (backend persists + curl-verifiable; UI = follow-up), persisting pinned widgets (table exists with RLS, frontend pin state stays local), service-key admin endpoints (reserved). All flagged as clean opt-ins in the README.

**Next session:** Apply 016 (`uv sync`, paste schema, set the runbook in motion). Then P4.3 (Mem0) and P4.4 (Langfuse) can fan out — both consume the same authenticated `user_id` 015/016 establish. Pin-widget persistence + conversation-history UI are post-P4.3/4 polish.

---

## 2026-06-01 · 016 applied — **P4.2 VERIFIED ✅ — SECURITY_AUDIT HIGH-2 cleared**

**What landed:** Nicholas applied proposal 016 — `backend/auth.py` (AuthCtx + resolve_auth), new `backend/db.py` (async user-scoped persistence), new `backend/db/schema.sql` (4 RLS-protected tables + policies + trigger), `backend/main.py` (persistence wiring + `conversation` SSE event + 2 new GET routes + `/healthz.persistence_configured`), `backend/pyproject.toml` (`supabase>=2.10.0,<2.28.0` moved to main deps), `frontend/{lib/sse.ts, app/page.tsx}` (conversation_id round-trip). `scripts/test_P4_016_persistence.py` moved to repo scripts/.

**Two operational gotchas surfaced during apply and resolved:**

1. **First `uv sync` ripped supabase out of the venv.** Nicholas had applied 016's code files but not yet `pyproject.toml`, so `uv sync` reconciled against the *old* pyproject (supabase only in the `auth` group, not main deps) and uninstalled the supabase 2.10 stack (gotrue, postgrest, storage3, realtime, supafunc, deprecation, h2*, hyperframe) that had been transiently installed during my pre-apply verification. Backend then failed to boot on `import supabase`. **Fix:** apply pyproject *first*, then `uv sync` installs the supabase deps as declared. *Lesson:* on any proposal carrying a pyproject delta, apply ordering matters — fold a note into `proposed_changes/README.md` on the next workflow pass.
2. **`db/schema.sql` failed with `ERROR: 42703: column "user_id" does not exist`.** Nicholas initially ran it in the **wrong Supabase project** — the brother's `Finance_Chatbot` Node project on the same workspace, which had pre-existing tables `conversations`/`messages`/etc. with different column conventions. `create table if not exists` was a no-op against the existing rows, then the `create index … on conversations(user_id, …)` (or the policy `auth.uid() = user_id`) errored because that column doesn't exist on the brother's tables. **Fix:** ran in the correct (agentic-brokerage) project — schema deployed clean. No cross-project rename needed.

**P4.2 verification — the actual HIGH-2 gate, ran end-to-end:**

Two real Supabase accounts on the project — User A `e09942bf-7d65-4134-9a9a-d26f4ac30cd8`, User B `a10c7d99-6894-47d3-b722-a2b55448e989`. Each signed in via magic link in a separate browser context (incognito for B → independent `localStorage`). Each drove their own chat turn so each had a real row in `conversations` + `messages`.

- **Step 4 — restart-survival.** Captured `/api/conversations` + `/api/conversations/$CID` baselines under TOKEN_A, `Ctrl-C`-ed uvicorn, relaunched, recaptured. **A's conversation + messages are byte-identical pre and post-restart** — proves data lives in Postgres, not process memory.
- **Step 5 — two-account RLS isolation.** Three orthogonal assertions, all green:
  1. B's `GET /api/conversations` returns **only B's** id (`["02899b72-…"]`) — A's CID absent.
  2. B's `GET /api/conversations/$CID` (A's id) → **HTTP 404** (RLS hides A's row from a foreign reader; backend reports `not_found`).
  3. A's own list still contains A's CID (no collateral damage from B's request).

Final assertion: **`P4.2 verified ✅`**. The per-user data isolation the SECURITY_AUDIT pinned as HIGH-2 is now load-tested under real RLS, against two real authed users, via two real JWTs verified through the JWKS path that 015 established.

**Tooling lesson — silent ✗ masquerading as ✓.** During an early pass of step 4, both `_before.json` and `_after.json` contained `{"detail":"token_expired"}` because TOKEN_A had aged out (Supabase default 1h TTL) mid-debug. The naïve pattern `diff <(cmd_a | jq '.x[]') <(cmd_b | jq '.x[]') && echo ✓` *passed* — because both jq invocations errored on null-deref and emitted nothing to stdout; `diff` of empty vs empty exit-0; the `&&` happily printed ✓. **Fix in the runbook going forward: `jq -e`** (treats empty/false/null as exit 1) so a broken pipe honestly fails the `&&`; and/or print the raw body before jq when a check fails. Folded into the user-facing verification scripts.

**State after this session:**

- ✅ **P4.1 — Supabase magic-link auth** (proposals 012 + 015 applied + verified — ES256 JWKS verification; signed-in `/api/chat` returns 200; `REQUIRE_AUTH` flag-gated demo path preserved).
- ✅ **P4.2 — persistence + RLS** (proposal 016 applied + verified — two-account isolation gate green; restart-survival green).
- ▶ **Next:** **P4.3 (Mem0 memory)** and **P4.4 (Langfuse observability)** — both consume the same authenticated `user_id` 012/015/016 establish; per PRIORITIES_EXPLAINED, they fan out in parallel post-P4.1/4.2, with P4.4 ranked higher leverage (unblocks debugging + the P7-LLM eval gate). Both brother-owned.

**Carry-forward (small, tracked):**

- `API_CONTRACT.md` — needs the 016 additions (new SSE event `conversation`, new routes `GET /api/conversations` + `GET /api/conversations/{id}`, optional `conversation_id` on `POST /api/chat`, `/healthz.persistence_configured`) and the 015 additions (`401 unsupported_alg`, `503 jwks_unavailable` details). Scoped — ~15 min.
- `CLAUDE.md` tech-stack table — Mem0 / Langfuse rows defer until P4.3 / P4.4 land.
- `proposed_changes/README.md` workflow doc — note "apply `pyproject.toml` before `uv sync`" gotcha.
- `proposed_changes/016-supabase-persistence-rls/` — Nicholas' manual archive to `applied/`.

**Did NOT do:**

- Did NOT start P4.3 / P4.4 — awaiting Nicholas' direction on which to begin.
- Did NOT update `API_CONTRACT.md` — flagged for next session, scoped change.

**Next session:**

- Apply 012: `cd backend && uv sync`; set `SUPABASE_JWT_SECRET` + `REQUIRE_AUTH` (`.env`) and `NEXT_PUBLIC_SUPABASE_*` (`.env.local`); allow the redirect origin in Supabase Auth settings. Run `scripts/test_P4_012_auth.py` (18/18) + a live magic-link login via `pnpm dev`, then flip `REQUIRE_AUTH=1` and confirm the 401. Update `API_CONTRACT.md`.
- Then **P4.2** (persistence + RLS) and **P4.3** (Mem0) — both keyed on the real `user_id` 012 produces.
- Still open/parked: P1.2 first real TradingView run (dev machine); fix the pre-existing `ResearchCard.tsx` null-guard; FMP Starter → verify a non-sample ticker; candidate Proposal 011.

---

## 2026-06-01 (later) · Reference-doc catchup + Proposal 017 drafted (P4.4 Langfuse)

Nicholas chose the following sequence: reference-doc catchup first, then **P4.4 (Langfuse) as 017**, then **P4.3 (Mem0) as 018**.

**(A) — reference docs updated directly (per the workflow; not via proposal):**

- **`API_CONTRACT.md` → v1.1 (2026-06-01).** §2 fully rewritten — Bearer JWT, ES256/HS256 alg-dispatch + JWKS, `REQUIRE_AUTH` flag-gated, full 401/500/503 `detail` tables (`authentication_required`, `token_expired`, `invalid_token`, `token_missing_sub`, `unsupported_alg`, `auth_not_configured`, `jwks_unavailable`). §5 `/healthz` gains `require_auth`, `auth_configured`, `persistence_configured`. §6 `POST /api/chat` — `Authorization` now required when signed in, `conversation_id` optional, `user_id` removed from body, new `conversation` SSE event documented + the "persistence failures are swallowed" rule. New **§6b** — `GET /api/conversations` + `GET /api/conversations/{id}` (RLS posture + 4-table schema sketch + which tables are wired vs not). §9 cleaned up — P4.1/P4.2 line items removed (done); P4.3/P4.4 + pinned-widget routes + history UI listed as remaining planned work.
- **`proposed_changes/README.md`** — added the **"Apply-order gotcha: `pyproject.toml` before `uv sync`"** section recording the 016-apply lesson (transient deps getting uninstalled when the pyproject delta isn't applied first), with the canonical 3-step apply order.

**(B) — Proposal 017 (P4.4 Langfuse observability) drafted.** 7 files + a unit test, in `proposed_changes/017-langfuse-observability/`.

- **Backend:** new `observability.py` — `Tracer` protocol, `_NoopTracer` singleton, `_LangfuseTracer` (Langfuse-backed), `trace_chat` async context manager (yields a tracer; in unconfigured / setup-failure modes yields `NOOP_TRACER` so the agent code path is identical), lazy import of `langfuse`. Built on Langfuse 4.7.1's OTel surface: `start_as_current_observation` for the root span, `propagate_attributes(user_id=, session_id=, tags=)` for trace-level attribution, `start_observation(...).end()` for child generations + tool spans. Found `propagate_attributes` by probing — it's the documented way to set `user.id`/`session.id` in Langfuse v3+/v4 (the `LangfuseOtelSpanAttributes.TRACE_USER_ID` constant is `'user.id'`).
- **Agent integration:** `agent.py` gains `tracer: Tracer = NOOP_TRACER` kwarg; per-iteration `tracer.record_generation(name='anthropic.iter_N', model=, input=messages_snapshot, output=_serialise_blocks(final_msg.content), usage_details={input, output}, metadata={iteration, latency_ms})`; per-tool `tracer.record_tool(name=, args=, result=, ok=, latency_ms=, metadata={tool_use_id})`; terminal `tracer.set_output({kind, ...})` so the root span carries the final widget/message.
- **`main.py`:** wraps `event_stream` in `async with observability.trace_chat(user_id=auth.user_id, conversation_id=conversation_id, message=req.message) as tracer:` and passes the tracer to `run_agent`. Adds `langfuse_configured` to `/healthz`. **016's persistence behaviour is preserved verbatim** — the `conversation` SSE event still emits first, the pre/post-stream persistence calls still run, demo mode still skips persistence.
- **`pyproject.toml`:** adds `langfuse>=4.7.0,<5.0.0` to main deps (pure-Python, no heavy transitives like the supabase/pyiceberg trap).
- **`.env.example`:** adds `LANGFUSE_PUBLIC_KEY`/`SECRET_KEY`/`HOST` with the US/EU/HK regional URLs spelled out (PRIORITIES_EXPLAINED's "mind the Japan-region endpoint" hint applies — `hk.cloud.langfuse.com` is the APAC endpoint).

**Verified pre-apply** (no Langfuse account / no network):

- `py_compile` of all three backend files ✓.
- `scripts/test_P4_017_observability.py` → **13/13** (langfuse_configured truth table including the REPLACE-placeholder edge case; `NOOP_TRACER` satisfies the `Tracer` protocol surface; `trace_chat` unconfigured returns the NOOP singleton cleanly; a synthetic agent loop driving a `_RecordingTracer` captures the expected 2 generations + 1 tool span + terminal output; the lazy-client "unavailable sticky" bit also falls back to NOOP).
- The Langfuse 4.7.1 API surface is verified live in the venv (the methods + kwargs used in `observability.py` all match the installed signatures).
- 017 doesn't touch `auth.py`/`db.py`/`schema.sql` → the applied 012 HS256 test (18/18), 015 ES256/JWKS test (10/10), and 016 persistence unit test (17/17) trivially still pass.

**Apply order matters** (per the (A) README addendum): `pyproject.toml` → `uv sync` → other files. Then sign up at `cloud.langfuse.com`, paste the keys into `.env`, restart. Drive one chat turn → a trace named `chat` appears in the Langfuse dashboard, tagged with `user.id` + `session.id`, containing `anthropic.iter_*` generations and `tool:<name>` spans.

**Design notes worth keeping:**

- Observability is read-only — never raises into the user stream. Every Langfuse call is wrapped; failures log at debug only.
- Soft dependency on auth: anonymous demo turns are still traced (with `user_id="anonymous"`), per PRIORITIES_EXPLAINED §P4.4.
- Per-tool latency is currently the *batch* `asyncio.gather` total — per-call timing is a clean follow-up (`_call_tool` wrapper, ~5 lines). Noted in the README "Risks" section.
- The lazy `langfuse` import means a backend without the package installed still boots (the no-op path kicks in).

**Did NOT do this turn:**

- Did NOT start (C) — Proposal 018 (P4.3 Mem0 memory) — that's the next track per Nicholas' sequence; awaiting his go-ahead.
- Did NOT apply 017 (Nicholas applies + archives manually; pyproject-first order documented).
- Did NOT update `CLAUDE.md` tech-stack table — defer to when 018 also lands so Mem0 + Langfuse both move from `// planned` to live in one pass.

**Next:** draft Proposal 018 (P4.3 Mem0 memory) — the per-user fact recall layer. Key correctness target: **every `memory.search` + `memory.store` MUST scope by `AuthCtx.user_id` (the trusted Supabase UUID), never a client-supplied value** — wrong scope = cross-user leak via injected context (PRIORITIES_EXPLAINED §"Mem0 ↔ auth — a privacy-critical, hard dependency"). Doing it under 017's traces means a wrong-scope bug would show up immediately in the Langfuse dashboard.

**Follow-up — 017 applied, surfaced `AttributeError` on real-mode trace setup → Proposal 018 drafted.** Tom applied 017; every real-mode chat turn returned `200 OK` (the read-only design held) but logged `langfuse trace setup failed; turn untraced` with `AttributeError: 'Langfuse' object has no attribute 'propagate_attributes'`. Root cause: `propagate_attributes` is a **module-level** helper in Langfuse v4 (`langfuse.propagate_attributes(...)`), not an instance method — my 017 called it as `client.propagate_attributes(...)`. So every trace setup fell into the `except` branch and yielded `NOOP_TRACER` — chat unaffected, dashboard silently empty.

- **Proposal 018** (`018-fix-langfuse-propagate-attributes/`, one file, supersedes 017's `observability.py`): switch the call site to `langfuse.propagate_attributes(...)` after a lazy `import langfuse` (the module is already in `sys.modules` after `_get_client` ran, so the import is essentially free). One added line, one changed line, plus a short comment so the same mistake doesn't recur. Everything else from 017 — Tracer protocol, NOOP/real tracers, `record_generation`/`record_tool`/`set_output`, the setup-failure-to-NOOP fallback — unchanged.
- **Verified pre-apply:** `py_compile` ✓. 017's offline unit test still **13/13** against the fixed `observability.py` (it covers the NOOP and recording paths, which weren't affected). **Live integration probe** under a real-but-isolated Langfuse client (`LANGFUSE_PUBLIC_KEY=pk-lf-test`, `LANGFUSE_HOST=http://localhost:19999` so the OTel batch exporter has nowhere to deliver to): prints `trace_chat yielded a real _LangfuseTracer: True` — pre-018 this would be `False` (the AttributeError forced NOOP). The "Transient error … Connection refused" lines from Langfuse's background batch exporter are proof the spans were created and queued — they just couldn't reach the fake URL. Real `cloud.langfuse.com` will accept them.
- **Lesson surfaced + carry-forward:** the 017 unit test couldn't catch this because it exercises the NOOP path + a synthetic agent vs a `_RecordingTracer`; the bug only triggered on the **real-mode setup path** with a real configured client. Next pass on `scripts/test_P4_017_observability.py` should add a "configured-but-isolated" probe (real keys + dead `LANGFUSE_HOST`) that asserts `isinstance(tracer, _LangfuseTracer)` — would have caught the API-shape regression at draft time. Not in 018's scope (single-file fix); folded into a TODO.
- **Net:** P4.4's data plane now actually flows. With 018 applied, `chat` traces land in Langfuse tagged with `user.id` + `session.id`, containing `anthropic.iter_*` generations and `tool:<name>` spans — the original P4.4 acceptance criterion ("a chat turn appears as a trace, attributed to the right user") finally holds end-to-end. **Then** Proposal 019 (P4.3 Mem0) — renumbered from the originally-planned 018, since the bugfix takes the next sequential slot.

**Follow-up — 018 applied (Langfuse data plane confirmed live via metadata in Tom's trace paste); separately, a chart prompt surfaced a latent P1.2 mock-mode bug → Proposal 019 drafted.** Tom's trace paste showed `scope.attributes.public_key=pk-lf-9f4444f1-…` + `scope.version=4.7.1` + `resourceAttributes.telemetry.sdk.*=opentelemetry` on the tool span — proves 017+018 are working end-to-end (P4.4 verified by direct observation of a tool-span attribute that only the OTel/Langfuse layer would set). **Separately**, Tom drove "add the 50 and 200-day SMA on NVDA" in mock mode (TradingView Desktop not yet set up); the widget rendered correctly *except* the chart image, which 404'd with `GET /api/mock-chart/NVDA.svg`.

- **Root cause:** `_mock_technical_levels` in `backend/tools/technicals.py` returned `screenshot_url=f"/api/mock-chart/{ticker}.svg"` — a placeholder URL **no backend route was ever registered for**. The real path (proposal 002) uses `screenshot_url=""` to mean "no real screenshot — frontend, render `<MockChartSvg/>`"; `TAChart.tsx`'s `hasScreenshot = … length > 0` check honours it. The mock path just didn't follow the same contract. Latent since 002 applied ~6 days ago; undetected because 002's verification exercised the *real* path only.
- **Proposal 019** (`019-mock-chart-screenshot-url/`, one file, `backend/tools/technicals.py`): change the mock branch's `screenshot_url` literal to `""` (one functional line + 5-line explanatory comment). `chart_apply_indicator` / `chart_draw_levels` / `chart_scroll_to_date` all route through `_mock_technical_levels` for their mock branch → inherit the fix for free. Real path completely untouched.
- **Verified pre-apply:** static diff confirms exactly one functional change; `py_compile` ✓; **runtime check across all three mock entry points** (staging the proposed file over a temp copy of the live `tools/` package with `PYTHONPATH=$TMP:backend`) shows `screenshot_url=''` everywhere. Frontend's existing fallback to `<MockChartSvg/>` will render correctly post-apply.
- **Renumber:** Mem0 was provisionally 019 after the 018 langfuse fix → with this bugfix taking 019, Mem0 becomes **020**.
- **Belt-and-braces follow-up** (not in 019; tracked): a tiny snapshot test asserting `_mock_technical_levels(...).["screenshot_url"] == ""` — same shape as `scripts/test_P1_003.py`. Cheap, prevents future regressions on this specific contract. Not in scope for the single-file fix.

**Follow-up — looking at the same chart card surfaced a trust-#3 hygiene gap → Proposal 020 drafted.** Tom's screenshots showed the mock-mode chart card's "Sources" pills as `[TradingView, Daily OHLC]`, but the tool result (paste from the previous turn) had `sources: [{"name": "TradingView (mocked)"}]`. Two violations: (a) the `(mocked)` suffix was silently stripped, presenting mock data as if from a real live feed; (b) `Daily OHLC` was invented — no tool returned that string. The numbers themselves (price, SMAs, S/R) were copied faithfully — rule #2 held; the *attribution* enjoys none of the same discipline because principle #6 ("Cite everything") has no mechanical companion the way #2 backs up #1.

- **Proposal 020** (`020-source-fidelity/`, one file, `backend/prompts/system.md`): add new trust-principle rule **#7 — Copy sources verbatim** modelled on #2's mechanical-rule shape. Four sub-bullets: (1) tool's `sources` field is copied as-is including `(mocked)`/`url`/dashes/modifiers; (2) if no `sources` field, compose one concise entry per tool called; (3) concatenate across tools called this turn in tool-call order, dedup exact matches only; (4) never invent a source no tool returned ("Daily OHLC", "Bloomberg", "SEC EDGAR" if no tool said so). Plus a worked-example tightening — the previous example narrated three tool calls but only listed two source pills, a latent violation of the new rule. No code, no schema, no env, no pyproject — prompt-only.
- **Why no offline test:** prompt behaviour is model-mediated; can't be unit-tested without driving the LLM. Post-apply verification = re-run the exact prompt that surfaced the bug ("add the 50 and 200-day SMA on NVDA") and confirm `Sources` pill now shows `TradingView (mocked)` verbatim. Langfuse traces (017+018) make this trivial — for any turn, compare the terminal generation's output `sources` field to the upstream `tool:*` spans' outputs; they should match.
- **Renumber:** Mem0 → **proposal 021** (sequential bump after 020 takes the next slot).
- **Three pragmatic follow-ups for hardening source-fidelity** (not in 020; tracked): (1) a Langfuse evaluator that asserts every terminal-widget `sources` entry appears in an upstream `tool:*` span — catches violations post-hoc; (2) a live-driven smoke test extending `scripts/test_P1_003.py` style — needs `ANTHROPIC_API_KEY`; cheapest immediate signal; (3) a rebuilt widget-output validator (the one CLAUDE.md flagged as deferred 2026-05-20) — now there's a genuine source-fidelity check it could enforce, but the original false-fail risks on `order_ticket`'s computed fields still hold; rebuild only if the prompt rule alone proves unstable.

**Follow-up — 020 applied, Tom started the TradingView setup and hit a path-with-spaces bug → Proposal 021 drafted.** With 020 in, Tom completed the real-mode TradingView prerequisites (TV Desktop with `--remote-debugging-port=9222`, sibling repo at `/Users/student/Documents – SNG058/Work/tradingview-mcp/`, `npm install`, `USE_MOCK_TA=0`). First chart prompt blew up at Node spawn with `Error: Cannot find module '/Users/student/Documents'` — only the first whitespace-delimited fragment of the path made it to Node. Plus a cascade `RuntimeError: Attempted to exit cancel scope in a different task` (anyio teardown fallout once spawn fails; not a separate bug).

- **Root cause:** `backend/mcp_client.py::_tradingview_config()` did `args=os.getenv("TRADINGVIEW_MCP_ARGS").split()`. The `.split()` shattered the path on every space and on the en-dash. The comment above the bug **acknowledged the gap** — "For paths with spaces set the env var via a script that handles quoting properly" — a workaround that was neither documented nor implemented, and that python-dotenv's auto-quote-stripping would have defeated anyway.
- **Proposal 021** (`021-mcp-args-path-with-spaces/`, one file `backend/mcp_client.py`): change `args=args.split()` → `args=[args]`. Pass `TRADINGVIEW_MCP_ARGS` as a single argument verbatim (the dataclass docstring already documented this contract: `args: list[str]  # CLI args (e.g. ["/path/to/server.js"])`). Plus a sentence in the module docstring and a 9-line replacement comment at the call site explaining why we don't split and pointing at `TRADINGVIEW_MCP_EXTRA_ARGS` + `shlex.split` as the escape hatch if multi-arg is ever needed.
- **Considered + rejected:** `shlex.split` with explicit quoting in `.env` (fragile dotenv-vs-shlex quoting interaction; users would have to know that python-dotenv strips one layer of outer quotes); introducing `TRADINGVIEW_MCP_EXTRA_ARGS` now (premature — no real multi-arg need today; documented as the escape hatch when needed).
- **Verified pre-apply:** static diff = exactly the targeted change; `py_compile` ✓; runtime check across three path shapes — space-free path (regression: still works as a single-element list), the en-dash repro path (now preserved as `len(args)==1`), and the unconfigured-returns-None case — all green.
- **Why 002's PR didn't catch this:** the 002 verification ran in mock mode (`USE_MOCK_TA=1`) — never exercised `_tradingview_config()` in earnest. ~5-week interval between 002 landing (2026-05-27) and the first real-mode chart prompt (today) is how the latent bug survived. **Pattern worth noting**: 021 is now the **fifth proposal touching applied 002's footprint** (004 coroutine leak, 007 import path, 019 mock screenshot URL, 021 args splitting) — budget for several small follow-ups whenever a future proposal can't real-mode-integration-test at apply time.
- **Renumber:** Mem0 → **proposal 022**. Same sequential pattern (004→002 / 007→002 / 008→006 / 013→012 / 018→017 / 019→002 / 020 / 021→002).
- **Belt-and-braces follow-up** (not in 021; tracked): a tiny snapshot test asserting `_tradingview_config()` returns the expected one-element args list for several path shapes (with/without spaces, with en-dash, unconfigured). Cheap, no subprocess needed. Same shape as the test idea flagged in 019's follow-up list. Folded into the carry-forward list.

**Follow-up — 020 applied, Tom started the TradingView setup and hit a path-with-spaces bug → Proposal 021 drafted.** With 020 in, Tom completed the real-mode TradingView prerequisites (TV Desktop with `--remote-debugging-port=9222`, sibling repo at `/Users/student/Documents – SNG058/Work/tradingview-mcp/`, `npm install`, `USE_MOCK_TA=0`). First chart prompt blew up at Node spawn with `Error: Cannot find module '/Users/student/Documents'` — only the first whitespace-delimited fragment of the path made it to Node. Plus a cascade `RuntimeError: Attempted to exit cancel scope in a different task` (anyio teardown fallout once spawn fails; not a separate bug).

- **Root cause:** `backend/mcp_client.py::_tradingview_config()` did `args=os.getenv("TRADINGVIEW_MCP_ARGS").split()`. The `.split()` shattered the path on every space and on the en-dash. The comment above the bug **acknowledged the gap** — "For paths with spaces set the env var via a script that handles quoting properly" — a workaround that was neither documented nor implemented, and that python-dotenv's auto-quote-stripping would have defeated anyway.
- **Proposal 021** (`021-mcp-args-path-with-spaces/`, one file `backend/mcp_client.py`): change `args=args.split()` → `args=[args]`. Pass `TRADINGVIEW_MCP_ARGS` as a single argument verbatim (the dataclass docstring already documented this contract: `args: list[str]  # CLI args (e.g. ["/path/to/server.js"])`). Plus a sentence in the module docstring and a 9-line replacement comment at the call site explaining why we don't split and pointing at `TRADINGVIEW_MCP_EXTRA_ARGS` + `shlex.split` as the escape hatch if multi-arg is ever needed.
- **Considered + rejected:** `shlex.split` with explicit quoting in `.env` (fragile dotenv-vs-shlex quoting interaction; users would have to know that python-dotenv strips one layer of outer quotes); introducing `TRADINGVIEW_MCP_EXTRA_ARGS` now (premature — no real multi-arg need today; documented as the escape hatch when needed).
- **Verified pre-apply:** static diff = exactly the targeted change; `py_compile` ✓; runtime check across three path shapes — space-free path (regression: still works as a single-element list), the en-dash repro path (now preserved as `len(args)==1`), and the unconfigured-returns-None case — all green.
- **Why 002's PR didn't catch this:** the 002 verification ran in mock mode (`USE_MOCK_TA=1`) — never exercised `_tradingview_config()` in earnest. ~5-week interval between 002 landing (2026-05-27) and the first real-mode chart prompt (today) is how the latent bug survived. **Pattern worth noting**: 021 is now the **fifth proposal touching applied 002's footprint** (004 coroutine leak, 007 import path, 019 mock screenshot URL, 021 args splitting) — budget for several small follow-ups whenever a future proposal can't real-mode-integration-test at apply time.
- **Renumber:** Mem0 → **proposal 022**. Same sequential pattern (004→002 / 007→002 / 008→006 / 013→012 / 018→017 / 019→002 / 020 / 021→002).
- **Belt-and-braces follow-up** (not in 021; tracked): a tiny snapshot test asserting `_tradingview_config()` returns the expected one-element args list for several path shapes (with/without spaces, with en-dash, unconfigured). Cheap, no subprocess needed. Same shape as the test idea flagged in 019's follow-up list. Folded into the carry-forward list.

**Follow-up — 021 applied, first real-mode chart turn surfaced TWO bugs in 002's real path → Proposal 022 drafted.** With 021 unblocking spawn, Tom's NVDA chart prompt actually ran end-to-end. The tool result revealed (a) `current_price=$211.14` (real, from `quote_get`) sitting alongside `key_levels.resistance=[959.47, 999.05]` / `support=[881.24, 829.40]` — a **trust-#3 violation** because those R/S numbers are `MOCK_QUOTES["NVDA"]=$942.50 × 1.018/1.06/0.935/0.88`. Sources pill claimed "Live OHLC via TradingView MCP" while the levels were silently mock-derived. Plus (b) empty `indicator_values` and empty `screenshot_url`: `data_get_study_values` and `capture_screenshot` returned shapes the parser didn't recognise and were silently swallowed with no log line.

- **Root cause of (a):** `_real_technical_levels` called `quote_get` LAST. The pine-lines fallback at step 6 fired BEFORE `current_price` was set, so it called `_extract_price(ticker)` which is hardcoded to `MOCK_QUOTES`. The "no silent fall-through" discipline was respected for tool errors but accidentally violated *inside* a successful tool result via that fallback's mock-cache lookup.
- **(b) is harder:** we don't yet know what shape `data_get_study_values` / `capture_screenshot` actually return — the parser was built against a guess. The right move is to make the silent failures **audible** in this proposal so the next iteration has data to fix them.
- **Proposal 022** (`022-real-mode-ta-fallbacks/`, one file `backend/tools/technicals.py` + one new test `scripts/test_P1_022_real_ta.py`):
  1. Move `quote_get` from step 7 → step 5 (right after `chart_manage_indicator`). `current_price` is then available for both the response AND the pine-lines fallback below.
  2. Pass `current_price` to `_key_levels(current_price)` in the pine-lines fallback (not `_extract_price(ticker)`). `_extract_price` keeps its role as the **last**-resort fallback (when `quote_get` itself fails).
  3. Add `log.info(...)` at three previously-silent paths: `data_get_study_values` returning an unrecognised shape, `data_get_pine_lines` returning no usable lines, `capture_screenshot` returning no `data`/`base64` field. Each log includes the raw response so the next follow-up can teach the parser the right field names.
- **Verified pre-apply** (no TV Desktop / no MCP needed — `mcp_client.tv_call` stubbed by a script-driven response map): new test **17/17** vs proposed code. Covers happy path with real $211.14 → swing-derived S/R $214.94/$223.81 (NOT the pre-022 $959/$881), quote_get-fails-last-resort (mock cache acceptable), three shape-mismatch logging cases, pine-lines-override, sources verbatim (020 contract preserved), `is_mock:False`. Test setup gotcha worth noting: `sys.path` order matters — `_TMP` (staged proposed file) MUST be inserted AFTER `_LIVE_BACKEND` so the LIFO ordering leaves `_TMP` at index 0 and the staged copy wins module resolution. (First test run got false-negatives because order was wrong; subtle but caught.)
- **What 022 deliberately doesn't fix:** the empty `indicator_values` and empty `screenshot_url` from the MCP server's unrecognised response shapes. 022 makes those gaps **audible** (the new `log.info` lines surface the raw responses); a follow-up will teach the parser whatever field names the MCP server actually uses once we see them in a real log.
- **Pattern continues:** 022 is now the **sixth proposal touching applied 002's footprint** (004 coroutine leak / 007 import path / 019 mock screenshot URL / 021 args splitting / 022 mock-price R/S + silent failures / + 002 itself). The lesson generalises: any "big-bang integration" proposal that can't real-mode-integration-test at apply time should budget ~4-6 small follow-ups during the first weeks of real use. Worth a CLAUDE.md note in the next session.
- **Renumber:** Mem0 → **proposal 023**. Sequential bump.
- **Two follow-ups tracked (not in 022):** (i) the follow-up proposal that teaches the parser the real MCP response shapes for the three calls 022 just made audible — gated on Tom's first post-apply log/Langfuse output; (ii) per-call error codes on the `ta_chart` widget so partial failures (price OK + screenshot missing) can be displayed honestly rather than just having empty fields.

**Follow-up — Tom said "if TradingView incomplete, proceed in that direction first; if complete, proceed with Mem0". Decided incomplete → drafted 023 same turn.** After 022 the chart card became *honest* (no more fake $959 R/S on a $211 stock) but the chart slot still showed the mock SVG and SMA values were still empty — those are central to P1.2's "talk to your charts" promise, so TradingView isn't done. Rather than ask Tom to apply 022 and report logs to drive the next iteration, **read the `tradesdontlie/tradingview-mcp` sibling repo directly** (it was cloned at `/Users/student/Documents – SNG058/Work/tradingview-mcp/` per the `TRADINGVIEW_MCP_ARGS` env var) and sourced the real response shapes from `src/core/data.js::getStudyValues / getPineLines / getQuote` + `src/core/capture.js::captureScreenshot`.

- **Three structural mismatches between our parser and the MCP server's actual API:**
  1. **`data_get_study_values` takes NO args** and returns `{success, study_count, studies: [{name, values: {title: value}}]}` — covering **all visible studies at once**. `name = meta.description` from TradingView, often without the length. We were calling it once per indicator with a `{study: <id>}` arg the server ignored, then reading `study.value` (a field that doesn't exist).
  2. **`data_get_pine_lines` returns `{studies: [{horizontal_levels: [num, ...]}]}` with NO labels.** We were parsing `{lines: [{y1, y2, label}]}` and looking for `support`/`resistance` substrings — wrong shape entirely.
  3. **`capture_screenshot` writes the PNG to disk** at `<sibling-repo>/screenshots/<fname>.png` and returns `{file_path, region, size_bytes}` — no inline base64. We were parsing `{data}`/`{base64}` which were never there.
- **Proposal 023** (`023-tv-mcp-real-shapes/`, supersedes 022's `_real_technical_levels`):
  - New `_extract_indicator_values(applied, studies)` — two-pass disambiguation for SMA 50 vs SMA 200 (both have `name="Moving Average Simple"` in TradingView). Pass 1 = length-aware (looks for the length in `name` like `"(50)"` OR in any title within `values` like `MA(50)`). Pass 2 = positional (`applied[i] → i-th matching study`, since studies arrive in chart-layer = add-order). Studies are consumed once mapped so the two SMAs never collide.
  - New `_partition_pine_levels(studies, current_price)` — flattens `studies[].horizontal_levels`, partitions by side relative to price (above → resistance closest-first; below → support closest-first; top-2 each). Returns None when nothing usable → caller falls back to swing-derived from real `current_price` (honest post-022 fallback). Partial pine lines (only one side) → fall back swing-derived on the missing side, not all-or-nothing.
  - New async `_encode_screenshot_file(file_path)` — reads PNG from disk via `asyncio.to_thread(p.read_bytes)` (don't block the event loop), base64-encodes, returns `data:image/png;base64,…` URL. Frontend `<img>` renders directly. Empty/missing/unreadable file → returns `""` so the existing `<MockChartSvg/>` fallback (019) fires.
  - Call args fixed: `data_get_study_values({})` not `{study: id}`; `capture_screenshot({region: "chart"})` for cleaner crop (was `{format: "png"}` which the server ignored).
  - `_real_technical_levels` restructured to do ONE study-values call instead of N (per-indicator).
- **Verified pre-apply** (no TV Desktop / no MCP needed): new test `test_P1_023_tv_mcp_parser.py` **25/25** vs proposed code. Response maps modelled on the real JS shapes; byte-exact screenshot round-trip via an on-disk PNG written to a tempdir; both length-aware and positional SMA disambiguation paths covered; partial-pine S/R fallback; missing-file logging; `quote_get` last-resort. `py_compile` ✓.
- **Apply order:** 022 first, then 023. 022 is the smaller surgical fix (audible failures + trust-#3 R/S); 023 is the structural rewrite that makes the parser correct against real shapes. Sequential apply preserves a clean audit trail of "each proposal does one thing." (Skipping 022 and going straight to 023 also works — 023's `_real_technical_levels` was built on top of 022's restructuring — but the audit gets murkier.)
- **Pattern continues:** 023 is the **seventh proposal touching applied 002's footprint** (004 / 007 / 019 / 021 / 022 / 023 / + 002 itself). The cluster is now large enough to be a documented lesson — flagged for a CLAUDE.md note in the next housekeeping pass: any "big-bang integration" proposal that can't real-mode-integration-test at apply time should expect ~5-7 small follow-ups during the first weeks of real use.
- **Renumber:** Mem0 → **proposal 024** (third bump this session). After 023 applies, P1.2 is genuinely complete and Mem0 becomes the unambiguous next track.

---

## ✱ Naming correction (this session)

The user is **Nicholas** (Tsun Li Nicholas Tam, per the Supabase email in the JWT decoded earlier this session). Past entries in this log refer to him as "Tom" — that was claude's mistake, pulled forward from an old assumption in `CLAUDE.md` / `CONTEXT_TRANSFER.md` and never corrected. **All references to "Tom" elsewhere in this log refer to Nicholas.** Going forward, claude addresses the user as Nicholas in all new entries, all proposal READMEs, and all reference docs. Folded into the 2026-06-01 `CONTEXT_TRANSFER.md` refresh.

---

## 2026-06-01 (later) · 023 applied surfaced the 200K-token cap → Proposal 024 drafted + reference docs refreshed

Nicholas applied 023. The chart card now uses real shapes from the MCP server — but the very next real-mode chart prompt hit Claude's context window:

```
agent error: Error code: 400 — 'prompt is too long: 239600 tokens > 200000 maximum'
```

**Root cause** (predictable in hindsight): 023 made `capture_screenshot` actually work end-to-end — `screenshot_url` is now ~hundreds of KB of base64 PNG. `agent.py` echoes the full tool result back to Claude on every iteration via the `tool_result` message content. Two parallel `chart_apply_indicator` calls (one per SMA) attached two screenshots to the message history; with system prompt (~30K) + widget contract (~10K) + other accumulated tool results, the next iteration's input crossed 239K tokens. The screenshot was needed for the *widget* render (frontend), not for re-feeding the LLM, but the agent loop didn't distinguish.

- **Proposal 024** (`024-llm-context-screenshot-strip/`, one file `backend/agent.py` + new offline test):
  - New `_compact_for_llm(result)` — replaces any `screenshot_url` `data:` URL larger than 1 KB with `""` (the canonical "no real screenshot" sentinel from 019/023) before serialisation into the LLM-bound `tool_result` message content. Idempotent, non-mutating, defensive against non-dicts.
  - New `_restore_screenshot_in_widget(widget, urls)` — before yielding the terminal `widget` SSE event, substitutes the most-recently-produced real URL back into `widget.data.screenshot_url` (the LLM emitted `""` because that's all it ever saw in its context).
  - `run_agent` wiring: per-turn `screenshot_urls_by_tool: dict[str, str]` map tracks the originals; the compaction happens at the point of `json.dumps()` into the LLM payload; the Langfuse `tool:*` span (017 contract) still receives the full unstripped result for debugging.
- **Verified pre-apply:** `py_compile` ✓; new `scripts/test_P1_024_screenshot_context.py` → **16/16** including the byte-savings sanity check (real-mode tool result with a 200 KB screenshot: **205,263 bytes raw → 441 bytes compacted, >99% reduction**). 11 cases on `_compact_for_llm` (large/small/empty/non-dict/None/list/idempotent), 5 on `_restore_screenshot_in_widget` (single tool, multiple tools picking most-recent, no-op-on-empty, widgets without `screenshot_url`, defensive against missing `.data`).
- **Pattern continues, in a useful way:** 024 is the **eighth proposal touching applied 002's footprint** but it's also a *new* kind — 023's success (real screenshots flowing) unlocked this one. Not a pre-existing latent bug; an emergent consequence of the system actually working. Different category from the 004/007/019/021/022/023 chain.
- **Renumber:** Mem0 → **proposal 025** (fourth bump this session). After 024 the chart card finally works end-to-end in real mode and Mem0 is the unambiguous next track.

**Reference docs refreshed in the same turn (per the workflow's "reference docs edited directly" rule):**

- **`CONTEXT_TRANSFER.md`** updated to reflect the full state since 2026-05-29 — applied proposals 010–024, P4.1+P4.2+P4.4 all done, P4.3 (Mem0) as the active next track, the 002-cluster pattern note for future big-bang integration proposals, the source-fidelity rule (020), the 014 demo-mode escape hatch, the JWKS verification path (015), and the Nicholas naming correction throughout. Last-updated date bumped from 2026-05-29 → 2026-06-01.
- **Naming correction** — see the ✱ block above. All past "Tom" references in this log refer to Nicholas; going forward claude uses Nicholas in new entries / proposal READMEs / reference docs.

---

## 2026-06-02 · P4.3 — Mem0 per-user memory drafted as proposal 025 (the last P4 infra track)

With P1.2 finally complete end-to-end (024 in) and P4.1/P4.2/P4.4 all done, Mem0 was the unambiguous next track — drafted as **proposal 025** (`025-mem0-memory/`), the fourth and final P4 infra item. Offline-verified, awaiting Nicholas's manual apply.

**What it is:** the "MEMORY BLOCK 1/2" bracket around the LLM call. Before each turn, search *this user's* Mem0 memories and inject the hits into the system prompt; after the turn, store the new salient facts. Persistence (016) remembers the *conversation*; this remembers the *user* (risk tolerance, watchlist, "holds NVDA") across brand-new chats — the "stateless box → agentic" jump.

**The load-bearing constraint (PRIORITIES_EXPLAINED §"Mem0 ↔ auth"):** every `search`/`add` is scoped by the authenticated `auth.user_id` (the P4.1 Supabase UUID), never a client value. Wrong scope = an *invisible* cross-user leak — User B's new chat surfacing User A's holdings via the model's context, which RLS can't catch because it never touches an API response. The proposal enforces it three ways: (1) `main.py` resolves identity once at the route boundary and threads the same `auth.user_id` into recall, remember, `run_agent`, AND the Langfuse trace tag; (2) `memory.py` passes `user_id` to Mem0 verbatim and refuses an empty scope; (3) with 017/018 live, a wrong-scope bug shows up immediately as a mismatched `user.id` on the `chat` trace. The test's headline assertions are exactly "search/add get the EXACT user_id, never substituted."

**Architecture (the ownership split):**

- **`backend/memory.py` (new)** — mirrors `observability.py`'s 017 soft-dep template precisely: a `MemoryStore` Protocol, a `NOOP_MEMORY` singleton, a lazily-built cached `_Mem0Memory` (hosted `AsyncMemoryClient`), `memory_configured()` for `/healthz`, sticky-unavailable on bad import/init. Failure-tolerant: a Mem0 outage injects nothing / drops the write, never breaks the stream. Bounded: `MEM0_SEARCH_LIMIT` (5) + `MEM0_MAX_CHARS` (1500) cap the injected block's token cost.
- **`main.py`** — recall happens inside `event_stream` *before* `run_agent` (it must reach the system prompt, which is built inside the agent; on the critical path, ~200–500ms when configured). Store happens in the post-stream slot *after* the `done` event already reached the client (beside the 016 persistence write), so Mem0's extraction latency is invisible. `/healthz` gains `memory_configured`.
- **`agent.py`** — `run_agent(..., memory_context="")` appends the pre-formatted block to `SYSTEM_PROMPT` for the turn. Default `""` ⇒ byte-identical to pre-025; `run_agent` never imports `mem0` (stays dumb + testable).
- **`system.md`** — new *"Remembered user facts"* section subordinates memory to the trust principles: a remembered number tells you *what to look up*, never *what to display* (re-fetch with a tool — trust #1/#2); memory is never a `sources` entry (#7); fresh tool data wins on conflict. The block header in `memory.py` carries the same wording so they can't drift.
- **`pyproject.toml`** — `mem0ai>=0.1.0` in a new **optional** `memory` group (not main deps), so the default/Railway install stays lean and the verified backend is untouched until memory is explicitly enabled (`uv sync --group memory`). Lazy import means the backend boots even without it (→ NOOP).

**No-op gating (additive + removable):** unset/placeholder `MEM0_API_KEY` → `NOOP_MEMORY` → zero cost (mock demo / smoke_test / curl all unaffected). Key set but `mem0ai` not installed → NOOP (logged once). Demo turns (when configured) store/recall under a shared `"demo"` bucket — local-only artifact, harmless because prod is `REQUIRE_AUTH=1` (every user a distinct UUID). Memory is gated by `memory_configured()` only, NOT by a Supabase token (unlike persistence — Mem0 is keyed by `user_id` string, not RLS).

**Verified offline (pre-apply):**

- `py_compile` ✓ on `memory.py` / `agent.py` / `main.py`.
- New `scripts/test_P4_025_memory.py` → **29/29**: `memory_configured()` truth table, NOOP fallback, **scope-key fidelity on both search and add**, search-shape normalisation (`list` / `{"results":[…]}` / bare-string / unrecognised-skipped), bounded block (`MEM0_SEARCH_LIMIT` forwarded to search, `MEM0_MAX_CHARS` truncates 3→2 facts), failure-tolerance (search/add raising → swallowed), empty-scope guards (no client call), broken-import → NOOP. The test stubs `mem0` in `sys.modules` and loads the proposed `memory.py` by path — fully offline, no key.
- Regression: applied **017** observability test still **13/13** against the proposed `agent.py`; a temp full-overlay of the live backend + the proposed files confirms `run_agent` exposes `memory_context=''` and `main.py` imports `memory` + constructs `app` cleanly.

**Highest draft-time uncertainty (same posture as 006's FMP field names):** the exact `mem0ai` `AsyncMemoryClient` API and its transitive-dep weight are unverified without a key. The code targets the hosted platform client (`.search(query, user_id=, limit=)` / `.add(messages, user_id=)`) and normalises both known result shapes defensively. Confirm on first `uv sync --group memory` (base `mem0ai` historically pulls `openai`/`qdrant-client` the platform path doesn't need client-side — pure-Python, no pyiceberg-style break, but image weight to revisit at P6). Tighten the pin once confirmed.

**Apply order (pyproject delta → the 016 gotcha):** `cp pyproject.toml` → `cd backend && uv sync --group memory` → apply the rest (`memory.py`, `agent.py`, `main.py`, `.env.example`, `prompts/system.md`) → `cp` the test. Then post-apply: `/healthz.memory_configured: true`; the two-conversation recall check on a real UUID; and the **two-account isolation check** (account B never surfaces A's facts — the P4.3 gate, same discipline that cleared P4.2's HIGH-2).

**On apply, P4 is complete** (auth → persistence+RLS → observability → memory all done). Remaining: update `CLAUDE.md`'s stack table to list Mem0 + Langfuse (PRIORITIES P4 line 147; its "never deviate silently" rule). Next live front becomes **P5 pre-launch lockdown** (PostHog, live Hero portfolio value, rate limiting, DOMPurify, CSP/HSTS, dep audits) — plus the still-open **P1.2 first real TradingView integration test**.

---

## 2026-06-04 · 025 applied — first `uv sync --group memory` exposed a wrong mem0 call shape (v3-API correction)

Nicholas applied 025 and ran the test. It reported **29/29 passed** — but printed a scary traceback mid-run (test case 11 deliberately feeds `get_memory()` a broken `mem0` module to prove the NOOP fallback; the trace was `memory.py`'s own `logger.warning(exc_info=True)` doing its job — expected, not a failure). Silenced that one expected log line in the test so a passing run reads clean.

**The real find, surfaced because `mem0ai` was now actually installed (2.0.4, a v3 client):** the proposal's biggest draft-time unknown — "does `user_id` go where I assumed?" — was **wrong**, and wrong in the fail-closed direction:

- `mem0.AsyncMemoryClient.search()` checks `ENTITY_PARAMS & kwargs` (ENTITY_PARAMS ⊇ {user_id, agent_id, run_id, app_id}) and **raises `ValueError("…Use filters={'user_id': '...'} instead")`** on a top-level `user_id=`. The limit param is `top_k`, not `limit`. So the draft's `search(query, user_id=…, limit=…)` would have raised → been swallowed by recall's `except` → **silently recalled nothing on every turn.** The feature would have looked alive (no crash, turns complete) but been dead. Classic fail-closed bug that only a real-library check catches — exactly the 006-FMP-fields lesson ("verify against the live thing, not the happy-path assumption").
- `add()` has **no** such guard and accepts `user_id=` directly (docstring + source) — so `add(messages, user_id=…)` is correct. Documented the asymmetry inline so nobody "fixes" it to match recall.
- Also: `AsyncMemoryClient.__init__` does a **network key-validation** that raises on a bad key — handled by `get_memory()`'s try/except → sticky NOOP (so a bad `MEM0_API_KEY` degrades gracefully, doesn't crash boot).

**Fix (verified, applied to live + the 025 proposal copy, kept identical):**

- `memory.py` recall → `search(query, filters={"user_id": user_id}, top_k=_search_limit())`; add unchanged. Inline comments cite the installed-source contract + the fail-closed warning.
- `pyproject.toml` pin tightened `mem0ai>=0.1.0` → **`>=2.0.0,<3.0.0`** — a *correctness* pin (the v3 call shape is version-specific; a 0.x or 3.x could silently break recall). Installed 2.0.4 already satisfies it → no `uv sync` needed.
- Test updated to the v3 contract: fake client now takes `search(query, *, filters=, top_k=, **kwargs)`; headline assertions are now "recall scopes via `filters={'user_id': …}`" **and** "recall never passes `user_id` as a top-level kwarg (the banned ENTITY_PARAM)". **30/30** (was 29; +1 for the new banned-kwarg check), and the run is now clean (no stray traceback).
- README + STATUS updated; "API unverified at draft" risk replaced with the VERIFIED v3 contract section.

**Evidence trail:** read `AsyncMemoryClient.{search,add}` source from the installed package; confirmed `ENTITY_PARAMS` membership and `SearchMemoryOptions`/`AddMemoryOptions` fields (`top_k` lives there, `limit` doesn't); empirically, a real-client construction with a dummy key raises at `_validate_api_key()` before the call guard — so the source read (not a live call) is the definitive check, and it's conclusive.

**Pattern note (mirrors the 002→022/023 cluster):** a "new external integration" proposal that can't exercise the real dependency at draft time should expect a first-real-install correction. 025's was a single, contained, offline-verifiable fix the moment the package was actually present — same discipline that kept the 002 and 006 trails clean. **Still pending for full P4.3 sign-off:** live runtime with a real `MEM0_API_KEY` (recall round-trips a stored fact across two conversations) + the two-account isolation check (account B never surfaces A's facts — the P4.3 gate). Code path is correct; only the live network leg is unverified.

**Bookkeeping (same session):** the v3-API correction above is tracked as its own proposal **`proposed_changes/026-mem0-v3-api-fix/`** (not folded into 025), mirroring how 022/023 were follow-ups to applied 002 — 025 stays the record of the original Mem0 feature, 026 the first-real-install fix. 026 holds the corrected `memory.py`/`pyproject.toml`/test (already applied + committed in the `025 …` commits), the `uv.lock` 1-line relock (`uv lock`; specifier `>=0.1.0`→`>=2.0.0,<3.0.0`; **uncommitted — Nicholas to commit**), and a **new** `scripts/mem0_probe.py` (live store/recall + cross-user-isolation check driving the real `memory.py`; the `fmp_probe.py` pattern; awaits `cp` to live). mem0ai 2.0.4 pulled openai/qdrant-client/sqlalchemy/protobuf/posthog/pytz/pydantic — the heavy three are unused by the hosted-client path (P6 image-weight note). Post-apply test procedure (real `MEM0_API_KEY` → probe → two-account isolation gate) lives in 026's README.

**Live-verified P4.3 (2026-06-04, same session).** Ran the 026 probes against real Mem0. First `mem0_probe.py` (fixed 8s wait) FAILED — recall empty — but client init / key-validation / `delete_all` / isolation all worked, so not auth/scope. `mem0_diag.py` (raw client, no swallowing) found the mechanism: **Mem0 `add` is asynchronous** — returns `{"event_id","status":"PENDING"}`, no inline memories; `get_all(filters={"user_id":…})` went count 0→2 over 5–10s (the extraction latency that sank the fixed wait); `search("…", filters={"user_id":…}, top_k=5)` then returned **both extracted memories with relevance scores** — definitive proof the 026 v3 recall shape works against the live API. Re-ran the **polling** probe → **✓ PASS** (recalled both facts ~5s; B isolated). So P4.3 is store/recall + isolation **live-verified**; the v3 `filters`/`top_k` fix is correct end-to-end. Two follow-throughs folded into 026: probe now polls (a fixed sleep gives false failures — Mem0 add is async); added `mem0_diag.py`; fixed the diag's misleading "inference extracted nothing" line (add is PENDING by design). **P4 (auth → persistence+RLS → observability → memory) is now functionally complete.** Remaining housekeeping: commit the `uv.lock` 1-liner; `cp` the two probes to live `scripts/`; update `CLAUDE.md`'s stack table to list Mem0 + Langfuse (PRIORITIES P4 line 147); optional in-app two-account agent run. Next live front: **P5 pre-launch lockdown**.

---

## 2026-06-05 · P5 begins — drafted 027 (PostHog wiring) + 028 (live Hero portfolio)

First P5 session. Scoped (with Nicholas) to the **Analytics + UX** slice of the pre-launch lockdown; the security-hardening items (rate-limit + token budget, daily-trade cap, DOMPurify, CSP/HSTS, dep audit) are deferred to a later session. Two proposals drafted into `proposed_changes/` (not yet applied — Nicholas reviews/applies manually per the locked workflow).

**027 — Wire PostHog (frontend only).** The finding that shaped it: `frontend/lib/analytics.ts` was already fully built (`initAnalytics`, every `track*` helper, `classifyIntent`, `hashText`, all METRICS.md-aligned) but **nothing ever called it** — PostHog was wired to the door and stopped. So 027 is pure wiring, no new dep/env (`posthog-js` + a real `phc_` project key are already in place). New `components/Analytics.tsx` runs init from `layout.tsx`; `page.tsx` fires 6 helpers from existing handlers (`chat_session_started`, `prompt_submitted` with hash-not-raw-text, `widget_generated`, `order_ticket_shown` guarded, `widget_pinned`, `chat_error`). Analytics side-effects deliberately kept OUT of the `setTurns` state updater (updaters must stay pure). No-op when the key is `phc_REPLACE`/absent. Trade-confirm + signup events deferred (no UI trigger today).

**028 — Live Hero portfolio value (backend + frontend).** Replaces the hardcoded `$51,000.00`/`+$964.10` Hero. `get_portfolio` is the single source of truth and already mock-first/real-aware, so it's **extended additively** with `day_pnl`/`day_pnl_pct` (mock keeps the demo numbers; real derives from the Alpaca account's `last_equity`) rather than recomputing in the endpoint or re-fetching Alpaca. New thin `GET /api/portfolio` mirrors `/api/conversations` (header fields only — no positions list). Frontend gets a `next.config.js` rewrite, a `lib/portfolio.ts` fetch helper (reusing the `sse.ts` Bearer pattern), and a `Hero` that renders the fetched total + signed/colored day P&L with a static fallback so it never looks broken.

**Coupling flagged:** both proposals edit `frontend/app/page.tsx`. Apply **027 first, then 028** — 028's `page.tsx` copy is authored on top of 027's (contains both); 027 still owns `Analytics.tsx` + `layout.tsx`. Neither touches `pyproject.toml`/`package.json` → no `uv sync`/`pnpm install`.

**Verified offline (pre-apply):** `py_compile` ✓ on the two backend files; `ChatEvent`/`Widget` contracts in `sse.ts`/`widgets.ts` confirm the `page.tsx` type usage (`ev.data.type`, narrowed `ev.data.data` for `order_ticket`, `ev.data.message`). Frontend `pnpm typecheck` is an apply-time gate (the `@/` alias only resolves once files are in the live tree) — listed in both READMEs' acceptance criteria.

**Two false alarms cleared during exploration:** (1) an Explore agent flagged "live PostHog key + Supabase creds committed to the repo" — checked `git ls-files`: `frontend/.env.local` and `backend/.env` are **gitignored & untracked** (they exist on disk only). No leak. (2) `get_portfolio` does **not** return day P&L today — which is exactly why 028 extends it rather than reading a field that isn't there.

**Decision recorded — MEDIUM-3 (shared Alpaca account), designed now, built later as proposal 029 after 027/028 land.** Runtime-switchable via `ALPACA_ACCOUNT_MODE=shared|per_user` (default `shared` = today's behavior, a no-op default so the verified path is untouched). Option B mechanism chosen: **per-user stored Alpaca paper Trading API keys** in RLS-protected `user_profiles` columns (NOT the Broker API — no Alpaca application needed, works with the current `TradingClient`/Trading-API SDK). One `_resolve_alpaca_client(user_id)` routes all 4 client-construction sites (`execution.py` ×3, `portfolio.py` ×1, where `user_id` is already in scope but currently unused on the real path); trade audit log (INFO-2) folds in here. Also recorded: the deferred per-user **daily token budget** will be **Supabase-backed** (durable across Railway restarts).

**Next:** Nicholas applies 027 → 028 and runs the acceptance checks (PostHog Live Events / Network tab; `curl /api/portfolio`; Hero render + fallback). Then either continue P5 security-hardening or draft 029 (switchable Alpaca).

---

## 2026-06-05 · 027 verified live; TradingView degraded-fallback documented + proposal 029

**027 (PostHog) verified live** from a PostHog events export (`team_id 187304`): `prompt_submitted` (with `text_hash`/`intent_classification`, **no raw text** — PII rule holds), `widget_generated` (`widget_type`+`latency_ms`), `widget_pinned` (`widget_type`+`time_since_session_start_ms`), `chat_error` (`error_type: "Read error: network error"`), `chat_session_started`, plus `$pageview`/`$pageleave`/`$autocapture`. 5/6 wired events confirmed; `order_ticket_shown` simply wasn't triggered (no order prompt run this session). Note: `chat_session_started` fired 4× vs 2 `$pageview`s — React **Strict Mode** dev double-invoke of the mount effect (dev-only; prod won't double). The earlier `/flags` 401 + `/array/config` 404 are feature-flag/config endpoints, not capture — non-blocking; ingestion works.

**Chart-showing-mock diagnosis (NOT a 027/028 regression).** The TA card showed real source pills (`TradingView Desktop` / `Live OHLC via TradingView MCP`) over **mock numbers** (NVDA `$942.50`, S/R `959.47/881.24` = `_key_levels(942.50)`). Cause: `backend/.env` had `USE_MOCK_TA=` **blank** → `_use_mock_ta()` False → the **real** path ran, but TradingView Desktop wasn't open (CDP `:9222` unreachable, confirmed via curl), so `quote_get` failed → `current_price` + swing S/R fell back to the mock cache (`_extract_price`) while the screenshot was omitted. 022/023/024 ARE applied in the live backend (verified markers: `_extract_indicator_values`, `_partition_pine_levels`, `_compact_for_llm`, `screenshot_urls_by_tool`, quote_get@step-5) — so `STATUS.md` was stale there; `CONTEXT_TRANSFER.md` is right. Neither 027 (analytics) nor 028 (portfolio) touches the TA path. The user then launched TV Desktop (`open -a "TradingView" --args --remote-debugging-port=9222`) and CDP came up (UA `TVDesktop/3.2.0`), restoring the real path.

**Documented the limitation directly** (reference docs, edited in place): `README.md` gained a "Limitation: live charts require TradingView Desktop to be *open*" subsection (both-conditions rule: `USE_MOCK_TA=0` **and** TV Desktop on `:9222`; the launch command; the `curl localhost:9222/json/version` check; the degraded-fallback behavior). Same caveat added to `backend/.env.example` (TradingView block) and `CLAUDE.md` (tech-stack Charts row).

**Proposal 029 drafted** (`proposed_changes/029-ta-mock-fallback-sources/`, 1 file — `technicals.py`): when the real path falls back to the mock cache for `current_price`, set a `price_is_mock` flag and **downgrade the widget sources** to `TradingView (mocked — live data unavailable)` (`source: "tradingview_mcp_degraded"`), instead of claiming live TradingView over mock numbers — closing the trust-#3 / rule-#7 gap. Keyed only on the price mock-cache fallback (a real price with a missing screenshot/indicators is not downgraded); `is_mock` left False; live + pure-mock paths unchanged. `py_compile` ✓; diff vs live = only the targeted edits.

**Numbering note:** MEDIUM-3 (switchable Alpaca routing) was tentatively "029" in the plan; since 029 is now the TA-source fix, MEDIUM-3 moves to **030** (still to be drafted after 027/028 are implemented).

---

## 2026-06-05 · 027 + 028 applied & verified end-to-end

Both P5 Analytics+UX proposals are now applied to the live tree and verified.

**027 (PostHog) — verified** from a live PostHog events export (`team_id 187304`): all 6 wired events fire with correct payloads — `prompt_submitted` (12×, every one carrying `text_hash`, no raw text; intents bucketed: technical_analysis/place_trade/stock_research/morning_brief/other), `widget_generated` (10×, 5 widget types incl. ta_chart/order_ticket/morning_brief/portfolio_risk/thesis, `latency_ms` populated), `order_ticket_shown` (2×, `TSLA` / notional `2460` / `has_tp_sl True` — correct for "buy 10 TSLA @ 246, TP 290, SL 225"), `widget_pinned`, `chat_error` (`Read error: network error`), `chat_session_started`. Known dev-only quirk: `chat_session_started` double-fires under React Strict Mode (8 events / 4 page loads; prod halves). Reassessment of "all buttons": the buttons 027 covers (submit, example prompts, pin) all emit correctly; OrderTicket Confirm/Edit + Tracker tap are **dead buttons** (no handler wired) and emit nothing — deferred to a possible proposal 031 (shelved per Nicholas).

**028 (live Hero portfolio) — verified, all 4 acceptance criteria:**

1. Backend `import main` OK; `pnpm typecheck` clean under Node 22.22.3 (note: pnpm needs Node ≥22 — a shell on Node 20 fails to even start pnpm).
2. Demo curl on a throwaway `:8001` (`REQUIRE_AUTH=0 USE_MOCK_MARKET=1 USE_MOCK_BROKER=1`) → `{"total_equity":51000.0,"day_pnl":964.1,"day_pnl_pct":1.93,"currency":"$","is_mock":true}`.
3. Real path (Bearer token, real Alpaca, `:8000` `REQUIRE_AUTH=1`) → Hero shows `$99,998.93 / −$0.05 (−0.00%)` in red, **matching the Alpaca dashboard** (portfolio $99,998.94, Daily Change −$0.04 at capture) — confirms `day_pnl = equity − last_equity` and the negative→`text-red-DEFAULT` render.
4. Backend down → `GET /api/portfolio` 500 → `fetchPortfolio` returns null → Hero **falls back to static** `$51,000.00 / +$964.10` (green), no crash; backend restart → live value returns.

Operational notes worth keeping: `/api/portfolio` honours `REQUIRE_AUTH` — token-less curl 401s under `REQUIRE_AUTH=1` (expected); to see the mock `$51k` you need `USE_MOCK_BROKER=1` (real Alpaca keys otherwise return the live book). **P5 Analytics+UX slice is complete.** Remaining P5: the deferred security-hardening track (rate-limit + Supabase-backed token budget, daily-trade cap, DOMPurify, CSP/HSTS, dep audit) + proposal 029 (TA mock-source downgrade, awaiting apply) + the switchable-Alpaca proposal (now numbered 030).

---

## 2026-06-05 · 029 applied & verified across all three TA paths

Proposal 029 (TA degraded-source label) applied verbatim to `backend/tools/technicals.py` (diff vs the proposed copy = identical) and verified on every path:

- **Pure mock** (`USE_MOCK_TA=1`) → `TradingView (mocked)` — unchanged. ✓
- **Live** (TV Desktop open on `:9222`, real `quote_get`) → real numbers + `[TradingView Desktop, Live OHLC via TradingView MCP]`. ✓
- **Degraded** (TV Desktop closed while `USE_MOCK_TA=0`) → the NVDA card showed the mock numbers ($942.50 / R $959.47 / S $881.24) but the source pill now reads **`TradingView (mocked — live data unavailable)`** (`source: "tradingview_mcp_degraded"`). ✓ — closes the trust-#3 / rule-#7 gap where mock numbers used to sit under a "live TradingView" label.

This also completes the 2026-06-05 TradingView-limitation documentation (README "Talk-to-your-charts" subsection + `.env.example` + CLAUDE.md Charts row): live charts require BOTH `USE_MOCK_TA=0` AND TV Desktop open on `:9222` (`open -a "TradingView" --args --remote-debugging-port=9222`), else the card degrades (now honestly labelled).

**P5 status:** the Analytics + UX slice (027 + 028) plus the 029 source-fidelity fix are all applied & verified. Remaining P5 = the security-hardening track (rate-limit + Supabase-backed token budget, daily-trade cap, DOMPurify, CSP/HSTS, dep audit) and proposal 030 (switchable per-user Alpaca routing). Proposal 031 (dead OrderTicket/Tracker buttons + events) shelved.

---

## 2026-06-05 · 030 drafted — switchable Alpaca account routing (MEDIUM-3)

Drafted `proposed_changes/030-switchable-alpaca-accounts/` (the renumbered MEDIUM-3). Makes Alpaca account routing switchable behind `ALPACA_ACCOUNT_MODE`, **default `shared` = today's behavior verbatim** (a no-op default — the verified shared path is untouched until flipped):

- `shared` → the env `ALPACA_API_KEY`/SECRET account (as today).
- `per_user` → each user's own linked Alpaca **paper Trading-API** creds (the chosen mechanism — no Broker API, no Alpaca application), stored on their RLS-protected `user_profiles` row, linked via `POST /api/alpaca/link`.

**Architecture decision (the load-bearing bit):** tool callables get only `user_id`, not the JWT, but `user_profiles` RLS needs the JWT. Resolved with a **request-scoped `contextvars.ContextVar[AuthCtx]`** set in `main.py` around the agent/portfolio run; the new `backend/alpaca_accounts.py` resolver reads the token from it and queries creds **under the user JWT** (`db.get_alpaca_creds`) — RLS stays the guard, **no service key** (honors the P4.2 user-JWT-only locked decision). Rejected alternatives: service-role read (deviates from the locked decision) and threading the token through ~15 tool signatures (too invasive).

**Surface:** new `alpaca_accounts.py` (`account_mode`/`alpaca_enabled`/`resolve_trading_client`/`AlpacaAccountError` + contextvar helpers); the 4 client constructions (`execution.py` ×3, `portfolio.py` ×1) reroute through `resolve_trading_client(user_id)`; `db.py` gains `get_/set_alpaca_creds` (user-JWT); `main.py` gains the contextvar wrap + `POST /api/alpaca/link` + `GET /api/alpaca/status` + `/healthz.alpaca_account_mode`; `user_profiles` gains `alpaca_api_key`/`alpaca_api_secret` (schema.sql + `migration_030_alpaca_creds.sql`); `.env.example` gains `ALPACA_ACCOUNT_MODE`. No new dependency → no `uv sync`.

**Verified offline:** `py_compile` ✓ on all backend files; new `scripts/test_P5_030_alpaca_accounts.py` **19/19** (stubs `db` + `alpaca`, exercises account_mode/alpaca_enabled/shared-creds/per_user-no-token/unlinked/linked/client-build/default-shared); diffs vs live = only the targeted edits (default-`shared` path byte-for-byte unchanged).

**Known limits (documented in the README, not blockers):** plaintext paper creds at rest (RLS-guarded row; encrypt-at-rest is a later hardening pass — paper-only, low blast radius); trade audit log (INFO-2) deferred to a follow-up; `per_user`+unlinked returns an honest `alpaca_account_not_linked` (no shared/mock leak). **Draft-time uncertainty (the 002/006/025 pattern):** contextvar propagation through `EventSourceResponse`'s streamed generator — set *inside* `event_stream` (the reliable placement); documented one-line fallback is a service-role-scoped read if a first real `per_user` run shows the resolver seeing `None`. **Apply note:** packaged as full copies for the new module + tools + schema + test, and precise diffs for `main.py`/`db.py`/`.env.example`; run `migration_030_alpaca_creds.sql` before enabling `per_user`.

**P5 status:** Analytics+UX (027/028) + 029 applied & verified; **030 drafted, awaiting apply**. Remaining P5 = the rest of the security-hardening track (rate-limit + Supabase-backed token budget, daily-trade cap, DOMPurify, CSP/HSTS, dep audit). Proposal 031 (dead-button wiring) shelved.

---

## 2026-06-05 · STRATEGIC PIVOT — IBKR + WhatsApp waitlist briefing; chat MVP paused; 030 paused

Nicholas pivoted the project to a **pre-launch waitlist product**: land → connect Interactive Brokers via a one-time **Flex token** → every morning a **WhatsApp** narrative macro briefing (what moved / why / what it means), Claude-generated, Twilio-delivered. A bridge between broker and phone; validates demand before the full agent-chat brokerage.

**Decisions (asked + locked):**

- **Direction = PIVOT** — the agent-chat brokerage MVP (P5 security / P6 deploy) is **PAUSED** (kept, not abandoned).
- **Alpaca stays for now** while we add **IBKR Flex for holdings reads**; **swapping execution Alpaca→IBKR is a separate later step** (heavy — IBKR Client Portal/TWS API, not Flex).
- **Reuse this repo** (FastAPI + Supabase + the `morning_brief` generation + Langfuse + PostHog).
- **Proposal 030 (switchable Alpaca routing) PAUSED** — deprioritised; draft stays ready.

**Two technical realities surfaced (so the framing is accurate):** (1) **IBKR Flex Web Service is read-only** — token+queryID → 2-step `SendRequest`/`GetStatement` → XML holdings/NAV (EOD/report-style; great for a morning brief; *cannot trade*). So Flex replaces the holdings-READ only, not execution. (2) **Twilio WhatsApp proactive/scheduled sends are business-initiated** → production needs a registered WhatsApp Business sender + approved **templates** (a long narrative doesn't fit; send a short templated line + link), but the **Sandbox** covers a small opted-in validation cohort. WhatsApp send stays a **system cron job, never an agent tool** (threat 1).

**Captured (this is a deviation → written down first, per CLAUDE.md):**

- New **`self_management/DECISION_pivot_waitlist.md`** — full rationale, reuse-map, caveats, security (Flex token = sensitive real-account creds → encrypt at rest), and the staged plan **W1–W6** (W1 IBKR Flex connector, W2 briefing generator, W3 WhatsApp delivery, W4 storage+connect flow, W5 scheduler, W6 launch hardening) + the deferred execution swap.
- **Pivot banners** added atop `PRIORITIES.md` (with the active W-checklist), `CLAUDE.md`, and `CONTEXT_TRANSFER.md`; 030 marked PAUSED in `STATUS.md` + `PRIORITIES.md`.

**Build order (prove the riskiest externals first, per the 002/006/025 lesson):** W1 (can we read a user's IBKR holdings?) is the suggested first build — biggest unknown, gates everything; W3 (can we deliver via WhatsApp?) close second. No waitlist code written yet — awaiting go-ahead on W1.

**Kept, no rollback:** 027/028/029 (applied). PostHog (027) is reused for the waitlist conversion funnel. P7-NOTIFY backlog idea is effectively superseded (it became the product).

---

## 2026-06-05 · W1 drafted — IBKR Flex connector (pivot, read-half of the bridge)

First build of the waitlist pivot. Drafted `proposed_changes/W1-ibkr-flex-connector/` — a read-only IBKR **Flex Web Service** client, mirroring `fmp_client.py`'s mock-first external-provider shape.

**Files:** `backend/ibkr_flex.py` (2-step `SendRequest`→`GetStatement` with 1019 "in progress" poll/backoff; defensive `parse_flex_statement` merging Open Positions + MTM per-position day-P&L + instrument names by symbol, plus NAV/ChangeInNAV/MTD-YTD/FX; `IBKRFlexError` with codes; `ibkr_flex_enabled()`; mock-first `get_portfolio_snapshot()`), `backend/data/mock_flex_statement.xml` fixture, `scripts/ibkr_flex_probe.py` (live first-real-run — dumps raw XML to `/tmp/ibkr_flex_raw.xml`), `scripts/test_W1_ibkr_flex.py`, additive `.env.example` block (with the Flex Query setup: Open Positions + NAV + MTM essential; XML / Last Business Day; base-currency P&L on; currency rates yes; audit no). No new dependency (httpx + stdlib xml.etree).

**Design choices:** W1 is ONLY the connector — **not** an agent tool (briefing is a system job, threat 1), and not W2–W5. Credentials are **params** on `fetch_flex_statement(token, query_id)` so W4's per-user encrypted-token store plugs in with no refactor. Errors surfaced, no silent mock fallback (the codebase rule).

**Verified offline: 26/26.** A real bug was caught during verification and fixed: the **ElementTree "childless element is falsy" gotcha** — `eq = _find(a) or _find(b)` picked the wrong element when the first match was a leaf (attributes only, no children), so NAV total + MTD/YTD parsed as None. Fixed by branching on `is None` instead of `or` for the three multi-spelling element lookups (NAV / MTD-YTD / Realized-Unrealized). Good catch-by-test; documented inline.

**Known unknown (expected follow-up):** exact IBKR tag/attribute names vary by Flex version. The parser is deliberately tolerant (`_attr(el, *aliases)`, namespace-strip, audible `log.info` on missing sections) and the fixture is a best-effort guess. **Next: run `scripts/ibkr_flex_probe.py` with the acquired token+query id** → reconcile any attr-name gaps + the fixture against the real statement (same verify-against-the-live-dependency pattern as FMP fields 006/009 and the Mem0 v3 API 026). Then W3 (WhatsApp delivery) or W2 (briefing generator).

## 2026-06-05 · W1 real-run fix #1 — GetStatement host fallback (gdcdyn → ndcdyn)

First live probe of the IBKR Flex connector (W1 was applied to live). **The token + query ID work** — SendRequest (against `ndcdyn.interactivebrokers.com`) returned a real ReferenceCode. But step 2 failed: IBKR's SendRequest `<Url>` pointed at **`gdcdyn.interactivebrokers.com`**, which **doesn't resolve** on this machine (`httpx.ConnectError: [Errno 8] nodename nor servname` — DNS EAI_NONAME), even though the `ndcdyn` host SendRequest used is fine.

**Fix (in the W1 proposal):** `_http_get` now tags DNS/TCP failures `ibkr_flex_connect_error`; `_get_statement` tries the returned `<Url>` first and, on a connect error, **falls back to GetStatement on the same host/service as SendRequest** (`_GETSTATEMENT_FALLBACK` = ndcdyn …/FlexWebService/GetStatement, confirmed reachable) via `_candidate_get_urls`. Faithful (uses IBKR's Url when reachable) + resilient. Offline test **27/27** (added a host-fallback case). Changed files: `backend/ibkr_flex.py` + `scripts/test_W1_ibkr_flex.py`; probe/fixture unchanged.

**Re-sync needed:** W1 was applied before this fix, so live `backend/ibkr_flex.py` must be re-copied from the proposal, then re-run the probe. **Still pending:** the statement actually downloading + parsing → only then can we do the attr-name reconciliation (the original "known unknown"; the run hasn't reached the XML yet).

## 2026-06-05 · W1 real-run fix #2 — parser reconciled to the live Flex XML (VERIFIED)

The probe (post-fix-#1) downloaded the real statement (acct U19883362, 98 KB) and the parse exposed the predicted attr/shape gaps. Reconciled `parse_flex_statement` against the actual XML:

- **Daily P&L attr is `total`** on `MTMPerformanceSummaryUnderlying` (not `mtmPnl`) — `day_pnl` was null for every position.
- **`OpenPositions` emits `SUMMARY` + `LOT` rows** per symbol; the LOT row has empty `percentOfNAV` and was overwriting the SUMMARY value → kept SUMMARY rows only.
- **Holdings = OpenPositions only.** The MTM section also lists cash legs (HKD/USD) and symbols sold today (AAOI, RONB); my prior code created position slots from MTM → they leaked into `positions`. Now MTM/SecuritiesInfo only *enrich* existing holdings.
- **Base currency = the NAV section's `currency` (HKD)**, not a position's native USD (positions are USD; account base is HKD). Added `position_value_base` (`positionValueInBase`) + `fx_rate_to_base` so W2 can present consistently in base.
- **Two `EquitySummaryByReportDateInBase` rows** (prior + current) → pick the one matching the statement `toDate` (`as_of`); ChangeInNAV is authoritative for total/prev_total.
- **Performance reads the account Total rows** (`symbol=""`) of `MTDYTDPerformanceSummary` (mtd/ytd + realized MTD/YTD) and `FIFOPerformanceSummaryInBase` (realized/unrealized) — previously it grabbed the first per-symbol row.

**Verified against the live statement:** base HKD; 8 holdings (AAPL/CLSK/EOSE/GWRE/META/NBIS/NOW + a T-bill) each with day_pnl + pct_of_nav + description; NAV total 889,051 HKD (today), prev 898,999; change_in_nav.mtm −9,963.53; perf mtd −6,955 / ytd +30,979. Cash + sold-today symbols correctly excluded. The fixture (`mock_flex_statement.xml`) was rewritten to mirror the real shapes (HKD base, SUMMARY+LOT, `total` MTM, Total rows) so the offline test guards these; **32/32**.

**Re-sync to live** (W1 was applied pre-fix): re-copy `backend/ibkr_flex.py`, `backend/data/mock_flex_statement.xml`, `scripts/test_W1_ibkr_flex.py`. **W1 is now done & live-verified** — the read-half of the bridge works end-to-end. Next: W3 (Twilio WhatsApp) or W2 (briefing generator). Note for W2: the account is HKD-base with USD holdings — present P&L in base (HKD) using `day_pnl`/`position_value_base`; today is a broad down day, good narrative test material.

---

## 2026-06-07 · W2 — briefing generator → real market context (news + macro)

Built **W2** (`proposed_changes/W2-briefing-generator/`), the middle of the bridge: W1's Flex snapshot → a Claude-written **WhatsApp narrative** brief. Continues from W1 (done & verified, logged 06-05).

**The generator** — new `backend/briefing.py` + `prompts/briefing_system.md`. A **system-side** generator (NOT an agent tool, threat 1): a single **tool-less** `messages.create`, deliberately not the `run_agent` loop. "**Raw facts in, prose out**": `compute_brief_facts` does *all* arithmetic in Python (NAV, overnight Δ+%, per-mover base-ccy day P&L+%, MTD/YTD) with pre-formatted `*_display` strings; the LLM copies them verbatim and only writes prose — same trust mechanism as `get_full_research`, so "no number without a source / copy digit-for-digit" holds without a validator. **Base ccy is load-bearing** (account HKD, holdings USD → quote base-ccy figures). Mock-first (`USE_MOCK_BRIEFING`/no key → deterministic template); real-path failure raises `BriefingError`. `build_briefing(token, query_id)` is param-based so W4's per-user store plugs in. Live-verified over the mock fixture AND real IBKR (U19883362).

**Real-run fix #1 (trust #1/#5) — suppress mock context in a *live* brief.** The first live run quoted **hardcoded-mock macro** (`S&P +0.4%, VIX 14.8, FOMC 14:00`) as if real — `get_macro_snapshot`/`get_company_news` are mock-only and always return `is_mock:true`. Fix: `gather_market_context` drops any `is_mock` context layer when the *snapshot* is live (`_real_enough`); mock-demo briefs keep mock context (labelled). Also hardened the proposal test's path-resolution (a stale *applied* `briefing.py` was shadowing the proposal copy — the multi-`backend/`-on-`sys.path` trap).

**Real "why" layer — `backend/news_context.py` (new).** `fetch_recent_news` pulls per-mover headlines from **yfinance** (free, no API tier → covers small/mid-caps FMP's free tier misses; defensive parse of both `.news` shapes). Deliberately a standalone system-side helper, **NOT** in `tools/` — leaves the chat product's `get_company_news` mock + `test_P1_003` untouched, and avoids the shared-package shadowing problem. Prompt tightened to **ban invented qualifiers** (no "on light volume"/"risk-off" unless a headline says so). Live: GWRE −10% → *"plummeted post-Q3 earnings per StockStory"*, NBIS → *"AI selloff per Motley Fool"*, EOSE → honest *"no fresh catalyst"*.

**Real "what it means" layer — `fetch_macro_context`** (same module): index futures (`ES=F`/`NQ=F`), `^VIX`, `^TNX`, commodities via `fast_info` (last + prev close → overnight %), each with a `display` string. No Fed/earnings-calendar (no reliable free source — that was the fix-#1 hallucination). `compute_brief_facts._macro_indicators` normalizes both the real indicators list and the legacy mock dict into one shape; the prompt treats macro as real-data-to-cite-when-present, omit-when-empty. Live close: *"Nasdaq futures -3.78%, S&P 500 futures -2.07%, VIX 21.51 (+39.8%)"* — the VIX spike now *earns* the "risk-off" framing the earlier version invented.

**Tests:** offline **55/55** by end of the macro layer. Probes added: `briefing_probe.py`, `news_probe.py`, `macro_probe.py`. Discipline note: every real-run fix here came from *verifying against the live dependency* (mock-as-real macro, the yfinance `.news` shape) — the 002/006/025 pattern.

---

## 2026-06-08 · W2 recency cap → W3 → W4 → W4b → W5 — the bridge runs end-to-end (LIVE)

The day the whole pivot worked unattended: a real IBKR portfolio → a Claude narrative → delivered to WhatsApp, for a user onboarded through the UI. Each stage is its own `proposed_changes/W*/` proposal (offline test + live-verify).

**W2 headline recency cap.** `fetch_recent_news` returned the newest N headlines with **no age floor** → the first live brief cited a 4-day-old EOSE valuation piece for a same-day −12.38% drop. Fix (reusing the existing `since` filter): `gather_market_context` passes `_news_since(as_of) = as_of − BRIEFING_NEWS_MAX_AGE_DAYS` (default 2). **Anchored to `as_of`, not wall-clock `now`** — so replaying/testing days later keeps the *relevant* news (the key design call). Live: EOSE's stale headlines dropped → honest *"no specific headline"*; GWRE/NBIS/CLSK's fresh ones kept. **56/56.**

**W3 — WhatsApp delivery** (`backend/whatsapp.py`, Twilio). System-side (never an agent tool), mock-first (`USE_MOCK_WHATSAPP`), `WhatsAppError`, `send_whatsapp`/`send_briefing`, optional `twilio` dep group. **LIVE-VERIFIED:** real Sandbox send of a test body AND the real W2 brief, both received (Twilio `sid`). Real-run fix: a creds-set-but-`twilio`-not-installed case now raises the actionable `whatsapp_twilio_not_installed`. **uv-sync footgun caught:** `uv sync --group whatsapp` *prunes* other optional groups (it uninstalled `mem0ai`/`posthog`) — always sync all groups (`--group whatsapp --group memory --group auth --group dev` / `--all-groups`); flagged a P6 deploy gap (the Dockerfile installs no optional groups → twilio/mem0ai absent on Railway). **32/32.**

**W4 — storage + connect** (backend). App-level **Fernet** encryption (`token_crypto.py`, via the existing `cryptography` dep — chosen over Vault/pgsodium). `connections.py` with **two access models**: user-JWT + RLS (connect) and **service-key admin** (the cron read/log — the *first* legit admin path; `db.py` never used the service key). `waitlist_api.py` (`/api/ibkr/connect|connection|opt-in`, `/api/waitlist`), `db/schema_waitlist.sql` (`waitlist_signups`/`ibkr_connections` encrypted/`briefing_deliveries` + RLS). **LIVE-VERIFIED:** connect → **ciphertext at rest** (`gAAAAAB…`) → token-free read-back → `w4_connect_probe.py --fetch`: decrypted token == original AND pulls real IBKR (U19883362, 8 holdings). Two real-run fixes: opt-in 500 (bare UPDATE → `.eq("user_id")`); waitlist `{"ok":false}` (insert-only RLS + default `return=representation` did an RLS-denied post-insert SELECT → `returning=minimal`, 23505→idempotent). **37/37.**

**W4b — connect UI** (`proposed_changes/W4b-connect-ui/`, frontend). New `/connect` route (separate from the paused chat `/`): hero + waitlist email + inline magic-link sign-in + connect form (Flex token masked, E.164 WhatsApp, consent checkbox) + connection status with **Pause/Resume**; `lib/waitlist.ts`; `next.config.js` rewrites (`/api/waitlist`, `/api/ibkr/*`); a "📩 Daily WhatsApp briefing" link from the chat `/`. `tsc --noEmit` clean. **LIVE-VERIFIED:** UI onboarding → `waitlist_signups` row (`source=connect-page`) + encrypted `ibkr_connections` row; Pause/Resume moved the cron's view (0↔1).

**W5 — scheduler** (`backend/scheduler.py` + `scripts/run_briefings.py`). Orchestrates per opted-in user: `list_active_connections_admin` (W4) → `build_briefing` (W2) → `send_briefing` (W3) → `log_delivery_admin` (W4). Resilience: per-user failure isolation, retries + backoff, cost cap (`BRIEFING_MAX_USERS_PER_RUN`), `--dry-run` (build, no send/log), non-zero exit for cron alerting. **28/28.** **🎯 Milestone:** `run_briefings.py --max-users 1` → `sent:1`, a **real WhatsApp brief delivered** (Twilio `sid SMbe6f…`) to a user who self-onboarded via `/connect`. The full **land → connect IBKR → daily WhatsApp brief** loop works end-to-end against real IBKR + Twilio.

**Process:** every stage proved the riskiest external first and *verified against the live dependency* — that discipline caught every real-run fix (mock-context-as-real, yfinance `.news` shapes, the two PostgREST RLS/mass-update gotchas, the uv-sync pruning, the twilio-not-installed message). `PRIORITIES.md` waitlist checklist updated (W1–W5 + connect UI done & live-verified). **Daily *scheduling* itself (firing each morning) = Railway cron, deferred to P6.**

**Forward:** **W6.1 (STOP/unsubscribe webhook)** drafted (`proposed_changes/W6-stop-webhook/`, 14/14 — Twilio signature validation + STOP/START → `opt_in` via service-key admin) — the compliance gate. Remaining W6 (PostHog funnel, `permalink` web copy, Business sender+templates, cost caps) + **P6 deploy** (Railway cron + Dockerfile groups + secrets).

---

## 2026-06-09 · W6 launch hardening — opt-out (the long pole), funnel, a security catch, and the permalink

Built the W6 hardening track as focused sub-proposals; several **live-verified**. The recurring theme again: Twilio's *real* behavior only surfaced live, and eyeballing live PostHog data caught a credential leak no offline test could.

**W6.1 — STOP/unsubscribe webhook** (`proposed_changes/W6-stop-webhook/`, 14/14). New `backend/webhooks.py` `POST /api/twilio/inbound`: validates `X-Twilio-Signature` (Twilio `RequestValidator`; bad/missing/tampered → 403, no DB write), parses Body → flips `opt_in` for the `From` number via new `connections.set_opt_in_by_whatsapp` (service-key admin). 2-line `main.py` include. The compliance gate.

**W6.1b — make opt-out actually work on Twilio** (`W6.1b-optout/`, 16+9+9). Two live findings reshaped it: (1) Twilio's **Advanced Opt-Out INTERCEPTS STOP/START/HELP** — they're never forwarded to the webhook (texting STOP produced no POST); (2) WhatsApp opt-out is **ASYNC** — `messages.create` returns `queued`, then delivery `failed` with **ErrorCode 63015** (confirmed by fetching the message). So: (a) added **PAUSE/RESUME** (non-reserved → Twilio *does* forward them → the in-WhatsApp control users can trigger today; verified live); (b) the real STOP mirror is a **status-callback** — `whatsapp.send_whatsapp` sets `status_callback`, new `POST /api/twilio/status` flips `opt_in` on `failed`/`undelivered` + opted-out `ErrorCode` (`TWILIO_OPTED_OUT_CODES`=21610,63024,63015); (c) a synchronous SMS-style catch (`WhatsAppError.retryable`, scheduler skips-not-retries). **Consent decision (locked):** no auto-resume on a technical reconnect — re-subscription is explicit (RESUME / `/connect` Resume / re-connect form). **Live-verified:** PAUSE/RESUME flip `opt_in`; STOP → 63015 → status callback (204) → `opt_in` false.

**W6.2 — PostHog waitlist funnel** (`W6.2-posthog-funnel/`). Reuses applied 027 (PostHog already inits for all routes; `$pageview` = "land"). Typed helpers fire from `/connect`: `waitlist_joined{source}` / `connect_started` / `connect_completed` / `connect_failed` / `briefing_opt_in_changed{opt_in}`. **PII-safe** (no email). Verified all five fired with correct props.

**W6.2b — PostHog URL PII scrub (security) + connect_started accuracy** (`W6.2b-posthog-pii/`). 🔴 Verifying W6.2's live export caught a **credential leak**: the auto-`$pageview` recorded the Supabase magic-link `$current_url` **fragment** → `access_token` + long-lived **`refresh_token`** sent to PostHog (affects any magic-link landing page, incl. chat `/`). Fix: `scrubUrl` + `sanitize_properties` in `analytics.ts` — drops the fragment + redacts auth query params (implicit & PKCE). 🟡 Also a `connLoaded` gate so `ConnectForm`/`connect_started` don't fire during the load race (it was firing spuriously for already-connected users). **LIVE-VERIFIED** post-apply export: `/connect` `$current_url` = `http://localhost:3000/connect`, **0 token leaks** (was 2), **0 spurious connect_started** (was 6). ⚠️ **Open operator action:** revoke the previously-leaked test `refresh_token` (Sign out / Supabase revoke) + delete the old leaked events.

**W6.3 — brief web permalink** (`W6.3-permalink/`, 18/18). The link a WhatsApp template (W6.4) will carry. New `published_briefs` table (token PK, body, 7d expiry; **RLS on, no policy → service-role-only**, a capability link — token-gated + expiring + noindex; the "stored access-controlled" body path the pivot decision allows). `backend/published_briefs.py` (`publish_brief`/`get_published_brief`, `secrets.token_urlsafe(32)`, fail-closed expiry, reuses W4 `_admin_client`), `backend/brief_api.py` (`GET /api/brief/{token}`, public, 404 on miss/expiry — no enumeration), `frontend/app/b/[token]/page.tsx` (Next dynamic route — one folder serves all tokens; `*bold*`→React, no HTML injection) + `app/b/layout.tsx` (`robots:noindex`) + `lib/brief.ts` + next.config rewrite. `scheduler.py` publishes each brief (best-effort, `PUBLISH_BRIEFS`) + appends the link to the sent body. **Verified served + rendered over http** (backend 200 with real content; `/b/<token>` page 200). **Note:** `PUBLIC_BASE_URL=localhost` only works in the dev-machine browser (and beware `localhost`→https HSTS — use `127.0.0.1`); a phone tap needs a public frontend origin (a frontend ngrok tunnel, or Vercel in P6).

**Docs:** `PRIORITIES.md` waitlist checklist + `CONTEXT_TRANSFER.md` pivot-status banner updated to W1–W6.3 live-verified; each stage's `proposed_changes/W*/STATUS.md` row reflects status. **Process note (carried):** the live-dependency findings this block — STOP-not-forwarded, async-opt-out/63015, the PostHog token leak, the localhost→https HSTS — were all things offline tests structurally can't model; the live probes earned their keep again (002/006/025 lineage).

**Next decision:** **P6 deploy** is the efficient "complete the system" step — it operationalizes the whole built stack (autonomous daily cron, phone-reachable permalinks, stable Twilio webhook/status-callback URLs) and retires the ngrok/localhost friction. W6.4 (Business sender + templates) is **Meta-gated** (start the verification in parallel, can't be the next *build*); W6.5 (cost caps) is a small unblocked add; **P5 is the PAUSED chat-MVP's lockdown — not part of the waitlist product.**

---

## 2026-06-10 · P6 deploy — config artifacts + runbook drafted

Acted on the prior session's "next decision": drafted **`proposed_changes/P6-deploy/`** — the artifacts that operationalize the live-verified W1–W6.3 loop. **No code change, no new dep, no offline test** (deploy configs + an operator runbook).

**Confirmed the Railway-vs-Vercel split is not an either/or** (Nicholas asked): the architecture is **locked** — Railway hosts the **backend web service** (SSE loop, Twilio webhooks, permalink API — needs a long-lived server) *and* the **briefing cron** (a 2nd service on the same image); Vercel hosts the **frontend**. The cron is a Railway cron service running `uv run python -m scheduler` directly — deliberately **no public trigger endpoint**, because the briefing send is a system job, never an agent tool (SECURITY threat 1). A Vercel cron hitting an HTTP route would have created exactly that forbidden trigger surface.

**Built:**

- `backend/railway.cron.json` (NEW) — cron service config: `startCommand: uv run python -m scheduler`, `cronSchedule: 0 23 * * 1-5` (UTC = ~07:00 HKT, tunable), `restartPolicyType: NEVER`, no healthcheck. `scripts/run_briefings.py` isn't in the backend build context, so the cron invokes `scheduler.__main__` (which `run_daily_briefings()` + prints a JSON summary; cost-capped by `BRIEFING_MAX_USERS_PER_RUN`; per-user failures isolated → `briefing_deliveries.status='failed'`).
- `frontend/vercel.json` (Edit) — removed the stale `CHANGE-ME` rewrites (2 routes); `next.config.js` owns all ~6 rewrites via `NEXT_PUBLIC_API_URL`, so the vercel.json block was a partial/stale override. Kept framework + install/build commands.
- `docs/DEPLOY.md` (NEW) — the authoritative runbook: Supabase schema (3 files) → Railway backend → Railway cron → Vercel frontend → **cross-wire origins** (`PUBLIC_BASE_URL`=frontend, `NEXT_PUBLIC_API_URL`=backend, `CORS_ALLOW_ORIGINS`) → Twilio webhooks (`/api/twilio/inbound` + `/api/twilio/status`, exact URLs for signature match) → end-to-end smoke test. Plus a **secrets matrix** (every var × web/cron/frontend × prod value) and the production mock-flag posture (`REQUIRE_AUTH=1`, `USE_MOCK_IBKR/BRIEFING/WHATSAPP=0`, `USE_MOCK_TA=1`).

**Verified correct, not changed:** `backend/Dockerfile` already installs `--group whatsapp --group memory` (the W3 uv-sync-prunes-groups gap is closed); `backend/railway.json` (web service) is right. Confirmed the `auth` optional group (only `posthog`) isn't imported by any backend `.py` — not needed in the image.

**Checks:** both JSON configs parse; `python -m scheduler` is a valid cron entry (`__main__` + `run_daily_briefings` present, `py_compile` ✓, top-level imports resolve from `/app`).

**Decisions/notes:** P6 is **operator-applied** — the actual cloud steps (create services, set secrets, point Twilio) need Nicholas's accounts; my deliverable is the apply-ready configs + runbook. Cron exits 0 even on per-user failures (monitor `briefing_deliveries` for `failed`); richer exit-code alerting would need `scripts/` added to a cron image — noted, not done.

**Next:** **W6.5 per-user cost caps** is the recommended next *build* (small, unblocked, offline-testable — adds daily-send idempotency so a cron misfire/retry can't double-bill a user, on top of the coarse `BRIEFING_MAX_USERS_PER_RUN`). **W6.4** (Business sender + templates) stays **Meta-gated** — start verification in parallel; it slots in post-deploy with only Twilio config + a template-SID env, no topology change.

---

## 2026-06-10 (later) · P6 DEPLOYED LIVE — the whole pivot loop runs in production 🎯

The deploy went all the way: Nicholas applied the P6 proposal and stood up **Railway (backend web + briefing cron) + Vercel (frontend) + Supabase**, and the full `land → connect IBKR → daily WhatsApp brief` loop now runs against real cloud infra. Every failure surfaced **only on the live deploy** (builds/images were always fine) — the same "verify against the live dependency" lesson as 002/006/025, now applied to infra. Six real-run fixes, each diagnosed from the actual logs Nicholas pasted:

**Backend (Railway web service) — two boot crashes, both `Network › Healthcheck` "service unavailable":**

1. **No writable HOME.** `useradd` ran without `--create-home`, so `/home/app` didn't exist → `uv run` couldn't create its cache under `$HOME/.cache/uv` → crash before binding the port. Fix in `Dockerfile`: `useradd --create-home` + `ENV UV_NO_CACHE=1` (the venv is fully built at image time, so the runtime cache is pure liability).
2. **Unexpanded `${PORT}`.** `railway.json`'s `startCommand` overrides the Dockerfile `CMD`, and Railway runs the override in **exec form (no shell)** → `--port ${PORT:-8000}` reached uvicorn as the literal string → `invalid integer` crash. Fix: **drop `startCommand` from `railway.json`** so the Dockerfile `CMD` (`["sh","-c", …]`, which expands `$PORT`) runs. Backend then live — `Uvicorn running on 0.0.0.0:8080`, `/healthz 200` (all flags true; Railway injected `PORT=8080`).

**Frontend (Vercel) — one build failure:**
3. **Scheme-less `NEXT_PUBLIC_API_URL`.** `next build` failed `Error: Invalid rewrites found` — `next.config.js` interpolates the env var into rewrite destinations, and a bare host (`…railway.app`, no `https://`) violates Next's `/`-or-`http(s)://` rule. Fix: set the Vercel var to `https://…` **and** harden `next.config.js` (real-run fix #3) to auto-prepend `https://` + strip a trailing slash so it can't recur. Also required **Root Directory = `frontend`** (no root `package.json`) + **Node 22**. Frontend then live — `/connect` + `/` return 200.

**Supabase + auth — two config gotchas (no code change):**
4. **"Invalid API key" red herring.** My first validation test (`curl /rest/v1/` with the anon key) was wrong — that root endpoint is **`service_role`-only**, so a *valid* anon key returns "Invalid API key / only service_role" there. Corrected test: `GET /auth/v1/settings` with the anon key (→ 200 = valid). The anon key was fine all along; the swagger dump also confirmed all 8 tables migrated.
5. **Magic link redirected to `localhost:3000`.** Supabase **Site URL** was still `http://localhost:3000`, and the code's requested `emailRedirectTo: window.location.href` (the Vercel `/connect`) **wasn't in the Redirect URLs allow-list** → Supabase silently fell back to the Site URL. Fix (Auth → URL Configuration): Site URL = the Vercel origin + add `<vercel>/**` to Redirect URLs (kept `localhost:3000/**` for dev). Sign-in then worked.

**Cron + delivery:**
6. **WhatsApp "queued" but never delivered.** A manual run (`scripts/run_briefings.py --max-users 1`) reported `sent:1`, real Twilio `sid`, `is_mock:false` — the whole pipeline (IBKR fetch → Claude brief → Twilio accept) worked. But `queued` is the *creation* status; delivery is async and silently failed on the **Sandbox 24-hour window** (freeform only within 24 h of the recipient's last inbound). Re-sending `join <phrase>` to the sandbox reopened it → the brief landed on the phone. (This window limit is exactly what W6.4 templates remove.)

Also clarified, not bugs: Railway **cron runs only on its UTC schedule** (no "Run now"; config-as-code overrides the dashboard schedule; a redeploy doesn't fire it) — manual test via local run / near-future `cronSchedule` / `railway run`; and the **public domain is generated** (Settings → Networking), it isn't auto-assigned, and serves only once a deploy is healthy.

**Decisions/notes:**

- **Locked split confirmed in practice:** Railway = backend web + cron (2nd service, `python -m scheduler`, **no public trigger endpoint** — threat 1); Vercel = frontend. Not an either/or.
- **`/healthz` `*_configured` flags are presence checks, not validity** — a present-but-wrong key passes health and fails at runtime (bit us on the Supabase key hunt).
- All six fixes folded into the `proposed_changes/P6-deploy/` proposal (`Dockerfile`, `railway.json`, `next.config.js` are now edits, not "verified unchanged"); `docs/DEPLOY.md` gained a full **Troubleshooting** section (each gotcha above), a corrected anon-key validation test, the "generate the domain" step, and the cron-trigger methods. `STATUS.md` row marked **✅ STACK LIVE**.
- **Local-run caveat that mattered:** the manual run only picks up a connection made via the deployed site if local `.env` shares the same Supabase project **and the same `FLEX_TOKEN_ENC_KEY`** (else the row is undecryptable → skipped). And the local run's permalink is `localhost` because local `PUBLIC_BASE_URL` is localhost — the Railway cron uses the Vercel origin.

**Status: P6 fully deployed & live-verified end-to-end.** A real IBKR portfolio → Claude narrative → WhatsApp delivery, for a user who self-onboarded through the deployed `/connect`. **Next:** **W6.5 per-user cost caps** (daily-send idempotency — recommended next build); **W6.4** Business sender + templates (Meta-gated, removes the 24 h window); and operator polish (set the Railway cron's `PUBLIC_BASE_URL` to the Vercel origin; point Twilio webhook/status URLs at the Railway backend).

---

## 2026-06-10 (later still) · W6.5 — per-user cost caps (daily-send idempotency)

Drafted `proposed_changes/W6.5-cost-caps/` — the recommended post-deploy build. Closes the gap the live P6 run exposed: `BRIEFING_MAX_USERS_PER_RUN` only caps *per run*, so a second run in a day (a manual test + the scheduled cron, or an operator re-running `run_briefings.py`) would re-spend an IBKR fetch + a Claude call **and** double-message the user.

**What:** new `connections.already_delivered_since_admin(user_id, since_iso)` — a service-key presence check on `briefing_deliveries` (`status ∈ {sent,queued}` only, so a prior `failed`/`skipped` never blocks a retry; `limit(1)`). `scheduler.run_daily_briefings` calls it **before building** each brief (via `_recently_delivered`, window = now − `BRIEFING_MIN_RESEND_HOURS`); a hit logs `skipped`/`already_sent_recently` and `continue`s — saving the fetch + Claude + send. `--force`/`force=` bypasses; `BRIEFING_DEDUP=0` disables; dry-run ignores it (it's for testing the build).

**Design calls:** (1) **time-window, not calendar-day** — default **12 h** sits between a minutes-apart retry (caught) and the ~24 h next daily run (allowed), dodging the midnight-UTC edge a calendar-day key would have. (2) **Pre-build skip** (not post-build) — that's where the Claude cost is, so the guard runs before the fetch. (3) **Fail-open** — if the dedup probe errors, the send proceeds (a missed brief is worse than a rare duplicate), logged WARNING; matches the scheduler's isolate-and-continue posture. (4) **`sent`/`queued` only** in the count — a previously-failed delivery must still be retryable.

**Verified:** offline **17/17** (skip-before-build, fresh-sends, dry-run-ignores, --force-bypasses, DEDUP=0-disables, probe-error-fail-open, query-wired-and-async). `py_compile` clean; live files untouched (proposal copies edited). Built the safe way — `cp` live files into the proposal, edit *those*, test resolves the proposal backend first (the multi-backend-on-`sys.path` pattern). **Apply:** `cp` the 4 files (no `uv sync`); live-verify by running `--max-users 1` twice (2nd → `skipped`). **Remaining W6:** W6.4 (Meta-gated templates) + operator polish from P6.

**W6.5 applied + LIVE-VERIFIED same day** (Nicholas): `run_briefings.py --max-users 5` returned `skipped:1` / `already_sent_recently` for the seeded user (briefed earlier that day) — the guard fired, no second WhatsApp, no re-spend. Working as designed.

---

## 2026-06-10 (later still ×2) · W6.4a — Meta/Twilio onboarding runbook (the W6.4 prerequisite)

Question that triggered it: *which is higher priority, W6.4 or Meta verification, and does Meta verification affect W6.4?* Answer: **Meta verification gates W6.4 and is the long pole** (3 external Meta reviews you don't control: business verification days→weeks, display-name + template approval hours→days), while W6.4's code is ~an afternoon. They're sequential-with-overlap, not competing — **start the Meta track now, in parallel**.

Drafted `proposed_changes/W6.4a-meta-twilio-onboarding/` — an **operator runbook, no code** (W6.4's `whatsapp.py` template path is untestable until the `ContentSid` exists; building it now would be premature). New `self_management/WHATSAPP_BUSINESS_SENDER.md`: the critical-path map (Steps A upgrade Twilio → B WhatsApp Sender embedded signup/WABA/OTP/display-name → C `daily_portfolio_brief` **Utility** template in Content Template Builder → D Meta business verification), the **2 upfront decisions** (a sender number not on consumer WhatsApp; Utility vs Marketing), the **ready-to-submit template** (body + `{{1}}`=as_of, `{{2}}`=W6.3 permalink, sample values), and the **W6.4 code-consumption contract** (`TWILIO_WHATSAPP_FROM` business number + `TWILIO_BRIEF_TEMPLATE_SID` HX… + a `content_sid`/`content_variables` send branch; full narrative stays at the permalink).

**The load-bearing insight:** the **first proactive brief to the cohort is gated on sender + approved template + billing — NOT on full business verification.** A fresh WABA sends at the lowest tier (fine for 5–10 users) while verification (the slowest step) lifts limits in parallel. So start verification first, but don't block launch on it. **Handoff:** when sender + template land, W6.4 is small + no P6-topology change. `TWILIO_SETUP.md` left untouched (its Sandbox content is still accurate).

**Onboarding-run findings folded into the runbook as Nicholas executed it:** (1) **number ≠ account upgrade** (separate ~$1–2/mo buy; needn't be Twilio's; needs SMS+Voice for the OTP, not on consumer WhatsApp — pick Local/Mobile not Toll-Free); (2) **business verification can't be forced on an empty portfolio** — the Security Center honestly shows *"does not need to be verified"* until a WABA exists, so D is *triggered by* B, not a standalone first step (and **"Meta Verified" is a paid blue badge — not the free messaging verification, don't buy it**); (3) **business vs personal Twilio + whose Meta portfolio** — use the *operating entity's* portfolio (verification verifies the entity); (4) **no business registration number is not a blocker** — the Twilio compliance profile can be **Individual** (personal ID), or buy a **US local number** (no regulatory bundle, no 10DLC since it's a WhatsApp-only sender); using a **personal WhatsApp number means losing personal WhatsApp on it** (one number = app OR API, never both); changing the sender number later is a re-registration but templates/display-name survive (they live on the WABA).

## 2026-06-11 · W6.4 — WhatsApp template send path (built with the Business sender still deferred)

Nicholas completed onboarding **Steps A/C/D**, **sidestepped B** (staying on the Twilio Sandbox for now), and asked to "continue as much of W6.4 as possible." So built the **code half** — `proposed_changes/W6.4-template-send/` — which is fully buildable + offline-testable without B, and **flag-gated** so the live Sandbox is untouched until the Business sender lands.

**What:** `whatsapp.send_whatsapp` gains optional `content_sid`/`content_variables` (a Content-template path beside the freeform one — empty-body check skipped in template mode since the *approved template* is the content; mock logs the template id+vars; the W6.1b status-callback is wired on both). `send_briefing` now **chooses template-vs-freeform**: if `TWILIO_BRIEF_TEMPLATE_SID` is set **and** a `permalink` is present → send the approved template (`{1}`=formatted as_of, `{2}`=W6.3 permalink, full narrative stays at the permalink); else → freeform `text` (Sandbox / today / inside-24h / publish-failed). `scheduler.py` got one line — pass the raw `permalink` through to `send_briefing` (built on the **applied-W6.5** file, so the dedup guard is preserved). `_fmt_as_of` (`2026-06-09`→`09 Jun 2026`, degrades to raw).

**Design call — flag-gated, zero-risk apply:** unset `TWILIO_BRIEF_TEMPLATE_SID` → freeform exactly as before (so applying this on the Sandbox today changes nothing); set it (+ the Business `From`) later → template. **Go-live is two env vars on the Railway cron, no code or P6-topology change.** This is the max progress while B is deferred; the first *real* template send is verified once W6.4a's template is approved (Sandbox template testing is limited).

**Verified:** offline **22/22** (template-vs-freeform real-path `messages.create` kwargs, empty-body-skip-in-template, `send_briefing` decision + no-permalink fallback + no-template-env freeform, date fmt, scheduler-passes-permalink). `py_compile` clean; live files untouched (proposal copies edited — `cp`-then-edit pattern). **Apply note:** re-copying `scheduler.py` means re-running the W6.5 test too (still green). **Remaining for the proactive cadence:** W6.4a Steps B + template approval (external), then flip the two env vars; plus the small P6 operator polish (cron `PUBLIC_BASE_URL`→Vercel, Twilio webhook/status URLs→Railway).

**W6.4 applied + test made format-agnostic.** Nicholas applied it, customised `_fmt_as_of` to `%Y/%m/%d` (`2026/06/09`) — a valid preference — which surfaced that the test had pinned `"09 Jun 2026"`. Fixed the test to assert the *wiring* (`{1} == _fmt_as_of(...)`, `{2}` = permalink; date transformed + year present) not a cosmetic format → robust to any format; re-cp + 22/22.

## 2026-06-11 · W6.6 — security hardening for the LIVE waitlist product

With W6 code-complete and P6/W6.4-go-live blocked on external (Meta/Twilio) + operator steps, the substantive code-able next step was **securing the now-live public surface** (`/connect`, `/api/waitlist`, `/api/ibkr/*`, `/api/brief`, the Twilio webhooks). The waitlist subset of old P5 — **not** the paused chat lockdown. Drafted `proposed_changes/W6.6-waitlist-security/`.

**What:** NEW `backend/security.py` — a **pure-ASGI** `SecurityMiddleware` (the load-bearing design call: **not** Starlette `BaseHTTPMiddleware`, which buffers the body and **would break the `/api/chat` SSE stream**). Per-IP fixed-window **rate limit** over `/api/*`, **excluding** `/api/twilio/*` (signature-gated + Twilio-retried from shared IPs → must not 429) and `/healthz` (Railway probes every 30s); X-Forwarded-For first hop (behind Railway's proxy); in-memory (single replica); **fail-open** (a limiter bug never takes the API down); OPTIONS not counted; 429 carries `Retry-After` + the headers. **Security headers** (nosniff / frame-DENY / referrer / permissions / HSTS) set-if-absent on every response incl. SSE. 2-line `main.py` wire (after CORS → outermost). `frontend/next.config.js` `headers()` — same headers + a **CSP** allowlisting Supabase/PostHog + Next's inline/eval (flagged: verify on a Vercel preview / Report-Only first — the one header that can break the app).

**Dependency audit (run, with fixes):** `pnpm audit` → **postcss `<8.5.10`** moderate XSS (transitive via next) → `package.json` override `>=8.5.10`. `pip-audit` (against the installed venv — the `-r` mode wouldn't run because this Mac can't source-build numpy 2.4.6) → **pyjwt 2.12.1** PYSEC-2026-175/177/178/179 (the **JWT auth** lib!) → `pyproject.toml` `>=2.13.0`; **starlette 1.0.0** PYSEC-2026-161 → pinned `>=1.0.1` directly. Apply needs pyproject **before** `uv lock`/`uv sync`, then re-run the 012/015 auth tests (pyjwt bump, same API).

**Verified:** backend offline **18/18** (FastAPI TestClient: headers-everywhere incl. 429, 4th-past-cap 429 + Retry-After, `/healthz`+`/api/twilio/*`+OPTIONS exempt, per-IP isolation via XFF, fail-open, `RATE_LIMIT_ENABLED=0`). `next.config.js` `headers()` loads (CSP allowlists supabase+posthog). `py_compile` clean; live files untouched; pip-audit uninstalled from the venv after the scan. **Scoped out:** DOMPurify (moot — `/b/[token]` renders `*bold*`→React, no HTML injection); per-user token budget (per-IP covers the live surface); Redis store (single replica fine). **Operator follow-up still open (W6.2b):** revoke the leaked test `refresh_token` + delete the old leaked `$pageview` events.

**P5 (waitlist subset) CLOSED + operator checklist created (2026-06-11).** Ran the last open P5 item — a **secret-leak scan**: no `.env` tracked, no live secrets in source (the `service_role` grep hits were all comments/docs/a test fake), and the shipped frontend bundle contains **only the public Supabase anon key** (`role:anon`) — no service_role/Anthropic/Fernet/Twilio secret. So P5-for-the-waitlist is complete (rate-limit/headers/dep-audit via W6.6; auth/RLS/encryption/opt-out/PII-scrub earlier); the rest of canonical P5 is the paused chat MVP. Consolidated the **non-code remainder** into `self_management/OPERATOR_CHECKLIST.md`: P6 polish (cron `PUBLIC_BASE_URL`→Vercel; Twilio webhook/status URLs→Railway), W6.2b (refresh_token already revoked via the user deletion; PostHog project wipe + re-key left), W6.4a (Meta/Twilio sender — the long pole), and the gotchas learned. **IBKR reconnected** (`run_briefings.py --dry-run`/`--max-users 1` → `total:1`) after the accidental Supabase user deletion.

**APPLIED & LIVE-VERIFIED same day (Nicholas).** `uv lock && uv sync` bumped pyjwt 2.12.1→2.13.0 + starlette 1.0.0→**1.2.1** (uv also swept 14 orphaned pip-audit transitive deps — correct, the venv now matches the lock/prod image); auth tests stayed green (012 18/18, 015 10/10). **Live prod checks all pass:** backend `/healthz` 200 with every security header; **rate limit fires exactly at the 60/window cap** (`404×60`→`429×5`); frontend `/connect` 200 with full CSP + headers; **`/b/<token>` renders the full brief under the enforced CSP** (CSP doesn't block Supabase/PostHog/same-origin fetch). **Real-run insight — VPN vs IP-keying:** the first rate-limit attempt showed *no* 429 because a **VPN rotated the egress IP per request**, so the per-IP counter never accumulated; off-VPN (stable client IP) it fires perfectly. Not a bug — the inherent limit of IP-based limiting (a rotating-IP client partially evades it; a per-user tier / WAF would complement it, noted out-of-scope). Also a healthz-URL gotcha: `/healthz` is **backend-only** — `https://<frontend>/healthz` is a Next 404; use the Railway URL or `<frontend>/api/healthz`. Remaining nicety: eyeball `/connect` magic-link sign-in console for CSP violations (the `/b` render is already strong evidence CSP is fine).

**P6 operator polish DONE + CSP fully validated + chat-MVP P5 resumed (2026-06-11).** Nicholas set `PUBLIC_BASE_URL`→Vercel + `TWILIO_WEBHOOK_URL`/`TWILIO_STATUS_CALLBACK_URL`→Railway and pointed the Twilio console → the **waitlist system is operationally complete**. Confirmed `/connect` magic-link sign-in has **no CSP violations** (CSP fully validated). Then resumed the **paused chat MVP** to finish its **P5 lockdown** — and most of it was *already done by the waitlist work*: W6.6's rate-limit guards `/api/chat`, its CSP/HSTS headers cover the chat `/` page, the pyjwt/starlette/postcss fixes are project-wide, and RLS/auth landed in P4. The remaining chat-specific items: DOMPurify, per-user token budget, proposal 030. Built the clearest one — **proposal 032: DOMPurify sanitizer.** `SafeHtml` (in `Sources.tsx`, used by every widget) swaps its hand-rolled regex for **isomorphic-dompurify** under the same strict `['strong','em']`/no-attr allowlist — finishing the P5 "replace `dangerouslySetInnerHTML` with DOMPurify" item (the HIGH-1 regex fix held, this hands it to the battle-tested lib for mXSS/malformed edge cases; isomorphic → SSR-safe). Verified **9/9** in an isolated install (keep strong/em; strip attr/script/img-onerror/anchor/unknown-tag; bare-& + empty safe) — live frontend untouched; dep added on apply via `pnpm add` (non-stale lockfile, W6.6-postcss pattern). **Chat-MVP P5 now:** rate-limit ✅(per-IP, W6.6)/per-user-token-budget ⬜, CSP/HSTS ✅, dep-audit ✅, secret-scan ✅, RLS ✅, DOMPurify ✅(032 drafted), Alpaca-routing 030 ⏸(drafted, un-pause for multi-user), numeric-validator ✋(deliberately not built).

**032 deploy chain + per-user token budget (2026-06-11).** 032 (DOMPurify) hit two apply-time snags, both resolved: (1) the local test "module not found" was just the `pnpm add isomorphic-dompurify` step skipped (installed → 9/9); (2) Vercel build failed at the `/` prerender with `ENOENT default-stylesheet.css` because isomorphic-dompurify pulls **jsdom**, which doesn't survive Next's server bundling → fixed by **032b** (`serverExternalPackages: ['isomorphic-dompurify','jsdom']` in next.config.js — jsdom loaded from node_modules; only at build-time prerender). 032b verified via temp-apply→`pnpm build`(node22)→restore (5/5 pages, live next.config.js untouched). Also surfaced: the Bash tool defaults to **Node 20** while the user's shell is 22.22.3 — use `~/.nvm/versions/node/v22.22.3/bin` for frontend builds. **Then built proposal 034 — the per-user daily token budget**, closing the last buildable chat-MVP P5 item. New `backend/token_budget.py` (**in-memory** daily cap, opt-in `CHAT_DAILY_TOKEN_BUDGET`, fail-open, day-reset — W6.6-rate-limiter posture; Supabase upgrade path noted for multi-replica/durability). `agent.py` now accumulates each iteration's `usage.input/output_tokens` and surfaces the turn total in the **`done`** event; `main.py` `/api/chat` checks `over_budget(auth.user_id)` *before* the agent (refuse + `daily_token_budget_exceeded` + return — no spend) and `record(...)`s the turn after. Offline **16/16** (disabled-noop, accumulate-to-cap≥, per-user isolation, day-rollover, empty/zero/negative, exact-cap); `py_compile` clean; live files untouched (`cp`-then-edit). **Chat-MVP P5 is now essentially complete** — only the deliberately-deferred items remain (030 Alpaca routing — drafted/paused until multi-user; widget numeric validator — not built by decision). Both remaining items only matter once the chat app goes to multiple real users.

## 2026-06-11 (later) · 034 live-verified, 035 hero email, permalink→Vercel, account re-onboarded

**034 (token budget) APPLIED & LIVE-VERIFIED** — Nicholas applied it and the **daily data limit hit properly in live testing** (the `over_budget` pre-check refused the turn after the cap), so the pre-check + `record` loop works against real token counts. `test_034_token_budget.py` green.

**035 — show the signed-in email above "Portfolio value"** (`proposed_changes/035-hero-email/`). `frontend/app/page.tsx` `Hero` reads `useAuth().session?.user?.email` (useAuth already imported; the header avatar uses it too) and renders a muted, `truncate`d line above the "Portfolio value" label; omitted in demo mode (null email). `tsc --noEmit` clean via temp-apply→typecheck→restore (live untouched). Frontend-only, no dep.

**Brief permalink now points to the deployed frontend.** The localhost links were just the `PUBLIC_BASE_URL` default — the base is `published_briefs._public_base()` (`os.getenv("PUBLIC_BASE_URL","http://localhost:3000")`), used by `permalink_for()` → `{base}/b/{token}`. Local `backend/.env` is now `PUBLIC_BASE_URL=https://agentic-brokerage-mvp-front.vercel.app` (+ the Railway cron's), so a fresh `run_briefings.py --max-users 1 --force` produced `https://agentic-brokerage-mvp-front.vercel.app/b/<token>` ✓ (old WhatsApp messages keep their old link — baked at send time). **Re-confirmed the WhatsApp `queued`-but-undelivered = Sandbox 24h-window (63016)** lesson: re-`join` the sandbox to reopen the window; the durable fix is W6.4's approved template (code ready, gated on W6.4a).

**Account note:** the live IBKR connection is now user **`d898502e-…`** — Nicholas had accidentally deleted the Supabase test users (which **cascaded** the `ibkr_connections` row), then re-onboarded via `/connect`. So `e09942bf-…` (referenced in older entries / CONTEXT_TRANSFER) is retired; `d898502e-…` is current. Gotcha recorded: deleting a Supabase user drops its IBKR connection → re-connect to restore the daily brief.

**State:** both products deployed & hardened — waitlist fully operational in prod; chat-MVP P5 lockdown essentially complete. Remaining work is external/operator (W6.4a Meta/Twilio sender; W6.2b PostHog wipe) — tracked in `self_management/OPERATOR_CHECKLIST.md`. CONTEXT_TRANSFER.md banner updated to a **STATUS 2026-06-11** block.

---

## 2026-06-12 · Proposals 036–041 — connect link, proactive email (SendGrid→Resend), IBKR main-page portfolio (shared→per-user), docs reconciliation

A working session against a 5-task list (proactive email · TradingView-via-Vercel · Alpaca→IBKR main-page portfolio · back-to-main link · doc reconciliation). Task 2 (charts on Vercel) was only **assessed** (it collides with the deferred "containerised TradingView" v2 problem — either solve that or re-architect the TA path to browser-rendered **TradingView Lightweight Charts**; user OK'd a swap if needed, not yet built). Everything else became a `proposed_changes/` proposal; **Nicholas applied 036/038/039 mid-session; 040/041 await apply.**

**036 — "← Back to the app" link on `/connect` (APPLIED).** Completes the two-way loop with the existing `/`→`/connect` link; scoped to `/connect` only (the public `noindex` `/b/<token>` brief page deliberately gets no chat-app link). Same-tab `<a href="/">`, styled to the dark footer. `tsc` clean. *(I initially edited it directly, then — per Nicholas's "all changes via `proposed_changes` first" rule — reverted and re-routed it as the proposal.)*

**Repo hygiene — `frontend/tsconfig.tsbuildinfo` un-tracked.** It was committed AND in `.gitignore` (the classic gotcha: ignore rules don't apply to already-tracked files), so every `tsc` dirtied the tree. Fixed with `git rm --cached` (file kept on disk; ignore rule now effective). Also adopted a **temp-apply→typecheck/test→restore** verification pattern so checking a proposal never leaves the live tree dirty.

**037 → 038 — proactive portfolio email (P7-NOTIFY), provider pivoted SendGrid → Resend.** Email-the-WhatsApp-brief: a SECOND delivery channel for the same daily brief, **system-side (never an agent tool — threat 1)**, mirroring `whatsapp.py`. Built `email_delivery.py` (provider behind one seam), `email_unsubscribe.py` (stateless HMAC one-click token), `email_api.py` (`GET/POST /api/email/unsubscribe`), a `scheduler._maybe_email` leg (additive/best-effort/`channel="email"`), per-user `email_opt_in` (schema + connect UI + consent copy), and the `get_user_email_admin` lookup. **037 used SendGrid on the assumption the funded Twilio account would cover it — it won't (Twilio owns SendGrid but bills it separately), so 038 swapped to Resend** (permanent free tier 3k/mo). Because the provider was a single seam, the swap touched only `email_delivery.py` + env + docs; **038 supersedes 037 (apply one, not both).** No new Python dep (Resend over `httpx`). Offline 33/33. **APPLIED.** *(Operator Q&A captured into the 038 README go-live section: Resend needs a verified domain + DNS (SPF/DKIM) for real sends; `onboarding@resend.dev` is self-only test; `EMAIL_FROM` must match the verified domain — `adventai.io` is the domain referenced in the repo; `EMAIL_UNSUBSCRIBE_SECRET` is self-minted; `PUBLIC_BACKEND_URL` = the Railway backend.)*

**W6.4a interaction (recorded):** email needs **no** change when the WhatsApp Business template (W6.4/W6.4a) lands — the scheduler already splits `wa_brief` (WhatsApp view: text+permalink, or the template) from `brief_with_link` (email view: full prose + raw permalink), so the WhatsApp freeform→template switch can't truncate the email. Keep that split if ever refactoring.

**039 — main-page portfolio Alpaca → read-only IBKR (APPLIED), then 040 — per-user + nil (drafted).** `get_portfolio` (single source for the Hero `/api/portfolio` AND the agent `morning_brief`) repointed to `ibkr_flex` behind a reversible `PORTFOLIO_SOURCE` flag (default `ibkr`); base-ccy mapping (Hero is currency-driven → `HK$` with no frontend change); TTL cache (Flex is end-of-day); **trading disabled** (`TRADING_ENABLED=0` → `place_paper_order` returns `trading_unavailable`, with a `system.md` rule). 039 read one **shared/env** account. Nicholas then asked for **per-user** + **nil-until-connected** → **040**: `get_portfolio` resolves the authed `user_id` → their own `/connect` connection (new `connections.get_connection_with_token_admin`, service key, decrypted token); not-connected → `_nil_portfolio()` (Hero shows `—` + a "connect IBKR" hint, no fabricated demo number); cache now per-user-keyed; `system.md` "no brokerage connected" rule. 039 offline 20/20; 040 offline 16/16; frontend `tsc` clean. **No `main.py` change in either (frontend infers not-connected from null equity) → no conflict with 038.**

**041 — reconcile the 5 top-level docs (README/CLAUDE/METRICS/SCOPE/SECURITY) with SESSION_LOG (drafted, docs-only).** Kept the PAUSED chat-MVP content + made the LIVE waitlist product first-class. Highlights: README live URLs (no more "TBD") + waitlist run section + Node 22; CLAUDE **Anthropic-SDK-direct (not `claude-agent-sdk`)** + model `claude-opus-4-5` (was 4.7) + IBKR/Twilio/Resend stack rows + a waitlist-architecture section; SCOPE 2026-06-05 amendment banner; SECURITY Threat 1 *realized* (system-side sends) + Fernet token-at-rest + checklist status; METRICS waitlist funnel. Proposal write-up is `PROPOSAL.md` (avoids the top-level-README collision); apply = `cp` the 5 docs to root, **not** `PROPOSAL.md`.

**Process note:** all of the above are `proposed_changes/036–041` (STATUS.md updated). Email-leg live test is **pending** — a `run_briefings.py` attempt was skipped by the **W6.5 12h resend guard** (`already_sent_recently`); use `--force` (or `BRIEFING_DEDUP=0`) to exercise it.

**State:** unchanged at the product level (deployed waitlist + paused chat). New since 2026-06-11: a proactive **email** channel (Resend, applied), the main page now shows the user's **own read-only IBKR** portfolio (039 applied; per-user 040 drafted), a `/connect`→`/` link, and the top-level docs reconciled (041 drafted). Awaiting apply: **040** (per-user portfolio) + **041** (docs). Operator setup for email go-live: Resend domain/DNS + env (see 038 README).

---

## 2026-06-12 (later) · 042 + 043 — chat markdown rendering + HK technicals (yfinance)

**Trigger:** a screenshot of a 7-criteria TA reply for **1398.HK (ICBC)** surfaced two distinct bugs — (a) the chat bubble rendered **raw markdown** (`**bold**`, `###`, `| table |` shown literally); (b) the agent said "my technical analysis tools don't have coverage for Hong Kong-listed stocks." Diagnosed via two Explore agents (frontend rendering + the TA tool stack).

**042 — render markdown in the loose-chat bubble (DRAFTED).** Root cause: `frontend/app/page.tsx` rendered the `message` bubble as a raw React string (`{m}`) and the frontend had **no markdown library** at all (`SafeHtml`/DOMPurify only allows `strong`/`em` and is widget-only). Fix: new `frontend/components/Markdown.tsx` (**react-markdown + remark-gfm**, per-element Tailwind styling, tables wrapped in an `overflow-x` scroller for the narrow phone bubble); `page.tsx` renders `<Markdown>{m}</Markdown>`; `package.json` adds the 2 deps. **XSS-safe by construction** — no `rehype-raw`, so raw HTML in model output is treated as text, exactly the SECURITY threat-7 posture (no `dangerouslySetInnerHTML`). Verified `tsc` clean via temp-apply → `pnpm install` → typecheck → restore (live untouched). `proposed_changes/042-chat-markdown/`.

**043 — HK (+ any-ticker) technicals via yfinance-computed indicators (DRAFTED).** Root cause: ALL indicator math was delegated to **TradingView Desktop** (local-only; prod runs `USE_MOCK_TA=1`), and the mock covered only ~11 hardcoded **US** tickers → `1398.HK` → `no_coverage`. (`get_quote` worked because it uses yfinance, which *does* cover HK and can also fetch daily OHLCV — unused.) Fix: new `_yfinance_technical_levels` computes **SMA 10/20/50/200, EMA 20, RSI 14 (Wilder), MACD 12/26/9** + swing S/R + trend + `price_above_sma200` from yfinance daily candles — **pure pandas via the returned DataFrame, no new dependency**, sync fetch in `asyncio.to_thread` — in the ticker's **base currency** (`HK$`). `get_technical_levels` re-prioritised: **mock → TradingView (only when `TRADINGVIEW_MCP_ARGS` is set) → yfinance**, and crucially a **non-covered ticker in mock mode falls through to yfinance — so HK works even under today's prod `USE_MOCK_TA=1`, no flag change.** Also: `system.md` rule (emit a `ta_chart`, NOT a markdown table; TA covers HK+US; copy `currency`/`indicator_values`; never say "no HK coverage"); `widget_contract.md` ta_chart gains `currency`; `frontend/.../TAChart.tsx` renders `data.currency` (was a hardcoded `$`). **Two verification-surfaced fixes:** RSI must be 100 on all-gains windows (don't NaN-out a zero `avg_loss` — let `inf` → 100 fall out naturally), and a too-strict MACD-histogram test assertion on a perfectly linear synthetic ramp (hist settles to ≈0). Offline **18/18**, `py_compile` ✓, frontend `tsc` clean. `proposed_changes/043-hk-technicals-yfinance/`.

**Reinforcing pair:** the ugly markdown table appeared *because* the agent couldn't build a `ta_chart` for HK (043's gap) and fell back to a table (which `system.md` calls a bug). 043 makes that case a real widget; 042 makes genuine loose-chat replies format properly. Apply both. Approach choices (both user-confirmed): react-markdown+remark-gfm over a hand-rolled renderer; yfinance-computed indicators as the **default** real source over a HK-only branch.

**Method note (carried from earlier this session):** dep-adding frontend proposals are verified by temp-apply → `pnpm install` → `tsc` → restore (package.json + lockfile + files all restored, live left clean); package-relative backend tools (`tools/technicals.py`) are tested by temp-applying then running the offline test, then restoring.

**State:** unchanged at the product level. Both 042 + 043 **drafted/awaiting apply** (STATUS.md rows added; queue now 040, 041, 042, 043 awaiting apply — 036/038/039 already applied). 043 optional prod tweak: set `USE_MOCK_TA=0` on Railway to make *US* technicals real too (HK already works via the mock fall-through).

---

## 2026-06-12 (later ×2) · 044 — in-app TradingView charts (Lightweight Charts); the whole 036–044 batch APPLIED

**Task 2 done — "TradingView charts via Vercel, no local TV" → proposal 044 (planned in plan mode, then built).** Decision (user-confirmed over an iframe embed): render charts **client-side with TradingView's open-source `lightweight-charts`**, fed by OUR yfinance OHLCV (reusing 043) — real candlesticks in the deployed app, no TradingView Desktop, no API key, base ccy (HK$), HK + US, numbers stay sourced.

- **Out-of-band data (the key call):** a ~250-bar OHLCV array must NOT travel through the LLM, so instead of stuffing the widget JSON, the chart component fetches a dedicated **`GET /api/chart-data`** (new `backend/chart_api.py`; TTL cache; `candles:[]` on no-data; public, guarded by the W6.6 per-IP limit). Zero `agent.py`/widget-contract change. Refactored 043's yfinance pull into a shared **`_fetch_ohlcv`** (technicals.py) so the chart + the indicator values read the same bars — **behaviour-preserving (043 test still 18/18).**
- **Frontend:** `lightweight-charts ^5` dep; `/api/chart-data` rewrite (**no CSP change** — `script-src 'self'` covers the bundled lib, `connect-src 'self'` the same-origin fetch); `lib/chart.ts`; `TAChart.tsx` rewritten (`'use client'` `ChartSlot` — candlesticks + SMA 50/200 overlays computed client-side + dashed S/R price lines + `ResizeObserver` + `chart.remove()` cleanup so a pinned chart survives; render priority **live chart → screenshot (local-TV) → `MockChartSvg`**).
- **Verification:** backend **8/8** (`test_044_chart_data.py`) + 043 regression **18/18**; `py_compile` ✓; frontend `tsc` clean (temp-apply → `pnpm install` → restore). Interactive chart-verbs (`chart_apply_indicator`/draw/scroll) + pattern recognition kept out of scope (a later follow-on).

**Applications (this session's whole batch is now live).** Nicholas applied **041 (docs reconciliation)** + **044**. So **036/038/039/040/041/042/043/044 are ALL APPLIED** (037 superseded by 038); the `proposed_changes/` queue is empty. Net effect on the **chat MVP**: the main page now shows the signed-in user's **own read-only IBKR** portfolio (per-user, nil-until-connected, trading disabled), with a `/connect`↔`/` link; chat replies render as **markdown**; technical analysis covers **HK + US** (yfinance SMA/RSI/MACD) and draws a **live in-app candle chart** (no local TV). The pivot also gained a proactive **email** channel (Resend). And the top-level docs (README/CLAUDE/METRICS/SCOPE/SECURITY) are reconciled with this log (041).

**Operator follow-ups (unchanged + new):** email go-live needs a Resend-verified domain/DNS + env (`adventai.io`; see `proposed_changes/038-email-briefing-resend/`); 043 optional `USE_MOCK_TA=0` on Railway to make US technicals real; the standing W6.4a / W6.2b items in `OPERATOR_CHECKLIST.md`.

**State:** chat MVP materially upgraded (per-user IBKR portfolio, markdown, HK technicals, in-app charts); pivot gained email; docs current. Everything code-complete + applied; remaining work is external/operator only.

---

## 2026-06-16 · 045 (real chat news) + 046 (conversation memory)

A chat-MVP session against a 3-task list — real news (was mocked) · conversation memory · Alpaca→IBKR execution swap. Tasks 1 & 2 became `proposed_changes/` proposals (offline-tested, self-contained); task 3 (IBKR execution) was assessed, not built.

**045 — real news + macro in chat (APPLIED & live-verified).** `get_company_news` and `get_macro_snapshot` (`backend/tools/market.py`) were **hardcoded mock** — the latter even fabricated `fed_events_today`/`earnings_today` (the same invented "FOMC 14:00" the W2 briefing fix #1 removed system-side). 045 routes both to the already-live, W2-verified **`backend/news_context.py`** yfinance engine, **mock-first** (`_use_mock_news()`: `USE_MOCK_NEWS=1`/`USE_MOCK_MARKET=1`/no-yfinance → mock; deterministic demo byte-identical). `get_company_news`→`fetch_recent_news` (same `news_by_ticker` shape + `url`, honours `since`, covers US **and** HK); `get_macro_snapshot`→`fetch_macro_context` (rich `indicators` list + legacy flat fields mapped from `ES=F/NQ=F/^TNX/^VIX/CL=F/GC=F`; **no fabricated** DXY/BTC/Dow/Fed-calendar/earnings on the real path — honest `macro_unavailable` over blanks). One file + test; `news_context.py` untouched (stays system-side, not an agent tool) so the briefing + `test_P1_003` are unaffected. Offline **36/36**. Nicholas applied it; confirmed real yfinance news flowing in chat, reasonably accurate from source.

**046 — conversation memory (DRAFTED → applied + pushed).** Task 2 — "current chat forgets prior message contexts." `run_agent` built `messages` fresh each call, so follow-ups ("what about *its* risks?") lost the antecedent — even though turns are persisted (P4.2) and the frontend already threads `conversation_id`. 046 closes the loop **server-side**: new pure **`db.to_agent_history(rows)`** converts persisted rows → an Anthropic-ready, **alternation-safe**, bounded `[{role,content}]` (merges consecutive same-role; drops dangling leading-assistant/trailing-user so a prior errored turn can't 400 the API; widget-only turns → `[Assistant showed a <type> widget]` placeholder, **not** the numeric JSON — trust #1; caps `CHAT_HISTORY_MAX_MESSAGES`=20 / `CHAT_HISTORY_MAX_CHARS`=12000, ≤0 disables). `main.py` loads the thread (RLS-scoped, **before** writing the current msg) → `history=` to `run_agent`; `agent.py` seeds `messages = history + [current user]` (`history=None` default = pre-046). **Distinct from P4.3 Mem0** (cross-conversation *facts* vs in-thread *history*; they compose). Server-authoritative — no client-trusted input, no API/schema/frontend change. **Scope limit:** persisted/signed-in convos only; demo mode stays historyless. 3 files (`db.py`/`main.py`/`agent.py`) + test; offline **17/17** (`to_agent_history` edge cases + `run_agent` seeding with a stubbed Anthropic client). `proposed_changes/046-chat-conversation-memory/`.

**Test-harness lesson (carried from 045 → 046).** Self-contained proposal tests must locate the repo root by walking up for a marker **not present in the proposal's own mirror tree** — 045's first cut anchored on `backend/tools/market.py`, which the mirror also contains, so running the test from `scripts/` resolved the wrong root (`FileNotFoundError` on a path one level above the repo). Fixed to anchor on a file the mirror doesn't carry (`backend/news_context.py` for 045, `backend/auth.py` for 046) and to skip the temp-apply when the proposal copy is absent. Both tests restore the live files in a `finally` and leave them git-untouched.

**State:** unchanged at the product level. New since 2026-06-12: chat now serves **real yfinance news/macro** (045, applied) and **remembers earlier turns in a conversation** (046, applied + deployed). Task 3 (Alpaca→IBKR execution swap) remains deferred — still trading-disabled. `proposed_changes/` queue: 046 applied; no drafts pending.

---

## 2026-06-17 · 047 (chat loading ring + multi-line input) + 048 (NER name→ticker prompt)

A 3-task chat-UX/quality list — loading ring · input text-wrap · improve NER for news retrieval. Planned in plan mode, then built as two proposals (frontend bundled, backend separate); **Nicholas applied both.**

**047 — loading ring + multi-line input (APPLIED).** Two frontend-only UX fixes, bundled (one Vercel deploy, one `tsc`/`build` recipe). (1) **Loading ring:** `frontend/app/page.tsx` rendered nothing between the user bubble and the first SSE event, so the UI looked frozen during the multi-second gap. Added a "Working" spinner card shown ONLY in the pristine gap (`thoughts/widgets/messages` all empty, `!error`, `!done`), reusing `ThinkingCard`'s exact card chrome + `animate-spin-slow` accent ring so the swap to the real ThinkingCard is seamless. `!t.done` is load-bearing (an empty-finish turn would otherwise spin forever); mutually exclusive with every content branch (no double-card/flicker); handles the message-only turn. Pure derived render — no new state/effect. (2) **Input text-wrap:** `frontend/components/ChatBar.tsx` single-line `<input>` (`h-12`) → auto-growing `<textarea>`. **Enter sends / Shift+Enter newline** (user-confirmed) + an IME `isComposing` guard; `autoGrow()` reset-then-measure (so it shrinks too) capped ~5 lines then scrolls; **`submit()` manually resets the height** because `setValue('')` doesn't fire `onChange` (the easy-to-miss bug); `h-12`→`py-3` (single line still ≈48px, matches the mic button), `rounded-3xl`→`rounded-2xl`, `resize-none`; the pulse-dot + animated-placeholder overlays moved off `top-1/2 -translate-y-1/2` to first-line `top-[18px]`/`top-[14px]` so they don't float when the box grows. Verified on **Node 22** (pnpm needs ≥22.13) via temp-apply→restore: `pnpm exec tsc --noEmit` exit 0 (validates the `HTMLInputElement`→`HTMLTextAreaElement` ref + `isComposing`) + `pnpm build` exit 0 (5 static pages). `proposed_changes/047-chat-input-and-loading-ring/`.

**048 — NER (name→ticker) resolution prompt (APPLIED).** The news/data tools (`get_company_news`/`get_quote`/research) take yfinance-valid symbols and do **no** entity resolution, but `system.md` had no rule for mapping company/sector/nickname → tickers, so "Mag 7"/"chipmakers"/"Tencent" (dual-listing) resolved inconsistently. New `system.md` section *"Resolving names to tickers (before any data tool)"* — recognise refs · expand aggregates within the 10-ticker cap · disambiguate dual listings (prefer the **held** symbol via `get_portfolio`, else local-primary) · prefer the user's own holding symbol · **fallback to the model's own company→ticker knowledge rather than refusing** (user's refinement: don't let the rule block — resort to default LLM NER when it doesn't cover a name) · a closing **no-fabrication trust caveat** (the load-bearing line — keeps resolution from being read as license to invent). Scoped to **all** data tools, not just news. Plus a one-sentence sharpen of the `get_company_news` tool **description** (schema unchanged). Prompt-only; existing catalysts-grounding rule untouched. Offline **12/12** (`test_048_news_ner.py`: ToolDef intact after the description edit — `tickers` array/maxItems 10/required, callable wired — + system.md section/fallback/trust-caveat present + existing rule preserved). `proposed_changes/048-news-ner-ticker-resolution/`.

**Packaging note (carried):** bundled the two frontend tweaks (047) but kept the backend prompt change separate (048) — same-tree/same-verification-recipe bundles, cross-tree concerns split (mirrors why 045 stayed one file and 046 was its own backend proposal). Verification left the live tree clean each time (frontend temp-apply→`tsc`/`build`→restore, incl. reverting the `pnpm`-touched `pnpm-lock.yaml`; backend temp-apply→registry-assert→restore).

**State:** unchanged at the product level. New since 045/046: the chat input **wraps onto multiple lines** and shows a **loading ring** while waiting (047), and the agent has explicit **name→ticker resolution** guidance for news/data tools (048). Both applied. Task 3 (Alpaca→IBKR execution swap) still deferred — trading remains disabled. `proposed_changes/` queue: empty (045–048 all applied).
