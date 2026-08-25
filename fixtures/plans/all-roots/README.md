# Two independent cleanups

Two things worth doing. The second is listed after the first because it was noticed
second, not because it needs it.

## Steps

1. **Drop the legacy exporter** — bring `src/export/` to a state where `legacy.py` is gone
   and nothing imports it.
   Verify: `python3 -m pytest tests/export -q`

2. **Tighten the log format** — bring `src/logging/format.py` to a state where every
   record carries a request id.
   Verify: `python3 -m pytest tests/test_logging.py -q`
