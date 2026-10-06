# TASK-376.24 — Parse cache keyed by content hash

Bring `src/incremental/cache.ts` to a state where a parse result is stored under the SHA-256
of the file's bytes, so a file whose content returns to an earlier state is served from the
cache rather than re-parsed.

Verify: `npm test -- cache`
