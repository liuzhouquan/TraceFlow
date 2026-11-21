# TraceFlow maintenance

- Backend: Python 3.12, FastAPI, SQLAlchemy. Run Ruff format with `--line-length 100`.
- Frontend: Next.js 15, TypeScript. Use the pinned Prettier configuration and ESLint.
- Keep public display branding TraceFlow. Preserve internal database identifiers where changing them would break compatibility.
- Do not change historical dataset/reference result contents in `examples/rag-evaluation` during cleanup.
- Never commit credentials, local environments, runtime data, generated build artifacts, or node_modules.
- Review changes against the adoption spec, run relevant tests and frontend build, and record limitations honestly.
