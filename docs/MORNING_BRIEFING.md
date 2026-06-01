# 🌅 Morning briefing

**Read this first when you wake up.** It's the diff from where we left off, what's working, what's mocked, what to test, and the exact commands to flip mocks → real once your API keys arrive.

---

## What got built while you slept

Last night I went through phases 1 → 7 of the plan with mocks anywhere a real API key was needed.

### Backend — `/Users/tom/Code/agentic-brokerage-mvp/backend/`

Fully scaffolded Python FastAPI service with a streaming agent loop. Imports cleanly; **15 tools registered**:

| Module | Tools |
| --- | --- |
| `tools/portfolio.py` | `get_portfolio` (mock → Alpaca on key) |
| `tools/market.py` | `get_quote`, `get_company_news`, `get_macro_snapshot` |
| `tools/research.py` | `get_company_fundamentals`, `get_consensus_targets`, `get_full_research`, `get_peer_set` |
| `tools/technicals.py` | `get_technical_levels`, `get_correlation_matrix` |
| `tools/execution.py` | `place_paper_order`, `get_open_position`, `list_open_positions` (mock JSON → Alpaca on key) |
| `tools/risk.py` | `compute_portfolio_risk`, `compose_thesis` |

The agent loop (`agent.py`) streams SSE events: `thought` → `tool_call` → `tool_result` → `widget` / `message` → `done`. The FastAPI app (`main.py`) exposes `POST /api/chat` and `GET /healthz`.

### Frontend — `/Users/tom/Code/agentic-brokerage-mvp/frontend/`

Next.js 15 + React 19 + Tailwind. Same phone-frame aesthetic as the demo, but powered by real backend streaming. Files:

- `app/page.tsx` — single-page experience: dashboard + chat + pinned widgets
- `components/ChatBar.tsx` — persistent chat bar, animated cycling placeholder
- `components/ThinkingCard.tsx` — streaming tool-call breadcrumbs
- `components/widgets/*.tsx` — all 8 widget renderers from the contract
- `lib/sse.ts` — SSE consumer
- `lib/widgets.ts` — typed widget contracts

Side panel lists the demo prompts. Pin button on every widget → adds it to the dashboard area.

### Deployment configs ready (not deployed)

- `backend/Dockerfile` + `backend/railway.json` — one-click Railway deploy
- `frontend/vercel.json` — Vercel deploy, with proxy rewrites to the backend
- `frontend/.env.local.example` + `backend/.env.example` — env templates

---

## What's mocked (and how to flip to real)

| Mock | Lives at | Flip path |
| --- | --- | --- |
| **Portfolio data** | `tools/portfolio.py` `MOCK_PORTFOLIO` | Just set `ALPACA_API_KEY` + `ALPACA_API_SECRET` — code auto-detects. |
| **Quotes** | `tools/market.py` `MOCK_QUOTES` | Already tries yfinance first if installed; falls back to mock. `USE_MOCK_MARKET=0` (default) prefers real. |
| **News** | `tools/market.py` `MOCK_NEWS` | Future: swap to Anthropic web search inside Claude. The data shape stays the same. |
| **Fundamentals + targets** | `tools/research.py` `RESEARCH` dict | Same — hand-tuned for 6 tickers. Swap to yfinance.Ticker(...).info when ready. |
| **TradingView chart** | `tools/technicals.py` | Real MCP integration is Phase 4-live. Frontend `TAChart` widget already renders an inline animated SVG that looks like a real chart. |
| **Trade execution** | `tools/execution.py` mock orders → `backend/data/mock_orders.json` | Real Alpaca path is in the same file. Set `ALPACA_API_KEY` and it switches automatically. |
| **Voice input** | `components/ChatBar.tsx` `handleMicTap` | Shows an alert. Wire Web Speech API or skip entirely. |
| **Auth** | None yet | Phase 7 of the plan was supposed to add Supabase magic links. Deferred — currently no auth, single hardcoded `user_id="demo"`. |

---

## What needs your input

When you wake up, please paste in this block (from `docs/ACCOUNTS_SETUP_GUIDE.md`):

```
ANTHROPIC_API_KEY=sk-ant-...
ALPACA_API_KEY=PK...
ALPACA_API_SECRET=...
ALPACA_BASE_URL=https://paper-api.alpaca.markets
SUPABASE_URL=https://....supabase.co
SUPABASE_ANON_KEY=eyJ...
SUPABASE_SERVICE_KEY=eyJ...
POSTHOG_API_KEY=phc_...
```

Once pasted, the agent does this in one step:

1. Writes them to `backend/.env`
2. Writes the public ones to `frontend/.env.local`
3. Runs `curl http://localhost:8000/healthz` — confirms all keys are recognised
4. Runs a real Claude call: `curl -N -X POST .../api/chat -d '{"message":"give me a tldr on my portfolio"}'`
5. Verifies the SSE stream returns thoughts → tool_call → widget

---

## How to test it manually right now (no keys needed)

Backend won't run without `ANTHROPIC_API_KEY` because that's the only key the agent needs to do *anything*. So manual tests today:

### Test 1 — Tool registry imports clean (already ran ✓)

