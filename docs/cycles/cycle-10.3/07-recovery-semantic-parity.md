# Recovery semantic parity

S16 now compares the backup database with the restored database by table
schema and complete row contents, checks SQLite integrity and foreign keys,
and compares every blob filename and SHA-256. Only append-only operational
audit rows are excluded from data parity, because backup/restore journals are
legitimate history changes.

Final status: PASS in Golden Path run `20261110T230014Z-7286ba79`: 23 tables
were checked for schema/row parity, SQLite integrity and foreign keys passed,
and the blob inventory/hash set matched (0 blobs in this fixture).
