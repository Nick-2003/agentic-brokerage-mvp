# API_CONTRACT.md — Agentic Brokerage MVP

**Last changed:** 2026-05-29 · **Status:** v1.0 — matches the live Python backend

This is the **single source of truth for the HTTP boundary** between:

- **Frontend** — Next.js 15 PWA on Vercel (the mobile app). Owns `frontend/`.
- **Backend** — **Python · FastAPI + uvicorn**, deployed on **Railway**. Owns `backend/`. Runs a Claude agent loop (Anthropic SDK directly) and streams Server-Sent Events. (The earlier Node-on-Vercel design in this doc was wrong and never built — Plan A / Python is LOCKED, see `CLAUDE.md`.)

**Rules:**
1. Neither side ships a field that isn't in this document.
2. Field names are **`snake_case`** (Python convention) — on the wire, in widgets, everywhere.
3. The widget `data` schemas are owned by **`backend/prompts/widget_contract.md`** and mirrored in **`frontend/lib/widgets.ts`** — change those two together; this doc summarises them.
4. If this doc and `CLAUDE.md` / `widget_contract.md` disagree, the same change fixes one of them — never leave them divergent.

---

## 1. Environments & base URL

- Backend runs as `uvicorn main:app` (dev: `cd backend && uv run uvicorn main:app --reload --port 8000`; prod: Railway, see `backend/railway.json`). `backend/` is on `sys.path` — there is no `backend.` package prefix.
- Frontend reaches the backend either via Next.js rewrites (`/api/* → <backend>/api/*`, see `frontend/next.config.js`) or directly via `NEXT_PUBLIC_API_URL`. **Never hardcoded.**
- Local dev: backend `http://localhost:8000`, frontend `http://localhost:3000`.
- Only two endpoints exist today: `GET /healthz` and `POST /api/chat` (§5, §6). Everything else is **planned, not built** (§9).

## 2. Authentication — NONE yet ⚠️

- **There is currently no auth.** `POST /api/chat` takes a `user_id` **in the request body**, defaulting to the string `"demo"`. It is client-supplied and therefore spoofable (SECURITY_AUDIT **HIGH-2**).
- **Do not ship to separate real users in this state.** Real auth is **P4.1** (Supabase magic-link): the frontend will send `Authorization: Bearer <supabase_jwt>`, the backend will verify it and derive `user_id` server-side, and `user_id` will be **removed from the request body**. When P4.1 lands, update §5 + this section together.
- No endpoint returns `401` today (nothing is gated).

## 3. CORS

Configured in `backend/main.py` via `CORSMiddleware`:

- `allow_origins` = the `CORS_ALLOW_ORIGINS` env var (comma-separated), default `http://localhost:3000`.
- `allow_credentials` = `true`; `allow_methods` = `*`; `allow_headers` = `*`.
- In production set `CORS_ALLOW_ORIGINS` to the exact Vercel origin (not `*`). Note: when the frontend uses Next.js rewrites, requests are same-origin and CORS doesn't apply — CORS only matters for direct `NEXT_PUBLIC_API_URL` calls (e.g. `pnpm dev`).

## 4. Conventions

- JSON bodies, UTF-8.
- **Field names: `snake_case`.**
- Timestamps: ISO 8601 UTC strings, e.g. `2026-05-29T13:57:16Z` (and fractional-second forms from `datetime.isoformat()`).
- Money: JSON **numbers**. A separate `currency` field carries the **symbol string** (`"$"`, `"€"`, `"£"`) — values are not pre-formatted.
- `*_html` fields may contain only `<strong>` and `<em>` (sanitised on render). No other tags.
- Numbers in widgets must trace to a tool result (the *"no number without a source"* trust rule). It is enforced by the system prompt, surfaced via the `sources` array (§7) — there is **no** machine-checked `tool_call_id` link on widget fields today (the validator in `CLAUDE.md` is deliberately deferred).

---

## 5. `GET /healthz` — liveness + config diagnostics

No auth, no body. Returns `200` with:

```json
{
  "ok": true,
  "model": "claude-opus-4-5",
  "tools_registered": ["get_portfolio", "get_quote", "...", "chart_apply_indicator"],
  "alpaca_configured": true,
  "anthropic_key_present": true
}
```

- `model` — the `ANTHROPIC_MODEL` in use.
- `tools_registered` — the agent's tool names (18 as of 2026-05-29). Diagnostic only; not part of the chat contract.
- `alpaca_configured` / `anthropic_key_present` — booleans derived from key presence/prefix. Used to spot mis-configured deploys.

