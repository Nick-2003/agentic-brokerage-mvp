# Repo A backend + Repo B frontend connection runbook

## Objective

Operate one product from two Git repositories without replacing the existing
provider projects:

| Responsibility | Repository | Deployment/project |
| --- | --- | --- |
| FastAPI API, authorization, brokerage integrations, cron, migrations | Repo A: `Nick-2003/agentic-brokerage-mvp` | Existing Railway project and Supabase project |
| Next.js UI, browser authentication lifecycle, typed analytics | Repo B: `geraldtam11/tendies-demo` | Existing Vercel project and PostHog project |
| Canonical public frontend | Repo B deployment | `https://www.tendiestrade.com` |

Repo A remains the backend authority. Repo B becomes the only actively developed
frontend. Supabase, PostHog, and SnapTrade remain shared external projects rather
than being duplicated.

## Domain clarification

The public frontend domain belongs to a **Vercel project**, not to a Git repository
or the Railway backend. Connecting that Vercel project to Repo B changes which Git
source builds the frontend; it does not move the Railway service or Supabase data.

As observed on 2026-08-06:

- `www.tendiestrade.com` resolves over HTTPS;
- it returns HTTP 200 from Vercel;
- `www.tendiestrade.com` resolves through `tendiestrade.com`;
- the current response carries the application's security headers.

This confirms DNS and TLS are already substantially configured. It does **not** by
itself prove which Vercel project or Git repository currently owns the deployment;
confirm that in Vercel before changing the Git source.

Use `https://www.tendiestrade.com` as the canonical origin. Add the apex
`tendiestrade.com` to the same Vercel project and configure it to redirect to
`https://www.tendiestrade.com`. Vercel treats apex and `www` as separate project
domains, so verify that both appear under Project Settings -> Domains.

## Architecture

```text
User
  |
  v
https://www.tendiestrade.com
  |
  | Vercel project built from Repo B
  | same-origin /api/* rewrites
  v
Existing Railway public backend built from Repo A
  |             |              |
  v             v              v
Supabase     SnapTrade       other backend providers

Repo A Railway cron -> Supabase / messaging providers
```

The browser must not receive SnapTrade application credentials, per-user provider
secrets, Supabase service-role credentials, or encryption keys.

## Sources of truth

Use these ownership rules to prevent design and behavior drift:

| Concern | Authority |
| --- | --- |
| API routes, request/response schemas, error codes | Repo A |
| Authentication and authorization semantics | Repo A + Supabase migrations/RLS |
| Browser session integration | Repo B, conforming to Repo A auth contract |
| Product terminology and state names | Repo A contract document |
| Visual tokens, components, layout, responsive behavior | Repo B design system |
| Typed analytics event names and allowed properties | Shared tracking plan; browser implementation in Repo B |
| Provider secrets and callbacks | Repo A/Railway and provider dashboards |
| Database schema and migrations | Repo A only |

Do not keep two independently edited copies of a design or API decision. Record one
canonical ADR and link the other repository to its exact commit.

## Phase 0 - Record and freeze

Before changing any provider configuration:

1. Record the existing Vercel project ID/team, production branch, Git source,
   domains, Node version, root directory, and environment-variable **names**.
2. Record the Railway project/environment and both service IDs: backend web and
   cron. Do not export sealed values into either repository.
3. Record the Supabase project reference, Site URL, redirect allowlist, signing
   method, migration head, and enabled auth providers.
4. Record the PostHog project/region and the approved typed-event property list.
5. Record the SnapTrade application and its allowed redirect URL.
6. Capture a healthy production baseline: frontend `/`, `/connect`, callback route,
   Railway `/healthz`, authenticated portfolio, brokerage state, and Gate 5 evidence.
7. Freeze breaking Repo A API changes until Repo B passes contract tests.

Store identifiers and variable names in the operator record. Keep actual credentials
only in their provider secret stores.

### Phase 0 Supabase record

Record the following as an evidence-backed snapshot, not as values inferred only
from repository configuration. Export or screenshot the relevant Supabase dashboard
pages and date the record.

**Redirect allowlist.** In Authentication -> URL Configuration, copy the Site URL
and every allowed redirect URL exactly, including scheme, host, port, path, and
wildcards. Classify each entry as production, preview, local development, legacy
overlap, or unknown. The intended production entries are:

```text
Site URL: https://www.tendiestrade.com
Allowed redirect: https://www.tendiestrade.com/**
```

