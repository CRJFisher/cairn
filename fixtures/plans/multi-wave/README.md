# Offline export

Let the desktop app export a workspace while offline, then reconcile on reconnect.

## Steps

1. **Export schema** — bring `schema/export.json` to a state where it pins the export
   envelope: workspace id, revision, and an ordered item list.
   Verify: `python3 -m jsonschema --instance fixtures/export.json schema/export.json`

2. **Writer** — depends on the export schema. Bring `src/export/writer.py` to a state where
   it emits an envelope conforming to the schema for any workspace on disk.
   Verify: `python3 -m pytest tests/export/test_writer.py -q`

3. **Zip packer** — depends on the writer. Bring `src/export/pack.py` to a state where an
   export directory becomes one `.wsx` archive with a manifest at its root.
   Verify: `python3 -m pytest tests/export/test_pack.py -q`

4. **Reader** — depends on the writer, and is independent of the zip packer. Bring
   `src/export/reader.py` to a state where it parses an envelope back into a workspace.
   Verify: `python3 -m pytest tests/export/test_reader.py -q`

5. **Reconcile** — depends on both the zip packer and the reader. Bring
   `src/sync/reconcile.py` to a state where an imported archive merges into the live
   workspace, newest revision winning per item.
   Verify: `python3 -m pytest tests/sync/test_reconcile.py -q`
