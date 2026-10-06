# TASK-376.27 — Re-index only the ranges that moved

Bring `src/incremental/reindex.ts` to a state where the index is rebuilt for the edit ranges
alone, leaving every symbol outside them untouched and their references intact.

Verify: `npm test -- reindex`
