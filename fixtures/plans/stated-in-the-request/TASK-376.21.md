# TASK-376.21 — Edit ranges from the watcher

Bring `src/incremental/ranges.ts` to a state where a watcher event becomes the byte ranges
that changed, merged where they overlap and ordered by start offset.

Verify: `npm test -- ranges`
