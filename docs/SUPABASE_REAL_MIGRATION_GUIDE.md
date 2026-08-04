# Creating and Applying a Real Supabase Migration

**Project:** `agentic-brokerage-mvp`  
**Purpose:** Convert reviewed SQL proposals into timestamped Supabase CLI migrations, verify them locally, and deploy them without resetting the existing remote database.  
**Last updated:** 2026-08-03

## 1. Local, linked, and production are different targets

The Supabase CLI operates against three conceptually different environments:

| Target | Purpose | Typical flag |
| --- | --- | --- |
| Local Docker stack | Migration development and destructive replay testing | `--local` |
| Linked cloud project | Existing app database and migration history | `--linked` |
| Optional staging project | Remote deployment rehearsal before production | `--linked` after re-linking |

`supabase init` does not create a cloud project and does not modify the existing
database. It creates the local `supabase/config.toml` and directory structure.

For this app, use the existing Supabase project as the schema baseline. Use a new
cloud project only as an optional staging environment.

## 2. Prerequisites

- Supabase CLI installed.
- Docker Desktop installed and running.
- Access to the existing Supabase project.
- The database password or authenticated CLI access when requested.
- Reviewed SQL in a numbered `.proposed_changes/` folder.

Check the installed tools from the repository root:

```bash
supabase --version
docker --version
docker info
```

`docker info` must return server information. If the Docker daemon is unavailable
on macOS, open Docker Desktop and wait for it to finish starting:

```bash
open -a Docker
```

## 3. Initialize the repository

Run once from the repository root:

```bash
supabase init
```

This creates:

```text
supabase/
├── config.toml
├── migrations/
└── seed.sql              # optional
```

Commit `config.toml`, migration files, and intentional seed files. Do not commit
CLI state under `supabase/.temp/` or `supabase/.branches/`. The generated
`supabase/.gitignore` excludes those paths.

Do not put API keys, database passwords, service-role keys, or encryption keys in
`config.toml` or migration SQL.

## 4. Link the existing app project

Authenticate and link the existing `agentic-brokerage-mvp` Supabase project:

```bash
supabase login
```

```bash
supabase link --project-ref YOUR_EXISTING_PROJECT_REF
```

The project reference is the identifier in the dashboard URL:

```text
https://supabase.com/dashboard/project/YOUR_EXISTING_PROJECT_REF
```

Linking selects a remote target for commands using `--linked`. It does not apply a
schema change by itself.

## 5. Pull the existing remote schema before adding a migration

This project originally created tables through standalone SQL files and the
Supabase SQL Editor. A fresh local database therefore needs a baseline migration
representing the already-deployed remote schema.

Pull that baseline before creating the new change:

```bash
supabase db pull
```

The CLI creates a timestamped file similar to:

```text
supabase/migrations/20260803094042_remote_schema.sql
```

Inspect it before continuing. It should contain the existing tables, policies,
triggers, and helper functions such as:

```sql
create function public.set_updated_at()
returns trigger
...
```

If the pulled baseline already contains the proposed new tables, do not create a
second migration for them. The remote project already has that change.

## 6. Start the local stack

```bash
supabase start
```

```bash
supabase status
```

The normal local database URL is:

```text
postgresql://postgres:postgres@127.0.0.1:54322/postgres
```

The first start can take several minutes while Docker images download.

## 7. Generate the migration filename with the CLI

Never invent or copy a timestamp from documentation. Run:

```bash
supabase migration new create_broker_connections
```

The CLI prints the exact generated path, for example:

```text
Created new migration at supabase/migrations/20260803095550_create_broker_connections.sql
```

Use the path printed by the current command. A timestamp shown in documentation is
only an example and must not be reused.

## 8. Put the reviewed SQL in the generated file

For proposal 086, the reviewed source is:

```text
.proposed_changes/086-essential-connection-state/
└── backend/db/schema_broker_connections.sql
```

Copy it into the exact empty migration file generated in the previous step:

```bash
cp \
  .proposed_changes/086-essential-connection-state/backend/db/schema_broker_connections.sql \
  supabase/migrations/TIMESTAMP_create_broker_connections.sql
```

Replace `TIMESTAMP_create_broker_connections.sql` with the filename printed by the
CLI. Alternatively, open the generated file and paste the reviewed SQL into it.

Confirm there is only one migration for the change:

```bash
find supabase/migrations -maxdepth 1 -type f -name '*.sql' | sort
```

The baseline must sort before the new migration:

```text
..._remote_schema.sql
..._create_broker_connections.sql
```

## 9. Rebuild the local database from migrations

Run the strongest local reproducibility check:

```bash
supabase db reset --local
```

This destroys only the local database, then applies every file in
`supabase/migrations/` in timestamp order. It does not reset the linked project.

Never add `--linked` to this command for a production project.

## 10. Validate the local result

Run schema linting and advisors:

```bash
supabase db lint --local --schema public --fail-on error
```

```bash
supabase db advisors --local
```

```bash
supabase migration list --local
```

Confirm the broker tables exist:

```bash
psql postgresql://postgres:postgres@127.0.0.1:54322/postgres \
  -c "\dt public.broker_*"
```

Confirm RLS is enabled:

```bash
psql postgresql://postgres:postgres@127.0.0.1:54322/postgres \
  -c "select tablename, rowsecurity from pg_tables where schemaname = 'public' and tablename in ('broker_connections', 'broker_accounts');"
```

Both rows must report `rowsecurity = true`.

Inspect the policies:

```bash
psql postgresql://postgres:postgres@127.0.0.1:54322/postgres \
  -c "select tablename, policyname, cmd, roles from pg_policies where schemaname = 'public' and tablename in ('broker_connections', 'broker_accounts') order by tablename, policyname;"
```

Expected policy operations are SELECT, INSERT, UPDATE, and DELETE for authenticated
users with ownership checks.

## 11. Preview remote deployment

Confirm which cloud project is linked before any push:

```bash
supabase projects list
```

Compare migration history:

```bash
supabase migration list --linked
```

Preview the push:

```bash
supabase db push --dry-run --linked
```

For proposal 086, the dry run should propose only the one correctly generated
`create_broker_connections` migration. Stop if it proposes:

- an example-timestamp migration;
- two broker-connection migrations;
- the remote baseline as a new destructive change;
- unrelated table deletion or recreation.

## 12. Apply and verify remotely

After reviewing the dry run and confirming the linked project:

```bash
supabase db push --linked
```

Then verify:

```bash
supabase migration list --linked
```

```bash
supabase db advisors --linked
```

Do not seed production data unless that is an explicit, separately reviewed task.

## 13. Optional staging project

If the existing project contains active users or production data, first deploy the
same migration set to a separate staging Supabase project:

```bash
supabase link --project-ref YOUR_STAGING_PROJECT_REF
```

```bash
supabase db push --dry-run --linked
```

```bash
supabase db push --linked
```

After staging verification, re-link the existing project and repeat the dry run:

```bash
supabase link --project-ref YOUR_EXISTING_PROJECT_REF
```

One working directory has one linked project at a time. Always confirm the target
again before pushing.

## 14. Troubleshooting

### `ECONNREFUSED 127.0.0.1:54322`

Example:

```text
failed to connect to postgres ... ECONNREFUSED 127.0.0.1:54322
```

Cause: the command targeted local Postgres, but the local stack was not running.

Resolution:

```bash
docker info
supabase start
supabase status
supabase db advisors --local
```

If the intention was to inspect the cloud project instead, first confirm the link
and use:

```bash
supabase db advisors --linked
```

### `function public.set_updated_at() does not exist`

Cause: a feature migration ran before the remote baseline migration that creates
the helper function.

Inspect ordering:

```bash
find supabase/migrations -maxdepth 1 -type f -name '*.sql' | sort
```

The baseline must have an earlier timestamp than the feature migration. Do not fix
this by duplicating the helper in an incorrectly ordered migration when the actual
problem is a duplicate or invented timestamp.

### Duplicate broker migrations

The incident encountered during proposal 086 had this order:

```text
20260801144500_create_broker_connections.sql  # copied example; incorrect
20260803094042_remote_schema.sql               # baseline
20260803095550_create_broker_connections.sql  # CLI-generated; correct
```

The two broker migration files were byte-for-byte identical. The first ran before
the baseline and failed because `set_updated_at()` did not exist yet.

The correction was to move the obsolete duplicate out of the migration directory:

```bash
mkdir -p .proposed_changes/086-essential-connection-state/superseded-migrations
```

```bash
mv \
  supabase/migrations/20260801144500_create_broker_connections.sql \
  .proposed_changes/086-essential-connection-state/superseded-migrations/
```

Then rerun:

```bash
supabase db reset --local
```

### Migration history differs between local and remote

First inspect; do not repair blindly:

```bash
supabase migration list --linked
```

`supabase migration repair` changes migration history. Use it only after identifying
the exact timestamp and confirming whether the schema is already present remotely.

### Migration SQL fails during `db reset`

The reset output identifies the migration and statement. Correct the reviewed SQL
or migration ordering, then rerun the full reset. Do not apply later migrations
manually to bypass a failed earlier migration.

## 15. Commands that require special caution

| Command | Risk |
| --- | --- |
| `supabase db reset --local` | Deletes local data; appropriate for migration testing |
| `supabase db push --linked` | Changes the linked cloud database |
| `supabase migration repair` | Alters recorded migration history |
| `supabase db reset --linked` | Deletes and rebuilds the linked remote database |
| `supabase stop --no-backup` | Deletes local Supabase Docker data |

Never run `supabase db reset --linked` against the existing production project.

## 16. Completion checklist

- [ ] Docker is running.
- [ ] The repository is initialized with `supabase init`.
- [ ] The correct existing project is linked.
- [ ] The remote schema baseline was pulled before the feature migration.
- [ ] The migration filename was generated by `supabase migration new`.
- [ ] There is exactly one migration for the feature.
- [ ] The baseline sorts before the feature migration.
- [ ] `supabase db reset --local` succeeds from an empty local database.
- [ ] Local lint and advisors were reviewed.
- [ ] Tables, RLS, and policies were checked directly.
- [ ] The linked project was confirmed before deployment.
- [ ] `supabase db push --dry-run --linked` contains only intended changes.
- [ ] Remote migration history and advisors were checked after deployment.

