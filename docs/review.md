# Code review and validation

## Review findings

- Standards review: no blocking findings.
- Requirements review: runtime display labels, prompts and notifications consistently use TraceFlow.
- The 62 files in `examples/rag-evaluation/` were verified byte-for-byte against the preserved working copy before publication.

## Corrections

- Implemented standard authentication subsystem: added PBKDF2-HMAC-SHA256 password hashing with random salt, JWT access token issuance and signature verification, user registration, login, and `/api/auth/me` profile probe endpoints.
- Implemented multi-tenant and multi-user data isolation: added repository ownership (`created_by`), repository collaborator relations (`RepositoryMember`), user-scoped repository list filtering, and protected sensitive repository deletion and sync operations with RBAC permission checks and audit log recording (`AuditLog`).
- Introduced versioned database migration pipeline using Alembic: created `backend/alembic.ini`, `backend/migrations/env.py` (with SQLite batch mode support and PostgreSQL support), baseline schema `0001_initial_schema`, and incremental schema `0002_auth_and_isolation` supporting bidirectional `upgrade` and `downgrade`.
- Corrected the incompatible `pydantic-settings` pin required by `langchain-community`.
- Removed automatic startup deletion of the legacy document embedding column. Existing rows are preserved.
- Git credentials use process-scoped, URL-specific HTTPS headers instead of credential-bearing clone URLs. Cached remotes are sanitized; fetch and pull use the current token. SSH and local remotes retain normal authentication.
- Updated Next.js and React within their existing major versions. Replaced obsolete `next lint` with ESLint flat configuration.
- Added Ruff and Prettier formatting rules, LF conventions, a CI workflow, and regression tests for credential handling and startup data preservation.
- Grouped the standalone RAG evaluation materials under `examples/` and removed unrelated resume/image utility scripts.

## Validation

- Backend: 240 tests passed using SQLite with external services disabled. Migration tests include rejected zero/missing/misdirected foreign keys, no version stamp on rejection, and retained user data after legacy upgrade.
- The three new failure cases were first run against the previous implementation and failed; they passed after the correction. A fourth case covers one missing member-table foreign key.
- Ruff source formatting, frontend Prettier, ESLint (0 errors), TypeScript, and production build passed.
- CI now uses `python -m pytest`; the backend pytest configuration also defines its import path, addressing the previous GitHub collection failure (`No module named app`).
- Git URL-scoped credential selection and workflow YAML were checked during earlier verification.
- No live LLM, GitHub application-data write or Milvus end-to-end call was included in offline tests. PostgreSQL live migration was not exercised.

ESLint reports 13 existing React Hook dependency warnings. The backend suite reports 57 warnings, including FastAPI lifecycle deprecations. These do not fail the checks.

## Known limits

The homepage is large and has not been decomposed. The main RAG evaluation case file `docs/datas/evals/rag_cases.json` is not present, so no main-application benchmark result is claimed.