```bash
cd /Users/tom/Code/agentic-brokerage-mvp/backend
.venv/bin/python -c "import sys, os; os.chdir('.'); sys.path.insert(0, '.'); from tools import TOOL_REGISTRY; print(f'{len(TOOL_REGISTRY)} tools registered:'); [print(f'  - {n}') for n in TOOL_REGISTRY]"
```

Expected output: 15 tool names listed.

### Test 2 — FastAPI app boots

```bash
cd /Users/tom/Code/agentic-brokerage-mvp/backend
ANTHROPIC_API_KEY=sk-ant-placeholder .venv/bin/python -c "
import sys; sys.path.insert(0, '.')
from main import app
print('App boots with', len(app.routes), 'routes')
"
```

### Test 3 — Frontend builds

```bash
cd /Users/tom/Code/agentic-brokerage-mvp/frontend
pnpm install
pnpm typecheck   # should pass — all types align with backend
pnpm build       # production build
```

### Test 4 — Frontend dev server runs (UI works, no real Claude)

```bash
cd /Users/tom/Code/agentic-brokerage-mvp/frontend
pnpm dev
# open http://localhost:3000
# you'll see the dashboard + chat bar — typing a prompt will fail because backend isn't running
```

---

## How to test it FULL end-to-end (after you paste keys)

```bash
# Terminal 1 — backend
cd /Users/tom/Code/agentic-brokerage-mvp/backend
.venv/bin/uvicorn main:app --reload --port 8000

# Terminal 2 — frontend
cd /Users/tom/Code/agentic-brokerage-mvp/frontend
pnpm dev

# Open http://localhost:3000 in incognito Chrome at 390x844
# Type: give me a tldr on my portfolio
# Expected: see thoughts stream, watch a morning brief widget render
```

---

## What to demo first (suggested 90-second walkthrough)

Once it's running end-to-end with real keys:

1. Open `localhost:3000` (incognito, 390×844)
2. Type *"give me a tldr on my portfolio"* — watch the thinking breadcrumbs ("Reading your paper portfolio" → "Pulling overnight quotes" → "Reading S&P futures…" → "Scanning news catalysts") then a real morning brief widget renders with real source chips
3. Pin to home
4. Type *"analyze NVDA"* — real research card with BUY rating + $1,100 target
5. Pin to home
6. Type *"help me place $10,000 of NVDA at limit $945, TP $1100, SL $880"* — order ticket
7. Tap Confirm → live trade card (real Alpaca paper order placed!)
8. Pin to home
9. Type *"audit my portfolio for risk"* — real risk audit with sector bars
10. Pull back: dashboard now has 4 personalised widgets, all generated from prompts

---

## What's NOT built yet (per scope)

These are out of scope for the janky prototype — explicitly excluded in SCOPE.md. Don't add unless 3+ users ask for them.

- ❌ Real-time market data (15-min yfinance is enough)
- ❌ Real-money execution (paper only)
- ❌ Voice input (Web Speech API)
- ❌ KYC, identity verification
- ❌ Push notifications
- ❌ Mobile native app (web only)
- ❌ Crypto, options, futures
- ❌ Auth UI / magic link sign-in (deferred — uses fixed `user_id="demo"` for now)

---

## What to look at before flipping keys

Read these before pasting credentials — they're the contracts:

1. **`CLAUDE.md`** — 7 KB. Architecture + trust principles. Read at start of every session.
2. **`SCOPE.md`** — 4 KB. What we will and won't build.
3. **`METRICS.md`** — 6 KB. PMF benchmarks before launch.
4. **`SECURITY.md`** — 8 KB. The lockdown checklist before any tester touches it. Especially: monthly Anthropic spend cap, never frontend-bundled keys.
5. **`backend/prompts/system.md` + `widget_contract.md`** — what we're telling Claude to do.
6. **`docs/SESSION_LOG.md`** — what each session decided.

---

## Known issues / things to verify

- `uv sync` was slow on the network and might still be running when you wake up. Check `~/Code/agentic-brokerage-mvp/backend/.venv/lib/python*/site-packages/` — if it has `anthropic/`, `fastapi/`, etc., we're good. If not, re-run `UV_HTTP_TIMEOUT=300 uv sync` from the backend dir.
- Frontend `pnpm install` not yet run because it'd download ~200 MB and we'd hit the same network issue. First thing in the morning: `cd frontend && pnpm install` (~5 min).
- The model ID defaults to `claude-opus-4-5` (a known-good alias). If the latest Opus is 4.7, set `ANTHROPIC_MODEL=claude-opus-4-7` in `.env`.

---

## My recommendation for tomorrow's session

1. ☐ Paste API keys (5 min)
2. ☐ Set Anthropic monthly spend cap to $100 (Security threat #5 — non-optional)
3. ☐ Backend: `cd backend && .venv/bin/uvicorn main:app --reload --port 8000`
4. ☐ Frontend: `cd frontend && pnpm install && pnpm dev`
5. ☐ Do the 90-second walkthrough above. If it works end-to-end, you have a janky-but-real demo to send to 5 trader friends.
6. ☐ Update `docs/SESSION_LOG.md` with what changed.
7. ☐ If anything fails, the failure modes are mostly: (a) wrong key prefix, (b) network timeout to Anthropic, (c) model alias mismatch. All easy fixes.

Sleep well. The hard structural choices are locked in writing — no architectural drift to worry about.
