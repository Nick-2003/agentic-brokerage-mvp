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
- TradingView Desktop (for the talk-to-charts wedge — see "Talk-to-your-charts" below)

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

# TradingView MCP — see "Talk-to-your-charts" section below
USE_MOCK_TA=1
TRADINGVIEW_MCP_COMMAND=node
TRADINGVIEW_MCP_ARGS=/absolute/path/to/tradingview-mcp/src/server.js
TRADINGVIEW_MCP_CDP_PORT=9222
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

## Talk-to-your-charts (TradingView MCP)

P1.2's wedge: the user says "add RSI to NVDA" or "draw support at 220" and the chart actually changes. Requires three things running locally on the **same machine**:

1. **TradingView Desktop** — download from [tradingview.com/desktop](https://www.tradingview.com/desktop/). Free account is fine. Sign in.
2. **TV Desktop launched with CDP enabled** — `--remote-debugging-port=9222`. On macOS:

   ```bash
   open -a "TradingView" --args --remote-debugging-port=9222
   ```

   (Alternative: let the MCP server auto-spawn via its `tv_launch` tool — set `USE_MOCK_TA=0` and just call any chart tool; the server will try to start TV Desktop itself.)
3. **The MCP server** — clone the [tradesdontlie/tradingview-mcp](https://github.com/tradesdontlie/tradingview-mcp) repo as a **sibling** of this one (not inside it):

   ```bash
   cd ..   # one level above agentic-brokerage-mvp/
   git clone https://github.com/tradesdontlie/tradingview-mcp.git
   cd tradingview-mcp
   npm install   # Node 22+
   ```

4. **Set env vars** in `backend/.env`:

   ```env
   USE_MOCK_TA=0
   TRADINGVIEW_MCP_COMMAND=node
   TRADINGVIEW_MCP_ARGS=/absolute/path/to/tradingview-mcp/src/server.js
   TRADINGVIEW_MCP_CDP_PORT=9222
   ```

5. **Restart the backend.** First chart-related prompt will spawn the MCP server as a subprocess and connect.

### Production posture (read this before deploying)

**TradingView Desktop is a local Electron app. Railway containers cannot run it.** That means:

- **In local dev / on a demo laptop:** real charts work end-to-end.
- **In Railway production:** there is no TV Desktop. Set `USE_MOCK_TA=1` in the Railway env. Users hitting the hosted URL will see the convincing inline SVG chart that's there today.
- **Containerised TV Desktop** is a v2 problem (see `docs/SESSION_LOG.md` 2026-05-20). Until then, the chart-manipulation wedge is local-demo-only.

If you forget and set `USE_MOCK_TA=0` in production, every chart-related prompt will return `error: "tradingview_mcp_unreachable"` until you flip it back.

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
| --- | --- | --- |
| Frontend | Vercel | `vercel --prod` from `frontend/` |
| Backend | Railway (or Fly.io) | Connect repo, set env vars (set `USE_MOCK_TA=1`), deploy on push |
| Postgres | Supabase Cloud | Already hosted |
| Object storage (chart screenshots) | Supabase Storage | `screenshots` bucket, public read (only if/when we move off inline base64) |

Live URLs (TBD after Phase 9): `agentic-brokerage.vercel.app` (frontend), `agentic-brokerage-api.up.railway.app` (backend).

## Pre-launch checklist

See [SECURITY.md § Pre-launch lockdown checklist](./SECURITY.md#pre-launch-lockdown-checklist). Every item must pass before sharing the URL with a tester.

## Useful references

- Plan (this build): `/Users/tom/.claude/plans/nifty-moseying-aho.md`
- Demo source (deterministic mockup, visual reference): `/Users/tom/Downloads/agentic_brokerage_demo.html`
- Design rationale: `/Users/tom/Downloads/agentic-brokerage-session-recap-2026-05-19.md`
- Product memory: `~/.claude/projects/-Users-tom-Desktop-workspace/memory/project_agentic_brokerage.md`
