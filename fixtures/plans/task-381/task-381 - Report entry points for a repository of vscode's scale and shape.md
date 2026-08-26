# task-381 — Report entry points for a repository of vscode's scale and shape

The entry-point report is derived once per repository and cached. At vscode's scale the
derivation walks every module twice, and the second walk is the one nobody needs: the set
of self-referencing keywords is rebuilt per call, so the cost is quadratic in a corpus
where it should be linear.

This plan is one step, and it exists in the corpus for its **name**: the document is
titled the way a backlog document is titled, so the file name carries the whole title and
runs past the forty characters the engine allows a DAG name. The slug is the task id the
document already calls itself by.

## Steps

1. **Self keywords as a module-scoped map** — bring `src/report/entry_points.ts` to a
   state where `SELF_KEYWORDS` is built once at module scope rather than per call.
   Verify: `npm test -- --run src/report/entry_points.test.ts`
