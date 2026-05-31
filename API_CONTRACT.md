# API_CONTRACT.md — Agentic Brokerage MVP

**Last changed:** 2026-06-01 · **Status:** v1.1 — matches the live Python backend (P4.1 auth + P4.2 persistence applied)

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
- Endpoints today: `GET /healthz` (§5), `POST /api/chat` (§6), `GET /api/conversations` + `GET /api/conversations/{id}` (§6b). Remaining additions (Mem0 / Langfuse / pinned-widget persistence) are **planned, not built** (§9).

## 2. Authentication — Supabase magic-link JWT (P4.1 ✅ applied 012 + 015)

- The frontend signs in via **Supabase magic link** (`signInWithOtp`), then attaches the access token as `Authorization: Bearer <supabase_jwt>` on every request. The body **no longer carries `user_id`** — any client-supplied `user_id` field is silently dropped by Pydantic, and identity is derived **server-side** from the verified token's `sub` claim (a Supabase UUID). This closes SECURITY_AUDIT **HIGH-2** (the old spoofable-`"demo"` issue).
- The backend's `verify_jwt` (in `backend/auth.py`) **dispatches on the token's header `alg`**:
  - **`ES256` / `RS256` / `EdDSA`** — Supabase's "JWT Signing Keys" (current default). Verified against the project's published **JWKS public key** at `<SUPABASE_URL>/auth/v1/.well-known/jwks.json` for the token's `kid`. PyJWT's `PyJWKClient` caches keys after first fetch; the fetch runs in FastAPI's threadpool (the dependency is sync) so it never blocks the event loop. The anon key is sent as the `apikey` header.
  - **`HS256`** — legacy symmetric secret, verified offline with `SUPABASE_JWT_SECRET`.
  - **`none` / any other alg** — rejected as `unsupported_alg`.
  - No alg-confusion risk: HS256 uses the secret, ES256 uses the JWKS public key — different key material, each pinned to its own `algorithms=[…]` allowlist at decode time.
