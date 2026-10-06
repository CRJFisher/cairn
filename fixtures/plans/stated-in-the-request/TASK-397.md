# TASK-397 — Index metrics

Bring `src/metrics/index.ts` to a state where every index build records how many files it
read, how many it parsed and how long each phase took.

Verify: `npm test -- metrics`
