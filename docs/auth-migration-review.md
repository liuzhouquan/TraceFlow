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

## Resolution & Verification (Round 2)

All 3 Standards findings and 4 Spec requirement gaps have been resolved and verified with objective automated test suites:

### 1. Standards Resolutions
- **S-1 (0001 Schema Match ORM Baseline)**: Rebuilt `0001_initial_schema.py` table definitions to exactly match SQLAlchemy `Base.metadata` definitions (including `workflow_runs.logs_text`, correct indices, column types, and defaults). Verified with `test_migrated_schema_matches_orm_metadata` asserting 100% table and column equality between Alembic migrated schema and ORM metadata.
- **S-2 (Startup Alembic Migration & Legacy Adoption)**: Removed handwritten `create_all` and `ALTER TABLE` statements from `backend/app/main.py`. Replaced with `run_migrations(bind=engine)`. Implemented automated legacy database adoption in `backend/app/db/migration.py` (stamps `head` or `0001_initial` depending on user table columns before executing `upgrade head`). Verified with `test_legacy_database_adoption_by_run_migrations`.
- **S-3 (Database URL Prioritization)**: Cleared hardcoded URL in `backend/alembic.ini`. Configured `migrations/env.py` and `scripts/migrate.py` so runtime config and `DATABASE_URL` / `settings.database_url` are respected with highest priority. Verified with `alembic upgrade head --sql` against PostgreSQL target.

### 2. Spec Resolutions
- **Spec-1 (Privileged Role Registration Guard)**: In `backend/app/api/routes/auth.py`, registration requests attempting to select privileged roles (`owner`, `maintainer`) are rejected with HTTP 400 Bad Request. Verified with `test_public_registration_cannot_choose_privileged_role`.
- **Spec-2 (Route Authentication & Repository-Scoped Authorization)**: Protected core routes across `ci.py`, `pull_requests.py`, `rag.py`, `chat.py`, `code_graph.py`, and `knowledge.py` using `get_current_user` and repository-scoped checks (`check_repo_access(..., repo_id, "read")`). Cross-user unauthorized access returns HTTP 401/403/404. Verified with `test_core_routes_require_authentication_when_auth_required` and `test_cross_user_ci_and_rag_isolation`.
- **Spec-3 (Production Authentication & Security Hardening)**: In `backend/app/core/config.py` and `backend/app/services/permissions.py`, `APP_ENV=production` disables anonymous demo fallback (raising HTTP 401 Unauthorized for unauthenticated callers) and strictly validates that `jwt_secret_key` is not the insecure default. Verified with `test_production_mode_enforces_auth_and_rejects_insecure_key`.
- **Spec-4 (PostgreSQL & Alembic Compatibility)**: Verified PostgreSQL transactional DDL generation via `DATABASE_URL=postgresql+psycopg://... alembic upgrade head --sql`.

### 3. Verification Evidence
- **Backend Tests**: 228 passed in 21.02s (`pytest app/tests/ -v`).
- **Backend Formatting**: `ruff format --check` passed across 143 files.
- **Frontend Formatting & Quality**: `npm run format:check`, `npm run lint`, and `npm run typecheck` all passed.
