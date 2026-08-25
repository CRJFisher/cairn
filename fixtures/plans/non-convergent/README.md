# Changelog and audit trail

Both steps are phrased as blind appends, so a resumed run duplicates their work.

## Steps

1. **Append a section to `CHANGELOG.md`** describing this release.
   Verify: `grep -q "## Unreleased" CHANGELOG.md`

2. **Create a new audit log file** under `var/audit/` and add an entry recording the
   release.
   Verify: `test -d var/audit`
