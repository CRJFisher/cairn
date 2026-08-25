# Nightly index rebuild

One agent step and two plain commands.

## Steps

1. **Refresh the corpus** — bring `data/corpus/` to a state where it mirrors the current
   contents of the source bucket. This is a weekly job: redo it once per week, not once
   ever. Deny shell removal commands with `Bash(rm:*)`. Give it two hours, spend at most
   eight dollars on it, and pin it to the opus model.
   Verify: `test -f data/corpus/MANIFEST`

2. **Rebuild the index** — depends on the refreshed corpus. Redo this whenever anything
   under `data/corpus/` changes. Run `bin/reindex --full`.
   Verify: `test -f var/index/.rebuild-started`

3. **Wait for the index to settle** — depends on the rebuild. Poll until
   `bin/index-status --quiet` succeeds, at most fifteen minutes. Once per run occasion.
   Verify: `bin/index-status --quiet`
