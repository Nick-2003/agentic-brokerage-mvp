# Agentic Brokerage MVP

A prompt-first mobile brokerage where every action — research, charting, trading, alerts, risk audits — is a prompt that generates a personalised widget you can pin to your dashboard.

> ## ⚠️ ACTIVE PIVOT (2026-06-05) — IBKR + WhatsApp/email waitlist briefing
>
> The chat MVP above is **PAUSED**. The **live, deployed** product is a pre-launch **waitlist briefing**: land → join waitlist → magic-link sign-in → **connect Interactive Brokers** via a one-time Flex token → a daily **WhatsApp + email** narrative briefing of what moved in your portfolio (the LLM rail writes it — Anthropic/OpenAI/DeepSeek per `LLM_RAIL`, currently DeepSeek; Twilio + Resend deliver it; a web permalink holds the full text).
>
> **Live URLs:** frontend **<https://agentic-brokerage-mvp-front.vercel.app>** (`/connect`) · backend **<https://agentic-brokerage-mvp-production.up.railway.app>** (`/healthz`).
>
> **Read first for the live product:** `.self_management/DECISION_pivot_waitlist.md` (rationale), `.self_management/PRIORITIES.md` (W1–W6 + status), `.self_management/OPERATOR_CHECKLIST.md` (non-code remainder), `docs/DEPLOY.md` (deploy runbook). The chat-MVP setup below still works and is the reference for when chat resumes.

**Status:** waitlist product **deployed & live** (the full `land → connect IBKR → daily WhatsApp/email brief` loop runs in production: Railway web + briefing cron, Vercel, Supabase). Chat MVP: feature-complete through P5, **paused**.

**Start every dev session by reading:** [CLAUDE.md](./CLAUDE.md) · [SCOPE.md](./SCOPE.md) · [METRICS.md](./METRICS.md) · [SECURITY.md](./SECURITY.md) · [docs/ICP.md](./docs/ICP.md) · `.self_management/CONTEXT_TRANSFER.md`.

## Local setup

### Prereqs

