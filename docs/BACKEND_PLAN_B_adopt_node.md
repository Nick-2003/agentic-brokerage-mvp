# Backend Plan B — Adopt the Node backend  *(Alternative)*

**One-line:** Make `Finance_Chatbot` (the Node/Vercel "TrueNorth" backend) the app backend; rebuild its `/api/chat` to match the frontend's streaming + widget contract. Larger effort — it re-implements verified Python work — but consolidates on a single backend the brother owns.

> Sibling document: `BACKEND_PLAN_A_extend_python.md` (the recommended route). Read both, then decide.

---

## Context — read this first

The **Agentic Brokerage MVP** is an agent-first mobile brokerage: the UI collapses into one chat bar, and every answer is rendered as a generative "widget" card — research card, TA chart, order ticket, portfolio-risk panel, and so on. The frontend is a Next.js PWA. The question these two plans answer is **which backend powers it.**

Two candidate backends exist:

- **Python backend** — `agentic-brokerage-mvp/backend/`. FastAPI; streams Server-Sent Events (SSE); runs a Claude agent loop with 15 tools; emits 8 structured widget JSON schemas. **Built and verified** end-to-end against real Claude and real Alpaca paper trading (see `docs/SESSION_LOG.md`).
- **Node backend** — `github.com/Nick-2003/Finance_Chatbot` ("TrueNorth"). Vercel serverless. A polished crypto-trading chatbot with Mem0 memory, Langfuse evaluation, and Supabase persistence — but it returns markdown text (not widgets), does not stream, and is framed for a crypto perp desk.

**Key finding:** the frontend and the **Python** backend are a verified matched pair (identical SSE protocol, 7 event types, 8 widgets). The **Node** backend does *not* match the frontend: it returns a single markdown JSON blob, has no streaming, no widget schemas, and is crypto-framed. Adopting it means rebuilding it to the frontend's contract.

---

## The approach

Make the Node backend the application backend. The frontend is largely protocol-agnostic, but it expects a specific contract: a *streamed* sequence of SSE events carrying *structured widget JSON*. The Node backend currently returns one markdown JSON blob. Adopting it therefore means rebuilding its chat endpoint and agent loop to emit the frontend's contract, porting the equities toolset, and re-flavoring the prompt — while keeping the Node backend's existing memory / eval / persistence / deployment, which are this route's genuine advantage.

All work below is in the `Finance_Chatbot` repo, under `tn_app/`.

---

## Steps

### Step 0 — CORS & timeout fixes  *(~30 min)*
- `tn_app/vercel.json`: `Access-Control-Allow-Headers` currently allows only `Content-Type`. Add `Authorization` — once auth lands, the browser preflight fails without it. Replace `Access-Control-Allow-Origin: *` with the exact frontend origin for production.
- Bump `api/chat.js` `maxDuration` from `30` toward `60–300`. A multi-tool agent turn can exceed 30s and be killed mid-stream.

### Step 1 — Convert `/api/chat` to SSE streaming  *(~1 session)*
- `tn_app/api/chat.js` currently ends with `res.status(200).json(...)`. Replace with a `text/event-stream` response.
- Emit events: `thought`, `tool_call`, `tool_result`, `widget`, `message`, `error`, `done`. The `done` payload must be `{elapsed_ms, iterations}` — match `frontend/lib/sse.ts` field names exactly.
- Interim shortcut: emit all events in one burst at the end of the existing logic, then upgrade to true token streaming later. The frontend consumes an event stream either way and does not change.

### Step 2 — Build a widget-emitting agent loop  *(~1–2 sessions)*
- The Node backend returns markdown; the frontend renders 8 widget types and renders nothing without `widget` events.
- Add the 8 widget JSON schemas to the system prompt — copy `backend/prompts/widget_contract.md` from the Python repo.
- Add a parse step: extract the model's widget JSON and emit it as a `widget` event.
- Give every tool call a stable `id` so `tool_call`/`tool_result` correlate and `sources[].toolCallId` resolves. The current `toolCalls` array is a messy interleave of `{name,input}` and `{result}`.
- Budget for prompt tuning: the Python repo needed multiple sessions to get widgets emitting reliably (Bugs A & C in `SESSION_LOG.md`). That tuning is redone here.