`http://localhost:3000/**` may remain only when local sign-in is supported. Keep an
old frontend origin only for a named owner and an expiry covering already-issued
magic links and rollback. Do not add a broad `https://*.vercel.app/**` production
wildcard: add an exact preview origin temporarily when a preview must test auth.
Repo B must pass an explicit `redirectTo` URL that is present on this list; the
allowlist is not a CORS policy and Railway CORS must be recorded separately.

**Signing method.** Record the active JWT signing algorithm and key identifier
(`kid`), whether the key is current/standby/previously used, and the last rotation
date. Production gate evidence observed ES256 access JWTs and Repo A verifies
asymmetric Supabase tokens from the project's JWKS endpoint. Repo A also retains an
HS256 compatibility path, but that fallback is not evidence that the project still
uses the legacy shared secret. Confirm the dashboard reports asymmetric signing and
that a fresh token's header names an allowed asymmetric algorithm and a published
JWKS `kid`; never paste a JWT, private key, or shared secret into the record.

**Migration head.** Record both (a) the newest migration committed in Repo A and
(b) the newest version present in the linked Supabase project's migration history.
They must match before cutover. At this runbook revision the expected Repo A head is:

```text
20260806071948_sanitize_broker_account_display_names.sql
```

Verify the linked project with `supabase migration list --linked`; a filename in Git
alone does not prove it was applied. If local and remote heads differ, stop and
reconcile them in Repo A. Repo B must never create or apply database migrations.

**Enabled auth providers.** Copy the complete enabled-provider list from
Authentication -> Providers, including Email settings (magic link/OTP, confirmation
policy, and mail delivery configuration), OAuth providers, phone, anonymous sign-in,
and SSO. The repository's implemented and verified browser flow is Supabase Email
magic-link authentication. Treat any additional enabled provider as unverified until
its callback URLs, secrets, and Repo B UI are deliberately tested; disable an
unknown provider rather than silently carrying it into cutover. Record provider
status and non-secret client/application identifiers only.

### Phase 0 approved typed analytics properties

The supplied PostHog export contains the following application event/property pairs.
This is the approved list evidenced by the table; absence of a property means the
event must be sent with no application-defined properties:

| Typed event | Approved application-defined properties |
| --- | --- |
| `briefing_opt_in_changed` | `opt_in` |
| `broker_account_selected` | `provider` |
| `broker_connection_completed` | `provider`, `account_count` |
| `broker_connection_failed` | `provider`, `reason_code` |
| `broker_connection_started` | `provider` |
| `chat_error` | `error_type` |
| `chat_session_started` | none |
| `connect_started` | none |
| `prompt_submitted` | `intent_classification`, `text_hash` |
| `widget_generated` | `widget_type`, `latency_ms` |

PostHog-generated fields such as `distinct_id`, `token`, `$current_url`, browser,
device, geo, session, and library fields are transport/automatic metadata, not
approved typed-event properties. `$pageview`, `$pageleave`, and `$identify` are also
PostHog lifecycle events, not application typed events. This extracted list does not
authorize other properties present in code but absent from the supplied table; add
those only through a reviewed tracking-plan change with privacy tests. In particular,
never add email, JWTs, magic-link parameters, prompts, brokerage labels, holdings,
balances, or local/provider connection or account identifiers.

### Phase 0 breaking-change freeze

From the Phase 0 baseline until Repo B's required contract suite is green, Repo A's
public API is frozen against removals or incompatible changes. This covers routes and
methods, authentication requirements, request fields and types, response fields and
types, enum values, status codes, stable error codes, SSE event names/shapes, and
callback semantics. Database-only and internal refactors are allowed only when the
published behavior remains identical.

Enforce the freeze as follows:

1. Commit a canonical Repo A OpenAPI/contract artifact and contract version. Pin its
   exact Repo A commit in Repo B and generate or check Repo B's client/types from it.
2. In Repo A CI, fail on an OpenAPI compatibility diff that removes or incompatibly
   changes existing operations, schemas, or responses. Require an explicit
   `breaking-api` approval for an exceptional change; the label must not bypass the
   Repo B test gate.
3. In Repo B CI, run typecheck/build plus contract tests against the pinned artifact
   and a deployed candidate of Repo A. Cover bearer-token attachment, fixed brokerage
   routes, error mappings, SSE parsing, and the SnapTrade callback/verification flow.
4. Protect the production branches in both repositories so these checks and a named
   cross-repository reviewer are required. Record the matching Repo A commit, Repo B
   commit, contract version, deployments, migration head, evidence, and rollback pair.
