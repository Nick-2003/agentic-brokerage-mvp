# Backend Plan A — Extend the Python backend  *(Recommended)*

**One-line:** Keep the verified Python FastAPI backend as the app backend; absorb the Node backend's strengths (memory, evaluation, persistence) as additive modules and use TrueNorth's MCP server as the live-data source. No rewrite, nothing discarded.

> Sibling document: `BACKEND_PLAN_B_adopt_node.md` (the alternative route). Read both, then decide.

---

## Context — read this first

The **Agentic Brokerage MVP** is an agent-first mobile brokerage: the UI collapses into one chat bar, and every answer is rendered as a generative "widget" card — research card, TA chart, order ticket, portfolio-risk panel, and so on. The frontend is a Next.js PWA. The question these two plans answer is **which backend powers it.**

Two candidate backends exist:

- **Python backend** — `agentic-brokerage-mvp/backend/`. FastAPI; streams Server-Sent Events (SSE); runs a Claude agent loop with 15 tools; emits 8 structured widget JSON schemas. **Built and verified** end-to-end against real Claude and real Alpaca paper trading (see `docs/SESSION_LOG.md`).
- **Node backend** — `github.com/Nick-2003/Finance_Chatbot` ("TrueNorth"). Vercel serverless. A polished crypto-trading chatbot with Mem0 memory, Langfuse evaluation, and Supabase persistence — but it returns markdown text (not widgets), does not stream, and is framed for a crypto perp desk.

**Key finding:** the frontend and the Python backend are a **verified matched pair** — identical `POST /api/chat` SSE protocol, the same 7 event types (`thought, tool_call, tool_result, widget, message, error, done`), the same 8 widget schemas. They have already run together end-to-end. **The frontend is therefore not blocked** — it works against the Python backend today.

---

## The approach

Keep the Python backend as the application backend — it is the complete, verified "brain" of the app. The Node backend's genuinely valuable parts are **additive**: Mem0 memory, Langfuse evaluation, and Supabase persistence are modules that bolt onto the Python backend with no rewrite, and TrueNorth's MCP server becomes the live research-data source. The brother contributes by owning those modules — patterns he has already built once in the Node repo — plus his MCP server. Nothing built so far is thrown away.

---

## Part 1 — Unblock & confirm the frontend  *(~10 min, no code changes)*

The matched pair already works; this just verifies it still runs.

1. **Start the backend** (deterministic demo mode — numbers match the demo HTML):
   ```bash
   cd agentic-brokerage-mvp/backend
   USE_MOCK_MARKET=1 USE_MOCK_BROKER=1 .venv/bin/uvicorn main:app --reload --port 8000
   ```
   Confirm: `curl -s localhost:8000/healthz` → `anthropic_key_present:true`, 15 tools registered.
2. **Backend-only smoke test:** `bash scripts/smoke_test.sh` — expect `thought → tool_call → tool_result → widget → done`.
3. **Start the frontend:**
   ```bash
   cd agentic-brokerage-mvp/frontend && pnpm dev
   ```
   Open `http://localhost:3000`, send a prompt, confirm thinking breadcrumbs and a rendered widget.
4. **Optional:** create `frontend/.env.local` from `.env.local.example`. Only needed to override the `localhost:8000` default or to set Supabase/PostHog keys later — `next.config.js` already defaults `NEXT_PUBLIC_API_URL` to `http://localhost:8000`.

If step 3 shows the user bubble but no response, check that the SSE frame-split fix in `frontend/lib/sse.ts` is intact (`/\r?\n\r?\n/`) — this was a past bug, already fixed.

## Part 2 — Reconcile the contract doc  *(~15 min)*

`API_CONTRACT.md` (drafted earlier) describes the **wrong** contract — it assumed the Node backend (`{conversationId, messages, context}`, `elapsedMs`). Rewrite it to match the **actual** Python implementation: request `{message, user_id}`; SSE events as emitted by `backend/agent.py`; `done` payload `{elapsed_ms, iterations}`; 8 widget schemas per `backend/prompts/widget_contract.md`. Keep it as the single consolidated HTTP-boundary reference for the brother.

## Part 3 — Best-of-both roadmap  *(additive, sequenced)*

Each item bolts onto the Python backend. None require a rewrite. The brother owns the modules he has already built once in the Node repo.

