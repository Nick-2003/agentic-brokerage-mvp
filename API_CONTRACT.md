# API_CONTRACT.md — Agentic Brokerage MVP

**Last changed:** 2026-05-22 · **Status:** draft v0.1

This is the **single source of truth for the HTTP boundary** between:

- **Frontend** — Next.js PWA on Vercel (the mobile app). Owns `frontend/`.
- **Backend** — Node serverless on Vercel, evolved from `github.com/Nick-2003/Finance_Chatbot`. Owns `backend/`.

**Rules:**
1. Neither side ships a field that isn't in this document.
2. Any change is a PR that edits this file *first*, reviewed by both devs.
3. If this doc and `CLAUDE.md` disagree, the same PR fixes one of them — never leave them divergent.

---

## 1. Environments & base URL

- The frontend reads the backend base URL from `NEXT_PUBLIC_API_BASE_URL`. **Never hardcoded.**
- Local dev: `http://localhost:3000` (via `vercel dev`). Production: the deployed Vercel URL.
- All endpoints live under `/api`.

## 2. Authentication

- Scheme: **Supabase JWT** (magic-link login). The frontend obtains the session access token from Supabase Auth and sends it on every request:

  ```
  Authorization: Bearer <supabase_access_token>
  ```

- The backend verifies the token and derives `userId` server-side. **The frontend never sends `userId` in the body.**
- **Transition mode:** until multi-user auth lands, the backend MAY accept tokenless requests and fall back to a single dev user (`gerald`). This is dev-only and must be removed before any external tester. See migration Phase 3.
- Missing/invalid token when one is required → `401` (see §8).

## 3. CORS

For the frontend origin, the backend must return:

- `Access-Control-Allow-Origin: <exact frontend origin>` — an exact origin in production, not `*`.
- `Access-Control-Allow-Methods: GET, POST, PATCH, DELETE, OPTIONS`
- `Access-Control-Allow-Headers: Content-Type, Authorization` — **`Authorization` is required.**
- `OPTIONS` preflight answered with `204`.

## 4. Conventions

- JSON bodies, UTF-8.
- Field names: `camelCase`.
- Timestamps: ISO 8601 UTC, e.g. `2026-05-22T09:57:32Z`.
- Money: JSON **numbers** in the instrument's quote currency (USD) — not strings, not pre-formatted.
- Errors: every error response uses the shape in §8.

---

## 5. `POST /api/chat` — the conversation endpoint

The one endpoint that matters. The frontend sends the conversation; the backend streams back thinking breadcrumbs and generative widgets.

### Request

Headers: `Authorization: Bearer ...`, `Content-Type: application/json`, `Accept: text/event-stream`.

Body:

```json
{
  "conversationId": "uuid-or-null",
  "messages": [
    { "role": "user", "content": "what's NVDA doing today?" }
  ],
  "context": {
    "activeAsset": "NVDA",
    "timeframe": "1D",
    "position": { "symbol": "NVDA", "side": "LONG", "entry": 920.0, "size": 10 }
  }
}
```

- `messages` — full running history; the last item is the new user turn. Required, non-empty.
- `conversationId` — `null` on the first turn. The backend returns the created id in the `done` event.
- `context` — optional. Mirrors what the user is looking at; `position` may be omitted.

### Response — Server-Sent Events

`Content-Type: text/event-stream`. The backend emits a sequence of events, each:

```
event: <type>
data: <single-line JSON>

```

> **Backend note:** the frontend consumes a *stream of events*. It does **not** care whether the backend emits them token-by-token in real time or flushes them all in one burst at the end. A "burst" implementation satisfies this contract today; true streaming can be added later **without any frontend change**. See migration Phase 0.

Event types, in typical emission order:

| event         | `data` payload                                              | meaning                                              |
|---------------|-------------------------------------------------------------|------------------------------------------------------|
| `thought`     | `{ "text": "Reading NVDA 10-Q…" }`                          | one human-readable breadcrumb. 0..N.                 |
| `tool_call`   | `{ "id": "tc_1", "name": "get_quote", "args": { ... } }`    | a tool was invoked. diagnostic.                      |
| `tool_result` | `{ "id": "tc_1", "ok": true, "summary": "NVDA $942.50" }`   | result of the matching `tool_call`. diagnostic.      |
| `widget`      | a Widget object (§6)                                        | a generative UI card. 0..N.                          |
| `message`     | `{ "text": "markdown…" }`                                   | plain chat reply (loose, not pinnable). 0..1.        |
| `error`       | `{ "code": "...", "message": "..." }`                       | terminal failure.                                    |
| `done`        | `{ "conversationId": "uuid", "elapsedMs": 8421 }`           | stream complete. always the final event on success. |

Rules:

