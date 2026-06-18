# CLAUDE.md — Agentic Brokerage MVP

**Read this first at the start of every session.** This file is the source of truth for architectural decisions. Don't re-derive these choices in conversation — they're settled until SCOPE.md says otherwise.

> ## ⚠️ ACTIVE PIVOT (2026-06-05) — read before assuming the chat-app architecture below is current
>
> The product described in this file (agent-first chat brokerage) is **PAUSED**. The active track is a pre-launch **IBKR + WhatsApp waitlist briefing** product: connect Interactive Brokers via a one-time **Flex token** → daily **WhatsApp** narrative macro briefing (Claude generates, Twilio delivers). Reuses this repo's FastAPI backend + Supabase + the `morning_brief` generation; does **not** use the phone-chat UI, TradingView MCP, or (for the briefing) Alpaca. **Alpaca stays** for now; **IBKR Flex is added for holdings reads** (execution swap Alpaca→IBKR = a separate later step). New stack pieces: **IBKR Flex Web Service** (read-only XML holdings/NAV) + **Twilio WhatsApp** and **Resend email** (both *system-side scheduled sends* — *never* agent tools, per trust principle / threat 1) + per-user **Fernet** token encryption. The IBKR read also became the **main-page portfolio** source (per-user, nil-until-connected — proposals 039/040). **Status (2026-06-11): the full `land → connect IBKR → daily WhatsApp/email brief` loop is BUILT, DEPLOYED & LIVE** (Railway web + briefing cron, Vercel, Supabase). Full rationale, reuse-map, caveats, and staged plan: **`.self_management/DECISION_pivot_waitlist.md`**; active checklist + operator remainder: top of `.self_management/PRIORITIES.md` and `.self_management/OPERATOR_CHECKLIST.md`. The architecture below remains the source of truth for the *paused* chat MVP.

## What this product is

An agent-first mobile brokerage. The entire UI collapses into one persistent chat bar. Every action is a prompt — research, trade execution, charting, alerts, portfolio risk — surfaced as generative widgets that the user can pin to a personalised dashboard.

**Lead value prop:** *Your personal analyst, at your command — a team of Wall Street–grade analysts, serving you at every step of the journey.*

The validated experience lives at `~/Downloads/agentic_brokerage_demo.html` (mockup) and `~/Downloads/agentic-brokerage-session-recap-2026-05-19.md` (design rationale). This MVP builds the working version with real LLM + real broker + real market data.

## Audience for this MVP

5–10 active retail traders. Shareable URL. Goal is PMF evidence — D1/D7/D30 retention, activation funnel — not polish or scale. See METRICS.md for benchmarks.

## Architecture at a glance

```
Frontend (Next.js on Vercel)
  ↕ HTTPS + SSE
Backend (Python FastAPI + Anthropic SDK [direct] on Railway)
  ↕ tool calls
  IBKR Flex (read-only holdings) · Alpaca Paper API · yfinance · FMP research ·
  Anthropic web search · TradingView MCP · Supabase Postgres
```

**Backend orchestrates the agent loop and exposes one SSE chat endpoint.** Frontend is a thin renderer that streams thinking breadcrumbs and widget JSON from the backend. No business logic in the frontend.

> **Correction (locked):** the agent loop uses the **Anthropic SDK directly**, *not* `claude-agent-sdk` (rejected — see SESSION_LOG 2026-05-20 / proposal 002). MCP servers (TradingView) are hosted as **backend-side stdio clients** (`backend/mcp_client.py`), not via the SDK. Earlier mentions of `claude-agent-sdk` below are historical.

## Waitlist product architecture (the LIVE track)

The active product reuses the same FastAPI backend + Supabase, but the daily brief is a **system job**, not the agent loop — a deliberate trust boundary (threat 1: the agent has no outbound-comms or credential tools).

```
Frontend /connect (Vercel)                Briefing CRON (Railway, `python -m scheduler`)
  land → waitlist → magic-link sign-in       per opted-in user:
  → connect IBKR (Flex token, encrypted)       ibkr_flex (read holdings, W1)
                                               → briefing (Claude narrative, W2 — tool-less)
Backend web (Railway, FastAPI)               → whatsapp (Twilio) + email (Resend)  [system send]
  /api/ibkr/connect · /api/waitlist            → publish permalink (/b/<token>) + log delivery
  /api/twilio/inbound|status (opt-out)       NO public trigger endpoint (threat 1)
  /api/email/unsubscribe (one-click)
  /api/brief/{token} (public permalink)
  /api/chat + /api/portfolio (read-only IBKR, per-user)
```