5. For a necessary breaking change, first add backward-compatible Repo A behavior;
   update and deploy Repo B while both versions work; verify production; then remove
   the old behavior in a later Repo A release. Do not merge or deploy the removal in
   the same release that introduces its replacement.

The freeze is released only for the recorded contract version after Repo B's tests
pass against the exact Repo A candidate and the compatible deployment pair is saved.
Any later breaking proposal starts the same protocol again.

#### Frozen-boundary declaration - 2026-08-07

The Repo A/Repo B separation boundary is declared frozen at Git commit:

```text
a1d3846ec2299369263a45b2671ed17ac580cfc1
```

At declaration time, all four refs below resolve to that exact commit:

```text
ntam-whole_app_repo_07082026
origin/ntam-whole_app_repo_07082026
ntam-tendies_backend
origin/ntam-tendies_backend
```

This commit is the common baseline. From this point, the branch names have different
meanings: `ntam-whole_app_repo_07082026` preserves the baseline unchanged, while
`ntam-tendies_backend` may advance only under the compatibility rules below.

The frozen boundary consists of the following externally observable contracts:

| Boundary | Frozen baseline |
| --- | --- |
| HTTP API | Paths, methods, authentication, request/response schemas, status codes, and stable error codes exposed by Repo A |
| Streaming | SSE framing, event names, ordering guarantees, and payload shapes consumed by the frontend |
| Widgets | `backend/prompts/widget_contract.md` and its frontend TypeScript representation |
| Authentication | Supabase bearer-token behavior, trusted `sub` identity, ES256/JWKS verification, and legacy HS256 compatibility behavior |
| Brokerage | Fixed connection, verification, account-listing, account-selection, portfolio, and callback semantics |
| Analytics | Approved typed event/property pairs and identity/reset/privacy rules |
| Database | Repo A ownership of migrations and migration head `20260806071948_sanitize_broker_account_display_names.sql` |
| Operations | Railway health behavior, frontend-to-backend routing, canonical callback paths, and identifier-free access-log paths |

`API_CONTRACT.md` v1.1, the FastAPI behavior at the baseline SHA, the widget
contract, migration files, and the applied regression tests collectively describe
the boundary. Where prose is stale or incomplete, the baseline's tested wire
behavior is controlling until the documents and generated OpenAPI artifact are
reconciled without changing that behavior.

The following changes are prohibited on `ntam-tendies_backend` until Repo B passes
the contract gate against the exact candidate commit:

- deleting or renaming an endpoint, SSE event, field, enum value, or stable error;
- making an optional request field required or narrowing an accepted type;
- removing a response field or changing its type or meaning;
- changing authentication, authorization, RLS, callback, or account-selection
  semantics in a way an existing client cannot tolerate;
- applying destructive or Repo B-owned database migrations; and
- sending new analytics properties without tracking-plan and privacy approval.

Internal refactors, defect fixes that restore the frozen behavior, and additive
optional capabilities are permitted only when Repo A regression checks and Repo B
contract tests pass. A necessary incompatibility must be implemented first as a
backward-compatible expansion and the old behavior may be removed only in a later
release after Repo B production verification.

The declaration is operationally effective when the baseline SHA is retained by an
immutable tag, both branches have the protections described below, and Railway does
not auto-promote an unpaired `ntam-tendies_backend` commit. Documentation alone does
not enforce the freeze.

#### Branch roles during the Repo A/Repo B transition

The two Repo A branches have deliberately different roles. At the start of this
transition both pointed to commit `a1d3846`; record that SHA (or the actual SHA at
the moment the freeze is activated) in the operator record rather than relying on a
moving branch name.

| Branch | Role | Allowed changes |
| --- | --- | --- |
| `ntam-whole_app_repo_07082026` | Frozen reference for the last verified version in which backend and frontend lived in one repository | None during the transition. Use for comparison and rollback evidence only. |
| `ntam-tendies_backend` | Repo A integration branch used to connect and stabilize Repo B | Backward-compatible backend fixes, contract-test infrastructure, migrations owned by Repo A, and temporary compatibility adapters required by Repo B |

Protect `ntam-whole_app_repo_07082026` as an immutable reference: block direct
pushes, pull-request merges, force pushes, deletion, and administrator bypass where
the hosting platform permits it. Also create an immutable annotated tag such as
`whole-app-baseline-2026-08-07` at the recorded SHA. A branch is only a convenient
name; the tag and SHA are the durable baseline. Do not deploy new work from this
reference branch and do not periodically merge the backend branch back into it.

