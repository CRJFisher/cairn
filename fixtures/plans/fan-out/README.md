# Split the settings reader

`settings.py` reads three unrelated config files. Give each its own reader.

## Steps

1. **Extract the loader interface** — bring `src/config/loader.py` to a state where it
   exposes a `load(path) -> dict` helper that every reader below uses.
   Verify: `python3 -m pytest tests/config/test_loader.py -q`

2. **Theme reader** — depends only on the loader. Bring `src/config/theme.py` to a state
   where it reads `theme.toml` through the loader and validates the palette keys.
   Verify: `python3 -m pytest tests/config/test_theme.py -q`

3. **Keymap reader** — depends only on the loader, and touches no file that step 2 touches.
   Bring `src/config/keymap.py` to a state where it reads `keys.toml` through the loader and
   rejects a duplicate binding.
   Verify: `python3 -m pytest tests/config/test_keymap.py -q`
