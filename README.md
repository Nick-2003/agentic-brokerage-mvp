# Agentic Brokerage MVP

A janky prototype of a voice/prompt-first mobile brokerage where every action — research, charting, trading, alerts, risk audits — is a prompt that generates a personalised widget you can pin to your dashboard.

**Status:** Phase 0 / 10 (architecture docs done; backend skeleton next).

**Start every dev session by reading:** [CLAUDE.md](./CLAUDE.md) · [SCOPE.md](./SCOPE.md) · [METRICS.md](./METRICS.md) · [SECURITY.md](./SECURITY.md).

## Local setup

### Prereqs

- Python 3.13+
- Node 20+
- `uv` (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- `pnpm` (`npm i -g pnpm`)
- Postgres (via Supabase local CLI or Docker)
- TradingView Desktop (for the talk-to-charts wedge — see Phase 4 docs)

### Required API accounts (free)

| Service | Why | How to get the key |
|---|---|---|
| Anthropic | Claude API | console.anthropic.com → API Keys → new key |
| Alpaca | Paper trading | alpaca.markets → sign up → Paper Trading dashboard → API Keys |
| Supabase | Auth + Postgres + magic links | supabase.com → new project → free tier → copy URL + anon key + service key |
| PostHog | Analytics | posthog.com → existing PWA Prod project (project_id: 148926) or new project |
| TradingView | Charts | tradingview.com → free account is fine for MVP |

### `.env` template (backend/.env)

```
ANTHROPIC_API_KEY=sk-ant-...
ALPACA_API_KEY=PK...
ALPACA_API_SECRET=...
ALPACA_BASE_URL=https://paper-api.alpaca.markets
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_SERVICE_KEY=eyJ...
POSTHOG_API_KEY=phc_...
POSTHOG_HOST=https://us.i.posthog.com
TRADINGVIEW_MCP_COMMAND=npx          # placeholder — TBD in Phase 4
TRADINGVIEW_MCP_ARGS=...             # placeholder — TBD in Phase 4
```

### `.env.local` template (frontend/.env.local)

```
NEXT_PUBLIC_SUPABASE_URL=https://xxx.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=eyJ...
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_POSTHOG_API_KEY=phc_...
NEXT_PUBLIC_POSTHOG_HOST=https://us.i.posthog.com
```

### Run backend

```bash
cd backend
uv sync
uv run uvicorn main:app --reload --port 8000
```

### Run frontend

```bash
cd frontend
pnpm install
pnpm dev   # http://localhost:3000
```

### Run TradingView MCP (Phase 4+)

```bash
# Open TradingView Desktop first, sign in
# Then start the MCP server (command TBD — Phase 4 will document)
```

## Project layout

See [CLAUDE.md § Repo layout](./CLAUDE.md#repo-layout).

## How to contribute (to yourself)

End every coding session by appending to [docs/SESSION_LOG.md](./docs/SESSION_LOG.md):
- What was built
- What decisions surfaced
- What assumptions you introduced

Don't skip. Five minutes per session. This is the practice that prevents architectural drift across Claude Code sessions.

## Deployment

| Component | Where | How |
|---|---|---|
| Frontend | Vercel | `vercel --prod` from `frontend/` |
| Backend | Railway (or Fly.io) | Connect repo, set env vars, deploy on push |
| Postgres | Supabase Cloud | Already hosted |
| Object storage (chart screenshots) | Supabase Storage | `screenshots` bucket, public read |

Live URLs (TBD after Phase 9): `agentic-brokerage.vercel.app` (frontend), `agentic-brokerage-api.up.railway.app` (backend).

## Pre-launch checklist

See [SECURITY.md § Pre-launch lockdown checklist](./SECURITY.md#pre-launch-lockdown-checklist). Every item must pass before sharing the URL with a tester.

## Useful references

- Plan (this build): `/Users/tom/.claude/plans/nifty-moseying-aho.md`
- Demo source (deterministic mockup, visual reference): `/Users/tom/Downloads/agentic_brokerage_demo.html`
- Design rationale: `/Users/tom/Downloads/agentic-brokerage-session-recap-2026-05-19.md`
- Product memory: `~/.claude/projects/-Users-tom-Desktop-workspace/memory/project_agentic_brokerage.md`