Protect `ntam-tendies_backend` as the only Repo A transition branch. Require pull
requests, current required checks, a Repo A reviewer, and the Repo B integration
owner for contract-surface changes. Block direct and force pushes. Configure the
Railway candidate/staging service from this branch; production may use it only as a
recorded Repo A/Repo B commit pair after all release gates pass.

Changes may flow in one direction only during the transition:

```text
ntam-whole_app_repo_07082026 at frozen SHA
                 |
                 | initial ancestry/reference only; no continuing merges
                 v
ntam-tendies_backend -> Repo B contract tests -> recorded deployment pair
```

Do not merge Repo B frontend work into `ntam-whole_app_repo_07082026`. Do not merge
`ntam-tendies_backend` back into the reference branch. If a critical production fix
must also be represented in the old whole-app topology, apply it through a separately
named, reviewed hotfix branch from the frozen SHA and record the exception; leave the
original reference branch and tag untouched.

On every pull request to `ntam-tendies_backend`, compare the candidate against the
frozen SHA and classify the diff:

1. `internal` - no observable API or database-contract change;
2. `additive-compatible` - new optional field, route, or behavior that old clients
   can ignore;
3. `breaking` - removal, rename, new required input, narrowed type/enum, changed
   auth/status/error/SSE/callback behavior, or destructive migration.

Categories 1 and 2 may merge only after Repo A checks and Repo B contract tests pass.
Category 3 remains blocked; use the expand-migrate-contract sequence instead. A pull
request label or administrator approval may classify a change but must not bypass the
tests.

Keep `ntam-tendies_backend` until Repo B has passed all contract, authentication,
brokerage, callback, privacy, isolation, and rollback gates and a production pair has
been stable for the chosen observation window. At that point:

1. record the final transition-branch SHA and matching Repo B SHA;
2. merge `ntam-tendies_backend` into Repo A's chosen long-lived default branch by a
   reviewed pull request (or designate it as the long-lived branch explicitly);
3. repoint Railway only after the same commit is verified;
4. retain the frozen reference branch and tag for the documented rollback/audit
   period; and
5. delete the temporary integration branch only after branch references, Railway,
   runbooks, and rollback records no longer depend on it.

Branch convergence does not release the API freeze. Future breaking Repo A changes
must still follow the cross-repository release protocol because Repo B remains an
independent consumer.

## Phase 1 - Prepare Repo B

Repo B must implement or port the following integration surfaces from Repo A:

1. Supabase browser client using only the project URL and anon/publishable key.
2. Root-mounted Supabase session listener with PostHog identify/reset behavior.
3. PostHog initialization with DOM autocapture and session replay disabled.
4. One typed API client that attaches the Supabase bearer token.
5. The SnapTrade callback route:
   `/settings/brokerage/snaptrade/callback`.
6. Brokerage connection/account selection UI using Repo A's current fixed routes.
7. Public brief route `/b/[token]` if existing brief links must continue working.
8. All required `/api/*` rewrites to the Railway backend.
9. Existing CSP/security-header allowances for Supabase and PostHog.
10. Loading, unauthenticated, empty, success, provider-sync, and stable-error states.

Repo B should not copy backend domain logic into components. Convert Repo A's
OpenAPI schema or contract document into checked frontend types and fail CI when the
frontend targets an unsupported contract version.

### Required Vercel variables

Keep these in the existing Vercel project and apply them to the appropriate
Production/Preview environments:

```text
NEXT_PUBLIC_API_URL=https://<existing-railway-backend-domain>
NEXT_PUBLIC_SUPABASE_URL=https://<existing-project>.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=<existing-anon-or-publishable-key>
NEXT_PUBLIC_POSTHOG_API_KEY=<existing-project-key>
NEXT_PUBLIC_POSTHOG_HOST=https://<existing-region-ingestion-host>
NEXT_PUBLIC_SITE_URL=https://www.tendiestrade.com
```

`NEXT_PUBLIC_API_URL` must include `https://`, have no trailing `/api`, and point to
Repo A's existing Railway web service. Never add `SUPABASE_SERVICE_KEY`, SnapTrade
credentials, broker encryption keys, or provider API secrets to Vercel.

## Phase 2 - Preview without production mutation