---

## 6. `POST /api/chat` — the conversation endpoint

The one endpoint that matters. The frontend sends a single user message; the backend streams thinking breadcrumbs and one generative widget (or a plain message).

### Request

Headers: `Content-Type: application/json` (and `Accept: text/event-stream`). No `Authorization` yet (§2).

Body (`ChatRequest` in `main.py`):

```json
{
  "message": "what's NVDA doing today?",
  "user_id": "demo"
}
```

- `message` — **required**, string, length 1–4096. The single new user turn. *(There is no server-side history today — each call is one turn. Multi-turn / `conversation_id` arrives with P4.2, §9.)*
- `user_id` — optional, string, length 1–128, default `"demo"`. **Client-supplied today** (§2); will move to the JWT at P4.1.

### Response — Server-Sent Events

`Content-Type: text/event-stream` (via `sse-starlette`). The backend emits a sequence of frames, each:

```
event: <type>\r\n
data: <single-line JSON>\r\n
\r\n
```

> **Framing notes (these have bitten us — keep them):**
> - Lines end with `\r\n`; frames are separated by a blank line, i.e. `\r\n\r\n`. The frontend SSE parser (`frontend/lib/sse.ts`) splits frames on `/\r?\n\r?\n/` and lines on `/\r?\n/`. Splitting on `\n\n` alone parses **zero** events — that was a real bug.
> - `sse-starlette` injects periodic keep-alive **comment** lines (`: ping - <timestamp>`). They are not events; ignore any line starting with `:`.
> - Emission is incremental but the contract only promises a *stream of events* in order — a consumer must not assume timing.

Event types (`agent.py`), in typical emission order:

| `event`       | `data` payload                                              | meaning |
|---------------|-------------------------------------------------------------|---------|
| `thought`     | `{ "text": "Reading your portfolio…" }`                     | one human-readable breadcrumb. 0..N. |
| `tool_call`   | `{ "id": "toolu_…", "name": "get_quote", "args": { … } }`   | a tool was invoked. diagnostic. |
| `tool_result` | `{ "id": "toolu_…", "ok": true, "summary": "get_quote → 1 quotes" }` | result of the matching `tool_call` (same `id`). diagnostic. |
| `widget`      | a Widget object — `{ "type", "data", "sources" }` (§7)      | the generative UI card. Typically exactly one, terminal. |
| `message`     | `{ "text": "markdown…" }`                                   | plain markdown reply (loose chat, not pinnable). Terminal alternative to `widget`. |
| `error`       | `{ "message": "…" }`                                         | failure. terminal. |
| `done`        | `{ "elapsed_ms": 8421, "iterations": 3 }`                   | stream complete. always the final event on success. |

Rules:

- A successful turn ends with **exactly one** terminal payload — a `widget` **or** a `message` — followed by `done`.
- `tool_call` and its `tool_result` share the same `id`. Tool calls may run in parallel; results may arrive in any order.
- On failure, an `error` event is emitted; `done` may or may not follow. (Pre-stream failures surface as a normal HTTP error instead.)
- Frontend rendering: `thought` → breadcrumbs; `widget` → card; `message` → chat bubble; `tool_call` / `tool_result` → **dev mode only**.

---

## 7. Widget objects

Every `widget` event's `data` is one object with this envelope:

```json
{
  "type": "research_card",
  "data": { "…": "type-specific, see below" },
  "sources": [
    { "name": "FMP — consensus + ratios + profile" },
    { "name": "SEC 10-Q", "url": "https://www.sec.gov/…" }
  ]
}
```

- `type` — one of the 8 types below (`frontend/lib/widgets.ts::KNOWN_WIDGET_TYPES`). Unknown types fall back to a plain markdown bubble.
- `data` — type-specific (below). **`snake_case`.**
- `sources` — array of `{ "name": string, "url"?: string }`. Names the data behind the numbers. There is no `id` / `title` / `pinnable` / `tool_call_id` field (those were from the old Node design).

### The 8 widget `data` schemas

Canonical JSON lives in `backend/prompts/widget_contract.md`; TS types in `frontend/lib/widgets.ts`. Summary:

| `type` | `data` fields |
|---|---|
| `morning_brief` | `headline: string`, `paragraphs: string[]` |
| `research_card` | `ticker`, `company_name`, `current_price: number \| null`, `currency`, `rating: "BUY"\|"HOLD"\|"SELL"`, `target_price: number`, `horizon_months: number`, `thesis_html`, `catalysts: string[]`, `risks: string[]` |
| `ta_chart` | `ticker`, `timeframe`, `current_price: number`, `screenshot_url?: string` (a `data:image/png;base64,…` URL when real, else empty/mock SVG path), `indicators_applied: string[]`, `key_levels: { resistance: number[], support: number[] }`, `trend_summary_html` |
| `order_ticket` | `side: "buy"\|"sell"`, `ticker`, `shares`, `notional`, `limit_price`, `currency`, `tp_price?`, `sl_price?`, `rr_ratio?`, `risk_amount?`, `reward_amount?`, `portfolio_pct?`, `within_risk_rule?: bool`, `bracket_source?: "from_prompt"\|"from_research"\|"from_default"`, `notes_html?` |
| `live_trade` | `order_id`, `ticker`, `side: "long"\|"short"`, `shares`, `fill_price`, `current_price`, `currency`, `unrealized_pnl`, `unrealized_pnl_pct`, `tp_armed_at?`, `sl_armed_at?`, `filled_at: iso`, `news_since_fill?: [{ headline, source, ts }]` |
| `thesis` | `ticker`, `rating`, `horizon: string`, `weight_pct_nav`, `confidence: string`, `tldr_html`, `reasons_to_be_in: string[]`, `what_to_watch_weekly: string[]`, `thesis_breakers: string[]` |
| `tracker` | `ticker`, `thesis_tldr_html`, `trade: { side, shares, fill_price, current_price, unrealized_pnl, unrealized_pnl_pct, tp?, sl? }` |
| `portfolio_risk` | `risk_score`, `risk_label`, `risk_summary`, `sector_exposure: [{ label, pct, severity: "normal"\|"warn"\|"danger" }]`, `flags: [{ severity: "low"\|"med"\|"high", title, detail_html }]`, `suggestions: string[]` |

Notes on fields that recently changed:
- `research_card.current_price` is `number | null` — `null` when no live price source is available (yfinance down + no FMP profile price). The frontend renders `—` and omits the upside; the agent must not fabricate a price (Proposal 008).
- `live_trade.news_since_fill` is optional — present only when post-fill news exists (Proposal 001).

### Full example — `research_card`

```json
{
  "type": "research_card",
  "data": {
    "ticker": "AAPL",
    "company_name": "Apple Inc.",
    "current_price": 310.58,
    "currency": "$",
    "rating": "BUY",
    "target_price": 324.0,
    "horizon_months": 12,
    "thesis_html": "Apple compounds at <strong>29.0% FCF margin</strong>; reasonable at <strong>10.21× EV/Sales</strong>.",
    "catalysts": ["iPhone 17 cycle", "Services at $100B+ run-rate"],
    "risks": ["China demand", "App Store regulation"]
  },
  "sources": [
    { "name": "FMP — consensus + ratios + profile" }
  ]
}
```

---

## 8. Errors

- **Before the stream opens** (e.g. malformed body → FastAPI/Pydantic 422): a normal HTTP error response.
- **During the stream** (`/api/chat`): an `error` SSE event — `{ "message": "…" }` — and the stream closes.
- Tool-level failures are **not** stream errors. They come back inside the relevant `tool_result` (`ok: false`) or as an `error` field on the tool's JSON output (e.g. `alpaca_fetch_failed`, `yfinance_fetch_failed`, `fmp_fetch_failed`, `tradingview_mcp_unreachable`); the agent then surfaces them honestly in the terminal `widget`/`message`.

---

## 9. Planned, not yet implemented

These do **not** exist on the backend today. Listed so the boundary is unambiguous — add them here (with real shapes) when they land.

- **Auth headers** (`Authorization: Bearer`) — **P4.1** (Supabase magic-link). Replaces body `user_id`.
- **`GET /api/conversations`** + optional `conversation_id` on `ChatRequest` — **P4.2** (Supabase persistence). Reuse the `Finance_Chatbot` schema shape; RLS-scoped to the authenticated `user_id`.
- No `/api/trades`, `/api/config`, `PATCH`/`DELETE` routes are planned — those were artefacts of the old Node design and are removed.

## 10. Versioning

- This document *is* the version. A breaking change edits this file in the same change that ships it.
- Bump the **Last changed** line on every edit.
