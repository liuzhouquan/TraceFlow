# Authentication and migration review

Scope: uncommitted changes against `98c1f32`; requirements: `plan.md`. Outcome: blocked; not committed or pushed.

## Standards

Three blocking correctness findings:

1. `backend/migrations/versions/0001_initial_schema.py:188`: migration output does not match the ORM schema. A fresh disposable SQLite migration lacks `workflow_runs.logs_text`, with additional missing/renamed fields in other tables. Compare all migrated columns, constraints and defaults to the intended frozen baseline, then exercise actual ORM reads and writes.
2. `backend/app/main.py:90`: startup still uses `create_all` and handwritten ALTER statements, never invoking `run_migrations`. A disposable existing database fails migration with `OperationalError: table users already exists`. Replace competing schema-management paths and define a verified legacy-database adoption procedure.
3. `scripts/migrate.py:22`: CLI configuration does not set the application database URL. `migrations/env.py` prioritizes the nonempty default in `alembic.ini`, ignoring the application DATABASE_URL setting. Upgrade/downgrade can target the wrong database.

Standards: 3 blocking findings; most serious is the unusable migrated ORM schema.

## Spec

Four blocking requirement gaps:

1. `api/routes/auth.py:29`: public registration accepts role=owner. `permissions.py:117` grants that role access to all repositories. Anonymous registration returned 201 owner, followed by 200 when reading another user's private repository. The plan's fail-closed isolation requirement is violated; registration must not let the caller choose a privileged system role.
2. Core routes including `api/routes/ci.py:626`, chat and RAG still lack authentication and repository-scoped authorization. A request without a token returned 200 with a private CI record even with AUTH_REQUIRED=true. Authenticating only some routes does not establish the promised boundary.
3. `permissions.py:81`: production mode does not disable anonymous demo fallback. APP_ENV=production with default demo/auth flags returned 200 and the first user's identity from /api/auth/me. Enforce production authentication and reject insecure JWT configuration.
4. `main.py:90`: the plan explicitly requires replacing handwritten startup DDL with Alembic, but that integration is missing. PostgreSQL legacy compatibility also needs verification; SQLite-only migration success is insufficient.

Spec: 4 blocking findings; most serious are arbitrary administrator registration and unprotected private resources.

## Validation

- Full existing backend suite: 222 passed, 29 warnings. This result does not cover the failing boundary probes above.
- Frontend TypeScript check passed.
- Prettier check failed for frontend/lib/api.ts.
- Independent boundary probes used only temporary/in-memory SQLite databases and TestClient without application startup. No real repository, user database, model provider or external account was modified.
- No source fixes were applied by this review; no commit or push was performed.