- **Connect flow (W4):** `backend/waitlist_api.py` + `backend/connections.py` store the per-user **Fernet-encrypted** Flex token in `ibkr_connections` (RLS). The cron reads it back via the **service key** (the one legit admin path).
- **Brief generation (W2):** `backend/briefing.py` — a single tool-less `messages.create`; numbers computed in Python, copied verbatim (P&L in the account **base ccy**, e.g. HKD). Real market context via `backend/news_context.py` (yfinance headlines + index-futures/VIX/10Y).
- **Delivery (W3/038):** `backend/whatsapp.py` (Twilio) + `backend/email_delivery.py` (Resend) — both system-side, optional-dep/`httpx`, mock-first, never agent tools.
- **Scheduler (W5):** `backend/scheduler.py` + `scripts/run_briefings.py` — per-user isolation, retries, cost cap, W6.5 daily-send idempotency.
- **Main-page portfolio (039/040):** `get_portfolio` reads the signed-in user's **own** IBKR connection (read-only, base ccy); **nil until they connect**; trading disabled.
- **Deploy (P6):** Railway = backend web (`/healthz`) + briefing cron (UTC `cronSchedule`, no public trigger); Vercel = frontend; Supabase = DB. Live URLs in `README.md` / `OPERATOR_CHECKLIST.md`. Full detail: `.self_management/DECISION_pivot_waitlist.md`, `docs/DEPLOY.md`.

## Trust principles (these are the moat)

These are non-negotiable. They're what distinguishes us from a chat-with-PDF wrapper. They're also expensive to retrofit — bake them in from day 0.

1. **No number without a source.** Every numeric claim in any widget has a citation chip pointing to the tool call that produced it. Implemented via a `sources: [{name, url?}]` field on every widget schema and a system-prompt rule that all numeric values must come from tool outputs.

2. **No black box.** Chain-of-thought renders as tool-action breadcrumbs ("Reading 10-Q → Computing margins → Comparing to consensus"), not raw reasoning tokens. SSE stream emits a `thought` event per tool call.

3. **No hallucinated data.** Strict tool/output contract: the LLM never produces numeric values that aren't passed through from a tool result. Enforced in the system prompt and reinforced by a validator that scans widget JSON for numeric fields and confirms they trace back to a tool call ID. Validator fails closed.

4. **No fight with power users.** TradingView MCP is in scope from day 1. Power users get voice-controlled charts, not a dumbed-down UI.

## Tech stack