- The audience must be `"authenticated"` (Supabase's standard claim); 10-second clock-skew leeway.
- **`REQUIRE_AUTH` env flag** (mirrors the codebase's `USE_MOCK_*` kill-switches):
  - `REQUIRE_AUTH=1` (production posture, set in Railway) → unauthenticated requests are rejected with **401** `authentication_required`.
  - `REQUIRE_AUTH=0` (local default) → a token-less request falls back to the `"demo"` user, so the deterministic mock demo, `scripts/smoke_test.sh`, and curl checks keep working. A token *if present* is always verified — a provided-but-invalid token is **never** silently downgraded to demo.
- **401 error details** the backend may return (in the response body `{"detail":"…"}`):

  | `detail` | Meaning |
  |---|---|
  | `authentication_required` | No token sent, but `REQUIRE_AUTH=1`. |
  | `token_expired` | Signed token, but `exp` is in the past. |
  | `invalid_token` | Bad signature / wrong audience / malformed / `kid` not in JWKS. |
  | `token_missing_sub` | Token verified but had no `sub` claim. |
  | `unsupported_alg` | Header `alg` is none / not in our allowlist. |
- **5xx details related to auth/persistence:**

  | Status | `detail` | Meaning |
  |---|---|---|
  | 500 | `auth_not_configured` | HS256 token arrived but no `SUPABASE_JWT_SECRET` is set. |
  | 503 | `jwks_unavailable` | JWKS public-key fetch failed (network blip / wrong `SUPABASE_URL`). |

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
  "anthropic_key_present": true,
  "require_auth": false,
  "auth_configured": true,
  "persistence_configured": true
}
```

- `model` — the `ANTHROPIC_MODEL` in use.
- `tools_registered` — the agent's tool names (18 as of 2026-06-01). Diagnostic only; not part of the chat contract.
- `alpaca_configured` / `anthropic_key_present` — booleans derived from key presence/prefix. Used to spot mis-configured deploys.
- `require_auth` (P4.1) — value of the `REQUIRE_AUTH` env var as a bool. **Must be `true` in production.**
- `auth_configured` (P4.1) — `true` when **either** `SUPABASE_URL` (asymmetric path: JWKS) **or** `SUPABASE_JWT_SECRET` (HS256 path) is set to a non-placeholder value. A deploy with `require_auth: true` but `auth_configured: false` is mis-configured (every authed request would 401 / 500).
- `persistence_configured` (P4.2) — `true` when `SUPABASE_URL` + `SUPABASE_ANON_KEY` are both real (non-placeholder). When `false`, the persistence layer (§6b) silently no-ops — chat still streams, but turns aren't saved.

---

## 6. `POST /api/chat` — the conversation endpoint

The one endpoint that matters. The frontend sends a single user message; the backend streams thinking breadcrumbs and one generative widget (or a plain message).

### Request

Headers: `Content-Type: application/json` (and `Accept: text/event-stream`). **`Authorization: Bearer <supabase_jwt>`** when signed in (P4.1, §2). Demo / `REQUIRE_AUTH=0` requests may omit it.

Body (`ChatRequest` in `main.py`):

```json
{
  "message": "what's NVDA doing today?",
  "conversation_id": "eb98059d-ae39-4cbb-9f37-1616349c821b"
}
```

- `message` — **required**, string, length 1–4096. The single new user turn.
- `conversation_id` — **optional** (P4.2), string ≤ 64 chars. When omitted, the backend creates a new conversation and announces its id via the `conversation` SSE event (see below). When provided, that conversation is continued — RLS guarantees it must be owned by the authenticated user, otherwise the backend silently creates a fresh row rather than disclose its existence.
- `user_id` is **no longer accepted** (it was the spoofable client field; identity now derives from the JWT). Any stray `user_id` in the body is silently dropped (Pydantic ignores extra fields), so old callers don't break.

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

Event types (`agent.py` + `main.py`), in typical emission order:

| `event`        | `data` payload                                              | meaning |
|----------------|-------------------------------------------------------------|---------|
| `conversation` | `{ "id": "uuid", "title": "…" \| null }`                    | **(P4.2)** Emitted at most once, *before* the agent stream starts, when the request is authenticated AND persistence is configured. The frontend captures this id and echoes it as `conversation_id` on subsequent turns. Absent in demo mode. |
| `thought`      | `{ "text": "Reading your portfolio…" }`                     | one human-readable breadcrumb. 0..N. |
| `tool_call`    | `{ "id": "toolu_…", "name": "get_quote", "args": { … } }`   | a tool was invoked. diagnostic. |
| `tool_result`  | `{ "id": "toolu_…", "ok": true, "summary": "get_quote → 1 quotes" }` | result of the matching `tool_call` (same `id`). diagnostic. |
| `widget`       | a Widget object — `{ "type", "data", "sources" }` (§7)      | the generative UI card. Typically exactly one, terminal. |
| `message`      | `{ "text": "markdown…" }`                                   | plain markdown reply (loose chat, not pinnable). Terminal alternative to `widget`. |
| `error`        | `{ "message": "…" }`                                         | failure. terminal. |
| `done`         | `{ "elapsed_ms": 8421, "iterations": 3 }`                   | stream complete. always the final event on success. |

Rules:

- A successful turn ends with **exactly one** terminal payload — a `widget` **or** a `message` — followed by `done`.
- `tool_call` and its `tool_result` share the same `id`. Tool calls may run in parallel; results may arrive in any order.
- On failure, an `error` event is emitted; `done` may or may not follow. (Pre-stream failures surface as a normal HTTP error instead.)
- Persistence failures (DB unreachable, write rejected) are **swallowed** — the user-facing stream is never broken by a persistence hiccup; the turn just doesn't get saved. Surfaced operationally via `/healthz.persistence_configured` and via missing rows the user can spot in `GET /api/conversations`.
- Frontend rendering: `conversation` → captured as the current id (not displayed); `thought` → breadcrumbs; `widget` → card; `message` → chat bubble; `tool_call` / `tool_result` → **dev mode only**.

---

---

## 6b. Conversation routes (P4.2 ✅ applied 016)

These read user-scoped chat history. **Both are RLS-protected** — the backend forwards the user's JWT to Supabase via `client.postgrest.auth(user_jwt)`, so PostgreSQL's `auth.uid() = user_id` policy physically prevents any user from reading another's rows. The Supabase **service key is NOT used** in `db.py`; it would bypass RLS and is reserved for admin tasks.

### `GET /api/conversations`

Lists the authenticated user's conversations, most-recently-updated first (limit 50).

Headers: `Authorization: Bearer <supabase_jwt>`.

Response `200`:

```json
{
  "conversations": [
    {
      "id": "eb98059d-ae39-4cbb-9f37-1616349c821b",
      "title": "give me a tldr on my portfolio",
      "created_at": "2026-06-01T10:42:11.123456Z",
      "updated_at": "2026-06-01T10:43:02.987654Z"
    }
  ]
}
```

- **Demo mode** (no token, `REQUIRE_AUTH=0`) → returns `{"conversations": []}`. Same shape when `persistence_configured` is `false`.
- **A user with no conversations** → returns `{"conversations": []}`.
- 401 with the usual `detail` (§2) when `REQUIRE_AUTH=1` and no/bad token.

### `GET /api/conversations/{conversation_id}`

Returns the ordered messages of one conversation owned by the authenticated user.

Headers: `Authorization: Bearer <supabase_jwt>`. Path: `{conversation_id}` is a UUID.

Response `200`:

```json
{
  "conversation_id": "eb98059d-ae39-4cbb-9f37-1616349c821b",
  "messages": [
    { "id": "…", "role": "user",      "content": "…", "widgets": null,    "created_at": "…" },
    { "id": "…", "role": "assistant", "content": "…", "widgets": [ … ],   "created_at": "…" }
  ]
}
```

- `role` ∈ `"user"` | `"assistant"`.
- `widgets` — array of full widget envelopes (§7) the assistant emitted that turn, or `null` for user rows / pure-text replies.
- **Returns `404 not_found`** for a missing conversation **or** a conversation owned by a different user — those cases are indistinguishable by design (RLS returns the empty set; the backend doesn't disclose existence). Demo mode also returns 404.

### Schema (where it lives)

`backend/db/schema.sql` (run once in the Supabase SQL Editor). Four `public` tables, all with RLS on and the policy `for all to authenticated using (auth.uid() = user_id) with check (auth.uid() = user_id)`:

| Table | Purpose | Wired to an endpoint? |
|---|---|---|
| `conversations` | One row per chat session: `id, user_id, title, created_at, updated_at`. `set_updated_at` trigger bumps `updated_at` on UPDATE. | Yes — §6 + this section. |
| `messages` | One row per turn: `id, conversation_id, user_id, role, content, widgets (jsonb), created_at`. | Yes — written by `/api/chat`, read by §6b above. |
| `pinned_widgets` | One row per pinned widget. RLS on, but **frontend wiring deferred** to a follow-up — pins still live in client state today. | Not yet. |
| `user_profiles` | Per-user metadata (`user_id PK`, `display_name`, …). | Not yet. |

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

- **Pinned-widget persistence routes** — `GET /api/pinned_widgets`, `POST /api/pinned_widgets`, `DELETE /api/pinned_widgets/{id}`. The `pinned_widgets` table is in §6b's schema with RLS already on; only the routes + frontend wiring are missing.
- **Conversation-history UI** — backend reads exist (§6b); a sidebar/picker in the frontend that calls them is a follow-up.
- **P4.3 — Mem0 memory.** Per-user fact recall injected into the system prompt before the LLM call and stored after. Scoped to the authenticated `user_id` UUID — never a client-supplied value (cross-user-leak hazard). No new public route; lives inside the existing `/api/chat` flow.
- **P4.4 — Langfuse observability.** Wraps the agent loop with traces tagged by the authenticated `user_id`. Read-only side-effect; no public route.
- **Service-key admin routes** — none planned; the service key bypasses RLS and is reserved for one-off ops.
- No `/api/trades`, `/api/config`, `PATCH`/`DELETE` routes are planned — those were artefacts of the old Node design and are removed.

## 10. Versioning

- This document *is* the version. A breaking change edits this file in the same change that ships it.
- Bump the **Last changed** line on every edit.