- A successful response contains **at least one** of `widget` or `message`.
- A `tool_call` and its `tool_result` share the same `id`.
- `done` is always the final event on success; `error` is the final event on failure.
- Frontend rendering: `thought` → breadcrumbs, `widget` → cards, `message` → chat bubble. `tool_call` / `tool_result` are shown in **dev mode only**.

---

## 6. Widget objects

Every `widget` event's `data` is one object matching this envelope:

```json
{
  "type": "research_card",
  "id": "w_abc123",
  "title": "NVDA — Equity Research",
  "data": { "...": "type-specific, see below" },
  "sources": [
    { "name": "Alpaca", "toolCallId": "tc_1", "url": null }
  ],
  "pinnable": true
}
```

- `type` — one of the schemas below.
- `sources` — **required and non-empty whenever `data` contains any number.** This enforces the *"no number without a source"* trust rule from `CLAUDE.md`: every numeric value must trace to a `tool_call` via `toolCallId`.
- `pinnable` — whether the user may pin this card to their dashboard.

### Widget types — the `data` shape per `type`

| `type`           | `data` fields |
|------------------|---------------|
| `morning_brief`  | `portfolioPnl: {usd, pct}`, `marketContext: string`, `watch: [{symbol, note}]` |
| `research_card`  | `symbol`, `rating: "BUY"\|"HOLD"\|"SELL"`, `priceTarget: number`, `currentPrice: number`, `thesis: string`, `catalysts: string[]`, `risks: string[]` |
| `ta_chart`       | `symbol`, `timeframe`, `chartUrl: string`, `indicators: [{name, value}]`, `levels: [{kind: "support"\|"resistance", price}]` |
| `order_ticket`   | `symbol`, `side: "BUY"\|"SELL"`, `qty: number`, `orderType: "market"\|"limit"`, `limitPrice?: number`, `estCost: number`, `takeProfit?: number`, `stopLoss?: number` |
| `live_trade`     | `symbol`, `side`, `qty`, `entryPrice`, `currentPrice`, `pnl: {usd, pct}`, `status` |
| `thesis`         | `symbol`, `tldr: string`, `whyIn: string`, `whatToWatch: string[]`, `breakers: string[]` |
| `tracker`        | `trade: <live_trade data>`, `thesis: <thesis data>` |
| `portfolio_risk` | `concentrationScore: number`, `sectors: [{name, weightPct}]`, `flags: string[]`, `hedges: [{action, rationale}]` |

### Full example — `research_card`

```json
{
  "type": "research_card",
  "id": "w_9f2a",
  "title": "NVDA — Equity Research",
  "data": {
    "symbol": "NVDA",
    "rating": "BUY",
    "priceTarget": 1100,
    "currentPrice": 942.50,
    "thesis": "Dominant compute platform for AI training/inference; data-center revenue compounding on Blackwell ramp.",
    "catalysts": ["Blackwell GB200 shipping ahead of plan", "Sovereign AI commitments"],
    "risks": ["China export restrictions", "Customer concentration"]
  },
  "sources": [
    { "name": "Alpaca", "toolCallId": "tc_1", "url": null },
    { "name": "SEC 10-Q", "toolCallId": "tc_2", "url": "https://www.sec.gov/..." }
  ],
  "pinnable": true
}
```

---

## 7. Other REST endpoints

These already exist in the backend and stay REST/JSON. Only **auth** and **CORS** change; once multi-user auth lands, every query is scoped to the authenticated `userId`.

| Method · path | Response |
|---|---|
| `GET /api/conversations` | `{ conversations: [{id, title, activeAsset, messageCount, createdAt, updatedAt}] }` |
| `GET /api/conversations?id=<uuid>` | full thread `{ id, title, messages, ... }` |
| `DELETE /api/conversations?id=<uuid>` | `{ ok: true }` (soft-delete) |
| `GET /api/trades` (`?status=`, `?symbol=`, `?limit=`) | `{ trades: [...], stats: {...} }` |
| `POST /api/trades` | created trade object |
| `PATCH /api/trades?id=<uuid>` | updated trade object |
| `DELETE /api/trades?id=<uuid>` | `{ ok: true }` |
| `GET /api/config` | deployment/diagnostic info (no auth) |

## 8. Errors

- **Non-stream endpoints:** HTTP status code + body `{ "error": { "code": string, "message": string, "detail"?: string } }`.
- **`/api/chat`:** if the failure happens *before* the stream opens, return a normal HTTP error. If *after*, emit an `error` SSE event and close the stream.
- Codes: `unauthorized` (401), `bad_request` (400), `upstream_failed` (502), `rate_limited` (429), `server_error` (500).

## 9. Versioning

- This document *is* the version. A breaking change = a PR editing this file, reviewed by both devs.
- Bump the `Last changed` line at the top on every change.