### Step 3 — Port the equities toolset  *(~1–2 sessions — the largest chunk)*
- The frontend's widgets are fed by these 15 tools: `get_portfolio`, `get_quote`, `get_company_news`, `get_macro_snapshot`, `get_company_fundamentals`, `get_consensus_targets`, `get_full_research`, `get_peer_set`, `get_technical_levels`, `get_correlation_matrix`, `place_paper_order`, `get_open_position`, `list_open_positions`, `compute_portfolio_risk`, `compose_thesis`.
- The Node backend has crypto/MCP tools and a signal scanner — not these. Port them from `backend/tools/*.py` to Node, or wire equivalents via TrueNorth MCP + the existing Alpaca tools.

### Step 4 — Re-flavor the system prompt  *(~½ session)*
- `SYSTEM_PROMPT` in `chat.js` is a crypto perp desk (Deribit, Hyperliquid, Polymarket, liquidation clusters, funding rates). Rewrite it to US-equities retail — copy `backend/prompts/system.md` from the Python repo.

### Step 5 — Reconcile the request shape  *(~½ session)*
- The Node backend expects `{conversationId, messages, context, userId}`; the frontend sends `{message, user_id}`. Either adapt `frontend/lib/sse.ts` or make the Node endpoint accept the frontend's shape. Recommended: the latter, so the frontend stays put.

### Step 6 — Keep what the Node backend already has
- Mem0 memory, Langfuse evaluation, and Supabase persistence (conversations / trades / playbooks) stay as-is. These are the genuine advantage of this route — you inherit them without building them.

### Step 7 — Auth & deploy  *(~1 session)*
- Add Supabase JWT verification (`tn_app/lib/auth.js`); the frontend attaches the bearer token.
- The DB schema is single-user (`userId='gerald'`, RLS deny-all + service key). Add `user_id` columns to `conversations` / `trades` / `playbooks` and filter every query by the authenticated user.
- The Node backend is already deployed on Vercel — point the frontend's `NEXT_PUBLIC_API_URL` at that deployment.

---

## Effort & ownership

- **Total:** multi-session and larger than Route A — Steps 1–4 essentially re-implement the verified Python backend in Node.
- **Owner:** the brother (it is his repo), with the frontend dev verifying the contract at each step.

## Risks & tradeoffs

- **Redoes verified work.** Steps 1–4 re-create what the Python backend already does and has tested against real Claude + real Alpaca.
- **Crypto framing.** The backend, prompt, and tooling are perp-desk oriented; re-flavoring to equities is real work and easy to half-finish.
- **Vercel SSE timeout.** Serverless functions cap execution time; long agent streams risk being killed mid-response. Long-lived servers (Railway / Render / Fly) suit streaming better — which is what the Python backend already runs on.
- **Upside:** you inherit Mem0 + Langfuse + Supabase persistence + an existing deployment without building them, and you consolidate on a single backend with a single owner.

## When to choose this route

Pick Route B only if the brother will **exclusively** own and maintain a Node backend, and consolidating on his stack outweighs re-implementing the verified Python work. Otherwise Route A reaches the same destination — memory, eval, persistence, live data, auth, deployment — for materially less effort and discards nothing.

## Verification

- **Step 0:** a browser preflight (`OPTIONS /api/chat`) from the frontend origin with an `Authorization` header succeeds.
- **Step 1:** `POST /api/chat` returns `Content-Type: text/event-stream`; the frontend at `localhost:3000` receives events and resets its streaming state.
- **Step 2:** a research prompt produces a `widget` event whose JSON validates against a schema in `widget_contract.md`; the frontend renders the card.
- **Step 3:** each of the 8 widget types can be produced end-to-end (portfolio, research, chart, order ticket, live trade, thesis, tracker, portfolio risk).
- **Step 4:** an equities prompt no longer returns crypto-desk phrasing or invented Deribit/Hyperliquid numbers.
- **Step 7:** an unauthenticated `POST /api/chat` is rejected; a magic-link session succeeds; the deployed frontend talks to the deployed Node backend end-to-end.
