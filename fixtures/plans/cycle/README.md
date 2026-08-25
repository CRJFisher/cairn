# Circular plan

Three steps that each wait on the next. The document says so plainly, and it is not a
topology.

## Steps

1. **Parser** — depends on the emitter. Bring `src/parse.py` to a state where it reads the
   emitter's token table.
   Verify: `python3 -m pytest tests/test_parser.py -q`

2. **Checker** — depends on the parser. Bring `src/check.py` to a state where it
   type-checks the parser's output.
   Verify: `python3 -m pytest tests/test_checker.py -q`

3. **Emitter** — depends on the checker. Bring `src/emit.py` to a state where it emits code
   for the checker's output.
   Verify: `python3 -m pytest tests/test_emitter.py -q`
