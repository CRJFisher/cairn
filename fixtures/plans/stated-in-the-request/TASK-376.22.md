# TASK-376.22 — Symbol table diffing

Bring `src/incremental/diff.ts` to a state where two symbol tables produce the added,
removed and moved symbols between them, with a moved symbol reported once rather than as a
removal and an addition.

Verify: `npm test -- diff`