1. Connect a Vercel preview or temporary Vercel project to Repo B first.
2. Point Preview variables to a staging Railway environment and staging/test users.
3. Add the exact preview callback origins to Supabase and SnapTrade only when the
   preview must exercise those flows.
4. Run frontend typecheck/build and Repo A's API/RLS/isolation suites.
5. Exercise two users in separate profiles and then sequentially in one profile.
6. Confirm PostHog receives only identified typed events and sanitized pageviews.
7. Confirm Railway paths contain no account UUIDs or credentials.

Do not point arbitrary Vercel preview branches at production brokerage accounts.

## Phase 3 - Connect the existing Vercel project to Repo B

Prefer reusing the existing production Vercel project because it retains the
configured domain, environment variables, project settings, and deployment history.

1. Produce and retain a known-good production deployment from Repo A for rollback.
2. In Vercel, confirm `www.tendiestrade.com` and `tendiestrade.com` are assigned to
   this project.
3. Disconnect the Vercel project's Repo A Git source.
4. Connect `geraldtam11/tendies-demo`.
5. Set Repo B's production branch.
6. Set Root Directory to the directory containing Repo B's frontend `package.json`.
7. Preserve the existing Vercel environment variables and Node/framework settings.
8. Deploy Repo B as a preview and repeat the production-readiness tests.
9. Promote the verified Repo B deployment to production.
10. Confirm both the canonical host and apex redirect before changing other
    providers.

Changing Vercel's Git source must not change the Railway service source. Railway web
and cron remain connected to Repo A.

## Phase 4 - Make `www.tendiestrade.com` canonical

### Vercel and DNS

1. Under the production Vercel project, add/verify both:
   - `www.tendiestrade.com` as the production domain;
   - `tendiestrade.com` redirected permanently to `www.tendiestrade.com`.
2. Follow the DNS records Vercel displays for this specific project. Do not guess a
   CNAME target or delete unrelated MX/TXT records.
3. Wait for Vercel to report valid configuration and an issued certificate.
4. Verify from more than one network/region:

```bash
dig +short tendiestrade.com
dig +short www.tendiestrade.com
curl -sSIL https://tendiestrade.com/
curl -sSIL https://www.tendiestrade.com/
curl -sS https://www.tendiestrade.com/api/healthz
```

Pass requires the apex to redirect to `www`, the canonical origin to return the Repo
B application, and `/api/healthz` to reach Repo A through the rewrite.

### Supabase

In Authentication -> URL Configuration:

```text
Site URL: https://www.tendiestrade.com
Production redirect URLs:
https://www.tendiestrade.com/**
```

Keep `http://localhost:3000/**` only if local authentication remains supported. Use
exact production paths where practical. Request fresh magic links after this change;
already-issued links retain their minted redirect behavior.

### Railway

Set on the web service:

```text
PUBLIC_BASE_URL=https://www.tendiestrade.com
CORS_ALLOW_ORIGINS=https://www.tendiestrade.com,<temporary-old-origin-if-needed>
```

Set `PUBLIC_BASE_URL=https://www.tendiestrade.com` on the cron service too so new
brief permalinks use the canonical frontend. Keep `PUBLIC_BACKEND_URL` pointing to
Railway; Twilio inbound/status callbacks remain Railway URLs.

### SnapTrade

Set in Railway and in the same existing SnapTrade application:

```text
SNAPTRADE_REDIRECT_URL=https://www.tendiestrade.com/settings/brokerage/snaptrade/callback
```

Update both sides before creating a new portal session. Verify registration, portal
completion, callback verification, account import, selection, and reload. Do not
delete existing SnapTrade users or encrypted local identities as part of a domain
change.

### PostHog

Retain the existing PostHog project/key and ingestion host. Verify new events use
`www.tendiestrade.com`, retain Supabase UUID identity, reset between users, and have
no `$autocapture`, session replay, email, broker label, IDs, holdings, or balances.

PostHog does not determine application routing, so no separate PostHog project is
needed for the domain change.

### Other URL-dependent integrations

Review and update:

- email templates and confirmation/reset links;
- `PUBLIC_BASE_URL` brief links;
- Resend sender/domain configuration if the sending domain changes;
- CSP `connect-src`, `img-src`, and other origin allowlists;
- OAuth provider callback/JavaScript-origin allowlists, if enabled;
- monitoring checks, bookmarks, documentation, and support links.

Twilio webhook URLs should continue using the Railway backend domain. They should
not be changed to `www.tendiestrade.com` merely because the frontend domain changed.

