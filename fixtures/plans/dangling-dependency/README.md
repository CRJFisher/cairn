# Dangling reference

The second step names a predecessor the plan never defines.

## Steps

1. **Write the loader** — bring `src/load.py` to a state where it reads the manifest.
   Verify: `python3 -m pytest tests/test_load.py -q`

2. **Write the renderer** — depends on the theme compiler. Bring `src/render.py` to a state
   where it renders a loaded manifest.
   Verify: `python3 -m pytest tests/test_render.py -q`
