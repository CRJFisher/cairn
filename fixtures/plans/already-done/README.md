# Search reindex

Every step below is done. The folder is kept for the record.

## Steps

1. ✅ **Schema migration** — the `documents` table carries a `tsv` column.
2. ✅ **Backfill** — every existing row has a populated `tsv`.
3. ✅ **Query path** — search reads the index rather than scanning.
