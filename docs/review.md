# Code review and validation

## Review findings

- Standards review: no blocking findings.
- Requirements review: runtime display labels, prompts and notifications consistently use TraceFlow.
- The 62 files in `examples/rag-evaluation/` were verified byte-for-byte against the preserved working copy before publication.

## Corrections

- Corrected the incompatible `pydantic-settings` pin required by `langchain-community`.
- Removed automatic startup deletion of the legacy document embedding column. Existing rows are preserved.
- Git credentials use process-scoped, URL-specific HTTPS headers instead of credential-bearing clone URLs. Cached remotes are sanitized; fetch and pull use the current token. SSH and local remotes retain normal authentication.
- Updated Next.js and React within their existing major versions. Replaced obsolete `next lint` with ESLint flat configuration.
- Added Ruff and Prettier formatting rules, LF conventions, a CI workflow, and regression tests for credential handling and startup data preservation.
- Grouped the standalone RAG evaluation materials under `examples/` and removed unrelated resume/image utility scripts.

## Validation

- Backend: 213 tests passed with SQLite and external services disabled.
- Ruff: all 133 Python files checked as formatted.
- Frontend: clean `npm install`; Prettier check, ESLint (0 errors), TypeScript check, and Next.js production build passed.
- Git URL-scoped credential selection was checked using Git's own `config --get-urlmatch` command.
- Workflow YAML parses successfully. No live LLM, GitHub repository mutation, Docker database or Milvus end-to-end run is included in these offline checks.

ESLint reports 13 React Hook dependency warnings in the UI. FastAPI reports 8 deprecation warnings for the lifecycle hooks. They did not fail checks.

## Known limits

The homepage is large and has not been decomposed. Authentication, database migration management and production hardening remain outside this release. The main RAG evaluation case file `docs/datas/evals/rag_cases.json` is not present, so no main-application benchmark result is claimed.
