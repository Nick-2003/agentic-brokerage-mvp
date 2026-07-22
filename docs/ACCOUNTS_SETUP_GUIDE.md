# Accounts & API Keys — Setup Guide

**Read this in a new Claude session (or just follow it yourself).** It's self-contained — you don't need any context from the main build session. Just walk through the 8 services below in order. When you're done, paste the resulting keys back into the build session.

Total time: ~25 min if you don't get distracted by signup forms.

---

## What you're getting at the end

A `.env` block ready to paste back, like this:

```
# Backend
ANTHROPIC_API_KEY=sk-ant-...
ALPACA_API_KEY=PK...
ALPACA_API_SECRET=...
ALPACA_BASE_URL=https://paper-api.alpaca.markets
SUPABASE_URL=https://xxxxxxxxxxxx.supabase.co
SUPABASE_ANON_KEY=eyJ...
SUPABASE_SERVICE_KEY=eyJ...
POSTHOG_API_KEY=phc_...
POSTHOG_HOST=https://us.i.posthog.com

# Frontend (NEXT_PUBLIC_ prefix means it's exposed to browser — that's fine for these)
NEXT_PUBLIC_SUPABASE_URL=https://xxxxxxxxxxxx.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=eyJ...
NEXT_PUBLIC_POSTHOG_API_KEY=phc_...
```

Keep the SECRET-named ones (`ALPACA_API_SECRET`, `SUPABASE_SERVICE_KEY`) **out** of the frontend block. They never leave the backend.

---

## ⚠ Security ground rules (read before you start)

1. **Never commit a .env file.** The repo already has `.gitignore` excluding it. Verify by running `git check-ignore .env` after pasting.
2. **The Anthropic key starts with `sk-ant-`. Treat it like a credit card number.** Anyone with it can rack up your Anthropic bill.
3. **Anthropic spend cap is mandatory.** When you create the API key, also go to Plans & Billing → Spend Limits and set a monthly cap (suggest **$100** for this MVP — Claude calls for 5–10 users typically run $30–40/mo).
4. **Use a strong password + 2FA on every account below.** These are real services with real (paper) trading capability.

---

## 1 · LLM rail API key (pick one)

The brain. The agent runs on **one** LLM rail, chosen by `LLM_RAIL` (default `anthropic`): **Anthropic** (`ANTHROPIC_API_KEY`), **OpenAI** (`OPENAI_API_KEY`), or **DeepSeek** (`DEEPSEEK_API_KEY`). An Anthropic usage-limit auto-fails-over to DeepSeek when `LLM_FALLBACK_ENABLED=1`. Every rail is mock-first, so the app also boots keyless in demo mode. **Live today the product runs on DeepSeek** (Anthropic + OpenAI credits exhausted) — so if you only get one key, get **DeepSeek** (`platform.deepseek.com` → API keys) and set `LLM_RAIL=deepseek`.

The Anthropic steps below still apply if you choose the Anthropic rail; the OpenAI/DeepSeek keys are created the same way at each provider's console (see `backend/.env.example`).

**Steps (Anthropic rail):**