| # | Capability | What / where | Effort | Owner |
|---|---|---|---|---|
| 3a | **Supabase persistence** | New `backend/db.py` (Supabase Python client — `uv sync --group auth`). Add a `conversations` table (reuse `Finance_Chatbot/tn_app/db/schema.sql` shape). Persist messages per turn; add optional `conversation_id` to `ChatRequest`; add `GET /api/conversations`. Keys already in `backend/.env`. | ~1 session | Brother (built in Node `conversations.js`) |
| 3b | **Mem0 memory** | New `backend/memory.py` (`mem0ai` Python SDK): search memories before the LLM call → inject into the system prompt; store after. Mirror Node `chat.js` MEMORY BLOCK 1/2. | ~½–1 session | Brother (built in Node `memory.js`) |
| 3c | **Langfuse evaluation** | New `backend/observability.py` mirroring Node `observability.js` (`trace_chat_request`, `trace_llm_call`). Wrap `run_agent` / Anthropic calls. | ~½ session | Brother (built in Node `observability.js`) |
| 3d | **TrueNorth MCP data** | Point the research tools (`get_full_research`, `get_consensus_targets`, …) at TrueNorth's MCP server. Full spec already written: `docs/TRUENORTH_MCP_INTEGRATION.md`. Slots into the mock-first pattern — data source only, no prompt/widget changes. | ~1 session | Brother (it is his MCP server) |
| 3e | **Auth (Supabase JWT)** | Backend: verify `Authorization: Bearer` JWT → real `user_id` (`SECURITY_AUDIT.md` HIGH-2). Frontend: Supabase magic-link login (SDK installed, unwired); attach the token in `lib/sse.ts`. | ~1 session | Split — backend brother/Tom, frontend Tom |
| 3f | **Deploy** | Backend → Railway (`Dockerfile` + `railway.json` ready). Frontend → Vercel (fix the `CHANGE-ME` URL in `frontend/vercel.json`). | ~½ session | Tom |

**Sequencing:** Part 1 → 3a / 3b / 3c can proceed in parallel (independent) → 3d → 3e → 3f. Items 3a–3d are *not* required to "unblock" the frontend — it works without them; they are the enhancement track.

After 3b/3c land, update `CLAUDE.md` (its stack table lists PostHog only, no Mem0/Langfuse) — per its own "never deviate silently" rule.

---

## Effort & ownership

- **Total:** ~5–6 focused sessions for the full roadmap; the frontend is unblocked in **minutes** (Part 1).
- **Division of labour** — this is the concrete "build together": the brother owns the backend modules he has already built once in Node (persistence, memory, eval) plus his MCP data server; Tom owns the frontend and the frontend half of auth. Nothing is discarded.

## Risks & tradeoffs

- **Low risk.** Nothing verified is thrown away; the frontend already works against this backend.
- The Python backend runs on Railway — a long-lived server — so streaming SSE agent loops have **no timeout problem** (unlike Vercel serverless).
- Main ongoing discipline: the widget JSON contract is the cross-boundary source of truth — change `backend/prompts/widget_contract.md` and `frontend/lib/widgets.ts` **together** or they drift.

## Verification

- **Part 1:** `scripts/smoke_test.sh` passes; the frontend at `localhost:3000` renders a widget from a typed prompt.
- **Part 2:** every request / event / payload field name in `API_CONTRACT.md` matches `backend/main.py` + `backend/agent.py`.
- **3a:** a conversation survives a backend restart (reload it via `GET /api/conversations`).
- **3b:** a fact stated in conversation 1 is recalled in conversation 2.
- **3c:** a chat turn appears as a trace in the Langfuse dashboard.
- **3d:** `get_full_research` returns real data for a ticker outside the 7 hand-tuned mocks (e.g. `CRM`).
- **3e:** an unauthenticated `POST /api/chat` is rejected; a magic-link session succeeds.
- **3f:** the deployed frontend URL talks to the deployed backend end-to-end.

## Why this route is recommended

The Python backend is the complete, verified brain of the product; the Node backend's strengths are additive modules, not a reason to switch brains. Route A reaches the same destination as Route B — memory, eval, persistence, live data, auth, deployment — for materially less effort, while discarding none of the verified work and giving the brother a clear, well-scoped ownership area.
