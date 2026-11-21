# Review round 3 — 82e3645

Scope: `f17aa8d...82e3645` and the remaining requirements in `plan.md`. Status: blockers remain; no commit or push performed by this review.

## Standards

**P2 — Legacy adoption still accepts incomplete schemas.**

`backend/app/db/migration.py:27-45` checks a small subset of user/repository columns and whether repository_members exists. It does not check users.updated_at, other required tables/columns or constraints before stamping head. In disposable SQLite databases, both removing users.updated_at and removing the documents table still let run_migrations finish and stamp 0002_auth_and_isolation, leaving the schema broken. Validate the full supported schema before stamping; unknown/incomplete states must fail closed. The latest narrower partial-schema fixture passes, but does not cover these variants.

Standards: 1 remaining blocking finding for release; worst is inaccurate legacy schema adoption.

## Spec

**P1 — Eval comparison and rescoring bypass repository authorization.**

`backend/app/api/routes/evaluations.py:266` and `:462` check only global eval:read. Neither authorizes the repository of the loaded source/base/candidate EvalRun. With a private evaluation owned by another user, an unrelated developer receives 403 from its normal detail endpoint but 200 from compare and rescore. Rescoring creates a new evaluation job before repository access is checked. Apply one shared authorization helper to each loaded evaluation, including both comparison inputs and rescore source, before reading result data or scheduling work.

**P1 — Basic evaluation persistence loses repository scope.**

`backend/app/api/routes/evaluations.py:89-90` saves run_basic_eval output directly. `services/evals/runner.py:264` does not return target.repo_id; list/get authorization checks only that metadata and treats absent scope as globally readable. A private-repository basic evaluation therefore becomes readable to an unrelated user. In a disposable API fixture using a deterministic local evaluator substitute, private evaluation creation returned 200 and a subsequent unrelated-user GET also returned 200. Persist repository scope explicitly and reliably; migrate or restrict ambiguous legacy results rather than silently exposing them.

Spec: 2 remaining blocking findings; worst is cross-repository evaluation access and job creation.

## Confirmed fixes

- Anonymous private-index snooze now returns 401 with authentication required.
- An unrelated developer sees an empty draft list; direct draft read/cancel returns 403.
- Fresh migration now has 49 foreign keys, matching the ORM count.
- Previously reproduced two-column partial legacy schemas are rejected, although broader completeness validation remains missing.

## Validation

- Backend: 233 passed, 53 warnings.
- Frontend: Prettier, ESLint, TypeScript and production build passed. Existing hook warnings remain.
- Negative API and migration probes used disposable/in-memory SQLite only. Rescore background work was replaced with a no-op; basic evaluator was substituted locally to check route persistence without model/vector calls. No live service, external account or real database was changed.
- PostgreSQL live migration and upgrade were not exercised.

The successful suite does not cover the negative paths above. Add tests for every evaluation entry point, missing persisted scope, and full-schema legacy adoption before accepting this release.
