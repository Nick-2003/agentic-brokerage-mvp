# DEPLOY — IBKR + WhatsApp waitlist briefing (P6)

**What this operationalizes:** the live-verified `land → connect IBKR → daily WhatsApp brief` loop (W1–W6.3). After this, the daily cron fires unattended each morning, the brief permalinks are phone-reachable, and the Twilio webhook/status-callback URLs are stable — retiring the ngrok/localhost friction.

**The split (locked architecture — not an either/or):**

| Piece | Host | Why |
| --- | --- | --- |
| **Backend web service** (FastAPI) | **Railway** | Long-lived: SSE agent loop, Twilio inbound/status webhooks, brief permalink API. Streaming would hit serverless timeouts. |
| **Briefing cron** (`scheduler`) | **Railway** (2nd service, same image) | Runs `run_daily_briefings` on a schedule and exits. Deliberately **no public trigger endpoint** — the send is a system job, never an agent tool (SECURITY threat 1). |
| **Frontend** (Next.js) | **Vercel** | Static-ish; magic-link friendly; `next.config.js` rewrites proxy `/api/*` to the backend. |

> **W6.4 (WhatsApp Business sender + approved templates) is Meta-gated and NOT part of this deploy.** Production runs on the Twilio **Sandbox** for the validation cohort (each tester sends `join <phrase>` once). Start Meta business verification in parallel; it does not block P6. See `.self_management/TWILIO_SETUP.md`.

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
4. Deploy.
5. **Get the public URL — you must generate it; Railway does NOT auto-assign one.** Service → **Settings → Networking → Public Networking → Generate Domain**. When asked for the **target port**, enter the port the app listens on (it binds `${PORT}`, which Railway injects — so just confirm Railway's detected port, typically the one from `EXPOSE`/`$PORT`). This mints `https://<svc>.up.railway.app`. **Note it — call it `$BACKEND`.**
   - **The domain exists independently of deploy health** — you can generate it even while the deploy is failing. But traffic only routes to a **healthy** deployment, so until the healthcheck passes the URL returns `502`. So: fix the boot crash first (see Troubleshooting), *then* the domain serves.
   - The **cron service needs no domain** — don't generate one for it (it has no HTTP surface; that's deliberate — threat 1).
6. **Verify (once the deploy is healthy):**
   ```bash
   curl -s $BACKEND/healthz | jq
   ```
   Expect `ok:true`, `require_auth:true`, `auth_configured:true`, `persistence_configured:true`, `connect_storage_configured:true`, and the full `tools_registered` list.

---

## Step 3 — Railway: briefing cron service

A **second service** in the same project, same repo, **Root Directory = `backend`**, but pointed at the cron config so it runs the job and exits instead of serving HTTP.

