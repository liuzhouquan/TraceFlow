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

- Backend: 222 tests passed with SQLite and external services disabled (including 9 new dedicated tests for authentication, RBAC data isolation, collaborator lifecycle, and Alembic migrations).
- Ruff: all 143 Python files checked as formatted.
- Frontend: Prettier check, ESLint (0 errors), and TypeScript check passed.
- Git URL-scoped credential selection was checked using Git's own `config --get-urlmatch` command.
- Workflow YAML parses successfully. No live LLM, GitHub repository mutation, Docker database or Milvus end-to-end run is included in these offline checks.

ESLint reports 13 React Hook dependency warnings in the UI. FastAPI reports deprecation warnings for the legacy on_event lifecycle hooks. They did not fail checks.

## Known limits

The homepage is large and has not been decomposed. The main RAG evaluation case file `docs/datas/evals/rag_cases.json` is not present, so no main-application benchmark result is claimed.
