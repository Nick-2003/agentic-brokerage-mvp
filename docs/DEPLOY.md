# DEPLOY — IBKR + WhatsApp waitlist briefing (P6)

**What this operationalizes:** the live-verified `land → connect IBKR → daily WhatsApp brief` loop (W1–W6.3). After this, the daily cron fires unattended each morning, the brief permalinks are phone-reachable, and the Twilio webhook/status-callback URLs are stable — retiring the ngrok/localhost friction.

**The split (locked architecture — not an either/or):**

| Piece | Host | Why |
| --- | --- | --- |
| **Backend web service** (FastAPI) | **Railway** | Long-lived: SSE agent loop, Twilio inbound/status webhooks, brief permalink API. Streaming would hit serverless timeouts. |
| **Briefing cron** (`scheduler`) | **Railway** (2nd service, same image) | Runs `run_daily_briefings` on a schedule and exits. Deliberately **no public trigger endpoint** — the send is a system job, never an agent tool (SECURITY threat 1). |
| **Frontend** (Next.js) | **Vercel** | Static-ish; magic-link friendly; `next.config.js` rewrites proxy `/api/*` to the backend. |

> **W6.4 (WhatsApp Business sender + approved templates) is Meta-gated and NOT part of this deploy.** Production runs on the Twilio **Sandbox** for the validation cohort (each tester sends `join <phrase>` once). Start Meta business verification in parallel; it does not block P6. See `self_management/TWILIO_SETUP.md`.

---

## Prerequisites

- A Supabase project (the one already used in dev is fine — schema is additive).
- A Railway account, a Vercel account, a Twilio account (Sandbox enabled).
- Real keys for: Anthropic, Twilio, Supabase (anon + **service** + URL), and the **`FLEX_TOKEN_ENC_KEY`** that encrypts stored Flex tokens. **The encryption key must be the SAME value used when the dev `ibkr_connections` rows were written** — rotating it makes existing ciphertext undecryptable (the cron will `skip` those users). If you're starting fresh, mint a new one (Step 0) and have testers re-connect.

---

## Step 0 — Collect / mint secrets