1. Go to **<https://console.anthropic.com>**
2. Sign in (or sign up — they'll need a phone number for verification)
3. Top-right → **Settings** → **API Keys**
4. Click **Create Key**
5. Name it `agentic-brokerage-mvp-dev`
6. Copy the key — it starts with `sk-ant-` — and paste it into a notes app temporarily
   - ⚠ You can't see this key again after you close the modal. Lose it = generate a new one.

**Then set the spend ceiling:**
7. Left sidebar → **Plans & Billing** → **Spend Limits**
8. Set **Monthly Limit** to **$100**
9. Set **Alert Threshold** to **$50** (you'll get an email when you hit this)

You should now have one value:

```
ANTHROPIC_API_KEY=sk-ant-api03-xxxx...xxxx
```

---

## 2 · Alpaca paper trading account

Real broker API. $100k of fake money. No real money risk.

**Steps:**

1. Go to **<https://alpaca.markets>**
2. Click **Sign Up** → use your email
3. Fill in the signup form (you'll need to provide a name, country, but **NOT** SSN or bank account — that's only for live trading)
4. Verify your email
5. After login, you'll see two tabs at the top: **Paper Trading** and **Live Trading**. Make sure you're on **Paper Trading**.
6. Left sidebar → **API Keys** (under "Your Account")
7. Click **Generate New Key**
8. You'll get two values: **API Key ID** (starts with `PK`) and **Secret Key**. Copy both immediately.
   - ⚠ Same as Anthropic — you can't see the secret again. Save it now or regenerate later.

You should now have:

```
ALPACA_API_KEY=PKxxxxxxxxxxxxx
ALPACA_API_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
ALPACA_BASE_URL=https://paper-api.alpaca.markets
```

(The `ALPACA_BASE_URL` value is always the same for paper trading — just copy that exact string.)

**Optional — fund your paper account with positions:**
Go to **Account** → **Reset Paper Account** to refresh the $100k balance any time you want a clean slate for testing.

---

## 3 · Supabase project (auth + database)

This handles user signup (magic links) and stores your data.

**Steps:**

1. Go to **<https://supabase.com>**
2. Sign in with GitHub (recommended — it's how Railway/Vercel auth works too)
3. **New Project**
   - Name: `agentic-brokerage-mvp`
   - Database Password: generate a strong one and save it in your password manager (you won't usually need it, but you might for Postgres direct access)
   - Region: pick closest to you (Singapore for HK, or Tokyo)
   - Plan: **Free** (the $0 tier is enough for 5–10 users)
4. Wait ~2 minutes for the project to provision
5. Once ready, left sidebar → **Settings** → **API**
6. Copy three values:
   - **Project URL** → save as `SUPABASE_URL`
   - **anon / public** key → save as `SUPABASE_ANON_KEY`
   - **service_role / secret** key → save as `SUPABASE_SERVICE_KEY` ⚠ this one is highly sensitive, never put it in frontend code

You should now have:

```
SUPABASE_URL=https://xxxxxxxxxxxx.supabase.co
SUPABASE_ANON_KEY=eyJhbGciOiJ...                  # safe in frontend
SUPABASE_SERVICE_KEY=eyJhbGciOiJ...               # backend ONLY
```

**Enable email auth (the magic link flow):**
7. Left sidebar → **Authentication** → **Providers**
8. Confirm **Email** is enabled (it is by default)
9. **Authentication** → **Email Templates** → Magic Link → confirm the template includes the URL. (No changes needed; just verify it exists.)

---

## 4 · TradingView (for the talk-to-charts wedge)

We need this for Phase 4 — talking to your charts.

**Steps:**

1. Go to **<https://www.tradingview.com/>**
2. Sign Up → free plan is enough for our MVP indicators (SMA, S/R)
3. Download **TradingView Desktop**: <https://www.tradingview.com/desktop/>
4. Install it and sign in with your account
5. Open it once to verify it works — load any chart (e.g., NVDA)
6. Leave it installed; we don't need an API key from TradingView itself — the MCP integration uses your logged-in desktop session.

You don't need to paste anything back for this one yet. We'll wire it up in Phase 4 (build session).

---

## 5 · GitHub repository

For source control + auto-deploy to Railway and Vercel.

**Steps:**

1. Go to **<https://github.com/new>**
2. Repository name: `agentic-brokerage-mvp`
3. **Private** repo
4. **Do NOT** add a README, .gitignore, or license — we already have these
5. Create
6. You'll see a quick setup page. Note the SSH or HTTPS URL — but no need to push yet, the build session will handle the first commit.

Nothing to paste back. Just note the URL: `git@github.com:YOUR_USERNAME/agentic-brokerage-mvp.git`

---

## 6 · Railway account (backend hosting)

Hosts the Python FastAPI backend.

**Steps:**

1. Go to **<https://railway.app>**
2. Sign in with **GitHub** (same account as step 5)
3. You'll see a $5/month free credit on the dashboard — that's enough for a small backend
4. We'll deploy in Phase 9 (build session). For now just confirm you can log in.

Nothing to paste back.

---

## 7 · Vercel account (frontend hosting)

Hosts the Next.js frontend.

**Steps:**

1. Go to **<https://vercel.com>**
2. Sign in with **GitHub** (same account)
3. Free hobby tier is plenty
4. Confirm you can log in.

Nothing to paste back.

---

## 8 · PostHog (analytics) — probably reusing your existing project

You already have a PostHog project (PWA Prod, project ID 148926).

**Option A — reuse existing project:**

1. Log in to <https://app.posthog.com>
2. Switch to **PWA Prod** project
3. Project Settings → **Project API Key** → copy it. It starts with `phc_`.
4. Note the Project Host: usually `https://us.i.posthog.com`

```
POSTHOG_API_KEY=phc_xxxxxxxxxxxx
POSTHOG_HOST=https://us.i.posthog.com
```

**Option B — create a new project (cleaner separation):**

1. Top-left project dropdown → **New project**
2. Name: `agentic-brokerage-mvp`
3. Project Settings → Project API Key → copy

Either works. Cleaner separation = easier dashboards later, but you'll need to set up dashboards from scratch. Reusing means metrics mix with your existing analytics — fine if you filter by `project=agentic-brokerage`.

---

## Final paste-back format

When you're done, paste this **complete block** back into the build session:

```
# === COPY EVERYTHING BELOW THIS LINE AND PASTE IT BACK ===
ANTHROPIC_API_KEY=sk-ant-...
ALPACA_API_KEY=PK...
ALPACA_API_SECRET=...
ALPACA_BASE_URL=https://paper-api.alpaca.markets
SUPABASE_URL=https://xxxxxxxxxxxx.supabase.co
SUPABASE_ANON_KEY=eyJ...
SUPABASE_SERVICE_KEY=eyJ...
POSTHOG_API_KEY=phc_...
POSTHOG_HOST=https://us.i.posthog.com
GITHUB_REPO_URL=git@github.com:YOUR_USERNAME/agentic-brokerage-mvp.git
# === END ===
```

The build session will:

1. Validate that each key looks right (no leading whitespace, correct prefixes)
2. Write them to `~/Code/agentic-brokerage-mvp/backend/.env`
3. Verify `.env` is gitignored
4. Initialise the backend with real credentials
5. Run a smoke test (one real LLM call + one real Alpaca portfolio fetch)

⚠ **Don't paste your keys into a public channel.** Send them inside Claude Code (same session) — that's the safest path.

---

## Troubleshooting

**Alpaca says "account suspended" / KYC requested:**

- Some regions require additional verification. Pick a different country in signup if you're getting blocked (paper trading should be available globally — but try US if HK is restricted).

**Supabase: "Project provisioning takes more than 5 minutes":**

- Refresh the page. Usually it's just the UI not updating.

**Anthropic console says "card required":**

- Yes — Anthropic API requires a credit card. The first $5 of usage is free; with the $100 spend cap above you're not at risk of a runaway bill.

**TradingView Desktop won't install on Mac (Gatekeeper warning):**

- Right-click the .dmg → Open → Open Anyway in the warning dialog. macOS sometimes flags non-App-Store downloads.

**"I want to use my real account, not paper":**

- No. The MVP doesn't ship real-money execution. See SCOPE.md non-goals. Paper only.

---

## Done?

Paste the keys back into the build session. The next message you send will trigger Phase 1 (backend skeleton with real credentials) and a smoke test.
