# TASK-376 — Incremental re-parse

The parsed index is rebuilt from scratch whenever any file changes. This epic makes a change
to one file cost a re-parse of one file.

## Close-out measurement

Bring `docs/measurements/incremental.md` to a state where it records the measured cost of a
one-file re-parse against a whole-project parse, taken on this repository.

Verify: `npm run measure:incremental -- --check`

## Acceptance criteria

- [x] the parser can be invoked for one file at a time
- [ ] a parse result is addressed by content rather than by path and mtime
- [ ] an edit re-indexes the ranges it touched and nothing else
- [ ] the measurement is recorded and the figure is in the docs
