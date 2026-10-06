# TASK-398 — Watcher debounce

Bring `src/watch/debounce.ts` to a state where a burst of events for one file collapses into
a single event carrying the last state, and events for different files are never collapsed
together.

Verify: `npm test -- watcher`