- Python 3.13+ (3.14 fine)
- Node 22+ (`nvm install 22` — Vercel build also uses 22)
- `uv` (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- `pnpm` (`npm i -g pnpm`)
- Postgres (via Supabase local CLI or Docker)
- TradingView Desktop (paused chat only — for the talk-to-charts wedge; see "Talk-to-your-charts" below)

### Required API accounts (free)

`backend/.env.example` is the **source of truth** for every env var (with setup notes per provider). Accounts:

| Service | Why | How to get the key |
| --- | --- | --- |
| **LLM rail** (pick ≥1) | Powers chat + the brief narrative. Selectable via `LLM_RAIL`: **Anthropic** (console.anthropic.com → API Keys), **OpenAI** (platform.openai.com → API keys), or **DeepSeek** (platform.deepseek.com → API keys). A usage-limit on the Anthropic rail auto-fails-over to DeepSeek. **Live: running on DeepSeek** (Anthropic + OpenAI credits exhausted). | any one provider's key + set `LLM_RAIL` accordingly |
| Supabase | Auth + Postgres + RLS + magic links | supabase.com → new project → free tier → URL + anon key + **service key** |
| **Interactive Brokers** | **Read-only holdings/NAV (Flex Web Service)** — the live product + the main-page portfolio | IBKR Account Mgmt → set up an *Activity* Flex Query + a Flex Web Service token (see `backend/.env.example` "Holdings") |
| **Twilio** | **WhatsApp delivery** of the daily brief (system-side) | twilio.com → Messaging → WhatsApp (Sandbox to start; Business sender = W6.4a, see `.self_management/WHATSAPP_BUSINESS_SENDER.md`) |
| **Resend** | **Email delivery** of the daily brief (system-side) | resend.com → free tier (3k/mo) → verify a sending domain (SPF/DKIM) → API key (Sending access) |
| Alpaca | Paper trading (chat; **trading currently disabled**) | alpaca.markets → Paper Trading dashboard → API Keys |
| FMP | Research data for the `research_card` | financialmodelingprep.com → free tier (~87 sample symbols) |
| PostHog (EU) | Analytics — chat funnel + waitlist funnel | posthog.com → project → public `phc_` key + `eu.i.posthog.com` |
| TradingView | Charts (paused chat) | tradingview.com → free account is fine |

### `.env` (backend/.env)

**Copy `backend/.env.example` → `backend/.env` and fill it in — it's the canonical, commented list.** Everything is **mock-first**, so the app boots with no keys (deterministic demo). The variable groups:

- **Core — LLM rail:** `LLM_RAIL` (`anthropic` default | `openai` | `deepseek`) selects the primary; supply that rail's key — `ANTHROPIC_API_KEY` + `ANTHROPIC_MODEL` (default `claude-opus-4-5`), `OPENAI_API_KEY` + `OPENAI_MODEL`, or `DEEPSEEK_API_KEY`. `LLM_FALLBACK_ENABLED=1` adds automatic DeepSeek fallback on an Anthropic usage-limit. Every rail is **mock-first** (`USE_MOCK_DEEPSEEK`/`USE_MOCK_OPENAI`), so the app still boots keyless. See the rail block in `backend/.env.example`.
- **Core — rest:** `SUPABASE_URL` / `SUPABASE_SERVICE_KEY`, `REQUIRE_AUTH`.
- **Holdings / portfolio (live):** `IBKR_FLEX_TOKEN`, `IBKR_FLEX_QUERY_ID`, `USE_MOCK_IBKR`; `PORTFOLIO_SOURCE` (default `ibkr`, per-user), `TRADING_ENABLED` (default `0`).
- **Brief delivery (live):** `TWILIO_ACCOUNT_SID`/`_AUTH_TOKEN`/`_WHATSAPP_FROM` + `USE_MOCK_WHATSAPP`; `RESEND_API_KEY` + `EMAIL_FROM`/`EMAIL_FROM_NAME` + `EMAIL_UNSUBSCRIBE_SECRET` + `USE_MOCK_EMAIL`; `PUBLIC_BASE_URL`/`PUBLIC_BACKEND_URL` (permalink + unsubscribe).
- **Chat (paused):** `ALPACA_API_KEY`/`_SECRET`, `FMP_API_KEY`, `MEM0_API_KEY`, `LANGFUSE_*`, the `USE_MOCK_TA` TradingView block (see below).
- **Optional dep groups:** `uv sync --group auth` / `--group memory` / `--group whatsapp` as needed.

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
pnpm dev   # http://localhost:3000  (chat at /, waitlist onboarding at /connect)
```

## The waitlist product (the live track)

Onboarding is the `/connect` page (waitlist → magic-link sign-in → connect IBKR + WhatsApp/email opt-in). The daily brief is a **system job** (not the agent), run by the scheduler:

```bash
# Fully-offline dry run (build every brief, send NOTHING) — proves the pipeline:
USE_MOCK_IBKR=1 USE_MOCK_BRIEFING=1 USE_MOCK_WHATSAPP=1 USE_MOCK_EMAIL=1 \
  backend/.venv/bin/python scripts/run_briefings.py --dry-run

# REAL run (IBKR fetch → LLM narrative → WhatsApp/email send → log), capped + idempotent:
backend/.venv/bin/python scripts/run_briefings.py --max-users 1
#   --force bypasses the W6.5 12h resend guard (e.g. to re-test the same day)
```

In production this is the **Railway briefing cron** (`python -m scheduler` on a UTC `cronSchedule`) — there's no public endpoint that triggers a send (threat 1). Provider setup: WhatsApp → `.self_management/WHATSAPP_BUSINESS_SENDER.md`; email (Resend domain/DNS + env) → the go-live section of `.proposed_changes/038-email-briefing-resend/README.md`.

## Talk-to-your-charts (TradingView MCP) — *paused chat MVP*

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

### Limitation: live charts require TradingView Desktop to be *open*

This is the most common confusion, so it's called out explicitly:

- **`USE_MOCK_TA=0` is not enough on its own.** The real path also needs **TradingView Desktop actually running with the debug port**:

  ```bash
  open -a "TradingView" --args --remote-debugging-port=9222
  ```

  Verify it's live before using the app: `curl -s localhost:9222/json/version` should return a JSON blob whose User-Agent contains `TVDesktop`. If that curl fails, TV Desktop isn't reachable.

- **If TV Desktop is closed (or the debug port is down) while `USE_MOCK_TA=0`,** the chart card can **silently degrade to mock data** — `current_price` and the S/R levels fall back to the built-in mock quote cache (e.g. NVDA shows `$942.50`), the screenshot is omitted (you get the inline SVG, not a real TradingView capture), even though the card is otherwise on the "real" path. As of proposal **029**, this degraded state is no longer disguised: the card's **`sources` are downgraded to `TradingView (mocked — live data unavailable)`** so you can tell at a glance the numbers aren't live. Before 029 it misleadingly showed `TradingView Desktop` / `Live OHLC via TradingView MCP` over mock numbers (a trust-principle-#3 gap).

- **Net rule:** for live charts you need *both* `USE_MOCK_TA=0` **and** TV Desktop open on `:9222`. For a clean, honest demo without TV Desktop, set `USE_MOCK_TA=1` — then the card is fully mock and labelled as such.

## Project layout

See [CLAUDE.md § Repo layout](./CLAUDE.md#repo-layout).

## How to contribute (to yourself)

End every coding session by appending to [docs/SESSION_LOG.md](./docs/SESSION_LOG.md):

- What was built
- What decisions surfaced
- What assumptions you introduced

Don't skip. Five minutes per session. This is the practice that prevents architectural drift across Claude Code sessions.

## Deployment

**Deployed & live (P6).** Full runbook + troubleshooting: `docs/DEPLOY.md`. Operator config remainder: `.self_management/OPERATOR_CHECKLIST.md`.

| Component | Where | How |
| --- | --- | --- |
| Frontend | **Vercel** | Root Dir = `frontend`, Node 22. `https://agentic-brokerage-mvp-front.vercel.app` |
| Backend (web) | **Railway** | FastAPI web service; `USE_MOCK_TA=1` + `REQUIRE_AUTH=1`. `https://agentic-brokerage-mvp-production.up.railway.app` (`/healthz`) |
| Briefing **cron** | **Railway** (2nd service) | `python -m scheduler` on a UTC `cronSchedule`, `restartPolicyType:NEVER`, **no public trigger endpoint** (threat 1) |
| Postgres | Supabase Cloud | Already hosted; run `backend/db/schema.sql` + `backend/db/schema_waitlist.sql` |

Six deploy-only fixes are documented in `docs/DEPLOY.md` (Docker `--create-home`/`UV_NO_CACHE`, the `railway.json` startCommand, `next.config.js` scheme-normalize, Supabase anon-key validation, magic-link Site-URL allow-list, Twilio Sandbox 24h window).

## Pre-launch checklist

See [SECURITY.md § Pre-launch lockdown checklist](./SECURITY.md#pre-launch-lockdown-checklist). Every item must pass before sharing the URL with a tester.

## Useful references

- **Live product:** `.self_management/DECISION_pivot_waitlist.md` · `.self_management/PRIORITIES.md` · `.self_management/OPERATOR_CHECKLIST.md` · `docs/DEPLOY.md`
- **Cold-start brief:** `.self_management/CONTEXT_TRANSFER.md`
- **HTTP contract:** `API_CONTRACT.md` · **proposal index:** `.proposed_changes/STATUS.md` · **decisions log:** `docs/SESSION_LOG.md`

> The original spec's `/Users/tom/...` plan/demo/memory paths are historical (the user is **Nicholas** — "Tom" was an earlier naming; same person). The canonical references are the `.self_management/` set above.
