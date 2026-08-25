# Release manifest

The release script publishes a directory and tells nobody what is in it. One step, and
nothing depends on anything.

## Steps

1. **Manifest writer** — bring `src/release/manifest.py` to a state where it writes
   `dist/manifest.json` holding every artefact's name, size and sha256.
   Verify: `python3 -m pytest tests/test_manifest.py -q`
