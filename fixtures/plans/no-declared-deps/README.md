# Release checklist

Four things to do before tagging. The order matters but the document never says so.

## Steps

1. **Bump the version** — bring `pyproject.toml` to a state where its version field reads
   the release number.
   Verify: `grep -q "version = \"2.4.0\"" pyproject.toml`

2. **Regenerate the changelog** from the commits since the last tag, into `CHANGELOG.md`
   under a heading for the version in `pyproject.toml`.
   Verify: `grep -q "## 2.4.0" CHANGELOG.md`

3. **Build the wheel** into `dist/`, named for the version in `pyproject.toml`.
   Verify: `test -f dist/tool-2.4.0-py3-none-any.whl`

4. **Publish the release notes** to the docs site from `CHANGELOG.md`, attaching the wheel
   from `dist/`.
   Verify: `curl -sf https://docs.example.com/releases/2.4.0`
