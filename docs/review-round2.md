# Review round 2 — f17aa8d

Scope: fixes in `4a52c27...f17aa8d`, checked against `plan.md` and the previous review. Outcome: blockers remain; no commit or push performed by this review.

## Standards

1. **P1: Missing foreign keys in fresh migrated databases.** `backend/migrations/versions/0001_initial_schema.py:23` and the remaining baseline table definitions contain no foreign-key constraints. At head, disposable SQLite inspection reports 3 foreign keys against 49 in ORM-created schema. With foreign key enforcement enabled, an orphan WorkflowRun is accepted. Restoring columns alone does not restore integrity; add frozen FK definitions and exercise constraints in migration tests.
2. **P2: Incomplete legacy databases are stamped as fully migrated.** `backend/app/db/migration.py:46` and `:59` infer head from only users.email and users.hashed_password. A disposable partially upgraded schema was stamped head although users.is_active/updated_at and repository_members were missing. Validate the whole supported schema before adoption; reject unknown states or repair them explicitly rather than reporting upgrade success.

Standards: 2 blocking findings for this release; worst is migration integrity drift.

## Spec

1. **P1: Remaining anonymous routes bypass required authentication.** `backend/app/api/routes/project_index.py:32`, feedback, search and reports retain unguarded endpoints; main.py supplies no shared authentication guard. With AUTH_REQUIRED=true and DEMO_MODE=false, anonymous POST /api/repos/{private_repo}/memory-index/snooze returns 200 and persists state. Anonymous feedback metrics also return 200. This fails the plan's fail-closed authentication boundary. Cover every route and separate explicit public routes.
2. **P1: Global role checks are insufficient for private resources.** `backend/app/api/routes/action_drafts.py:116` and `:143` do not authorize draft.repo_id or restrict list results to accessible repositories. An unrelated developer can list/read another user's private draft (200) and cancel it (200, status=cancelled). Apply repository-scoped checks to read, mutation and execution paths; audit similar evaluation/workspace APIs.

Spec: 2 blocking findings; worst is unauthenticated private-resource mutation and cross-user draft access.

## Confirmed progress and validation

- Public registration now rejects owner/maintainer selection.
- Production mode blocks anonymous demo fallback; startup validates JWT configuration.
- Alembic is invoked at startup; fresh table/column parity and supported complete pre-auth legacy adoption now work. Migration CLI now sets the configured database URL.
- Backend suite: 228 passed, 45 warnings.
- Frontend Prettier and TypeScript checks passed.
- Independent probes used only in-memory/disposable SQLite databases; no existing user database or external account was changed.

The current green suite does not cover these remaining boundaries. Add negative HTTP tests (anonymous, viewer and unrelated developer), migration FK parity/enforcement tests, and partial-schema adoption tests before accepting the release. PostgreSQL live upgrade was not exercised in this review.

## Resolution & Verification (Round 3)

All 2 Standards findings and 2 Spec requirement gaps have been resolved and verified with comprehensive automated test suites:

### 1. Standards Resolutions
- **P1 (Foreign Key Parity and Enforcement)**:
  - Added all 46 baseline `sa.ForeignKeyConstraint` definitions into `backend/migrations/versions/0001_initial_schema.py` in strict topological order (`Base.metadata.sorted_tables`).
  - Together with the 3 foreign keys in `0002_auth_and_isolation.py`, the migrated database has exactly 49 foreign keys, achieving 100% parity with `Base.metadata`.
  - Added `test_migrated_schema_fk_parity_and_enforcement` in `test_migrations.py`: asserts 49/49 FK parity across all tables and proves that SQLite with `PRAGMA foreign_keys = ON` strictly rejects orphan `WorkflowRun` insertions with `IntegrityError`.
- **P2 (Strict Legacy Database Validation and Partial Schema Rejection)**:
  - Refactored `_inspect_and_adopt_legacy_database` in `backend/app/db/migration.py` to validate the full supported schema:
    1. Fully upgraded legacy DB: verifies `repository_members` table, `users` (email, hashed_password, is_active), and `repositories` (created_by, is_public) are all present before stamping `head`.
    2. Baseline legacy DB: verifies clean absence of all 0002 tables and columns before stamping `0001_initial` and running `upgrade head`.
    3. Partially upgraded / inconsistent schema: Fail-Closed by raising `RuntimeError` with a detailed diagnostic message, preventing silent false migration claims.
  - Added `test_partial_legacy_database_adoption_rejected` and `test_baseline_legacy_database_adoption_and_upgrade` in `test_migrations.py`.

### 2. Spec Resolutions
- **P1 (Complete Authentication Coverage for Anonymous Endpoints)**:
  - Guarded all endpoints in `project_index.py`, `feedback.py`, `search.py`, `reports.py`, and `skills.py` with `get_current_user`, rejecting anonymous callers with HTTP 401 Unauthorized when `auth_required=True` or `demo_mode=False`.
  - Added `test_anonymous_access_blocked_on_all_unprotected_routes` in `test_permissions_and_isolation.py`, verifying that snooze, feedback metrics, search, reports, skills, action drafts, and workspace multi-repo reports all return HTTP 401 when unauthenticated.
- **P1 (Repository-Scoped Authorization for Drafts, Workspaces, and Evals)**:
  - In `backend/app/api/routes/action_drafts.py`:
    - Listing drafts and audit logs filters by repositories accessible to the user (`created_by`, `is_public`, or member).
    - Reading, cancelling, and confirming drafts strictly checks `check_repo_access(db, user, draft.repo_id, permission)` with appropriate permission (`repo:read`, `draft:create`, or `draft:approve`).
  - Audited and updated `workspaces.py` and `evaluations.py` to enforce repository-scoped authorization on all repo-linked operations.
  - Added `test_unrelated_developer_and_viewer_cross_repo_isolation` in `test_permissions_and_isolation.py`:
    - An unrelated developer cannot view, list, cancel, snooze, or run reports on another developer's private drafts.
    - A viewer collaborator can read drafts (HTTP 200) but cannot cancel or confirm drafts (HTTP 403).
    - The repository owner can successfully manage and cancel drafts (HTTP 200).

### 3. Verification Evidence
- **Backend Test Suite**: 233 passed, 0 failed in 33.24s (`pytest app/tests/`).
- **PostgreSQL DDL Generation**: Transactional DDL generated successfully via `alembic upgrade head --sql`.
- **Code Style & Format**: `ruff format --check` (143 files) and `prettier --check .` both passed.
- **Frontend Quality**: `npm run lint` and `npm run typecheck` both passed with 0 errors.
