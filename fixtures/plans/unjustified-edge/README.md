# Two unrelated fixes

Two steps the document never connects. A derivation that chains them anyway has nothing to
quote.

## Steps

1. **Fix the date parser** — bring `src/dates.py` to a state where a two-digit year is
   rejected rather than guessed.
   Verify: `python3 -m pytest tests/test_dates.py -q`

2. **Fix the CSV writer** — bring `src/csvout.py` to a state where an embedded newline is
   quoted.
   Verify: `python3 -m pytest tests/test_csvout.py -q`