1. New service → same repo → Root Directory `backend`.
2. **Settings → Config-as-code path = `railway.cron.json`** (provided in this proposal). **This is what makes it a cron** — without it, the new service falls back to the default `railway.json` and runs as a *second web server* (uvicorn + healthcheck), not the scheduler. Confirm the service shows a cron schedule + the `uv run python -m scheduler` start command after setting it. It sets:
   - `startCommand: uv run python -m scheduler` (the cron entry — `scheduler.py`'s `__main__` runs `run_daily_briefings()` and prints a JSON summary). `scripts/run_briefings.py` is **not** in the backend image's build context, so the module is invoked directly.
   - `cronSchedule: 0 23 * * 1-5` (**UTC** — = 07:00 HKT Tue–Sat, a morning brief over the just-closed US session). **Tune to your cohort's timezone.**
   - `restartPolicyType: NEVER`, no healthcheck (a finished run must not be restarted/probed).
   - ⚠️ **Config-as-code OVERRIDES the dashboard.** Because the cron schedule lives in `railway.cron.json`, editing the schedule in Railway's UI is *reverted on the next deploy*. To change the time, **edit `cronSchedule` in the file and push** (or delete it from the file and manage it in the dashboard — pick one source of truth, not both). The schedule is **UTC** — `0 23 * * 1-5` is 23:00 UTC, *not* your local time.
3. **Variables** — the cron needs the same *backend* secrets it imports (`briefing`, `connections`, `whatsapp`, `published_briefs`): `ANTHROPIC_*`, `SUPABASE_URL/ANON/SERVICE`, `FLEX_TOKEN_ENC_KEY`, `TWILIO_*` (incl. `TWILIO_STATUS_CALLBACK_URL` from Step 6 so async opt-out mirrors fire), `PUBLIC_BASE_URL`, `PUBLISH_BRIEFS=1`, `USE_MOCK_IBKR=0`, `USE_MOCK_BRIEFING=0`, `USE_MOCK_WHATSAPP=0`, and the **cost cap** `BRIEFING_MAX_USERS_PER_RUN` (start small, e.g. `5`, for the cohort). It does **not** need `REQUIRE_AUTH`/`CORS` (no HTTP). *Tip: use Railway shared/reference variables so the two services don't drift.*
4. **A Railway cron service runs ONLY on its schedule — NOT on deploy/redeploy.** Building or redeploying it just rebuilds the image and *schedules* it; the `python -m scheduler` command executes at the next `cronSchedule` tick, then the container exits. So a redeploy will **not** send a brief (by design — you don't want every redeploy blasting WhatsApp). Railway has no "Run now" button for cron services. To **test the job on demand**, pick one:
   - **Easiest (already proven in W5):** run it from your machine against your local `.env` — `backend/.venv/bin/python scripts/run_briefings.py --max-users 1`. Same code path the cron runs.
   - **On Railway (near-future schedule):** temporarily set `cronSchedule` in `railway.cron.json` to ~2 minutes in the future (UTC), push, let it fire, watch the cron service's **Deploy Logs** for the JSON summary, then revert. (Config-as-code overrides the dashboard, so edit the file; cron granularity is ~5 min — pick a concrete near-future minute.)
   - **`railway run` (deployed env, runs locally):** `railway link` → select the cron service → `railway run python -m scheduler` (from `backend/`, with the venv active). Injects the cron service's *Railway* env vars (same `FLEX_TOKEN_ENC_KEY`, Twilio, Supabase) into a local run — the best way to exercise the deployed config without copying secrets or waiting for the tick.
   - Confirm success the same way regardless: a real WhatsApp brief arrives, a `briefing_deliveries` row appears (`status='sent'`), and a `published_briefs` row + the `/b/<token>` link land in the message. Per-user failures are isolated as `status='failed'` rows (monitor that table).
   - **The `/b/<token>` permalink only resolves once the frontend (Vercel, Step 4) is live and `PUBLIC_BASE_URL` points to it.** Until then the WhatsApp text still sends, but the link 404s — set `PUBLISH_BRIEFS=0` on the cron to omit the link while testing pre-frontend, or just ignore the dead link.

---

## Step 4 — Vercel: frontend

1. New project → import the repo → **Root Directory = `frontend` (REQUIRED).** There is **no `package.json` at the repo root** — if Root Directory is left at the repo root, `pnpm install` finds nothing to install and the build **fails with `… exited with 1`**. Vercel reads `frontend/vercel.json` (this proposal, stale `CHANGE-ME` rewrites removed) for the install/build commands; routing is `next.config.js`'s job via `NEXT_PUBLIC_API_URL`. Also set **Node.js Version = 22.x** (Settings → General) — Next 15.5 + the committed `pnpm-lock.yaml` expect Node 22.
2. **Environment Variables:**
   - `NEXT_PUBLIC_API_URL = $BACKEND`  — **MUST include the `https://` scheme** (e.g. `https://agentic-brokerage-mvp-production.up.railway.app`), **no** trailing slash, **no** `/api`. `next.config.js` builds rewrite destinations as `${NEXT_PUBLIC_API_URL}/api/…`; if the value has no scheme, `next build` **fails** with `Error: Invalid rewrites found` (`destination` must start with `/`, `http://`, or `https://`). A bare host like `…railway.app` (no `https://`) is the #1 cause.
   - `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` (anon key only — never the service key on the frontend)
   - `NEXT_PUBLIC_POSTHOG_API_KEY` (the `phc_` **Project** key), `NEXT_PUBLIC_POSTHOG_HOST=https://eu.i.posthog.com`
3. Deploy. **Note the production origin — call it `$FRONTEND`** (e.g. `https://<proj>.vercel.app`).
4. Supabase → Authentication → **URL Configuration** (this is what makes magic links land on the deployed site, not localhost):
   - **Site URL** → set to `$FRONTEND` (e.g. `https://<proj>.vercel.app`). This is the **default redirect** Supabase uses when the requested `emailRedirectTo` isn't allow-listed — if it's left at `http://localhost:3000`, every magic link lands on localhost.
   - **Redirect URLs** (allow-list) → add `$FRONTEND/**` (wildcard covers `/connect` and the token fragment). The connect page requests `emailRedirectTo: window.location.href`, and Supabase **only honors a requested redirect if it matches this allow-list** — otherwise it silently falls back to the Site URL. Keep `http://localhost:3000/**` too so local dev still works.
   - Save, then request a **new** magic link — an already-sent link keeps whatever redirect it was minted with.

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

   ```bash
   # 1) DRY RUN first — builds the brief, sends/logs NOTHING:
   backend/.venv/bin/python scripts/run_briefings.py --dry-run --max-users 1
   # 2) REAL run — fetches IBKR, Claude writes it, sends WhatsApp, logs delivery:
   USE_MOCK_IBKR=0 USE_MOCK_BRIEFING=0 USE_MOCK_WHATSAPP=0 \
   backend/.venv/bin/python scripts/run_briefings.py --max-users 1
   ```

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
- **Dockerfile boot fix (first-deploy real-run).** The non-root `app` user is created with `useradd --create-home` and the image sets `ENV UV_NO_CACHE=1`. Without these, `uv run` at runtime tries to write a cache under a non-existent `$HOME` and the container crashes before binding the port → Railway's `/healthz` "service unavailable" → "replicas never became healthy." This is baked into the Dockerfile; no operator action beyond rebuilding.

---

## Troubleshooting

- **`Network › Healthcheck` fails / "1/1 replicas never became healthy"** — the build is fine; the *process* didn't start (or didn't open the port within the retry window). Read the **Deploy Logs** tab (the container's stdout — *not* the Build logs) to see the actual crash. Common causes:
  - *uv/HOME cache crash* — fixed by the Dockerfile `--create-home` + `UV_NO_CACHE=1` (above). Rebuild after the fix lands.
  - *Unexpanded `${PORT}` (uvicorn "invalid integer")* — a `startCommand` in `railway.json` (or a dashboard Custom Start Command) is run by Railway in **exec form / no shell**, so `--port ${PORT:-8000}` reaches uvicorn as a literal string and it exits. Fix: don't set a shell-variable start command; let the Dockerfile `CMD` (`["sh","-c", …]`, which *does* expand `$PORT`) run. `railway.json` here has **no** `startCommand` for exactly this reason. **Also check the dashboard:** Settings → Deploy → **Custom Start Command must be empty** (it overrides both `railway.json` and the Dockerfile `CMD`).
  - *Missing required env var at import time* — e.g. a client that raises if its key is absent. The healthcheck only passes once `main:app` imports cleanly and uvicorn binds. Confirm every "✅ B" var in the Step 8 matrix is set.
  - *Wrong port* — the app binds `${PORT}` (Railway injects it). Don't hardcode `8000` in the start command; keep `--port ${PORT:-8000}`.
  - *Healthcheck window too tight* — `railway.json` sets `healthcheckTimeout: 30`. If cold imports legitimately need longer, raise it (Railway allows up to ~300s). The boot crash here was instant, not slow, so 30s was not the cause.
- **URL returns `502` but the service "looks deployed"** — the domain is generated but the current deployment is unhealthy (see above). The 502 clears once a deployment passes `/healthz`.
- **Cron set but nothing runs / no deploy logs** — expected until the scheduled minute *actually arrives*; build logs never mention the schedule, and a deploy/redeploy does **not** trigger a run. Check, in order: (1) the schedule is **UTC**, not local (`0 23 * * 1-5` = 23:00 UTC) — if you set it for "now" in local time it's hours off; (2) you edited `cronSchedule` **in `railway.cron.json` and pushed** — a schedule typed into the dashboard UI is reverted by the config-as-code file on the next deploy; (3) today matches the day filter (`1-5` = Mon–Fri UTC); (4) the cron service's **latest deployment is "Success"** and used the cron config (start command `uv run python -m scheduler`, no healthcheck) — not a leftover web-server deploy. To prove the pipeline immediately, don't wait for the tick: run `backend/.venv/bin/python scripts/run_briefings.py --max-users 1` locally, or set `cronSchedule` to a concrete near-future UTC minute, push, and watch the cron service's Deploy Logs at that minute.
- **Vercel build fails: `pnpm install … && pnpm build` exited with `1`** — the app builds clean locally, so it's config, not code. Most likely **Root Directory is not `frontend`** (there's no root `package.json`, so install finds nothing → exit 1) — set it in Project Settings → General. Then check **Node.js Version = 22.x**. The real error is in the Vercel build log *above* the `exited with 1` line (a missing module, a prerender throw, an install error) — read that, not the wrapper. Reproduce locally with `cd frontend && pnpm build`.
- **`next build` fails with `Error: Invalid rewrites found` (`destination` does not start with `/`, `http://`, or `https://`)** — `NEXT_PUBLIC_API_URL` is set on Vercel **without a scheme** (e.g. `…railway.app` instead of `https://…railway.app`). `next.config.js` interpolates it into every `/api/*` rewrite destination, and Next rejects a schemeless destination. Fix: set `NEXT_PUBLIC_API_URL = https://<backend>.up.railway.app` (scheme, no trailing slash, no `/api`) and redeploy. (Vercel `[REDACTED]`s the value in the log — that redaction *is* the tell that the var is set, just wrong.)
- **`curl …vercel.app` → `404 … DEPLOYMENT_NOT_FOUND`** — there is **no successful production deployment** at that domain yet (every build so far failed), or the domain name is wrong. It resolves the moment a build succeeds. Confirm the exact production domain under Vercel → the project → **Domains**.
- **`Invalid API key` when signing in / connecting a broker** — that exact string is a **Supabase** error: an anon key that doesn't match its project URL. It's set in **two** places (both pair the **anon/public** key with the **matching project URL**): frontend `NEXT_PUBLIC_SUPABASE_URL`/`_ANON_KEY` (Vercel — used at sign-in, surfaces the message *verbatim* in the UI) and backend `SUPABASE_URL`/`SUPABASE_ANON_KEY` (Railway — used to write the connection). A raw `Invalid API key` in the browser points at the **frontend** key first (the backend wraps its failures as `could not store connection`). Common causes: URL and key from **different projects**; the **service_role** key pasted where the **anon/public** key belongs (or swapped); a **truncated**/newline-mangled paste (the JWT is long). Validate a key in 5 s — but use the **right endpoint per key type**. The `/rest/v1/` OpenAPI **root is `service_role`-only**, so a *valid* anon key returns `Invalid API key … Only the service_role API key can be used for this endpoint` there (a false negative — don't test the anon key on root). Test each correctly:
  - **anon** key: `curl -sS -i "<SUPABASE_URL>/auth/v1/settings" -H "apikey: <ANON_KEY>"` → `200` + JSON = valid; `Invalid API key` = bad.
  - **service_role** key: `curl -sS "<SUPABASE_URL>/rest/v1/" -H "apikey: <SERVICE_KEY>"` → full OpenAPI swagger = valid (also confirms the schema migrated — you'll see every table). Copy fresh values from Supabase → Project Settings → **API** (Project URL + anon public; service_role only into `SUPABASE_SERVICE_KEY`). ⚠️ **`NEXT_PUBLIC_*` are baked at BUILD time** — after fixing the Vercel value you must **redeploy** (a new build), not just save the var. (Note: `/healthz` `*_configured:true` only checks the var is *present*, not *valid*.)
- **Magic link redirects to `http://localhost:3000/#access_token=…` instead of the deployed site** — Supabase's **Site URL** is still `http://localhost:3000`, and the requested `emailRedirectTo` (`window.location.href` = your Vercel `/connect`) **isn't in the Redirect URLs allow-list**, so Supabase falls back to the Site URL. Fix in Auth → URL Configuration: set **Site URL = `$FRONTEND`** and add **`$FRONTEND/**`** to **Redirect URLs** (keep `http://localhost:3000/**` for dev). No code/redeploy needed — but you must request a **fresh** magic link (already-sent links keep the old redirect). This is *not* an API-key problem.
- **"Build log shows nothing" / the Logs tab is empty** — the **Logs** (Observability) tab is *runtime* logs (serverless/edge function output), **not** build output. A mostly-static Next.js frontend emits **no** runtime logs, so "No logs found" there is normal and means nothing about the build. **Build output lives per-deployment:** Deployments tab → click the latest deployment → the **Building** section. And the thing to read is the deployment's **status badge**: **Ready** (green) = build succeeded; **Error** (red) = failed (open Building for the reason). Don't diagnose frontend health from the Logs tab — **hit the URL**: `curl -sS -i https://<frontend>/connect` (expect `200`) and open it in a browser.
- **`curl …/healthz | jq` prints nothing (empty)** — almost always a **missing `https://`**. Railway's edge serves the generated domain on **HTTPS (443) only**; `curl host/healthz` defaults to plain HTTP (80) and `-s` swallows the empty redirect/refusal, so `jq` gets nothing. Always use the scheme, and diagnose with headers first:
  ```bash
  curl -sS -i https://<domain>/healthz          # -i shows status+headers; drop | jq until you see JSON
  curl -sS -L https://<domain>/healthz | jq     # once you confirm 200 + a JSON body
  ```
  If `-i` shows a connection error or 404, the **domain string is wrong** — copy it verbatim from Settings → Networking (it may carry an environment/random suffix). If it shows `502`, the deploy is unhealthy or the **generated domain's target port doesn't match the app's listening port** (the app binds Railway's injected `PORT`, seen as `8080` in the deploy logs — set the domain's target port to that, not `8000`).
- **The cron exits 0 even when individual users fail** (failures are isolated + logged to `briefing_deliveries`). For alerting, monitor that table for `status='failed'` rather than the process exit code. (`scripts/run_briefings.py` gives non-zero-on-failure but isn't in the backend image; add `scripts/` to a future cron image if you want exit-code alerting.)
- **Vercel rewrites:** routing lives in `next.config.js` (covers `/api/chat|healthz|portfolio|waitlist|ibkr/*|brief/:token`), driven by `NEXT_PUBLIC_API_URL`. The old `vercel.json` rewrites (2 routes, `CHANGE-ME`) are removed to avoid a stale/partial override.
- **W6.4 (Business sender + templates)** replaces the Sandbox `From` + `join` opt-in with a registered sender + approved template (which carries the W6.3 permalink). Meta-gated; start verification now, slot it in when approved — no redeploy of this topology needed, just Twilio config + the template SID env.