| Layer | Choice | Why |
| --- | --- | --- |
| LLM | Claude Opus 4.x — backend default **`claude-opus-4-5`** (`ANTHROPIC_MODEL`; originally specced 4.7) | Best at structured, cited finance synthesis |
| Agent runtime | **Anthropic SDK directly** (Python) — *not* `claude-agent-sdk` (rejected) | Simpler SSE streaming control; MCP via backend-side stdio clients |
| Backend | FastAPI + uvicorn | SSE-first, fast cold start |
| Frontend | Next.js 15 on Vercel | Free hosting, magic link auth, SSE-friendly |
| Auth + DB | Supabase | Magic link + Postgres + RLS in one |
| Broker (execution) | Alpaca paper trading | Real broker API, $100k paper, no KYC. **Trading is currently disabled** (039) — read-only portfolio; the Alpaca→IBKR execution swap is a deferred step |
| Holdings (read) | **IBKR Flex Web Service** (`backend/ibkr_flex.py`, W1) | Read-only XML holdings/NAV. Powers the WhatsApp/email brief AND the main-page portfolio (per-user, 039/040). Mock-first (`USE_MOCK_IBKR`); creds = per-user encrypted Flex token (W4) |
| Research data | **FMP** (Financial Modeling Prep, `backend/fmp_client.py`, P2b/006) | Analyst consensus + targets + fundamentals for the `research_card`; mock-first, free tier = ~87 sample symbols |
| WhatsApp delivery | **Twilio** (`backend/whatsapp.py`, W3) | System-side scheduled send of the daily brief — **never an agent tool** (threat 1). Sandbox now; Business sender + approved template = W6.4/W6.4a |
| Email delivery | **Resend** (`backend/email_delivery.py`, 038) | System-side email of the same brief over `httpx` (no new dep). Per-user opt-in + one-click unsubscribe. *(Twilio funding does not subsidise SendGrid → Resend's free tier chosen)* |
| Token-at-rest | **Fernet** (`backend/token_crypto.py`, W4, via existing `cryptography`) | App-level encryption of each user's IBKR Flex token before storage |
| Market data | yfinance | 15-min delayed, free, no rate limits in practice |
| News | Anthropic web search | Already in Claude API, no separate provider |
| Charts | TradingView MCP (`tradesdontlie/tradingview-mcp`) | The talk-to-charts wedge. Real chart in local dev; mock in production until containerised TV Desktop (v2). **Live charts need BOTH `USE_MOCK_TA=0` AND TV Desktop open on the debug port** (`open -a "TradingView" --args --remote-debugging-port=9222`); if TV Desktop is closed the card silently degrades to mock data, labelled `(mocked — live data unavailable)` since proposal 029. See README "Talk-to-your-charts". |
| Indicators math | ta-lib (Python) | For portfolio risk / correlation, not chart rendering |
| Memory | Mem0 hosted platform (`mem0ai>=2.0.0,<3.0.0`, `AsyncMemoryClient`) | Per-user fact recall across conversations (P4.3, proposals 025+026). Optional `memory` dep group; no-op when `MEM0_API_KEY` unset. Scoped by the authenticated `user_id` — `search(filters={"user_id":…}, top_k=…)`, `add(messages, user_id=…)`. |
| Observability | Langfuse (`langfuse>=4.7.0,<5.0.0`, OTel) | Per-turn agent traces — one `chat` span with `generation`/`tool:*` children, tagged `user.id` (P4.4, proposals 017+018). No-op when unconfigured. |
| Analytics | PostHog Cloud (EU region) | Free tier covers the MVP. Wired for the chat funnel (027) AND the waitlist funnel (W6.2); PII-scrubbed (W6.2b). Use the public `phc_` project key |

## Widget JSON contract (the schema constrain)

Every agent response that includes a widget conforms to one of N strict schemas defined in `backend/prompts/widget_contract.md`. The LLM is told via system prompt: "Your final response is a JSON object matching one of the schemas below. Numeric values come from tool results — do not invent."

Schemas planned for MVP:

- `morning_brief` — portfolio P&L, market context, 1–2 names to watch
- `research_card` — rating, target, thesis, catalysts, risks
- `ta_chart` — TradingView screenshot URL, indicator values, S/R levels
- `order_ticket` — proposed buy/sell with sizing + TP/SL
- `live_trade` — filled position with real Alpaca P&L
- `thesis` — TLDR, why-I'm-in, what-to-watch, breakers
- `tracker` — trade + thesis combo card
- `portfolio_risk` — concentration score, sector bars, flags, suggested hedges

If Claude wants to produce something that doesn't match a schema, it should respond as plain markdown — but the chat surface treats markdown as "loose chat" and won't allow pinning.

## SSE event types (the streaming protocol)

The backend `/api/chat` endpoint streams these events:

```
event: thought         data: { "text": "Reading 10-Q..." }
event: tool_call       data: { "name": "get_quote", "args": {"ticker":"NVDA"}, "id": "tc_1" }
event: tool_result     data: { "id": "tc_1", "ok": true, "summary": "NVDA $942.50" }
event: widget          data: { "type": "research_card", "data": { ... } }
event: message         data: { "text": "Plain markdown response" }
event: error           data: { "message": "..." }
event: done            data: { "elapsed_ms": 8421 }
```

Frontend renders `thought` events as the thinking breadcrumbs, `widget` events as a generative card, `message` as a plain chat bubble. `tool_call` / `tool_result` are diagnostic — visible in dev mode only.

## Repo layout

```
agentic-brokerage-mvp/
├── CLAUDE.md, SCOPE.md, METRICS.md, SECURITY.md, README.md
├── backend/
│   ├── main.py                 FastAPI app + SSE endpoint
│   ├── agent.py                Claude agent loop
│   ├── tools/                  Tool implementations
│   ├── prompts/                System prompts + widget schemas
│   ├── db/schema.sql           Postgres schema
│   └── pyproject.toml
├── frontend/
│   ├── pages/                  Next.js pages
│   ├── components/
│   │   ├── widgets/            One file per widget schema
│   │   ├── ChatBar.tsx, ThinkingCard.tsx, PinAnimation.tsx
│   └── lib/sse.ts              SSE consumer
└── docs/SESSION_LOG.md         5 mins per Claude session of decisions
```

## What gets written down at end of every session

Append to `docs/SESSION_LOG.md`:

- What was built
- What decisions surfaced (e.g. "chose yfinance over Polygon because rate limits")
- What assumptions were introduced (e.g. "assumed Alpaca OCO bracket supports stop-limit")

Cheap insurance against architectural drift.

## When to deviate from this doc

Never silently. If the right move is to deviate, update this file first, then build. The point is that Claude Code sessions don't drift — if the architecture changes, it changes here in writing, not in someone's head.

## Related files

- `SCOPE.md` — what's in/out and the amendment rule (now incl. the 2026-06-05 pivot amendment)
- `METRICS.md` — PMF benchmarks (chat) + the waitlist funnel
- `SECURITY.md` — threat model + lockdown checklist before any tester
- **Pivot / live product:** `.self_management/DECISION_pivot_waitlist.md` (rationale + reuse-map), `.self_management/PRIORITIES.md` (W1–W6 checklist), `.self_management/OPERATOR_CHECKLIST.md` (non-code remainder), `docs/DEPLOY.md` (deploy runbook)
- **Day-to-day reference:** `.self_management/CONTEXT_TRANSFER.md` (cold-start brief), `docs/SESSION_LOG.md` (chronological decisions), `API_CONTRACT.md` (HTTP boundary), `proposed_changes/STATUS.md` (proposal index)

> Note: the user is **Nicholas** (earlier sessions said "Tom" — same person, corrected 2026-06-01). Stale `~/Downloads/*` and `~/.claude/projects/-Users-tom-*` paths from the original spec are historical; the canonical reference docs are the `.self_management/` set above.
