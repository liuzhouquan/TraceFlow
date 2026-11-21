# Review round 4 — b7cc1cf

Scope: `82e3645...b7cc1cf`, with focus on closing the three previous findings. This review did not change implementation, commit or push.

## Standards

One remaining P2 correctness finding in `backend/app/db/migration.py:47-54`:

`has_0002_fks` starts as True and is recalculated only when `get_foreign_keys('repository_members')` is nonempty. A complete set of column names with zero member-table foreign keys therefore passes the newly added constraint check and is stamped head.

Reproduced using a disposable SQLite database with all ORM table/column definitions but foreign-key constraints omitted: member foreign keys before adoption = 0; run_migrations succeeds; version becomes 0002_auth_and_isolation; constraints remain missing. This can accept a damaged or previously incompletely migrated legacy database as complete.

Check the required foreign-key definitions even when the inspection result is empty. Empty must fail validation. Add a legacy-adoption regression for a column-complete member table without foreign keys; verify adoption is refused and no version stamp is written. Consider validating the full expected FK column/target signatures for supported legacy schemas.

Previous missing users.updated_at and missing documents table cases are now checked and rejected.

Standards: 1 P2 finding remains; the ordinary fresh-database migration is not affected by this legacy inspection branch.

## Spec

No remaining findings in the scoped evaluation fixes:

- Unrelated user: private eval detail / comparison / rescore all return 403.
- Repository owner: basic evaluation creation returns 200; unrelated-user read returns 403.
- Common evaluation authorization now precedes result access and job scheduling.
- Basic evaluation and background completion persist repository scope.
- Ordinary-user lists exclude ambiguous legacy evaluation records.

Spec: 0 findings; the prior two evaluation findings are closed.

## Validation

- Backend: 236 passed, 57 warnings.
- Frontend: Prettier, ESLint, TypeScript and production build passed; ESLint has 13 existing hook warnings.
- Independent negative checks used temporary/in-memory SQLite only, with local no-op substitutes to prevent background model/vector calls.
- PostgreSQL live migration was not exercised. Passing tests do not cover the zero-foreign-key adoption case above.


## Resolution

The remaining P2 finding is fixed: legacy adoption compares every expected foreign-key endpoint (local columns, target table and target columns), for both baseline and current schemas. Empty, partial and wrongly mapped constraints are refused before stamping. Regression tests cover these cases and verify rejection leaves existing data and version state untouched; the supported baseline upgrade also checks user-data retention. See `docs/review.md` for final verification.