## Old Repo A frontend domain

First identify what “old domain” means:

1. **Old Vercel custom domain:** remove or redirect it only after the overlap period.
2. **Vercel-generated `*.vercel.app` project URL:** it is a deployment/project alias,
   not Repo A's backend. It may remain available even after Repo B becomes the Git
   source. Treat it as a technical alias and enforce canonical redirects if needed.
3. **Railway `*.up.railway.app` domain:** retain it. This is Repo A's API and webhook
   origin, not the old frontend.

Do not remove the old frontend origin on cutover day. It may still appear in:

- already-issued Supabase magic links;
- active browser sessions and cached tabs;
- previously sent brief permalinks;
- bookmarks and operator runbooks;
- a rollback deployment.

### Retirement sequence

1. Stop generating new links to the old origin.
2. Redirect old frontend requests to the equivalent path on
   `https://www.tendiestrade.com` where Vercel permits it.
3. Keep the old origin in Supabase's redirect allowlist and Railway CORS only for a
   documented overlap window.
4. Wait at least through the longest live auth-link lifetime, brief permalink TTL,
   and the chosen rollback window. The current brief TTL is seven days; use a longer
   window if operational policy requires it.
5. Confirm traffic and logs show no legitimate old-origin use.
6. Remove the old origin from Supabase redirects, Railway CORS, provider allowlists,
   monitoring, and documentation.
7. Remove a removable custom domain from the old Vercel assignment. Do not delete
   the Vercel project until rollback is no longer required.

If the existing Vercel project is simply reconnected to Repo B, the old
`*.vercel.app` alias does not imply that Repo A still serves a frontend. Repository
ownership and domain aliases are separate concepts.

## Cross-repository release protocol

For a breaking API change:

1. Repo A documents and tests a backward-compatible transition.
2. Repo B updates its generated/checked client and supports both versions if needed.
3. Deploy Repo A first while the old frontend remains compatible.
4. Deploy Repo B and verify production.
5. Remove the old Repo A behavior only in a later release.

For a frontend-only design change, Repo B can deploy independently provided API,
analytics, authentication, accessibility, and security contracts remain unchanged.

Each cross-repository release record should contain:

```text
Repo A commit
Repo B commit
API contract version
Vercel deployment ID
Railway deployment ID
Supabase migration head
domain/callback configuration version
verification evidence
rollback pair
```

## Production acceptance checklist

- [ ] `https://tendiestrade.com` redirects to `https://www.tendiestrade.com`.
- [ ] `https://www.tendiestrade.com` renders Repo B's production commit.
- [ ] `/api/healthz` and authenticated API requests reach Repo A on Railway.
- [ ] Railway web and cron still deploy from Repo A.
- [ ] Supabase magic-link sign-in, refresh, sign-out, and user switch pass.
- [ ] RLS and two-user brokerage isolation pass.
- [ ] SnapTrade portal and canonical callback complete exactly once.
- [ ] Selected brokerage account persists after reload.
- [ ] PostHog identity/reset and Gate 5 privacy inspection pass.
- [ ] New brief links use `www.tendiestrade.com` and existing links remain handled.
- [ ] Twilio webhooks/status callbacks still reach Railway and validate signatures.
- [ ] No service-role key, provider secret, JWT, or encryption key entered Repo B.
- [ ] A tested Repo A/Repo B deployment pair and domain rollback are recorded.

## Rollback

Rollback must restore a compatible pair, not just one repository:

1. Promote the retained known-good Vercel deployment.
2. If necessary, reconnect the previous Vercel Git source only after the known-good
   deployment is serving.
3. Keep or restore `www.tendiestrade.com` on the working Vercel project.
4. Restore the matching Railway deployment/config only if its contract also changed.
5. Restore Supabase/SnapTrade redirect allowlists for the active frontend origin.
6. Do not roll back database migrations destructively unless a migration-specific
   recovery plan explicitly requires it.

## References

- Vercel custom domains:
  `https://vercel.com/kb/guide/how-do-i-add-a-custom-domain-to-my-vercel-project`
- Vercel domain ownership and project assignment:
  `https://vercel.com/docs/domains/working-with-domains`
- Vercel Git deployments:
  `https://vercel.com/docs/git`
- Supabase redirect URLs:
  `https://supabase.com/docs/guides/auth/redirect-urls`
- Railway services:
  `https://docs.railway.com/services`
- Railway variables:
  `https://docs.railway.com/variables`