1. **`FLEX_TOKEN_ENC_KEY`** (if you don't already have one): from the repo,
   ```bash
   cd backend && .venv/bin/python -m token_crypto
   ```
   Copy the printed Fernet key. **Reuse your existing dev key** if you want existing connections to keep working.
2. Have these ready (sources in parentheses):
   - `ANTHROPIC_API_KEY` (Anthropic console)
   - `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_KEY` (Supabase → Settings → API). HS256-only projects also need `SUPABASE_JWT_SECRET`; ES256/JWKS projects (Supabase's current default) do **not** — verification uses `SUPABASE_URL` + anon key.
   - `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM` (Twilio Console; Sandbox `From` is `whatsapp:+14155238886`).

---

## Step 1 — Supabase schema (run once)

In the Supabase SQL editor, run all three schema files (idempotent / additive):

```
backend/db/schema.sql                  # chat-app tables (conversations, messages, …) — shared auth/RLS base
backend/db/schema_waitlist.sql         # waitlist_signups, ibkr_connections (encrypted), briefing_deliveries + RLS
backend/db/schema_published_briefs.sql # published_briefs (token PK, 7d expiry, service-role-only)
```

`ibkr_connections` stores the Flex token **encrypted** (Fernet, `gAAAAAB…`); RLS scopes user rows to `auth.uid()`. `published_briefs` has RLS **on with no policy** (service-role-only capability links). Confirm all three tables exist before deploying.

---

## Step 2 — Railway: backend web service

1. New project → Deploy from the `Nick-2003/agentic-brokerage-mvp` repo.
2. **Service settings → Root Directory = `backend`.** Railway then auto-detects `backend/railway.json` (Dockerfile builder, `/healthz` healthcheck). The Dockerfile already installs the `whatsapp` + `memory` optional groups, so Twilio/Mem0 are present in the image.
3. **Variables** — set the backend web-service env (see the matrix in Step 8). The waitlist-critical ones:
   - `REQUIRE_AUTH=1`  (kills the spoofable `"demo"` user — SECURITY_AUDIT HIGH-2)
   - `USE_MOCK_IBKR=0` `USE_MOCK_BRIEFING=0` `USE_MOCK_WHATSAPP=0` `USE_MOCK_TA=1`
   - `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`
   - `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_KEY` (+ `SUPABASE_JWT_SECRET` if HS256)
   - `FLEX_TOKEN_ENC_KEY`
   - `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM`, `TWILIO_OPTED_OUT_CODES=21610,63024,63015`
   - `PUBLISH_BRIEFS=1`, `BRIEF_PERMALINK_TTL_DAYS=7`
   - `PUBLIC_BASE_URL`, `CORS_ALLOW_ORIGINS`, `TWILIO_WEBHOOK_URL`, `TWILIO_STATUS_CALLBACK_URL` — **set these in Step 5/6** once you know the public URLs.
4. Deploy. Railway assigns a URL like `https://<svc>.up.railway.app`. **Note it — call it `$BACKEND`.**
5. **Verify:**
   ```bash
   curl -s $BACKEND/healthz | jq
   ```
   Expect `ok:true`, `require_auth:true`, `auth_configured:true`, `persistence_configured:true`, `connect_storage_configured:true`, and the full `tools_registered` list.

---

## Step 3 — Railway: briefing cron service

A **second service** in the same project, same repo, **Root Directory = `backend`**, but pointed at the cron config so it runs the job and exits instead of serving HTTP.

1. New service → same repo → Root Directory `backend`.
2. **Settings → Config-as-code path = `railway.cron.json`** (provided in this proposal). It sets:
   - `startCommand: uv run python -m scheduler` (the cron entry — `scheduler.py`'s `__main__` runs `run_daily_briefings()` and prints a JSON summary). `scripts/run_briefings.py` is **not** in the backend image's build context, so the module is invoked directly.
   - `cronSchedule: 0 23 * * 1-5` (**UTC** — = 07:00 HKT Tue–Sat, a morning brief over the just-closed US session). **Tune to your cohort's timezone.**
   - `restartPolicyType: NEVER`, no healthcheck (a finished run must not be restarted/probed).
3. **Variables** — the cron needs the same *backend* secrets it imports (`briefing`, `connections`, `whatsapp`, `published_briefs`): `ANTHROPIC_*`, `SUPABASE_URL/ANON/SERVICE`, `FLEX_TOKEN_ENC_KEY`, `TWILIO_*` (incl. `TWILIO_STATUS_CALLBACK_URL` from Step 6 so async opt-out mirrors fire), `PUBLIC_BASE_URL`, `PUBLISH_BRIEFS=1`, `USE_MOCK_IBKR=0 USE_MOCK_BRIEFING=0 USE_MOCK_WHATSAPP=0`, and the **cost cap** `BRIEFING_MAX_USERS_PER_RUN` (start small, e.g. `5`, for the cohort). It does **not** need `REQUIRE_AUTH`/`CORS` (no HTTP). *Tip: use Railway shared/reference variables so the two services don't drift.*
4. **Test before trusting the schedule** — trigger the cron service manually once (Railway → the cron service → "Run now" / redeploy), and confirm: a real WhatsApp brief is delivered, a `briefing_deliveries` row appears, and a `published_briefs` row + `/b/<token>` link land in the message. Per-user failures are isolated and written as `status='failed'` rows (monitor that table).

---

## Step 4 — Vercel: frontend

1. New project → import the repo → **Root Directory = `frontend`.** `vercel.json` (this proposal, with the stale `CHANGE-ME` rewrites removed) sets the install/build commands; routing is handled by `next.config.js` rewrites via `NEXT_PUBLIC_API_URL`.
2. **Environment Variables:**
   - `NEXT_PUBLIC_API_URL = $BACKEND`  (the Railway backend URL — **no** trailing slash, **no** `/api`)
   - `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` (anon key only — never the service key on the frontend)
   - `NEXT_PUBLIC_POSTHOG_API_KEY` (the `phc_` **Project** key), `NEXT_PUBLIC_POSTHOG_HOST=https://us.i.posthog.com`
3. Deploy. **Note the production origin — call it `$FRONTEND`** (e.g. `https://<proj>.vercel.app`).
4. Supabase → Authentication → URL Configuration → add `$FRONTEND` (and `$FRONTEND/connect`) to **Redirect URLs**, so magic links return to the deployed site.

---

## Step 5 — Cross-wire the origins (then redeploy)

The two halves only know each other's URLs after both exist:

- **Backend (Railway, both services):** set `PUBLIC_BASE_URL = $FRONTEND` (so brief permalinks are `$FRONTEND/b/<token>` — a phone-tappable origin, not localhost). Set `CORS_ALLOW_ORIGINS = $FRONTEND` on the web service.
- **Frontend (Vercel):** confirm `NEXT_PUBLIC_API_URL = $BACKEND`.
- Redeploy the backend web service + cron after changing their vars.

> **`localhost` HSTS trap (carried from W6.3):** never leave `PUBLIC_BASE_URL` as `localhost` in prod — browsers may force it to `https` and the link breaks. Use the real `$FRONTEND`.

---

## Step 6 — Twilio webhooks (point them at the deployed backend)

Twilio must call **stable** URLs and the signature must match the **exact** public URL.

- **Inbound** (PAUSE/RESUME/HELP): Twilio Console → WhatsApp Sandbox settings → "When a message comes in" → `POST $BACKEND/api/twilio/inbound`. Set backend var `TWILIO_WEBHOOK_URL = $BACKEND/api/twilio/inbound` (exact, for signature validation behind Railway's proxy). Keep `TWILIO_WEBHOOK_VALIDATE=1`.
- **Status callback** (async opt-out / 63015 mirror): set backend + cron var `TWILIO_STATUS_CALLBACK_URL = $BACKEND/api/twilio/status`. `whatsapp.send_whatsapp` attaches it per send; `POST /api/twilio/status` flips `opt_in` off on `failed`/`undelivered` + an opted-out `ErrorCode`.
- Redeploy backend + cron after setting these.

> Reminder (W6.1b): Twilio **intercepts** STOP/START (they never reach the webhook). In-WhatsApp control for the cohort is **PAUSE/RESUME**; real STOP surfaces asynchronously via the status callback. Consent is explicit — no auto-resume on reconnect.

---

## Step 7 — End-to-end smoke test (prod)

1. **Frontend:** open `$FRONTEND/connect` → join the waitlist → sign in via magic link → connect IBKR (Flex token + query id + WhatsApp number + consent).
   - Confirm a `waitlist_signups` row (`source=connect-page`) and an **encrypted** `ibkr_connections` row (`flex_token_encrypted` = `gAAAAAB…`).
2. **Cron (manual trigger):** run the cron service once → a real WhatsApp brief arrives; the message ends with `📄 …/b/<token>`; that URL renders the brief; `briefing_deliveries` logs `sent`.
3. **Opt-out:** from WhatsApp send `PAUSE` → `ibkr_connections.opt_in` flips false → next cron run `skip`s that user. `RESUME` re-enables.
4. **Schedule:** confirm the cron's next scheduled UTC tick is correct for your cohort's morning, then let it fire unattended.

---

## Step 8 — Secrets matrix

`B` = Railway backend web · `C` = Railway cron · `V` = Vercel frontend. Prod values shown; **secrets are set in each platform's dashboard, never committed.**

| Variable | B | C | V | Prod value / note |
| --- | :-: | :-: | :-: | --- |
| `ANTHROPIC_API_KEY` | ✅ | ✅ | | real key (briefing + chat) |
| `ANTHROPIC_MODEL` | ✅ | ✅ | | `claude-opus-4-5` (or set `BRIEFING_MODEL` cheaper) |
| `SUPABASE_URL` | ✅ | ✅ | | project URL |
| `SUPABASE_ANON_KEY` | ✅ | ✅ | | anon key |
| `SUPABASE_SERVICE_KEY` | ✅ | ✅ | | **service key** — admin read for connect-storage + cron; backend-only, never on V |
| `SUPABASE_JWT_SECRET` | ⚠️ | | | only if the project is HS256 (ES256/JWKS needs none) |
| `FLEX_TOKEN_ENC_KEY` | ✅ | ✅ | | Fernet key — **must match the key used to write existing rows** |
| `REQUIRE_AUTH` | `1` | | | reject unauthenticated `/api/chat` |
| `USE_MOCK_IBKR` | `0` | `0` | | real Flex (per-user tokens from the DB) |
| `USE_MOCK_BRIEFING` | `0` | `0` | | real Claude narrative |
| `USE_MOCK_WHATSAPP` | `0` | `0` | | real Twilio send |
| `USE_MOCK_TA` | `1` | | | **stays 1** — no TradingView Desktop on Railway |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` | ✅ | ✅ | | real creds |
| `TWILIO_WHATSAPP_FROM` | ✅ | ✅ | | Sandbox `whatsapp:+14155238886` |
| `TWILIO_OPTED_OUT_CODES` | ✅ | ✅ | | `21610,63024,63015` (drop `63015` once off Sandbox) |
| `TWILIO_WEBHOOK_URL` | ✅ | | | `$BACKEND/api/twilio/inbound` (exact) |
| `TWILIO_STATUS_CALLBACK_URL` | ✅ | ✅ | | `$BACKEND/api/twilio/status` (exact) |
| `TWILIO_WEBHOOK_VALIDATE` | `1` | | | keep validation on in prod |
| `PUBLIC_BASE_URL` | ✅ | ✅ | | `$FRONTEND` (permalink origin) |
| `PUBLISH_BRIEFS` | `1` | `1` | | publish + append permalink |
| `BRIEF_PERMALINK_TTL_DAYS` | `7` | `7` | | link expiry |
| `BRIEFING_MAX_USERS_PER_RUN` | | ✅ | | cron cost cap — start small (e.g. `5`) |
| `CORS_ALLOW_ORIGINS` | ✅ | | | `$FRONTEND` |
| `NEXT_PUBLIC_API_URL` | | | ✅ | `$BACKEND` |
| `NEXT_PUBLIC_SUPABASE_URL` / `_ANON_KEY` | | | ✅ | anon only |
| `NEXT_PUBLIC_POSTHOG_API_KEY` / `_HOST` | | | ✅ | `phc_` Project key |
| `LANGFUSE_*`, `MEM0_*`, `FMP_API_KEY` | ⬜ | ⬜ | | optional (chat-app / observability); no-op when unset |

⚠️ = conditional · ⬜ = optional.

---

## Notes & gotchas

- **Dockerfile groups — already correct.** `backend/Dockerfile` installs `--group whatsapp --group memory`, so Twilio + Mem0 ship in the image (the W3 "uv-sync prunes groups" footgun is handled). The chat-app `auth` group (only `posthog`) is **not** imported by any backend `.py` and is not needed in the image.
- **The cron exits 0 even when individual users fail** (failures are isolated + logged to `briefing_deliveries`). For alerting, monitor that table for `status='failed'` rather than the process exit code. (`scripts/run_briefings.py` gives non-zero-on-failure but isn't in the backend image; add `scripts/` to a future cron image if you want exit-code alerting.)
- **Vercel rewrites:** routing lives in `next.config.js` (covers `/api/chat|healthz|portfolio|waitlist|ibkr/*|brief/:token`), driven by `NEXT_PUBLIC_API_URL`. The old `vercel.json` rewrites (2 routes, `CHANGE-ME`) are removed to avoid a stale/partial override.
- **W6.4 (Business sender + templates)** replaces the Sandbox `From` + `join` opt-in with a registered sender + approved template (which carries the W6.3 permalink). Meta-gated; start verification now, slot it in when approved — no redeploy of this topology needed, just Twilio config + the template SID env.
