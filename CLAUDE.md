# CLAUDE.md — Agentic Brokerage MVP

**Read this first at the start of every session.** This file is the source of truth for architectural decisions. Don't re-derive these choices in conversation — they're settled until SCOPE.md says otherwise.

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
Backend (Python FastAPI + claude-agent-sdk on Railway)
  ↕ tool calls
  Alpaca Paper API · yfinance · Anthropic web search · TradingView MCP · Supabase Postgres
```

**Backend orchestrates the agent loop and exposes one SSE chat endpoint.** Frontend is a thin renderer that streams thinking breadcrumbs and widget JSON from the backend. No business logic in the frontend.

## Trust principles (these are the moat)

These are non-negotiable. They're what distinguishes us from a chat-with-PDF wrapper. They're also expensive to retrofit — bake them in from day 0.

1. **No number without a source.** Every numeric claim in any widget has a citation chip pointing to the tool call that produced it. Implemented via a `sources: [{name, url?}]` field on every widget schema and a system-prompt rule that all numeric values must come from tool outputs.

2. **No black box.** Chain-of-thought renders as tool-action breadcrumbs ("Reading 10-Q → Computing margins → Comparing to consensus"), not raw reasoning tokens. SSE stream emits a `thought` event per tool call.

3. **No hallucinated data.** Strict tool/output contract: the LLM never produces numeric values that aren't passed through from a tool result. Enforced in the system prompt and reinforced by a validator that scans widget JSON for numeric fields and confirms they trace back to a tool call ID. Validator fails closed.

4. **No fight with power users.** TradingView MCP is in scope from day 1. Power users get voice-controlled charts, not a dumbed-down UI.

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| LLM | Claude Opus 4.7 (`claude-opus-4-7`) | Best at structured, cited finance synthesis |
| Agent runtime | `claude-agent-sdk` (Python) | First-party tool/MCP loop, streaming primitives |
| Backend | FastAPI + uvicorn | SSE-first, fast cold start |
| Frontend | Next.js 15 on Vercel | Free hosting, magic link auth, SSE-friendly |
| Auth + DB | Supabase | Magic link + Postgres + RLS in one |
| Broker | Alpaca paper trading | Real broker API, $100k paper, no KYC, no real money |
| Market data | yfinance | 15-min delayed, free, no rate limits in practice |
| News | Anthropic web search | Already in Claude API, no separate provider |
| Charts | TradingView MCP (`tradesdontlie/tradingview-mcp`) | The talk-to-charts wedge (Real chart in local dev; mock in production until containerised TV Desktop (v2)) |
| Indicators math | ta-lib (Python) | For portfolio risk / correlation, not chart rendering |
| Analytics | PostHog Cloud | Already in Tom's stack, free tier covers MVP |

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

- `SCOPE.md` — what's in/out and the amendment rule
- `METRICS.md` — PMF benchmarks before launch
- `SECURITY.md` — threat model + lockdown checklist before any tester
- `~/Downloads/agentic-brokerage-session-recap-2026-05-19.md` — full design rationale
- `~/.claude/projects/-Users-tom-Desktop-workspace/memory/project_agentic_brokerage.md` — product memory
